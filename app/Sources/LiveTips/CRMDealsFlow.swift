import SwiftUI
import LiveTipsCore

// H4 (PB-60): the deal part of Update HubSpot. The deal the call is linked to (or one the PM picks), the suggested
// changes with the evidence from the call page, and the portal's own stages of the deal's pipeline. Applying goes
// through the existing Update HubSpot flow (one updateDeal call, Undo restores the old values).

struct CRMDealModel {
    var deal: CRMDeal? = nil
    var open: [CRMDeal] = []              // the company's open deals, for the picker
    var stages: [CRMStage] = []           // every pipeline of the portal
    var suggestions: [CRMDealSuggestion] = []
    var picked = ""                       // a stage chosen by hand ("" = keep the stage)

    var myStages: [CRMStage] { CRMDealSuggester.pipelineStages(stages, pipeline: deal?.pipeline ?? "") }
    /// The properties Apply writes: ticked suggestions and a stage picked by hand, only stages of this deal's pipeline.
    var fields: [String: String] {
        deal.map { CRMDealSuggester.fields(suggestions, pickedStage: picked, deal: $0, stages: stages) } ?? [:]
    }

    /// Reads the linked deal, the company's open deals and the portal's stages, then builds the suggestions from the
    /// call page (<stem>.sales.json as edited). A read error leaves the model empty: the other rows still work.
    @MainActor static func load(link: CRMLink, file: String) async -> CRMDealModel {
        var m = CRMDealModel()
        guard let c = CRMService.shared.connector() else { return m }
        if !link.companyId.isEmpty { m.open = ((try? await c.deals(companyId: link.companyId)) ?? []).filter { $0.open } }
        guard !link.dealId.isEmpty, link.dealId != "none", let d = try? await c.deal(id: link.dealId) else { return m }
        m.deal = d
        m.stages = (try? await (c as? CRMDealStages)?.dealStages()) ?? []
        let sales = SalesFile.load(file) ?? [:]
        let mapping = SettingsWindow.model.config.crm.qualification[(sales["method"] as? String) ?? ""] ?? [:]
        m.suggestions = CRMDealSuggester.build(sales: sales, deal: d, stages: m.stages, mapping: mapping)
        // the Stage menu is the one stage control: a suggested stage is preselected there, Apply is the confirmation
        if let i = m.suggestions.firstIndex(where: { $0.kind == "stage" }) { m.picked = m.suggestions[i].value; m.suggestions[i].on = false }
        return m
    }
}

struct CRMDealSection: View {
    @Binding var model: CRMDealModel
    let dealName: String
    let setDeal: (String, String) -> Void

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
            }
            if model.deal != nil {
                if model.suggestions.isEmpty {
                    Text(L("crm.deal.none")).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                }
                ForEach(model.suggestions.indices.filter { model.suggestions[$0].kind != "stage" }, id: \.self) { i in
                    let s = model.suggestions[i]
                    Toggle(isOn: Binding(get: { model.suggestions[i].on }, set: { model.suggestions[i].on = $0 })) {
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
            } else if !dealName.isEmpty {
                Text(L("crm.deal.unreadable")).font(PN.Font.caption).foregroundStyle(PN.C.text3)
            }
        }
    }
}
