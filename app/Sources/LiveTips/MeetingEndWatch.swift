import AppKit
import CoreAudio
import Darwin
import UserNotifications
import LiveTipsCore

/// 2026-11-10 (owner): hanging up in the meeting ends the call in Pacenote, the way Notion stops its notes, with a
/// short grace in case the guess is wrong. The signal is not silence: while you are in a meeting, Zoom, Teams, Meet in
/// the browser or the Meet app keeps the microphone open, also when you are muted, quiet or away from the Mac. Hanging
/// up closes it. Every 2 s the app asks CoreAudio which processes capture from a microphone (macOS 14.4+: the process
/// objects). Once another app has held it for 10 s during the call, 4 s without it (a device switch takes about 1 s)
/// show "The meeting has ended? Stopping in 5 s" (2026-11-11, owner: was 10 s) in the tips window and as a notification, both with Keep recording.
/// The microphone coming back hides it; otherwise the call stops as End call does. Pacenote's own recorder and screen
/// recording never count (also the macOS capture service replayd, 2026-11-17); a call with no meeting app (in person) is never stopped. Settings > Integrations >
/// Calendars > "Stop the call when the meeting ends" (on by default).
@MainActor
final class MeetingEndWatch {
    static let shared = MeetingEndWatch()
    static let defaultsKey = "meetingEndStop"
    static let notificationId = "pn-meetend"
    static let category = "pn-meetend-cat"
    static let keepAction = "pn-meetend-keep"
    static let step: TimeInterval = 2
    static let meetingSeen: TimeInterval = 10   // another app held the microphone this long: there is a meeting
    static let releasedFor: TimeInterval = 4    // it let go for this long: ask
    static let grace: TimeInterval = 5          // then stop, unless the microphone comes back or the PM keeps recording

    var enabled: Bool { UserDefaults.standard.object(forKey: Self.defaultsKey) as? Bool ?? true }
    private var timer: Timer?
    private var callKey = ""
    private var seen: TimeInterval = 0
    private var releasedSince: Date?
    private var asking = false
    private var done = false
    private var loggedSeen = false      // 0.27.2: what the watcher saw goes to app.log once per call and per release
    private var loggedRelease = false

