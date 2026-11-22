import AppKit
import SwiftUI
import LiveTipsCore

// Pacenote 0.8.7 (I3-I5, rules/crm.yaml): the call's link to the CRM, the snapshot <base>.crm.md that the preparation
// and the engine read, and Update <CRM> after the call: a review with checkboxes, Apply, Undo for 8 seconds.
// Nothing is written to the CRM without the PM's click.

/// <base>.crm.json: which company, deal and contacts this call is linked to (ids only, no key, no text).
struct CRMLink: Codable, Equatable {
    var provider = "hubspot"
    var companyId = "", companyName = ""
    var dealId = "", dealName = ""
    var contactIds: [String] = []
    var contactNames: [String] = []     // H2: the participants named in the Call engagement
    var callKey = ""                    // H2: the Pacenote call id, made once and kept with the call
    var callId = ""                     // H2: the HubSpot Call engagement of this call, so a re-run updates it

    init() {}
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        func s(_ k: CodingKeys, _ def: String = "") -> String { (try? c.decodeIfPresent(String.self, forKey: k)) ?? nil ?? def }
        provider = s(.provider, "hubspot"); companyId = s(.companyId); companyName = s(.companyName); dealId = s(.dealId); dealName = s(.dealName)
        contactIds = (try? c.decodeIfPresent([String].self, forKey: .contactIds)) ?? nil ?? []
        contactNames = (try? c.decodeIfPresent([String].self, forKey: .contactNames)) ?? nil ?? []
        callKey = s(.callKey); callId = s(.callId)
    }

    static func url(base: String) -> URL { URL(fileURLWithPath: CallLayout.sidecar(base, ".crm.json")) }
    static func load(base: String) -> CRMLink? { (try? Data(contentsOf: url(base: base))).flatMap { try? JSONDecoder().decode(CRMLink.self, from: $0) } }
    func save(base: String) { if let d = try? JSONEncoder().encode(self) { try? d.write(to: Self.url(base: base), options: .atomic) } }
}

extension CRMService {
    /// The PM's own domains (from the name aliases' emails is not known): the domains of the calendar accounts.
    static var ownDomains: Set<String> {
        Set(CalendarService.shared.calendars.compactMap { c -> String? in
            let s = c.source.title.lowercased(); guard let at = s.lastIndex(of: "@") else { return nil }; return String(s[s.index(after: at)...]) })
    }

    /// 0.8.7 (I6.2): the user's own emails (calendar accounts), to pick the CRM owner.
    static var ownEmails: [String] {
        CalendarService.shared.calendars.map { $0.source.title.lowercased() }.filter { $0.contains("@") }
    }

    /// Finds the company (attendee domains, else the name), its freshest open deal and the contacts; keeps a link the
    /// PM chose. Writes <base>.crm.md and <base>.crm.json. Returns the link or an error line.
    func refresh(base: String, emails: [String], name: String, keep: CRMLink?) async -> (CRMLink?, String) {
        guard let c = connector() else { return (nil, L("crm.notConnected")) }
        do {
            var link = keep ?? CRMLink()
            var company: CRMCompany? = nil
            if !link.companyId.isEmpty { company = CRMCompany(id: link.companyId, name: link.companyName, domain: "") }
            if company == nil, SettingsWindow.model.config.crm.matchByDomain {   // 0.8.7 (I6.2): the toggle in Matching
                for d in CRMSnapshot.clientDomains(emails: emails, ownDomains: Self.ownDomains) {
                    if let co = try await c.findCompany(domain: d, name: "") { company = co; break }
                }
            }
            if company == nil, !name.isEmpty { company = try await c.findCompany(domain: "", name: name) }
            let contacts = try await c.findContacts(emails: emails)
            var deal: CRMDeal? = nil
            if link.dealId == "none" { deal = nil }
            else if !link.dealId.isEmpty { deal = try await c.deal(id: link.dealId) }
            else if let co = company { deal = try await c.deals(companyId: co.id).filter { $0.open }.max { $0.updated < $1.updated } }
            if let d = deal, d.stageName.isEmpty, let names = try? await c.pipelines() { deal?.stageName = names[d.stage] ?? "" }
            var refs: [CRMRef] = []
            if let d = deal { refs.append(CRMRef("deals", d.id)) } else if let co = company { refs.append(CRMRef("companies", co.id)) }
            let acts = (try? await c.recentActivity(refs: refs, limit: 10)) ?? []
            link.companyId = company?.id ?? ""; link.companyName = company?.name ?? ""
            if link.dealId != "none" { link.dealId = deal?.id ?? ""; link.dealName = deal?.name ?? "" }
            link.contactIds = contacts.map(\.id)
            link.contactNames = contacts.map { $0.name.isEmpty ? $0.email : $0.name }
            try? FileManager.default.createDirectory(atPath: (CallLayout.sidecar(base, ".crm.md") as NSString).deletingLastPathComponent, withIntermediateDirectories: true)   // 0.8.7.1; call folders: .work/
            let md = CRMSnapshot.render(provider: c.providerName, company: company, deal: deal, contacts: contacts, activity: acts, taken: Date())
            try? md.replacingOccurrences(of: "\u{2014}", with: "-").write(toFile: CallLayout.sidecar(base, ".crm.md"), atomically: true, encoding: .utf8)
            link.save(base: base)
            return (link, "")
        } catch {
            return (nil, (error as? CRMError)?.message ?? error.localizedDescription)
        }
    }
}

