import Foundation

// PB-12 (owner 25.11): two-factor sign-in with a code from an authenticator app (TOTP, RFC 6238: SHA-1, 6 digits,
// 30 s), 10 one-time recovery codes, replace, turn off, the organization policy, the staff reset. The server keeps
// the secret and the hashed recovery codes and checks every code; the app only shows, asks and sends. Pure helpers
// (CoreChecks `twoFactorPB12`): the /v1/me state, the sign-in challenge, request bodies, the code field, the
// recovery codes and their download, the otpauth URI for the QR, what the app offers after sign-in.
//
// The server side lives in the server repository and was not in this worktree. The routes, fields and problem types
// below are the app's contract (DEVELOPMENT.md, "Two-factor sign-in"); a server without them sends no `two_factor`
// in /v1/me and never a `two_factor_required`, and the app then shows and asks nothing new.

public enum TwoFactor {

    // MARK: routes

    /// POST: the second step of a password sign-in. Body `verifyBody`, answer a TokenPair.
    public static let verifyPath = "/v1/auth/two-factor/verify"
    /// POST: a new secret (first time, or a replacement with `proofBody`). Answer `Setup`.
    public static let setupPath = "/v1/me/two-factor/setup"
    /// POST: the first good code turns it on (or swaps in the new secret). Answer `{recovery_codes: [...]}` when new
    /// codes were made (first time), else `{}`.
    public static let confirmPath = "/v1/me/two-factor/confirm"
    /// POST with `proofBody`: a new set of 10 recovery codes, the old set stops working.
    public static let recoveryPath = "/v1/me/two-factor/recovery-codes"
    /// DELETE with `proofBody`: turns it off.
    public static let disablePath = "/v1/me/two-factor"

    // MARK: /v1/me `auth.two_factor`

    public struct State: Equatable {
        public var enabled: Bool
        /// An organization of this person (or the staff rule) requires it.
        public var required: Bool
        /// The server asks the app to offer enrolment once (after a support reset).
        public var offer: Bool
        public var recoveryLeft: Int
        public var enabledAt: Date?
        public init(enabled: Bool, required: Bool = false, offer: Bool = false, recoveryLeft: Int = 0, enabledAt: Date? = nil) {
            self.enabled = enabled; self.required = required; self.offer = offer; self.recoveryLeft = recoveryLeft; self.enabledAt = enabledAt
        }
    }
    /// nil: a server without two-factor sign-in (the section stays hidden).
    public static func state(_ o: Any?) -> State? {
        guard let t = o as? [String: Any], let on = t["enabled"] as? Bool else { return nil }
        return State(enabled: on, required: (t["required"] as? Bool) == true, offer: (t["offer"] as? Bool) == true,
                     recoveryLeft: max(0, (t["recovery_codes_left"] as? Int) ?? 0), enabledAt: PacenoteServer.isoDate(t["enabled_at"]))
    }

    /// What the app does after sign-in or a /v1/me read: nothing, offer enrolment (support reset), or ask for it
    /// before anything else (an organization requires it).
    public enum Prompt: Equatable { case none, offer, required }
    public static func prompt(_ s: State?) -> Prompt {
        guard let s, !s.enabled else { return .none }
        if s.required { return .required }
        return s.offer ? .offer : .none
    }

    // MARK: the sign-in challenge

    /// 401 `two_factor_required` from a password sign-in (login, password reset, Google link): the password was right,
    /// the code comes next. The device was already sent with the password.
    public struct Challenge: Equatable {
        public var id: String
        public var expires: Date
        public var email: String
        public init(id: String, expires: Date, email: String) { self.id = id; self.expires = expires; self.email = email }
    }
    public static func challenge(_ data: Data, status: Int, email: String, now: Date = Date()) -> Challenge? {
        guard status == 401 || status == 403 || status == 200,
              let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        var t = (o["type"] as? String) ?? ""
        if t.contains("/") { t = String(t.split(separator: "/").last ?? "") }
        guard t == "two_factor_required", let id = o["challenge_id"] as? String, !id.isEmpty else { return nil }
        let life = max(60, (o["expires_in"] as? Int) ?? 300)
        return Challenge(id: id, expires: now.addingTimeInterval(Double(life)), email: PacenoteServer.cleanEmail(email))
    }

    // MARK: what the person types

