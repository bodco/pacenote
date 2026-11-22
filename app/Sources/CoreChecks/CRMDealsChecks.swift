import Foundation
import LiveTipsCore

/// H4 (PB-60): deal suggestions with evidence, the portal's own pipeline stages, apply and Undo. A small in-memory
/// HubSpot with one deal and two pipelines. Never the network.
final class FakeHubSpotDeals: CRMTransport {
    var deal: [String: String] = ["dealname": "Orbit renewal", "dealstage": "s2", "pipeline": "p1", "hs_next_step": "Old step", "closedate": "2026-12-01", "hs_is_closed": "false"]
    var log: [String] = []
    var patches: [[String: Any]] = []
    func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> (Int, [String: Any], [String: String]) {
        let bare = String(path.split(separator: "?")[0])
        log.append(method + " " + bare)
        if method == "GET" && bare == "/crm/v3/pipelines/deals" {
            func st(_ id: String, _ label: String, _ o: Int, closed: Bool = false) -> [String: Any] {
                ["id": id, "label": label, "displayOrder": o, "metadata": ["isClosed": closed ? "true" : "false"]]
            }
            return (200, ["results": [
                ["id": "p1", "label": "Sales", "stages": [st("s1", "Appointment", 0), st("s2", "Qualified", 1), st("s3", "Proposal sent", 2), st("s4", "Contract sent", 3), st("s5", "Closed won", 4, closed: true)]],
                ["id": "p2", "label": "Renewals", "stages": [st("r1", "Renewal contract", 0), st("r2", "Renewed", 1, closed: true)]]]], [:])
        }
        if bare == "/crm/v3/objects/deals/D1" {
            if method == "GET" { return (200, ["id": "D1", "properties": deal], [:]) }
            if method == "PATCH" {
                patches.append(body ?? [:])
                for (k, v) in ((body?["properties"] as? [String: Any]) ?? [:]) { deal[k] = "\(v)" }
                return (200, ["id": "D1"], [:])
            }
        }
        return (404, ["message": "not found"], [:])
    }
}

