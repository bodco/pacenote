import AppKit
import LiveTipsCore

enum DiskSpace {
    static var free: Int64 { free(at: URL(fileURLWithPath: NSHomeDirectory())) }

    static func free(at url: URL) -> Int64 {
        var folder = url
        while !FileManager.default.fileExists(atPath: folder.path), folder.path != "/" {
            folder.deleteLastPathComponent()
        }
        return (try? folder.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage ?? 0
    }
}

@MainActor
enum DownloadPreflight {
    private static var presenting = false

    static func confirm(steps: [String], bytes: Int64?, free: Int64, purpose: String = "") async -> Bool {
        guard !presenting else { return false }
        presenting = true
        defer { presenting = false }
        var hardware: Int32 = 0
        var size = MemoryLayout<Int32>.size
        sysctlbyname("hw.optional.arm64", &hardware, &size, nil, 0)
        let unsupported = DownloadPlan.requiresAppleSilicon(steps) && hardware != 1
        let lowSpace = bytes.map { $0 > free } ?? false
        let alert = NSAlert()
        alert.messageText = L("download.title")
        let amounts = L("download.space", ["size": bytes.map(ComponentCatalog.format) ?? L("download.unknownSize"),
                                          "free": ComponentCatalog.format(free)])
        alert.informativeText = [amounts, purpose, unsupported ? L("download.appleSilicon") : "",
                                 lowSpace ? L("download.lowSpace") : ""].filter { !$0.isEmpty }.joined(separator: "\n")
        let blocked = unsupported || lowSpace
        if !blocked { alert.addButton(withTitle: L("comp.install")) }
        alert.addButton(withTitle: L("common.cancel"))
        let response: NSApplication.ModalResponse
        if let window = NSApp.keyWindow, window.attachedSheet == nil {
            response = await alert.beginSheetModal(for: window)
        } else {
            response = alert.runModal()
        }
        return !blocked && response == .alertFirstButtonReturn
    }
}
