import Foundation

/// H3 (PB-59, docs/specs/hubspot-crm-integration.md sections 2, 6 and 7): find or create the contacts, the companies and
/// the leads of a call. Match first (email, then name inside the participant's own company), propose what is missing
/// with the fields filled in, create only after the PM's click or with the per-object Auto switch, never a duplicate,
/// and give the new ids to the Call of H2. Own file on purpose: the CRMConnector protocol does not change.
public struct CRMParticipant: Equatable {
    public var name: String
    public var email: String
    public var role: String          // the job title, from the project's client team table (CRMPeople.withRoles), else empty
    public init(name: String, email: String = "", role: String = "") { self.name = name; self.email = email; self.role = role }
}

public enum CRMProposalKind: String { case company, contact, lead }

/// One thing Pacenote would create, with every field the PM sees in the review row.
public struct CRMProposal: Equatable, Identifiable {
    public var id: String            // "company.<n>", "contact.<n>", "lead.<n>"
    public var kind: CRMProposalKind
    public var name: String          // the person's or company's name; the lead's name
    public var email = ""
    public var role = ""
    public var company = ""          // the company's name (contact and lead rows)
    public var domain = ""           // the company's domain; a contact or lead with no companyId gets the company made for it
    public var companyId = ""        // the participant's company already in HubSpot
    public var contactId = ""        // a lead for a contact already in HubSpot
    public init(id: String, kind: CRMProposalKind, name: String, email: String = "", role: String = "", company: String = "", domain: String = "",
                companyId: String = "", contactId: String = "") {
        self.id = id; self.kind = kind; self.name = name; self.email = email; self.role = role; self.company = company; self.domain = domain
        self.companyId = companyId; self.contactId = contactId
    }
    /// The row's second line: what will be written.
    public var detail: String {
        switch kind {
        case .company: return [name, domain].filter { !$0.isEmpty }.joined(separator: " · ")
        case .contact: return [name, email, role, company].filter { !$0.isEmpty }.joined(separator: " · ")
        case .lead: return [name, company].filter { !$0.isEmpty }.joined(separator: " · ")
        }
    }
}

public struct CRMPeopleMatch: Equatable {
    public var company: CRMCompany?          // the call's company (the link's): linked, else the first domain's, else by name
    public var contacts: [CRMContact]        // found in HubSpot: by email, else by name inside the participant's company
    public var proposals: [CRMProposal]
    public var companies: [CRMCompany] = []  // every participant domain's company already in HubSpot (a mixed call has several)
    public var problem: CRMError?            // the lead lookup failed (a portal without the Leads scopes): no lead rows, the rest stays
}

/// What was created (and so can be undone) and what already existed (reused, never undone).
public struct CRMPeopleResult {
    public var created: [(type: String, id: String)] = []
    public var company: CRMCompany?
    public var companies: [CRMCompany] = []  // every company made or reused in this run, one per domain
    public var contacts: [CRMContact] = []
    public var reused = 0
    public var failure: CRMError?
    public var changed: Bool { !created.isEmpty || reused > 0 }
}

/// What the call's stored link (<stem>.crm.json) holds when the write's turn comes: the Call id, the portal and the refs.
public struct CRMStoredLink: Equatable {
    public var callId: String, portalId: String, linked: [CRMRef]
    public init(callId: String, portalId: String, linked: [CRMRef] = []) { self.callId = callId; self.portalId = portalId; self.linked = linked }
}

public struct CRMCreated: Equatable { public var id: String; public var created: Bool
    public init(id: String, created: Bool) { self.id = id; self.created = created } }

/// The write side of H3. HubSpotConnector implements it; the app uses it when `connector() as? CRMPeopleConnector`.
public protocol CRMPeopleConnector: CRMConnector {
    /// Every contact associated with the company (all pages).
    func companyContacts(companyId: String) async throws -> [CRMContact]
    /// The contact's lead, if it has one.
    func leadId(contactId: String) async throws -> String?
    func createCompany(name: String, domain: String) async throws -> CRMCreated
    func createContact(name: String, email: String, role: String, companyId: String) async throws -> CRMCreated
    func createLead(name: String, contactId: String, companyId: String) async throws -> CRMCreated
    func associateCall(callId: String, refs: [CRMRef]) async throws
}

