import Foundation

/// 0.24.1 (F3, owner 18.11): the AI of each job follows what is connected. Whatever AI is connected does the
/// preparation and the summary; when an account (Claude or ChatGPT) gets connected it becomes the default for both;
/// until then the job runs on what there is. A choice the PM made stays while its AI is connected. Pure, for CoreChecks.
public enum AIDefaults {
    /// The accounts, in the order a newly connected one is adopted (Claude first when both arrive at once).
    public static let accounts = ["code", "codex"]

    /// The order a job looks for a connected AI when its own is not connected. Tips: the fast API keys first;
    /// preparation and after the call: the accounts first (no per-token bill), then the keys, then the slow ones.
    public static func preference(job: String) -> [String] {
        job == "tips" ? ["api", "openai", "gemini", "code", "codex"]
                      : ["code", "codex", "api", "openai", "gemini", "local"]
    }

    /// The AI the job uses: the chosen one while it is connected (and allowed for the job), else the first connected
    /// one by `preference`; with nothing connected the choice stays as it is.
    public static func pick(job: String, chosen: String, connected: Set<String>) -> String {
        if connected.contains(chosen) && AIChain.allowed(chosen, job: job) { return chosen }
        return preference(job: job).first { connected.contains($0) && AIChain.allowed($0, job: job) } ?? chosen
    }

    /// The routes the jobs run with (AIRoute.effective): each job on its AI while connected, else `pick`.
    /// PB-52 G2 (S5, spec 8.6): with the evidence_store flag on (`holdIndexing`), indexing is first held on the AI it
    /// is set to, chosen or the after-call AI it follows, so replacing the after-call AI never moves project text to
    /// another AI; a not connected indexing AI is left to the engine (the model on this Mac or off).
    public static func runtime(_ r: AppConfig.AIRoutes, connected: Set<String>, holdIndexing: Bool) -> AppConfig.AIRoutes {
        var out = r
        if holdIndexing { out.evidence = r.route("evidence") }
        out.prep = pick(job: "prep", chosen: r.prep, connected: connected)
        out.after = pick(job: "after", chosen: r.after, connected: connected)
        out.tips = pick(job: "tips", chosen: r.tips, connected: connected)
        return out
    }

    /// The routes after a change of what is connected. `seen` is what was connected the last time (nil: never looked,
    /// e.g. the first launch after the update: nothing is adopted then, only routes whose AI is not connected move).
    /// An account connected since the last look becomes the preparation and after-the-call AI.
    /// PB-96: `unchecked` are the accounts whose sign-in check has not finished (at launch): a job on one stays on it
    /// and it keeps its place in `seen`, so the check that follows moves nothing back and forth. An unchecked account
    /// is never picked or adopted.
    public static func apply(_ r: AppConfig.AIRoutes, connected: Set<String>, unchecked: Set<String> = []) -> AppConfig.AIRoutes {
        var out = r
        let wait = unchecked.subtracting(connected)
        if let seen = r.seen.map(Set.init) {
            if let acc = accounts.first(where: { connected.contains($0) && !seen.contains($0) }) {
                out.prep = acc
                out.after = acc
            }
        }
        func keep(_ job: String, _ chosen: String) -> String {
            wait.contains(chosen) && AIChain.allowed(chosen, job: job) ? chosen : pick(job: job, chosen: chosen, connected: connected)
        }
        out.prep = keep("prep", out.prep)
        out.after = keep("after", out.after)
        out.tips = keep("tips", out.tips)
        // an unchecked account stays seen when it was (or on the first look, which adopts nothing)
        let was = r.seen.map(Set.init)
        out.seen = AIChain.defaultOrder.filter { connected.contains($0) || (wait.contains($0) && (was?.contains($0) ?? true)) }
        return out
    }
}