/// H2: the Call engagement of a finished call, from the call page as edited. Nothing from the transcript.
@MainActor
enum CRMCallLogger {
    static func log(file: String, link: inout CRMLink) -> CRMCallLog? {
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        guard let rec = CallHistory.shared.records.first(where: { $0.file == file }) else { return nil }
        if link.callKey.isEmpty { link.callKey = UUID().uuidString.lowercased() }
        let o = CallOutcome.read(transcript: file) ?? CallOutcome()
        let sales = (try? Data(contentsOf: URL(fileURLWithPath: CallLayout.sidecar(base, ".sales.json")))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let next = (sales["next_step"] as? String) ?? ((sales["next_step"] as? [String: Any])?["what"] as? String) ?? ""
        var steps = o.onMe + o.theirs.map { $0.who + ": " + $0.what }
        if steps.isEmpty && !next.isEmpty { steps = [next] }
        let ms = Int(((rec.end ?? rec.start.addingTimeInterval((rec.minutes ?? 0) * 60)).timeIntervalSince(rec.start)) * 1000)
        return CRMCallLog(key: link.callKey, title: CallPageView.title(file), start: rec.start, durationMs: ms,
                          direction: SettingsWindow.model.config.crm.callDirection,
                          body: CRMCallLog.body(summary: o.onMe, nextSteps: steps, participants: link.contactNames, link: rec.notionURL ?? "", key: link.callKey))
    }

    static func refs(_ l: CRMLink) -> [CRMRef] {
        var r: [CRMRef] = []
        if !l.dealId.isEmpty && l.dealId != "none" { r.append(CRMRef("deals", l.dealId)) }
        if !l.companyId.isEmpty { r.append(CRMRef("companies", l.companyId)) }
        return r + l.contactIds.map { CRMRef("contacts", $0) }
    }

    /// Auto switch (Settings > Integrations > HubSpot, off by default): the call is logged after it ends, without a click.
    /// Only the Call engagement; notes, deal fields and tasks still go through Update HubSpot.
    @MainActor static func start() {
        NotificationCenter.default.addObserver(forName: CallEngine.callEnded, object: nil, queue: .main) { n in
            guard let file = n.userInfo?["file"] as? String, !file.isEmpty else { return }
            // the call page's notes are written in the background after the call
            DispatchQueue.main.asyncAfter(deadline: .now() + 150) { MainActor.assumeIsolated { auto(file) } }
        }
    }

    @MainActor static func auto(_ file: String) {
        guard SettingsWindow.model.config.crm.autoWrites(.calls) else { return }
        Task { _ = await run(file, source: "auto") }
    }

    /// Logs the Call now (Auto, or Retry in the sync log) and records the result in the call's sync log (H6).
    /// Returns the error line, "" when written.
    @MainActor static func run(_ file: String, source: String) async -> String {
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        guard var l = CRMLink.load(base: base), let c = CRMService.shared.connector(),
              let entry = log(file: file, link: &l) else { return L("crm.update.noLink") }
        do {
            let r = try await c.upsertCall(entry, knownId: l.callId, associations: refs(l))
            l.callId = r.id; l.save(base: base)
            UsageLog.log("update_crm", ["fields": 0, "call": 1])
            CRMSyncRecorder.call(r, log: entry, stem: base, source: source)
            return ""
        } catch {
            let msg = (error as? CRMError)?.message ?? error.localizedDescription
            l.save(base: base)      // keeps the new call key, so a retry updates the same Call
            CRMSyncRecorder.failed("calls", stem: base, source: source, error: msg)
            AnalyticsTracker.shared.error("crm", Analytics.errorCode(message: msg), provider: "hubspot")
            return msg
        }
    }
}

/// G7: "CRM: Northwind · Deal: Orbit renewal" in Start call, with Refresh and a menu to change the deal or say No deal.
struct CRMCallRow: View {
    @ObservedObject var model: StartCallModel
    @ObservedObject var crm = CRMService.shared
    @State private var link: CRMLink? = nil
    @State private var deals: [CRMDeal] = []
    @State private var busy = false
    @State private var err = ""