public enum CRMPeople {
    public static let freeMail: Set<String> = ["gmail.com", "outlook.com", "icloud.com", "yahoo.com", "hotmail.com"]

    public static func normEmail(_ e: String) -> String { e.lowercased().trimmingCharacters(in: .whitespacesAndNewlines) }
    public static func domain(_ email: String) -> String {
        guard let at = email.lastIndex(of: "@") else { return "" }
        return String(email[email.index(after: at)...]).lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
    }
    /// "Emily  Clark" and "emily clark" are the same person; accents and punctuation do not count.
    public static func nameKey(_ s: String) -> String {
        s.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: nil)
            .components(separatedBy: CharacterSet.letters.inverted).filter { !$0.isEmpty }.joined(separator: " ")
    }
    /// "emily.clark@orbit.example" -> "Emily Clark" (the same rule the calendar uses when an invitee has no name).
    public static func derivedName(_ email: String) -> String {
        let local = email.components(separatedBy: "@").first ?? ""
        return local.split(whereSeparator: { ".-_".contains($0) }).map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
    }
    /// first name = every word but the last; last name = the last word; one word is a first name.
    public static func splitName(_ n: String) -> (first: String, last: String) {
        let w = n.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard w.count > 1 else { return (w.first ?? "", "") }
        return (w.dropLast().joined(separator: " "), w.last ?? "")
    }
    /// "orbit.example" -> "Orbit" (only when the PM gave the call's company no name).
    public static func companyName(fromDomain d: String) -> String {
        let label = d.split(separator: ".").first.map(String.init) ?? d
        return label.prefix(1).uppercased() + label.dropFirst()
    }

    /// Calendar names and emails (the PM left out) become participants. The two lists are paired by position only when
    /// they have the same length; otherwise each email gets the name derived from it. The calendar has no job title:
    /// the role comes from the project (withRoles).
    public static func participants(names: [String], emails: [String]) -> [CRMParticipant] {
        var out: [CRMParticipant] = [], seen = Set<String>()
        let paired = names.count == emails.count
        for (i, raw) in emails.enumerated() {
            let e = normEmail(raw)
            guard e.contains("@"), seen.insert(e).inserted else { continue }
            out.append(CRMParticipant(name: paired ? names[i] : derivedName(e), email: e))
        }
        if !paired {      // names without an email still help the name match inside the company
            for n in names where !n.isEmpty && !out.contains(where: { nameKey($0.name) == nameKey(n) }) { out.append(CRMParticipant(name: n)) }
        }
        return out
    }

    /// The role of each participant from the project's people ("Team - Client" in the project config, the same list
    /// the call page names the other side from), matched by the full name. Only the client side: the PM's own team is
    /// never created in HubSpot. A role already set is kept; no match leaves it empty (never guessed).
    public static func withRoles(_ parts: [CRMParticipant], people: [Project.Person]) -> [CRMParticipant] {
        var roleOf: [String: String] = [:]
        for p in people where p.side != "team" && !p.role.isEmpty {
            let k = nameKey(p.name)
            if k.split(separator: " ").count >= 2 && roleOf[k] == nil { roleOf[k] = p.role }
        }
        return parts.map { p in
            var n = p
            if n.role.isEmpty, let r = roleOf[nameKey(p.name)] { n.role = r }
            return n
        }
    }

    /// match, only in the portal the call is linked to: the key's portal is checked first, so on another (or an unknown)
    /// portal no participant email is sent and no record id of that portal comes back to be stored on the link.
    public static func matchInPortal(_ portal: String, participants: [CRMParticipant], existing: CRMCompany?, ownDomains: Set<String>, companyName: String,
                                     hasDeal: Bool, leads: Bool, connector c: CRMPeopleConnector) async throws -> CRMPeopleMatch {
        try await CRMPortal.verify(link: portal, connector: c)
        return try await match(participants: participants, existing: existing, ownDomains: ownDomains, companyName: companyName, hasDeal: hasDeal, leads: leads, connector: c)
    }

    /// Finds what exists and proposes what is missing. Reads only.
    /// Each participant's company comes from their own email domain. The call's company (the link's, or the one found
    /// by the PM's name for the account) stands in only on a call with one client domain, and never for a free-mail
    /// address: on a mixed call it cannot tell whose company it is.
    /// - existing: the company the call is already linked to
    /// - companyName: what the PM calls the account (the project's CRM company), else the name comes from the domain
    /// - hasDeal: a call with an open deal gets no lead (the deal is the sales loop)
    public static func match(participants: [CRMParticipant], existing: CRMCompany?, ownDomains: Set<String>, companyName: String,
                             hasDeal: Bool, leads: Bool, connector c: CRMPeopleConnector) async throws -> CRMPeopleMatch {
        let own = Set(ownDomains.map { $0.lowercased() })
        let external = participants.filter { $0.email.isEmpty || !own.contains(domain($0.email)) }
        let emails = external.map { normEmail($0.email) }.filter { !$0.isEmpty }
        var found: [CRMContact] = []
        for chunk in stride(from: 0, to: emails.count, by: 5).map({ Array(emails[$0..<min($0 + 5, emails.count)]) }) {
            for k in try await c.findContacts(emails: chunk) where !found.contains(where: { $0.id == k.id }) { found.append(k) }
        }
        var contactOf: [String: CRMContact] = [:]       // participant email -> the HubSpot contact
        for k in found { contactOf[normEmail(k.email)] = k }
        var matched: [(contact: CRMContact, person: CRMParticipant)] = []     // for the lead rows, with or without an email
        for p in external where !p.email.isEmpty { if let k = contactOf[normEmail(p.email)] { matched.append((k, p)) } }

        let clientDomains = CRMSnapshot.clientDomains(emails: emails, ownDomains: own)
        var byDomain: [String: CRMCompany] = [:]
        for d in clientDomains { if let co = try await c.findCompany(domain: d, name: ""), !co.id.isEmpty { byDomain[d] = co } }
        var company = existing ?? clientDomains.lazy.compactMap { byDomain[$0] }.first
        if company == nil, !companyName.isEmpty { company = try await c.findCompany(domain: "", name: companyName) }
        let single = clientDomains.count <= 1
        func companyOf(_ p: CRMParticipant) -> CRMCompany? {
            let d = domain(p.email)
            if let co = byDomain[d] { return co }
            return single && (p.email.isEmpty || clientDomains.first == d) ? company : nil
        }

        // second pass: an invitee whose email is not in HubSpot may be the same person under another email, inside their own company
        var roster: [String: [CRMContact]] = [:]         // company id -> its contacts, read once
        var unmatched: [CRMParticipant] = []
        for p in external where p.email.isEmpty || contactOf[normEmail(p.email)] == nil {
            let key = nameKey(p.name)
            if key.split(separator: " ").count >= 2, let co = companyOf(p) {
                if roster[co.id] == nil { roster[co.id] = try await c.companyContacts(companyId: co.id) }
                if let hit = roster[co.id]?.first(where: { nameKey($0.name) == key }) {
                    if !found.contains(where: { $0.id == hit.id }) { found.append(hit) }
                    if !p.email.isEmpty { contactOf[normEmail(p.email)] = hit }
                    matched.append((hit, p))
                    continue
                }
            }
            unmatched.append(p)
        }

        var proposals: [CRMProposal] = []
        var newCompany: [String: String] = [:]           // domain -> the name of the company proposed for it
        for d in clientDomains where byDomain[d] == nil && !(single && company != nil) {
            let name = single && !companyName.isEmpty ? companyName : Self.companyName(fromDomain: d)
            newCompany[d] = name
            proposals.append(CRMProposal(id: "company.\(newCompany.count - 1)", kind: .company, name: name, domain: d))
        }
        func place(_ p: CRMParticipant) -> (name: String, domain: String, id: String) {
            if let co = companyOf(p) { return (co.name, co.domain, co.id) }
            let d = domain(p.email)
            return newCompany[d].map { ($0, d, "") } ?? ("", "", "")
        }
        var n = 0
        func lead(_ person: String, email: String, at co: (name: String, domain: String, id: String), contactId: String = "") {
            proposals.append(CRMProposal(id: "lead.\(n)", kind: .lead, name: person + (co.name.isEmpty ? "" : " (\(co.name))"), email: email,
                                         company: co.name, domain: co.domain, companyId: co.id, contactId: contactId))
        }
        for p in unmatched where !p.email.isEmpty {
            let e = normEmail(p.email), d = domain(e)
            let name = p.name.isEmpty ? derivedName(e) : p.name
            // decision 4: an address at a free-mail service without a real name is not worth a contact
            if freeMail.contains(d) && (p.name.isEmpty || nameKey(p.name) == nameKey(derivedName(e))) { continue }
            let co = place(p)
            proposals.append(CRMProposal(id: "contact.\(n)", kind: .contact, name: name, email: e, role: p.role, company: co.name, domain: co.domain, companyId: co.id))
            if leads && !hasDeal { lead(name, email: e, at: co) }
            n += 1
        }
        // a person already in HubSpot gets a lead too when they have none (a contact made by an earlier Apply whose lead
        // failed, or one found by name without an email). A failed lookup (no Leads scopes) drops the lead rows only.
        var problem: CRMError?
        if leads && !hasDeal {
            var asked = Set<String>()
            for (k, p) in matched where asked.insert(k.id).inserted {
                let has: String?
                do { has = try await c.leadId(contactId: k.id) } catch {
                    problem = (error as? CRMError) ?? CRMError(code: 0, message: error.localizedDescription)
                    proposals.removeAll { $0.kind == .lead }
                    break
                }
                guard has == nil else { continue }
                let e = p.email.isEmpty ? normEmail(k.email) : normEmail(p.email)
                lead(k.name.isEmpty ? (p.name.isEmpty ? derivedName(e) : p.name) : k.name, email: e, at: place(p), contactId: k.id)
                n += 1
            }
        }
        let all = clientDomains.compactMap { byDomain[$0] }
        return CRMPeopleMatch(company: company, contacts: found, proposals: proposals, companies: all, problem: problem)
    }

    /// What Auto creates by itself: only the kinds whose own switch is on. A new contact's lead waits until the contact
    /// exists (its row comes back on the next run, for the contact then in HubSpot) unless Contact Auto made it too;
    /// Lead Auto never creates a contact. The click path adds a lead's contact instead (the PM ticked the lead).
    public static func autoChosen(_ proposals: [CRMProposal], contacts: Bool, companies: Bool, leads: Bool) -> [CRMProposal] {
        let on = proposals.filter {
            switch $0.kind {
            case .company: return companies
            case .contact: return contacts
            case .lead: return leads
            }
        }
        return on.filter { p in
            p.kind != .lead || !p.contactId.isEmpty || on.contains { $0.kind == .contact && $0.email == p.email }
        }
    }

    /// The people refs a Call needs: every company and every contact the call has (the link's and this run's).
    public static func callRefs(companies: [CRMCompany], contacts: [CRMContact]) -> [CRMRef] {
        var out: [CRMRef] = []
        for r in companies.filter({ !$0.id.isEmpty }).map({ CRMRef("companies", $0.id) }) + contacts.filter({ !$0.id.isEmpty }).map({ CRMRef("contacts", $0.id) })
            where !out.contains(r) { out.append(r) }
        return out
    }

    /// Creates the given proposals, companies first, then contacts, then leads. Every create looks for an existing
    /// object first (the email, the domain, the contact's leads), so a second run or a second Pacenote never makes a
    /// duplicate; what existed is reused and counted, not undone. A failure stops the run; what was made stays listed.
    /// A contact or lead goes to its own company: the one it was matched with, else the one made for its domain.
    public static func apply(_ chosen: [CRMProposal], company existing: CRMCompany?, connector c: CRMPeopleConnector) async -> CRMPeopleResult {
        var r = CRMPeopleResult()
        r.company = existing
        var byEmail: [String: String] = [:]      // email -> contact id
        var madeFor: [String: String] = [:]      // domain -> company id
        func companyId(_ p: CRMProposal) -> String { !p.companyId.isEmpty ? p.companyId : (madeFor[p.domain] ?? "") }
        do {
            for p in chosen where p.kind == .company {
                let k = try await c.createCompany(name: p.name, domain: p.domain)
                madeFor[p.domain] = k.id
                let co = CRMCompany(id: k.id, name: p.name, domain: p.domain)
                if r.company == nil { r.company = co }
                r.companies.append(co)
                if k.created { r.created.append(("companies", k.id)) } else { r.reused += 1 }
            }
            for p in chosen where p.kind == .contact {
                let k = try await c.createContact(name: p.name, email: p.email, role: p.role, companyId: companyId(p))
                byEmail[p.email] = k.id
                r.contacts.append(CRMContact(id: k.id, name: p.name, email: p.email, role: p.role))
                if k.created { r.created.append(("contacts", k.id)) } else { r.reused += 1 }
            }
            for p in chosen where p.kind == .lead {
                let cid = byEmail[p.email] ?? p.contactId
                guard !cid.isEmpty else { continue }       // a lead needs its contact (HubSpot requires one)
                let k = try await c.createLead(name: p.name, contactId: cid, companyId: companyId(p))
                if k.created { r.created.append(("leads", k.id)) } else { r.reused += 1 }
            }
        } catch {
            r.failure = (error as? CRMError) ?? CRMError(code: 0, message: error.localizedDescription)
        }
        return r
    }

    /// The one write path of Auto and Apply: creates the chosen rows, then gives a Call logged earlier every person and
    /// company of the call (the people refs the stored link already has, and `companies` = the matched ones). The
    /// association PUTs are idempotent and are all sent again on every run, so one that failed before (while the link
    /// kept the ids) is made by the next Apply or Auto. When the Call is written in this run its own upsert associates
    /// them, so nothing is added here. A failed association is the result's failure, never hidden; what was created
    /// stays listed for Undo. The association and `save` take one turn in the per-call queue of H2 (Relink takes its
    /// turn there too): the stored link is read when the turn comes and, when it now names another portal than
    /// `portal` (the one checked before the creates), nothing is associated or saved and the failure has
    /// CRMPortal.mismatchCode, so one portal's ids never reach the other's link or Call.
    public static func commit(_ chosen: [CRMProposal], company: CRMCompany?, companies: [CRMCompany] = [], found: [CRMContact], portal: String,
                              callKey: String, callWritten: Bool, connector c: CRMPeopleConnector, queue: CRMCallQueue = .shared,
                              stored: @escaping () -> CRMStoredLink, save: @escaping (CRMPeopleResult) -> Void = { _ in }) async -> CRMPeopleResult {
        var r = chosen.isEmpty ? CRMPeopleResult(company: company) : await apply(chosen, company: company, connector: c)
        let made = r
        let own = callRefs(companies: [made.company].compactMap { $0 } + companies + made.companies, contacts: found + made.contacts)
        do {
            try await queue.run(callKey) {
                let s = stored()
                guard CRMPortal.matches(link: s.portalId, current: portal) else {
                    throw CRMError(code: CRMPortal.mismatchCode, message: "This call was linked to another HubSpot portal meanwhile. Nothing was added to it.", scope: "portal")
                }
                save(made)
                guard !callWritten, !s.callId.isEmpty else { return }
                let add = (s.linked.filter { $0.type == "companies" || $0.type == "contacts" } + own)
                    .reduce(into: [CRMRef]()) { out, a in if !out.contains(a) { out.append(a) } }
                if !add.isEmpty { try await c.associateCall(callId: s.callId, refs: add) }
            }
        } catch {
            let e = (error as? CRMError) ?? CRMError(code: 0, message: error.localizedDescription)
            r.failure = r.failure.map { CRMError(code: $0.code, message: $0.message + " " + e.message, scope: $0.scope) } ?? e
        }
        return r
    }
}