func crmDeals() async {
    let t = FakeHubSpotDeals()
    let c = HubSpotConnector(transport: t, sleep: { _ in })
    let stages = (try? await c.dealStages()) ?? []
    let deal = (try? await c.deal(id: "D1")) ?? CRMDeal(id: "?", name: "")
    expect(stages.count == 7 && deal.pipeline == "p1" && deal.stage == "s2", "deals: the deal knows its pipeline, the portal lists every stage")
    expect(CRMDealSuggester.pipelineStages(stages, pipeline: "p1").map(\.id) == ["s1", "s2", "s3", "s4", "s5"], "deals: only the deal's own pipeline, in the portal's order")
    expect(CRMDealSuggester.pipelineStages(stages, pipeline: "").isEmpty, "deals: an unknown pipeline offers no stages")

    let today = Date(timeIntervalSince1970: 1_790_000_000)      // 2026-09-21
    let quote = "We have to decide by the end of Q4, budget is approved"
    let sales: [String: Any] = [
        "next_step": ["what": "Send the contract to Emily Clark", "due": "2026-11-30", "owner": "me"],
        "mutual_plan": [["step": "Signature by the board", "owner": "them", "due": "2026-12-15"], ["step": "Security review", "owner": "us", "due": "next Tuesday"]],
        "signals": [["what": "Emily Clark asked for the contract draft", "t": "00:12:00"]],
        "criteria": [["criterion": "budget", "state": "known", "what": "Budget approved for Q4", "quote": quote, "t": "00:10:00"],
                     ["criterion": "authority", "state": "known", "what": "Emily decides", "quote": "", "t": ""],
                     ["criterion": "need", "state": "unknown", "what": "", "quote": "", "t": ""],
                     ["criterion": "timeline", "state": "partial", "what": "Wants it live in Q1", "quote": "Live in Q1 would be great", "t": "00:20:00"]]]
    let mapping = ["budget": "bant_budget", "authority": "bant_authority", "need": "bant_need", "timeline": ""]
    let s = CRMDealSuggester.build(sales: sales, deal: deal, stages: stages, mapping: mapping, today: today)
    let byId = Dictionary(uniqueKeysWithValues: s.map { ($0.id, $0) })
    expect(byId["bant_budget"]?.value == "Budget approved for Q4" && byId["bant_budget"]?.evidence == quote, "deals: a criterion with a quote is proposed, the quote is the evidence")
    expect(byId["bant_authority"] == nil && byId["bant_need"] == nil && byId["timeline"] == nil, "deals: no quote, no state or no property chosen means no suggestion")
    expect(byId["hs_next_step"]?.value == "Send the contract to Emily Clark" && byId["hs_next_step"]?.evidence.contains("2026-11-30") == true, "deals: the agreed next step is proposed with itself as evidence")
    expect(byId["closedate"]?.value == "2026-12-15" && byId["closedate"]?.evidence.contains("Signature") == true, "deals: close date from the closing step with a real date")
    expect(byId["dealstage"]?.value == "s4" && byId["dealstage"]?.shown == "Contract sent" && byId["dealstage"]?.evidence.contains("contract draft") == true, "deals: a stage of the deal's own pipeline, with the signal as evidence")

    // no evidence, no change
    let none = CRMDealSuggester.build(sales: ["next_step": ["what": "", "due": ""], "criteria": [], "mutual_plan": [["step": "Security review", "due": "next Tuesday"], ["step": "Closing call", "due": "not a date"]]],
                                      deal: deal, stages: stages, mapping: mapping, today: today)
    expect(none.isEmpty, "deals: nothing on the call page means nothing is suggested; a free-text date is not a close date")
    expect(CRMDealSuggester.build(sales: ["mutual_plan": [["step": "Closing call", "due": "2026-02-31"]]], deal: deal, stages: stages, mapping: [:], today: today).isEmpty, "deals: 2026-02-31 is not a date")
    expect(CRMDealSuggester.build(sales: ["mutual_plan": [["step": "Closing call", "due": "2026-09-01"]]], deal: deal, stages: stages, mapping: [:], today: today).isEmpty, "deals: a close date in the past is not proposed")
    expect(byId["hs_next_step"] != nil && CRMDealSuggester.build(sales: ["next_step": ["what": "Old step"]], deal: deal, stages: stages, mapping: [:], today: today).isEmpty, "deals: a value the deal already has is not proposed")

    // stage rules: no signal, a design review ("sign" is not "design"), a closed stage, an earlier stage and a deal without a pipeline
    func stage(_ sales: [String: Any], _ d: CRMDeal) -> CRMDealSuggestion? { CRMDealSuggester.stageSuggestion(sales: sales, deal: d, stages: stages) }
    expect(stage(["signals": [["what": "Asked about the design review"]]], deal) == nil, "deals: design is not sign")
    expect(stage(["signals": [["what": "The contract is signed"]]], deal)?.value == "s4", "deals: signed moves to Contract sent, never to Closed won")
    expect(stage(["signals": [["what": "Asked for a demo"]]], deal)?.value == "s3", "deals: a demo moves Qualified forward to Proposal sent")
    var late = deal; late.stage = "s4"
    expect(stage(["signals": [["what": "Asked for a demo"]]], late) == nil, "deals: a stage before the current one is not proposed")
    var other = deal; other.pipeline = "p2"; other.stage = "r1"
    expect(stage(["signals": [["what": "Asked for the contract"]]], other) == nil, "deals: another pipeline's stage names are not used (the stage must be later in the same pipeline)")
    var lost = deal; lost.pipeline = ""
    expect(stage(["signals": [["what": "Asked for the contract"]]], lost) == nil, "deals: a deal without a pipeline gets no stage suggestion")

    // fields: ticked rows only, a hand-picked stage must be in the deal's pipeline
    var rows = s
    for i in rows.indices where rows[i].id == "bant_budget" || rows[i].id == "closedate" { rows[i].on = false }
    let f = CRMDealSuggester.fields(rows, pickedStage: "", deal: deal, stages: stages)
    expect(f["bant_budget"] == nil && f["closedate"] == nil && f["hs_next_step"] != nil && f["dealstage"] == "s4", "deals: only ticked rows are written")
    var stageOff = s
    for i in stageOff.indices where stageOff[i].kind == "stage" { stageOff[i].on = false }      // as the app loads it: the Stage menu holds the pick
    expect(CRMDealSuggester.fields(stageOff, pickedStage: "", deal: deal, stages: stages)["dealstage"] == nil, "deals: Keep the stage writes no stage")
    expect(CRMDealSuggester.fields(s, pickedStage: "", deal: deal, stages: stages)["closedate"] == "2026-12-15T00:00:00Z", "deals: the close date is written as UTC midnight")
    expect(CRMDealSuggester.fields([], pickedStage: "r2", deal: deal, stages: stages)["dealstage"] == nil, "deals: a stage from another pipeline is never written")
    expect(CRMDealSuggester.fields([], pickedStage: "s3", deal: deal, stages: stages)["dealstage"] == "s3", "deals: a stage the PM picked from the deal's pipeline is written")
    expect(CRMDealSuggester.fields(rows, pickedStage: "s3", deal: deal, stages: stages)["dealstage"] == "s3", "deals: the PM's pick wins over the suggestion")

    // Apply through the connector: one PATCH, no quote or transcript text in the body; Undo puts the old values back
    let old = (try? await c.updateDeal(id: "D1", fields: f)) ?? [:]
    let sent = (t.patches.first?["properties"] as? [String: Any]) ?? [:]
    expect(t.patches.count == 1 && sent["hs_next_step"] as? String == "Send the contract to Emily Clark" && sent["dealstage"] as? String == "s4", "deals: Apply is one PATCH with the ticked fields")
    let body = String(data: (try? JSONSerialization.data(withJSONObject: t.patches)) ?? Data(), encoding: .utf8) ?? ""
    expect(!body.contains(quote) && !body.contains("Live in Q1") && !body.contains("Signature by the board"), "deals: evidence quotes are never sent to HubSpot")
    expect(old["hs_next_step"] == "Old step" && old["dealstage"] == "s2" && t.log.filter { $0 == "GET /crm/v3/objects/deals/D1" }.count >= 2 && t.deal["dealstage"] == "s4", "deals: the old values are read before the PATCH")
    _ = try? await c.updateDeal(id: "D1", fields: old)
    expect(t.deal["dealstage"] == "s2" && t.deal["hs_next_step"] == "Old step", "deals: Undo restores the old values")
}