    func start() {
        let keep = UNNotificationAction(identifier: Self.keepAction, title: L("meetend.keep"), options: [])
        let cat = UNNotificationCategory(identifier: Self.category, actions: [keep], intentIdentifiers: [], options: [])
        UNUserNotificationCenter.current().getNotificationCategories { have in
            UNUserNotificationCenter.current().setNotificationCategories(have.filter { $0.identifier != MeetingEndWatch.category }.union([cat]))
        }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: Self.step, repeats: true) { _ in MainActor.assumeIsolated { MeetingEndWatch.shared.tick() } }
    }

    func tick() {
        let st = EngineStatus.load(path: CallEngine.shared.statusPath)
        let live = CallEngine.shared.isRunning || ["recording", "standby"].contains(st.state)
        guard enabled, live else { if asking { hideAsk() }; callKey = ""; return }
        let key = CallEngine.shared.appTipsFile ?? st.tipsFile ?? ""
        if key != callKey { if asking { hideAsk() }; callKey = key; seen = 0; releasedSince = nil; done = false; loggedSeen = false; loggedRelease = false }
        if done { return }
        let holders = Self.micHolders()
        let held: Bool = holders == nil || !(holders ?? []).isEmpty     // CoreAudio cannot say: held (never stop on a guess)
        if held {
            seen += Self.step
            if releasedSince != nil, loggedRelease { AppLog.app("meeting end: the microphone is back (\(Self.names(holders)))") }
            releasedSince = nil; loggedRelease = false
            if seen >= Self.meetingSeen, !loggedSeen { loggedSeen = true; AppLog.app("meeting end: a meeting holds the microphone (\(Self.names(holders)))") }
            if asking { hideAsk() }
            return
        }
        guard seen >= Self.meetingSeen else { return }
        let now = Date()
        guard let r = releasedSince else {
            releasedSince = now
            if !loggedRelease { loggedRelease = true; AppLog.app("meeting end: the microphone was released") }
            return
        }
        let quiet = now.timeIntervalSince(r)
        if quiet >= Self.releasedFor + Self.grace { stop(); return }
        if quiet >= Self.releasedFor { showAsk(left: Int((Self.releasedFor + Self.grace - quiet).rounded(.up))) }
    }

    /// Keep recording (the tips window or the notification): no more questions on this call.
    func keepRecording() {
        guard asking else { return }
        done = true; hideAsk()
        UsageLog.log("meeting_end", ["action": "keep"])
        AppLog.app("meeting end: keep recording")
    }

    func stopNow() { guard asking else { return }; stop() }

    private func stop() {
        hideAsk()
        done = true
        UsageLog.log("meeting_end", ["action": "stop"])
        AppLog.app("meeting end: stopping the call")
        CallEngine.shared.stop()
    }

    private func showAsk(left: Int) {
        (NSApp.delegate as? AppDelegate)?.tips?.push("window.LiveTips && LiveTips.setMeetEnd({left: \(max(0, left))})")
        guard !asking else { return }
        asking = true
        UsageLog.log("meeting_end", ["action": "ask"])
        AppLog.app("meeting end: asking, stops in \(left) s")
        let c = UNMutableNotificationContent()
        c.title = L("meetend.notif.title")
        c.body = L("meetend.notif.body", ["n": "\(Int(Self.grace))"])
        c.categoryIdentifier = Self.category
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: Self.notificationId, content: c, trigger: nil))
    }

    private func hideAsk() {
        asking = false
        (NSApp.delegate as? AppDelegate)?.tips?.push("window.LiveTips && LiveTips.setMeetEnd(null)")
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: [Self.notificationId])
    }

    /// 0.27.2: End call pressed by hand: what the watcher knew at that moment, so a call that did not stop by itself can
    /// be explained from app.log (the owner's call of 23.11 ended by hand with no meeting_end event at all).
    func noteManualEnd() {
        guard enabled else { AppLog.app("meeting end: off in Settings"); return }
        var state: String
        if done {
            state = "kept recording"
        } else if seen < Self.meetingSeen {
            state = "no meeting app held the microphone (" + String(Int(seen)) + " s)"
        } else if let r = releasedSince {
            state = "the microphone was released " + String(Int(Date().timeIntervalSince(r))) + " s ago"
        } else {
            state = "a meeting still holds the microphone (" + Self.names(Self.micHolders()) + ")"
        }
        AppLog.app("meeting end: End call by hand, " + state)
    }

    /// The short names of the processes in a holders list, for app.log (never sent anywhere).
    nonisolated static func names(_ h: [String]?) -> String {
        guard let h = h else { return "unknown" }
        if h.isEmpty { return "none" }
        return Array(Set(h)).sorted().joined(separator: ", ")
    }

    /// Some process other than Pacenote and its recorder is capturing from an input device. When CoreAudio cannot
    /// say, the answer is yes: a call is never stopped on a guess.
    nonisolated static func otherAppsUseMicrophone() -> Bool { micHolders().map { !$0.isEmpty } ?? true }

    /// 0.27.2: the processes other than Pacenote capturing from an input now (file names); nil when CoreAudio cannot say.
    nonisolated static func micHolders() -> [String]? {
        let sys = AudioObjectID(kAudioObjectSystemObject)
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                              mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(sys, &addr, 0, nil, &size) == noErr, size > 0 else { return nil }
        var objs = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(sys, &addr, 0, nil, &size, &objs) == noErr else { return nil }
        let me = getpid()
        var out: [String] = []
        for o in objs {
            var running: UInt32 = 0
            var s = UInt32(MemoryLayout<UInt32>.size)
            var a = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyIsRunningInput,
                                               mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            guard AudioObjectGetPropertyData(o, &a, 0, nil, &s, &running) == noErr else { return nil }
            if running == 0 { continue }
            var pid: pid_t = 0
            s = UInt32(MemoryLayout<pid_t>.size)
            a.mSelector = kAudioProcessPropertyPID
            guard AudioObjectGetPropertyData(o, &a, 0, nil, &s, &pid) == noErr else { return nil }
            if pid == me || isOurs(pid, me: me) || isScreenCapture(pid) { continue }
            var buf = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
            out.append(proc_pidpath(pid, &buf, UInt32(buf.count)) > 0 ? (String(cString: buf) as NSString).lastPathComponent : "pid \(pid)")
        }
        return out
    }

    /// 2026-11-17 (owner): macOS's screen capture service. While Pacenote records the call (screen, video with sound or
    /// sound only), ScreenCaptureKit takes the Mac's sound through replayd, and replayd then holds an input for as long
    /// as the recording runs (checked on the owner's Mac: runs/ms/micdiag_1117.log). It counted as a meeting app, so
    /// hanging up never ended a recorded call. The same holds for the macOS screen recording (Shift-Cmd-5). It is never
    /// the meeting itself.
    nonisolated static func isScreenCapture(_ pid: pid_t) -> Bool {
        var buf = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
        guard proc_pidpath(pid, &buf, UInt32(buf.count)) > 0 else { return false }
        return String(cString: buf) == "/usr/libexec/replayd"
    }

    /// Pacenote's own processes: a child of the app (the recorder, audiocap) or a binary from its folders.
    nonisolated static func isOurs(_ pid: pid_t, me: pid_t) -> Bool {
        var p = pid
        for _ in 0..<6 {
            var info = proc_bsdinfo()
            let n = proc_pidinfo(p, PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout<proc_bsdinfo>.size))
            guard n == Int32(MemoryLayout<proc_bsdinfo>.size) else { break }
            let parent = pid_t(info.pbi_ppid)
            if parent == me { return true }
            if parent <= 1 { break }
            p = parent
        }
        var buf = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
        guard proc_pidpath(pid, &buf, UInt32(buf.count)) > 0 else { return false }
        let path = String(cString: buf)
        return path.contains("/Pacenote.app/") || path.contains("/LiveTips.app/") || path.contains("/Tools/LiveTips/")
            || path.contains("/live-transcriber/")
    }
}