    /// A code from the app ("123 456", "123-456") or a recovery code ("k7m2p-9xqfd"). Six digits are a code, anything
    /// else with letters or digits is a recovery code (recovery codes always hold a letter).
    public enum Proof: Equatable { case code(String), recovery(String) }
    public static func proof(_ s: String) -> Proof? {
        let digits = codeDigits(s)
        let alnum = s.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) && $0.isASCII }
        if digits.count == 6 && alnum.count == 6 { return .code(digits) }
        let r = recoveryNormal(s)
        return r.count >= 8 && r.contains(where: { $0.isLetter }) ? .recovery(r) : nil
    }

    /// The digits of an app code, at most 6 (the email code helpers are 8 digits).
    public static func codeDigits(_ s: String) -> String {
        String(s.unicodeScalars.filter { CharacterSet.decimalDigits.contains($0) && $0.isASCII }.map(Character.init).prefix(6))
    }
    /// The field shows "123 456".
    public static func formatCode(_ s: String) -> String {
        let d = codeDigits(s)
        return d.count > 3 ? String(d.prefix(3)) + " " + String(d.dropFirst(3)) : d
    }
    public static func codeComplete(_ s: String) -> Bool { codeDigits(s).count == 6 }

    /// A recovery code as the server compares it: lowercase letters and digits only.
    public static func recoveryNormal(_ s: String) -> String {
        String(s.lowercased().unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) && $0.isASCII }.map(Character.init).prefix(32))
    }

    // MARK: request bodies (the server rejects unknown fields)

    static func body(_ o: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: o, options: [.sortedKeys])) ?? Data()
    }
    /// `{code}` or `{recovery_code}`: what proves it is you (replace, regenerate, turn off).
    public static func proofBody(_ p: Proof) -> Data {
        switch p {
        case .code(let c): return body(["code": c])
        case .recovery(let r): return body(["recovery_code": r])
        }
    }
    public static func verifyBody(challengeID: String, proof: Proof) -> Data {
        switch proof {
        case .code(let c): return body(["challenge_id": challengeID, "code": c])
        case .recovery(let r): return body(["challenge_id": challengeID, "recovery_code": r])
        }
    }
    public static func confirmBody(setupID: String, code: String) -> Data { body(["setup_id": setupID, "code": codeDigits(code)]) }

    // MARK: setup and the QR

    public struct Setup: Equatable {
        public var id: String
        /// Base32, no padding.
        public var secret: String
        /// What the QR holds: the server's URI when it is a valid one, else built here from the secret.
        public var uri: String
        public init(id: String, secret: String, uri: String) { self.id = id; self.secret = secret; self.uri = uri }
    }
    public static func setup(_ o: [String: Any], account: String) -> Setup? {
        guard let id = o["setup_id"] as? String, !id.isEmpty,
              let raw = o["secret"] as? String, let secret = base32Clean(raw) else { return nil }
        let given = (o["otpauth_uri"] as? String) ?? ""
        let uri = uriValid(given, secret: secret) ? given : otpauthURI(secret: secret, account: account)
        return Setup(id: id, secret: secret, uri: uri)
    }

    public static let issuer = "Pacenote"

    /// The standard key URI: `otpauth://totp/Pacenote:mike%40example.com?secret=...&issuer=Pacenote&algorithm=SHA1&digits=6&period=30`.
    /// Every authenticator app reads it (Google Authenticator, Microsoft Authenticator, Authy, Apple Passwords, 1Password).
    public static func otpauthURI(secret: String, account: String) -> String {
        var allowed = CharacterSet.alphanumerics; allowed.insert(charactersIn: "-._~")
        let label = issuer + ":" + (account.addingPercentEncoding(withAllowedCharacters: allowed) ?? account)
        return "otpauth://totp/" + label + "?secret=" + secret + "&issuer=" + issuer + "&algorithm=SHA1&digits=6&period=30"
    }

    /// A URI the app may show: otpauth://totp, the same secret, and SHA1 / 6 / 30 s when it says anything else.
    public static func uriValid(_ s: String, secret: String) -> Bool {
        guard let c = URLComponents(string: s), c.scheme == "otpauth", c.host == "totp" else { return false }
        var q: [String: String] = [:]
        for i in c.queryItems ?? [] { q[i.name.lowercased()] = i.value ?? "" }
        guard base32Clean(q["secret"] ?? "") == secret else { return false }
        if let a = q["algorithm"], a.uppercased() != "SHA1" { return false }
        if let d = q["digits"], d != "6" { return false }
        if let p = q["period"], p != "30" { return false }
        return true
    }

    /// A Base32 secret (RFC 4648) uppercased, without spaces or padding; nil when it holds anything else or is shorter
    /// than 80 bits (RFC 4226 asks for at least 128, the server makes 160).
    public static func base32Clean(_ s: String) -> String? {
        let t = s.uppercased().filter { $0 != " " && $0 != "=" && $0 != "-" }
        guard t.count >= 16, t.allSatisfy({ ("A"..."Z").contains($0) || ("2"..."7").contains($0) }) else { return nil }
        return t
    }
    /// The key as typed into an app by hand: groups of 4.
    public static func groupedKey(_ secret: String) -> String {
        var out: [String] = []
        var cur = ""
        for ch in secret { cur.append(ch); if cur.count == 4 { out.append(cur); cur = "" } }
        if !cur.isEmpty { out.append(cur) }
        return out.joined(separator: " ")
    }

    // MARK: recovery codes

    public static func recoveryCodes(_ o: [String: Any]) -> [String] {
        ((o["recovery_codes"] as? [Any]) ?? []).compactMap { $0 as? String }.filter { !$0.isEmpty }
    }

    /// The file the person downloads: the heading lines (in the interface language), then one code per line.
    public static func recoveryFile(codes: [String], heading: [String]) -> String {
        (heading + [""] + codes).joined(separator: "\n") + "\n"
    }

    // MARK: problems

    /// The interface key for a two-factor problem, or nil to use the general sign-in texts.
    public static func messageKey(_ p: PacenoteServer.AuthProblem) -> String? {
        switch p.type {
        case "invalid_code": return p.attemptsLeft.map { $0 <= 0 } == true ? "auth.2fa.err.spent" : "auth.2fa.err.code"
        case "code_expired", "challenge_expired": return "auth.2fa.err.expired"
        case "two_factor_required_by_org": return "account.2fa.err.required"
        case "two_factor_off": return "account.2fa.err.off"
        default: return nil
        }
    }
}
