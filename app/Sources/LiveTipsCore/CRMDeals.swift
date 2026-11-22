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
        if let m = plan.first(where: { p in
            let t = s(p["step"]).lowercased()
            guard let d = isoDate(s(p["due"])) else { return false }
            return has(t, closeWords) && d >= todayStr && d != deal.closeDate
        }), let d = isoDate(s(m["due"])) {
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

    /// A stage only when the call says so (a signal, a plan step or the next step names it), only a later open stage of the
    /// deal's own pipeline, never Closed won or Closed lost.
    public static func stageSuggestion(sales: [String: Any], deal: CRMDeal, stages: [CRMStage]) -> CRMDealSuggestion? {
        let mine = pipelineStages(stages, pipeline: deal.pipeline)
        guard let cur = mine.first(where: { $0.id == deal.stage }) else { return nil }
        var texts: [String] = []
        texts += ((sales["signals"] as? [[String: Any]]) ?? []).map { s($0["what"]) }
        texts += ((sales["mutual_plan"] as? [[String: Any]]) ?? []).map { s($0["step"]) }
        let next = sales["next_step"] as? [String: Any] ?? [:]
        texts.append(s(next["what"]))
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