    var base: String? {
        if let p = PrepDocWindow.path(for: model) { return String(p.dropLast(9)) }
        if let b = model.prepBase { return b }
        // 0.8.7.1: before Prepare, the snapshot goes where Prepare will write the brief (PrepRunner's base), so the first
        // brief already reads it; Refresh was greyed out until a brief existed
        guard let root = model.root, !root.isEmpty else { return nil }
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd-HHmm"
        let day = DateFormatter(); day.dateFormat = "yyyy-MM-dd"
        let slug = StartCallModel.slugify(model.effectiveTitle)
        // the same folder PrepRunner writes the brief to (call folders)
        return (CallLayout.folder(live: (root as NSString).appendingPathComponent("live"), date: day.string(from: model.callDate), slug: slug) as NSString)
            .appendingPathComponent("prep-\(f.string(from: model.callDate))-\(slug)")
    }
    var emails: [String] { model.calEventID.flatMap { CalendarService.shared.event($0)?.emails } ?? [] }

    var body: some View {
        if crm.connected {
            HStack(spacing: 8) {
                Image(systemName: "briefcase").foregroundStyle(PN.C.text3)
                Text(line).font(PN.Font.caption).foregroundStyle(PN.C.text2).lineLimit(1)
                if !deals.isEmpty {
                    Menu {
                        ForEach(deals) { d in Button(d.name) { setDeal(d.id, d.name) } }
                        Divider()
                        Button(L("crm.noDeal")) { setDeal("none", "") }
                    } label: { Image(systemName: "chevron.down") }.menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                        .accessibilityLabel(L("crm.changeDeal"))
                }
                Spacer()
                if busy { PNSpinner(size: 16) }
                Button(L("crm.refresh")) { refresh() }.buttonStyle(.pnTertiary).disabled(busy || base == nil)
                    .help(base == nil ? L("crm.refresh.needBase") : L("crm.refresh.help"))
            }
            .onAppear { if let b = base { link = CRMLink.load(base: b) } }
        }
    }

    var line: String {
        if !err.isEmpty { return err }
        guard let l = link else { return L("crm.notLinked") }
        let co = l.companyName.isEmpty ? L("crm.noCompany") : l.companyName
        let deal = l.dealId == "none" ? L("crm.noDeal") : (l.dealName.isEmpty ? L("crm.noDeal") : l.dealName)
        return L("crm.line", ["company": co, "deal": deal])
    }

    func refresh() {
        guard let b = base else { return }
        busy = true; err = ""
        let name = model.project?.crmCompanyName.isEmpty == false ? model.project!.crmCompanyName : (model.project?.name ?? "")
        Task {
            let (l, e) = await crm.refresh(base: b, emails: emails, name: name, keep: link)
            link = l; err = e; busy = false
            if let co = l?.companyId, !co.isEmpty, let c = crm.connector() { deals = ((try? await c.deals(companyId: co)) ?? []).filter { $0.open } }
        }
    }

