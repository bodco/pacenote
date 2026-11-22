import Foundation
import LiveTipsCore

/// PB-17 fix round 1: the task workflow (CRMTaskFlow) on the fake HubSpot. Overlapping adds, twins of another
/// customer, writes across portals, refused Remove and Undo, and the after-call add with its switch off.
@MainActor
func crmTaskFlowChecks() async {
    final class Box {
        var settings = CRMTasksSettings()
        var notes: [CRMTaskFlow.Note] = []
        var slow = false
    }
    let box = Box()
    box.settings.enabled = true
    let dir = NSTemporaryDirectory() + "pb17-flow-" + UUID().uuidString
    let folder = dir + "/live"
    try? FileManager.default.createDirectory(atPath: folder, withIntermediateDirectories: true)
    let hs = FakeHubSpotTasks()
    hs.tasks = ["1": ["hs_task_subject": "Send the pricing sheet", "hs_task_status": "NOT_STARTED", "hubspot_owner_id": "9"],
                "2": ["hs_task_subject": "Book the venue for the offsite", "hs_task_status": "NOT_STARTED", "hubspot_owner_id": "9"]]
    hs.assoc = ["1": ["contacts:P9"], "2": ["deals:D1"]]
    func makeFlow() -> CRMTaskFlow {
        CRMTaskFlow(cachePath: dir + "/state/hubspot-tasks.json", connected: { true },
                    connector: { HubSpotConnector(transport: hs, ownerId: "9", sleep: { _ in }) },
                    settings: { box.settings }, matchingOwner: { "9" }, items: { CommitmentRegister.load(folder: folder) },
                    notify: { box.notes.append($0) },
                    sleep: { _ in if box.slow { try? await Task.sleep(nanoseconds: 300_000_000) } })
    }
    let flow = makeFlow()
    func item(_ id: String) -> Commitment? { CommitmentRegister.load(folder: folder).first { $0.id == id } }
    func creates() -> Int { hs.log.filter { $0 == "POST /crm/v3/objects/tasks" }.count }
    let call = "2026-11-20-1000-orbit.md"
    let props = { (title: String) in CRMTasks.newTask(box.settings, title: title, due: Date(timeIntervalSince1970: 1_790_000_000), body: "From the call Orbit") }

    await flow.sync()
    expect(flow.portal == "111" && Set(flow.shown.map(\.id)) == ["1", "2"], "hflow: the sync binds the portal and lists the tasks")

    // 1. two adds of the same item at once make one task; an add from a stale menu after it makes none
    let a = CommitmentRegister.add(folder: folder, text: "Share the security review", project: "orbit", title: "Orbit", call: call) ?? ""
    let ca = item(a)!
    let before = creates()
    async let r1 = flow.add(ca, properties: props(ca.text), refs: [CRMRef("deals", "D1")])
    async let r2 = flow.add(ca, properties: props(ca.text), refs: [CRMRef("deals", "D1")])
    let (x1, x2) = await (r1, r2)
    expect(creates() - before == 1 && [x1, x2].compactMap { $0 }.count == 1, "hflow: overlapping adds create one task")
    expect(item(a)?.crmTask == (x1 ?? x2)?.id && item(a)?.crmPortal == "111", "hflow: the link keeps the task and its portal")
    let stale = await flow.add(ca, properties: props(ca.text), refs: [CRMRef("deals", "D1")])
    expect(stale == nil && creates() - before == 1, "hflow: the register is read again before a create")

    // 2. a contact-only call never reuses a similar task of another contact; the same contact is linked
    let b = CommitmentRegister.add(folder: folder, text: "Send pricing sheet", project: "orbit", title: "Orbit", call: call) ?? ""
    let made = await flow.add(item(b)!, properties: props("Send pricing sheet"), refs: [CRMRef("contacts", "P1")])
    expect(made?.created == true && made?.id != "1", "hflow: a twin on another contact is not linked")
    let b2 = CommitmentRegister.add(folder: folder, text: "Send the pricing sheet today", project: "orbit", title: "Orbit", call: "2026-11-21-1000-orbit.md") ?? ""
    let linked = await flow.add(item(b2)!, properties: props("Send the pricing sheet today"), refs: [CRMRef("contacts", "P9")])
    expect(linked == CRMTaskFlow.Added(id: "1", created: false), "hflow: a twin on the same contact is linked")
    // the cache says deal D1, a fresh read says the task left the deal: a new task
    hs.assoc["2"] = []
    let b3 = CommitmentRegister.add(folder: folder, text: "Book the venue for the offsite", project: "orbit", title: "Orbit", call: call) ?? ""
    let fresh = await flow.add(item(b3)!, properties: props("Book the venue for the offsite"), refs: [CRMRef("deals", "D1")])
    expect(fresh?.created == true && fresh?.id != "2", "hflow: a twin is checked on a fresh read of its associations")
    hs.assoc["2"] = ["deals:D1"]

    // 3. a tick queued offline is never sent to another portal; links of the old portal are left alone
    await flow.sync()
    hs.offline = ["PATCH /crm/v3/objects/tasks/2"]
    let t2 = flow.tasks.first { $0.id == "2" }!
    await flow.tick(t2)?.value
    expect(flow.pending.count == 1 && flow.pending.first?["portal"] == "111", "hflow: an offline tick waits with its portal")
    hs.offline = []
    hs.portalId = "222"
    let mark = hs.log.count
    await flow.sync()
    let after = hs.log[mark...]
    expect(!after.contains { $0.hasPrefix("PATCH") || $0.hasPrefix("DELETE") } && flow.pending.isEmpty && flow.portal == "222",
           "hflow: another portal drops the queue, nothing is sent there")
    expect(!after.contains { $0.hasPrefix("GET /crm/v3/objects/tasks/") } && item(a)?.crmTask.isEmpty == false && item(a)?.crmPortal == "111",
           "hflow: links of the old portal are not read or unlinked")
    expect(flow.linkedCommitment(CRMTask(id: item(a)?.crmTask ?? "", title: "")) == nil, "hflow: a task of the new portal is not the old link")
    // a tick on a row read from 222 after the key moved back to 111 writes nothing
    let t1 = flow.tasks.first { $0.id == "1" }!
    hs.portalId = "111"
    let mark2 = hs.log.count
    await flow.tick(t1)?.value
    expect(!hs.log[mark2...].contains { $0.hasPrefix("PATCH") }, "hflow: a tick is not sent when the key opens another portal")
    expect(box.notes.contains { if case .accountChanged = $0 { return true }; return false }, "hflow: the PM is told the account changed")
    await flow.sync()

    // 4. Remove that HubSpot refuses gives the task and the item back; Undo writes nothing
    let r = CommitmentRegister.add(folder: folder, text: "Draft the rollout plan", project: "orbit", title: "Orbit", call: call) ?? ""
    let rt = await flow.add(item(r)!, properties: props("Draft the rollout plan"), refs: [CRMRef("deals", "D1")])
    let task = flow.tasks.first { $0.id == rt?.id }!
    hs.fail["DELETE /crm/v3/objects/tasks/" + task.id] = 403
    await flow.remove(task, commitment: flow.linkedCommitment(task))?.value
    expect(item(r)?.status == "open" && item(r)?.crmTask == task.id && flow.shown.contains { $0.id == task.id },
           "hflow: a refused Remove gives the item and the task back")
    expect(box.notes.contains { if case .removeRefused = $0 { return true }; return false } && hs.tasks[task.id] != nil, "hflow: the refusal is reported")
    hs.fail = [:]
    hs.offline = ["DELETE /crm/v3/objects/tasks/" + task.id]
    await flow.remove(task, commitment: flow.linkedCommitment(task))?.value
    expect(item(r)?.status == "dropped" && !flow.shown.contains { $0.id == task.id } && flow.pending.contains { $0["op"] == "delete" },
           "hflow: an offline Remove waits in the queue")
    hs.offline = []
    await flow.sync()
    expect(hs.tasks[task.id] == nil && flow.pending.isEmpty, "hflow: the queued delete is sent at the next sync")
    box.slow = true
    let u = CommitmentRegister.add(folder: folder, text: "Send the meeting notes", project: "orbit", title: "Orbit", call: call) ?? ""
    let ut = await flow.add(item(u)!, properties: props("Send the meeting notes"), refs: [CRMRef("deals", "D1")])
    let utask = flow.tasks.first { $0.id == ut?.id }!
    let job = flow.remove(utask, commitment: flow.linkedCommitment(utask))
    if case .removed(let undo)? = box.notes.last { undo() }
    await job?.value
    expect(item(u)?.status == "open" && hs.tasks[utask.id] != nil && !hs.log.contains("DELETE /crm/v3/objects/tasks/" + utask.id),
           "hflow: Undo of Remove writes nothing to HubSpot")
    box.slow = false

    // 5. after the call: switch off sends nothing; on, Undo deletes, a refused delete keeps its link, offline retries
    let call2 = "2026-11-22-1000-orbit.md"
    let p1 = CommitmentRegister.add(folder: folder, text: "Send the contract draft", project: "orbit", title: "Orbit", call: call2) ?? ""
    let p2 = CommitmentRegister.add(folder: folder, text: "Introduce Michael Johnson to support", project: "orbit", title: "Orbit", call: call2) ?? ""
    let p3 = CommitmentRegister.add(folder: folder, text: "Check the invoice address", project: "orbit", title: "Orbit", call: call2) ?? ""
    let draft = { (c: Commitment) in (properties: props(c.text), refs: [CRMRef("deals", "D1")]) }
    hs.log = []
    await flow.afterCall(file: call2, folder: folder, destination: "hubspot", draft: draft)
    expect(hs.log.isEmpty && item(p1)?.crmTask == "", "hflow: auto add off sends no request at all")
    box.settings.autoAdd = true
    await flow.afterCall(file: call2, folder: folder, destination: "notion", draft: draft)
    expect(hs.log.isEmpty, "hflow: auto add on but Notion the destination: nothing to HubSpot")
    await flow.afterCall(file: call2, folder: folder, destination: "hubspot", draft: draft)
    let ids = [p1, p2, p3].map { item($0)?.crmTask ?? "" }
    expect(ids.allSatisfy { !$0.isEmpty } && hs.writes().filter { $0 == "POST /crm/v3/objects/tasks" }.count == 3, "hflow: auto add on makes the tasks")
    hs.fail["DELETE /crm/v3/objects/tasks/" + ids[1]] = 403
    hs.offline = ["DELETE /crm/v3/objects/tasks/" + ids[2]]
    let undo = await flow.undoAdds(ids, folder: folder, at: flow.portal)
    expect(undo[ids[0]] == .sent && item(p1)?.crmTask == "" && hs.tasks[ids[0]] == nil, "hflow: Undo deletes the task and unlinks the item")
    expect(item(p2)?.crmTask == ids[1] && hs.tasks[ids[1]] != nil && flow.shown.contains { $0.id == ids[1] }
           && box.notes.contains { if case .undoRefused(1, _) = $0 { return true }; return false }, "hflow: a refused Undo keeps the link and says so")
    expect(item(p3)?.crmTask == ids[2] && !flow.shown.contains { $0.id == ids[2] } && flow.pending.contains { $0["task"] == ids[2] },
           "hflow: an offline Undo keeps the delete queued")
    hs.offline = []
    await flow.sync()
    expect(hs.tasks[ids[2]] == nil && item(p3)?.crmTask == "" && item(p3)?.status == "open", "hflow: the queued Undo is sent and unlinks the item")

    // 6. a refused tick gives the item back
    let k = CommitmentRegister.add(folder: folder, text: "Confirm the pilot dates", project: "orbit", title: "Orbit", call: call) ?? ""
    let kt = await flow.add(item(k)!, properties: props("Confirm the pilot dates"), refs: [CRMRef("deals", "D1")])
    let ktask = flow.tasks.first { $0.id == kt?.id }!
    hs.fail["PATCH /crm/v3/objects/tasks/" + ktask.id] = 400
    await flow.tick(ktask)?.value
    expect(item(k)?.status == "open" && flow.shown.contains { $0.id == ktask.id }, "hflow: a refused tick gives the item back")
    hs.fail = [:]

    // fix round 2
    // 7. a task deleted in HubSpot (404) unlinks its item, which can be added again
    let g = CommitmentRegister.add(folder: folder, text: "Send the onboarding checklist", project: "orbit", title: "Orbit", call: call) ?? ""
    let gt = await flow.add(item(g)!, properties: props("Send the onboarding checklist"), refs: [CRMRef("deals", "D1")])
    hs.tasks[gt?.id ?? ""] = nil
    await flow.sync()
    expect(item(g)?.crmTask == "" && item(g)?.status == "open", "hflow: a task deleted in HubSpot unlinks its item")
    let gAgain = await flow.add(item(g)!, properties: props("Send the onboarding checklist"), refs: [CRMRef("deals", "D1")])
    expect(gAgain?.created == true, "hflow: an item whose task was deleted in HubSpot can be added again")

    // 8. Undo of a tick goes to the portal of the tick or nowhere
    let tt = flow.tasks.first { $0.id == gAgain?.id }!
    box.notes = []
    await flow.tick(tt)?.value
    var tickUndo: (() -> Void)?
    if case .completed(_, let u)? = box.notes.last { tickUndo = u }
    hs.portalId = "222"
    await flow.sync()
    let mark3 = hs.log.count
    tickUndo?()
    for _ in 0..<20 { await Task.yield() }
    expect(tickUndo != nil && !hs.log[mark3...].contains { $0.hasPrefix("PATCH") } && !flow.tasks.contains { $0.id == tt.id },
           "hflow: Undo of a tick after the key moved to another portal writes nothing and adds no row")
    expect(item(g)?.status == "done" && box.notes.contains { if case .accountChanged = $0 { return true }; return false },
           "hflow: the item stays as HubSpot has it and the PM is told")
    hs.portalId = "111"
    await flow.sync()

    // 9. an item linked to a task outside the lists (another owner's) still ticks and removes it in HubSpot
    let o = CommitmentRegister.add(folder: folder, text: "Call Sarah Miller about the renewal", project: "orbit", title: "Orbit", call: call) ?? ""
    let ot = await flow.add(item(o)!, properties: props("Call Sarah Miller about the renewal"), refs: [CRMRef("deals", "D1")])
    let oid = ot?.id ?? ""
    hs.tasks[oid]?["hubspot_owner_id"] = "5"
    await flow.sync()
    expect(!flow.shown.contains { $0.id == oid } && item(o)?.crmTask == oid, "hflow: a task of another owner leaves the list, the link stays")
    await flow.tickLinked(item(o)!, done: true)?.value
    expect(hs.tasks[oid]?["hs_task_status"] == "COMPLETED" && item(o)?.status == "done", "hflow: a tick on a linked item outside the list reaches HubSpot")
    if case .completed(_, let u)? = box.notes.last { u() }
    for _ in 0..<20 { await Task.yield() }
    expect(hs.tasks[oid]?["hs_task_status"] == "NOT_STARTED" && item(o)?.status == "open" && !flow.shown.contains { $0.id == oid },
           "hflow: Undo of that tick reopens it in HubSpot without adding it to the list")
    await flow.tickLinked(item(o)!, done: true)?.value
    await flow.tickLinked(item(o)!, done: false)?.value
    expect(hs.tasks[oid]?["hs_task_status"] == "NOT_STARTED" && item(o)?.status == "open", "hflow: reopening it reaches HubSpot too")
    await flow.removeLinked(item(o)!)?.value
    expect(hs.tasks[oid] == nil && item(o)?.status == "dropped", "hflow: Remove on a linked item outside the list deletes the task")
    let o2 = CommitmentRegister.add(folder: folder, text: "Share the case study", project: "orbit", title: "Orbit", call: call) ?? ""
    let o2t = await flow.add(item(o2)!, properties: props("Share the case study"), refs: [CRMRef("deals", "D1")])
    hs.fail["PATCH /crm/v3/objects/tasks/" + (o2t?.id ?? "")] = 403
    await flow.tickLinked(item(o2)!, done: true)?.value
    expect(item(o2)?.status == "open" && box.notes.contains { if case .refused = $0 { return true }; return false },
           "hflow: a refused tick on a linked item gives it back and says so")
    hs.fail = [:]

    // 10. a busy HubSpot (5xx) keeps the delete queued; a refusal at replay is reported with Retry and kept in the error line
    let q = CommitmentRegister.add(folder: folder, text: "Send the revised quote", project: "orbit", title: "Orbit", call: call) ?? ""
    let qt = await flow.add(item(q)!, properties: props("Send the revised quote"), refs: [CRMRef("deals", "D1")])
    let qid = qt?.id ?? ""
    let qtask = flow.tasks.first { $0.id == qid }!
    hs.fail["DELETE /crm/v3/objects/tasks/" + qid] = 503
    box.notes = []
    await flow.remove(qtask, commitment: flow.linkedCommitment(qtask))?.value
    expect(item(q)?.status == "dropped" && flow.pending.contains { $0["task"] == qid } && !box.notes.contains { if case .removeRefused = $0 { return true }; return false },
           "hflow: a 5xx on Remove keeps the delete queued")
    hs.fail["DELETE /crm/v3/objects/tasks/" + qid] = 403
    await flow.sync()
    var retry: (() async -> Void)?
    if let n = box.notes.last(where: { if case .replayRefused = $0 { return true }; return false }), case .replayRefused(1, _, let r) = n { retry = r }
    expect(retry != nil && item(q)?.status == "open" && flow.shown.contains { $0.id == qid } && flow.pending.isEmpty && !flow.error.isEmpty,
           "hflow: a delete refused at replay gives the item back, says so with Retry and keeps the error")
    hs.fail = [:]
    await retry?()
    expect(hs.tasks[qid] == nil && item(q)?.status == "dropped" && flow.pending.isEmpty && flow.error.isEmpty, "hflow: Retry sends the delete again")
    // a failed sync is not retried at once (the call window asks every few seconds)
    hs.offline = ["*"]
    await flow.sync()
    expect(!flow.stale() && !flow.error.isEmpty, "hflow: a failed sync counts as an attempt for the 5 minutes")
    hs.offline = []

    // fix round 3
    // 11. a task completed here and then deleted in HubSpot unlinks its item, which stays done
    let d = CommitmentRegister.add(folder: folder, text: "Send the signed order form", project: "orbit", title: "Orbit", call: call) ?? ""
    let dt = await flow.add(item(d)!, properties: props("Send the signed order form"), refs: [CRMRef("deals", "D1")])
    await flow.tickLinked(item(d)!, done: true)?.value
    hs.tasks[dt?.id ?? ""] = nil
    await flow.sync()
    expect(item(d)?.crmTask == "" && item(d)?.crmPortal == "" && item(d)?.status == "done",
           "hflow: a completed item whose task was deleted in HubSpot is unlinked and stays done")

    // 12. a queued delete refused at replay is reported with Retry even when the list read fails
    let l = CommitmentRegister.add(folder: folder, text: "Send the training schedule", project: "orbit", title: "Orbit", call: call) ?? ""
    let lt = await flow.add(item(l)!, properties: props("Send the training schedule"), refs: [CRMRef("deals", "D1")])
    let lid = lt?.id ?? ""
    let ltask = flow.tasks.first { $0.id == lid }!
    hs.fail["DELETE /crm/v3/objects/tasks/" + lid] = 503
    await flow.remove(ltask, commitment: flow.linkedCommitment(ltask))?.value
    hs.fail["DELETE /crm/v3/objects/tasks/" + lid] = 403
    hs.fail["POST /crm/v3/objects/tasks/search"] = 500
    box.notes = []
    await flow.sync()
    var lretry: (() async -> Void)?
    if let n = box.notes.last(where: { if case .replayRefused = $0 { return true }; return false }), case .replayRefused(1, _, let r) = n { lretry = r }
    expect(lretry != nil && flow.pending.isEmpty && item(l)?.status == "open" && !flow.error.isEmpty,
           "hflow: a refusal at replay is reported with Retry when the list read fails")
    hs.fail = [:]
    await lretry?()
    expect(hs.tasks[lid] == nil && item(l)?.status == "dropped", "hflow: that Retry sends the delete")

    // 13. a queued tick HubSpot refuses at replay gives the item back; Retry ticks it again; a newer change stays
    let s = CommitmentRegister.add(folder: folder, text: "Send the API keys checklist", project: "orbit", title: "Orbit", call: call) ?? ""
    let st = await flow.add(item(s)!, properties: props("Send the API keys checklist"), refs: [CRMRef("deals", "D1")])
    let sid = st?.id ?? ""
    hs.fail["PATCH /crm/v3/objects/tasks/" + sid] = 503
    await flow.tickLinked(item(s)!, done: true)?.value
    expect(item(s)?.status == "done" && flow.pending.contains { $0["task"] == sid && $0["op"] == "status" }, "hflow: a 5xx on a tick keeps it queued")
    hs.fail["PATCH /crm/v3/objects/tasks/" + sid] = 403
    box.notes = []
    await flow.sync()
    var sretry: (() async -> Void)?
    if let n = box.notes.last(where: { if case .replayRefused = $0 { return true }; return false }), case .replayRefused(1, _, let r) = n { sretry = r }
    expect(sretry != nil && flow.pending.isEmpty && item(s)?.status == "open" && hs.tasks[sid]?["hs_task_status"] == "NOT_STARTED"
           && flow.shown.contains { $0.id == sid }, "hflow: a tick refused at replay gives the item and the row back")
    hs.fail = [:]
    await sretry?()
    expect(item(s)?.status == "done" && hs.tasks[sid]?["hs_task_status"] == "COMPLETED", "hflow: Retry of a refused tick ticks it again")
    let s2 = CommitmentRegister.add(folder: folder, text: "Send the data export", project: "orbit", title: "Orbit", call: call) ?? ""
    let s2t = await flow.add(item(s2)!, properties: props("Send the data export"), refs: [CRMRef("deals", "D1")])
    let s2id = s2t?.id ?? ""
    hs.fail["PATCH /crm/v3/objects/tasks/" + s2id] = 503
    await flow.tickLinked(item(s2)!, done: true)?.value
    CommitmentRegister.mark(folder: folder, id: s2, op: "drop")
    hs.fail["PATCH /crm/v3/objects/tasks/" + s2id] = 403
    await flow.sync()
    expect(item(s2)?.status == "dropped" && flow.pending.isEmpty, "hflow: a refused queued tick leaves a newer change of the item alone")
    hs.fail = [:]

    // fix round 4
    // 14. a tick and a reopen both queued and both refused at replay: the item and the row stay as HubSpot has them
    let w = CommitmentRegister.add(folder: folder, text: "Send the renewal terms", project: "orbit", title: "Orbit", call: call) ?? ""
    let wt = await flow.add(item(w)!, properties: props("Send the renewal terms"), refs: [CRMRef("deals", "D1")])
    let wid = wt?.id ?? ""
    hs.fail["PATCH /crm/v3/objects/tasks/" + wid] = 503
    await flow.tickLinked(item(w)!, done: true)?.value
    await flow.tickLinked(item(w)!, done: false)?.value
    expect(flow.pending.filter { $0["task"] == wid }.count == 2, "hflow: a reopen after a queued tick waits behind it")
    hs.fail["PATCH /crm/v3/objects/tasks/" + wid] = 403
    await flow.sync()
    expect(flow.pending.isEmpty && item(w)?.status == "open" && hs.tasks[wid]?["hs_task_status"] == "NOT_STARTED"
           && flow.tasks.first { $0.id == wid }?.status == "NOT_STARTED", "hflow: two refused queued writes leave the item as HubSpot has it")
    // a done item reopened while HubSpot is busy, refused at replay: done again, with its date and source
    hs.fail = [:]
    await flow.tickLinked(item(w)!, done: true)?.value
    let wDone = item(w)!
    hs.fail["PATCH /crm/v3/objects/tasks/" + wid] = 503
    await flow.tickLinked(item(w)!, done: false)?.value
    await flow.tickLinked(item(w)!, done: true)?.value
    await flow.tickLinked(item(w)!, done: false)?.value
    hs.fail["PATCH /crm/v3/objects/tasks/" + wid] = 403
    await flow.sync()
    expect(flow.pending.isEmpty && item(w)?.status == "done" && item(w)?.doneAt == wDone.doneAt && item(w)?.doneSource == wDone.doneSource
           && hs.tasks[wid]?["hs_task_status"] == "COMPLETED", "hflow: refused queued writes give a done item back with its date")
    // a reopen queued behind a tick, HubSpot back: both sent in order, HubSpot ends open
    hs.fail["PATCH /crm/v3/objects/tasks/" + wid] = 503
    await flow.tickLinked(item(w)!, done: false)?.value
    await flow.tickLinked(item(w)!, done: true)?.value
    hs.fail = [:]
    await flow.sync()
    expect(flow.pending.isEmpty && item(w)?.status == "done" && hs.tasks[wid]?["hs_task_status"] == "COMPLETED",
           "hflow: queued writes of one task reach HubSpot in order")

    // 15. Delete of a completed item (call page): Undo and a refusal give it back completed, with its date
    let v = CommitmentRegister.add(folder: folder, text: "Send the launch checklist", project: "orbit", title: "Orbit", call: call) ?? ""
    let vt = await flow.add(item(v)!, properties: props("Send the launch checklist"), refs: [CRMRef("deals", "D1")])
    let vid = vt?.id ?? ""
    await flow.tickLinked(item(v)!, done: true)?.value
    let vDone = item(v)!
    box.slow = true
    let vjob = flow.removeLinked(item(v)!)
    expect(item(v)?.status == "dropped", "hflow: Delete of a completed linked item drops it")
    if case .removed(let undo)? = box.notes.last { undo() }
    await vjob?.value
    box.slow = false
    expect(item(v)?.status == "done" && item(v)?.doneAt == vDone.doneAt && item(v)?.doneSource == vDone.doneSource
           && hs.tasks[vid]?["hs_task_status"] == "COMPLETED", "hflow: Undo of Delete gives a completed item back completed")
    hs.fail["DELETE /crm/v3/objects/tasks/" + vid] = 403
    await flow.removeLinked(item(v)!)?.value
    expect(item(v)?.status == "done" && item(v)?.doneAt == vDone.doneAt && hs.tasks[vid] != nil,
           "hflow: a refused Delete gives a completed item back completed")
    hs.fail["DELETE /crm/v3/objects/tasks/" + vid] = 503
    await flow.removeLinked(item(v)!)?.value
    hs.fail["DELETE /crm/v3/objects/tasks/" + vid] = 403
    await flow.sync()
    expect(item(v)?.status == "done" && item(v)?.doneAt == vDone.doneAt, "hflow: a Delete refused at replay gives it back completed")
    hs.fail = [:]

    // 16. more links than one sync reads: the next sync goes on, so a deleted task of a completed item is found
    for n in 0..<89 {       // one call each, so the register does not fold them into one item as duplicates
        let id = CommitmentRegister.add(folder: folder, text: "Follow up with account \(n)", project: "orbit", title: "Orbit",
                                        call: "2026-10-01-\(1000 + n)-orbit.md") ?? ""
        hs.tasks["x\(n)"] = ["hs_task_subject": "Follow up with account \(n)", "hs_task_status": "NOT_STARTED", "hubspot_owner_id": "5"]
        CommitmentRegister.mark(folder: folder, id: id, op: "crm_link", crmTask: "x\(n)", crmPortal: "111")
    }
    expect(CommitmentRegister.load(folder: folder).filter { $0.crmTask.hasPrefix("x") }.count == 89, "hflow: 89 more linked items")
    let z = CommitmentRegister.add(folder: folder, text: "Send the final invoice", project: "orbit", title: "Orbit", call: call) ?? ""
    let zt = await flow.add(item(z)!, properties: props("Send the final invoice"), refs: [CRMRef("deals", "D1")])
    await flow.tickLinked(item(z)!, done: true)?.value
    hs.tasks[zt?.id ?? ""] = nil
    var reads: [Int] = []
    for _ in 0..<2 {
        let m = hs.log.count
        await flow.sync()
        reads.append(hs.log[m...].filter { $0.hasPrefix("GET /crm/v3/objects/tasks/") }.count)
    }
    expect(reads.allSatisfy { $0 <= CRMTaskFlow.linkReads } && item(z)?.crmTask == "" && item(z)?.status == "done",
           "hflow: links past the first 80 are read at the next sync (\(reads))")

    // the cache keeps the portal; a queued write without one is never sent
    let again = makeFlow()
    expect(again.portal == flow.portal && again.tasks.count == flow.tasks.count, "hflow: the cache keeps the portal")
    try? FileManager.default.removeItem(atPath: dir)
}
