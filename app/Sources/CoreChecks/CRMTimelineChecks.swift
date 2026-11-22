import Foundation
import LiveTipsCore

/// H5: a small in-memory HubSpot for Meeting and Email engagements. Never the network.
final class FakeHubSpotTimeline: CRMTransport {
    var objects: [String: [String: [String: String]]] = ["meetings": [:], "emails": [:]]    // type -> id -> properties
    var assoc: [String: [String]] = [:]                  // "type/id" -> "to#typeId" at create, "kind/to" by PUT
    var log: [String] = []
    var bodies: [[String: Any]] = []
    var next = 500
    var portal = 500
    var failPut = 0          // the n-th association PUT from now fails with a 500 (0: none)
    var failPatch = false    // a PATCH of an object fails with a 500
    var failDelete: Set<String> = []   // association DELETEs ("kind/id") that fail with a 500
    var onRequest: ((String) -> Void)? // sees "METHOD /path" before it is answered (a Relink while a write is pending)
    static let kinds = ["212": "deals", "188": "companies", "200": "contacts", "210": "deals", "186": "companies", "198": "contacts"]
    /// "kind/id" of everything the object is associated with, however it was added.
    func linked(_ key: String) -> Set<String> {
        Set((assoc[key] ?? []).map { a in
            let x = a.split(separator: "#").map(String.init)
            return x.count == 2 ? (Self.kinds[x[1]] ?? "?") + "/" + x[0] : a
        })
    }
    func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> (Int, [String: Any], [String: String]) {
        let clean = String(path.split(separator: "?")[0])
        log.append(method + " " + clean)
        onRequest?(method + " " + clean)
        if let b = body { bodies.append(b) }
        if clean == "/account-info/v3/details" { return (200, ["portalId": portal], [:]) }
        let parts = clean.split(separator: "/").map(String.init)
        let props = { (b: [String: Any]?) -> [String: String] in ((b?["properties"] as? [String: Any]) ?? [:]).mapValues { "\($0)" } }
        if method == "POST" && parts.last == "search", parts.count == 5, objects[parts[3]] != nil {
            let type = parts[3]
            // every filter of one group must hold; groups are alternatives
            let groups = (body?["filterGroups"] as? [[String: Any]]) ?? []
            let hits = objects[type]!.filter { (id, p) in
                groups.contains { g in
                    ((g["filters"] as? [[String: Any]]) ?? []).allSatisfy { f in
                        let name = f["propertyName"] as! String, v = f["value"] as! String, op = f["operator"] as! String
                        if name.hasPrefix("associations.") {
                            let kind = ["contact": "contacts", "company": "companies", "deal": "deals"][String(name.dropFirst(13))]!
                            return linked(type + "/" + id).contains(kind + "/" + v)
                        }
                        if name == "hs_timestamp" {
                            let ts = HubSpotConnector.time(p["hs_timestamp"] ?? "", or: .distantPast).timeIntervalSince1970 * 1000
                            return op == "GTE" ? ts >= Double(v)! : ts <= Double(v)!
                        }
                        return p[name] == v
                    }
                }
            }
            return (200, ["results": hits.map { ["id": $0.key, "properties": $0.value] }], [:])
        }
        if method == "POST", parts.count == 4, objects[parts[3]] != nil {
            next += 1; let id = "\(next)"
            objects[parts[3]]![id] = props(body)
            assoc[parts[3] + "/" + id] = ((body?["associations"] as? [[String: Any]]) ?? []).map { a in
                "\((a["to"] as! [String: Any])["id"]!)#\(((a["types"] as! [[String: Any]])[0])["associationTypeId"]!)" }
            return (200, ["id": id], [:])
        }
        if method == "PUT", parts.count == 9 {
            if failPut > 0 { failPut -= 1; if failPut == 0 { return (500, ["message": "association failed"], [:]) } }
            assoc[parts[3] + "/" + parts[4], default: []].append(parts[7] + "/" + parts[8]); return (200, [:], [:])
        }
        if method == "DELETE", parts.count == 8 {         // an association
            let key = parts[3] + "/" + parts[4], gone = parts[6] + "/" + parts[7]
            if failDelete.contains(gone) { return (500, ["message": "delete failed"], [:]) }
            assoc[key] = (assoc[key] ?? []).filter { a in
                let x = a.split(separator: "#").map(String.init)
                return (x.count == 2 ? (Self.kinds[x[1]] ?? "?") + "/" + x[0] : a) != gone
            }
            return (204, [:], [:])
        }
        guard parts.count == 5, objects[parts[3]]?[parts[4]] != nil else { return (404, ["message": "not found"], [:]) }
        let (type, id) = (parts[3], parts[4])
        switch method {
        case "GET":
            var a: [String: Any] = [:]
            if path.contains("associations=") {
                for l in linked(type + "/" + id) {
                    let x = l.split(separator: "/").map(String.init)
                    var r = (a[x[0]] as? [String: Any])?["results"] as? [[String: Any]] ?? []
                    r.append(["id": x[1], "type": "default"]); a[x[0]] = ["results": r]
                }
            }
            return (200, ["id": id, "properties": objects[type]![id]!, "associations": a], [:])
        case "PATCH":
            if failPatch { return (500, ["message": "patch failed"], [:]) }
            for (k, v) in props(body) { objects[type]![id]![k] = v }; return (200, ["id": id], [:])
        case "DELETE": objects[type]![id] = nil; return (204, [:], [:])
        default: return (400, [:], [:])
        }
    }
}

