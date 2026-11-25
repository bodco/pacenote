import Foundation

/// PB-127 (owner 25.11): the rules every run of the Notion meeting page (wrapup_notion) shares, whoever starts it (after
/// the call, the notes fill, Regenerate, a move to another project), so the page says what the call page says.
public enum NotionPageRun {
    /// The AI keys a run that makes no model request never gets (the Notion page, its fill, the moves).
    public static let aiKeys = ["CLAUDE_API", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "CLAUDE_WORKSPACE"]

    public static func withoutAI(_ env: [String: String]) -> [String: String] {
        var e = env
        for k in aiKeys { e.removeValue(forKey: k) }
        return e
    }

    /// A wrapup_notion run for a call (`--md`) gets the call page's action item lines (`--actions`, from its register)
    /// when its caller did not pass them; other modules and runs that already have them are left as they are.
    public static func withActions(module: String, args: [String], actions: () -> [String]) -> [String] {
        guard module == "wrapup_notion", args.contains("--md"), !args.contains("--actions") else { return args }
        return args + actions()
    }
}

/// PB-127 (review 3): the calls whose Notion page was written before their notes were ready and still waits for the
/// notes fill. Kept in a small file (state/notion-fills.json), so an app quit, an update or a failed fill does not leave
/// the page at "being written" for good: the app tries again at launch. An entry goes only after a fill Notion took.
public struct NotionFillQueue {
    public let path: String
    public init(path: String) { self.path = path }

    public func all() -> [String] {
        guard let d = FileManager.default.contents(atPath: path),
              let a = (try? JSONSerialization.jsonObject(with: d)) as? [String] else { return [] }
        return a
    }

    public func add(_ file: String) {
        var a = all()
        guard !file.isEmpty, !a.contains(file) else { return }
        a.append(file)
        save(a)
    }

    public func remove(_ file: String) {
        let a = all()
        guard a.contains(file) else { return }
        save(a.filter { $0 != file })
    }

    /// At launch: which waiting calls to fill now, and which to forget (the call file or its Notion page is gone).
    /// A call whose notes are being written again is kept for later: its notes' end fills the page anyway.
    public static func due(_ files: [String], exists: (String) -> Bool, hasPage: (String) -> Bool,
                           inWork: (String) -> Bool) -> (fill: [String], drop: [String]) {
        var fill: [String] = [], drop: [String] = []
        for f in files {
            if !exists(f) || !hasPage(f) { drop.append(f) } else if !inWork(f) { fill.append(f) }
        }
        return (fill, drop)
    }

    private func save(_ a: [String]) {
        let dir = (path as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        if let d = try? JSONSerialization.data(withJSONObject: a) { try? d.write(to: URL(fileURLWithPath: path), options: .atomic) }
    }
}