    func setDeal(_ id: String, _ name: String) {
        guard let b = base else { return }
        var l = link ?? CRMLink(); l.dealId = id; l.dealName = name; l.save(base: b); link = l
        refresh()
    }
}

// MARK: - Update <CRM> (I5)

@MainActor
enum CRMUpdateWindow {
    static var window: NSWindow?
    static func show(file: String, only: Set<String>? = nil) {
        let w = window ?? NSWindow(contentViewController: NSHostingController(rootView: AnyView(EmptyView())))
        let hc = NSHostingController(rootView: Localized { CRMUpdateView(file: file, only: only) })
        hc.sizingOptions = []      // 0.8.7.1: the window grew to 1572 pt and Apply was below the screen
        w.contentViewController = hc
        w.tabbingMode = .disallowed
        w.title = L("crm.update.title", ["crm": "HubSpot"])
        w.setContentSize(NSSize(width: 560, height: 520))
        w.isReleasedWhenClosed = false
        window = w
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

struct CRMUpdateView: View {
    let file: String
    var only: Set<String>? = nil         // H6: Retry in the sync log ticks only the rows that failed
    @ObservedObject var crm = CRMService.shared
    @State private var plan: CRMUpdatePlan? = nil
    @State private var link: CRMLink? = nil
    @State private var stage = ""
    @State private var busy = false
    @State private var status = ""
    @State private var undo: [(String, String, [String: String])] = []    // (type, id, old fields)
    @State private var undoUntil: Date? = nil
    @State private var applied = false     // 0.8.7.1: a second Apply wrote the note and the task twice

    var base: String { file.hasSuffix(".md") ? String(file.dropLast(3)) : file }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L("crm.update.sub")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            if let l = link, let p = plan {
                Text(L("crm.line", ["company": l.companyName.isEmpty ? L("crm.noCompany") : l.companyName, "deal": l.dealName.isEmpty ? L("crm.noDeal") : l.dealName]))
                    .font(PN.Font.body.weight(.semibold))
                ForEach(p.rows.indices, id: \.self) { i in
                    Toggle(isOn: Binding(get: { plan?.rows[i].on ?? false }, set: { plan?.rows[i].on = $0 })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(L("crm.row." + p.rows[i].id))
                            if !p.rows[i].detail.isEmpty { Text(p.rows[i].detail).font(PN.Font.caption).foregroundStyle(PN.C.text3) }
                        }
                    }
                    .toggleStyle(.pnCheckbox)
                    .disabled(p.rows[i].id == "deal" && (l.dealId.isEmpty || l.dealId == "none"))
                }
                TextField(L("crm.stage"), text: $stage, prompt: Text(L("crm.stage.prompt"))).help(L("crm.stage.help"))
            } else {
                Text(L("crm.update.noLink")).foregroundStyle(PN.C.text3)
            }
            CRMSyncLogView(file: file, stem: base)       // H6: what was written, when, the result; Retry and Undo
            Spacer()
            HStack {
                Text(status).font(PN.Font.caption).foregroundStyle(PN.C.text3).lineLimit(2)
                Spacer()
                if let u = undoUntil, u > Date() { Button(L("crm.undo")) { runUndo() }.buttonStyle(.pnSecondary) }
                Button(L("crm.apply")) { apply() }.buttonStyle(.pnPrimary).disabled(busy || applied || plan == nil || link == nil)
            }
        }
        .padding(18)
        .frame(minWidth: 460, minHeight: 360)
        .onAppear { load() }
    }