extension HubSpotConnector: CRMPeopleConnector {
    /// Added only when the PM turns Leads on (Settings > Integrations > HubSpot); a portal without them answers 403 naming the scope.
    public static let leadScopes = ["crm.objects.leads.read", "crm.objects.leads.write"]
    // HUBSPOT_DEFINED association type ids, object on the left to object on the right
    static let contactToCompany = 1, leadToContact = 578, leadToCompany = 610

    public func companyContacts(companyId: String) async throws -> [CRMContact] {
        var ids: [String] = [], after = ""
        repeat {
            let a = try await call("GET", "/crm/v4/objects/companies/\(companyId)/associations/contacts?limit=500" + (after.isEmpty ? "" : "&after=" + after))
            ids += ((a["results"] as? [[String: Any]]) ?? []).compactMap { r -> String? in (r["toObjectId"]).map { "\($0)" } }
            after = (((a["paging"] as? [String: Any])?["next"] as? [String: Any])?["after"]).map { "\($0)" } ?? ""
        } while !after.isEmpty
        var out: [CRMContact] = []
        for start in stride(from: 0, to: ids.count, by: 100) {      // batch read takes 100 at a time
            let r = try await call("POST", "/crm/v3/objects/contacts/batch/read", [
                "properties": ["firstname", "lastname", "email", "jobtitle"], "inputs": ids[start..<min(start + 100, ids.count)].map { ["id": $0] }])
            out += ((r["results"] as? [[String: Any]]) ?? []).compactMap { c in
                guard let id = c["id"] as? String else { return nil }
                return CRMContact(id: id, name: [Self.p(c, "firstname"), Self.p(c, "lastname")].filter { !$0.isEmpty }.joined(separator: " "),
                                  email: Self.p(c, "email"), role: Self.p(c, "jobtitle"))
            }
        }
        return out
    }

