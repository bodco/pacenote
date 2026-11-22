import SwiftUI
import LiveTipsCore

// H3 (PB-59): the people of a call in HubSpot. The review rows (unticked: nothing is created without a click), the
// Apply step that runs before the Call of H2 so the Call is associated with them, and the per-object Auto switches.
// The matching and the writes are in LiveTipsCore/CRMPeople.swift; the HubSpot API is never called from a test.

@MainActor
enum CRMPeopleFlow {
    /// The invitees of the call from its calendar event (the PM left out), with the job title from the call's project
    /// (its client team table), as the calendar has none.
    static func participants(file: String) -> [CRMParticipant] {
        guard let r = CallHistory.shared.records.first(where: { $0.file == file }) else { return [] }
        let key = EditLog.seriesKey(r.title)
        guard let ev = CalendarService.shared.events.first(where: { abs($0.start.timeIntervalSince(r.start)) < 1800 && EditLog.seriesKey($0.title) == key }) else { return [] }
        return CRMPeople.withRoles(CRMPeople.participants(names: ev.attendees, emails: ev.emails), people: PyTool.project(of: file).project?.people ?? [])
    }

    static func existingCompany(_ l: CRMLink) -> CRMCompany? { l.companyId.isEmpty ? nil : CRMCompany(id: l.companyId, name: l.companyName, domain: "") }

    /// The match, or the HubSpot error that stopped it (shown in the review, never swallowed). No invitees: nothing.
    /// Only in the link's portal: on another one the error has CRMPortal.mismatchCode and nothing was looked up.
    static func match(file: String, link l: CRMLink, connector c: CRMPeopleConnector, name: String) async -> Result<CRMPeopleMatch, CRMError>? {
        let cfg = SettingsWindow.model.config.crm
        let parts = participants(file: file)
        guard !parts.isEmpty else { return nil }
        do {
            return .success(try await CRMPeople.matchInPortal(l.portalId, participants: parts, existing: existingCompany(l), ownDomains: CRMService.ownDomains, companyName: name,
                                                              hasDeal: !l.dealId.isEmpty && l.dealId != "none", leads: cfg.proposeLeads, connector: c))
        } catch {
            return .failure((error as? CRMError) ?? CRMError(code: 0, message: error.localizedDescription))
        }
    }

    /// The link with the companies and the people the call now has (ids and names only, like the rest of <stem>.crm.json).
    nonisolated static func merge(_ l: CRMLink, company: CRMCompany?, companies: [CRMCompany] = [], contacts: [CRMContact]) -> CRMLink {
        var n = l
        if let co = company, n.companyId.isEmpty { n.companyId = co.id; n.companyName = co.name }
        for co in companies where !co.id.isEmpty && co.id != n.companyId && !n.companyIds.contains(co.id) { n.companyIds.append(co.id) }
        for k in contacts where !n.contactIds.contains(k.id) {
            n.contactIds.append(k.id); n.contactNames.append(k.name.isEmpty ? k.email : k.name)
        }
        return n
    }

    /// Undo of a created company, contact or lead: it leaves the link too, so the next Apply proposes it again.
    static func forget(type: String, id: String, in l: CRMLink) -> CRMLink {
        var n = l
        if type == "companies" && n.companyId == id { n.companyId = ""; n.companyName = "" }
        if type == "companies" { n.companyIds.removeAll { $0 == id } }
        if type == "contacts", let i = n.contactIds.firstIndex(of: id) {
            n.contactIds.remove(at: i); if i < n.contactNames.count { n.contactNames.remove(at: i) }
        }
        return n
    }

    /// Creates the chosen rows and gives a Call logged earlier all its people and companies (CRMPeople.commit, the
    /// PUTs sent again each time, so a failed one is retried), then stores them on the link on disk, keeping a Call id
    /// another path stored meanwhile. Auto and Apply both use it, after checking that `l` names the key's portal. The
    /// stored link is read once in the call's queue turn (under the call key, the one Relink uses) and the save merges
    /// into that portal-checked read: when Relink moved it to another portal meanwhile, it is left alone and the link
    /// comes back unchanged.
    static func commit(_ chosen: [CRMProposal], match m: (company: CRMCompany?, companies: [CRMCompany], found: [CRMContact]), link: CRMLink, base: String,
                       callWritten: Bool, connector pc: CRMPeopleConnector) async -> (CRMLink, CRMPeopleResult) {
        let l = CRMCallLogger.keyed(base: base, link)
        final class Snapshot { var link: CRMLink? }
        let snap = Snapshot()      // stored and save run one after the other in the same turn
        let r = await CRMPeople.commit(chosen, company: m.company, companies: m.companies, found: m.found, portal: l.portalId, callKey: l.callKey,
                                       callWritten: callWritten, connector: pc,
                                       stored: { let d = CRMLink.load(base: base) ?? l; snap.link = d; return CRMStoredLink(callId: d.callId, portalId: d.portalId, linked: CRMCallLogger.refs(d)) },
                                       save: { r in
                                           let cos = m.companies + r.companies
                                           if merge(l, company: r.company, companies: cos, contacts: m.found + r.contacts) != l {
                                               merge(snap.link ?? l, company: r.company, companies: cos, contacts: m.found + r.contacts).save(base: base)
                                           }
                                       })
        let made = ["contacts", "companies", "leads"].map { t in r.created.filter { $0.type == t }.count }
        if !chosen.isEmpty { UsageLog.log("update_crm", ["fields": 0, "contacts": made[0], "companies": made[1], "leads": made[2]]) }
        if r.failure?.scope == "portal" { return (l, r) }       // nothing was stored: the link is another portal's now
        return (merge(l, company: r.company, companies: m.companies + r.companies, contacts: m.found + r.contacts), r)
    }

