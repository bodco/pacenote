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
    var failRemove = false                     // removing a Call's association answers 500
    var failRemoveDeal = ""                    // removing only this deal from a Call answers 500
    var failCallPatch = false                  // the update of a Call answers 500
    var failAdd = false, addLands = false      // adding a Call's deal answers 500 (addLands: HubSpot made it anyway)
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
            if method == "PATCH" && failCallPatch { return (500, ["message": "HubSpot had an internal error"], [:]) }
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
            if method == "PUT" {
                if failAdd && to.hasPrefix("deals/") && !addLands { return (500, ["message": "HubSpot had an internal error"], [:]) }
                if !calls[id]!.contains(to) { calls[id]!.append(to) }
                return failAdd && to.hasPrefix("deals/") ? (500, ["message": "HubSpot had an internal error"], [:]) : (200, [:], [:])
            }
            if method == "DELETE" {
                if failRemove || (!failRemoveDeal.isEmpty && to == "deals/" + failRemoveDeal) { return (500, ["message": "HubSpot had an internal error"], [:]) }
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
           && !CRMDealSuggester.establishes("We reviewed the contract", label: "Decision maker bought-in")
           && !CRMDealSuggester.establishes("The decision maker sent the contract", label: "Decision maker bought-in"),
           "deals: a label without a completion word needs its own thing in the signal")
    // review round 3: the completed action must refer to the stage's own thing, in the same clause
    for text in ["We sent the proposal and reviewed the contract", "Ми надіслали пропозицію і переглянули контракт",
                 "We sent the proposal for the contract", "The proposal for the contract was sent", "Ми надіслали пропозицію щодо контракту",
                 "Пропозицію щодо контракту надіслали", "Пропозицію надіслали щодо контракту"] {
        expect(stage(["signals": [["what": text]]], deal)?.value == "s3" && !CRMDealSuggester.establishes(text, label: "Contract sent")
               && CRMDealSuggester.establishes(text, label: "Proposal sent"), "deals: \"\(text)\" supports Proposal sent only")
    }
    expect(stage(["signals": [["what": "We reviewed the proposal, the contract was sent"]]], deal)?.value == "s4", "deals: a sent contract in its own clause is still Contract sent")
    // fix round 3: the stage's thing must be what was sent, not a word next to it
    expect(stage(["signals": [["what": "We sent them the signed contract"]]], deal)?.value == "s4" && stage(["signals": [["what": "We signed the NDA"]]], deal) == nil
           && stage(["signals": [["what": "The contract was sent with the proposal"]]], deal)?.value == "s4",
           "deals: the object of the completed action decides the stage")
    // fix round 4: a stage noun that only describes the object (or sits in its phrase) is no evidence
    for text in ["We sent the pricing sheet for the enterprise contract", "We sent the contract template", "We sent the proposal template",
                 "We sent the pricing sheet for the proposal", "The contract template was sent", "Ми надіслали шаблон контракту",
                 "We sent the enterprise contract checklist", "The pricing sheet for the enterprise contract was sent"] {
        expect(stage(["signals": [["what": text]]], deal) == nil && !CRMDealSuggester.establishes(text, label: "Contract sent")
               && !CRMDealSuggester.establishes(text, label: "Proposal sent"), "deals: no stage from \"\(text)\"")
    }
    for (text, id) in [("Contract sent", "s4"), ("We sent over the final contract today", "s4"), ("The contract has been signed", "s4"),
                       ("We sent the contract draft to Emily Clark", "s4"), ("Пропозицію надіслали", "s3"), ("Ми вже надіслали нашу пропозицію", "s3")] {
        expect(stage(["signals": [["what": text]]], deal)?.value == id, "deals: \"\(text)\" is evidence for its own stage")
    }
    // fix round 5: only an affirmative report counts; doubt or an alternative anywhere in the signal is no evidence
    for text in ["We discussed whether they sent the contract", "We sent the contract, or maybe just the pricing sheet.",
                 "I think we sent the contract", "Possibly the contract was signed", "We sent either the contract or the proposal",
                 "Ми обговорили, чи надіслали вони контракт", "Ми надіслали контракт, або, можливо, лише прайс",
                 "Здається, контракт уже надіслали"] {
        expect(stage(["signals": [["what": text]]], deal) == nil && !CRMDealSuggester.happened(text), "deals: no stage from \"\(text)\"")
    }
    expect(stage(["signals": [["what": "We sent the contract and the pricing sheet"]]], deal)?.value == "s4",
           "deals: a plain report with a second object is still evidence")
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
    // ported to the fix round 8 API: the link (Call id, portal, call key, associations with the deal) is read at the turn
    func stored(_ key: String, _ refs: [CRMRef]) -> (target: CRMCallTarget, managed: String) {
        (CRMCallTarget(callId: disk.callId, portalId: disk.portal, callKey: key, associations: refs), disk.managed)
    }
    func write(_ deal: String) async -> CRMCallDeal.Move? {
        var refs = [CRMRef("companies", "C1"), CRMRef("contacts", "P1")]
        if CRMCallDeal.target(deal) != "" { refs.insert(CRMRef("deals", deal), at: 0) }
        return try? await CRMCallDeal.write(entry, connector: c, queue: q, fallback: stored(entry.key, refs),
                                            load: { stored(entry.key, refs) }, save: { disk.callId = $0; disk.managed = $1 })
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
    expect(disk.managed == "D1", "deals: Undo of the pick puts the managed deal back")
    // review round 3: picking a deal already on the Call, then No deal, keeps that association
    let p19 = await write("D9")
    expect(p19?.added == "" && p19?.removed == "D1" && disk.managed == "", "deals: a picked deal that was already on the Call is not stored as Pacenote's")
    let p9n = await write("none")
    expect(p9n?.removed == "" && p9n?.undoItem == nil && Set(t.calls[id] ?? []) == ["deals/D9", "companies/C1", "contacts/P1"],
           "deals: No deal after picking a deal added in HubSpot removes nothing")
    let p9d = await write("D9")
    expect(p9d?.added == "" && p9d?.removed == "" && disk.managed == "", "deals: re-picking that deal still does not make it Pacenote's")
    _ = await undo(p19)
    expect(Set(t.calls[id] ?? []) == ["deals/D1", "deals/D9", "companies/C1", "contacts/P1"] && disk.managed == "D1", "deals: Undo of that pick links D1 again")
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
    // review round 3, changed in round 6: the old deal is taken off before the new one goes on, so a failed removal
    // adds nothing; the error carries the Call write for Undo and the old deal stays the managed one
    func writeD2() async -> (CRMCallDeal.Move?, Error?) {
        do {
            let refs = [CRMRef("deals", "D2"), CRMRef("companies", "C1"), CRMRef("contacts", "P1")]
            let m = try await CRMCallDeal.write(entry, connector: c, queue: q, fallback: stored(entry.key, refs),
                                                load: { stored(entry.key, refs) }, save: { disk.callId = $0; disk.managed = $1 })
            return (m, nil)
        } catch { return (nil, error) }
    }
    let base = ["deals/D9", "companies/C1", "contacts/P1"]
    t.failRemove = true
    let (_, e1) = await writeD2()
    let partial = e1 as? CRMCallDeal.Partial
    t.failRemove = false
    expect(partial != nil && partial?.move.added == "" && partial?.move.removed == "" && partial?.move.undoItem == nil && disk.managed == "D1"
           && Set(t.calls[id] ?? []) == Set(base + ["deals/D1"]),
           "deals: a failed removal of the old deal adds nothing and keeps the old deal managed")
    let up = await undo(partial?.move)
    expect(up == 0 && Set(t.calls[id] ?? []) == Set(base + ["deals/D1"]) && disk.managed == "D1", "deals: Undo after a failed removal leaves the Call as before")
    let (r1, _) = await writeD2()
    let r1n = await write("none")
    expect(r1?.removed == "D1" && r1?.added == "D2" && r1n?.removed == "D2" && Set(t.calls[id] ?? []) == Set(base) && disk.managed == "",
           "deals: a retry after a failed removal moves the call, and No deal then takes the new deal off")

    // review round 6: the Call update fails after the switch was asked for. The new deal is not part of the Call write,
    // so nothing is added; the retry adds it as Pacenote's and No deal takes it off again
    t.calls[id] = base + ["deals/D1"]; disk.managed = "D1"
    t.failCallPatch = true
    let (m6, e6) = await writeD2()
    t.failCallPatch = false
    expect(m6 == nil && e6 != nil && Set(t.calls[id] ?? []) == Set(base + ["deals/D1"]) && disk.managed == "D1",
           "deals: a failed Call update during a switch adds no deal and keeps the old deal managed")
    let (r6, _) = await writeD2()
    expect(r6?.removed == "D1" && r6?.added == "D2" && disk.managed == "D2" && Set(t.calls[id] ?? []) == Set(base + ["deals/D2"]),
           "deals: the retry after a failed Call update adds the new deal as Pacenote's")
    let r6n = await write("none")
    expect(r6n?.removed == "D2" && Set(t.calls[id] ?? []) == Set(base) && disk.managed == "", "deals: No deal after that retry leaves no deal Pacenote added")

    // review round 6: adding the new deal fails after the old one is off; the error carries the removal, so Undo
    // links the old deal again; the retry finishes the switch
    t.calls[id] = base + ["deals/D1"]; disk.managed = "D1"
    t.failAdd = true
    let (_, e7) = await writeD2()
    t.failAdd = false
    let p7 = e7 as? CRMCallDeal.Partial
    expect(p7?.move.removed == "D1" && p7?.move.added == "" && disk.managed == "" && Set(t.calls[id] ?? []) == Set(base),
           "deals: a failed add after the removal reports the removal and stores no managed deal")
    let u7 = await undo(p7?.move)
    expect(u7 == 0 && Set(t.calls[id] ?? []) == Set(base + ["deals/D1"]) && disk.managed == "D1", "deals: Undo of a failed add links the old deal again")
    // the add of D2 reached HubSpot although it answered an error, and its clean-up fails too: D2 stays managed and in Undo
    t.failAdd = true; t.addLands = true; t.failRemoveDeal = "D2"
    let (_, e9) = await writeD2()
    t.failAdd = false; t.addLands = false; t.failRemoveDeal = ""
    let p9 = e9 as? CRMCallDeal.Partial
    expect(p9?.move.removed == "D1" && p9?.move.added == "D2" && disk.managed == "D2" && Set(t.calls[id] ?? []) == Set(base + ["deals/D2"]),
           "deals: an add that landed and cannot be cleaned up is kept as Pacenote's and in Undo")
    let r9n = await write("none")
    expect(r9n?.removed == "D2" && Set(t.calls[id] ?? []) == Set(base) && disk.managed == "", "deals: No deal then takes that deal off")
    // a Call found by search after the stored id was lost: its deals are read first, so only a new deal is Pacenote's
    t.calls["701"] = ["deals/D1", "companies/C1"]
    final class Lost { var callId = ""; var managed = "D9" }
    let lost = Lost()
    let found = CRMCallLog(key: "k-found", title: "Orbit renewal", start: today, durationMs: 60_000, body: "<p>Summary</p>")
    func writeFound(_ deal: String) async -> CRMCallDeal.Move? {
        let t = (target: CRMCallTarget(callId: lost.callId, portalId: "500", callKey: found.key, associations: [CRMRef("deals", deal), CRMRef("companies", "C1")]),
                 managed: lost.managed)
        return try? await CRMCallDeal.write(found, connector: c, queue: q, fallback: t,
                                            load: { (CRMCallTarget(callId: lost.callId, portalId: "500", callKey: found.key, associations: t.target.associations), lost.managed) },
                                            save: { lost.callId = $0; lost.managed = $1 })
    }
    let f1 = await writeFound("D1")
    expect(f1?.call.id == "701" && f1?.added == "" && f1?.removed == "" && lost.managed == "" && t.calls["701"]?.contains("deals/D9") == false,
           "deals: a Call found by search keeps a deal it already had as not Pacenote's and removes nothing")
    let f2 = await writeFound("D2")
    expect(f2?.added == "D2" && f2?.removed == "" && lost.managed == "D2" && Set(t.calls["701"] ?? []) == ["deals/D1", "deals/D2", "companies/C1"],
           "deals: a new deal on a Call found by search is Pacenote's")
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
    // integration with PB-58 fix round 8: the link is relinked to another portal while HubSpot answers the Call write;
    // neither the Call id nor a managed deal is stored, and no deal goes on that Call
    final class Relinked { var reads = 0; var saved = false }
    let rl = Relinked()
    let relinkEntry = CRMCallLog(key: "k-relinked", title: "Orbit renewal", start: today, durationMs: 60_000, body: "<p>Summary</p>")
    let relinkRefs = [CRMRef("deals", "D1"), CRMRef("companies", "C1")]
    let callsBefore = Set(t.calls.keys)
    let mr = try? await CRMCallDeal.write(relinkEntry, connector: c, queue: q,
                                          fallback: (CRMCallTarget(portalId: "500", callKey: relinkEntry.key, associations: relinkRefs), ""),
                                          load: { rl.reads += 1
                                                  return (CRMCallTarget(portalId: rl.reads == 1 ? "500" : "777", callKey: relinkEntry.key, associations: relinkRefs), "") },
                                          save: { _, _ in rl.saved = true })
    let made = Set(t.calls.keys).subtracting(callsBefore)
    expect(mr?.call.created == true && made.count == 1 && !rl.saved && mr?.added == "" && (made.first.flatMap { t.calls[$0] } ?? []) == ["companies/C1"],
           "deals: a link relinked during the Call write stores nothing and gets no deal")
}
