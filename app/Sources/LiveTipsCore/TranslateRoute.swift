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
}
