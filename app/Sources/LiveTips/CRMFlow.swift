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
    var portalId = ""                   // H2: the portal these ids belong to; writes need it to match the key's portal
    var occurrence = ""                 // H2 (fix round 6): the calendar occurrence Refresh linked it for; carried only to that call

    init() {}
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        func s(_ k: CodingKeys, _ def: String = "") -> String { (try? c.decodeIfPresent(String.self, forKey: k)) ?? nil ?? def }
        provider = s(.provider, "hubspot"); companyId = s(.companyId); companyName = s(.companyName); dealId = s(.dealId); dealName = s(.dealName)
        contactIds = (try? c.decodeIfPresent([String].self, forKey: .contactIds)) ?? nil ?? []
        contactNames = (try? c.decodeIfPresent([String].self, forKey: .contactNames)) ?? nil ?? []
        callKey = s(.callKey); callId = s(.callId); portalId = s(.portalId); occurrence = s(.occurrence)
    }

    static func url(base: String) -> URL { URL(fileURLWithPath: CallLayout.sidecar(base, ".crm.json")) }
    static func load(base: String) -> CRMLink? { CRMLinkCarry.stored(base: base).flatMap { try? JSONDecoder().decode(CRMLink.self, from: $0) } }
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
    /// occurrence (Start call's Refresh): the calendar occurrence the link is saved for, "" for a call without an event;
    /// nil (Relink on a recorded call) leaves it as it was.
    func refresh(base: String, emails: [String], name: String, keep: CRMLink?, occurrence: String? = nil) async -> (CRMLink?, String) {
        guard let c = connector() else { return (nil, L("crm.notConnected")) }
        do {
            var link = keep ?? CRMLink()
            let portal = try await c.portalId()
            if link.portalId != portal {     // H2: ids from another portal (or an unknown one) are not kept; the call key
                var fresh = CRMLink(); fresh.callKey = link.callKey    // and the PM's "No deal" are, and the calendar
                fresh.occurrence = link.occurrence                     // occurrence (it is not a portal id)
                if link.dealId == "none" { fresh.dealId = "none" }
                link = fresh
            }
            link.portalId = portal
            if let o = occurrence { link.occurrence = o }
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
        if link.callKey.isEmpty {
            // the key is stored at once, so Auto and an open Update window use the same one
            var disk = CRMLink.load(base: base) ?? link
            if disk.callKey.isEmpty { disk.callKey = UUID().uuidString.lowercased(); disk.save(base: base) }
            link.callKey = disk.callKey
        }
        let o = CallOutcome.read(transcript: file) ?? CallOutcome()
        let m = CallNotes.meeting(file)      // the call page's Summary: In short and Recap
        let sales = (try? Data(contentsOf: URL(fileURLWithPath: CallLayout.sidecar(base, ".sales.json")))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let next = (sales["next_step"] as? String) ?? ((sales["next_step"] as? [String: Any])?["what"] as? String) ?? ""
        let s = CRMCallLog.sections(tldr: m?.tldr ?? "", recap: m?.recap ?? [], onMe: o.onMe, theirs: o.theirs.map { $0.who + ": " + $0.what }, salesNext: next)
        let ms = Int(((rec.end ?? rec.start.addingTimeInterval((rec.minutes ?? 0) * 60)).timeIntervalSince(rec.start)) * 1000)
        return CRMCallLog(key: link.callKey, title: CallPageView.title(file), start: rec.start, durationMs: ms,
                          direction: SettingsWindow.model.config.crm.callDirection,
                          body: CRMCallLog.body(summary: s.summary, nextSteps: s.nextSteps, participants: link.contactNames, link: rec.notionURL ?? "", key: link.callKey))
    }

    /// H2: writes the Call through the per-call queue; the stored id and portal are read from <base>.crm.json when the
    /// turn comes and the new id is stored before the next turn. H6: `allowed` is asked when the turn comes; a Retry's
    /// `bound` portal (the failed line's) must still be the link's, else nothing is sent (CRMSyncLog.retryPortal).
    /// `attempt` keeps the portal the turn was checked against, for the sync line (PB-62 fix round 4).
    static func write(_ entry: CRMCallLog, base: String, link: CRMLink, connector c: CRMConnector,
                      allowed: @escaping () async -> Bool = { true }, bound: String? = nil,
                      attempt: CRMSyncLog.Attempt = CRMSyncLog.Attempt()) async throws -> CRMCallResult {
        try await CRMCallSync.log(entry, connector: c, associations: refs(link), allowed: allowed,
                                  load: attempt.load { let d = CRMLink.load(base: base) ?? link
                                          return (d.callId, bound.map { CRMSyncLog.retryPortal(bound: $0, link: d.portalId) } ?? d.portalId) },
                                  save: { id in var d = CRMLink.load(base: base) ?? link; d.callId = id; d.save(base: base) })
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
    /// Both go through the per-call queue with Apply; Auto asks its switch again when its turn comes. Retry passes the
    /// failed line's `portal`: the Call goes out only while that is still the link's and the key's portal.
    /// Returns the error line ("" when written, or when Auto was turned off meanwhile and nothing was sent) and whether
    /// the sync log line was saved.
    @MainActor static func run(_ file: String, source: String, portal bound: String? = nil) async -> (line: String, saved: Bool) {
        let r = await send(file, source: source, bound: bound)
        // Auto's result and its Retry live in the sync log, and Auto has no window: an unsaved line gets a notice
        if source == "auto" && !r.saved {
            let result = r.line.isEmpty ? L("crm.updated", ["crm": "HubSpot"]) : r.line
            NoticeCenter.shared.post(L("crm.log.autoNotice", ["call": CallPageView.title(file), "result": CRMSyncRecorder.status(result, saved: false)]), level: .warning)
        }
        return r
    }

    @MainActor private static func send(_ file: String, source: String, bound: String?) async -> (line: String, saved: Bool) {
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        guard var l = CRMLink.load(base: base), let c = CRMService.shared.connector(),
              let entry = log(file: file, link: &l) else { return (L("crm.update.noLink"), true) }    // log() stores a new call key at once
        let allowed: () async -> Bool = source == "auto" ? { await MainActor.run { SettingsWindow.model.config.crm.autoWrites(.calls) } } : { true }
        // the portal the write was checked against when its turn came, never the link read again after the request:
        // a relink while it was out must not move the line (and its Retry) to the new portal
        let attempt = CRMSyncLog.Attempt()
        do {
            let r = try await write(entry, base: base, link: l, connector: c, allowed: allowed, bound: bound, attempt: attempt)    // a link from another portal is not written
            UsageLog.log("update_crm", ["fields": 0, "call": 1])
            let saved = CRMSyncRecorder.call(r, log: entry, stem: base, source: source, revision: (c as? HubSpotConnector)?.revision(type: "calls", id: r.id) ?? "",
                                             portal: attempt.portal)
            return ("", saved)
        } catch let e as CRMError where e.code == CRMCallSync.notAllowedCode && e.scope == "auto" {
            return ("", true)       // the switch was turned off while the write waited: nothing sent, nothing logged
        } catch let e as CRMError where bound != nil && e.code == CRMPortal.mismatchCode {
            return (L("crm.log.otherPortal"), true)     // Retry for another portal: nothing sent, the line stays as it is
        } catch {
            let msg = (error as? CRMError)?.message ?? error.localizedDescription
            // the portal this write was meant for, so its Retry goes nowhere else ("" when the turn never came: no Retry target)
            let saved = CRMSyncRecorder.failed("calls", stem: base, source: source, error: msg, portal: attempt.portal)
            AnalyticsTracker.shared.error("crm", Analytics.errorCode(message: msg), provider: "hubspot")
            return (msg, saved)
        }
    }
}

extension StartCallModel {
    /// The calendar occurrence this window is for (event id + start), sent as crm_event; "" without an event.
    var crmOccurrence: String { calEventID ?? "" }
    /// The preparation was chosen by hand in this window (sent as crm_chosen): its link is the PM's own choice. Not the
    /// one Start call filled by title and time (fix round 7).
    var crmChosen: Bool { CRMLinkCarry.chosenByHand(picked: pickedPrep, current: chosenPrep, path: chosenPrepPath) }
    /// The link at crmBase goes to the call (fix round 6): chosen by hand, or Refresh saved it for this occurrence. The
    /// planned preparation is found by title and time, which never proves it is this call's, so its link alone is not used.
    var crmLinkVerified: Bool { crmBase.map { CRMLinkCarry.verified(base: $0, occurrence: crmOccurrence, chosen: crmChosen) } ?? false }

    /// The preparation the CRM row's Refresh saves the HubSpot link with, sent with the call as crm_base: the call gets
    /// exactly the link this window showed (fix round 5), never one found by title.
    var crmBase: String? {
        // the same preparation Start call copies to the call (CallEngine.adoptPrep): the one chosen by hand, else the planned one
        if let b = CRMLinkCarry.prepBase(chosen: chosenPrepPath, planned: prepBase) { return b }
        if let p = PrepDocWindow.path(for: self), p.hasSuffix(".brief.md") { return String(p.dropLast(".brief.md".count)) }
        // 0.8.7.1: before Prepare, the snapshot goes where Prepare will write the brief (PrepRunner's base), so the first
        // brief already reads it; Refresh was greyed out until a brief existed
        guard let root = root, !root.isEmpty else { return nil }
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd-HHmm"
        let day = DateFormatter(); day.dateFormat = "yyyy-MM-dd"
        let slug = StartCallModel.slugify(effectiveTitle)
        // the same folder PrepRunner writes the brief to (call folders)
        return (CallLayout.folder(live: (root as NSString).appendingPathComponent("live"), date: day.string(from: callDate), slug: slug) as NSString)
            .appendingPathComponent("prep-\(f.string(from: callDate))-\(slug)")
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

    var base: String? { model.crmBase }
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
            .onAppear { link = shown }
            // fix round 5: another preparation (round 6: or another calendar event) picked: show its own link, the
            // previous one's deals and error go
            .onChange(of: identity) { link = shown; deals = []; err = "" }
        }
    }

    /// What decides the row's link: the preparation, the calendar occurrence and whether the prep was chosen by hand.
    var identity: String { (base ?? "") + "|" + model.crmOccurrence + "|" + (model.crmChosen ? "1" : "0") }
    /// fix round 6: only a link Start call will carry is shown (the same rule), so the row never shows another call's link.
    var shown: CRMLink? { model.crmLinkVerified ? base.flatMap { CRMLink.load(base: $0) } : nil }

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
        let id = identity, occ = model.crmOccurrence
        // the link kept is the one saved with this base, not the one on screen (it may be another preparation's); one not
        // verified for this call (fix round 6: another occurrence's, or one without an occurrence) is not kept
        let keep = model.crmLinkVerified ? CRMLink.load(base: b) : nil
        Task {
            let (l, e) = await crm.refresh(base: b, emails: emails, name: name, keep: keep, occurrence: occ)
            guard identity == id else { busy = false; return }      // the preparation or event changed while HubSpot answered
            link = l; err = e; busy = false
            // the snapshot still helps the preparation, but a link no call identity backs is not offered as the call's
            if let l = l, !model.crmLinkVerified {
                err = L("crm.unverified", ["company": l.companyName.isEmpty ? L("crm.noCompany") : l.companyName]); deals = []; return
            }
            if let co = l?.companyId, !co.isEmpty, let c = crm.connector() {
                let open = ((try? await c.deals(companyId: co)) ?? []).filter { $0.open }
                if identity == id { deals = open }      // not the deals of a company picked before a switch
            }
        }
    }

    func setDeal(_ id: String, _ name: String) {
        guard let b = base else { return }
        var l = CRMLink.load(base: b) ?? CRMLink(); l.dealId = id; l.dealName = name; l.save(base: b); link = l
        refresh()
    }
}

