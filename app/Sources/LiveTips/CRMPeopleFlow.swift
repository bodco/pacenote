import SwiftUI
import LiveTipsCore

// H3 (PB-59): the people of a call in HubSpot. The review rows (unticked: nothing is created without a click), the
// Apply step that runs before the Call of H2 so the Call is associated with them, and the per-object Auto switches.
// The matching and the writes are in LiveTipsCore/CRMPeople.swift; the HubSpot API is never called from a test.

@MainActor
enum CRMPeopleFlow {
    /// The invitees of the call from its calendar event (the PM left out), with a job title when the call page has one.
    static func participants(file: String) -> [CRMParticipant] {
        guard let r = CallHistory.shared.records.first(where: { $0.file == file }) else { return [] }
        let key = EditLog.seriesKey(r.title)
        guard let ev = CalendarService.shared.events.first(where: { abs($0.start.timeIntervalSince(r.start)) < 1800 && EditLog.seriesKey($0.title) == key }) else { return [] }
        let base = file.hasSuffix(".md") ? String(file.dropLast(3)) : file
        let sales = (try? Data(contentsOf: URL(fileURLWithPath: CallLayout.sidecar(base, ".sales.json")))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        var titles: [String: String] = [:]      // stakeholders[].title, when the PM or the notes gave one (the buying "role" is not a job title)
        for s in (sales["stakeholders"] as? [[String: Any]]) ?? [] {
            if let n = s["name"] as? String, let t = s["title"] as? String, !t.isEmpty { titles[CRMPeople.nameKey(n)] = t }
        }
        return CRMPeople.participants(names: ev.attendees, emails: ev.emails, titles: titles)
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

    /// After the call, without a click, for the objects whose own Auto switch is on. Needs a linked call. Returns the new link.
    static func auto(file: String, link l: CRMLink, connector c: CRMConnector, name: String) async -> CRMLink {
        let cfg = SettingsWindow.model.config.crm
        guard let pc = c as? CRMPeopleConnector, let m = await match(file: file, link: l, connector: pc, name: name) else { return l }
        var chosen = m.proposals.filter {
            switch $0.kind {
            case .company: return cfg.autoCreateCompanies
            case .contact: return cfg.autoCreateContacts
            case .lead: return cfg.autoCreateLeads && cfg.proposeLeads
            }
        }
        for lead in chosen where lead.kind == .lead {              // a lead's contact is created or reused with it
            if let k = m.proposals.first(where: { $0.kind == .contact && $0.email == lead.email }), !chosen.contains(k) { chosen.append(k) }
        }
        let r = await CRMPeople.apply(chosen, company: m.company, connector: pc)
        if let f = r.failure { AnalyticsTracker.shared.error("crm", Analytics.errorCode(message: f.message), provider: "hubspot") }
        if !chosen.isEmpty { UsageLog.log("update_crm", ["fields": 0, "contacts": r.created.filter { $0.type == "contacts" }.count, "companies": r.created.filter { $0.type == "companies" }.count, "leads": r.created.filter { $0.type == "leads" }.count]) }
        let n = merge(l, company: r.company, contacts: m.contacts + r.contacts)
        if n != l { n.save(base: file.hasSuffix(".md") ? String(file.dropLast(3)) : file) }
        return n
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

    /// Creates the ticked rows (a lead only with its contact) before the Call. Returns the link, what to undo and an error line.
    func apply(link l: CRMLink, connector c: CRMConnector, base: String) async -> (CRMLink, [(String, String, [String: String])], String) {
        guard let pc = c as? CRMPeopleConnector else { return (l, [], "") }
        var ticked = rows.filter { $0.on }.map(\.p)
        for lead in ticked where lead.kind == .lead {               // a lead's contact is created or reused with it
            if let k = rows.first(where: { $0.p.kind == .contact && $0.p.email == lead.email })?.p, !ticked.contains(k) { ticked.append(k) }
        }
        guard !ticked.isEmpty else { return (l, [], "") }
        let r = await CRMPeople.apply(ticked, company: company, connector: pc)
        let n = CRMPeopleFlow.merge(l, company: r.company, contacts: r.contacts)
        if n != l { n.save(base: base) }
        let done = ticked.map(\.id)
        rows.removeAll { done.contains($0.id) && r.failure == nil }
        UsageLog.log("update_crm", ["fields": 0, "contacts": r.created.filter { $0.type == "contacts" }.count, "companies": r.created.filter { $0.type == "companies" }.count, "leads": r.created.filter { $0.type == "leads" }.count])
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
