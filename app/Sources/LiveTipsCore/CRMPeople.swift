import Foundation

/// H3 (PB-59, docs/specs/hubspot-crm-integration.md sections 2, 6 and 7): find or create the contacts, the company and
/// the leads of a call. Match first (email, then name inside the company), propose what is missing with the fields
/// filled in, create only after the PM's click or with the per-object Auto switch, never a duplicate, and give the new
/// ids to the Call of H2. Own file on purpose: the CRMConnector protocol does not change.
public struct CRMParticipant: Equatable {
    public var name: String
    public var email: String
    public var role: String          // the job title when the call page or the calendar has one, else empty
    public init(name: String, email: String = "", role: String = "") { self.name = name; self.email = email; self.role = role }
}

public enum CRMProposalKind: String { case company, contact, lead }

/// One thing Pacenote would create, with every field the PM sees in the review row.
public struct CRMProposal: Equatable, Identifiable {
    public var id: String            // "company", "contact.<n>", "lead.<n>"
    public var kind: CRMProposalKind
    public var name: String          // the person's or company's name; the lead's name
    public var email = ""
    public var role = ""
    public var company = ""          // the company's name (contact and lead rows)
    public var domain = ""           // the company's domain
    public init(id: String, kind: CRMProposalKind, name: String, email: String = "", role: String = "", company: String = "", domain: String = "") {
        self.id = id; self.kind = kind; self.name = name; self.email = email; self.role = role; self.company = company; self.domain = domain
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
    public var company: CRMCompany?
    public var contacts: [CRMContact]        // found in HubSpot: by email, else by name inside the company
    public var proposals: [CRMProposal]
}

/// What was created (and so can be undone) and what already existed (reused, never undone).
public struct CRMPeopleResult {
    public var created: [(type: String, id: String)] = []
    public var company: CRMCompany?
    public var contacts: [CRMContact] = []
    public var reused = 0
    public var failure: CRMError?
    public var changed: Bool { !created.isEmpty || reused > 0 }
}

public struct CRMCreated: Equatable { public var id: String; public var created: Bool
    public init(id: String, created: Bool) { self.id = id; self.created = created } }

/// The write side of H3. HubSpotConnector implements it; the app uses it when `connector() as? CRMPeopleConnector`.
public protocol CRMPeopleConnector: CRMConnector {
    func findContacts(named: String, companyId: String) async throws -> [CRMContact]
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
    /// they have the same length; otherwise each email gets the name derived from it.
    public static func participants(names: [String], emails: [String], titles: [String: String] = [:]) -> [CRMParticipant] {
        var out: [CRMParticipant] = [], seen = Set<String>()
        let paired = names.count == emails.count
        for (i, raw) in emails.enumerated() {
            let e = normEmail(raw)
            guard e.contains("@"), seen.insert(e).inserted else { continue }
            let name = paired ? names[i] : derivedName(e)
            out.append(CRMParticipant(name: name, email: e, role: titles[nameKey(name)] ?? ""))
        }
        if !paired {      // names without an email still help the name match inside the company
            for n in names where !n.isEmpty && !out.contains(where: { nameKey($0.name) == nameKey(n) }) {
                out.append(CRMParticipant(name: n, role: titles[nameKey(n)] ?? ""))
            }
        }
        return out
    }

    /// Finds what exists and proposes what is missing. Reads only.
    /// - existing: the company the call is already linked to (skips the company search)
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
        let foundEmails = Set(found.map { normEmail($0.email) })

        var company = existing
        let clientDomains = CRMSnapshot.clientDomains(emails: emails, ownDomains: own)
        if company == nil {
            for d in clientDomains { if let co = try await c.findCompany(domain: d, name: ""), !co.id.isEmpty { company = co; break } }
        }
        if company == nil, !companyName.isEmpty { company = try await c.findCompany(domain: "", name: companyName) }

        // second pass: an invitee whose email is not in HubSpot may be the same person under another email
        var unmatched = external.filter { $0.email.isEmpty || !foundEmails.contains(normEmail($0.email)) }
        if let co = company, !unmatched.isEmpty {
            for p in unmatched {
                let key = nameKey(p.name)
                guard key.split(separator: " ").count >= 2 else { continue }
                let hits = try await c.findContacts(named: p.name, companyId: co.id).filter { nameKey($0.name) == key }
                if let hit = hits.first {
                    if !found.contains(where: { $0.id == hit.id }) { found.append(hit) }
                    unmatched.removeAll { $0 == p }
                }
            }
        }

