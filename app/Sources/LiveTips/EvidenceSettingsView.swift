import LiveTipsCore
import SwiftUI

/// PB-52 G2 (S5): Settings > AI > Project memory, only while the flag `evidence_store` is on (spec section 10; text only,
/// Design shapes it later). Owner decision 24.11 (option b): no consent prompt before the first index; the row says
/// in one plain line where the text goes and what the first index is estimated to cost. Cost numbers come only from
/// an estimate the engine marks calibrated (S3); until then the line says the estimate is not available yet.
struct ProjectMemorySection: View {
    @ObservedObject var model: SettingsModel
    @ObservedObject private var service = EvidenceService.shared
    @State private var deleting: Project?
    @State private var deletingOrphans = false              // spec section 13.4: orphan stores go only after the PM confirms

    static func visible(_ c: AppConfig) -> Bool { EvidenceFlag.on(c) }

    private var prefs: Binding<EvidencePrefs> {
        Binding(get: { model.config.evidencePrefs ?? EvidencePrefs() }, set: { model.config.evidencePrefs = $0 })
    }

    private var route: String { model.config.ai.route(AIChain.evidenceJob) }

    private func text(_ r: EvidenceText.Ranged) -> String {
        var vars = r.line.vars
        if let rg = r.range { vars["range"] = L(rg.key, rg.vars) }
        return L(r.line.key, vars)
    }

    /// The estimate of the first index the engine reports (none before S3's calibration).
    private var firstEstimate: EvidenceText.Estimate? {
        service.status.values.compactMap { EvidenceText.Estimate.parse($0["estimate"]) }.first
    }

    var body: some View {
        Section {
            Toggle(L("mem.switch"), isOn: Binding(get: { EvidenceFlag.on(model.config) },
                                                    set: { EvidenceFlag.set(&model.config, on: $0) }))
            Text(L("mem.help")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            Text(text(EvidenceText.consent(provider: EvidenceText.provider(route: route), local: route == AIRoute.local,
                                           estimate: firstEstimate)))
                .font(PN.sys(12, .semibold)).fixedSize(horizontal: false, vertical: true)
            let prepProv = EvidenceText.provider(route: model.config.ai.prep), memProv = EvidenceText.provider(route: route)
            if !memProv.isEmpty && memProv != prepProv {
                Text(L("mem.prepProvider", ["prep": prepProv.isEmpty ? AIRoute.name(AIRoute.local) : prepProv, "index": memProv]))
                    .font(PN.Font.caption).foregroundStyle(PN.C.onMe).fixedSize(horizontal: false, vertical: true)
            }
            Picker(L("mem.route"), selection: Binding(get: { model.config.ai.evidence ?? "" },
                                                      set: { model.config.ai.evidence = $0.isEmpty ? nil : $0 })) {
                Text(L("mem.route.same")).tag("")
                ForEach(AIChain.defaultOrder, id: \.self) { r in Text(AIRoute.name(r)).tag(r) }
            }
            ceilings
            Picker(L("mem.topics"), selection: prefs.topicsEager) {
                Text(L("mem.topics.eager")).tag(true)
                Text(L("mem.topics.lazy")).tag(false)
            }
            Toggle(L("mem.useAI"), isOn: prefs.useAI)
            if !prefs.wrappedValue.useAI {
                Text(L("mem.useAI.off")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            }
            Button(prefs.wrappedValue.paused ? L("mem.resume") : L("mem.pause")) { prefs.wrappedValue.paused.toggle() }
            ForEach(service.projects(), id: \.slug) { p in projectRow(p) }
            let orphans = service.orphans()
            if !orphans.isEmpty {
                HStack {
                    Text(L("mem.orphans", ["count": "\(orphans.count)"])).font(PN.Font.caption).foregroundStyle(PN.C.text3)
                    Button(L("mem.orphans.delete")) { deletingOrphans = true }
                }
            }
        } header: { Text(L("mem.title")).settingsAnchor("ai.memory") }
        .onAppear {
            service.ensureUIDs()
            for p in service.projects() { service.refreshStatus(p) }
        }
        .confirmationDialog(L("mem.delete.confirm", ["project": deleting?.name ?? ""]), isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button(L("mem.delete"), role: .destructive) { if let p = deleting { service.delete(p) }; deleting = nil }
        }
        .confirmationDialog(L("mem.orphans.confirm", ["count": "\(service.orphans().count)"]), isPresented: $deletingOrphans) {
            Button(L("mem.orphans.delete"), role: .destructive) { service.deleteOrphans(); deletingOrphans = false }
        }
    }

    /// The ceilings in the unit of the indexing AI: USD on keys, percent of the 5-hour limit on plans, minutes on this Mac.
    @ViewBuilder private var ceilings: some View {
        let unit = EvidenceText.unit(route: route)
        let kp: (WritableKeyPath<EvidencePrefs, Double>, WritableKeyPath<EvidencePrefs, Double>, WritableKeyPath<EvidencePrefs, Double>) =
            unit == .usd ? (\.passUSD, \.dayUSD, \.allUSD) : unit == .pct ? (\.passPct, \.dayPct, \.allPct) : (\.passMin, \.dayMin, \.allMin)
        ForEach([("mem.ceiling.pass", kp.0), ("mem.ceiling.day", kp.1), ("mem.ceiling.all", kp.2)], id: \.0) { item in
            HStack {
                Text(L(item.0))
                Spacer()
                TextField("", value: Binding(get: { prefs.wrappedValue[keyPath: item.1] }, set: { prefs.wrappedValue[keyPath: item.1] = max(0, $0) }),
                          format: .number).frame(width: 70).multilineTextAlignment(.trailing)
                Text(L("mem.unit." + unit.rawValue)).font(PN.Font.caption).foregroundStyle(PN.C.text3)
            }
        }
    }

    @ViewBuilder private func projectRow(_ p: Project) -> some View {
        let st = p.evidenceUid.flatMap { service.status[$0] } ?? [:]
        VStack(alignment: .leading, spacing: 4) {
            Text(p.name).font(PN.sys(13, .semibold))
            Text(statusLine(p, st)).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            if let pending = st["pending_extraction"] as? Int, pending > 0 {
                Text(text(EvidenceText.before(pending: pending, estimate: EvidenceText.Estimate.parse(st["estimate"]))))
                    .font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            }
            if let run = EvidenceText.Run.parse(st["last_run"]) {
                let l = EvidenceText.after(run)
                Text(L(l.key, l.vars)).font(PN.Font.caption).foregroundStyle(PN.C.text3)
            }
            HStack {
                Button(L("mem.indexNow")) { service.indexNow(p) }.disabled(p.evidenceUid.map { service.running.contains($0) } ?? true)
                Button(L("mem.delete")) { deleting = p }
            }
        }
    }

    private func statusLine(_ p: Project, _ st: [String: Any]) -> String {
        if let uid = p.evidenceUid, service.running.contains(uid) { return L("mem.status.running") }
        guard !st.isEmpty else { return L("mem.status.none") }
        let sources = ((st["sources"] as? [String: Any]) ?? [:]).values.compactMap { ($0 as? NSNumber)?.intValue }.reduce(0, +)
        let parts = (st["snippets"] as? Int) ?? 0
        guard let when = (st["last_pass"] as? String).flatMap({ ISO8601DateFormatter().date(from: $0) }) else {
            return L("mem.status.never", ["sources": "\(sources)", "parts": "\(parts)"])
        }
        let f = DateFormatter(); f.dateStyle = .medium; f.timeStyle = .short
        return L("mem.status", ["sources": "\(sources)", "parts": "\(parts)", "when": f.string(from: when)])
    }
}