func crmTimeline() async {
    let start = Date(timeIntervalSince1970: 1_780_000_000)
    let key = "22222222-2222-2222-2222-222222222222"
    let refs = [CRMRef("deals", "D1"), CRMRef("companies", "C1"), CRMRef("contacts", "P1"), CRMRef("notes", "X")]
    let notesKey = "hs_internal_meeting_notes"
    func meeting(_ summary: [String], at s: Date = start, key k: String = key, title: String = "Orbit renewal") -> CRMMeetingLog {
        CRMMeetingLog(key: k, title: title, start: s, end: s.addingTimeInterval(1800),
                      notes: CRMMeetingLog.notes(summary: summary, decisions: ["Go with the annual plan"], nextSteps: ["Send the demo link"], link: "https://notion.example/p2", key: k))
    }
    func hubspotTime(_ d: Date) -> String { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f.string(from: d) }

    // Meeting created: title, times, outcome, owner, the three association types; the notes name the call id
    let t = FakeHubSpotTimeline()
    let hs = HubSpotConnector(transport: t, ownerId: "9", sleep: { _ in })
    let m1 = try? await hs.upsertMeeting(meeting(["Budget approved"]), knownId: "", associations: refs)
    let p1 = t.objects["meetings"]?[m1?.id ?? ""] ?? [:]
    expect(m1?.created == true && t.objects["meetings"]?.count == 1, "meetings: created once")
    expect(p1["hs_meeting_title"] == "Orbit renewal" && p1["hs_meeting_outcome"] == "COMPLETED" && p1["hubspot_owner_id"] == "9"
           && p1["hs_meeting_start_time"] != nil && p1["hs_meeting_end_time"] != nil && p1["hs_timestamp"] == p1["hs_meeting_start_time"], "meetings: fields, outcome, owner")
    expect(Set(t.assoc["meetings/" + (m1?.id ?? "")] ?? []) == ["D1#212", "C1#188", "P1#200"], "meetings: associated with the deal, the company and the contact only")
    expect((p1[notesKey] ?? "").contains("Budget approved") && (p1[notesKey] ?? "").contains("annual plan") && (p1[notesKey] ?? "").contains(CRMMeetingLog.startMark(key))
           && (p1[notesKey] ?? "").contains(CRMMeetingLog.endMark(key)), "meetings: the notes and the call id")
    expect(t.log.contains("POST /crm/v3/objects/meetings/search"), "meetings: HubSpot is asked for a synced meeting before creating")

    // re-run with the stored id: same meeting, the notes block replaced not repeated, no second POST, no new associations
    let posts = t.log.filter { $0 == "POST /crm/v3/objects/meetings" }.count
    let m2 = try? await hs.upsertMeeting(meeting(["Budget approved", "Legal next"]), knownId: m1?.id ?? "", associations: refs)
    let n2 = t.objects["meetings"]?[m1?.id ?? ""]?[notesKey] ?? ""
    expect(m2?.id == m1?.id && m2?.created == false && t.objects["meetings"]?.count == 1 && t.log.filter { $0 == "POST /crm/v3/objects/meetings" }.count == posts, "meetings: re-run updates the same meeting")
    expect(n2.contains("Legal next") && n2.components(separatedBy: CRMMeetingLog.startMark(key)).count == 2 && (m2?.old[CRMTimeline.notesBeforeKey] ?? "").contains("Budget approved")
           && m2?.old[notesKey] == nil && m2?.old[CRMTimeline.notesCallKey] == key, "meetings: notes appear once; only the old block is kept for Undo, never the whole field")
    expect(!t.log.contains { $0.hasPrefix("PUT ") } && m2?.old[CRMTimeline.addedKey] == nil, "meetings: associations it already has are not added again")
    // stored id lost: the time window finds our own meeting, never a second one
    let m3 = try? await hs.upsertMeeting(meeting(["Id lost"], at: start.addingTimeInterval(120)), knownId: "", associations: refs)
    expect(m3?.id == m1?.id && t.objects["meetings"]?.count == 1, "meetings: no stored id finds the meeting by time and contact")
    // a stale stored id (deleted in HubSpot) with nothing to find makes a new one
    let t0 = FakeHubSpotTimeline(); let hs0 = HubSpotConnector(transport: t0, sleep: { _ in })
    let m0 = try? await hs0.upsertMeeting(meeting(["x"]), knownId: "999", associations: refs)
    expect(m0?.created == true && t0.objects["meetings"]?.count == 1, "meetings: a stale stored id makes a new meeting")

    // HubSpot's own calendar sync made the meeting: the notes are attached, nothing else of it changes, no POST
    let ts = FakeHubSpotTimeline(); let hss = HubSpotConnector(transport: ts, sleep: { _ in })
    ts.objects["meetings"]!["77"] = ["hs_meeting_title": "Orbit renewal sync", "hs_meeting_body": "Invite text of the PM", notesKey: "<p>My own prep</p>",
                                     "hs_timestamp": hubspotTime(start.addingTimeInterval(-300))]
    ts.objects["meetings"]!["78"] = ["hs_meeting_title": "A day later", "hs_timestamp": hubspotTime(start.addingTimeInterval(86400))]
    ts.assoc["meetings/77"] = ["P1#200"]; ts.assoc["meetings/78"] = ["P1#200"]
    // review round 2, #3: alone in the window but with another title and no calendar event: not sure, so nothing is
    // written and it is offered to pick
    let m4q = try? await hss.upsertMeeting(meeting(["Synced"]), knownId: "", associations: refs)
    expect(m4q?.id == "" && m4q?.pick.map(\.id) == ["77"] && m4q?.pick.first?.title == "Orbit renewal sync" && ts.objects["meetings"]?["77"]?[notesKey] == "<p>My own prep</p>"
           && !ts.log.contains { $0.hasPrefix("PUT ") || $0.hasPrefix("PATCH ") || $0 == "POST /crm/v3/objects/meetings" }, "meetings: a nearby meeting with another title is offered, nothing is written")
    // review round 3 (reused sheet): a title or a start alone never writes; the calendar event may be another meeting's (a reused
    // sheet), so even the event's title or start only orders the pick
    var sure = meeting(["Synced"]); sure.eventTitle = "Orbit Renewal  sync"
    let m4s = try? await hss.upsertMeeting(sure, knownId: "", associations: [CRMRef("contacts", "P1")])
    expect(m4s?.id == "" && m4s?.pick.map(\.id) == ["77"], "meetings: the calendar event's title alone only offers the synced meeting")
    var atStart = meeting(["Synced"]); atStart.eventStart = start.addingTimeInterval(-300)
    ts.objects["meetings"]!["77"]!["hs_meeting_start_time"] = hubspotTime(start.addingTimeInterval(-290))
    let m4t = try? await hss.upsertMeeting(atStart, knownId: "", associations: [CRMRef("contacts", "P1")])
    expect(m4t?.id == "" && m4t?.pick.map(\.id) == ["77"], "meetings: starting with the calendar event alone only offers the synced meeting")
    // the PM started an ad hoc call with another meeting's preparation: its sheet came along with that invite's event
    // id, title and start (another title than the call's). The invite's synced meeting matches both, and is only offered
    let tu = FakeHubSpotTimeline(); let hsu = HubSpotConnector(transport: tu, sleep: { _ in })
    tu.objects["meetings"]!["90"] = ["hs_meeting_title": "Board prep", notesKey: "<p>Board prep, private</p>",
                                     "hs_timestamp": hubspotTime(start.addingTimeInterval(600)), "hs_meeting_start_time": hubspotTime(start.addingTimeInterval(600))]
    tu.objects["meetings"]!["91"] = ["hs_meeting_title": "Quick check-in", "hs_timestamp": hubspotTime(start.addingTimeInterval(-120))]
    tu.assoc["meetings/90"] = ["P1#200"]; tu.assoc["meetings/91"] = ["P1#200"]
    let reusedRef = try? JSONDecoder().decode(MeetingRef.self, from: Data(#"{"event_id": "BOARD@1", "start": "2026-05-28T21:00:00Z"}"#.utf8))
    let reusedEv = CRMMeetingLog.event(ref: reusedRef, sheetTitle: "Board prep",
                                       calendar: [(id: "BOARD@1", title: "Board prep", start: start.addingTimeInterval(600))])
    var reused = meeting(["Ad hoc"]); reused.eventTitle = reusedEv.title; reused.eventStart = reusedEv.start
    let mr = try? await hsu.upsertMeeting(reused, knownId: "", associations: refs)
    expect(mr?.id == "" && mr?.created == false && mr?.pick.map(\.id) == ["90", "91"] && tu.objects["meetings"]?["90"]?[notesKey] == "<p>Board prep, private</p>"
           && tu.assoc["meetings/90"] == ["P1#200"] && !tu.log.contains { $0.hasPrefix("PUT ") || $0.hasPrefix("PATCH ") || $0 == "POST /crm/v3/objects/meetings" },
           "meetings: a reused sheet's event never writes to that invite's meeting; every nearby one is offered, the event's first")
    expect(ts.objects["meetings"]?["77"]?[notesKey] == "<p>My own prep</p>", "meetings: nothing was written to the matched meetings")
    // review round 3: the call's calendar event only by the id its sheet kept, never the nearest event
    func mref(_ j: String) -> MeetingRef? { try? JSONDecoder().decode(MeetingRef.self, from: Data(j.utf8)) }
    let near = [(id: "OTHER@1", title: "Board prep", start: start.addingTimeInterval(-60)), (id: "EV@2", title: "Orbit renewal sync", start: start.addingTimeInterval(-300))]
    let noRef = CRMMeetingLog.event(ref: nil, sheetTitle: "", calendar: near)
    let noId = CRMMeetingLog.event(ref: mref(#"{"start": "2026-05-28T21:00:00Z"}"#), sheetTitle: "Orbit renewal", calendar: near)
    expect(noRef.title == "" && noRef.start == nil && noId.title == "" && noId.start == nil, "meetings: no event id, no calendar event (the nearest is not the call's)")
    let byId = CRMMeetingLog.event(ref: mref(#"{"event_id": "EV@2", "start": "2026-05-28T21:00:00Z"}"#), sheetTitle: "Orbit", calendar: near)
    expect(byId.title == "Orbit renewal sync" && byId.start == start.addingTimeInterval(-300), "meetings: the event id finds the call's own event, not the nearest")
    let gone = CRMMeetingLog.event(ref: mref(#"{"event_id": "EV@9", "start": "2026-05-28T21:00:00+02:00"}"#), sheetTitle: "Orbit renewal", calendar: near)
    expect(gone.title == "Orbit renewal" && gone.start == ISO8601DateFormatter().date(from: "2026-05-28T19:00:00Z"), "meetings: an event the calendar dropped keeps the sheet's title and start")
    // an ad hoc call (no event id) at the time of another invite: that invite's synced meeting starts at its own
    // start, but it is only offered to pick
    var adHoc = meeting(["Ad hoc"]); let adEv = CRMMeetingLog.event(ref: nil, sheetTitle: "", calendar: near)
    adHoc.eventTitle = adEv.title; adHoc.eventStart = adEv.start
    let m4a = try? await hss.upsertMeeting(adHoc, knownId: "", associations: [CRMRef("contacts", "P1")])
    expect(m4a?.id == "" && m4a?.pick.map(\.id) == ["77"] && ts.objects["meetings"]?["77"]?[notesKey] == "<p>My own prep</p>", "meetings: an ad hoc call at another invite's time asks before adding notes")
    ts.log = []
    var picked = meeting(["Synced"]); picked.target = "77"       // the PM picked it
    let m4 = try? await hss.upsertMeeting(picked, knownId: "", associations: refs)
    expect(!ts.log.contains("POST /crm/v3/objects/meetings/search"), "meetings: the PM's pick is used without a search")
    let p4 = ts.objects["meetings"]?["77"] ?? [:]
    expect(m4?.id == "77" && m4?.created == false && m4?.attached == true && ts.objects["meetings"]?.count == 2, "meetings: a synced meeting is used, not duplicated")
    expect(!ts.log.contains("POST /crm/v3/objects/meetings") && p4["hs_meeting_title"] == "Orbit renewal sync" && p4["hs_meeting_body"] == "Invite text of the PM", "meetings: no POST; the synced title and invite text stay")
    expect(p4[notesKey]?.hasPrefix("<p>My own prep</p>") == true && p4[notesKey]?.contains("Synced") == true, "meetings: the PM's own notes stay above Pacenote's")
    expect(ts.linked("meetings/77").isSuperset(of: ["deals/D1", "companies/C1"]), "meetings: the synced meeting is also associated with the deal and the company")
    expect(ts.objects["meetings"]?["78"]?[notesKey] == nil, "meetings: only the meeting inside the window is touched")
    // Undo: an attached meeting gets its old notes back and loses the associations Pacenote added; a new one is deleted
    if let id = m4?.id, let old = m4?.old {
        expect(Set(CRMTimeline.decode(old[CRMTimeline.addedKey] ?? "").map { $0.type + "/" + $0.id }) == ["deals/D1", "companies/C1"], "meetings: Undo knows the associations it added")
        try? await hss.restore(type: "meetings", id: id, fields: old)
        expect(ts.objects["meetings"]?[id]?[notesKey] == "<p>My own prep</p>" && ts.linked("meetings/77") == ["contacts/P1"], "meetings: undo restores the old notes and the old associations")
    }
    if let id = m0?.id { try? await hs0.delete(type: "meetings", id: id) }
    expect(t0.objects["meetings"]?.isEmpty == true, "meetings: undo of a new meeting deletes it")

    // finding 3: the company alone is not enough, and two meetings at the time are not guessed between
    let tc = FakeHubSpotTimeline(); let hsc = HubSpotConnector(transport: tc, sleep: { _ in })
    tc.objects["meetings"]!["80"] = ["hs_meeting_title": "Orbit renewal", notesKey: "<p>Board prep, private</p>", "hs_timestamp": hubspotTime(start)]
    tc.assoc["meetings/80"] = ["C1#188", "D1#212"]        // same company and deal, not the call's contact
    let mc = try? await hsc.upsertMeeting(meeting(["Ours"]), knownId: "", associations: refs)
    expect(mc?.created == true && tc.objects["meetings"]?["80"]?[notesKey] == "<p>Board prep, private</p>", "meetings: a meeting on the same company but not on the call's contacts is not used")
    let ta = FakeHubSpotTimeline(); let hsa = HubSpotConnector(transport: ta, sleep: { _ in })
    for (id, title) in [("81", "Weekly sync"), ("82", "Pipeline review")] {
        ta.objects["meetings"]![id] = ["hs_meeting_title": title, notesKey: "<p>\(id) private</p>", "hs_timestamp": hubspotTime(start.addingTimeInterval(60))]
        ta.assoc["meetings/" + id] = ["P1#200"]
    }
    let ma = try? await hsa.upsertMeeting(meeting(["Ours"]), knownId: "", associations: refs)
    expect(ma?.created == false && Set(ma?.pick.map(\.id) ?? []) == ["81", "82"] && ta.objects["meetings"]?.count == 2
           && ta.objects["meetings"]?["81"]?[notesKey] == "<p>81 private</p>" && ta.objects["meetings"]?["82"]?[notesKey] == "<p>82 private</p>",
           "meetings: two meetings at the time with no decisive title: none is guessed, both are offered, nothing is made")
    var fresh = meeting(["Ours"]); fresh.target = CRMMeetingLog.newTarget
    let mn = try? await hsa.upsertMeeting(fresh, knownId: "", associations: refs)
    expect(mn?.created == true && ta.objects["meetings"]?.count == 3 && ta.objects["meetings"]?["81"]?[notesKey] == "<p>81 private</p>", "meetings: the PM's \"new Meeting\" makes one and touches no other")
    ta.objects["meetings"]![mn?.id ?? ""] = nil
    ta.objects["meetings"]!["82"]!["hs_meeting_title"] = "Orbit renewal"
    let mt = try? await hsa.upsertMeeting(meeting(["Ours"]), knownId: "", associations: refs)
    expect(mt?.id == "" && mt?.pick.map(\.id) == ["82", "81"] && ta.objects["meetings"]?["82"]?[notesKey] == "<p>82 private</p>",
           "meetings: of two at the time, the one with the call's title is offered first, both are offered, none is written")
    let tk = FakeHubSpotTimeline(); let hsk = HubSpotConnector(transport: tk, sleep: { _ in })
    let other = "33333333-3333-3333-3333-333333333333"
    tk.objects["meetings"]!["83"] = ["hs_meeting_title": "Orbit renewal", notesKey: meeting(["Another call"], key: other).notes, "hs_timestamp": hubspotTime(start)]
    tk.assoc["meetings/83"] = ["P1#200"]
    let mk = try? await hsk.upsertMeeting(meeting(["Ours"]), knownId: "", associations: refs)
    expect(mk?.created == true && !(tk.objects["meetings"]?["83"]?[notesKey] ?? "").contains("Ours"), "meetings: a meeting holding another call's notes is not used")

    // finding 4: only this call's bounded block is replaced; the text around it stays
    let blockOld = meeting(["old"]).notes, blockNew = meeting(["new"]).notes
    expect(CRMMeetingLog.merge(existing: "", notes: "N", key: key) == "N", "meetings: merge into an empty field")
    expect(CRMMeetingLog.merge(existing: "<p>A</p>" + blockOld, notes: blockNew, key: key) == "<p>A</p>" + blockNew, "meetings: merge replaces the old block")
    expect(CRMMeetingLog.merge(existing: "<p>A</p>" + blockOld + "<p>Added later by Dana</p>", notes: blockNew, key: key) == "<p>A</p>" + blockNew + "<p>Added later by Dana</p>",
           "meetings: text after the block stays")
    let mention = "<p>See the Pacenote notes from last week</p><p>Keep this</p>"
    expect(CRMMeetingLog.merge(existing: mention, notes: blockNew, key: key) == mention + blockNew, "meetings: a PM's mention of Pacenote notes is not cut")
    let otherBlock = meeting(["other call"], key: other).notes
    expect(CRMMeetingLog.merge(existing: otherBlock + blockOld, notes: blockNew, key: key) == otherBlock + blockNew, "meetings: another call's block stays")
    let rewritten = blockOld.replacingOccurrences(of: "<b>", with: "<strong>").replacingOccurrences(of: "</b>", with: "</strong>")
    expect(CRMMeetingLog.merge(existing: "<p>A</p>" + rewritten + "<p>Z</p>", notes: blockNew, key: key) == "<p>A</p>" + blockNew + "<p>Z</p>", "meetings: merge finds a block HubSpot rewrote")
    expect(CRMMeetingLog.notes(summary: ["a <b> & c"], decisions: [], nextSteps: [], link: "", key: "k").contains("a &lt;b&gt; &amp; c"), "meetings: notes are escaped")

    // finding 6: an association fails on a synced meeting: the notes are not changed and the associations just added are taken off
    let tf = FakeHubSpotTimeline(); let hsf = HubSpotConnector(transport: tf, sleep: { _ in })
    tf.objects["meetings"]!["90"] = ["hs_meeting_title": "Orbit renewal", notesKey: "<p>Mine</p>", "hs_timestamp": hubspotTime(start)]
    tf.assoc["meetings/90"] = ["P1#200"]
    tf.failPut = 2      // the deal is added, the company fails
    var pickedF = meeting(["Ours"]); pickedF.target = "90"        // the PM picked the synced meeting
    let mf = try? await hsf.upsertMeeting(pickedF, knownId: "", associations: refs)
    expect(mf == nil && tf.objects["meetings"]?["90"]?[notesKey] == "<p>Mine</p>" && tf.linked("meetings/90") == ["contacts/P1"] && !tf.log.contains { $0.hasPrefix("PATCH") },
           "meetings: a failed association changes nothing and takes off the ones just added")
    tf.failPatch = true
    let mp = try? await hsf.upsertMeeting(meeting(["Ours"]), knownId: "90", associations: refs)
    expect(mp == nil && tf.objects["meetings"]?["90"]?[notesKey] == "<p>Mine</p>" && tf.linked("meetings/90") == ["contacts/P1"], "meetings: a failed notes update takes off the associations it added")
    // review round 2, #6: the rollback DELETE fails too: the association left on the meeting comes back for Undo
    tf.failPatch = false; tf.failPut = 2; tf.failDelete = ["deals/D1"]
    do { _ = try await hsf.upsertMeeting(meeting(["Ours"]), knownId: "90", associations: refs); expect(false, "meetings: a failed rollback still throws") }
    catch let l as CRMTimelineLeftover {
        expect(l.type == "meetings" && l.id == "90" && CRMTimeline.decode(l.fields[CRMTimeline.addedKey] ?? "") == [CRMRef("deals", "D1")] && (l.error as? CRMError)?.code == 500,
               "meetings: the association a failed rollback left is returned with the first error")
        tf.failDelete = []
        try? await hsf.restore(type: "meetings", id: l.id, fields: l.fields)
        expect(tf.linked("meetings/90") == ["contacts/P1"] && tf.objects["meetings"]?["90"]?[notesKey] == "<p>Mine</p>", "meetings: Undo of the leftover takes it off")
    } catch { expect(false, "meetings: a failed rollback names what it left") }
    tf.failPatch = true; tf.failDelete = ["companies/C1"]
    do { _ = try await hsf.upsertMeeting(meeting(["Ours"]), knownId: "90", associations: refs); expect(false, "meetings: a failed notes update with a failed rollback throws") }
    catch let l as CRMTimelineLeftover {
        expect(CRMTimeline.decode(l.fields[CRMTimeline.addedKey] ?? "") == [CRMRef("companies", "C1")] && tf.linked("meetings/90") == ["contacts/P1", "companies/C1"],
               "meetings: after a failed notes update, only the association that could not be taken off is returned")
        tf.failDelete = []; tf.failPatch = false
        try? await hsf.restore(type: "meetings", id: l.id, fields: l.fields)
        expect(tf.linked("meetings/90") == ["contacts/P1"], "meetings: Undo takes the leftover off")
    } catch { expect(false, "meetings: a failed notes update names what it left") }
    // and through the queue: the leftover is an Undo record, the error is the first failure
    let tg = FakeHubSpotTimeline(); let hsg = HubSpotConnector(transport: tg, sleep: { _ in })
    tg.objects["meetings"]!["91"] = ["hs_meeting_title": "Orbit renewal", "hs_timestamp": hubspotTime(start)]
    tg.assoc["meetings/91"] = ["P1#200"]; tg.failPut = 2; tg.failDelete = ["deals/D1"]
    var pickedG = meeting(["Ours"]); pickedG.target = "91"; pickedG.targetPortal = "500"
    let og = await CRMTimelineSync.log(key: key, writer: hsg, connector: hsg, meeting: pickedG, email: nil, associations: refs, queue: CRMCallQueue(),
                                       load: { CRMTimelineIds(portalId: "500") }, save: { _ in })
    expect((og.error as? CRMError)?.code == 500 && og.undo.count == 1 && og.undo.first?.0 == "meetings" && og.undo.first?.1 == "91"
           && og.undo.first?.2[CRMTimeline.addedKey] == "deals/D1", "timeline: an association a failed rollback left stays in Undo")

    // Email: new with associations and the properties of a sent email
    let te = FakeHubSpotTimeline(); let hse = HubSpotConnector(transport: te, ownerId: "9", sleep: { _ in })
    let mail = CRMEmailLog(key: key, subject: "Next steps for Orbit", text: "Hi Dana,\n\nThanks for today.\n\nMarcus", sent: start, from: "marcus@example.com", to: ["dana@example.org"])
    let e1 = try? await hse.upsertEmail(mail, knownId: "", ours: false, associations: refs)
    let q1 = te.objects["emails"]?[e1?.id ?? ""] ?? [:]
    expect(e1?.created == true && e1?.attached == false && q1["hs_email_direction"] == "EMAIL" && q1["hs_email_status"] == "SENT" && q1["hs_email_subject"] == "Next steps for Orbit"
           && q1["hs_email_text"] == mail.text && q1["hubspot_owner_id"] == "9", "emails: a sent email with subject, text and owner")
    expect((q1["hs_email_headers"] ?? "").contains("dana@example.org") && (q1["hs_email_html"] ?? "").contains("<p>Thanks for today.</p>"), "emails: recipients and paragraphs")
    expect(Set(te.assoc["emails/" + (e1?.id ?? "")] ?? []) == ["D1#210", "C1#186", "P1#198"], "emails: associated with the deal, the company and the contact only")
    // re-run of Pacenote's own email: the stored id updates the same email
    let e2 = try? await hse.upsertEmail(CRMEmailLog(key: key, subject: "Next steps for Orbit", text: "Edited", sent: start), knownId: e1?.id ?? "", ours: true, associations: refs)
    expect(e2?.id == e1?.id && e2?.created == false && e2?.attached == false && te.objects["emails"]?.count == 1 && te.objects["emails"]?[e1?.id ?? ""]?["hs_email_text"] == "Edited" && (e2?.old["hs_email_text"] ?? "").hasPrefix("Hi Dana"), "emails: re-run updates the same email")
    if let id = e1?.id, let old = e2?.old {
        try? await hse.restore(type: "emails", id: id, fields: old)
        expect(te.objects["emails"]?[id]?["hs_email_text"] == mail.text, "emails: undo restores the old text")
    }
    if let id = e1?.id { try? await hse.delete(type: "emails", id: id) }
    expect(te.objects["emails"]?.isEmpty == true, "emails: undo of a new email deletes it")

    // finding 5: an email HubSpot logged itself is matched by subject, contact and text, and stays read only
    let tl = FakeHubSpotTimeline(); let hsl = HubSpotConnector(transport: tl, sleep: { _ in })
    tl.objects["emails"]!["60"] = ["hs_email_subject": "Next steps for Orbit", "hs_email_text": "Hi Dana, Thanks for today. Marcus\n\n-- \nMarcus Reed, Northwind", "hs_timestamp": hubspotTime(start)]
    tl.objects["emails"]!["61"] = ["hs_email_subject": "Next steps for Orbit", "hs_email_text": "A different message to the same company", "hs_timestamp": hubspotTime(start)]
    tl.assoc["emails/60"] = ["P1#198"]; tl.assoc["emails/61"] = ["C1#186", "P1#198"]
    let e3 = try? await hsl.upsertEmail(mail, knownId: "", ours: false, associations: refs)
    expect(e3?.id == "60" && e3?.attached == true && e3?.created == false && tl.objects["emails"]?.count == 2, "emails: HubSpot's own logging of the same email is found by subject, contact and text")
    expect(tl.objects["emails"]?["60"]?["hs_email_text"]?.hasSuffix("Northwind") == true && !tl.log.contains { $0.hasPrefix("PATCH") }, "emails: an email Pacenote did not create gets no new content")
    expect(Set(CRMTimeline.decode(e3?.old[CRMTimeline.addedKey] ?? "").map { $0.type }) == ["deals", "companies"] && e3?.old.count == 1, "emails: only the missing associations, and Undo can take them off")
    // a re-run with that stored id (not ours) still never overwrites it
    let e4 = try? await hsl.upsertEmail(CRMEmailLog(key: key, subject: "Next steps for Orbit", text: "Edited", sent: start), knownId: "60", ours: false, associations: refs)
    expect(e4?.attached == true && e4?.old.isEmpty == true && tl.objects["emails"]?["60"]?["hs_email_text"]?.hasSuffix("Northwind") == true && !tl.log.contains { $0.hasPrefix("PATCH") },
           "emails: a re-run on an email Pacenote did not create changes nothing")
    if let old = e3?.old { try? await hsl.restore(type: "emails", id: "60", fields: old) }
    expect(tl.linked("emails/60") == ["contacts/P1"] && tl.objects["emails"]?["60"]?["hs_email_text"]?.hasSuffix("Northwind") == true, "emails: undo takes off only the added associations")
    // the same subject on the company with another text is not this email: a new one is made
    let tx = FakeHubSpotTimeline(); let hsx = HubSpotConnector(transport: tx, sleep: { _ in })
    tx.objects["emails"]!["62"] = ["hs_email_subject": "Next steps for Orbit", "hs_email_text": "A different message", "hs_timestamp": hubspotTime(start)]
    tx.assoc["emails/62"] = ["C1#186", "P1#198"]
    let e5 = try? await hsx.upsertEmail(mail, knownId: "", ours: false, associations: refs)
    expect(e5?.created == true && tx.objects["emails"]?["62"]?["hs_email_text"] == "A different message", "emails: the same subject with another text is not claimed")
    // a reply that quotes the draft is not the draft
    let quoted = "Thanks, see you Tuesday.\n\nOn Tue, Marcus Reed wrote:\n> Hi Dana,\n>\n> Thanks for today.\n>\n> Marcus"
    expect(!CRMEmailLog.sameText(quoted, mail.text) && CRMEmailLog.sameText("Hi Dana,\nThanks for today.\nMarcus\n-- \nNorthwind", mail.text), "emails: quoted history does not match, the draft with a signature does")
    // a failed association on a logged email takes off the ones just added
    let ty = FakeHubSpotTimeline(); let hsy = HubSpotConnector(transport: ty, sleep: { _ in })
    ty.objects["emails"]!["63"] = ["hs_email_subject": "Next steps for Orbit", "hs_email_text": mail.text, "hs_timestamp": hubspotTime(start)]
    ty.assoc["emails/63"] = ["P1#198"]; ty.failPut = 2
    let e6 = try? await hsy.upsertEmail(mail, knownId: "", ours: false, associations: refs)
    expect(e6 == nil && ty.linked("emails/63") == ["contacts/P1"], "emails: a failed association is rolled back")
    // an email with no linked contact cannot be matched and is still created
    let tn = FakeHubSpotTimeline(); let hsn = HubSpotConnector(transport: tn, sleep: { _ in })
    let en = try? await hsn.upsertEmail(mail, knownId: "", ours: false, associations: [CRMRef("companies", "C1")])
    expect(en?.created == true && !tn.log.contains { $0.hasSuffix("/search") }, "emails: no contact, no search")

    // finding 2: the queue reads the stored ids when the turn comes and stores each new id at once; a failure after
    // the Meeting keeps the Meeting's Undo record and id
    final class Disk { var ids = CRMTimelineIds(portalId: "500") }
    let tq = FakeHubSpotTimeline(); let hsq = HubSpotConnector(transport: tq, sleep: { _ in })
    let disk = Disk(), q = CRMCallQueue()
    tq.failPatch = false
    let o1 = await CRMTimelineSync.log(key: key, writer: hsq, connector: hsq, meeting: meeting(["Q"]), email: mail, associations: refs, queue: q,
                                       load: { disk.ids }, save: { disk.ids = $0 })
    expect(o1.error == nil && !disk.ids.meetingId.isEmpty && !disk.ids.emailId.isEmpty && disk.ids.emailOurs && o1.undo.count == 2, "timeline: both ids are stored with the call, the email as Pacenote's")
    let o2 = await CRMTimelineSync.log(key: key, writer: hsq, connector: hsq, meeting: meeting(["Q2"]), email: nil, associations: refs, queue: q,
                                       load: { disk.ids }, save: { disk.ids = $0 })
    expect(o2.error == nil && o2.ids.meetingId == o1.ids.meetingId && tq.objects["meetings"]?.count == 1 && disk.ids.emailId == o1.ids.emailId, "timeline: a second Apply reuses the stored ids and keeps the others")
    let tr = FakeHubSpotTimeline(); let hsr = HubSpotConnector(transport: tr, sleep: { _ in })
    let disk2 = Disk()
    tr.objects["emails"]!["70"] = ["hs_email_subject": "x"]
    disk2.ids.emailId = "70"; disk2.ids.emailOurs = true
    tr.failPatch = true
    let o3 = await CRMTimelineSync.log(key: key, writer: hsr, connector: hsr, meeting: meeting(["Q"]), email: mail, associations: refs, queue: q,
                                       load: { disk2.ids }, save: { disk2.ids = $0 })
    expect(o3.error != nil && o3.undo.count == 1 && o3.undo.first?.0 == "meetings" && !disk2.ids.meetingId.isEmpty, "timeline: what was written before a failure stays undoable and stored")
    // portal: a link from another portal writes nothing
    let tp = FakeHubSpotTimeline(); tp.portal = 777
    let hsp = HubSpotConnector(transport: tp, sleep: { _ in })
    let o4 = await CRMTimelineSync.log(key: key, writer: hsp, connector: hsp, meeting: meeting(["Q"]), email: mail, associations: refs, queue: q,
                                       load: { CRMTimelineIds(portalId: "500") }, save: { _ in })
    expect((o4.error as? CRMError)?.code == CRMPortal.mismatchCode && tp.log.allSatisfy { $0.hasPrefix("GET /account-info") }, "timeline: a link from another portal writes nothing")

    // review round 4: relinked from portal A (500) to portal B (600), both with a Meeting "91". The pick and the Undo
    // records of A are never used on B's "91"
    let pa = FakeHubSpotTimeline(), pb = FakeHubSpotTimeline(); pb.portal = 600
    for f in [pa, pb] {
        f.objects["meetings"]!["91"] = ["hs_meeting_title": f === pa ? "Orbit renewal sync" : "Board review", notesKey: f === pa ? "<p>A prep</p>" : "<p>B private</p>",
                                        "hs_timestamp": hubspotTime(start.addingTimeInterval(-300))]
        f.assoc["meetings/91"] = ["P1#200"]
    }
    let hpa = HubSpotConnector(transport: pa, sleep: { _ in }), hpb = HubSpotConnector(transport: pb, sleep: { _ in })
    let oa = await CRMTimelineSync.log(key: key, writer: hpa, connector: hpa, meeting: meeting(["Ours"]), email: nil, associations: refs, queue: CRMCallQueue(),
                                       load: { CRMTimelineIds(portalId: "500") }, save: { _ in })
    expect(oa.error == nil && oa.pick.map(\.id) == ["91"] && oa.portal == "500", "portals: the pick names the portal that offered it")
    var stale = meeting(["Ours"]); stale.target = "91"; stale.targetPortal = oa.portal
    let ob = await CRMTimelineSync.log(key: key, writer: hpb, connector: hpb, meeting: stale, email: mail, associations: refs, queue: CRMCallQueue(),
                                       load: { CRMTimelineIds(portalId: "600") }, save: { _ in })
    expect((ob.error as? CRMError)?.code == CRMTimelineSync.stalePickCode && ob.undo.isEmpty && pb.objects["meetings"]?["91"]?[notesKey] == "<p>B private</p>"
           && pb.linked("meetings/91") == ["contacts/P1"] && pb.objects["emails"]?.isEmpty == true && pb.log.allSatisfy { $0.hasPrefix("GET /account-info") },
           "portals: a pick from portal A writes nothing to portal B's meeting with the same id")
    var unbound = meeting(["Ours"]); unbound.target = "91"
    let ou = await CRMTimelineSync.log(key: key, writer: hpb, connector: hpb, meeting: unbound, email: nil, associations: refs, queue: CRMCallQueue(),
                                       load: { CRMTimelineIds(portalId: "600") }, save: { _ in })
    expect((ou.error as? CRMError)?.code == CRMTimelineSync.stalePickCode && pb.objects["meetings"]?["91"]?[notesKey] == "<p>B private</p>", "portals: a pick that names no portal is not written")
    // the PM picks A's "91" in A: written, and its Undo record belongs to A
    let oa2 = await CRMTimelineSync.log(key: key, writer: hpa, connector: hpa, meeting: stale, email: nil, associations: refs, queue: CRMCallQueue(),
                                        load: { CRMTimelineIds(portalId: "500") }, save: { _ in })
    expect(oa2.error == nil && oa2.portal == "500" && oa2.undo.first?.1 == "91" && (pa.objects["meetings"]?["91"]?[notesKey] ?? "").contains("Ours"), "portals: the pick is written in its own portal")
    // Undo of A's records with B's key: nothing is restored, deleted or taken off in B
    let pbWrites = pb.log.count
    let ub = try? await CRMUndo.run(oa2.undo + [("meetings", "91", [:])], portal: oa2.portal, connector: hpb)
    expect(ub == nil && pb.objects["meetings"]?["91"] != nil && pb.objects["meetings"]?["91"]?[notesKey] == "<p>B private</p>" && pb.linked("meetings/91") == ["contacts/P1"]
           && pb.log.dropFirst(pbWrites).allSatisfy { $0.hasPrefix("GET /account-info") }, "portals: Undo of portal A's records never touches portal B")
    // and in A it puts A's meeting back
    let ua = try? await CRMUndo.run(oa2.undo, portal: oa2.portal, connector: hpa)
    expect(ua?.failed == 0 && pa.objects["meetings"]?["91"]?[notesKey] == "<p>A prep</p>" && pa.linked("meetings/91") == ["contacts/P1"], "portals: Undo in the portal it was written in restores")
    // a new engagement's Undo deletes it and names it, so its stored id can go
    pa.objects["emails"]!["700"] = ["hs_email_subject": "x"]
    let ud2 = try? await CRMUndo.run([("emails", "700", [:])], portal: "500", connector: hpa)
    expect(ud2?.failed == 0 && ud2?.deleted.first?.1 == "700" && pa.objects["emails"]?["700"] == nil, "portals: a deleted engagement is named for its stored id")

    // review round 5: a Relink to portal B completes while the write to portal A is pending. A's new ids are never
    // stored in B's link, where the same id may be an unrelated record
    let pw = FakeHubSpotTimeline(); let hpw = HubSpotConnector(transport: pw, sleep: { _ in })
    let disk3 = Disk()
    pw.onRequest = { r in if r == "POST /crm/v3/objects/meetings" { disk3.ids = CRMTimelineIds(portalId: "600") } }    // Relink saves B's link
    let ow = await CRMTimelineSync.log(key: key, writer: hpw, connector: hpw, meeting: meeting(["Relinked"]), email: mail, associations: refs, queue: CRMCallQueue(),
                                       load: { disk3.ids }, save: { disk3.ids = $0 })
    expect(ow.error == nil && pw.objects["meetings"]?.count == 1 && ow.portal == "500" && ow.undo.first?.0 == "meetings",
           "relink during a write: the write stays in portal A and undoable there")
    expect(disk3.ids.portalId == "600" && disk3.ids.meetingId.isEmpty && disk3.ids.emailId.isEmpty && !disk3.ids.emailOurs
           && disk3.ids.meetingId != ow.ids.meetingId, "relink during a write: portal A's ids are not stored in portal B's link")

    // review round 6: Relink saves portal B's link after the write read A's link for its portal check and before it
    // stored A's ids. Relink takes the call's turn (CRMCallSync.relink), so it waits for the write's store to end and
    // B's link is never overwritten with A's
    final class Locked: @unchecked Sendable {
        let lock = NSLock(); var ids = CRMTimelineIds(portalId: "500"); var seq: [String] = []; var written = false
        func get<T>(_ f: (Locked) -> T) -> T { lock.lock(); defer { lock.unlock() }; return f(self) }
    }
    let pr = FakeHubSpotTimeline(); let hpr = HubSpotConnector(transport: pr, sleep: { _ in })
    let d6 = Locked(), q6 = CRMCallQueue()
    var relinkJob: Task<Void, Error>?
    pr.onRequest = { r in
        guard r == "POST /crm/v3/objects/meetings", relinkJob == nil else { return }
        d6.get { $0.written = true }
        relinkJob = Task.detached { try await CRMCallSync.relink(key: key, queue: q6) { d6.get { $0.seq.append("relink"); $0.ids = CRMTimelineIds(portalId: "600") } } }
    }
    let or6 = await CRMTimelineSync.log(key: key, writer: hpr, connector: hpr, meeting: meeting(["Interleaved"]), email: nil, associations: refs, queue: q6,
        load: {
            let r = d6.get { $0.ids }
            // the gap the review named: the write read the link, Relink may save before the write stores its ids
            if d6.get({ $0.written }) { Thread.sleep(forTimeInterval: 0.3) }
            return r
        },
        save: { i in d6.get { $0.seq.append("save"); $0.ids = i } })
    try? await relinkJob?.value
    let seq6 = d6.get { $0.seq }, final6 = d6.get { $0.ids }
    expect(or6.error == nil && seq6 == ["save", "relink"], "relink in turn: Relink saves only after the write stored its ids (\(seq6))")
    expect(final6.portalId == "600" && final6.meetingId.isEmpty && final6.emailId.isEmpty, "relink in turn: portal B's link is never overwritten with portal A's")

    // review round 7: Undo re-reads the notes and takes back only Pacenote's block. Text someone added after Apply
    // stays; a block edited since Apply is kept as it is and reported, never overwritten with the old snapshot
    do {
    func synced(_ t: FakeHubSpotTimeline, _ id: String, _ notes: String) {
        t.objects["meetings"]![id] = ["hs_meeting_title": "Orbit renewal", notesKey: notes, "hs_timestamp": hubspotTime(start)]
        t.assoc["meetings/" + id] = ["P1#200"]
    }
    func apply(_ hs: HubSpotConnector, _ id: String, _ summary: [String]) async -> [(String, String, [String: String])] {
        var m = meeting(summary); m.target = id; m.targetPortal = "500"
        return await CRMTimelineSync.log(key: key, writer: hs, connector: hs, meeting: m, email: nil, associations: refs, queue: CRMCallQueue(),
                                         load: { CRMTimelineIds(portalId: "500") }, save: { _ in }).undo
    }
    let tu = FakeHubSpotTimeline(); let hsu = HubSpotConnector(transport: tu, sleep: { _ in })
    synced(tu, "95", "<p>My own prep</p>")
    let u1 = await apply(hsu, "95", ["Ours"])
    tu.objects["meetings"]!["95"]![notesKey]! += "<p>Added in HubSpot by Dana after Apply</p>"
    let r1 = try? await CRMUndo.run(u1, portal: "500", connector: hsu)
    expect(r1?.failed == 0 && r1?.kept == 0 && tu.objects["meetings"]?["95"]?[notesKey] == "<p>My own prep</p><p>Added in HubSpot by Dana after Apply</p>"
           && tu.linked("meetings/95") == ["contacts/P1"], "undo: a note added in HubSpot after Apply stays, only Pacenote's block goes")
    let tv = FakeHubSpotTimeline(); let hsv = HubSpotConnector(transport: tv, sleep: { _ in })
    synced(tv, "96", "<p>My own prep</p>")
    let u2 = await apply(hsv, "96", ["Ours"])
    let edited = tv.objects["meetings"]!["96"]![notesKey]!.replacingOccurrences(of: "Ours", with: "Ours, and Dana's correction")
    tv.objects["meetings"]!["96"]![notesKey] = edited
    let r2 = try? await CRMUndo.run(u2, portal: "500", connector: hsv)
    expect(r2?.failed == 0 && r2?.kept == 1 && tv.objects["meetings"]?["96"]?[notesKey] == edited && tv.linked("meetings/96") == ["contacts/P1"],
           "undo: Pacenote's block edited after Apply is kept and reported; the associations Pacenote added still go")
    tv.objects["meetings"]!["96"]![notesKey] = "<p>My own prep</p><p>Dana removed the Pacenote notes</p>"
    let r3 = try? await CRMUndo.run(u2, portal: "500", connector: hsv)
    expect(r3?.kept == 1 && tv.objects["meetings"]?["96"]?[notesKey] == "<p>My own prep</p><p>Dana removed the Pacenote notes</p>", "undo: a block removed after Apply is not brought back")
    // a re-run's Undo puts back the previous Pacenote block, with the text around it, even after HubSpot rewrote the markup
    let tw = FakeHubSpotTimeline(); let hsw = HubSpotConnector(transport: tw, sleep: { _ in })
    let firstBlock = meeting(["First"]).notes
    synced(tw, "97", "<p>A</p>" + firstBlock + "<p>Z</p>")
    let u3 = await apply(hsw, "97", ["Second"])
    let strong = (tw.objects["meetings"]!["97"]![notesKey] ?? "").replacingOccurrences(of: "<b>", with: "<strong>").replacingOccurrences(of: "</b>", with: "</strong>")
    tw.objects["meetings"]!["97"]![notesKey] = strong + "<p>Later</p>"
    let r4 = try? await CRMUndo.run(u3, portal: "500", connector: hsw)
    expect(r4?.failed == 0 && r4?.kept == 0 && tw.objects["meetings"]?["97"]?[notesKey] == "<p>A</p>" + firstBlock + "<p>Z</p><p>Later</p>",
           "undo: a re-run's Undo restores the previous block in place and keeps the text added later")

    // review round 8: an edit that lives in the markup (a link's destination, a word made a link) is an edit too; only
    // HubSpot's known rewrites (<strong> for <b>, added style or target attributes) are not
    func markupEdit(_ id: String, _ change: (String) -> String) async -> (r: (failed: Int, kept: Int, deleted: [(String, String)])?, notes: String, now: String?, linked: Set<String>) {
        let t = FakeHubSpotTimeline(); let hs = HubSpotConnector(transport: t, sleep: { _ in })
        synced(t, id, "<p>My own prep</p>")
        let u = await apply(hs, id, ["Ours"])
        let notes = change(t.objects["meetings"]![id]![notesKey]!)
        t.objects["meetings"]![id]![notesKey] = notes
        let r = try? await CRMUndo.run(u, portal: "500", connector: hs)
        return (r, notes, t.objects["meetings"]?[id]?[notesKey], t.linked("meetings/" + id))
    }
    let h1 = await markupEdit("98") { $0.replacingOccurrences(of: "href=\"https://notion.example/p2\"", with: "href=\"https://docs.example/orbit-final\"") }
    expect(h1.notes.contains(">https://notion.example/p2</a>") && h1.r?.failed == 0 && h1.r?.kept == 1 && h1.now == h1.notes && h1.linked == ["contacts/P1"],
           "undo: a link destination changed in HubSpot after Apply (same label) is kept and reported")
    let h2 = await markupEdit("99") { $0.replacingOccurrences(of: "<li>Ours</li>", with: "<li><a href=\"https://docs.example/q3\">Ours</a></li>") }
    expect(h2.r?.kept == 1 && h2.now == h2.notes, "undo: a word made a link in HubSpot after Apply is kept and reported")
    let h3 = await markupEdit("100") {
        $0.replacingOccurrences(of: "<a href=", with: "<a target=\"_blank\" rel=\"noopener\" href=").replacingOccurrences(of: "<p>", with: "<p style=\"margin:0\">")
          .replacingOccurrences(of: "<b>", with: "<strong>").replacingOccurrences(of: "</b>", with: "</strong>")
    }
    expect(h3.r?.failed == 0 && h3.r?.kept == 0 && h3.now == "<p style=\"margin:0\">My own prep</p>" && h3.linked == ["contacts/P1"],
           "undo: HubSpot's own rewrite of the markup (strong, style, target) is not an edit; Pacenote's block goes")
    }
}
