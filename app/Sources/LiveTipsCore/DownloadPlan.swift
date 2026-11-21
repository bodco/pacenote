import Foundation

public enum DownloadPlan {
    public static func requiresAppleSilicon(_ steps: [String]) -> Bool {
        steps.contains("whisper")
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
            guard !existing.contains(file) else { return false }
            let parts = file.components(separatedBy: "-partial")
            guard let blob = parts.first, blobs.contains(blob) else { return false }
            if parts.count == 1 { return true }
            guard parts.count == 2, let suffix = parts.last else { return false }
            return suffix.isEmpty || (suffix.hasPrefix("-") && (Int(suffix.dropFirst()) ?? -1) >= 0)
        }
    }
}
