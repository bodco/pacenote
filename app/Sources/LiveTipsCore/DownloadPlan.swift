import Foundation

public enum DownloadPlan {
    public static func requiresAppleSilicon(_ steps: [String]) -> Bool {
        steps.contains("whisper")
    }

    /// Ollama can keep writing briefly after the pull connection closes.
    /// A scan returns the number of cleanup candidates, or nil on a filesystem error.
    public static func recheckOllamaCleanup(_ scan: () -> Int?) async -> Bool {
        var quietScans = 0
        for _ in 0..<6 {
            do { try await Task.sleep(nanoseconds: 500_000_000) }
            catch { return false }
            quietScans = scan() == 0 ? quietScans + 1 : 0
            if quietScans == 2 { return true }
        }
        return false
    }

    /// Ollama names a blob by its digest and its partial chunks by numeric suffix.
    public static func ollamaCleanup(files: [String], existing: Set<String>,
                                     digests: Set<String>, referenced: Set<String>) -> [String] {
        let blobs = Set(digests.compactMap { digest -> String? in
            let hex = digest.dropFirst(7)
            guard digest.hasPrefix("sha256:"), hex.utf8.count == 64,
                  hex.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { return nil }
            return "sha256-" + hex
        }).subtracting(referenced)

        return files.filter { file in
            let parts = file.components(separatedBy: "-partial")
            guard let blob = parts.first, blobs.contains(blob) else { return false }
            if parts.count == 1 { return !existing.contains(file) }
            guard parts.count == 2, let suffix = parts.last else { return false }
            return suffix.isEmpty || (suffix.hasPrefix("-") && (Int(suffix.dropFirst()) ?? -1) >= 0)
        }
    }
}
