import AppKit
import LiveTipsCore
import SwiftUI

// PB-37 (G6, design review B1): the Research this website panel in every phase, for the shot harness. A SiteResearchSession is
// put into the draft in a fixed phase with a fixture SitePlan and SiteReadReport: no process, no network, no sidecar.
// Demo data only (lumenfield.example, Emily Parker).
extension SheetShots {
    private static let paths = ["/about", "/team", "/customers", "/case-studies/reporting", "/services", "/pricing", "/blog/monthly-reports",
                                "/blog/finance-teams", "/leadership", "/security", "/partners", "/careers", "/news/launch", "/news/awards",
                                "/integrations", "/events", "/faq", "/press"]

    /// `planned` pages the sidecar would read (the first of them ticked up to the budget), then a sign-in page, a policy page and one over the limit.
    private func researchPlan(host: String = "lumenfield.example", planned: Int = 12, start: [String: Any] = ["status": "ok"], first: String? = nil) -> SitePlan {
        var rows: [[String: Any]] = []
        for (i, p) in Self.paths.prefix(planned).enumerated() {
            let path = i == 0 ? (first ?? p) : p
            rows.append(["url": "https://" + host + path, "path": path, "kind": ["/team": "team", "/about": "about", "/customers": "customers"][p] ?? "page",
                         "depth": 1, "planned": true, "note": "", "score": 0.9 - Double(i) * 0.03, "est_tokens": 900 + i * 120])
        }
        for (p, note) in [("/login", "sign-in"), ("/privacy", "policy"), ("/archive", "limit")] {
            rows.append(["url": "https://" + host + p, "path": p, "kind": "page", "depth": 1, "planned": false, "note": note, "est_tokens": 1500])
        }
        return SitePlan(json: ["url": "https://" + host + "/about", "domain": host, "start": start, "limits": ["depth": 2, "respect_robots": true], "pages": rows])!
    }

    /// The report after reading `read` pages: the rest empty, blocked, a duplicate or an error, as the sidecar's rows say.
    private func researchReport(_ plan: SitePlan, read: Int, others: [(String, String)] = [], tokens: Int) -> SiteReadReport {
        var sources: [[String: Any]] = [], rows: [[String: Any]] = [], sum: [String: Any] = ["tokens": tokens]
        for (i, p) in plan.ticked.enumerated() {
            if i < read {
                sources.append(["id": "w\(i)", "uri": p.url, "path": p.path, "title": "Lumenfield - " + p.path, "size_tokens": 800 + i * 40, "parent_uri": plan.url, "used": "full"])
                rows.append(["url": p.url, "status": "read", "note": ""]); sum["read"] = (sum["read"] as? Int ?? 0) + 1
            } else if i - read < others.count {
                let o = others[i - read]
                rows.append(["url": p.url, "status": o.0, "note": o.1]); sum[o.0] = (sum[o.0] as? Int ?? 0) + 1
            }
        }
        return SiteReadReport(json: ["url": plan.url, "sources": sources, "pages": rows, "summary": sum])!
    }

    /// Puts a research session into the draft's website row (index 1) in the given phase.
    private func stage(_ d: SheetDraft, _ plan: SitePlan?, _ phase: SiteResearchSession.Phase, route: String? = nil, _ set: (SiteResearchSession) -> Void = { _ in }) {
        let web = d.sources[1]
        let ses = SiteResearchSession(sourceID: web.id, url: web.uri ?? "")
        ses.plan = plan; ses.phase = phase
        assert(ses.idle, "a staged research session must hold no process")
        set(ses)
        d.research[web.id] = ses
        if let route { d.route = route }
    }

    /// A finished read: the session holds the report and the draft gets the child rows, as after a real read.
    private func finish(_ d: SheetDraft, _ plan: SitePlan, _ rep: SiteReadReport, cancelled: Bool = false) {
        stage(d, plan, .finished) { s in
            s.report = rep; s.pageStates = rep.states; s.done = rep.counts["read"] ?? 0; s.cancelling = cancelled
        }
        d.addResearched(rep, parent: d.sources[1].id)
    }

