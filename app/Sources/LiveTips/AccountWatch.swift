import AppKit
import Combine
import LiveTipsCore
import SwiftUI

/// PB-98 (owner 25.11): Sign in, Sign out and an honest Check for the Claude account and the ChatGPT account.
/// The tools' local status is read again on every focus of the app and every 2 minutes, so a sign-in made in
/// Terminal shows without a restart; it clears the "signed out" mark of the last request (AccountAuth). Check sends
/// one tiny live request through the engine (AIRoute.ping), not only the local auth file.
@MainActor
final class AccountWatch: ObservableObject {
    static let shared = AccountWatch()
    static let kinds = ["claude", "codex"]

    @Published private(set) var tick = 0                       // the out marks changed: rows read them again
    @Published private(set) var checking: Set<String> = []     // a live check or a sign-out is running
    @Published private(set) var result: [String: String] = [:] // the last Check's line per account
    private var previous: [String: Bool?] = [:]
    private var lastLive: [String: Double] = [:]
    private var subs: Set<AnyCancellable> = []
    private var timer: Timer?

    func start() {
        guard timer == nil else { return }
        PrepRunner.shared.$loggedIn.sink { [weak self] v in self?.localChanged("claude", v) }.store(in: &subs)
        CodexAccount.shared.$signedIn.sink { [weak self] v in self?.localChanged("codex", v) }.store(in: &subs)
        NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)
            .sink { [weak self] _ in self?.focused() }.store(in: &subs)
        timer = Timer.scheduledTimer(withTimeInterval: AccountAuth.pollS, repeats: true) { _ in
            MainActor.assumeIsolated { AccountWatch.shared.refreshLocal() }
        }
    }

    func refreshLocal() {
        PrepRunner.shared.refreshAuth()
        CodexAccount.shared.refresh()
        tick += 1                                               // an engine run may have marked an account out meanwhile
    }

    /// The app got focus: the local status again, and for an account the local file calls signed in but the last
    /// request found signed out, a live request (at most once a minute), so a new sign-in in Terminal shows now.
    private func focused() {
        let now = Date().timeIntervalSince1970
        for kind in Self.kinds where !checking.contains(kind) {
            let out = AIRoute.outStates()[AccountAuth.route(kind)]
            if AccountAuth.needsLiveCheck(local: Self.local(kind), out: out, lastCheck: lastLive[kind], now: now, focus: true) {
                live(kind, manual: false)
            }
        }
        refreshLocal()
    }

    static func installed(_ kind: String) -> Bool { AccountLogin.cli(kind) != nil }

    static func local(_ kind: String) -> Bool? {
        kind == "codex" ? CodexAccount.shared.signedIn : PrepRunner.shared.loggedIn
    }

    func state(_ kind: String) -> AccountAuth.State {
        _ = tick
        return AccountAuth.state(installed: Self.installed(kind), local: Self.local(kind),
                                 out: AIRoute.outStates()[AccountAuth.route(kind)])
    }

    /// Each answer of the tool's local status (it is published on every check, even when unchanged).
    private func localChanged(_ kind: String, _ now: Bool?) {
        let route = AccountAuth.route(kind)
        let out = AIRoute.outStates()[route]
        let prev = previous[kind] ?? nil
        previous[kind] = now
        if AccountAuth.clearsOutMark(previous: prev, now: now, out: out) {
            AppLog.app("account \(kind): signed in elsewhere, the signed-out mark is cleared")
            AIRoute.markUp(route)
            result[kind] = nil
        } else if !checking.contains(kind),
                  AccountAuth.needsLiveCheck(local: now, out: out, lastCheck: lastLive[kind], now: Date().timeIntervalSince1970) {
            live(kind, manual: false)
        }
        tick += 1
    }

    /// Settings > AI > Check: the local status and one tiny live request.
    func check(_ kind: String) {
        guard !checking.contains(kind) else { return }
        kind == "codex" ? CodexAccount.shared.refresh() : PrepRunner.shared.refreshAuth()
        live(kind, manual: true)
    }

    private func live(_ kind: String, manual: Bool) {
        checking.insert(kind)
        lastLive[kind] = Date().timeIntervalSince1970
        if manual { result[kind] = nil }
        AIRoute.ping(AccountAuth.route(kind)) { [weak self] ok in
            guard let self else { return }
            self.checking.remove(kind)
            let time = AIStatus.hhmm(Date().timeIntervalSince1970)
            switch ok {
            case true?: self.result[kind] = L("ai.acct.live.ok", ["time": time])
            case false?:
                let reason = AIRoute.outStates()[AccountAuth.route(kind)]?.reason ?? "error"
                self.result[kind] = L("ai.acct.live.fail", ["time": time, "reason": L("ai.reason." + (reason.isEmpty ? "error" : reason))])
            case nil: if manual { self.result[kind] = L("ai.acct.live.unavailable") }
            }
            AppLog.app("account \(kind): live check \(ok.map { $0 ? "answers" : "out" } ?? "not possible")")
            self.tick += 1
            AIStatusModel.shared.refresh(force: true)
        }
    }

    /// The tool's own sign-out, then its status again (the row turns Signed out).
    func signOut(_ kind: String) {
        guard let cli = AccountLogin.cli(kind), !checking.contains(kind) else { return }
        checking.insert(kind)
        result[kind] = nil
        let args = AccountAuth.signOutArguments(kind)
        var env = CodexCLI.environment(ProcessInfo.processInfo.environment)
        env.removeValue(forKey: "ANTHROPIC_AUTH_TOKEN")
        DispatchQueue.global().async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: cli)
            p.arguments = args
            p.environment = env
            p.standardOutput = FileHandle.nullDevice; p.standardError = FileHandle.nullDevice
            p.standardInput = FileHandle.nullDevice                 // never waits for an answer
            let timer = DispatchWorkItem { if p.isRunning { p.terminate() } }
            var rc: Int32 = -1
            if (try? p.run()) != nil {
                DispatchQueue.global().asyncAfter(deadline: .now() + 15, execute: timer)
                p.waitUntilExit(); timer.cancel()
                rc = p.terminationReason == .uncaughtSignal ? -1 : p.terminationStatus
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    AppLog.app("account \(kind): sign out rc=\(rc)")
                    self.checking.remove(kind)
                    if rc != 0 { self.result[kind] = L("ai.acct.signOutFailed") }
                    kind == "codex" ? CodexAccount.shared.refresh() : PrepRunner.shared.refreshAuth()
                }
            }
        }
    }
}

