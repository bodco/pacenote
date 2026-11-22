import Foundation

/// H4 (PB-60, docs/specs/hubspot-crm-integration.md): what Pacenote suggests for the linked deal after a call. Every
/// suggestion carries the evidence from the call page (a quote or the agreed step); a field without evidence is not
/// proposed. The evidence is shown to the PM and never written to HubSpot. Nothing here writes: the app applies the
/// ticked suggestions through `CRMConnector.updateDeal`, which returns the old values for Undo.

/// One stage of a deal pipeline in the portal (the portal's own stages, never Pacenote's).
public struct CRMStage: Equatable, Identifiable {
    public var id: String; public var label: String; public var pipeline: String; public var order: Int; public var closed: Bool
    public init(id: String, label: String, pipeline: String, order: Int, closed: Bool = false) {
        self.id = id; self.label = label; self.pipeline = pipeline; self.order = order; self.closed = closed
    }
}

/// A deal pipeline's stages are asked from the portal; implemented by the HubSpot connector.
public protocol CRMDealStages {
    func dealStages() async throws -> [CRMStage]
}

extension HubSpotConnector: CRMDealStages {
    /// Every pipeline's stages with their order and whether they close the deal (Closed won, Closed lost).
    public func dealStages() async throws -> [CRMStage] {
        let r = try await call("GET", "/crm/v3/pipelines/deals")
        var out: [CRMStage] = []
        for p in (r["results"] as? [[String: Any]]) ?? [] {
            let pid = (p["id"] as? String) ?? ""
            for s in (p["stages"] as? [[String: Any]]) ?? [] {
                guard let id = s["id"] as? String else { continue }
                let flag = (s["metadata"] as? [String: Any])?["isClosed"]
                let closed = (flag as? String) == "true" || (flag as? Bool) == true
                let order = (s["displayOrder"] as? Int) ?? Int((s["displayOrder"] as? String) ?? "") ?? 0
                out.append(CRMStage(id: id, label: (s["label"] as? String) ?? id, pipeline: pid, order: order, closed: closed))
            }
        }
        return out
    }
}

public struct CRMDealSuggestion: Equatable, Identifiable {
    public var id: String               // the HubSpot deal property: hs_next_step, closedate, dealstage or a mapped property
    public var kind: String             // nextStep, closeDate, stage or qualification
    public var title: String            // for qualification: the criterion
    public var current: String          // what the deal has now, for the review
    public var value: String            // what is written
    public var shown: String            // what the PM reads (the stage label for a stage id)
    public var evidence: String         // the quote or the agreed step; shown, never written
    public var on: Bool
    public init(id: String, kind: String, title: String = "", current: String, value: String, shown: String? = nil, evidence: String, on: Bool = true) {
        self.id = id; self.kind = kind; self.title = title; self.current = current; self.value = value
        self.shown = shown ?? value; self.evidence = evidence; self.on = on
    }
}

public enum CRMDealSuggester {
    /// Mirrors rules/sales.yaml > stages: the words that say a call moved a deal to a stage, latest stage first.
    static let stageWords: [[String]] = [
        ["closing", "close", "commit", "sign", "signed", "signing", "signature", "verbal", "закрит", "підпис"],
        ["decision", "negotiate", "negotiation", "negotiating", "contract", "переговор", "контракт", "рішен"],
        ["presentation", "proposal", "demo", "quote", "пропоз", "демо", "презент"],
    ]
    /// Whole words, not substrings ("design" and "signal" are not "sign"); Cyrillic entries are stems.
    static func has(_ text: String, _ words: [String]) -> Bool {
        let tokens = text.lowercased().split { !$0.isLetter && !$0.isNumber && $0 != "-" }.map(String.init)
        return words.contains { w in tokens.contains { t in w.allSatisfy(\.isASCII) ? t == w : t.hasPrefix(w) } }
    }
    /// A step of the mutual plan with one of these words is the closing step; its date is the close date.
    static let closeWords = ["close", "closing", "sign", "signed", "signing", "signature", "go-live", "закрит", "підпис"]