    func load() {
        link = CRMLink.load(base: base) ?? CRMLink.load(base: file)
        let o = CallOutcome.read(transcript: file) ?? CallOutcome()     // the call page as edited (E)
        let rec = CallHistory.shared.records.first { $0.file == file }
        let sales = (try? Data(contentsOf: URL(fileURLWithPath: CallLayout.sidecar(base, ".sales.json")))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let next = (sales["next_step"] as? String) ?? ((sales["next_step"] as? [String: Any])?["what"] as? String) ?? ""
        // 0.8.7 (I6.5): the qualification of the call page (criterion -> what) through the mapping of its method
        var qual: [String: String] = [:]
        for c in (sales["criteria"] as? [[String: Any]]) ?? [] {
            if let k = c["criterion"] as? String, let w = c["what"] as? String, !w.isEmpty { qual[k] = w }
        }
        let crmCfg = SettingsWindow.model.config.crm
        let mapping = crmCfg.qualification[(sales["method"] as? String) ?? ""] ?? [:]
        var callLog: CRMCallLog? = nil
        if var l = link { callLog = CRMCallLogger.log(file: file, link: &l); link = l }     // keeps the new call key in the view's link
        let transcript = SettingsWindow.model.config.crm.transcriptInNote ? (try? String(contentsOfFile: file, encoding: .utf8)) : nil
        plan = CRMUpdatePlan.build(title: CallPageView.title(file), date: rec.map { CallPageView.fmt("dMMMyyyy").string(from: $0.start) } ?? "",
                                   summary: o.onMe, decisions: o.decisions, nextSteps: o.onMe + o.theirs.map { $0.who + ": " + $0.what },
                                   notionURL: rec?.notionURL ?? "", transcript: transcript, nextStep: next, closeDate: "", stage: nil,
                                   qualification: qual, mapping: mapping, followUp: next.isEmpty ? nil : (next, nil),
                                   writeNote: crmCfg.writeNote, writeDeal: crmCfg.writeDeal, writeTask: crmCfg.writeTask,
                                   callLog: callLog, writeCall: crmCfg.writeCall)
        if let only, var p = plan { for i in p.rows.indices { p.rows[i].on = only.contains(p.rows[i].id) }; plan = p }
    }

    func apply() {
        guard var p = plan, let l = link, let c = crm.connector() else { return }
        if !stage.isEmpty { p.dealFields["dealstage"] = stage }
        busy = true; status = L("crm.writing")
        Task {
            var done: [(String, String, [String: String])] = []
            var fields = 0
            do {
                let refs = CRMCallLogger.refs(l)
                if p.rows.first(where: { $0.id == "note" })?.on == true {
                    let id = try await c.createNote(text: p.note, associations: refs); done.append(("notes", id, [:]))
                }
                if p.rows.first(where: { $0.id == "call" })?.on == true, let entry = p.callLog {
                    let r = try await c.upsertCall(entry, knownId: l.callId, associations: refs)
                    done.append(("calls", r.id, r.old))       // a new Call: old is empty and Undo deletes it; an update: Undo restores
                    var nl = link ?? l; nl.callId = r.id; nl.save(base: base); link = nl
                }
                if p.rows.first(where: { $0.id == "deal" })?.on == true, !l.dealId.isEmpty, l.dealId != "none", !p.dealFields.isEmpty {
                    let old = try await c.updateDeal(id: l.dealId, fields: p.dealFields); done.append(("deals", l.dealId, old)); fields = p.dealFields.count
                }
                if p.rows.first(where: { $0.id == "task" })?.on == true, let t = p.task {
                    let id = try await c.createTask(subject: t.subject, due: t.due, associations: refs); done.append(("tasks", id, [:]))
                }
                UsageLog.log("update_crm", ["fields": fields])
                CRMSyncRecorder.applied(plan: p, link: l, done: done, error: nil, stem: base, source: only == nil ? "click" : "retry")
                undo = done; undoUntil = Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds); applied = true
                status = L("crm.updated", ["crm": c.providerName])
                DispatchQueue.main.asyncAfter(deadline: .now() + 8.2) { undoUntil = nil }
            } catch {
                undo = done; undoUntil = done.isEmpty ? nil : Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds)
                status = (error as? CRMError)?.message ?? error.localizedDescription
                CRMSyncRecorder.applied(plan: p, link: l, done: done, error: status, stem: base, source: only == nil ? "click" : "retry")
                AnalyticsTracker.shared.error("crm", Analytics.errorCode(message: status), provider: "hubspot")   // S5: code only
            }
            busy = false
        }
    }

    /// Undo: notes and tasks are deleted, deal fields get their old values back.
    func runUndo() {
        guard let c = crm.connector() else { return }
        let items = undo; undo = []; undoUntil = nil
        Task {
            var failed = 0      // 0.8.7.1: a failed undo is said, not hidden behind "Undone"
            for (type, id, old) in items.reversed() {
                do {
                    if type == "deals" { _ = try await c.updateDeal(id: id, fields: old) }
                    else if type == "calls" && !old.isEmpty { try await c.restoreCall(id: id, fields: old) }
                    else {
                        try await c.delete(type: type, id: id)
                        if type == "calls", var l = link { l.callId = ""; l.save(base: base); link = l }   // the next Apply makes a new Call
                    }
                    CRMSyncRecorder.undo(type, id, stem: base, error: nil)
                } catch { failed += 1; CRMSyncRecorder.undo(type, id, stem: base, error: (error as? CRMError)?.message ?? error.localizedDescription) }
            }
            status = failed == 0 ? L("crm.undone") : L("crm.undoFailed", ["n": "\(failed)", "crm": c.providerName])
            if failed == 0 { applied = false }
        }
    }
}
extension CRMDeal: Identifiable {}