// MARK: - Update <CRM> (I5)

@MainActor
enum CRMUpdateWindow {
    static var window: NSWindow?
    static func show(file: String, only: Set<String>? = nil, portal: String? = nil) {
        let w = window ?? NSWindow(contentViewController: NSHostingController(rootView: AnyView(EmptyView())))
        let hc = NSHostingController(rootView: Localized { CRMUpdateView(file: file, only: only, retryPortal: portal) })
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
    var retryPortal: String? = nil       // H6: and writes only to the portal of the failed line (CRMSyncLog.retryPortal)
    @ObservedObject var crm = CRMService.shared
    @State private var plan: CRMUpdatePlan? = nil
    @State private var link: CRMLink? = nil
    @State private var stage = ""
    @State private var busy = false
    @State private var status = ""
    @State private var undo: [CRMUndoItem] = []
    @State private var undoPortal = ""     // H2: the portal the Apply wrote to; Undo checks it before any change
    @State private var undoRevisions: [String: String] = [:]    // H6: "type/id" -> updatedAt of this Apply's write
    @State private var undoUntil: Date? = nil
    @State private var applied = false     // 0.8.7.1: a second Apply wrote the note and the task twice
    @State private var needsRelink = false // H2: the link is from another portal, or from before the portal was kept

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
                if needsRelink { Button(L("crm.relink")) { relink() }.buttonStyle(.pnSecondary).disabled(busy).help(L("crm.relink.help")) }
                Button(L("crm.apply")) { apply() }.buttonStyle(.pnPrimary).disabled(busy || applied || plan == nil || link == nil || needsRelink)
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
        if let l = link, l.portalId.isEmpty { needsRelink = true; status = L("crm.relink.needed") }
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
            var done: [CRMUndoItem] = []
            var failed: String? = nil      // H6: the object whose request was out when the error came
            // H6: HubSpot's updatedAt of each object as Pacenote wrote it, for both Undo paths
            func revisions() -> [String: String] {
                Dictionary(done.map { ($0.type + "/" + $0.id, (c as? HubSpotConnector)?.revision(type: $0.type, id: $0.id) ?? "") }, uniquingKeysWith: { a, _ in a })
            }
            let source = only == nil ? "click" : "retry"
            do {
                // H2: nothing goes to another portal's ids; H6: a Retry only to the failed line's portal, still the link's
                try await CRMPortal.verify(link: retryPortal.map { CRMSyncLog.retryPortal(bound: $0, link: l.portalId) } ?? l.portalId, connector: c)
                let o = await CRMApply.run(plan: p, dealId: l.dealId, refs: CRMCallLogger.refs(l), connector: c, sync: SettingsWindow.model.config.crm.sync) { entry in
                    let r = try await CRMCallLogger.write(entry, base: base, link: l, connector: c, bound: retryPortal)    // in turn with Auto and Retry
                    link?.callId = r.id
                    return r
                }
                done = o.done; failed = o.failed
                if let e = o.error { throw e }
                UsageLog.log("update_crm", ["fields": o.fields])
                let revs = revisions()
                let saved = CRMSyncRecorder.applied(plan: p, portal: l.portalId, done: done, error: nil, failed: nil, stem: base, source: source, revisions: revs)
                undo = done; undoPortal = l.portalId; undoRevisions = revs
                undoUntil = Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds); applied = true
                status = CRMSyncRecorder.status(L("crm.updated", ["crm": c.providerName]), saved: saved)
                DispatchQueue.main.asyncAfter(deadline: .now() + CRMSyncLog.applyUndoSeconds + 0.2) { undoUntil = nil }
            } catch {
                let revs = revisions()
                undo = done; undoPortal = l.portalId; undoRevisions = revs
                undoUntil = done.isEmpty ? nil : Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds)
                let mismatch = (error as? CRMError)?.code == CRMPortal.mismatchCode
                needsRelink = mismatch && retryPortal == nil
                status = mismatch ? (retryPortal == nil ? L("crm.relink.needed") : L("crm.log.otherPortal")) :((error as? CRMError)?.message ?? error.localizedDescription)
                if !mismatch {     // a portal mismatch wrote nothing; Relink, not Retry, is the way on
                    let saved = CRMSyncRecorder.applied(plan: p, portal: l.portalId, done: done, error: status, failed: failed, stem: base, source: source, revisions: revs)
                    status = CRMSyncRecorder.status(status, saved: saved)
                }
                AnalyticsTracker.shared.error("crm", Analytics.errorCode(message: status), provider: "hubspot")   // S5: code only
            }
            busy = false
        }
    }

    /// H2: links the call again in the portal of the key in use: the company by the attendees' domains of the calendar
    /// occurrence the link was saved for (fix round 7: never the nearest meeting, it may be another customer's), else
    /// by the linked company's name without contacts; the call key is kept, the old ids are not.
    func relink() {
        guard let l = link else { return }
        let emails = CRMLinkCarry.relinkEmails(occurrence: l.occurrence,
                                               events: CalendarService.shared.events.map { ([$0.id] + $0.copies, $0.emails) })
        busy = true; status = ""
        Task {
            let (nl, e) = await crm.refresh(base: base, emails: emails, name: l.companyName, keep: l)
            busy = false
            if nl != nil { needsRelink = false; load(); status = L("crm.relink.done") } else { status = e }
        }
    }

    /// Undo: notes, tasks and new Calls are deleted, deal fields and updated Calls get their old values back; nothing
    /// is sent when the key now belongs to another portal than the one the Apply wrote to. H6: each object is changed
    /// only inside the Undo window and while HubSpot still has the revision of this Apply's write (rule 6).
    func runUndo() {
        guard let c = crm.connector(), let deadline = undoUntil, deadline > Date() else { undo = []; undoUntil = nil; return }
        let items = undo, portal = undoPortal, revs = undoRevisions
        undo = []; undoUntil = nil
        Task {
            do {
                var refused: Error? = nil      // left as it is: window passed or changed in HubSpot (no log line)
                var saved = true
                let failed = try await CRMCallSync.undo(items, portal: portal, connector: c,
                                                        check: { it in try await CRMSyncLog.guardUndo(type: it.type, id: it.id, revision: revs[it.type + "/" + it.id] ?? "",
                                                                                                      deadline: deadline, connector: c) },
                                                        result: { it, err in
                    let code = (err as? CRMError)?.code
                    if code == CRMSyncLog.expiredCode || code == CRMSyncLog.changedCode { refused = refused ?? err; return }
                    saved = CRMSyncRecorder.undo(it.type, it.id, stem: base, error: err.map { CRMSyncRecorder.undoError($0) }) && saved
                }) { it in
                    guard it.type == "calls" else { return }       // the next Apply makes a new Call
                    if var d = CRMLink.load(base: base), d.callId == it.id { d.callId = ""; d.save(base: base) }
                    link?.callId = ""
                }
                let line = failed == 0 ? L("crm.undone")
                    : refused.map { CRMSyncRecorder.undoError($0) + (failed > 1 ? " " + L("crm.undoFailed", ["n": "\(failed)", "crm": c.providerName]) : "") }
                    ?? L("crm.undoFailed", ["n": "\(failed)", "crm": c.providerName])     // 0.8.7.1: said, not hidden
                status = CRMSyncRecorder.status(line, saved: saved)
                if failed == 0 { applied = false }
            } catch {
                let mismatch = (error as? CRMError)?.code == CRMPortal.mismatchCode
                status = mismatch ? L("crm.undo.portal", ["crm": c.providerName]) : ((error as? CRMError)?.message ?? error.localizedDescription)
            }
        }
    }
}
extension CRMDeal: Identifiable {}
