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
    var companyIds: [String] = []       // H3: the other companies of the call's people (a mixed call), besides companyId
    var callDealId = ""                 // H4: the deal Pacenote associated the Call with, removed when the call moves to another
    var meetingId = ""                  // H5: the HubSpot Meeting that holds this call's notes (ours, or the one HubSpot's calendar sync made)
    var emailId = ""                    // H5: the HubSpot Email engagement of the follow-up
    var emailOurs = false               // H5: Pacenote created emailId; an email HubSpot or the PM logged is never changed

    init() {}
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        func s(_ k: CodingKeys, _ def: String = "") -> String { (try? c.decodeIfPresent(String.self, forKey: k)) ?? nil ?? def }
        provider = s(.provider, "hubspot"); companyId = s(.companyId); companyName = s(.companyName); dealId = s(.dealId); dealName = s(.dealName)
        contactIds = (try? c.decodeIfPresent([String].self, forKey: .contactIds)) ?? nil ?? []
        contactNames = (try? c.decodeIfPresent([String].self, forKey: .contactNames)) ?? nil ?? []
        callKey = s(.callKey); callId = s(.callId); portalId = s(.portalId); occurrence = s(.occurrence)
        companyIds = (try? c.decodeIfPresent([String].self, forKey: .companyIds)) ?? nil ?? []
        callDealId = s(.callDealId)
        meetingId = s(.meetingId); emailId = s(.emailId)
        emailOurs = (try? c.decodeIfPresent(Bool.self, forKey: .emailOurs)) ?? nil ?? false
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
                fresh.occurrence = link.occurrence                     // occurrence (it is not a portal id). H5: the
                if link.dealId == "none" { fresh.dealId = "none" }     // Meeting and Email ids are not kept either
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
    /// The call key, stored at once, so Auto, an open Update window and its Relink queue under the same one. H3: taken
    /// before any async step, as the people write and Relink use the queue too.
    static func keyed(base: String, _ link: CRMLink) -> CRMLink {
        var l = link
        l.callKey = CRMCallKey.ensure(link.callKey, stored: { CRMLink.load(base: base)?.callKey ?? "" },
                                      store: { k in var d = CRMLink.load(base: base) ?? link; d.callKey = k; d.save(base: base) })
        return l
    }

    static func log(file: String, link: inout CRMLink) -> CRMCallLog? {
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        link = keyed(base: base, link)
        guard let rec = CallHistory.shared.records.first(where: { $0.file == file }) else { return nil }
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

    /// H2: writes the Call through the per-call queue; the stored id, portal and associations are read from
    /// <base>.crm.json when the turn comes, and the new id is stored only if the link still names that portal and call
    /// key when HubSpot answers (fix round 8: a Relink to another portal in between keeps its own ids).
    /// H6: `allowed` is asked when the turn comes; a Retry's `bound` portal (the failed line's) must still be the link's,
    /// else nothing is sent (CRMSyncLog.retryPortal). `attempt` keeps the portal the turn was checked against, for the
    /// sync line. The app writes the Call with its deal move (`writeWithDeal`, the same hooks); this one is the plain write.
    static func write(_ entry: CRMCallLog, base: String, link: CRMLink, connector c: CRMConnector,
                      allowed: @escaping () async -> Bool = { true }, bound: String? = nil,
                      attempt: CRMSyncLog.Attempt = CRMSyncLog.Attempt()) async throws -> CRMCallResult {
        try await CRMCallSync.log(entry, connector: c, fallback: CRMSyncLog.retryTarget(bound: bound, target(link)),
                                  allowed: allowed, checked: attempt.record,
                                  load: { CRMLink.load(base: base).map { CRMSyncLog.retryTarget(bound: bound, target($0)) } },
                                  save: { id in var d = CRMLink.load(base: base) ?? link; d.callId = id; d.save(base: base) })
    }

    nonisolated static func target(_ l: CRMLink) -> CRMCallTarget {
        CRMCallTarget(callId: l.callId, portalId: l.portalId, callKey: l.callKey, associations: refs(l))
    }

    nonisolated static func refs(_ l: CRMLink) -> [CRMRef] {
        var r: [CRMRef] = []
        if !l.dealId.isEmpty && l.dealId != "none" { r.append(CRMRef("deals", l.dealId)) }
        if !l.companyId.isEmpty { r.append(CRMRef("companies", l.companyId)) }
        r += l.companyIds.filter { $0 != l.companyId }.map { CRMRef("companies", $0) }     // H3: a mixed call's other companies
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
        let cfg = SettingsWindow.model.config.crm
        let people = cfg.autoCreateContacts || cfg.autoCreateCompanies || (cfg.autoCreateLeads && cfg.proposeLeads)      // H3: per-object switches
        guard cfg.autoWrites(.calls) || people else { return }
        Task { _ = await run(file, source: "auto") }
    }

    /// Logs the Call now (Auto, or Retry in the sync log) and records the result in the call's sync log (H6). Auto also
    /// creates the people of its H3 switches first, so the Call gets them, each with its own sync line. Both go through
    /// the per-call queue with Apply; Auto asks its Call switch again when its turn comes. Retry passes the failed
    /// line's `portal`: the Call goes out only while that is still the link's and the key's portal.
    /// Returns the error line ("" when written, or when Auto was turned off meanwhile and nothing was sent) and whether
    /// the sync log lines were saved.
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
        guard var l = CRMLink.load(base: base), let c = CRMService.shared.connector() else { return (L("crm.update.noLink"), true) }
        l = keyed(base: base, l)     // H3: before the people write, so it queues with an open window's Apply and Relink
        var saved = true
        if source == "auto" {
            let cfg = SettingsWindow.model.config.crm
            if cfg.autoCreateContacts || cfg.autoCreateCompanies || (cfg.autoCreateLeads && cfg.proposeLeads) {
                // before the Call, so it gets them; nothing is created into another portal's link (the Call below checks
                // the portal again and refuses the same way)
                do {
                    try await CRMPortal.verify(link: l.portalId, connector: c)
                    let a = await CRMPeopleFlow.auto(file: file, link: l, connector: c, name: "", callWritten: cfg.autoWrites(.calls))
                    l = a.link
                    // one line per person made; a create HubSpot refused gets a failed line (a relink meanwhile: Relink, no line)
                    let refused = a.failure.flatMap { $0.scope == "portal" ? nil : $0.message }
                    saved = CRMSyncRecorder.people(a.made, failed: refused, stem: base, source: source, portal: l.portalId, connector: c)
                    if let e = a.failure ?? a.problem { report(e) }     // the Call below is still logged with what was made
                } catch { report(error) }
            }
            guard SettingsWindow.model.config.crm.autoWrites(.calls) else { return ("", saved) }
        }
        guard let entry = log(file: file, link: &l) else { return (L("crm.update.noLink"), saved) }    // log() stores a new call key at once
        let allowed: () async -> Bool = source == "auto" ? { await MainActor.run { SettingsWindow.model.config.crm.autoWrites(.calls) } } : { true }
        // the portal the write was checked against when its turn came, never the link read again after the request:
        // a relink while it was out must not move the line (and its Retry) to the new portal
        let attempt = CRMSyncLog.Attempt()
        let revision = { (id: String) in (c as? HubSpotConnector)?.revision(type: "calls", id: id) ?? "" }
        do {
            // a link from another portal is not written; H4: the Call follows the deal
            let w = try await writeWithDeal(entry, base: base, link: l, connector: c, allowed: allowed, bound: bound, attempt: attempt)
            UsageLog.log("update_crm", ["fields": 0, "call": 1])
            saved = CRMSyncRecorder.call(w.call, deal: w.undoItem, log: entry, stem: base, source: source, revision: revision(w.call.id), portal: attempt.portal) && saved
            return ("", saved)
        } catch let e as CRMError where e.code == CRMCallSync.notAllowedCode && e.scope == "auto" {
            return ("", saved)       // the switch was turned off while the write waited: nothing sent, nothing logged
        } catch let e as CRMError where bound != nil && e.code == CRMPortal.mismatchCode {
            return (L("crm.log.otherPortal"), saved)     // Retry for another portal: nothing sent, the line stays as it is
        } catch {
            // H4: the Call was written, then a step of its deal move failed: what was written gets its lines first
            if let p = error as? CRMCallDeal.Partial {
                saved = CRMSyncRecorder.call(p.move.call, deal: p.move.undoItem, log: entry, stem: base, source: source,
                                             revision: revision(p.move.call.id), portal: attempt.portal) && saved
            }
            let cause = (error as? CRMCallDeal.Partial)?.cause ?? error
            let msg = (cause as? CRMError)?.message ?? cause.localizedDescription
            // the portal this write was meant for, so its Retry goes nowhere else ("" when the turn never came: no Retry target)
            saved = CRMSyncRecorder.failed("calls", stem: base, source: source, error: msg, portal: attempt.portal) && saved
            report(cause)
            return (msg, saved)
        }
    }

    /// Auto has no window: a failure is an analytics error code only (spec section 11).
    static func report(_ error: Error) {
        AnalyticsTracker.shared.error("crm", Analytics.errorCode(message: (error as? CRMError)?.message ?? error.localizedDescription), provider: "hubspot")
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
    @State private var dm = CRMDealModel()      // H4: the deal, its suggestions with evidence and the pipeline's stages
    @State private var dealLoads = CRMLatestOnly()   // H4: a late answer for an earlier pick never replaces the current one
    @State private var busy = false
    @State private var status = ""
    @State private var undo: [CRMUndoItem] = []
    @State private var undoPortal = ""     // H2: the portal the Apply wrote to; Undo checks it before any change
    @State private var undoRevisions: [String: String] = [:]    // H6: "type/id" -> updatedAt of this Apply's write
    @State private var undoUntil: Date? = nil
    @State private var meetingLog: CRMMeetingLog? = nil      // H5 (CRMTimelineFlow.swift)
    @State private var emailLog: CRMEmailLog? = nil
    @State private var meetingPick: [CRMMeetingCandidate] = []   // H5: HubSpot Meetings near the call, not sure to be its own
    @State private var pickPortal = ""      // H5: the portal that offered meetingPick
    @State private var applied = false     // 0.8.7.1: a second Apply wrote the note and the task twice
    @State private var needsRelink = false // H2: the link is from another portal, or from before the portal was kept
    @StateObject private var people = CRMPeopleModel()     // H3: the call's contacts, company and leads

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
                CRMPeopleSection(model: people)
                CRMDealSection(model: $dm, dealName: l.dealId == "none" ? "" : l.dealName, setDeal: pickDeal, retry: loadDeal)
            } else {
                Text(L("crm.update.noLink")).foregroundStyle(PN.C.text3)
            }
            CRMSyncLogView(file: file, stem: base)       // H6: what was written, when, the result; Retry and Undo
            if !meetingPick.isEmpty {       // H5: the PM says which Meeting gets the notes; nothing was written to one yet
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("crm.meeting.pick")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
                    ScrollView {        // every meeting HubSpot returned, closest first: the right one may be any of them
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(meetingPick, id: \.id) { m in
                                Button(L("crm.meeting.attach", ["title": m.title.isEmpty ? L("crm.meeting.untitled") : m.title, "time": CallPageView.fmt("jmm").string(from: m.start)])) { pickMeeting(m.id) }
                                    .buttonStyle(.pnSecondary).disabled(busy)
                            }
                        }
                    }.frame(maxHeight: 150)
                    Button(L("crm.meeting.new")) { pickMeeting(CRMMeetingLog.newTarget) }.buttonStyle(.pnSecondary).disabled(busy)
                }
            }
            Spacer()
            HStack {
                Text(status).font(PN.Font.caption).foregroundStyle(PN.C.text3).lineLimit(2)
                Spacer()
                if let u = undoUntil, u > Date() { Button(L("crm.undo")) { runUndo() }.buttonStyle(.pnSecondary) }
                if needsRelink { Button(L("crm.relink")) { relink() }.buttonStyle(.pnSecondary).disabled(busy).help(L("crm.relink.help")) }
                Button(L("crm.apply")) { apply() }.buttonStyle(.pnPrimary).disabled(busy || applied || plan == nil || link == nil || needsRelink
                                                                                     || !dm.ready(for: link?.dealId ?? ""))   // H4: the deal on screen is the linked one
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
        // H4: deal fields (next step, close date, stage, qualification) are suggested in the deal section with their evidence
        let crmCfg = SettingsWindow.model.config.crm
        var callLog: CRMCallLog? = nil
        if var l = link { callLog = CRMCallLogger.log(file: file, link: &l); link = l }     // keeps the new call key in the view's link
        if let l = link, l.portalId.isEmpty { needsRelink = true; status = L("crm.relink.needed") }
        let transcript = SettingsWindow.model.config.crm.transcriptInNote ? (try? String(contentsOfFile: file, encoding: .utf8)) : nil
        plan = CRMUpdatePlan.build(title: CallPageView.title(file), date: rec.map { CallPageView.fmt("dMMMyyyy").string(from: $0.start) } ?? "",
                                   summary: o.onMe, decisions: o.decisions, nextSteps: o.onMe + o.theirs.map { $0.who + ": " + $0.what },
                                   notionURL: rec?.notionURL ?? "", transcript: transcript, nextStep: "", closeDate: "", stage: nil,
                                   qualification: [:], mapping: [:], followUp: next.isEmpty ? nil : (next, nil),
                                   writeNote: crmCfg.writeNote, writeDeal: crmCfg.writeDeal, writeTask: crmCfg.writeTask,
                                   callLog: callLog, writeCall: crmCfg.writeCall)
        if let l = link {       // H5: the Meeting and the sent follow-up Email as two more rows
            meetingLog = CRMTimelineLogger.meeting(file: file, link: l); emailLog = CRMTimelineLogger.email(file: file, link: l)
            let at = plan?.rows.firstIndex { $0.id == "transcript" } ?? plan?.rows.count ?? 0      // the transcript row stays last
            plan?.rows.insert(contentsOf: CRMTimelineLogger.rows(meeting: meetingLog, email: emailLog), at: at)
        }
        if let only, var p = plan { for i in p.rows.indices { p.rows[i].on = only.contains(p.rows[i].id) }; plan = p }    // H6: a Retry
        if let l = link, !needsRelink {      // H3: reads HubSpot, writes nothing; another portal gets no email and the call needs Relink
            Task {
                var nl = await people.load(file: file, link: l, name: "")
                if let cur = link { nl.dealId = cur.dealId; nl.dealName = cur.dealName }     // H4: a deal picked during the lookup stays
                link = nl
                if people.otherPortal { needsRelink = true; status = L("crm.relink.needed") }
            }
        }
        loadDeal()
    }

    /// H4: reads the deal and builds the suggestions; the "deal" row appears when there is something to write.
    func loadDeal() {
        guard let l = link else { return }
        let token = dealLoads.begin()
        dm = dm.switching(to: l.dealId)       // the old deal and its suggestions leave the screen at once
        Task {
            let m = await CRMDealModel.load(link: l, file: file)
            guard dealLoads.isLatest(token), link?.dealId == l.dealId else { return }    // an answer for an earlier pick
            dm = m
            if m.portalMismatch { needsRelink = true; status = L("crm.relink.needed") }
            guard var p = plan else { return }
            p.rows.removeAll { $0.id == "deal" }
            if !dm.suggestions.isEmpty || dm.myStages.count > 1 {
                let at = p.rows.firstIndex { $0.id == "task" || $0.id == "transcript" } ?? p.rows.endIndex
                // H6: a Retry ticks the deal row only when the deal was the failed line
                p.rows.insert(.init(id: "deal", title: "deal", detail: "", on: only.map { $0.contains("deal") } ?? SettingsWindow.model.config.crm.writeDeal), at: at)
            }
            plan = p
        }
    }

    /// The PM picks another open deal of the company (or No deal); the suggestions are built again for that deal.
    func pickDeal(_ id: String, _ name: String) {
        guard let l = link else { return }
        // from the file, so the Call id and the deal Pacenote associated it with (written by Apply or Auto) are kept
        var d = CRMLink.load(base: base) ?? l
        d.dealId = id; d.dealName = name; d.save(base: base); link = d
        loadDeal()
    }

    func apply() {
        guard var p = plan, var l = link, let c = crm.connector(), dm.ready(for: l.dealId) else { return }
        l = CRMCallLogger.keyed(base: base, l); link = l      // H3: one queue key with Auto and Relink, before any write
        let dealFields = dm.fields(for: l.dealId)      // H4: ticked suggestions and a stage of the deal's own pipeline; evidence is never part of it
        busy = true; status = L("crm.writing")
        Task {
            var done: [CRMUndoItem] = []
            var fields = 0
            var extra = ""
            var sending = ""       // H6: the object whose request is out, so a failed sync line names it and no other
            // H6: HubSpot's updatedAt of each object as Pacenote wrote it, for both Undo paths
            func revisions() -> [String: String] {
                Dictionary(done.map { ($0.type + "/" + $0.id, (c as? HubSpotConnector)?.revision(type: $0.type, id: $0.id) ?? "") }, uniquingKeysWith: { a, _ in a })
            }
            let source = only == nil ? "click" : "retry"
            do {
                // H2: nothing goes to another portal's ids; H6: a Retry only to the failed line's portal, still the link's
                try await CRMPortal.verify(link: retryPortal.map { CRMSyncLog.retryPortal(bound: $0, link: l.portalId) } ?? l.portalId, connector: c)
                // H3: the ticked people first, so the Call (and the note and task) are associated with them
                let callWritten = p.rows.first(where: { $0.id == "call" })?.on == true && p.callLog != nil
                sending = "contacts"
                let (nl, made, problem) = await people.apply(link: l, connector: c, base: base, callWritten: callWritten)
                done += made
                sending = ""
                if nl != l {
                    l = nl; link = nl
                    var again = nl
                    if p.callLog != nil, let entry = CRMCallLogger.log(file: file, link: &again) { p.callLog = entry }     // the participants line
                }
                if let e = problem { sending = "contacts"; throw e.scope == "portal" ? e : CRMError(code: 0, message: e.message) }    // a relink meanwhile: the window offers Relink
                let refs = CRMCallLogger.refs(l)
                if p.rows.first(where: { $0.id == "note" })?.on == true {
                    sending = "notes"
                    let id = try await c.createNote(text: p.note, associations: refs); done.append(CRMUndoItem("notes", id))
                }
                if p.rows.first(where: { $0.id == "call" })?.on == true, let entry = p.callLog {
                    sending = "calls"
                    let w: CRMCallDeal.Move
                    // in turn with Auto and Retry; H6: a Retry writes only to the failed line's portal
                    do { w = try await CRMCallLogger.writeWithDeal(entry, base: base, link: l, connector: c, bound: retryPortal) }
                    catch let e as CRMCallDeal.Partial {     // the old deal could not be taken off: what was written stays in Undo
                        done.append(CRMUndoItem("calls", e.move.call.id, e.move.call.old))
                        if let u = e.move.undoItem { done.append(u) }
                        throw e.cause
                    }
                    let r = w.call
                    done.append(CRMUndoItem("calls", r.id, r.old))       // a new Call: old is empty and Undo deletes it; an update: Undo restores
                    if let u = w.undoItem { done.append(u) }    // H4: Undo takes the new deal off and links the old deal again
                    link?.callId = CRMLink.load(base: base)?.callId ?? r.id      // the id as stored, never one the write declined to store
                    link?.callDealId = CRMLink.load(base: base)?.callDealId ?? ""     // what the write stored: only a deal Pacenote added
                }
                if let w = c as? CRMTimelineWriter {
                    // H5: in turn with Auto, with the stored ids and associations read from <base>.crm.json when the turn comes
                    let on = { (id: String) in p.rows.first(where: { $0.id == id })?.on == true }
                    let t = await CRMTimelineLogger.apply(w, connector: c, meeting: meetingLog, email: emailLog, base: base, link: link ?? l, refs: refs) { on($0) }
                    done += t.undo
                    // H6: the Meeting goes first; once it was written (or waits for a pick), a failure is the Email's
                    let meetingDone = !on("meeting") || meetingLog == nil || !t.pick.isEmpty || t.undo.contains { $0.type == "meetings" }
                    sending = meetingDone && on("email") && emailLog != nil ? "emails" : "meetings"
                    if link?.portalId == t.portal {     // a Relink while this was pending: its ids and pick are the old portal's
                        link?.meetingId = t.link.meetingId; link?.emailId = t.link.emailId; link?.emailOurs = t.link.emailOurs
                        meetingPick = t.pick; pickPortal = t.portal        // the pick block below says what is waiting
                    }
                    if !t.pick.isEmpty { extra = " " + L("crm.meeting.waiting") }
                    if let e = t.error { throw e }
                }
                // H6: deal fields that the mapping sets to Don't write are not a write (no request, no log line)
                let sync = SettingsWindow.model.config.crm.sync
                if p.rows.first(where: { $0.id == "deal" })?.on == true, !l.dealId.isEmpty, l.dealId != "none", !dealFields.isEmpty,
                   !sync.mapped(.deals, dealFields).isEmpty {
                    sending = "deals"
                    let old = try await c.updateDeal(id: l.dealId, fields: dealFields); done.append(CRMUndoItem("deals", l.dealId, old)); fields = dealFields.count
                }
                if p.rows.first(where: { $0.id == "task" })?.on == true, let t = p.task {
                    sending = "tasks"
                    let id = try await c.createTask(subject: t.subject, due: t.due, associations: refs); done.append(CRMUndoItem("tasks", id))
                }
                UsageLog.log("update_crm", ["fields": fields])
                let revs = revisions()
                let saved = CRMSyncRecorder.applied(plan: p, portal: l.portalId, done: done, error: nil, failed: nil, stem: base, source: source, revisions: revs)
                undo = done; undoPortal = l.portalId; undoRevisions = revs
                undoUntil = Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds); applied = true
                status = CRMSyncRecorder.status(L("crm.updated", ["crm": c.providerName]) + extra, saved: saved)
                // a kept batch has its own 8 s
                DispatchQueue.main.asyncAfter(deadline: .now() + CRMSyncLog.applyUndoSeconds + 0.2) { if let u = undoUntil, u <= Date() { undoUntil = nil } }
            } catch {
                let revs = revisions()
                undo = done; undoPortal = l.portalId; undoRevisions = revs
                undoUntil = done.isEmpty ? nil : Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds)
                let cause = (error as? CRMCallDeal.Partial)?.cause ?? error
                let mismatch = (cause as? CRMError)?.code == CRMPortal.mismatchCode
                needsRelink = mismatch && retryPortal == nil
                status = mismatch ? (retryPortal == nil ? L("crm.relink.needed") : L("crm.log.otherPortal")) : ((error as? CRMError)?.message ?? error.localizedDescription)
                // a portal mismatch sent nothing for its row (Relink, not Retry, is the way on, so no failed line), but
                // rows written before it (a note before a Call relinked meanwhile) still get their lines (fix round 9)
                if !mismatch || !done.isEmpty {
                    let saved = CRMSyncRecorder.applied(plan: p, portal: l.portalId, done: done, error: mismatch ? nil : status, failed: mismatch ? nil : sending,
                                                        stem: base, source: source, revisions: revs)
                    status = CRMSyncRecorder.status(status, saved: saved)
                }
                AnalyticsTracker.shared.error("crm", Analytics.errorCode(message: status), provider: "hubspot")   // S5: code only
            }
            busy = false
        }
    }

    /// H5: the PM's pick of the Meeting (an id, or a new one): only the Meeting row is written, in turn with the other
    /// writes of the call, and its Undo joins the one still open (or opens a new one).
    func pickMeeting(_ target: String) {
        guard var m = meetingLog, var l = link, let c = crm.connector(), let w = c as? CRMTimelineWriter else { return }
        l = CRMCallLogger.keyed(base: base, l); link = l      // H3: one queue key with Auto, Apply and Relink
        m.target = target; m.targetPortal = pickPortal       // written only in the portal that offered it
        busy = true; status = L("crm.writing")
        Task {
            let t = await CRMTimelineLogger.apply(w, connector: c, meeting: m, email: nil, base: base, link: l, refs: CRMCallLogger.refs(l)) { $0 == "meeting" }
            // an open Undo of another portal is not joined: each record is undone only in the portal it was written in
            if (undoUntil ?? .distantPast) <= Date() || (!t.portal.isEmpty && undoPortal != t.portal) { undo = []; undoRevisions = [:] }
            if !t.undo.isEmpty { undoPortal = t.portal }
            undo += t.undo
            if link?.portalId == t.portal { link?.meetingId = t.link.meetingId; meetingPick = t.pick; pickPortal = t.portal }   // not after a Relink meanwhile
            // H6: the Meeting's sync line; a refusal before any request (relinked, a pick of another portal) gets none
            let code = (t.error as? CRMError)?.code
            let refusedEarly = code == CRMPortal.mismatchCode || code == CRMTimelineSync.stalePickCode
            var saved = true
            // HubSpot's revision of each record written, as Apply keeps it, so the log can undo a picked Meeting too
            let revs = Dictionary(t.undo.map { ($0.type + "/" + $0.id, (c as? HubSpotConnector)?.revision(type: $0.type, id: $0.id) ?? "") }, uniquingKeysWith: { a, _ in a })
            undoRevisions.merge(revs) { _, b in b }
            if let pl = plan, !t.undo.isEmpty || (t.error != nil && !refusedEarly) {
                let msg = t.error.map { ($0 as? CRMError)?.message ?? $0.localizedDescription }
                saved = CRMSyncRecorder.applied(plan: pl, portal: t.portal.isEmpty ? l.portalId : t.portal, done: t.undo, error: msg,
                                                failed: refusedEarly ? nil : "meetings", stem: base, source: "click", revisions: revs)
            }
            if let e = t.error {
                needsRelink = code == CRMPortal.mismatchCode
                if code == CRMTimelineSync.stalePickCode { meetingPick = []; pickPortal = "" }
                status = needsRelink ? L("crm.relink.needed") : code == CRMTimelineSync.stalePickCode ? L("crm.meeting.stale")
                    : ((e as? CRMError)?.message ?? e.localizedDescription)
            } else { status = L("crm.updated", ["crm": c.providerName]) }
            status = CRMSyncRecorder.status(status, saved: saved)
            if !t.undo.isEmpty {
                undoUntil = Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds)
                DispatchQueue.main.asyncAfter(deadline: .now() + CRMSyncLog.applyUndoSeconds + 0.2) { if let u = undoUntil, u <= Date() { undoUntil = nil } }
            }
            busy = false
        }
    }

    /// H2: links the call again in the portal of the key in use: the company by the attendees' domains of the calendar
    /// occurrence the link was saved for (fix round 7: never the nearest meeting, it may be another customer's), else
    /// by the linked company's name without contacts; the call key is kept, the old ids are not. Fix round 8: in turn
    /// with the call's Call writes (an Auto write waiting for HubSpot finishes first), reading the link when its turn comes.
    /// H3: the call key is made and stored first, so Relink queues with the people write, Auto and Apply of this call.
    /// H5: the same turn as the Meeting and Email writes; the pick and the open Undo name ids of the old portal, so they go.
    func relink() {
        guard let old = link else { return }
        let l = CRMCallLogger.keyed(base: base, old); link = l
        let emails = CRMLinkCarry.relinkEmails(occurrence: l.occurrence,
                                               events: CalendarService.shared.events.map { ([$0.id] + $0.copies, $0.emails) })
        busy = true; status = ""
        let base = base, crm = crm
        Task {
            let (nl, e) = (try? await CRMCallSync.relink(key: l.callKey) {
                await crm.refresh(base: base, emails: emails, name: l.companyName, keep: CRMLink.load(base: base) ?? l)
            }) ?? (nil, L("crm.notConnected"))
            busy = false
            if nl != nil {
                meetingPick = []; pickPortal = ""; undo = []; undoPortal = ""; undoRevisions = [:]; undoUntil = nil
                needsRelink = false; load(); status = L("crm.relink.done")
            } else { status = e }
        }
    }

    /// Undo: notes, tasks and the new Call, contacts, companies and leads are deleted, deal and Call fields get their
    /// old values back. H3: only in the portal Apply wrote to; another portal gets nothing and the batch is kept. H4: a
    /// deal move is reversed (the new deal off the Call, the old one on again) and the managed deal is stored back. H5:
    /// a Meeting or Email gets its old fields back (a new one is deleted), a Meeting whose notes changed in HubSpot after
    /// Apply keeps them and that is said. In turn with the call's writes and Relink: Undo reads a Meeting's notes before
    /// it writes them back, and the stored ids go never between another write's read and save.
    /// H6: each record is changed only inside the Undo window and, when it has one, while HubSpot still has the revision
    /// of this Apply's write (rule 6, CRMSyncLog.undoCheck); each undone or failed record gets a sync log line.
    func runUndo() {
        guard let c = crm.connector(), let deadline = undoUntil, deadline > Date() else { undo = []; undoUntil = nil; return }
        let items = undo, portal = undoPortal, revs = undoRevisions; undo = []; undoUntil = nil
        let base = base, key = CRMCallLogger.keyed(base: base, link ?? CRMLink()).callKey
        let o = CRMUndoOutcomes()
        Task {
            let r = await CRMCallLogger.undo(items, portal: portal, base: base, key: key, connector: c,
                                             check: CRMSyncLog.undoCheck(revision: { revs[$0.type + "/" + $0.id] ?? "" }, deadline: deadline, connector: c),
                                             result: { it, err in o.record(it, err, stem: base, source: "click") })
            if let e = r.error {
                // H3: another portal (or none readable) changed nothing and the batch is kept for another 8 s
                undo = items; undoPortal = portal; undoRevisions = revs; undoUntil = Date().addingTimeInterval(CRMSyncLog.applyUndoSeconds)
                DispatchQueue.main.asyncAfter(deadline: .now() + CRMSyncLog.applyUndoSeconds + 0.2) { if let u = undoUntil, u <= Date() { undoUntil = nil } }
                status = e.code == CRMPortal.mismatchCode ? L("crm.undo.otherPortal", ["crm": c.providerName]) : e.message
                return
            }
            for it in r.undone where it.type == "callDeal" && link?.callId == it.id { link?.callDealId = it.old["managed"] ?? "" }
            for it in r.deleted {
                if it.type == "calls" { link?.callId = "" }     // the next Apply makes a new Call
                if link?.portalId == portal {       // H5: a deleted one is made again by the next Apply
                    if it.type == "meetings", link?.meetingId == it.id { link?.meetingId = "" }
                    if it.type == "emails", link?.emailId == it.id { link?.emailId = ""; link?.emailOurs = false }
                }
                if ["contacts", "companies", "leads"].contains(it.type), let l = link { link = CRMPeopleFlow.forget(type: it.type, id: it.id, in: l) }
            }
            // 0.8.7.1: a failed undo is said, not hidden behind "Undone"; H5: notes kept in a Meeting are said too;
            // H6: a record refused (window passed, changed in HubSpot) says why
            let line = r.failed > 0
                ? (o.refused.map { CRMSyncRecorder.undoError($0) + (r.failed > 1 ? " " + L("crm.undoFailed", ["n": "\(r.failed)", "crm": c.providerName]) : "") }
                   ?? L("crm.undoFailed", ["n": "\(r.failed)", "crm": c.providerName]))
                : r.kept > 0 ? L("crm.undoKept", ["n": "\(r.kept)", "crm": c.providerName]) : L("crm.undone")
            status = CRMSyncRecorder.status(line, saved: o.saved)
            if r.failed == 0 { applied = false }
        }
    }
}

