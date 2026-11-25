import Foundation

/// PB-111 (SX4): one sales method per call. The method chosen in Get ready is the value the preparation, the Meeting
/// Sheet block Qualification, the live tips and the after-call summary all get, unchanged. Sales off: no method.
public enum SalesMethod {
    /// rules/sales.yaml > default_method; rules/meeting-sheet.yaml > qualification.default_method is the same key
    /// (pytest test_sales_method_pb111 keeps the three equal).
    public static let defaultKey = "general"

    /// The call's method: nil when Sales is off, else the chosen key, else the default.
    public static func forCall(enabled: Bool, chosen: String) -> String? {
        guard enabled else { return nil }
        return chosen.isEmpty ? defaultKey : chosen
    }

    /// The method of a Qualification block: the call's method when Sales is on, else the preset's, else the default.
    public static func forBlock(call: String?, preset: String?) -> String {
        call ?? preset ?? defaultKey
    }

    /// The method the preparation request carries: the call's own method wins over the one the Get ready wizard
    /// saved earlier (the call's method may have changed since); with Sales off the wizard's block method stays.
    public static func forPrep(call: String?, wizard: String?) -> String? {
        call ?? wizard
    }
}
