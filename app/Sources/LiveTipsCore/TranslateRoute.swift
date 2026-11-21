import Foundation

/// PB-30 (D56): what the app tells the recorder about the AI route for the background translation of a line.
/// Text only. The command runs the tips engine's `livetips_m0.translate_text`, which uses the route, the auto-switch
/// and the spend ledger of the app; the recorder takes the command from config.json only, never from a call request.
public enum TranslateRoute {
    /// The route is on for this call: the call may use the AI and the tips engine is installed.
    public static func enabled(allowed: Bool, python: String, dir: String) -> Bool {
        allowed && !python.isEmpty && !dir.isEmpty
    }

    /// The keys for the recorder's config.json; empty values switch the route off (the local model still works).
    public static func coreKeys(allowed: Bool, python: String, dir: String) -> [String: Any] {
        guard enabled(allowed: allowed, python: python, dir: dir) else { return ["translate_cmd": [String](), "translate_dir": ""] }
        return ["translate_cmd": [python, "-m", "livetips_m0.translate_text"], "translate_dir": dir]
    }

    /// Keys of the AI route and its credentials: the recorder never inherits them when the call may not use the AI.
    static let routeKeys = ["CLAUDE_API", "ANTHROPIC_API_KEY", "CLAUDE_WORKSPACE", "OPENAI_API_KEY", "GEMINI_API_KEY",
                            "LT_AI_ROUTE", "LT_AI_CHAIN", "LT_CLAUDE_CLI", "LT_CODEX_CLI", "LT_AGY_CLI", "LT_AI_AUTO"]

    /// The recorder's whole environment for one launch. `LT_TRANSLATE_ROUTE` is the per-launch permission: "1" only
    /// when allowed (then `route` is the tips way's environment), else "0" and no key or route setting is passed on,
    /// so neither a stale command in config.json nor an inherited key can send a line out.
    public static func recorderEnv(allowed: Bool, base: [String: String], route: [String: String]) -> [String: String] {
        guard allowed else {
            var env = base
            for k in routeKeys { env.removeValue(forKey: k) }
            env["LT_TRANSLATE_ROUTE"] = "0"
            return env
        }
        var env = route
        env["LT_TRANSLATE_ROUTE"] = "1"
        return env
    }
}