        var proposals: [CRMProposal] = []
        let coName = companyName.isEmpty ? (clientDomains.first.map(companyName(fromDomain:)) ?? "") : companyName
        if company == nil, let d = clientDomains.first {
            proposals.append(CRMProposal(id: "company", kind: .company, name: coName, domain: d))
        }
        var n = 0
        for p in unmatched where !p.email.isEmpty {
            let e = normEmail(p.email), d = domain(e)
            let name = p.name.isEmpty ? derivedName(e) : p.name
            // decision 4: an address at a free-mail service without a real name is not worth a contact
            if freeMail.contains(d) && (p.name.isEmpty || nameKey(p.name) == nameKey(derivedName(e))) { continue }
            let cname = company?.name ?? (clientDomains.contains(d) ? coName : "")
            let dom = company?.domain.isEmpty == false ? company!.domain : (clientDomains.contains(d) ? d : "")
            proposals.append(CRMProposal(id: "contact.\(n)", kind: .contact, name: name, email: e, role: p.role, company: cname, domain: dom))
            if leads && !hasDeal {
                proposals.append(CRMProposal(id: "lead.\(n)", kind: .lead, name: name + (cname.isEmpty ? "" : " (\(cname))"), email: e, company: cname, domain: dom))
            }
            n += 1
        }
        return CRMPeopleMatch(company: company, contacts: found, proposals: proposals)
    }

    /// Creates the given proposals, company first, then contacts, then leads. Every create looks for an existing
    /// object first (the email, the domain, the contact's leads), so a second run or a second Pacenote never makes a
    /// duplicate; what existed is reused and counted, not undone. A failure stops the run; what was made stays listed.
    public static func apply(_ chosen: [CRMProposal], company existing: CRMCompany?, connector c: CRMPeopleConnector) async -> CRMPeopleResult {
        var r = CRMPeopleResult()
        r.company = existing
        var byEmail: [String: String] = [:]      // email -> contact id
        do {
            if let p = chosen.first(where: { $0.kind == .company }) {
                let k = try await c.createCompany(name: p.name, domain: p.domain)
                r.company = CRMCompany(id: k.id, name: p.name, domain: p.domain)
                if k.created { r.created.append(("companies", k.id)) } else { r.reused += 1 }
            }
            for p in chosen where p.kind == .contact {
                let k = try await c.createContact(name: p.name, email: p.email, role: p.role, companyId: r.company?.id ?? "")
                byEmail[p.email] = k.id
                r.contacts.append(CRMContact(id: k.id, name: p.name, email: p.email, role: p.role))
                if k.created { r.created.append(("contacts", k.id)) } else { r.reused += 1 }
            }
            for p in chosen where p.kind == .lead {
                guard let cid = byEmail[p.email] else { continue }       // a lead needs its contact (HubSpot requires one)
                let k = try await c.createLead(name: p.name, contactId: cid, companyId: r.company?.id ?? "")
                if k.created { r.created.append(("leads", k.id)) } else { r.reused += 1 }
            }
        } catch {
            r.failure = (error as? CRMError) ?? CRMError(code: 0, message: error.localizedDescription)
        }
        return r
    }
}

extension HubSpotConnector: CRMPeopleConnector {
    /// Added only when the PM turns Leads on (Settings > Integrations > HubSpot); a portal without them answers 403 naming the scope.
    public static let leadScopes = ["crm.objects.leads.read", "crm.objects.leads.write"]
    // HUBSPOT_DEFINED association type ids, object on the left to object on the right
    static let contactToCompany = 1, leadToContact = 578, leadToCompany = 610

    public func findContacts(named: String, companyId: String) async throws -> [CRMContact] {
        let a = try await call("GET", "/crm/v4/objects/companies/\(companyId)/associations/contacts?limit=500")
        let ids = ((a["results"] as? [[String: Any]]) ?? []).compactMap { r -> String? in (r["toObjectId"]).map { "\($0)" } }
        guard !ids.isEmpty else { return [] }
        let r = try await call("POST", "/crm/v3/objects/contacts/batch/read", [
            "properties": ["firstname", "lastname", "email", "jobtitle"], "inputs": ids.prefix(100).map { ["id": $0] }])
        return ((r["results"] as? [[String: Any]]) ?? []).compactMap { c in
            guard let id = c["id"] as? String else { return nil }
            return CRMContact(id: id, name: [Self.p(c, "firstname"), Self.p(c, "lastname")].filter { !$0.isEmpty }.joined(separator: " "),
                              email: Self.p(c, "email"), role: Self.p(c, "jobtitle"))
        }
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
        let a = try await call("GET", "/crm/v4/objects/contacts/\(contactId)/associations/leads?limit=1")
        if let id = ((a["results"] as? [[String: Any]])?.first?["toObjectId"]).map({ "\($0)" }) { return CRMCreated(id: id, created: false) }
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
