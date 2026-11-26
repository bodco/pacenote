import CryptoKit
import Foundation

/// PB-127 (owner 25.11): the tag of one call in the local spend ledger (LT_CALL), so the after-call model requests of a
/// call can be counted (livetips_m0.routing.call_model_calls). A short hash of the transcript's file name: never the
/// name itself (it may hold a client or meeting title), the same for the .md and .named.md copy and after the call moves
/// to another project. Local only: never sent in analytics.
public enum CallTag {
    public static func of(_ file: String) -> String {
        var name = (file as NSString).lastPathComponent
        for s in [".named.md", ".md"] where name.hasSuffix(s) { name = String(name.dropLast(s.count)); break }
        return SHA256.hash(data: Data(name.utf8)).prefix(6).map { String(format: "%02x", $0) }.joined()
    }

    /// The tag of the call a tool run is for: the value after `--md` (notes, Notion page, follow-up) or `--call-file`
    /// (decisions and risks to Notion); none without either. `--call` is the call's title, not its file.
    public static func of(args: [String]) -> String? {
        for flag in ["--md", "--call-file"] {
            if let i = args.firstIndex(of: flag), i + 1 < args.count, !args[i + 1].isEmpty { return of(args[i + 1]) }
        }
        return nil
    }
}
