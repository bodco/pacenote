import Foundation

// PB-37 (G6, subtask C): the pure part of "Research this website": the sidecar contract (`webplan`, `webfetch`, spec
// section 7), the plan with its ticks and budget, the reasons a page is not read, the cost line and the pages read
// as child sources. No UI and no process here; `LiveTips/SheetSiteResearch.swift` runs it. Checked in CoreChecks.

/// One page of the plan: the sidecar's row plus the tick the person controls.
public struct SitePlanPage: Identifiable, Equatable {
    public var url: String
    public var path: String
    public var kind: String
    public var depth: Int
    /// The sidecar's own verdict: false = not read, `note` says why (sign-in, cart, policy, feed, contact, file, robots, limit, duplicate).
    public var planned: Bool
    public var note: String
    public var score: Double?
    /// Why the page is where it is in the list: a code (start, goal, call, section) and the person's own goal words that matched.
    /// The panel words it in the app language; the sidecar's English `why_ranked` is never shown.
    public var whyCode: String
    public var whyTerms: [String]
    public var estTokens: Int
    public var ticked = false
    public var id: String { url }

    /// A page the person can tick: the sidecar planned it (the budget is applied on top by `SitePlan`).
    public var tickable: Bool { planned }
}

/// A link to another site: offered as a suggested source, never read.
public struct SiteExternal: Equatable {
    public var url: String
    public var domain: String
    public var text: String
}

public struct SitePlan: Equatable {
    public var url: String
    public var domain: String
    public var pages: [SitePlanPage]
    public var external: [SiteExternal]
    /// "ok", "blocked" (robots.txt) or "error" (the site is down, not a page ...); `startNote` is the cause.
    public var startStatus: String
    public var startNote: String
    /// "private" = the start address is a private or non-public one (not a robots.txt block).
    public var startWhy: String
    public var depth: Int
    public var respectRobots: Bool
    /// How many pages the person agreed to read: 15 by default, up to 30 (the code's hard cap in the sidecar).
    public private(set) var budget = SiteResearch.defaultBudget

    public init?(json: [String: Any]) {
        guard let url = json["url"] as? String, !url.isEmpty else { return nil }
        self.url = url
        domain = json["domain"] as? String ?? ""
        let start = json["start"] as? [String: Any] ?? [:]
        startStatus = start["status"] as? String ?? "ok"
        if let n = start["note"] as? String { startNote = n }
        else if let h = start["http"] as? Int { startNote = "HTTP \(h)" } else { startNote = "" }
        startWhy = start["why"] as? String ?? ""
        let lim = json["limits"] as? [String: Any] ?? [:]
        depth = lim["depth"] as? Int ?? 2
        respectRobots = lim["respect_robots"] as? Bool ?? true
        pages = (json["pages"] as? [[String: Any]] ?? []).compactMap { o in
            guard let u = o["url"] as? String else { return nil }
            return SitePlanPage(url: u, path: o["path"] as? String ?? u, kind: o["kind"] as? String ?? "page", depth: o["depth"] as? Int ?? 1,
                                planned: o["planned"] as? Bool ?? false, note: o["note"] as? String ?? "",
                                score: (o["score"] as? NSNumber)?.doubleValue, whyCode: o["why_code"] as? String ?? "", whyTerms: o["why_terms"] as? [String] ?? [],
                                estTokens: o["est_tokens"] as? Int ?? SiteResearch.pageEstimateTokens)
        }
        external = (json["external"] as? [[String: Any]] ?? []).compactMap { o in
            guard let u = o["url"] as? String else { return nil }
            return SiteExternal(url: u, domain: o["domain"] as? String ?? "", text: o["text"] as? String ?? "")
        }
        applyBudget()
    }

    /// The first `budget` planned pages, in the sidecar's order, are ticked.
    private mutating func applyBudget() {
        var left = budget
        for i in pages.indices {
            let on = pages[i].tickable && left > 0
            pages[i].ticked = on
            if on { left -= 1 }
        }
    }

    public mutating func setBudget(_ n: Int) {
        budget = max(1, min(n, SiteResearch.maxBudget))
        applyBudget()
    }

    public var ticked: [SitePlanPage] { pages.filter(\.ticked) }
    public var tickedCount: Int { ticked.count }
    public var tickedTokens: Int { ticked.reduce(0) { $0 + $1.estTokens } }
    /// Pages the sidecar found on the site and could plan (not skipped by a rule), for "15 of about 40 pages found".
    public var found: Int { pages.count }

    public mutating func toggle(_ url: String) {
        guard let i = pages.firstIndex(where: { $0.url == url }), pages[i].tickable else { return }
        if pages[i].ticked { pages[i].ticked = false; return }
        guard tickedCount < budget else { return }
        pages[i].ticked = true
    }

    /// The reason a page is not read, as a key suffix for `sheet.ui.web.why.<key>`; nil = it is planned or ticked.
    /// A planned page left out by the budget says "limit".
    public func reason(_ p: SitePlanPage) -> String? {
        if p.ticked { return nil }
        if !p.planned { return p.note.isEmpty ? "limit" : p.note }
        return tickedCount >= budget ? "limit" : "unticked"
    }