    static func s(_ v: Any?) -> String { ((v as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }

    /// A date as the call page keeps it (YYYY-MM-DD, optionally with a time); anything else ("next Tuesday") is not a date.
    public static func isoDate(_ v: String) -> String? {
        guard v.count >= 10, let r = v.range(of: "^[0-9]{4}-[0-9]{2}-[0-9]{2}", options: .regularExpression) else { return nil }
        let d = String(v[r])
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC"); f.dateFormat = "yyyy-MM-dd"
        guard let date = f.date(from: d), f.string(from: date) == d else { return nil }     // 2026-02-31 is not a date
        return d
    }

    /// The portal stages of the deal's own pipeline, in the portal's order. Empty when the pipeline is unknown.
    public static func pipelineStages(_ all: [CRMStage], pipeline: String) -> [CRMStage] {
        pipeline.isEmpty ? [] : all.filter { $0.pipeline == pipeline }.sorted { $0.order < $1.order }
    }

    /// sales = <stem>.sales.json as edited on the call page. mapping = criterion -> deal property (Settings). `stages`
    /// = the portal's stages of this deal's pipeline. Returns only fields that have evidence and differ from the deal.
    public static func build(sales: [String: Any], deal: CRMDeal, stages: [CRMStage], mapping: [String: String],
                             today: Date = Date()) -> [CRMDealSuggestion] {
        var out: [CRMDealSuggestion] = []
        let next = sales["next_step"] as? [String: Any] ?? [:]
        let step = s(next["what"])
        // the engine keeps only a step both sides agreed on; its quote, when there is one, is the evidence, else the step itself
        if !step.isEmpty, String(step.prefix(250)) != deal.nextStep {
            let q = s(next["quote"])
            out.append(CRMDealSuggestion(id: "hs_next_step", kind: "nextStep", current: deal.nextStep, value: String(step.prefix(250)),
                                         evidence: q.isEmpty ? step + (s(next["due"]).isEmpty ? "" : " (" + s(next["due"]) + ")") : q))
        }
        let plan = (sales["mutual_plan"] as? [[String: Any]]) ?? []
        let day = DateFormatter(); day.locale = Locale(identifier: "en_US_POSIX"); day.timeZone = TimeZone(identifier: "UTC"); day.dateFormat = "yyyy-MM-dd"
        let todayStr = day.string(from: today)
        // the closing step is chosen first and only then compared with the deal, so a re-run never flips between two steps
        if let m = plan.first(where: { p in
            guard let d = isoDate(s(p["due"])) else { return false }
            return has(s(p["step"]), closeWords) && d >= todayStr
        }), let d = isoDate(s(m["due"])), d != deal.closeDate {
            out.append(CRMDealSuggestion(id: "closedate", kind: "closeDate", current: deal.closeDate, value: d,
                                         evidence: s(m["step"]) + " (" + d + ")"))
        }
        if let st = stageSuggestion(sales: sales, deal: deal, stages: stages) { out.append(st) }
        // qualification: a criterion that was heard (known or partial) with the line it came from, to the property chosen in Settings
        for c in (sales["criteria"] as? [[String: Any]]) ?? [] {
            let key = s(c["criterion"]), what = s(c["what"]), quote = s(c["quote"]), state = s(c["state"])
            guard let prop = mapping[key], !prop.isEmpty, !what.isEmpty, !quote.isEmpty, state == "known" || state == "partial" else { continue }
            if out.contains(where: { $0.id == prop }) { continue }
            out.append(CRMDealSuggestion(id: prop, kind: "qualification", title: key, current: "", value: String(what.prefix(250)), evidence: quote))
        }
        return out
    }

    /// Words that say the milestone happened (ASCII whole words; Cyrillic entries are stems).
    static let doneWords = ["sent", "signed", "countersigned", "shared", "presented", "delivered", "received", "reviewed", "held",
                            "gave", "given", "showed", "shown", "demoed", "approved", "agreed", "accepted", "completed", "submitted",
                            "finished", "done", "надісл", "відправил", "підписал", "підписан", "показал", "провел", "отримал",
                            "погодил", "узгодил", "презентувал", "передал"]
    /// Words that say it did not happen, or not yet: negation, a plan, a wish or a request. Whole words only.
    static let notDoneWords = ["not", "never", "no", "nor", "without", "yet", "neither", "t", "ll", "will", "would", "shall", "should",
                               "going", "plan", "plans", "planned", "planning", "next", "tomorrow", "later", "soon", "want", "wants",
                               "need", "needs", "hope", "hopes", "if", "once", "until", "may", "might", "could", "can", "expect",
                               "expects", "ask", "asks", "asked", "request", "requested", "pending", "waiting", "about",
                               "не", "ні", "ніколи", "ще", "без", "буде", "будемо", "будуть", "якщо", "завтра", "пізніше", "планує",
                               "плануємо", "хоче", "хочуть", "треба", "потрібно", "просив", "просила", "просили", "чекаємо"]
    static func hasWord(_ text: String, _ words: [String]) -> Bool {
        let tokens = Set(text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init))
        return words.contains { tokens.contains($0) }
    }
    /// "Agreed to send", "погодились надіслати": an agreement about a step still to do, not the step.
    static let intentWords = ["agreed", "decided", "promised", "offered", "погодил", "узгодил", "домовил", "вирішил", "пообіцял"]
    static func intent(_ text: String) -> Bool {
        let t = text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
        return t.indices.dropLast().contains { i in
            has(t[i], intentWords) && (t[i + 1] == "to" || (!t[i + 1].allSatisfy(\.isASCII) && (t[i + 1].hasSuffix("ти") || t[i + 1].hasSuffix("тися"))))
        }
    }
    /// The text reports a milestone that took place: a completion word, no negation, no plan or request, not a question.
    /// "The contract was never sent", "We will send the contract" and "We agreed to send the contract" are not evidence
    /// for Contract sent.
    public static func happened(_ text: String) -> Bool {
        !text.contains("?") && has(text, doneWords) && !hasWord(text, notDoneWords) && !intent(text)
    }

    /// The thing a stage is about; the signal must name the same thing as the stage label ("we presented the demo" is
    /// not evidence for Proposal sent). Cyrillic entries are stems.
    static let milestoneNouns: [[String]] = [
        ["contract", "контракт", "догов"], ["proposal", "quote", "пропоз"], ["demo", "presentation", "демо", "презент"],
        ["decision", "рішен"], ["negotiate", "negotiation", "negotiating", "переговор"],
        ["sign", "signed", "signing", "signature", "підпис"], ["closing", "close", "commit", "verbal", "закрит"],
    ]
    /// A completion word in a stage label and the words that establish it: a signed or received contract was sent,
    /// a reviewed one may not have been. A label word not listed here needs that same word in the signal.
    static let impliedBy: [String: [String]] = {
        let sent = ["sent", "shared", "delivered", "submitted", "received", "signed", "countersigned",
                    "надісл", "відправил", "передал", "отримал", "підписал", "підписан"]
        let signed = ["signed", "countersigned", "підписал", "підписан"]
        var m: [String: [String]] = [:]
        for w in ["sent", "shared", "delivered", "submitted", "надісл", "відправил", "передал"] { m[w] = sent }
        for w in signed { m[w] = signed }
        return m
    }()
    /// Words that start a new clause; with , ; : . they split a signal into separate actions.
    static let clauseWords: Set<String> = ["and", "but", "then", "while", "also", "і", "й", "та", "а", "але", "потім"]
    /// "We sent the proposal and reviewed the contract" -> ["we sent the proposal", "reviewed the contract"].
    static func clauses(_ text: String) -> [String] {
        var out: [[String]] = [[]]
        for part in text.lowercased().components(separatedBy: CharacterSet(charactersIn: ",;:.!\n")) {
            for w in part.split(whereSeparator: \.isWhitespace).map(String.init) {
                if clauseWords.contains(w.trimmingCharacters(in: .punctuationCharacters)) { out.append([]) } else { out[out.count - 1].append(w) }
            }
            out.append([])
        }
        return out.filter { !$0.isEmpty }.map { $0.joined(separator: " ") }
    }
    static func tokenIs(_ t: String, _ words: [String]) -> Bool { words.contains { $0.allSatisfy(\.isASCII) ? t == $0 : t.hasPrefix($0) } }
    /// A noun after one of these (and only determiners between) belongs to a phrase, not to the action:
    /// "the proposal for the contract", "пропозицію щодо контракту".
    static let prepositions: Set<String> = ["for", "about", "of", "on", "with", "to", "from", "in", "regarding", "under", "after", "before",
                                            "щодо", "для", "до", "про", "з", "із", "зі", "на", "від", "у", "в", "під", "після", "за"]
    static let determiners: Set<String> = ["the", "a", "an", "this", "that", "our", "their", "its", "his", "her", "your", "my", "new", "final",
                                           "цей", "цього", "цю", "наш", "нашу", "нашого", "їхній", "їхню", "їхнього"]
    static func inPhrase(_ t: [String], _ j: Int) -> Bool {
        var k = j - 1
        while k >= 0, determiners.contains(t[k]) || tokenIs(t[k], doneWords) { k -= 1 }
        return k >= 0 && prepositions.contains(t[k])
    }
    /// The milestone noun the completion word at `i` is about: the first one after it ("we sent the proposal for the
    /// contract" -> proposal), else the first of the clause ("the proposal for the contract was sent", "пропозицію щодо
    /// контракту надіслали"). A noun in a phrase ("for the contract") and the completion word itself are never the object.
    static func object(_ t: [String], at i: Int) -> String? {
        let nouns = t.indices.filter { j in
            j != i && !tokenIs(t[j], doneWords) && milestoneNouns.contains { tokenIs(t[j], $0) } && !inPhrase(t, j)
        }
        return (nouns.first { $0 > i } ?? nouns.first).map { t[$0] }
    }
    /// The signal establishes this stage's own milestone: in one clause a completion word for the stage ("sent",
    /// "signed", or something that implies it; any completion word when the label has none) has the stage's thing as
    /// its object. "We sent the proposal and reviewed the contract" and "We sent the proposal for the contract"
    /// establish Proposal sent, not Contract sent.
    public static func establishes(_ text: String, label: String) -> Bool {
        let need = doneWords.filter { has(label, [$0]) }
        let done = need.isEmpty ? doneWords : need.flatMap { impliedBy[$0] ?? [$0] }
        let groups = milestoneNouns.filter { has(label, $0) }
        return clauses(text).contains { c in
            let t = c.split { !$0.isLetter && !$0.isNumber && $0 != "-" }.map(String.init)
            return t.indices.contains { i in
                tokenIs(t[i], done) && object(t, at: i).map { o in groups.contains { tokenIs(o, $0) } } == true
            }
        }
    }

    /// A stage only when a signal of the call reports that the stage's own milestone took place (`happened` and
    /// `establishes`), only a later open stage of the deal's own pipeline, never Closed won or Closed lost. Plan steps
    /// and the next step are things still to do, so they never move the stage.
    public static func stageSuggestion(sales: [String: Any], deal: CRMDeal, stages: [CRMStage]) -> CRMDealSuggestion? {
        let mine = pipelineStages(stages, pipeline: deal.pipeline)
        guard let cur = mine.first(where: { $0.id == deal.stage }) else { return nil }
        let texts = ((sales["signals"] as? [[String: Any]]) ?? []).map { s($0["what"]) }.filter(happened)
        for words in stageWords {
            for text in texts where !text.isEmpty {
                guard has(text, words) else { continue }
                let target = mine.filter { !$0.closed && $0.order > cur.order && has($0.label, words) && establishes(text, label: $0.label) }
                    .max { $0.order < $1.order }
                if let t = target { return CRMDealSuggestion(id: "dealstage", kind: "stage", current: cur.label, value: t.id, shown: t.label, evidence: text) }
            }
        }
        return nil
    }

    /// The properties to write: the ticked suggestions, plus a stage the PM picked by hand. A stage id that is not in the
    /// deal's pipeline is dropped. The evidence is not part of the result.
    public static func fields(_ suggestions: [CRMDealSuggestion], pickedStage: String, deal: CRMDeal, stages: [CRMStage]) -> [String: String] {
        let valid = Set(pipelineStages(stages, pipeline: deal.pipeline).map(\.id))
        var f: [String: String] = [:]
        for x in suggestions where x.on { f[x.id] = x.value }
        if let d = f["closedate"], d.count == 10 { f["closedate"] = d + "T00:00:00Z" }      // HubSpot's date-time property: UTC midnight
        if !pickedStage.isEmpty { f["dealstage"] = pickedStage }
        if let st = f["dealstage"], !valid.contains(st) { f["dealstage"] = nil }
        return f
    }
}

/// What Update HubSpot shows for the call's deal, built for one deal id of the link. Apply writes deal fields only from
/// a finished preview of the deal the call is linked to now, so a pick of another deal never gets the old suggestions.
public struct CRMDealPreview: Equatable {
    public var dealId = ""                    // the link's deal id this preview is for ("" or "none" = no deal)
    public var loaded = false                 // the reads are done
    public var deal: CRMDeal? = nil
    public var open: [CRMDeal] = []           // the company's open deals, for the picker
    public var stages: [CRMStage] = []        // every pipeline of the portal
    public var suggestions: [CRMDealSuggestion] = []
    public var picked = ""                    // a stage chosen by hand ("" = keep the stage)
    public var error = ""                     // a read failed (deals, the deal or the stages); Retry loads again
    public var portalMismatch = false         // the link is from another portal: nothing was read, Relink first
    public init() {}

