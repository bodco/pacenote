import Foundation
import LiveTipsCore

/// PB-98: Sign in / Sign out for the Claude and ChatGPT accounts, a sign-in made elsewhere, an honest Check.
func accountAuthPB98() {
    let authOut = AIOut.State(down: true, reason: "auth", since: 1000, checked: 1000)
    let limitOut = AIOut.State(down: true, reason: "limit", since: 1000, checked: 1000, until: 9000)

    // 1. Sign out and Sign in: the tools' own commands, and the row's state follows the tool
    expect(AccountAuth.signOutArguments("claude") == ["auth", "logout"], "PB-98: Claude account signs out with claude auth logout")
    expect(AccountAuth.signOutArguments("codex") == ["logout"], "PB-98: ChatGPT account signs out with codex logout")
    expect(AccountAuth.signOutArguments("gemini").isEmpty, "PB-98: no sign-out for a key-only provider")
    expect(CLILogin.arguments("claude") == ["auth", "login"] && CLILogin.arguments("codex").last == "login", "PB-98: Sign in runs the tools' own sign-in")
    expect(AccountAuth.route("claude") == "code" && AccountAuth.route("codex") == "codex", "PB-98: account to route")
    expect(AccountAuth.state(installed: true, local: true, out: nil) == .signedIn, "PB-98: signed in")
    expect(AccountAuth.state(installed: true, local: false, out: nil) == .signedOut, "PB-98: after Sign out the row says Signed out")
    expect(AccountAuth.state(installed: true, local: nil, out: nil) == .checking, "PB-98: not checked yet")
    expect(AccountAuth.state(installed: false, local: true, out: nil) == .notInstalled, "PB-98: no tool, Connect installs it")

    // 2. A revoked session (the local file still says signed in, the last request said signed out): Signed out, never signed in
    expect(AccountAuth.state(installed: true, local: true, out: authOut) == .signedOut, "PB-98: revoked session shows Signed out, not signed in")
    expect(AccountAuth.state(installed: true, local: nil, out: authOut) == .signedOut, "PB-98: revoked and not checked yet: Signed out")
    expect(AccountAuth.state(installed: true, local: true, out: limitOut) == .signedIn, "PB-98: a limit is not a sign-out")
    expect(AccountAuth.state(installed: true, local: true, out: AIOut.State(down: false, reason: "auth")) == .signedIn, "PB-98: a mark that is back up is not out")

    // 3. A sign-in made in Terminal is picked up and clears the out mark
    expect(AccountAuth.clearsOutMark(previous: false, now: true, out: authOut), "PB-98: signed out then signed in elsewhere clears the mark")
    expect(!AccountAuth.clearsOutMark(previous: true, now: true, out: authOut), "PB-98: no change: the mark stays until a live check")
    expect(!AccountAuth.clearsOutMark(previous: false, now: true, out: limitOut), "PB-98: a sign-in does not clear a limit")
    expect(!AccountAuth.clearsOutMark(previous: nil, now: true, out: authOut), "PB-98: the first read is not a sign-in")
    expect(!AccountAuth.clearsOutMark(previous: false, now: false, out: authOut), "PB-98: still signed out")
    expect(AccountAuth.pollS <= 180, "PB-98: the local status is read at least every 3 minutes")
    // the local file says signed in over an out mark: a live request within a few minutes, then less often
    expect(AccountAuth.needsLiveCheck(local: true, out: authOut, lastCheck: nil, now: 1000 + 180), "PB-98: live check 3 minutes after the mark")
    expect(!AccountAuth.needsLiveCheck(local: true, out: authOut, lastCheck: 1100, now: 1200), "PB-98: not more often than every 3 minutes")
    expect(!AccountAuth.needsLiveCheck(local: true, out: authOut, lastCheck: nil, now: 1100), "PB-98: the engine's own check counts")
    expect(AccountAuth.needsLiveCheck(local: true, out: authOut, lastCheck: 4000, now: 4000 + 900) &&
           !AccountAuth.needsLiveCheck(local: true, out: authOut, lastCheck: 4000, now: 4000 + 600), "PB-98: after an hour every 15 minutes")
    expect(AccountAuth.needsLiveCheck(local: true, out: authOut, lastCheck: 4000, now: 4060, focus: true) &&
           !AccountAuth.needsLiveCheck(local: true, out: authOut, lastCheck: 4000, now: 4030, focus: true),
           "PB-98: on focus a live check once a minute, also an hour after the mark")
    expect(!AccountAuth.needsLiveCheck(local: true, out: nil, lastCheck: nil, now: 5000, focus: true), "PB-98: focus without a mark sends nothing")
    expect(!AccountAuth.needsLiveCheck(local: false, out: authOut, lastCheck: nil, now: 5000), "PB-98: signed out locally: no request")
    expect(!AccountAuth.needsLiveCheck(local: true, out: nil, lastCheck: nil, now: 5000), "PB-98: signed in and not out: no request")
    expect(!AccountAuth.needsLiveCheck(local: true, out: limitOut, lastCheck: nil, now: 5000), "PB-98: a limit is not checked here")
}