    /// The pages the sidecar reads: only the ticked ones are planned, and the limit is exactly their count, so a page
    /// found while reading (depth 2) never pushes the total above what the person agreed to.
    public func fetchPlan() -> [String: Any] {
        let rows: [[String: Any]] = pages.map { p in
            var d: [String: Any] = ["url": p.url, "path": p.path, "depth": p.depth, "kind": p.kind, "planned": p.ticked,
                                    "note": p.ticked ? "" : (p.planned ? "not ticked" : p.note)]
            if let s = p.score { d["score"] = s }
            return d
        }
        return ["url": url, "domain": domain,
                "limits": ["same_site": true, "depth": depth, "max_pages": max(1, tickedCount), "respect_robots": respectRobots] as [String: Any],
                "pages": rows,
                "external": external.map { ["url": $0.url, "domain": $0.domain, "text": $0.text] }]
    }
}

/// How the extra tokens the pages add are shown (spec question 5b): dollars only where the route has a price.
public enum SiteCost: Equatable {
    /// usd is the extra input cost of one pass; `local` runs on this Mac and costs nothing.
    case dollars(tokens: Int, usd: Double)
    case free(tokens: Int)
    /// A subscription or account route: tokens and the share of one pass of the model's window, never a dollar figure.
    case window(tokens: Int, percent: Int)
}

/// One page read, as a source under the site row.
public struct SiteChild: Equatable {
    public var id: String
    public var uri: String
    public var path: String
    public var title: String
    public var tokens: Int
    public var why: [String]
    public var parentURI: String
    public var used: String
    public var reason: String?
}

/// The report of `webfetch`: what was read, every page's state, the other sites offered.
public struct SiteReadReport: Equatable {
    public var children: [SiteChild]
    public var suggested: [SiteExternal]
    /// read / empty / blocked / error / skipped / duplicate -> number of pages
    public var counts: [String: Int]
    public var tokens: Int
    /// page url -> (state, note), for the lines the panel shows after reading
    public var states: [String: (state: String, note: String)]

    public init?(json: [String: Any]) {
        guard json["error"] == nil, json["sources"] is [[String: Any]] else { return nil }
        let start = json["url"] as? String ?? ""
        children = (json["sources"] as? [[String: Any]] ?? []).compactMap { o in
            guard let u = o["uri"] as? String, let p = o["path"] as? String else { return nil }
            return SiteChild(id: o["id"] as? String ?? u, uri: u, path: p, title: o["title"] as? String ?? u, tokens: o["size_tokens"] as? Int ?? 0,
                             why: o["why"] as? [String] ?? [], parentURI: o["parent_uri"] as? String ?? start,
                             used: o["used"] as? String ?? "full", reason: o["reason"] as? String)
        }
        suggested = (json["suggested"] as? [[String: Any]] ?? []).compactMap { o in
            guard let u = o["uri"] as? String ?? o["url"] as? String else { return nil }
            return SiteExternal(url: u, domain: o["domain"] as? String ?? SiteResearch.host(u), text: o["title"] as? String ?? o["text"] as? String ?? "")
        }
        let sum = json["summary"] as? [String: Any] ?? [:]
        var c: [String: Int] = [:]
        for k in ["read", "empty", "blocked", "error", "skipped", "duplicate"] { c[k] = sum[k] as? Int ?? 0 }
        counts = c
        tokens = sum["tokens"] as? Int ?? children.reduce(0) { $0 + $1.tokens }
        var st: [String: (state: String, note: String)] = [:]
        for o in json["pages"] as? [[String: Any]] ?? [] {
            if let u = o["url"] as? String { st[u] = (o["status"] as? String ?? "", o["note"] as? String ?? "") }
        }
        states = st
    }

    public init(children: [SiteChild] = [], suggested: [SiteExternal] = [], counts: [String: Int] = [:], tokens: Int = 0,
                states: [String: (state: String, note: String)] = [:]) {
        self.children = children; self.suggested = suggested; self.counts = counts; self.tokens = tokens; self.states = states
    }

    /// What was read, rebuilt from the progress lines (`stage: "page"`: the page's row and its source) when the sidecar
    /// was ended before it printed the report. nil when no page was reported.
    public init?(progress lines: [[String: Any]], url: String) {
        var rows: [[String: Any]] = [], sources: [[String: Any]] = []
        for l in lines {
            guard let p = l["progress"] as? [String: Any], p["stage"] as? String == "page", let r = p["row"] as? [String: Any] else { continue }
            rows.append(r)
            if let s = p["source"] as? [String: Any] { sources.append(s) }
        }
        guard !rows.isEmpty else { return nil }
        var sum: [String: Any] = [:]
        for k in ["read", "empty", "blocked", "error", "skipped", "duplicate"] { sum[k] = rows.filter { $0["status"] as? String == k }.count }
        sum["tokens"] = rows.filter { $0["status"] as? String == "read" }.reduce(0) { $0 + ($1["tokens"] as? Int ?? 0) }
        self.init(json: ["url": url, "sources": sources, "pages": rows, "summary": sum])
    }

