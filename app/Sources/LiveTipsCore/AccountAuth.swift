import Foundation

/// PB-98 (owner 25.11): the sign-in state of the Claude account (Claude Code) and the ChatGPT account (Codex) in
/// Settings > AI. Two sources: the tool's own local status (`claude auth status`, `codex login status`) and the out
/// mark of the AI's last real request (state/ai-routes.json, reason "auth"). A session that was revoked still looks
/// signed in to the local file, so either source saying "out" shows Signed out; the row never says signed in then.
public enum AccountAuth {
    public enum State: String, Sendable { case notInstalled, checking, signedIn, signedOut }

    /// The route of each account in the AI chain.
    public static func route(_ kind: String) -> String { kind == "codex" ? "codex" : "code" }

    public static func isAuthOut(_ out: AIOut.State?) -> Bool { out?.down == true && out?.reason == "auth" }

    /// `local`: the tool's own status, nil until checked. `out`: the AI's out state.
    public static func state(installed: Bool, local: Bool?, out: AIOut.State?) -> State {
        guard installed else { return .notInstalled }
        if local == false || isAuthOut(out) { return .signedOut }
        return local == true ? .signedIn : .checking
    }

    /// The tool's own sign-out (it also signs out that tool in Terminal on this Mac: the credentials are shared).
    public static func signOutArguments(_ kind: String) -> [String] {
        switch kind {
        case "claude": return ["auth", "logout"]
        case "codex": return ["logout"]
        default: return []
        }
    }

    /// A sign-in made elsewhere (Terminal, another app): the tool said signed out and now says signed in, so the
    /// "signed out" mark of the last request no longer holds.
    public static func clearsOutMark(previous: Bool?, now: Bool?, out: AIOut.State?) -> Bool {
        previous == false && now == true && isAuthOut(out)
    }

    /// The tool says signed in, the last request said signed out (a revoked session, or a new sign-in in Terminal
    /// over the old one): one tiny live request tells which. Every 3 minutes in the first hour after the mark, then
    /// every 15 minutes, so a sign-in elsewhere shows within a few minutes without a stream of requests.
    public static let recheckFirstS: Double = 180
    public static let recheckLaterS: Double = 900
    /// When the app gets focus (the PM may have just signed in in Terminal): once a minute, whatever the mark's age.
    public static let recheckFocusS: Double = 60
    public static func needsLiveCheck(local: Bool?, out: AIOut.State?, lastCheck: Double?, now: Double, focus: Bool = false) -> Bool {
        guard local == true, isAuthOut(out) else { return false }
        let since = out?.since ?? now
        let every = focus ? recheckFocusS : now - since < 3600 ? recheckFirstS : recheckLaterS
        let last = max(lastCheck ?? 0, out?.checked ?? 0)
        return now - last >= every
    }

    /// How often the app reads the tools' local status in the background (and on every window focus).
    public static let pollS: Double = 120
}
