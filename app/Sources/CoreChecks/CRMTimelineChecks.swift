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
    expect(n2.contains("Legal next") && n2.components(separatedBy: CRMMeetingLog.startMark(key)).count == 2 && (m2?.old[notesKey] ?? "").contains("Budget approved"), "meetings: notes appear once; the old value is kept for Undo")
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
    let m4 = try? await hss.upsertMeeting(meeting(["Synced"]), knownId: "", associations: refs)
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
    expect(ma?.created == true && ma?.ambiguous == true && ta.objects["meetings"]?["81"]?[notesKey] == "<p>81 private</p>" && ta.objects["meetings"]?["82"]?[notesKey] == "<p>82 private</p>",
           "meetings: two meetings at the time with no decisive title: none is guessed, a new one is made")
    ta.objects["meetings"]!["82"]!["hs_meeting_title"] = "Orbit renewal"
    ta.objects["meetings"]![ma?.id ?? ""] = nil
    let mt = try? await hsa.upsertMeeting(meeting(["Ours"]), knownId: "", associations: refs)
    expect(mt?.id == "82" && ta.objects["meetings"]?["81"]?[notesKey] == "<p>81 private</p>", "meetings: of two at the time, the one with the call's title is used")
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
    let mf = try? await hsf.upsertMeeting(meeting(["Ours"]), knownId: "", associations: refs)
    expect(mf == nil && tf.objects["meetings"]?["90"]?[notesKey] == "<p>Mine</p>" && tf.linked("meetings/90") == ["contacts/P1"] && !tf.log.contains { $0.hasPrefix("PATCH") },
           "meetings: a failed association changes nothing and takes off the ones just added")
    tf.failPatch = true
    let mp = try? await hsf.upsertMeeting(meeting(["Ours"]), knownId: "90", associations: refs)
    expect(mp == nil && tf.objects["meetings"]?["90"]?[notesKey] == "<p>Mine</p>" && tf.linked("meetings/90") == ["contacts/P1"], "meetings: a failed notes update takes off the associations it added")

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
}
