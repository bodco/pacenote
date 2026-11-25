import Foundation

/// PB-89 (owner 25.11): the preparation signal of call_started and call_ended (rules/analytics.yaml). Until this
/// release has_brief read a "brief" key no call request ever had, so it was always false.
public enum CallBriefSignal {
    /// A preparation was chosen at the start: the request names its file (prep_file). The Start call window fills it
    /// with the PM's pick or with the preparation it found for this call by title and time.
    public static func chosen(_ req: [String: Any]) -> Bool {
        !((req["prep_file"] as? String) ?? "").isEmpty
    }

    /// The Meeting Sheet mode of the chosen preparation (its <base>.sheet.json "mode"): quick, expert, or none when
    /// the call has no sheet.
    public static func sheet(_ req: [String: Any], fm: FileManager = .default) -> String {
        guard let chosen = req["prep_file"] as? String, chosen.hasSuffix(".brief.md") || chosen.hasSuffix(".sheet.json") else { return "none" }
        let base = String(chosen.dropLast(chosen.hasSuffix(".sheet.json") ? ".sheet.json".count : ".brief.md".count))
        guard let d = fm.contents(atPath: CallLayout.sidecar(base, ".sheet.json")),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
              let m = o["mode"] as? String, ["quick", "expert"].contains(m) else { return "none" }
        return m
    }

    /// call_ended.brief from the call's own sidecar files (<stem>.brief.md or <stem>.sheet.json next to the
    /// transcript): none when neither is there; late when a preparation finished during the call replaced them;
    /// chosen when the start named one; auto when the engine picked one up by itself (lt prep-check --adopt-prep).
    public static func atEnd(transcript: String?, chosen: Bool, late: Bool, fm: FileManager = .default) -> String {
        guard let f = transcript, !f.isEmpty else { return "none" }
        let stem = f.hasSuffix(".md") ? String(f.dropLast(3)) : f
        let has = [".brief.md", ".sheet.json"].contains { fm.fileExists(atPath: CallLayout.sidecar(stem, $0)) }
        guard has else { return "none" }
        return late ? "late" : chosen ? "chosen" : "auto"
    }
}