    public static func == (a: SiteReadReport, b: SiteReadReport) -> Bool {
        a.children == b.children && a.suggested == b.suggested && a.counts == b.counts && a.tokens == b.tokens
    }

    /// Pages that were taken into the sheet: read, and not an empty placeholder.
    public var usable: [SiteChild] { children.filter { $0.used != "not_used" } }
}

public enum SiteResearch {
    public static let defaultBudget = 15
    /// The sidecar's hard cap (`max(1, min(max_pages, 30))` in web.py).
    public static let maxBudget = 30
    /// The estimate of one page until it is read (spec criterion 11).
    public static let pageEstimateTokens = 1500

    /// `webplan` request. The sidecar plans up to the hard cap so the stepper in the panel can move the tick line
    /// without planning again; the default budget is applied here.
    public static func planRequest(url: String, goal: String, callType: String, audience: String, route: String) -> [String: Any] {
        ["url": url, "depth": 2, "max_pages": maxBudget, "respect_robots": true,
         "goal": goal, "call_type": callType, "audience": audience, "route": route]
    }

    /// `webfetch` request: one call for all ticked pages. `progress`: the sidecar prints one JSON line per page
    /// (`{"progress": {"stage": "start" | "page", ...}}`) and the report as the last line. `cancel_path`: a file the app
    /// creates to stop; the sidecar looks for it between pages and still returns the report of what it read.
    public static func fetchRequest(plan: SitePlan, outDir: String, cancelPath: String) -> [String: Any] {
        ["plan": plan.fetchPlan(), "out_dir": outDir, "delay": 1.0, "cancel_path": cancelPath, "progress": true]
    }

    /// The page being fetched, from a progress line ("start"); nil for any other line.
    public static func progressStart(_ line: [String: Any]) -> (path: String, n: Int)? {
        guard let p = line["progress"] as? [String: Any], p["stage"] as? String == "start", let path = p["path"] as? String else { return nil }
        return (path, p["n"] as? Int ?? 0)
    }

    /// USD per million input tokens of the route's usual preparation model, from `spikes/m0/livetips_m0/cost.py`
    /// (checked 2026-10-21 there). nil = a subscription or account route: it has no price per token.
    public static func inputPrice(route: String) -> Double? {
        switch route {
        case "api": return 2.0         // sonnet-5-5
        case "openai": return 1.0      // gpt-6.1-terra
        case "gemini": return 0.75     // gemini-3.8-flash
        case "local": return 0
        default: return nil            // code, codex, agy
        }
    }

    public static func cost(tokens: Int, route: String, window: Int) -> SiteCost {
        guard let price = inputPrice(route: route) else {
            return .window(tokens: tokens, percent: window > 0 ? Int((Double(tokens) / Double(window) * 100).rounded()) : 0)
        }
        return price == 0 ? .free(tokens: tokens) : .dollars(tokens: tokens, usd: Double(tokens) / 1_000_000 * price)
    }

    /// The key of a page for "one source per page": the host in lower case without `www.`, no fragment, no trailing slash.
    public static func key(_ url: String) -> String {
        guard var c = URLComponents(string: url.trimmingCharacters(in: .whitespacesAndNewlines)), let h = c.host else { return url }
        c.fragment = nil
        var p = c.path
        while p.count > 1 && p.hasSuffix("/") { p.removeLast() }
        let host = h.lowercased().hasPrefix("www.") ? String(h.lowercased().dropFirst(4)) : h.lowercased()
        return host + (p == "/" ? "" : p) + (c.query.map { "?" + $0 } ?? "")
    }

    /// A website row (no parent) whose pages were read: its pages are the sources, so it is not fetched again.
    /// A child never counts, even the start page that has the same URL as the site.
    public static func hasPagesRead(uri: String?, parentURI: String?, among rows: [(uri: String?, parentURI: String?)]) -> Bool {
        guard parentURI == nil, let uri else { return false }
        return rows.contains { $0.parentURI == uri }
    }

    public enum ErrorKind: Equatable { case timeout, http(String), other }

    /// What an engine message says, so the panel can show its own localized text and never the English string.
    public static func errorKind(_ raw: String) -> ErrorKind {
        let l = raw.lowercased()
        if let r = l.range(of: "http ") {
            let code = l[r.upperBound...].prefix { $0.isNumber }
            if code.count == 3 { return .http(String(code)) }
        }
        if l.contains("did not answer") || l.contains("timed out") || l.contains("timeout") { return .timeout }
        return .other
    }

    public static func host(_ url: String) -> String { URLComponents(string: url)?.host ?? url }

    /// A tick-and-reason line for a failed or empty state, as a key suffix for `sheet.ui.web.state.<key>`.
    /// A page the sidecar refused because its address is private or not public (reported as blocked, but not by robots.txt).
    public static func isPrivateRefusal(state: String, note: String) -> Bool { state == "blocked" && note.hasPrefix("private or non-public") }

    public static func stateKey(_ s: String) -> String { ["read", "empty", "blocked", "error", "skipped", "duplicate"].contains(s) ? s : "error" }
}