    func researchScenes() -> [Scene] {
        var s: [Scene] = []
        func scene(_ name: String, quick: Bool = false, open: Bool = true, width: CGFloat = 860, _ build: @escaping (SheetDraft) -> Void) -> Scene {
            var made: SheetDraft?
            return Scene(name: "web-" + name, size: NSSize(width: width, height: 2400), make: { [unowned self] in
                let d = draft(quick ? .quick : .expert, step: 3)
                build(d)
                made = d
                SheetQuickView.shotOpen = quick && open ? "sources" : nil
                let v = SheetPrepView(draft: d)
                SheetQuickView.shotOpen = nil
                return AnyView(v)
            }, tweak: { made?.checking = false }, still: true)
        }
        // plan: the three cost lines (dollars on the API, free on this Mac, a share of the window on an account route)
        for (n, route) in [("usd", AIRoute.api), ("free", AIRoute.local), ("window", AIRoute.code)] {
            s.append(scene("plan-" + n) { [unowned self] d in stage(d, researchPlan(), .ready, route: route) })
        }
        s.append(scene("reading") { [unowned self] d in
            stage(d, researchPlan(), .reading) { $0.done = 3; $0.total = 12; $0.currentPath = "/team" }
        })
        s.append(scene("cancelling") { [unowned self] d in
            stage(d, researchPlan(), .reading) { $0.done = 3; $0.total = 12; $0.currentPath = "/team"; $0.cancelling = true }
        })
        s.append(scene("cancelled-kept") { [unowned self] d in
            let p = researchPlan()
            finish(d, p, researchReport(p, read: 3, tokens: 2_600), cancelled: true)
        })
        s.append(scene("finished") { [unowned self] d in
            let p = researchPlan()
            finish(d, p, researchReport(p, read: 7, others: [("empty", ""), ("empty", ""), ("blocked", "robots.txt"), ("duplicate", "same text as /about"), ("error", "HTTP 503")], tokens: 6_000))
        })
        s.append(scene("blocked") { [unowned self] d in
            stage(d, researchPlan(planned: 0, start: ["status": "blocked", "note": "robots.txt"]), .ready)
        })
        s.append(scene("unreachable") { [unowned self] d in
            stage(d, researchPlan(planned: 0, start: ["status": "error", "http": 503]), .ready)
        })
        s.append(scene("js-only") { [unowned self] d in
            let p = researchPlan(planned: 7)
            finish(d, p, researchReport(p, read: 0, others: Array(repeating: ("empty", ""), count: 7), tokens: 0))
        })
        s.append(scene("plan-failed") { [unowned self] d in
            stage(d, nil, .failed) { $0.error = "the site did not answer in 30 s" }
        })
        // a long site name in the row title, the panel title and the progress line
        s.append(scene("long-name") { [unowned self] d in
            let host = "a-very-long-company-name-and-division.example.co.uk"
            d.sources[1].title = host + "/about"; d.sources[1].uri = "https://" + host + "/about"
            stage(d, researchPlan(host: host, first: "/about-our-people-and-the-long-history-of-the-company-division/leadership"), .reading) {
                $0.done = 3; $0.total = 12; $0.currentPath = "/about-our-people-and-the-long-history-of-the-company-division/leadership"
            }
        })
        // the narrowest window (720 pt), where the long Ukrainian button label is tightest
        s.append(scene("narrow", width: 720) { [unowned self] d in stage(d, researchPlan(), .ready, route: AIRoute.code) })
        s.append(scene("narrow-long-name", width: 720) { [unowned self] d in
            let host = "a-very-long-company-name-and-division.example.co.uk"
            d.sources[1].title = host + "/about"; d.sources[1].uri = "https://" + host + "/about"
            stage(d, researchPlan(host: host), .ready, route: AIRoute.code)
        })
        s.append(scene("budget-reached") { [unowned self] d in stage(d, researchPlan(planned: 18), .ready) })
        // Quick, with Change open on Sources: the link field, the rows, the panel in plan and finished, then the collapsed row
        s.append(scene("quick-plan", quick: true) { [unowned self] d in stage(d, researchPlan(), .ready, route: AIRoute.code) })
        s.append(scene("quick-finished", quick: true) { [unowned self] d in
            let p = researchPlan()
            finish(d, p, researchReport(p, read: 7, others: [("empty", ""), ("empty", ""), ("blocked", "robots.txt"), ("duplicate", "same text as /about"), ("error", "HTTP 503")], tokens: 6_000))
        })
        s.append(scene("quick-collapsed", quick: true, open: false) { [unowned self] d in
            let p = researchPlan()
            finish(d, p, researchReport(p, read: 7, others: [], tokens: 6_000))
            d.closeResearch(sourceID: d.sources[1].id)
        })
        return s
    }
}
