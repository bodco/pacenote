import AppKit
import LiveTipsCore
import SwiftUI

@MainActor
final class CodexAccount: ObservableObject {
    static let shared = CodexAccount()
    @Published var signedIn: Bool?
    @Published var checking = false

    nonisolated static var cliPath: String? {
        CodexCLI.candidates(home: NSHomeDirectory(), path: ProcessInfo.processInfo.environment["PATH"] ?? "")
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    func refresh() {
        guard !checking else { return }
        guard let cli = Self.cliPath else { signedIn = false; return }
        checking = true
        Task {
            let ok = await Task.detached(priority: .utility) {
                let process = Process(), out = Pipe()
                process.executableURL = URL(fileURLWithPath: cli)
                process.arguments = ["-c", "forced_login_method=\"chatgpt\"", "login", "status"]
                process.environment = CodexCLI.environment(ProcessInfo.processInfo.environment)
                process.standardOutput = out
                process.standardError = out
                let timer = DispatchWorkItem { if process.isRunning { process.terminate() } }
                do {
                    try process.run()
                    DispatchQueue.global().asyncAfter(deadline: .now() + 15, execute: timer)
                    let data = out.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    timer.cancel()
                    return CodexCLI.isChatGPT(status: process.terminationStatus, output: String(data: data, encoding: .utf8) ?? "")
                } catch { timer.cancel(); return false }
            }.value
            signedIn = ok
            checking = false
        }
    }

    /// 0.24.1 (F1): the sign-in runs from the app (AccountLogin), never Terminal.
    func login() { AccountLogin.shared.connect("codex") }
}
// PB-98: the Settings > AI row is AIAccountRow (AccountWatch.swift), shared with the Claude account.
