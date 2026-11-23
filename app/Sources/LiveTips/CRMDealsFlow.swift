import SwiftUI
import LiveTipsCore

// H4 (PB-60): the deal part of Update HubSpot. The deal the call is linked to (or one the PM picks), the suggested
// changes with the evidence from the call page, and the portal's own stages of the deal's pipeline. Applying goes
// through the existing Update HubSpot flow (one updateDeal call, Undo restores the old values).

typealias CRMDealModel = CRMDealPreview

extension CRMDealPreview {
    /// Reads the linked deal, the company's open deals and the portal's stages for the link's deal (the portal is
    /// checked first), then builds the suggestions from the call page (<stem>.sales.json as edited).
    @MainActor static func load(link: CRMLink, file: String) async -> CRMDealPreview {
        guard let c = CRMService.shared.connector() else { var m = CRMDealPreview(); m.dealId = link.dealId; m.loaded = true; return m }
        let sales = SalesFile.load(file) ?? [:]
        let mapping = SettingsWindow.model.config.crm.qualification[(sales["method"] as? String) ?? ""] ?? [:]
        return await load(connector: c, portalId: link.portalId, companyId: link.companyId, dealId: link.dealId, sales: sales, mapping: mapping)
    }
}

/// The deal the Call was associated with by Pacenote, so moving the call to another deal removes only that one.
extension CRMCallLogger {
    /// The Call write with the deal move (`CRMCallDeal.write`), in turn with Auto and Relink; the ids, the associations
    /// and the deal are read from <base>.crm.json when the turn comes, and stored only while the link still names the
    /// write's portal and call key (PB-58 fix round 8).
    /// H6 (PB-62): `allowed` is asked when the turn comes (Auto); a Retry's `bound` portal pins the target of the
    /// fallback and of the link read at the turn (CRMSyncLog.retryTarget); `attempt` keeps the portal the turn was
    /// checked against, for the sync line.
    static func writeWithDeal(_ entry: CRMCallLog, base: String, link: CRMLink, connector c: CRMConnector,
                              allowed: @escaping () async -> Bool = { true }, bound: String? = nil,
                              attempt: CRMSyncLog.Attempt = CRMSyncLog.Attempt()) async throws -> CRMCallDeal.Move {
        try await CRMCallDeal.write(entry, connector: c, fallback: (CRMSyncLog.retryTarget(bound: bound, target(link)), link.callDealId),
                                    allowed: allowed, checked: attempt.record,
                                    load: { CRMLink.load(base: base).map { (CRMSyncLog.retryTarget(bound: bound, target($0)), $0.callDealId) } },
                                    save: { id, managed in var d = CRMLink.load(base: base) ?? link; d.callId = id; d.callDealId = managed; d.save(base: base) })
    }
}

struct CRMDealSection: View {
    @Binding var model: CRMDealModel
    let dealName: String
    let setDeal: (String, String) -> Void
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(L("crm.deal.heading", ["deal": dealName.isEmpty ? L("crm.noDeal") : dealName])).font(PN.Font.caption.weight(.semibold))
                if !model.open.isEmpty {
                    Menu {
                        ForEach(model.open) { d in Button(d.name) { setDeal(d.id, d.name) } }
                        Divider()
                        Button(L("crm.noDeal")) { setDeal("none", "") }
                    } label: { Image(systemName: "chevron.down") }.menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                        .accessibilityLabel(L("crm.changeDeal"))
                }
                if !model.loaded { PNSpinner(size: 14) }
            }
            if model.loaded && !model.error.isEmpty && !model.portalMismatch {
                // a failed read is not "no open deals": say so and offer to read again
                HStack(spacing: 6) {
                    Text(L("crm.deal.loadFailed", ["error": model.error])).font(PN.Font.caption).foregroundStyle(PN.C.text3).lineLimit(2)
                    Button(L("crm.deal.retry")) { retry() }.buttonStyle(.pnTertiary)
                }
            }
            if model.deal != nil {
                if model.suggestions.isEmpty {
                    Text(L("crm.deal.none")).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                }
                ForEach(model.suggestions.indices.filter { model.suggestions[$0].kind != "stage" }, id: \.self) { i in
                    let s = model.suggestions[i]
                    // a new pick empties the list at once; a row still on screen must not read past its end
                    Toggle(isOn: Binding(get: { model.suggestions.indices.contains(i) ? model.suggestions[i].on : false },
                                         set: { if model.suggestions.indices.contains(i) { model.suggestions[i].on = $0 } })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(s.kind == "qualification" ? s.title.replacingOccurrences(of: "_", with: " ") + " (" + s.id + ")" : L("crm.deal.field." + s.kind))
                            Text(s.current.isEmpty ? s.shown : s.current + "  \u{2192}  " + s.shown).font(PN.Font.caption).foregroundStyle(PN.C.text2)
                            Text(L("crm.deal.evidence", ["quote": s.evidence])).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                        }
                    }
                    .toggleStyle(.pnCheckbox)
                }
                if !model.myStages.isEmpty {
                    HStack(spacing: 6) {
                        Text(L("crm.stage")).font(PN.Font.caption)
                        Picker("", selection: $model.picked) {
                            Text(L("crm.deal.keepStage", ["stage": model.deal?.stageName ?? ""])).tag("")
                            ForEach(model.myStages.filter { $0.id != model.deal?.stage }) { Text($0.label).tag($0.id) }
                        }.labelsHidden().fixedSize().help(L("crm.stage.help"))
                    }
                    if let st = model.suggestions.first(where: { $0.kind == "stage" }), model.picked == st.value {
                        Text(L("crm.deal.evidence", ["quote": st.evidence])).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                    }
                }
            } else if model.loaded && !dealName.isEmpty && model.error.isEmpty {
                Text(L("crm.deal.unreadable")).font(PN.Font.caption).foregroundStyle(PN.C.text3)
            }
        }
    }
}
