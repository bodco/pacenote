import AppKit
import Foundation
import LiveTipsCore

/// 0.8.5 (owner: "Install Gemma" and local models in every model list): a model on this Mac through Ollama
/// (rules/ai.yaml > routes.local). Ollama itself comes from ollama.com (the app is not bundled); the app starts it,
/// lists its models and downloads one with progress through Ollama's own API (/api/tags, /api/pull).
@MainActor
final class LocalLLM: ObservableObject {
    static let shared = LocalLLM()
    static let url = "http://127.0.0.1:11434"
    /// The model the app suggests: about 16 GB, fits a 32 GB Mac (docs 14.1g).
    static let suggested = "gemma4:26b"
    static let suggestedLabel = "Gemma 4 26B"

    @Published private(set) var installed = false      // the Ollama app is on this Mac
    @Published private(set) var running = false
    @Published private(set) var models: [String] = []         // models that write text (the job lists)
    @Published private(set) var searchModels: [String] = []   // embedding models (bge-m3 ...): Local AI search only
    @Published private(set) var pulling: String? = nil
    @Published private(set) var progress: Double = 0    // 0...1 while pulling
    @Published private(set) var note = ""
    @Published private(set) var stopping = false
    private var pullTask: Task<Void, Never>?
    private var pullRequest: URLSessionDataTask?
    private var confirming = false

    private nonisolated static var modelDirectory: URL {
        URL(fileURLWithPath: ProcessInfo.processInfo.environment["OLLAMA_MODELS"] ?? (NSHomeDirectory() + "/.ollama/models"))
    }

    /// The chosen local model, shared by the preparation and the after-the-call jobs (one key since 0.8.5).
    static var model: String {
        get { UserDefaults.standard.string(forKey: "prepLocalModel") ?? suggested }
        set { UserDefaults.standard.set(newValue, forKey: "prepLocalModel") }
    }

    private init() { refresh() }

    static var appPath: String? {
        ["/Applications/Ollama.app", NSHomeDirectory() + "/Applications/Ollama.app"].first { FileManager.default.fileExists(atPath: $0) }
    }

