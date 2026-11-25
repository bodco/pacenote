import Foundation

/// PB-101 (Meeting Sheet, owner 24.11): a custom block's settings, as the build reads them (sheet/custom.py):
/// an instruction (what it does on this call), its own sources, an output format and a length. Format "verbatim" is the
/// built-in Verbatim block (PB-102): the build copies the first file or text source unchanged.
public struct SheetCustom: Codable, Equatable {
    public static let formats = ["summary", "bullets", "table", "qa", "checklist", "verbatim"]
    public static let lengths = ["short", "medium", "long"]
    public static let kinds = ["file", "folder", "call_source", "transcript", "url", "text"]

    /// One source of the block: a file or folder (path), a call source (path or link, and its id), the transcript of
    /// a past call (path), a link (uri) or free text (text). sections ("2" or "1-4") picks Markdown sections of a file.
    public struct Source: Codable, Equatable {
        public var kind: String
        public var path: String?
        public var uri: String?
        public var text: String?
        public var title: String?
        public var sections: String?
        public var sourceID: String?
        enum CodingKeys: String, CodingKey { case kind, path, uri, text, title, sections, sourceID = "source_id" }
        public init(kind: String, path: String? = nil, uri: String? = nil, text: String? = nil, title: String? = nil,
                    sections: String? = nil, sourceID: String? = nil) {
            self.kind = kind; self.path = path; self.uri = uri; self.text = text; self.title = title
            self.sections = sections; self.sourceID = sourceID
        }
        /// A file that sections can be picked from (the Verbatim and the section pick read it as a file).
        public var isFile: Bool { path != nil && kind != "folder" && kind != "url" && kind != "text" }
        /// What the source is, for the block's row and its settings.
        public var label: String {
            switch kind {
            case "text": return String((text ?? "").prefix(40))
            case "url": return uri ?? ""
            default: return title ?? ((path ?? uri ?? "") as NSString).lastPathComponent
            }
        }
        public var request: [String: Any] {
            var d: [String: Any] = ["kind": kind]
            if let p = path, !p.isEmpty { d["path"] = p }
            if let u = uri, !u.isEmpty { d["uri"] = u }
            if kind == "text" { d["text"] = text ?? "" }
            if let t = title, !t.isEmpty { d["title"] = t }
            let s = (sections ?? "").trimmingCharacters(in: .whitespaces)
            if !s.isEmpty, isFile { d["sections"] = s }
            if let i = sourceID, !i.isEmpty { d["source_id"] = i }
            return d
        }
    }

    public var libID: String?
    public var instruction: String = ""
    public var format: String = "bullets"
    public var length: String = "medium"
    public var sources: [Source] = []
    /// Written by the build: the sources the block was answered from (kept so a save does not lose it)
    public var sourceIDs: [String]?
    enum CodingKeys: String, CodingKey {
        case libID = "lib_id", instruction, format, length, sources, sourceIDs = "source_ids"
    }

    public init(libID: String? = nil, instruction: String = "", format: String = "bullets", length: String = "medium",
                sources: [Source] = []) {
        self.libID = libID; self.instruction = instruction; self.format = format; self.length = length; self.sources = sources
    }

    /// Tolerant: an unknown format or length reads as the default and a source the app cannot read is left out, so
    /// one odd block never makes the whole sheet unreadable.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        libID = try? c.decodeIfPresent(String.self, forKey: .libID)
        instruction = (try? c.decodeIfPresent(String.self, forKey: .instruction)) ?? ""
        let f = (try? c.decodeIfPresent(String.self, forKey: .format)) ?? nil
        format = Self.formats.contains(f ?? "") ? f! : "bullets"
        let l = (try? c.decodeIfPresent(String.self, forKey: .length)) ?? nil
        length = Self.lengths.contains(l ?? "") ? l! : "medium"
        struct Loose: Decodable {
            var s: Source?
            init(from d: Decoder) throws { s = try? Source(from: d) }
        }
        sources = ((try? c.decodeIfPresent([Loose].self, forKey: .sources)) ?? nil)?.compactMap(\.s).filter { Self.kinds.contains($0.kind) } ?? []
        sourceIDs = try? c.decodeIfPresent([String].self, forKey: .sourceIDs)
    }

    public var isVerbatim: Bool { format == "verbatim" }

    /// The request's form (sheet/custom.py settings_of).
    public var request: [String: Any] {
        var d: [String: Any] = ["instruction": instruction.trimmingCharacters(in: .whitespacesAndNewlines), "format": format,
                                "length": length, "sources": sources.map(\.request)]
        if let l = libID, !l.isEmpty { d["lib_id"] = l }
        return d
    }
}

/// PB-101: one block of the PM's personal library: a title and its settings, reusable in any call.
public struct CustomBlockDef: Codable, Equatable, Identifiable {
    public var id: String
    public var title: String
    public var settings: SheetCustom
    public init(id: String = CustomBlockDef.newID(), title: String, settings: SheetCustom) {
        self.id = id; self.title = title; self.settings = settings
    }
    public static func newID() -> String { "cb-" + String(UUID().uuidString.prefix(8)).lowercased() }
}

/// PB-101: the personal library of custom blocks (one JSON file in the app's support folder). Pure: the app's store
/// loads, changes and saves it; a block added to a call is a copy, so a change in the call stays in the call until
/// Save to library.
public struct CustomBlockLibrary: Codable, Equatable {
    public var blocks: [CustomBlockDef] = []
    public init(blocks: [CustomBlockDef] = []) { self.blocks = blocks }

    /// Adds a new block or replaces the one with the same id (Save to library from a call). Returns its id.
    @discardableResult
    public mutating func save(title: String, settings: SheetCustom, id: String? = nil) -> String {
        let t = title.trimmingCharacters(in: .whitespacesAndNewlines)
        var s = settings
        s.sourceIDs = nil
        if let id, let i = blocks.firstIndex(where: { $0.id == id }) {
            s.libID = id
            blocks[i].title = t.isEmpty ? blocks[i].title : t
            blocks[i].settings = s
            return id
        }
        let nid = CustomBlockDef.newID()
        s.libID = nid
        blocks.append(CustomBlockDef(id: nid, title: t.isEmpty ? "Custom block" : t, settings: s))
        return nid
    }

    /// A copy right after the original, titled "<title> (copy)" (or the given suffix). Returns the new id.
    @discardableResult
    public mutating func duplicate(_ id: String, suffix: String = " (copy)") -> String? {
        guard let i = blocks.firstIndex(where: { $0.id == id }) else { return nil }
        var d = blocks[i]
        d.id = CustomBlockDef.newID()
        d.title += suffix
        d.settings.libID = d.id
        blocks.insert(d, at: i + 1)
        return d.id
    }

    public mutating func delete(_ id: String) { blocks.removeAll { $0.id == id } }

    public func block(_ id: String?) -> CustomBlockDef? { id.flatMap { i in blocks.first { $0.id == i } } }

    public static func load(_ url: URL) -> CustomBlockLibrary {
        guard let d = try? Data(contentsOf: url), let l = try? JSONDecoder().decode(CustomBlockLibrary.self, from: d) else { return .init() }
        return l
    }

    public func save(to url: URL) throws {
        let enc = JSONEncoder(); enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try enc.encode(self).write(to: url, options: .atomic)
    }
}