    public var myStages: [CRMStage] { CRMDealSuggester.pipelineStages(stages, pipeline: deal?.pipeline ?? "") }
    /// The properties Apply writes: ticked suggestions and a stage picked by hand, only stages of this deal's pipeline.
    public var fields: [String: String] {
        deal.map { CRMDealSuggester.fields(suggestions, pickedStage: picked, deal: $0, stages: stages) } ?? [:]
    }
    /// True when this preview belongs to the deal the call is linked to now and its reads are finished.
    public func ready(for linkDeal: String) -> Bool { loaded && !portalMismatch && dealId == linkDeal }
    /// The deal fields to write for the link's deal; nothing when the preview is for another deal or still loading.
    public func fields(for linkDeal: String) -> [String: String] {
        guard ready(for: linkDeal), let d = deal, d.id == linkDeal else { return [:] }
        return fields
    }
    /// A preview for a newly picked deal: the picker list and the stages stay, the old deal and its suggestions go.
    public func switching(to dealId: String) -> CRMDealPreview {
        var p = CRMDealPreview(); p.dealId = dealId; p.open = open; p.stages = stages; return p
    }

    /// The portal is checked first; then the company's open deals, the deal and the portal's stages are read and the
    /// suggestions built from the call page (sales = <stem>.sales.json as edited). A failed read is kept in `error`.
    public static func load(connector c: CRMConnector, portalId: String, companyId: String, dealId: String,
                            sales: [String: Any], mapping: [String: String], today: Date = Date()) async -> CRMDealPreview {
        var p = CRMDealPreview(); p.dealId = dealId
        do { try await CRMPortal.verify(link: portalId, connector: c) } catch {
            p.portalMismatch = (error as? CRMError)?.code == CRMPortal.mismatchCode
            p.error = (error as? CRMError)?.message ?? error.localizedDescription
            p.loaded = true
            return p
        }
        func note(_ e: Error) { if p.error.isEmpty { p.error = (e as? CRMError)?.message ?? e.localizedDescription } }
        if !companyId.isEmpty {
            do { p.open = try await c.deals(companyId: companyId).filter { $0.open } } catch { note(error) }
        }
        if let st = c as? CRMDealStages {
            do { p.stages = try await st.dealStages() } catch { note(error) }
        }
        guard !dealId.isEmpty, dealId != "none" else { p.loaded = true; return p }
        do { p.deal = try await c.deal(id: dealId) } catch { note(error) }
        if let d = p.deal {
            p.suggestions = CRMDealSuggester.build(sales: sales, deal: d, stages: p.stages, mapping: mapping, today: today)
            // the Stage menu is the one stage control: a suggested stage is preselected there, Apply is the confirmation
            if let i = p.suggestions.firstIndex(where: { $0.kind == "stage" }) { p.picked = p.suggestions[i].value; p.suggestions[i].on = false }
        }
        p.loaded = true
        return p
    }
}

/// Only the newest of several overlapping loads may update the screen; an older answer that comes late is dropped.
public final class CRMLatestOnly: @unchecked Sendable {
    private var n = 0
    private let lock = NSLock()
    public init() {}
    public func begin() -> Int { lock.withLock { n += 1; return n } }
    public func isLatest(_ token: Int) -> Bool { lock.withLock { token == n } }
}

/// H4: one association of a Call, added or removed, for the deal Pacenote linked the Call to.
public protocol CRMCallAssociations {
    func addCallAssociation(callId: String, type: String, id: String) async throws
    func removeCallAssociation(callId: String, type: String, id: String) async throws
    /// The ids the Call is associated with now (a Call that is gone has none).
    func callAssociations(callId: String, type: String) async throws -> [String]
}

extension HubSpotConnector: CRMCallAssociations {
    public func callAssociations(callId: String, type: String) async throws -> [String] {
        do {
            let r = try await call("GET", "/crm/v4/objects/calls/\(callId)/associations/\(type)?limit=500")
            return ((r["results"] as? [[String: Any]]) ?? []).compactMap { $0["toObjectId"].map { "\($0)" } }
        } catch let e as CRMError where e.code == 404 { return [] }
    }
    public func addCallAssociation(callId: String, type: String, id: String) async throws {
        _ = try await call("PUT", "/crm/v4/objects/calls/\(callId)/associations/default/\(type)/\(id)")
    }
    /// An association that is already gone (404) counts as removed.
    public func removeCallAssociation(callId: String, type: String, id: String) async throws {
        do { _ = try await call("DELETE", "/crm/v4/objects/calls/\(callId)/associations/\(type)/\(id)") }
        catch let e as CRMError where e.code == 404 {}
    }
}

/// H4: the Call follows the call's deal. Pacenote remembers which deal it associated the Call with (`managed`); when the
/// PM moves the call to another deal or to No deal, that one association is removed on the next write. Associations
/// Pacenote did not make (a deal a person added in HubSpot, the company, the contacts) are never removed.
public enum CRMCallDeal {
    /// The deal the Call should be associated with: "" for none.
    public static func target(_ linkDeal: String) -> String { linkDeal == "none" ? "" : linkDeal }

