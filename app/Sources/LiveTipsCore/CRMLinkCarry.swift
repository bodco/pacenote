import Foundation

/// H2 (PB-58): Refresh in Start call saves the HubSpot link with the preparation (<prep>.crm.json). The recorded call
/// reads it under its own stem, so the link goes with the preparation's files when the call starts; without it Auto
/// skipped the call and Update HubSpot said "not linked". Fix round 6: a title or a time never proves which call a
/// preparation is for, so a link goes to a call only when its identity is verified: the PM chose the preparation by
/// hand, or Refresh saved the link for this very calendar occurrence. Otherwise the call stays unlinked.
public enum CRMLinkCarry {
    /// Copies <base>.crm.json to <stem>.crm.json when the call has no link yet and the link is verified for this call
    /// (see verified). The call key and the HubSpot Call id are left out: they belong to one call, and a preparation
    /// used again for another call must not update that Call.
    @discardableResult
    public static func carry(from base: String, to stem: String, occurrence: String, chosen: Bool, fm: FileManager = .default) -> Bool {
        let src = CallLayout.sidecar(base, ".crm.json"), dst = CallLayout.sidecar(stem, ".crm.json")
        guard src != dst, !fm.fileExists(atPath: dst), verified(base: base, occurrence: occurrence, chosen: chosen, fm: fm),
              let data = fm.contents(atPath: src),
              var obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return false }
        obj.removeValue(forKey: "callKey"); obj.removeValue(forKey: "callId")
        guard let out = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else { return false }
        try? fm.createDirectory(atPath: (dst as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        return (try? out.write(to: URL(fileURLWithPath: dst), options: .atomic)) != nil
    }

    /// The link at this preparation belongs to the call: the PM chose the preparation by hand in Start call, or the link
    /// carries the calendar occurrence (event id + start) Refresh saved it for and it is this call's occurrence. A call
    /// without a calendar event has no occurrence, so only a preparation chosen by hand links it.
    public static func verified(base: String, occurrence: String, chosen: Bool, fm: FileManager = .default) -> Bool {
        if chosen { return true }
        guard !occurrence.isEmpty, let d = stored(base: base, fm: fm),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return false }
        return o["occurrence"] as? String == occurrence
    }

    /// The link saved with this preparation, read when Refresh or a deal change runs (fix round 5): the link shown for
    /// a preparation picked before must not be saved into the one picked now. nil when it has none.
    public static func stored(base: String, fm: FileManager = .default) -> Data? {
        fm.contents(atPath: CallLayout.sidecar(base, ".crm.json"))
    }

    /// The preparation Refresh saves the link with, picked the way Start call picks the one it copies to the call: the
    /// prep chosen by hand (its brief or Meeting Sheet) first, else the planned one. nil when neither is known.
    public static func prepBase(chosen: String?, planned: String?) -> String? {
        if let c = chosen, !c.isEmpty {
            for ext in [".brief.md", ".sheet.json"] where c.hasSuffix(ext) { return String(c.dropLast(ext.count)) }
        }
        return planned
    }
}
