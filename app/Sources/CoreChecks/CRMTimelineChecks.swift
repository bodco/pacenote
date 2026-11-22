import Foundation
import LiveTipsCore

/// H5: a small in-memory HubSpot for Meeting and Email engagements. Never the network.
final class FakeHubSpotTimeline: CRMTransport {
    var objects: [String: [String: [String: String]]] = ["meetings": [:], "emails": [:]]    // type -> id -> properties
    var assoc: [String: [String]] = [:]                  // "type/id" -> "to#typeId" at create, "kind/to" by PUT
    var log: [String] = []
    var bodies: [[String: Any]] = []
    var next = 500
    func send(_ method: String, _ path: String, _ body: [String: Any]?) async throws -> (Int, [String: Any], [String: String]) {
        let clean = String(path.split(separator: "?")[0])
        log.append(method + " " + clean)
        if let b = body { bodies.append(b) }
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
                            return (assoc[type + "/" + id] ?? []).contains { $0.hasPrefix(v + "#") || $0 == kind + "/" + v }
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
            assoc[parts[3] + "/" + parts[4], default: []].append(parts[7] + "/" + parts[8]); return (200, [:], [:])
        }
        guard parts.count == 5, objects[parts[3]]?[parts[4]] != nil else { return (404, ["message": "not found"], [:]) }
        let (type, id) = (parts[3], parts[4])
        switch method {
        case "GET": return (200, ["id": id, "properties": objects[type]![id]!], [:])
        case "PATCH": for (k, v) in props(body) { objects[type]![id]![k] = v }; return (200, ["id": id], [:])
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
    func meeting(_ summary: [String], at s: Date = start) -> CRMMeetingLog {
        CRMMeetingLog(key: key, title: "Orbit renewal", start: s, end: s.addingTimeInterval(1800),
                      notes: CRMMeetingLog.notes(summary: summary, decisions: ["Go with the annual plan"], nextSteps: ["Send the demo link"], link: "https://notion.example/p2", key: key))
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
    expect((p1[notesKey] ?? "").contains("Budget approved") && (p1[notesKey] ?? "").contains("annual plan") && (p1[notesKey] ?? "").contains("Pacenote call id: " + key), "meetings: the notes and the call id")
    expect(t.log.contains("POST /crm/v3/objects/meetings/search"), "meetings: HubSpot is asked for a synced meeting before creating")

    // re-run with the stored id: same meeting, the notes block replaced not repeated, no second POST
    let posts = t.log.filter { $0 == "POST /crm/v3/objects/meetings" }.count
    let m2 = try? await hs.upsertMeeting(meeting(["Budget approved", "Legal next"]), knownId: m1?.id ?? "", associations: refs)
    let n2 = t.objects["meetings"]?[m1?.id ?? ""]?[notesKey] ?? ""
    expect(m2?.id == m1?.id && m2?.created == false && t.objects["meetings"]?.count == 1 && t.log.filter { $0 == "POST /crm/v3/objects/meetings" }.count == posts, "meetings: re-run updates the same meeting")
    expect(n2.contains("Legal next") && n2.components(separatedBy: "Pacenote call id").count == 2 && (m2?.old[notesKey] ?? "").contains("Budget approved"), "meetings: notes appear once; the old value is kept for Undo")
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
    let m4 = try? await hss.upsertMeeting(meeting(["Synced"]), knownId: "", associations: refs)
    let p4 = ts.objects["meetings"]?["77"] ?? [:]
    expect(m4?.id == "77" && m4?.created == false && m4?.attached == true && ts.objects["meetings"]?.count == 2, "meetings: a synced meeting is used, not duplicated")
    expect(!ts.log.contains("POST /crm/v3/objects/meetings") && p4["hs_meeting_title"] == "Orbit renewal sync" && p4["hs_meeting_body"] == "Invite text of the PM", "meetings: no POST; the synced title and invite text stay")
    expect(p4[notesKey]?.hasPrefix("<p>My own prep</p>") == true && p4[notesKey]?.contains("Synced") == true, "meetings: the PM's own notes stay above Pacenote's")
    expect(Set(ts.assoc["meetings/77"] ?? []).isSuperset(of: ["deals/D1", "companies/C1"]), "meetings: the synced meeting is also associated with the deal and the company")
    expect(ts.objects["meetings"]?["78"]?[notesKey] == nil, "meetings: only the meeting inside the window is touched")
    // Undo: an attached meeting gets its old notes back; a new one is deleted
    if let id = m4?.id, let old = m4?.old {
        try? await hss.restore(type: "meetings", id: id, fields: old)
        expect(ts.objects["meetings"]?[id]?[notesKey] == "<p>My own prep</p>", "meetings: undo restores the old notes")
    }
    if let id = m0?.id { try? await hs0.delete(type: "meetings", id: id) }
    expect(t0.objects["meetings"]?.isEmpty == true, "meetings: undo of a new meeting deletes it")
    expect(CRMMeetingLog.merge(existing: "", notes: "N") == "N", "meetings: merge into an empty field")
    expect(CRMMeetingLog.merge(existing: "A" + CRMMeetingLog.heading + "old", notes: CRMMeetingLog.heading + "new") == "A" + CRMMeetingLog.heading + "new", "meetings: merge replaces the old block")
    expect(CRMMeetingLog.merge(existing: "<p>A</p><p><strong>Pacenote notes</strong></p><p>old</p>", notes: CRMMeetingLog.heading + "new") == "<p>A</p>" + CRMMeetingLog.heading + "new", "meetings: merge finds a heading HubSpot rewrote")
    expect(CRMMeetingLog.notes(summary: ["a <b> & c"], decisions: [], nextSteps: [], link: "", key: "k").contains("a &lt;b&gt; &amp; c"), "meetings: notes are escaped")

    // Email: new with associations and the properties of a sent email
    let te = FakeHubSpotTimeline(); let hse = HubSpotConnector(transport: te, ownerId: "9", sleep: { _ in })
    let mail = CRMEmailLog(key: key, subject: "Next steps for Orbit", text: "Hi Dana,\n\nThanks for today.\n\nMarcus", sent: start, from: "marcus@example.com", to: ["dana@example.org"])
    let e1 = try? await hse.upsertEmail(mail, knownId: "", associations: refs)
    let q1 = te.objects["emails"]?[e1?.id ?? ""] ?? [:]
    expect(e1?.created == true && q1["hs_email_direction"] == "EMAIL" && q1["hs_email_status"] == "SENT" && q1["hs_email_subject"] == "Next steps for Orbit"
           && q1["hs_email_text"] == mail.text && q1["hubspot_owner_id"] == "9", "emails: a sent email with subject, text and owner")
    expect((q1["hs_email_headers"] ?? "").contains("dana@example.org") && (q1["hs_email_html"] ?? "").contains("<p>Thanks for today.</p>"), "emails: recipients and paragraphs")
    expect(Set(te.assoc["emails/" + (e1?.id ?? "")] ?? []) == ["D1#210", "C1#186", "P1#198"], "emails: associated with the deal, the company and the contact only")
    // re-run: the stored id updates the same email; a lost id finds it by subject, never a second one
    let e2 = try? await hse.upsertEmail(CRMEmailLog(key: key, subject: "Next steps for Orbit", text: "Edited", sent: start), knownId: e1?.id ?? "", associations: refs)
    expect(e2?.id == e1?.id && e2?.created == false && te.objects["emails"]?.count == 1 && te.objects["emails"]?[e1?.id ?? ""]?["hs_email_text"] == "Edited" && (e2?.old["hs_email_text"] ?? "").hasPrefix("Hi Dana"), "emails: re-run updates the same email")
    let e3 = try? await hse.upsertEmail(mail, knownId: "", associations: refs)
    expect(e3?.attached == true && e3?.created == false && e3?.old.isEmpty == true && te.objects["emails"]?.count == 1 && te.objects["emails"]?[e1?.id ?? ""]?["hs_email_text"] == "Edited", "emails: an email already in HubSpot is left as it is")
    if let id = e1?.id, let old = e2?.old {
        try? await hse.restore(type: "emails", id: id, fields: old)
        expect(te.objects["emails"]?[id]?["hs_email_text"] == mail.text, "emails: undo restores the old text")
    }
    if let id = e1?.id { try? await hse.delete(type: "emails", id: id) }
    expect(te.objects["emails"]?.isEmpty == true, "emails: undo of a new email deletes it")
    // an email with no linked object cannot be searched for and is still created
    let tn = FakeHubSpotTimeline(); let hsn = HubSpotConnector(transport: tn, sleep: { _ in })
    let en = try? await hsn.upsertEmail(mail, knownId: "", associations: [])
    expect(en?.created == true && !tn.log.contains { $0.hasSuffix("/search") }, "emails: no links, no search")
    // the logs take only the call page's text: a transcript line never reaches either API
    expect(!"\(t.bodies)\(ts.bodies)\(te.bodies)".contains("SECRET WORDS"), "timeline: nothing sent carries transcript text")
}
