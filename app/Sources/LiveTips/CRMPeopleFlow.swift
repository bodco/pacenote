import SwiftUI
import LiveTipsCore

// H3 (PB-59): the people of a call in HubSpot. The review rows (unticked: nothing is created without a click), the
// Apply step that runs before the Call of H2 so the Call is associated with them, and the per-object Auto switches.
// The matching and the writes are in LiveTipsCore/CRMPeople.swift; the HubSpot API is never called from a test.

@MainActor
enum CRMPeopleFlow {
    /// The invitees of the call from its calendar event (the PM left out). No job title: see CRMPeople.participants.
    static func participants(file: String) -> [CRMParticipant] {
        guard let r = CallHistory.shared.records.first(where: { $0.file == file }) else { return [] }
        let key = EditLog.seriesKey(r.title)
        guard let ev = CalendarService.shared.events.first(where: { abs($0.start.timeIntervalSince(r.start)) < 1800 && EditLog.seriesKey($0.title) == key }) else { return [] }
        return CRMPeople.participants(names: ev.attendees, emails: ev.emails)
    }

    static func existingCompany(_ l: CRMLink) -> CRMCompany? { l.companyId.isEmpty ? nil : CRMCompany(id: l.companyId, name: l.companyName, domain: "") }

    static func match(file: String, link l: CRMLink, connector c: CRMPeopleConnector, name: String) async -> CRMPeopleMatch? {
        let cfg = SettingsWindow.model.config.crm
        let parts = participants(file: file)
        guard !parts.isEmpty else { return nil }
        return try? await CRMPeople.match(participants: parts, existing: existingCompany(l), ownDomains: CRMService.ownDomains, companyName: name,
                                          hasDeal: !l.dealId.isEmpty && l.dealId != "none", leads: cfg.proposeLeads, connector: c)
    }

    /// The link with the company and the people the call now has (ids and names only, like the rest of <stem>.crm.json).
    static func merge(_ l: CRMLink, company: CRMCompany?, contacts: [CRMContact]) -> CRMLink {
        var n = l
        if let co = company, n.companyId.isEmpty { n.companyId = co.id; n.companyName = co.name }
        for k in contacts where !n.contactIds.contains(k.id) {
            n.contactIds.append(k.id); n.contactNames.append(k.name.isEmpty ? k.email : k.name)
        }
        return n
    }

    /// Undo of a created company, contact or lead: it leaves the link too, so the next Apply proposes it again.
    static func forget(type: String, id: String, in l: CRMLink) -> CRMLink {
        var n = l
        if type == "companies" && n.companyId == id { n.companyId = ""; n.companyName = "" }
        if type == "contacts", let i = n.contactIds.firstIndex(of: id) {
            n.contactIds.remove(at: i); if i < n.contactNames.count { n.contactNames.remove(at: i) }
        }
        return n
    }

    /// Creates the chosen rows and gives a Call logged earlier its new people (CRMPeople.commit), then stores the
    /// people on the link on disk, keeping a Call id another path stored meanwhile. Auto and Apply both use it.
    static func commit(_ chosen: [CRMProposal], match m: (company: CRMCompany?, found: [CRMContact]), link l: CRMLink, base: String,
                       callWritten: Bool, connector pc: CRMPeopleConnector) async -> (CRMLink, CRMPeopleResult) {
        let before = CRMCallLogger.refs(CRMLink.load(base: base) ?? l)      // what the stored Call was written with
        let r = await CRMPeople.commit(chosen, company: m.company, found: m.found, before: before, callKey: l.callKey, callWritten: callWritten,
                                       connector: pc, callId: { CRMLink.load(base: base)?.callId ?? l.callId })
        let n = merge(l, company: r.company, contacts: m.found + r.contacts)
        if n != l { merge(CRMLink.load(base: base) ?? l, company: r.company, contacts: m.found + r.contacts).save(base: base) }
        let made = ["contacts", "companies", "leads"].map { t in r.created.filter { $0.type == t }.count }
        if !chosen.isEmpty { UsageLog.log("update_crm", ["fields": 0, "contacts": made[0], "companies": made[1], "leads": made[2]]) }
        return (n, r)
    }

    /// After the call, without a click, for the objects whose own Auto switch is on. Needs a linked call. Returns the
    /// new link and the failure (an analytics code only: Auto has no window).
    static func auto(file: String, link l: CRMLink, connector c: CRMConnector, name: String, callWritten: Bool) async -> (CRMLink, CRMError?) {
        let cfg = SettingsWindow.model.config.crm
        guard let pc = c as? CRMPeopleConnector, let m = await match(file: file, link: l, connector: pc, name: name) else { return (l, nil) }
        var chosen = m.proposals.filter {
            switch $0.kind {
            case .company: return cfg.autoCreateCompanies
            case .contact: return cfg.autoCreateContacts
            case .lead: return cfg.autoCreateLeads && cfg.proposeLeads
            }
        }
        chosen = CRMPeopleFlow.withCompanions(chosen, from: m.proposals)
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        let (n, r) = await commit(chosen, match: (m.company, m.contacts), link: l, base: base, callWritten: callWritten, connector: pc)
        return (n, r.failure)
    }

    /// A lead's new contact is created with it (a company only with its own row or switch).
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
    @Published var checked = false         // the search ran (so "nothing to add" can be said)

    /// Reads HubSpot (no writes). Contacts found by name inside the company are added to the link so the Call gets them.
    func load(file: String, link l: CRMLink, name: String) async -> CRMLink {
        guard let c = CRMService.shared.connector() as? CRMPeopleConnector else { return l }
        let m = await CRMPeopleFlow.match(file: file, link: l, connector: c, name: name)
        checked = true
        guard let m else { return l }
        found = m.contacts; company = m.company
        rows = m.proposals.map { Row(p: $0) }
        return CRMPeopleFlow.merge(l, company: m.company, contacts: m.contacts)
    }

    /// Creates the ticked rows (a lead with its contact) before the Call; a Call logged earlier that is not written in
    /// this Apply gets the new and the found people. Returns the link, what to undo and an error line.
    func apply(link l: CRMLink, connector c: CRMConnector, base: String, callWritten: Bool) async -> (CRMLink, [(String, String, [String: String])], String) {
        guard let pc = c as? CRMPeopleConnector, checked else { return (l, [], "") }
        let ticked = CRMPeopleFlow.withCompanions(rows.filter { $0.on }.map(\.p), from: rows.map(\.p))
        let (n, r) = await CRMPeopleFlow.commit(ticked, match: (company, found), link: l, base: base, callWritten: callWritten, connector: pc)
        let done = ticked.map(\.id)
        rows.removeAll { done.contains($0.id) && r.failure == nil }
        return (n, r.created.map { ($0.type, $0.id, [:]) }, r.failure?.message ?? "")
    }
}

struct CRMPeopleSection: View {
    @ObservedObject var model: CRMPeopleModel
    var body: some View {
        if !model.rows.isEmpty || !model.found.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(L("crm.people.title")).font(PN.Font.body.weight(.semibold))
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