    func refresh() {
        installed = Self.appPath != nil || FileManager.default.fileExists(atPath: "/usr/local/bin/ollama")
            || FileManager.default.fileExists(atPath: "/opt/homebrew/bin/ollama")
        var req = URLRequest(url: URL(string: Self.url + "/api/tags")!); req.timeoutInterval = 3
        URLSession.shared.dataTask(with: req) { d, _, e in
            let names = (d.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }?["models"] as? [[String: Any]])?
                .compactMap { $0["name"] as? String }.map { $0.hasSuffix(":latest") ? String($0.dropLast(7)) : $0 }
            DispatchQueue.main.async { MainActor.assumeIsolated {
                self.running = e == nil
                // 0.8.6: while Ollama is stopped, the models already downloaded still count (it starts on demand)
                let all = names ?? LocalLLM.downloaded()
                let isSearch = { (n: String) in n.contains("embed") || n.hasPrefix("bge") }
                self.models = all.filter { !isSearch($0) }.sorted()
                self.searchModels = all.filter(isSearch).sorted()
            } }
        }.resume()
    }

    var hasSuggested: Bool { models.contains(Self.suggested) }

    /// Every model downloaded into Ollama's folder ("name:tag", ":latest" dropped), read from its manifests.
    nonisolated static func downloaded() -> [String] {
        let base = (ProcessInfo.processInfo.environment["OLLAMA_MODELS"] ?? (NSHomeDirectory() + "/.ollama/models")) + "/manifests/registry.ollama.ai/library"
        let fm = FileManager.default
        var out: [String] = []
        for m in (try? fm.contentsOfDirectory(atPath: base)) ?? [] where !m.hasPrefix(".") {
            for t in (try? fm.contentsOfDirectory(atPath: base + "/" + m)) ?? [] where !t.hasPrefix(".") {
                out.append(t == "latest" ? m : m + ":" + t)
            }
        }
        return out.sorted()
    }

    /// 0.8.6 (owner: "I installed Gemma yesterday"): a model already downloaded counts even while Ollama is not running
    /// (its manifest under ~/.ollama/models), so Install never downloads 16 GB a second time.
    nonisolated static func onDisk(_ name: String) -> Bool {
        let parts = name.split(separator: ":", maxSplits: 1).map(String.init)
        let model = parts.first ?? name, tag = parts.count > 1 ? parts[1] : "latest"
        let base = ProcessInfo.processInfo.environment["OLLAMA_MODELS"] ?? (NSHomeDirectory() + "/.ollama/models")
        let lib = model.contains("/") ? model : "library/" + model
        return FileManager.default.fileExists(atPath: base + "/manifests/registry.ollama.ai/" + lib + "/" + tag)
    }

    /// Opens the Ollama app in the background (it serves on 127.0.0.1:11434), then refreshes.
    func start() {
        guard let p = Self.appPath else { NSWorkspace.shared.open(URL(string: "https://ollama.com/download")!); return }
        NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: p), configuration: {
            let c = NSWorkspace.OpenConfiguration(); c.activates = false; return c }()) { _, _ in }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.refresh() }
    }

    func cancelPull() {
        guard let pullTask, !stopping else { return }
        stopping = true
        note = L("download.stopping")
        pullTask.cancel()
        pullRequest?.cancel()
    }

    /// Downloads a model through Ollama with progress; it then shows in every local model list.
    func install(_ name: String = suggested) async {
        guard pulling == nil, !confirming, !Self.onDisk(name), !models.contains(name), !searchModels.contains(name) else { return }
        confirming = true
        defer { confirming = false }
        let bytes = name == Self.suggested ? ComponentCatalog.estimate("local_model") : nil
        guard await DownloadPreflight.confirm(steps: ["local_model"], bytes: bytes,
            free: DiskSpace.free(at: Self.modelDirectory), purpose: L("download.localOllama")) else { return }

        guard !Self.onDisk(name), !models.contains(name), !searchModels.contains(name) else { return }
        let blobs = Self.modelDirectory.appendingPathComponent("blobs")
        let fm = FileManager.default
        let existing = try? Set(fm.contentsOfDirectory(atPath: blobs.path))
        let baseline = existing ?? (fm.fileExists(atPath: blobs.path) ? nil : [])
        if !running { start() }
        pulling = name
        progress = 0
        note = L("local.pull.starting")
        var req = URLRequest(url: URL(string: Self.url + "/api/pull")!)
        req.httpMethod = "POST"; req.timeoutInterval = 6 * 3600
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["name": name, "stream": true])
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let task = Task {
            var digests = Set<String>()
            var succeeded = false
            var problem: String?
            do {
                let bytes = try await Self.pullStream(req)
                pullRequest = bytes.task
                defer { bytes.task.cancel(); pullRequest = nil }
                try Task.checkCancellation()
                for try await line in bytes.lines {
                    try Task.checkCancellation()
                    guard let event = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else { continue }
                    if let error = event["error"] as? String { problem = error; break }
                    if let digest = event["digest"] as? String { digests.insert(digest) }
                    let total = (event["total"] as? NSNumber)?.doubleValue ?? 0
                    let done = (event["completed"] as? NSNumber)?.doubleValue ?? 0
                    let status = event["status"] as? String ?? ""
                    if status == "success" { succeeded = true; break }
                    if total > 0 { progress = min(1, done / total) }
                    note = total > 0 ? String(format: "%.1f / %.1f GB", done / 1e9, total / 1e9) : status
                }
            } catch { problem = error.localizedDescription }

            if Task.isCancelled || !succeeded {
                // Cleanup needs its own task because networking in the cancelled pull task would cancel immediately.
                let cleaned = await Task {
                    let deleted = await self.deleteModel(name, attempts: 2, timeout: 5)
                    guard let baseline else { return false }
                    let filesRemoved = await Task.detached { Self.cleanBlobs(blobs, existing: baseline, digests: digests) }.value
                    return deleted && filesRemoved
                }.value
                if Task.isCancelled {
                    note = L(cleaned ? "download.cancelled" : "download.cleanupFailed")
                } else {
                    let error = problem ?? L("download.incomplete")
                    note = L("local.pull.failed", ["err": error])
                    AnalyticsTracker.shared.error("local_llm", Analytics.errorCode(message: error), provider: "local")
                }
            } else {
                Self.model = name
                note = L("local.pull.done")
            }
            pulling = nil
            pullTask = nil
            stopping = false
            refresh()
        }
        pullTask = task
        await task.value
    }

    private static func pullStream(_ request: URLRequest) async throws -> URLSession.AsyncBytes {
        // Retry connection errors while Ollama is starting, but never start a second accepted pull.
        for attempt in 0..<10 {
            try Task.checkCancellation()
            do {
                let (bytes, response) = try await URLSession.shared.bytes(for: request)
                if (response as? HTTPURLResponse)?.statusCode == 200 { return bytes }
                bytes.task.cancel()
                throw URLError(.badServerResponse)
            } catch let error as URLError where error.code == .cannotConnectToHost && attempt < 9 {
                try await Task.sleep(nanoseconds: 1_500_000_000)
            }
        }
        throw URLError(.cannotConnectToHost)
    }

    private nonisolated static func cleanBlobs(_ directory: URL, existing: Set<String>, digests: Set<String>) -> Bool {
        if digests.isEmpty { return true }
        let fm = FileManager.default
        let manifests = directory.deletingLastPathComponent().appendingPathComponent("manifests")
        var referenced = Set<String>()
        if fm.fileExists(atPath: manifests.path) {
            var unreadable = false
            guard let entries = fm.enumerator(at: manifests, includingPropertiesForKeys: [.isRegularFileKey],
                                              options: [.skipsHiddenFiles], errorHandler: { _, _ in unreadable = true; return false })
            else { return false }
            for case let url as URL in entries {
                guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey]) else { return false }
                guard values.isRegularFile == true else { continue }
                guard let data = try? Data(contentsOf: url),
                      let manifest = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                      let layers = manifest["layers"] as? [[String: Any]] else { return false }
                let entries = layers + [manifest["config"] as? [String: Any] ?? [:]]
                referenced.formUnion(entries.compactMap { ($0["digest"] as? String)?.replacingOccurrences(of: ":", with: "-") })
            }
            if unreadable { return false }
        }
        guard let files = try? fm.contentsOfDirectory(atPath: directory.path) else {
            return !fm.fileExists(atPath: directory.path)
        }
        var ok = true
        for file in DownloadPlan.ollamaCleanup(files: files, existing: existing, digests: digests, referenced: referenced) {
            let path = directory.appendingPathComponent(file)
            do { try fm.removeItem(at: path) }
            catch { if fm.fileExists(atPath: path.path) { ok = false } }
        }
        return ok
    }

    // MARK: 0.16.0 (D3): size and removal (Settings > Components)

    /// The model's size on disk from its Ollama manifest (the config and every layer), nil when it is not downloaded.
    nonisolated static func sizeOnDisk(_ name: String) -> Int64? {
        let parts = name.split(separator: ":", maxSplits: 1).map(String.init)
        let model = parts.first ?? name, tag = parts.count > 1 ? parts[1] : "latest"
        let base = ProcessInfo.processInfo.environment["OLLAMA_MODELS"] ?? (NSHomeDirectory() + "/.ollama/models")
        let lib = model.contains("/") ? model : "library/" + model
        guard let d = FileManager.default.contents(atPath: base + "/manifests/registry.ollama.ai/" + lib + "/" + tag),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return nil }
        var total: Int64 = ((o["config"] as? [String: Any])?["size"] as? NSNumber)?.int64Value ?? 0
        for l in (o["layers"] as? [[String: Any]]) ?? [] { total += (l["size"] as? NSNumber)?.int64Value ?? 0 }
        return total
    }

    /// Removes a downloaded model through Ollama (the same as `ollama rm`): starts Ollama if needed, then
    /// DELETE /api/delete. Ollama itself stays: the PM installed it and other apps may use it.
    func remove(_ name: String) async -> Bool {
        guard pulling == nil, !confirming else { return false }
        if !running { start() }
        let ok = await deleteModel(name, attempts: 15, timeout: 120)
        refresh()
        return ok
    }

    private func deleteModel(_ name: String, attempts: Int, timeout: TimeInterval) async -> Bool {
        var req = URLRequest(url: URL(string: Self.url + "/api/delete")!)
        req.httpMethod = "DELETE"; req.timeoutInterval = timeout
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": name, "name": name])
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for attempt in 0..<attempts {
            if let (_, r) = try? await URLSession.shared.data(for: req), let code = (r as? HTTPURLResponse)?.statusCode {
                if code == 200 { return true }
                if code == 404 { return !Self.onDisk(name) }
                return false
            }
            if attempt + 1 < attempts { try? await Task.sleep(nanoseconds: 2_000_000_000) }
        }
        return false
    }
}
