import Foundation

/// H2 (PB-58): Refresh in Start call saves the HubSpot link with the preparation (<prep>.crm.json). The recorded call
/// reads it under its own stem, so the link goes with the preparation's files when the call starts; without it Auto
/// skipped the call and Update HubSpot said "not linked".
public enum CRMLinkCarry {
    /// Copies <base>.crm.json to <stem>.crm.json when the call has no link yet. The call key and the HubSpot Call id are
    /// left out: they belong to one call, and a preparation used again for another call must not update that Call.
    @discardableResult
    public static func carry(from base: String, to stem: String, fm: FileManager = .default) -> Bool {
        let src = CallLayout.sidecar(base, ".crm.json"), dst = CallLayout.sidecar(stem, ".crm.json")
        guard src != dst, !fm.fileExists(atPath: dst),
              let data = fm.contents(atPath: src),
              var obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return false }
        obj.removeValue(forKey: "callKey"); obj.removeValue(forKey: "callId")
        guard let out = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else { return false }
        try? fm.createDirectory(atPath: (dst as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        return (try? out.write(to: URL(fileURLWithPath: dst), options: .atomic)) != nil
    }
}
