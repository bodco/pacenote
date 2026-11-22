import Foundation

/// One output language (PB-32, FB2, owner decision D56): the language of everything a call writes is the call language.
/// Rules: rules/output_language.yaml. The same resolver in Python is spikes/m0/livetips_m0/call_language.py; both are
/// checked against Fixtures/output_language_cases.json. The interface language (L10n.lang) is never an input.
///
/// Order: explicit choice for this call > dominant language of the call > first language of Start call >
/// project client_language (a hint) > Settings > "en".
public enum CallLanguage {
    public static let defaultCode = "en"
    public static let spokenMinShare = 0.1
    public static let maxLanguages = 3
    public static let artifacts = ["notes", "meeting_notes", "followup", "notion", "sheet"]
    public static let suffix = ".outlang.json"

    public static let names: [String: String] = ["uk": "Ukrainian", "en": "English", "pl": "Polish", "de": "German", "es": "Spanish",
        "fr": "French", "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "cs": "Czech", "sk": "Slovak", "hu": "Hungarian"]
    static let clientHints: [(String, String)] = [("ukrain", "uk"), ("україн", "uk"), ("english", "en"), ("англій", "en"),
        ("spanish", "es"), ("español", "es"), ("german", "de"), ("deutsch", "de"), ("french", "fr"), ("polish", "pl"), ("polsk", "pl")]

    /// "uk", "UK", "uk-UA", "uk_UA" -> "uk"; "", "auto", "ukr", "Ukrainian", nil -> nil.
    public static func normalize(_ code: String?) -> String? {
        guard let raw = code else { return nil }
        let c = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased().replacingOccurrences(of: "_", with: "-")
            .split(separator: "-", omittingEmptySubsequences: false).first.map(String.init) ?? ""
        return c.count == 2 && c.unicodeScalars.allSatisfy({ $0.value >= 97 && $0.value <= 122 }) ? c : nil
    }

    /// The name given to a model ("Ukrainian"); a code without a name is written as the code.
    public static func name(_ code: String?) -> String {
        let c = normalize(code) ?? defaultCode
        return names[c] ?? c
    }

    /// The sentence that makes the resolved output language authoritative in a model request (prep brief, refresh).
    public static func promptLine(_ code: String?) -> String {
        "Write the whole brief in \(name(code)). This is the call's output language: it overrides any language named in the project config, the context or the skill files."
    }

    /// The project's client_language free text ("English", "Ukrainian (formal)") -> a code, nil when not recognised.
    public static func hint(fromClientLanguage text: String?) -> String? {
        let t = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for (k, v) in clientHints where t.hasPrefix(k) { return v }
        return normalize(t)
    }

    static func codes(_ items: [String]?) -> [String] {
        var out: [String] = []
        for x in items ?? [] { if let c = normalize(x), !out.contains(c) { out.append(c) } }
        return out
    }

    /// What the recorder says was spoken: the dominant language and the share of each language (ordered, first seen first).
    public struct Spoken: Equatable {
        public var dominant: String?
        public var shares: [(code: String, share: Double)]
        public init(dominant: String? = nil, shares: [(code: String, share: Double)] = []) { self.dominant = dominant; self.shares = shares }
        public static func == (a: Spoken, b: Spoken) -> Bool {
            a.dominant == b.dominant && a.shares.map { $0.code } == b.shares.map { $0.code } && a.shares.map { $0.share } == b.shares.map { $0.share }
        }

        /// The dominant language first, then those at or above the spoken threshold, the biggest share first (ties: first seen).
        public func languages() -> [String] {
            let ranked = shares.enumerated().sorted { $0.element.share != $1.element.share ? $0.element.share > $1.element.share : $0.offset < $1.offset }
            let rest = ranked.filter { $0.element.share >= CallLanguage.spokenMinShare - 1e-9 && $0.element.code != dominant }.map { $0.element.code }
            return Array((((dominant.map { [$0] }) ?? []) + rest).prefix(CallLanguage.maxLanguages))
        }
    }

    private static let tagRE = try! NSRegularExpression(pattern: #"^\[\d\d:\d\d:\d\d\][^\n(]*\(([a-z]{2})\):"#, options: .anchorsMatchLines)
    private static let headerRE = try! NSRegularExpression(pattern: #"^- (?:Languages|Мови):[^\n=]*=\s*([a-z]{2})"#, options: .anchorsMatchLines)

    private static func firstGroup(_ re: NSRegularExpression, _ text: String) -> [String] {
        let ns = text as NSString
        return re.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { ns.substring(with: $0.range(at: 1)) }
    }

    /// The adapter (the only function that knows the recorder's shape; PB-30 may change it, nothing else does).
    /// 1. `meta` of the call when PB-30 writes it: `dominant_language` ("uk" or empty) and `languages` ({"uk": 0.71, ...}
    ///    or ["uk", "en"]).
    /// 2. Otherwise the transcript: the majority of the line tags "[13:01:04] Name (uk): ..." (the value
    ///    callnotes.call_lang gives) and the share of each tag; no tags: the header's first language, no shares.
    public static func spoken(transcript: String = "", meta: [String: Any]? = nil) -> Spoken {
        var dom = normalize(meta?["dominant_language"] as? String)
        var shares: [(code: String, share: Double)] = []
        if let d = meta?["languages"] as? [String: Any] {
            for (k, v) in d.sorted(by: { $0.key < $1.key }) {
                guard let c = normalize(k), let n = v as? NSNumber, String(cString: n.objCType) != "c", n.doubleValue > 0 else { continue }
                shares.append((c, n.doubleValue))
            }
        } else if let l = meta?["languages"] as? [Any] {
            let list = codes(l.compactMap { $0 as? String })
            shares = list.map { ($0, 1.0 / Double(list.count)) }
        }
        if dom == nil, let top = shares.max(by: { $0.share < $1.share }) {   // no dominant field: the largest share (ties: the first code in alphabetical order; a list: first listed)
            dom = shares.first { $0.share == top.share }?.code
        }
        if shares.isEmpty || dom == nil {
            let tags = firstGroup(tagRE, transcript)
            if !tags.isEmpty {
                var order: [String] = [], counts: [String: Int] = [:]
                for t in tags { if counts[t] == nil { order.append(t) }; counts[t, default: 0] += 1 }
                if shares.isEmpty { shares = order.map { ($0, Double(counts[$0]!) / Double(tags.count)) } }
                if dom == nil { dom = order.first { counts[$0] == counts.values.max() } }   // the biggest, ties: first seen
            } else if dom == nil {
                dom = firstGroup(headerRE, transcript).first
            }
        }
        return Spoken(dominant: dom, shares: shares)
    }

    /// The output language of a call. Pass `dominant` from `spoken(...).dominant` after the call, nil before it.
    public static func resolve(explicit: String? = nil, dominant: String? = nil, startLangs: [String] = [],
                               clientLanguage: String = "", settings: String = "") -> String {
        normalize(explicit) ?? normalize(dominant) ?? codes(startLangs).first ?? hint(fromClientLanguage: clientLanguage)
            ?? normalize(settings) ?? defaultCode
    }

    /// The languages the Output language control lists: after the call those spoken from 10% of it, before the call
    /// the ones chosen in Start call (up to 3). The control is shown only for two or more.
    public static func toggleLanguages(_ sp: Spoken?, startLangs: [String] = [], afterCall: Bool = true) -> [String] {
        if afterCall, let sp = sp, !sp.languages().isEmpty { return sp.languages() }
        return Array(codes(startLangs).prefix(maxLanguages))
    }

    public static func isMulti(_ languages: [String]) -> Bool { languages.count >= 2 }

    // MARK: the follow-up Write rules (the call page tab and the tips window share them)

    /// A draft exists when a language is remembered or any text is in it.
    public static func followUpHasDraft(language: String?, subject: String, body: String) -> Bool { language != nil || !subject.isEmpty || !body.isEmpty }

    /// Write runs when nothing is running and there is no draft, or the caller replaces it.
    public static func followUpMayWrite(busy: Bool, hasDraft: Bool, replacingDraft: Bool) -> Bool { !busy && (!hasDraft || replacingDraft) }

    /// The tips window's Write: an emptied draft (its language is still remembered) is written again; a draft with text is kept.
    public static func tipsWriteReplacesDraft(body: String) -> Bool { body.isEmpty }

    // MARK: the per-call sidecar (<stem>.outlang.json, in the call folder's .work/)

    public struct Sidecar: Equatable {
        public var outputLanguage: String?
        public var setBy: String?
        public var followUpUsesClientLanguage = false
        public var staleArtifacts: [String] {
            CallLanguage.staleArtifacts(artifacts, outputLanguage: outputLanguage,
                                        followUpUsesClientLanguage: followUpUsesClientLanguage)
        }
        public var artifacts: [String: String]
        public init(outputLanguage: String? = nil, setBy: String? = nil, artifacts: [String: String] = [:]) {
            self.outputLanguage = outputLanguage; self.setBy = setBy; self.artifacts = artifacts
        }
    }

    /// "X.named.md" and "X.md" are one call: one sidecar.
    static func plainTranscript(_ t: String) -> String { t.hasSuffix(".named.md") ? String(t.dropLast(9)) + ".md" : t }

    public static func sidecarPath(transcript: String) -> String { CallLayout.sidecar(of: plainTranscript(transcript), suffix) }

    /// The call's own metadata, nil when absent or unreadable: the recorder's <project>/video/<stem>.recording.json
    /// (RecordingFiles: ".app" and ".md" stripped from the stem), where PB-30 puts `dominant_language` and `languages`.
    static func loadMeta(transcript: String) -> [String: Any]? {
        guard let d = FileManager.default.contents(atPath: RecordingFiles.metaPath(transcript: plainTranscript(transcript))) else { return nil }
        return (try? JSONSerialization.jsonObject(with: d)) as? [String: Any]
    }

    /// What was spoken in this call, from its metadata when it has some and else from the transcript text: the one result
    /// the Output language menu, its default and every artifact read (the menu must not disagree with the default).
    public static func spoken(forCall transcript: String, text: String) -> Spoken {
        spoken(transcript: text, meta: loadMeta(transcript: transcript))
    }

    /// Absent, empty, half-written or unreadable: nothing chosen; never an error.
    public static func readSidecar(transcript: String) -> Sidecar {
        guard let data = FileManager.default.contents(atPath: sidecarPath(transcript: transcript)),
              let d = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return Sidecar() }
        var s = Sidecar(outputLanguage: normalize(d["output_language"] as? String), setBy: d["set_by"] as? String)
        for (k, v) in (d["artifacts"] as? [String: Any]) ?? [:] where artifacts.contains(k) {
            if let c = normalize(v as? String) { s.artifacts[k] = c }
        }
        s.followUpUsesClientLanguage = d["followup_uses_client_language"] as? Bool == true
        return s
    }

    /// Set the PM's choice (`outputLanguage`) and/or record the language an artifact was written in. Atomic; keeps what
    /// is already there; returns what is stored now (nil when the file could not be written).
    @discardableResult
    public static func writeSidecar(transcript: String, outputLanguage: String? = nil, artifact: String? = nil, writtenIn: String? = nil,
                                    followUpUsesClientLanguage: Bool = false) -> Sidecar? {
        var cur = readSidecar(transcript: transcript)
        if let c = normalize(outputLanguage) { cur.outputLanguage = c; cur.setBy = "user" }
        if let a = artifact, artifacts.contains(a), let c = normalize(writtenIn) {
            cur.artifacts[a] = c
            if a == "followup" { cur.followUpUsesClientLanguage = followUpUsesClientLanguage }
        }
        var body: [String: Any] = ["artifacts": cur.artifacts]
        if let c = cur.outputLanguage { body["output_language"] = c }
        if let b = cur.setBy { body["set_by"] = b }
        if cur.followUpUsesClientLanguage { body["followup_uses_client_language"] = true }
        let path = sidecarPath(transcript: transcript)
        do {
            try FileManager.default.createDirectory(atPath: (path as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys, .prettyPrinted]).write(to: URL(fileURLWithPath: path), options: .atomic)
        } catch { return nil }
        return cur
    }

    /// Artifacts written in another language than the output language ("out of date"), in the fixed artifact order.
    public static func staleArtifacts(_ written: [String: String], outputLanguage: String?,
                                      followUpUsesClientLanguage: Bool = false) -> [String] {
        guard let out = normalize(outputLanguage) else { return [] }
        return artifacts.filter { a in
            guard !(a == "followup" && followUpUsesClientLanguage) else { return false }
            return written[a].map { normalize($0) != out } ?? false
        }
    }

    /// The whole chain for a call with a transcript: sidecar choice > dominant (adapter) > the rest.
    public static func outputLanguage(transcript: String, text: String = "", meta: [String: Any]? = nil, startLangs: [String] = [],
                                      clientLanguage: String = "", settings: String = "") -> String {
        resolve(explicit: readSidecar(transcript: transcript).outputLanguage, dominant: spoken(transcript: text, meta: meta ?? loadMeta(transcript: transcript)).dominant,
                startLangs: startLangs, clientLanguage: clientLanguage, settings: settings)
    }
}
