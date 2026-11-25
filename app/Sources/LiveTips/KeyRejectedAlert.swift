import LiveTipsCore
import SwiftUI

/// PB-97 (owner 25.11): an API key the provider rejected (401 or 403, in its check or a real run) is shown without
/// opening the key's details: a red banner on Home and on top of Settings > AI, a row in the menu bar panel and a red
/// dot on the menu bar icon (MenuBar). Each has Fix, which opens the key in Settings > AI. The alert follows the state
/// (AIStatusModel.rejected), so it cannot be closed while the key is rejected and goes once the key passes its check.
enum KeyRejectedAlert {
    /// The rejected routes in the order of Settings > AI.
    static func routes(_ rej: [String: Int]) -> [String] { AIChain.defaultOrder.filter { rej[$0] != nil } }

    /// "OpenAI API: key rejected (401). Pacenote does not use it until the key passes a check."
    static func text(_ r: String, _ rej: [String: Int]) -> String {
        L("ai.key.alert", ["route": AIRoute.name(r), "state": AIRoute.rejectedState(r, rej) ?? L("ai.key.rejected")])
    }

    /// Opens Settings > AI with the provider card of the key open.
    @MainActor static func fix(_ r: String) {
        SettingsWindow.show(tab: "ai")
        SettingsWindow.model.highlight = nil
        DispatchQueue.main.async { SettingsWindow.model.highlight = AIRoute.keyAnchor(r) }
    }
}

/// Home: one banner per rejected key, with Fix.
struct KeyRejectedHomeLine: View {
    @ObservedObject private var m = AIStatusModel.shared
    var body: some View {
        ForEach(KeyRejectedAlert.routes(m.rejected), id: \.self) { r in
            HomeBanner(icon: "!", title: KeyRejectedAlert.text(r, m.rejected), sub: L("ai.key.alertSub"),
                       edge: PN.C.rec, iconFill: PN.C.rec, iconInk: .white, go: L("ai.key.fix")) { KeyRejectedAlert.fix(r) }
        }
    }
}

/// Settings > AI: the first section while a key is rejected.
struct KeyRejectedSection: View {
    @ObservedObject private var m = AIStatusModel.shared
    var body: some View {
        if !m.rejected.isEmpty { Section { KeyRejectedRows() } }
    }
}

/// The menu bar panel, under the notices.
struct KeyRejectedPanelSection: View {
    @ObservedObject private var m = AIStatusModel.shared
    var relayout: () -> Void = {}
    var body: some View {
        VStack(alignment: .leading, spacing: 6) { KeyRejectedRows(compact: true) }
            .padding(.bottom, m.rejected.isEmpty ? 0 : 10)
            .onChange(of: m.rejected) { _, _ in DispatchQueue.main.async { relayout() } }
    }
}

/// Settings > AI (on top) and the menu bar panel: a red row per rejected key, with Fix.
struct KeyRejectedRows: View {
    @ObservedObject private var m = AIStatusModel.shared
    var compact = false
    var body: some View {
        ForEach(KeyRejectedAlert.routes(m.rejected), id: \.self) { r in
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "exclamationmark.octagon.fill").foregroundStyle(PN.C.recText)
                    .accessibilityLabel(L("notice.level.error"))
                Text(KeyRejectedAlert.text(r, m.rejected))
                    .font(compact ? PN.Font.caption : PN.Font.body).foregroundStyle(PN.C.recText)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button(L("ai.key.fix")) { KeyRejectedAlert.fix(r) }.buttonStyle(.pnTertiary)
            }
            .padding(.horizontal, compact ? 10 : 0).padding(.vertical, compact ? 7 : 2)
            .background(compact ? PN.C.recText.opacity(0.10) : .clear, in: RoundedRectangle(cornerRadius: PN.Radius.s))
            .accessibilityElement(children: .contain)
        }
    }
}