    /// After the call, without a click, for the objects whose own Auto switch is on. Needs a linked call. Returns the
    /// new link and the failure (an analytics code only: Auto has no window).
    static func auto(file: String, link l: CRMLink, connector c: CRMConnector, name: String, callWritten: Bool) async -> (CRMLink, CRMError?) {
        let cfg = SettingsWindow.model.config.crm
        guard let pc = c as? CRMPeopleConnector, let res = await match(file: file, link: l, connector: pc, name: name) else { return (l, nil) }
        let m: CRMPeopleMatch
        switch res { case .success(let v): m = v; case .failure(let e): return (l, e) }
        // each kind only with its own switch; Lead Auto never creates a contact (the lead waits for it)
        let chosen = CRMPeople.autoChosen(m.proposals, contacts: cfg.autoCreateContacts, companies: cfg.autoCreateCompanies,
                                          leads: cfg.autoCreateLeads && cfg.proposeLeads)
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        let (n, r) = await commit(chosen, match: (m.company, m.companies, m.contacts), link: l, base: base, callWritten: callWritten, connector: pc)
        return (n, r.failure ?? (cfg.autoCreateLeads ? m.problem : nil))
    }

    /// The click path only: a ticked lead's new contact is created with it, the PM's tick covers both (a company only
    /// with its own row). Auto uses CRMPeople.autoChosen instead.
    static func withCompanions(_ chosen: [CRMProposal], from all: [CRMProposal]) -> [CRMProposal] {
        var out = chosen
        for lead in chosen where lead.kind == .lead && lead.contactId.isEmpty {
            if let k = all.first(where: { $0.kind == .contact && $0.email == lead.email }), !out.contains(k) { out.append(k) }
        }
        return out
    }
}

/// The people rows of Update HubSpot: what Pacenote found, and what it can create.
@MainActor
final class CRMPeopleModel: ObservableObject {
    struct Row: Identifiable { var p: CRMProposal; var on = false; var id: String { p.id } }
    @Published var rows: [Row] = []
    @Published var found: [CRMContact] = []
    @Published var company: CRMCompany?
    @Published var companies: [CRMCompany] = []
    @Published var checked = false         // the search ran (so "nothing to add" can be said)
    @Published var problem = ""            // HubSpot's answer when the search, or only the lead lookup, failed
    @Published var otherPortal = false     // the key opens another portal: nothing was looked up, the call needs Relink

    /// Reads HubSpot (no writes), only in the link's portal. Contacts found by name inside the company are added to the
    /// link so the Call gets them. On another portal nothing is kept and Apply adds no people.
    func load(file: String, link l: CRMLink, name: String) async -> CRMLink {
        rows = []; found = []; company = nil; companies = []; checked = false; problem = ""; otherPortal = false
        guard let c = CRMService.shared.connector() as? CRMPeopleConnector else { return l }
        let res = await CRMPeopleFlow.match(file: file, link: l, connector: c, name: name)
        if case .failure(let e)? = res, e.code == CRMPortal.mismatchCode { otherPortal = true; return l }
        checked = true
        guard let res else { return l }
        switch res {
        case .failure(let e): problem = e.message; return l
        case .success(let m):
            problem = m.problem?.message ?? ""
            found = m.contacts; company = m.company; companies = m.companies
            rows = m.proposals.map { Row(p: $0) }
            return CRMPeopleFlow.merge(l, company: m.company, companies: m.companies, contacts: m.contacts)
        }
    }

    /// Creates the ticked rows (a lead with its contact) before the Call; a Call logged earlier that is not written in
    /// this Apply gets the new and the found people. Returns the link, what to undo and the error (a relinked call's
    /// has CRMPortal.mismatchCode).
    func apply(link l: CRMLink, connector c: CRMConnector, base: String, callWritten: Bool) async -> (CRMLink, [(String, String, [String: String])], CRMError?) {
        guard let pc = c as? CRMPeopleConnector, checked else { return (l, [], nil) }
        let ticked = CRMPeopleFlow.withCompanions(rows.filter { $0.on }.map(\.p), from: rows.map(\.p))
        let (n, r) = await CRMPeopleFlow.commit(ticked, match: (company, companies, found), link: l, base: base, callWritten: callWritten, connector: pc)
        let done = ticked.map(\.id)
        rows.removeAll { done.contains($0.id) && r.failure == nil }
        return (n, r.created.map { ($0.type, $0.id, [:]) }, r.failure)
    }
}

struct CRMPeopleSection: View {
    @ObservedObject var model: CRMPeopleModel
    var body: some View {
        if !model.rows.isEmpty || !model.found.isEmpty || !model.problem.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(L("crm.people.title")).font(PN.Font.body.weight(.semibold))
                if !model.problem.isEmpty {
                    Text(L("crm.people.problem", ["error": model.problem]))
                        .font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
                }
                if !model.found.isEmpty {
                    Text(L("crm.people.found", ["names": model.found.map { $0.name.isEmpty ? $0.email : $0.name }.joined(separator: ", ")]))
                        .font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
                }
                ForEach(model.rows.indices, id: \.self) { i in
                    Toggle(isOn: Binding(get: { model.rows[i].on }, set: { model.rows[i].on = $0 })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(L("crm.people." + model.rows[i].p.kind.rawValue))
                            Text(model.rows[i].p.detail).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                        }
                    }.toggleStyle(.pnCheckbox)
                }
            }
        }
    }
}