/// H6: what one Undo heard from its records: the first refusal (window passed, changed in HubSpot; no log line, nothing
/// was changed) and whether every undo line was saved. A record H5 kept (CRMUndoConflict) gets no line either.
@MainActor
final class CRMUndoOutcomes {
    var refused: Error? = nil
    var saved = true
    func record(_ it: CRMUndoItem, _ err: Error?, stem: String, source: String) {
        if err is CRMUndoConflict { return }
        let code = (err as? CRMError)?.code
        if code == CRMSyncLog.expiredCode || code == CRMSyncLog.changedCode { refused = refused ?? err; return }
        saved = CRMSyncRecorder.undo(it.type, it.id, stem: stem, error: err.map { CRMSyncRecorder.undoError($0) }, source: source) && saved
    }
}

extension CRMCallLogger {
    /// H6: the one Undo of written records for both paths (Apply's 8 seconds in Update HubSpot, the sync log's day): in
    /// the call's turn with its writes and Relink, through CRMUndo.run with `check` before each record; then, only in the
    /// link of the portal Undo ran in, the stored ids of what was deleted go (H3, H5) and a reversed deal move stores its
    /// managed deal back (H4), without overwriting an id another write stored.
    static func undo(_ items: [CRMUndoItem], portal: String, base: String, key: String, connector c: CRMConnector,
                     check: @escaping (CRMUndoItem) async throws -> Void, result: @escaping (CRMUndoItem, Error?) -> Void) async
        -> (undone: [CRMUndoItem], failed: Int, kept: Int, deleted: [CRMUndoItem], error: CRMError?) {
        (try? await CRMCallSync.relink(key: key) { () async -> (undone: [CRMUndoItem], failed: Int, kept: Int, deleted: [CRMUndoItem], error: CRMError?) in
            let r = await CRMUndo.run(items, portal: portal, connector: c, check: check, result: result)
            guard r.error == nil else { return r }
            for it in r.undone where it.type == "callDeal" {      // H4: the deal Pacenote managed before the move
                if var d = CRMLink.load(base: base), d.portalId == portal, d.callId == it.id { d.callDealId = it.old["managed"] ?? ""; d.save(base: base) }
            }
            for it in r.deleted {
                if ["calls", "meetings", "emails"].contains(it.type) { CRMTimelineLogger.forget(type: it.type, id: it.id, portal: portal, base: base) }
                if ["contacts", "companies", "leads"].contains(it.type), let d = CRMLink.load(base: base), d.portalId == portal {     // H3
                    await CRMPeopleFlow.forget(type: it.type, id: it.id, in: d).save(base: base)
                }
            }
            return r
        }) ?? ([], 0, 0, [], CRMError(code: 0, message: L("crm.notConnected")))
    }
}
extension CRMDeal: Identifiable {}
