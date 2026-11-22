import Foundation
import LiveTipsCore

/// H4 (PB-60): deal suggestions with evidence, the portal's own pipeline stages, apply and Undo. A small in-memory
/// HubSpot with one deal and two pipelines. Never the network.
final class FakeHubSpotDeals: CRMTransport {
    var deal: [String: String] = ["dealname": "Orbit renewal", "dealstage": "s2", "pipeline": "p1", "hs_next_step": "Old step", "closedate": "2026-12-01", "hs_is_closed": "false"]
    var deal2: [String: String] = ["dealname": "Orbit expansion", "dealstage": "s1", "pipeline": "p1", "hs_next_step": "", "closedate": "", "hs_is_closed": "false"]
    var log: [String] = []
    var patches: [[String: Any]] = []
    var portal = 500
    var d1Delay: UInt64 = 0                    // ns the read of D1 takes, so a later pick can answer first
    var failDeals = false, failStages = false  // the company's deals or the pipelines answer 500
    var calls: [String: [String]] = [:]        // Call id -> "type/id" associations
    var nextCall = 700
    let lock = NSLock()
    func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> (Int, [String: Any], [String: String]) {
        if method == "GET" && path.hasPrefix("/crm/v3/objects/deals/D1") && d1Delay > 0 { try? await Task.sleep(nanoseconds: d1Delay) }
        return lock.withLock { handle(method, path, body) }
    }
    func handle(_ method: String, _ path: String, _ body: [String: Any]?) -> (Int, [String: Any], [String: String]) {
        let bare = String(path.split(separator: "?")[0])
        log.append(method + " " + bare)
        if bare == "/account-info/v3/details" { return (200, ["portalId": portal], [:]) }
        if bare == "/crm/v4/objects/companies/C1/associations/deals" {
            return failDeals ? (500, ["message": "HubSpot had an internal error"], [:]) : (200, ["results": [["toObjectId": "D1"], ["toObjectId": "D2"]]], [:])
        }
        if bare == "/crm/v3/objects/deals/batch/read" {
            return (200, ["results": [["id": "D1", "properties": deal], ["id": "D2", "properties": deal2]]], [:])
        }
        if bare == "/crm/v3/objects/deals/D2" && method == "GET" { return (200, ["id": "D2", "properties": deal2], [:]) }
        // Calls: search (none), create with associations, PUT and DELETE of one association, read for an update
        if bare == "/crm/v3/objects/calls/search" {
            let want = ((((body?["filterGroups"] as? [[String: Any]])?.first?["filters"]) as? [[String: Any]])?.first?["value"] as? String) ?? ""
            return (200, ["results": want == "pacenote:k-found" ? [["id": "701"]] : []], [:])
        }
        if method == "POST" && bare == "/crm/v3/objects/calls" {
            nextCall += 1; let id = "\(nextCall)"
            calls[id] = ((body?["associations"] as? [[String: Any]]) ?? []).map { a in
                let t = ((a["types"] as! [[String: Any]])[0])["associationTypeId"] as! Int
                return [206: "deals", 182: "companies", 194: "contacts"][t]! + "/" + "\((a["to"] as! [String: Any])["id"]!)" }
            return (200, ["id": id], [:])
        }
        if bare.hasPrefix("/crm/v3/objects/calls/") {
            let id = String(bare.split(separator: "/")[4])
            guard calls[id] != nil else { return (404, ["message": "not found"], [:]) }
            return (200, ["id": id, "properties": [:]], [:])
        }
        if bare.hasPrefix("/crm/v4/objects/calls/") {
            let parts = bare.split(separator: "/").map(String.init)      // crm v4 objects calls <id> associations [default] <type> <toId>
            let id = parts[4], to = parts.suffix(2).joined(separator: "/")
            guard calls[id] != nil else { return (404, ["message": "not found"], [:]) }
            if method == "GET" {      // the Call's associations of one type
                let type = parts.last!
                return (200, ["results": calls[id]!.filter { $0.hasPrefix(type + "/") }.map { ["toObjectId": String($0.dropFirst(type.count + 1))] }], [:])
            }
            if method == "PUT" { if !calls[id]!.contains(to) { calls[id]!.append(to) }; return (200, [:], [:]) }
            if method == "DELETE" {
                guard calls[id]!.contains(to) else { return (404, ["message": "no association"], [:]) }
                calls[id]!.removeAll { $0 == to }; return (204, [:], [:])
            }
        }
        if method == "GET" && bare == "/crm/v3/pipelines/deals" {
            if failStages { return (500, ["message": "HubSpot had an internal error"], [:]) }
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
        "signals": [["what": "We sent the contract draft to Emily Clark", "t": "00:12:00"]],
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
    expect(stage(["signals": [["what": "We sent the proposal to the team"]]], deal)?.value == "s3", "deals: a proposal that was sent moves Qualified forward to Proposal sent")
    var late = deal; late.stage = "s4"
    expect(stage(["signals": [["what": "We sent the proposal to the team"]]], late) == nil, "deals: a stage before the current one is not proposed")
    // review round 2: the signal must establish the stage's own milestone, not a nearby one
    expect(stage(["signals": [["what": "We presented the demo to the team"]]], deal) == nil, "deals: a demo given is not a proposal sent")
    for text in ["We reviewed the contract", "We agreed to send the contract", "We agreed to send the contract on Monday",
                 "Ми погодились надіслати контракт", "We discussed the proposal"] {
        expect(stage(["signals": [["what": text]]], deal) == nil, "deals: no stage from \"\(text)\"")
    }
    expect(stage(["signals": [["what": "They received the contract"]]], deal)?.value == "s4" && stage(["signals": [["what": "We shared the proposal"]]], deal)?.value == "s3",
           "deals: received and shared establish sent")
    expect(CRMDealSuggester.establishes("The decision maker approved it", label: "Decision maker bought-in")
           && !CRMDealSuggester.establishes("We reviewed the contract", label: "Decision maker bought-in"),
           "deals: a label without a completion word needs its own thing in the signal")
    var other = deal; other.pipeline = "p2"; other.stage = "r1"
    expect(stage(["signals": [["what": "The contract was sent"]]], other) == nil, "deals: another pipeline's stage names are not used (the stage must be later in the same pipeline)")
    var lost = deal; lost.pipeline = ""
    expect(stage(["signals": [["what": "The contract was sent"]]], lost) == nil, "deals: a deal without a pipeline gets no stage suggestion")
    // the milestone must have happened: negated, future, requested or asked about is no evidence (review round 1)
    for text in ["They said the contract was never sent", "The contract was not sent", "The contract wasn't sent yet", "We will send the contract on Monday",
                 "We'll share the proposal next week", "Emily Clark asked for the contract draft", "Asked for a demo", "Was the contract sent?",
                 "They plan to sign the contract", "Контракт ще не надіслали", "Контракт буде надіслано завтра"] {
        expect(stage(["signals": [["what": text]]], deal) == nil, "deals: no stage from \"\(text)\"")
    }
    expect(stage(["signals": [["what": "The contract was sent on Monday"]]], deal)?.value == "s4" && stage(["signals": [["what": "Ми надіслали контракт"]]], deal)?.value == "s4",
           "deals: a sent contract is evidence for Contract sent (EN and UK)")
    expect(stage(["next_step": ["what": "Send the contract to Emily Clark"], "mutual_plan": [["step": "Contract signed", "due": "2026-12-15"]]], deal) == nil,
           "deals: plan steps and the next step are still to do and never move the stage")
    // close date: the closing step is chosen first, then compared with the deal; two closing steps never alternate
    let two: [String: Any] = ["mutual_plan": [["step": "Signature by the board", "due": "2026-12-15"], ["step": "Closing call", "due": "2026-12-20"]]]
    var atFirst = deal; atFirst.closeDate = "2026-12-15"
    var atSecond = deal; atSecond.closeDate = "2026-12-20"
    let cd = { (d: CRMDeal) in CRMDealSuggester.build(sales: two, deal: d, stages: stages, mapping: [:], today: today).first { $0.kind == "closeDate" }?.value }
    expect(cd(atFirst) == nil && cd(atSecond) == "2026-12-15" && cd(deal) == "2026-12-15", "deals: re-applying the same call does not move the close date again")

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

    await crmDealsRound1(sales: sales, mapping: mapping, today: today)
}

/// H4 review round 1: a late answer for an earlier pick, the portal check before the preview, failed reads, and the
/// Call's deal association when the call moves to another deal or to No deal.
func crmDealsRound1(sales: [String: Any], mapping: [String: String], today: Date) async {
    let t = FakeHubSpotDeals()
    let c = HubSpotConnector(transport: t, sleep: { _ in })
    func preview(_ deal: String, portal: String = "500") async -> CRMDealPreview {
        await CRMDealPreview.load(connector: c, portalId: portal, companyId: "C1", dealId: deal, sales: sales, mapping: mapping, today: today)
    }
    // the PM picks D1, then D2 at once; D1 answers last. Only the newest load reaches the screen, and Apply writes D2 only
    t.d1Delay = 80_000_000
    let latest = CRMLatestOnly()
    final class Screen { var shown = CRMDealPreview(); var linkDeal = "" }
    let screen = Screen()
    func pick(_ deal: String) async {
        let token = latest.begin()
        screen.linkDeal = deal
        screen.shown = screen.shown.switching(to: deal)
        let m = await preview(deal)
        if latest.isLatest(token) && screen.linkDeal == deal { screen.shown = m }
    }
    async let first: Void = pick("D1")
    try? await Task.sleep(nanoseconds: 10_000_000)
    async let second: Void = pick("D2")
    _ = await (first, second)
    expect(screen.shown.dealId == "D2" && screen.shown.deal?.id == "D2" && screen.shown.ready(for: "D2"), "deals: a late answer for the earlier pick does not replace the current deal")
    expect(t.log.filter { $0 == "GET /crm/v3/objects/deals/D1" }.count == 1, "deals: the earlier pick's read did finish (and was dropped)")
    t.d1Delay = 0
    // D1's suggestions are never written to D2
    let d1 = await preview("D1")
    expect(!d1.fields.isEmpty && d1.fields(for: "D2").isEmpty && !d1.ready(for: "D2") && d1.fields(for: "D1") == d1.fields, "deals: Apply writes a preview's fields only to its own deal")
    let moving = d1.switching(to: "D2")
    expect(moving.deal == nil && moving.suggestions.isEmpty && moving.picked.isEmpty && !moving.ready(for: "D2") && moving.open.count == 2 && !moving.stages.isEmpty,
           "deals: a new pick clears the old deal and its suggestions at once, the picker stays")
    let none = await preview("none")
    expect(none.ready(for: "none") && none.deal == nil && none.fields(for: "none").isEmpty && none.open.count == 2, "deals: No deal is ready with nothing to write")
    // the portal is checked before anything about the deal is read
    let before = t.log.count
    t.portal = 777
    let other = await preview("D1")
    expect(other.portalMismatch && other.deal == nil && other.open.isEmpty && !other.ready(for: "D1") && t.log.dropFirst(before).allSatisfy { $0.hasPrefix("GET /account-info") },
           "deals: a link from another portal reads nothing and is not ready")
    let unknown = await preview("D1", portal: "")
    expect(unknown.portalMismatch && unknown.deal == nil, "deals: a link with no portal needs Relink")
    t.portal = 500
    // a failed read is an error the PM can retry, not an empty list
    t.failDeals = true
    let noDeals = await preview("D1")
    expect(!noDeals.error.isEmpty && noDeals.open.isEmpty && noDeals.deal?.id == "D1" && !noDeals.portalMismatch, "deals: a failed read of the company's deals is reported")
    t.failDeals = false; t.failStages = true
    let noStages = await preview("D1")
    expect(!noStages.error.isEmpty && noStages.stages.isEmpty && noStages.open.count == 2, "deals: a failed read of the stages is reported")
    t.failStages = false
    let again = await preview("D1")
    expect(again.error.isEmpty && again.open.count == 2 && !again.stages.isEmpty, "deals: Retry reads again")

    // the Call follows the deal: D1 -> D2 removes the D1 association Pacenote made; D2 -> No deal removes D2;
    // the company, the contacts and a deal a person added in HubSpot stay
    final class Disk { var callId = ""; var portal = "500"; var managed = "" }
    let disk = Disk(), q = CRMCallQueue()
    let entry = CRMCallLog(key: "k-move", title: "Orbit renewal", start: today, durationMs: 60_000, body: "<p>Summary</p>")
    func write(_ deal: String) async -> CRMCallDeal.Move? {
        var refs = [CRMRef("companies", "C1"), CRMRef("contacts", "P1")]
        if CRMCallDeal.target(deal) != "" { refs.insert(CRMRef("deals", deal), at: 0) }
        return try? await CRMCallDeal.write(entry, connector: c, associations: refs, dealId: deal, queue: q,
                                            load: { (disk.callId, disk.portal, disk.managed) }, save: { disk.callId = $0; disk.managed = $1 })
    }
    let w1 = await write("D1")
    let id = w1?.call.id ?? ""
    expect(w1?.call.created == true && Set(t.calls[id] ?? []) == ["deals/D1", "companies/C1", "contacts/P1"] && disk.managed == "D1" && w1?.removed == "",
           "deals: the first Call is associated with the deal and Pacenote remembers it")
    t.calls[id]?.append("deals/D9")       // a person adds another deal in HubSpot
    let w2 = await write("D2")
    expect(w2?.call.id == id && w2?.removed == "D1" && Set(t.calls[id] ?? []) == ["deals/D2", "deals/D9", "companies/C1", "contacts/P1"] && disk.managed == "D2",
           "deals: moving the call to another deal moves the Call's association and keeps the others")
    let w3 = await write("none")
    expect(w3?.removed == "D2" && Set(t.calls[id] ?? []) == ["deals/D9", "companies/C1", "contacts/P1"] && disk.managed == "",
           "deals: No deal removes the deal Pacenote associated, nothing else")
    let w4 = await write("none")
    expect(w4?.removed == "" && Set(t.calls[id] ?? []) == ["deals/D9", "companies/C1", "contacts/P1"], "deals: a re-run with no move removes nothing")
    expect(w1?.undoItem == nil && w4?.undoItem == nil && w2?.added == "D2" && w3?.added == "", "deals: only a move has an Undo row; No deal adds nothing")
    // Undo reverses the move (review round 2): D1 -> D2 takes D2 off and puts D1 back; the others stay
    final class Restored { var managed: [String] = [] }
    let restored = Restored()
    func undo(_ m: CRMCallDeal.Move?, portal: String = "500") async -> Int? {
        try? await CRMCallDeal.undo([CRMUndoItem("calls", id, ["hs_call_title": "Orbit renewal"])] + [m?.undoItem].compactMap { $0 }, portal: portal, connector: c,
                                    restored: { restored.managed.append($0.old["managed"] ?? "-"); disk.managed = $0.old["managed"] ?? "" })
    }
    t.calls[id] = ["deals/D1", "deals/D9", "companies/C1", "contacts/P1"]; disk.managed = "D1"
    let m12 = await write("D2")
    expect(Set(t.calls[id] ?? []) == ["deals/D2", "deals/D9", "companies/C1", "contacts/P1"] && m12?.added == "D2" && m12?.removed == "D1", "deals: the switch is recorded both ways")
    let u12 = await undo(m12)
    expect(u12 == 0 && Set(t.calls[id] ?? []) == ["deals/D1", "deals/D9", "companies/C1", "contacts/P1"] && disk.managed == "D1" && restored.managed == ["D1"],
           "deals: Undo of a switch takes the new deal off, links the old one again and keeps the others")
    // D1 -> No deal: Undo links D1 again
    let m1n = await write("none")
    let u1n = await undo(m1n)
    expect(m1n?.removed == "D1" && u1n == 0 && Set(t.calls[id] ?? []) == ["deals/D1", "deals/D9", "companies/C1", "contacts/P1"] && disk.managed == "D1",
           "deals: Undo of No deal links the old deal again")
    // a deal a person had already added in HubSpot is not counted as added, so Undo leaves it
    let m19 = await write("D9")
    let u19 = await undo(m19)
    expect(m19?.added == "" && m19?.removed == "D1" && u19 == 0 && Set(t.calls[id] ?? []) == ["deals/D1", "deals/D9", "companies/C1", "contacts/P1"],
           "deals: Undo never removes a deal that was on the Call before")
    // Undo after the key moved to another portal sends nothing
    let m12b = await write("D2")
    t.portal = 777
    let sentBefore = t.log.count
    let blockedUndo = await undo(m12b)
    expect(blockedUndo == nil && t.log.dropFirst(sentBefore).allSatisfy { $0.hasPrefix("GET /account-info") } && t.calls[id]?.contains("deals/D2") == true,
           "deals: Undo of a move from another portal changes nothing")
    t.portal = 500
    _ = await undo(m12b)
    disk.managed = "D1"
    // an association that is already gone is not an error
    var gone: Error? = nil
    do { try await c.removeCallAssociation(callId: id, type: "deals", id: "D7") } catch { gone = error }
    expect(gone == nil, "deals: removing an association that is already gone succeeds")
    // the move goes only to the link's portal
    t.portal = 777
    let dels = t.log.filter { $0.hasPrefix("DELETE") }.count
    disk.managed = "D9"
    let blocked = await write("D2")
    expect(blocked == nil && t.log.filter { $0.hasPrefix("DELETE") }.count == dels, "deals: a link from another portal moves nothing")
    t.portal = 500
}