/// One account row of Settings > AI (Claude account, ChatGPT account): Signed in or Signed out (red), Sign in or
/// Sign out, Check with the result of its live request.
struct AIAccountRow: View {
    let kind: String          // claude | codex
    @ObservedObject private var watch = AccountWatch.shared
    @ObservedObject private var prep = PrepRunner.shared
    @ObservedObject private var codex = CodexAccount.shared
    @State private var confirmOut = false

    private var title: String { kind == "codex" ? L("ai.codex.account") : L("ai.access.account") }

    var body: some View {
        let st = watch.state(kind)
        let busy = watch.checking.contains(kind) || (kind == "codex" && codex.checking)
        VStack(alignment: .leading, spacing: 4) {
            LabeledContent(title) {
                HStack(spacing: 8) {
                    switch st {
                    case .notInstalled:
                        Text(L(kind == "codex" ? "ai.codex.missing" : "set.cc.notInstalled")).foregroundStyle(PN.C.text3)
                        Button(L("acct.connect")) { AccountLogin.shared.connect(kind) }   // 0.24.1 (F1): installs the tool, then signs in
                    case .checking:
                        Image(systemName: "circle.dotted").foregroundStyle(PN.C.text3)
                        Text(L("set.cc.checking")).foregroundStyle(PN.C.text3)
                    case .signedIn:
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(PN.C.turn)
                        Text(kind == "codex" ? L("ai.codex.connected")
                             : L("set.cc.signedIn") + (prep.authInfo.isEmpty ? "" : " (\(prep.authInfo))"))
                            .foregroundStyle(PN.C.text3)
                        Button(L("ai.acct.signOut")) { confirmOut = true }.disabled(busy)
                    case .signedOut:
                        Image(systemName: "xmark.circle.fill").foregroundStyle(PN.C.danger)
                        Text(L("ai.acct.signedOut")).foregroundStyle(PN.C.danger)
                        Button(L("ai.acct.signIn")) { AccountLogin.shared.connect(kind) }
                    }
                    if st != .notInstalled {
                        if busy { PNSpinner(size: 12) }
                        Button(L("set.check")) { watch.check(kind) }.disabled(busy)
                    }
                }
            }
            .help(L(kind == "codex" ? "ai.codex.notSignedIn" : "ai.access.accountHelp"))
            if let r = watch.result[kind] {
                Text(r).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            }
            AccountLoginStatus(kind: kind)
        }
        .alert(L("ai.acct.signOutTitle." + kind), isPresented: $confirmOut) {
            Button(L("ai.acct.signOut"), role: .destructive) { watch.signOut(kind) }
            Button(L("common.cancel"), role: .cancel) {}
        } message: { Text(L("ai.acct.signOutBody." + kind)) }
        .onAppear { kind == "codex" ? codex.refresh() : prep.refreshAuth() }
    }
}