    /// What one write did to the Call's deal associations, so Undo can reverse exactly that.
    public struct Move: Equatable {
        public var call: CRMCallResult
        public var removed = ""        // the deal whose association Pacenote removed
        public var added = ""          // the deal Pacenote associated that was not associated before
        public var managedBefore = ""  // the managed deal before the write
        public var moved: Bool { !removed.isEmpty || !added.isEmpty }
        /// The Undo row of the move (type "callDeal"); nil when the associations did not change.
        public var undoItem: CRMUndoItem? {
            moved ? CRMUndoItem("callDeal", call.id, ["removed": removed, "added": added, "managed": managedBefore]) : nil
        }
    }
    /// The Call was written (and maybe a deal added), then removing the old deal failed: `move` is what was done.
    public struct Partial: Error, LocalizedError {
        public var move: Move
        public var cause: Error
        public var errorDescription: String? { (cause as? CRMError)?.message ?? cause.localizedDescription }
    }

    /// The Call write of `CRMCallSync.log` plus the deal move, in the same turn of the per-call queue. `load` gives the
    /// stored Call id, portal and managed deal when the turn comes; `save` stores the Call id and the managed deal.
    /// Before an existing Call moves, its deal associations are read, so a deal that was already there (a person
    /// added it in HubSpot) is not counted as added and Undo leaves it.
    public static func write(_ entry: CRMCallLog, connector c: CRMConnector, associations: [CRMRef], dealId: String,
                             queue: CRMCallQueue = .shared,
                             load: @escaping () -> (callId: String, portalId: String, managed: String),
                             save: @escaping (_ callId: String, _ managed: String) -> Void) async throws -> Move {
        try await queue.run(entry.key) {
            let stored = load()
            try await CRMPortal.verify(link: stored.portalId, connector: c)
            let now = target(dealId)
            let a = c as? CRMCallAssociations
            var before: [String]? = nil      // nil: not read, so nothing is counted as added
            if !stored.callId.isEmpty, !now.isEmpty, now != stored.managed, let a {
                before = try await a.callAssociations(callId: stored.callId, type: "deals")
            }
            let r = try await c.upsertCall(entry, knownId: stored.callId, associations: associations)
            save(r.id, stored.managed)
            var m = Move(call: r, managedBefore: stored.managed)
            guard !r.created else { save(r.id, now); return m }     // a new Call is deleted whole by its own Undo row
            if r.id == stored.callId, let before, !before.contains(now) { m.added = now }
            if !stored.managed.isEmpty, stored.managed != now, let a {
                do { try await a.removeCallAssociation(callId: r.id, type: "deals", id: stored.managed) } catch {
                    // the Call and the new deal are written: the error carries them, so Undo can still take them back;
                    // the old deal is still on the Call and stays the managed one, so the next write removes it
                    save(r.id, stored.managed)
                    throw Partial(move: m, cause: error)
                }
                m.removed = stored.managed
            }
            // managed is the association Pacenote made, not the deal the PM picked: a deal that was already on the
            // Call (a person added it in HubSpot) is never stored, so a later No deal or switch does not remove it
            save(r.id, now == stored.managed ? stored.managed : m.added)
            return m
        }
    }

    /// Undo of one Apply with deal moves: the portal is checked once before anything is sent; a "callDeal" row takes
    /// the added deal off the Call and associates the removed one again (`restored` gets the row, to store its managed
    /// deal back); every other row goes to `CRMCallSync.undo`. Returns how many rows failed.
    public static func undo(_ items: [CRMUndoItem], portal: String, connector c: CRMConnector,
                            deleted: (CRMUndoItem) -> Void = { _ in }, restored: (CRMUndoItem) -> Void = { _ in }) async throws -> Int {
        try await CRMPortal.verify(link: portal, connector: c)
        var failed = 0
        for it in items.reversed() {
            guard it.type == "callDeal" else {
                do { failed += try await CRMCallSync.undo([it], portal: portal, connector: c, deleted: deleted) } catch { failed += 1 }
                continue
            }
            do {
                guard let a = c as? CRMCallAssociations else { failed += 1; continue }
                if let d = it.old["added"], !d.isEmpty { try await a.removeCallAssociation(callId: it.id, type: "deals", id: d) }
                if let d = it.old["removed"], !d.isEmpty { try await a.addCallAssociation(callId: it.id, type: "deals", id: d) }
                restored(it)
            } catch { failed += 1 }
        }
        return failed
    }
}
