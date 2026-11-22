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
    /// The text reports a milestone that took place: a completion word, no negation, no plan or request, not a question.
    /// "The contract was never sent" and "We will send the contract" are not evidence for Contract sent.
    public static func happened(_ text: String) -> Bool {
        !text.contains("?") && has(text, doneWords) && !hasWord(text, notDoneWords)
    }

    /// A stage only when a signal of the call reports that the stage's milestone took place (`happened`), only a later
    /// open stage of the deal's own pipeline, never Closed won or Closed lost. Plan steps and the next step are things
    /// still to do, so they never move the stage.
    public static func stageSuggestion(sales: [String: Any], deal: CRMDeal, stages: [CRMStage]) -> CRMDealSuggestion? {
        let mine = pipelineStages(stages, pipeline: deal.pipeline)
        guard let cur = mine.first(where: { $0.id == deal.stage }) else { return nil }
        let texts = ((sales["signals"] as? [[String: Any]]) ?? []).map { s($0["what"]) }.filter(happened)
        for words in stageWords {
            for text in texts where !text.isEmpty {
                guard has(text, words) else { continue }
                let target = mine.filter { !$0.closed && $0.order > cur.order && has($0.label, words) }
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
}

extension HubSpotConnector: CRMCallAssociations {
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

    /// The Call write of `CRMCallSync.log` plus the deal move, in the same turn of the per-call queue. `load` gives the
    /// stored Call id, portal and managed deal when the turn comes; `save` stores the Call id and the managed deal.
    /// Returns the Call result and the deal whose association was removed ("" when none), for Undo.
    public static func write(_ entry: CRMCallLog, connector c: CRMConnector, associations: [CRMRef], dealId: String,
                             queue: CRMCallQueue = .shared,
                             load: @escaping () -> (callId: String, portalId: String, managed: String),
                             save: @escaping (_ callId: String, _ managed: String) -> Void) async throws -> (call: CRMCallResult, removed: String) {
        try await queue.run(entry.key) {
            let stored = load()
            try await CRMPortal.verify(link: stored.portalId, connector: c)
            let r = try await c.upsertCall(entry, knownId: stored.callId, associations: associations)
            save(r.id, stored.managed)
            let now = target(dealId)
            var removed = ""
            if !stored.managed.isEmpty, stored.managed != now, !r.created, let a = c as? CRMCallAssociations {
                try await a.removeCallAssociation(callId: r.id, type: "deals", id: stored.managed)
                removed = stored.managed
            }
            save(r.id, now)
            return (r, removed)
        }
    }
}
