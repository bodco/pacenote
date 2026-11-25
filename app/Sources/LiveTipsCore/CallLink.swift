import Foundation

/// PB-117 (SX8): the link to a call page, `pacenote://call/<id>`. The id is the call key (the transcript's name
/// without `.md` and `.app`, CommitmentRegister.callKey), so it stays the same when the call moves to another
/// project. The Notion page writer (wrapup_notion.call_link) builds the same string.
/// A link can come from any app or web page: the id is only compared with the calls Pacenote already knows, it is
/// never turned into a path.
public enum CallLink {
    public static let scheme = "pacenote"
    public static let host = "call"

    /// RFC 3986 unreserved characters; everything else is percent-encoded (Python's quote(id, safe="") gives the same).
    private static let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

    public static func id(ofFile file: String) -> String { CommitmentRegister.callKey(file) }

    /// "pacenote://call/<id>" for a call's transcript, "" for an empty one.
    public static func url(file: String) -> String {
        let id = id(ofFile: file)
        guard !id.isEmpty, let e = id.addingPercentEncoding(withAllowedCharacters: unreserved) else { return "" }
        return "\(scheme)://\(host)/\(e)"
    }

    public static func isPacenote(_ u: URL) -> Bool { u.scheme?.lowercased() == scheme }

    /// The call id of a `pacenote://call/<id>` link; nil for any other link (another host, no id, a nested path).
    public static func id(of u: URL) -> String? {
        guard isPacenote(u), u.host?.lowercased() == host,
              let raw = URLComponents(url: u, resolvingAgainstBaseURL: false)?.percentEncodedPath else { return nil }
        let p = raw.hasPrefix("/") ? String(raw.dropFirst()) : raw
        guard !p.isEmpty, !p.contains("/"), let id = p.removingPercentEncoding,
              !id.isEmpty, !id.contains("/"), id != ".", id != ".." else { return nil }
        return id
    }

    /// A link waits for sign-in, the saved keys and Home at most this long; after that it is dropped, so a click from
    /// long ago never moves Home to a call page (or shows a notice) after a later call or sign-in.
    public static let pendingMaxAge: TimeInterval = 180

    public static func stillWanted(receivedAt: Date, now: Date) -> Bool {
        let age = now.timeIntervalSince(receivedAt)
        return age >= 0 && age <= pendingMaxAge
    }

    /// The transcript of the call with this id among the known calls (the newest when two share it), nil when none
    /// is known or its file is gone.
    public static func resolve(_ id: String, calls: [(file: String, start: Date)], exists: (String) -> Bool) -> String? {
        calls.filter { !$0.file.isEmpty && self.id(ofFile: $0.file) == id && exists($0.file) }
            .max { $0.start < $1.start }?.file
    }
}