    public func leadId(contactId: String) async throws -> String? {
        let a = try await call("GET", "/crm/v4/objects/contacts/\(contactId)/associations/leads?limit=1")
        return ((a["results"] as? [[String: Any]])?.first?["toObjectId"]).map { "\($0)" }
    }

    public func createCompany(name: String, domain: String) async throws -> CRMCreated {
        if !domain.isEmpty, let co = try await findCompany(domain: domain, name: "") { return CRMCreated(id: co.id, created: false) }
        var props: [String: Any] = ["name": name]       // no owner: Pacenote never sets the owner of a company (spec section 7)
        if !domain.isEmpty { props["domain"] = domain }
        let r = try await call("POST", "/crm/v3/objects/companies", ["properties": props])
        return CRMCreated(id: (r["id"] as? String) ?? "", created: true)
    }

    public func createContact(name: String, email: String, role: String, companyId: String) async throws -> CRMCreated {
        let e = CRMPeople.normEmail(email)
        if let k = try await findContacts(emails: [e]).first(where: { CRMPeople.normEmail($0.email) == e }) { return CRMCreated(id: k.id, created: false) }
        let n = CRMPeople.splitName(name)
        var props: [String: Any] = ["email": e]
        if !n.first.isEmpty { props["firstname"] = n.first }
        if !n.last.isEmpty { props["lastname"] = n.last }
        if !role.isEmpty { props["jobtitle"] = role }
        var body: [String: Any] = ["properties": props]
        if !companyId.isEmpty {
            body["associations"] = [["to": ["id": companyId], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": Self.contactToCompany]]]]
        }
        do {
            let r = try await call("POST", "/crm/v3/objects/contacts", body)
            return CRMCreated(id: (r["id"] as? String) ?? "", created: true)
        } catch let err as CRMError where err.code == 409 {
            // someone made the contact between the search and the create: HubSpot names it ("Existing ID: 123")
            if let m = err.message.range(of: "Existing ID: [0-9]+", options: .regularExpression) {
                return CRMCreated(id: String(err.message[m].dropFirst("Existing ID: ".count)), created: false)
            }
            throw err
        }
    }

    public func createLead(name: String, contactId: String, companyId: String) async throws -> CRMCreated {
        // one lead per contact: an existing one is reused
        if let id = try await leadId(contactId: contactId) { return CRMCreated(id: id, created: false) }
        var types: [[String: Any]] = [["to": ["id": contactId], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": Self.leadToContact]]]]
        if !companyId.isEmpty { types.append(["to": ["id": companyId], "types": [["associationCategory": "HUBSPOT_DEFINED", "associationTypeId": Self.leadToCompany]]]) }
        let r = try await call("POST", "/crm/v3/objects/leads", ["properties": owned(["hs_lead_name": name]), "associations": types])
        return CRMCreated(id: (r["id"] as? String) ?? "", created: true)
    }

    /// A Call that was logged before its people existed gets them now (the default association, no type id to guess).
    public func associateCall(callId: String, refs: [CRMRef]) async throws {
        for a in refs where Self.callAssoc[a.type] != nil {
            _ = try await call("PUT", "/crm/v4/objects/calls/\(callId)/associations/default/\(a.type)/\(a.id)")
        }
    }
}
