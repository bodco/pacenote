import LiveTipsCore
import SwiftUI

/// PB-17 (rules/hubspot-tasks.yaml > settings): Settings > Integrations > HubSpot > Tasks. Whose tasks, the queue,
/// the type, priority and default due date of new tasks, the automatic add after a call (off by default), where new
/// tasks go when Notion tasks are set up too, and a live check: "N tasks match now, synced X ago" with Refresh.
struct HubSpotTasksSection: View {
    @ObservedObject var model: SettingsModel
    @ObservedObject var store = HubSpotTasks.shared
    @State private var owners: [CRMOwner] = []
    @State private var queues: [(value: String, label: String)] = []
    @State private var types: [(value: String, label: String)] = CRMTasks.types.map { ($0, L("htask.type." + $0)) }
    @State private var priorities: [(value: String, label: String)] = CRMTasks.priorities.map { ($0, L("htask.prio." + $0)) }
    @State private var loadErr = ""

    private var s: Binding<CRMTasksSettings> { $model.config.crm.tasks }

    var body: some View {
        Section {
            Toggle(L("htask.enabled"), isOn: s.enabled)
            if s.wrappedValue.enabled {
                Picker(L("htask.whose"), selection: Binding(get: { s.wrappedValue.whose }, set: { v in
                    s.wrappedValue.whose = v; s.wrappedValue.whoseName = owners.first { $0.id == v }?.name ?? ""
                })) {
                    Text(L("htask.whose.me")).tag("me")
                    Text(L("htask.whose.anyone")).tag("anyone")
                    if !["me", "anyone"].contains(s.wrappedValue.whose), !owners.contains(where: { $0.id == s.wrappedValue.whose }) {
                        Text(s.wrappedValue.whoseName.isEmpty ? L("int.hubspot.ownerUnknown") : s.wrappedValue.whoseName).tag(s.wrappedValue.whose)
                    }
                    ForEach(owners) { o in Text(o.name).tag(o.id) }
                }
                Picker(L("htask.queue"), selection: Binding(get: { s.wrappedValue.queueId }, set: { v in
                    s.wrappedValue.queueId = v; s.wrappedValue.queueName = queues.first { $0.value == v }?.label ?? ""
                })) {
                    Text(L("htask.queue.any")).tag("")
                    if !s.wrappedValue.queueId.isEmpty, !queues.contains(where: { $0.value == s.wrappedValue.queueId }) {
                        Text(s.wrappedValue.queueName.isEmpty ? s.wrappedValue.queueId : s.wrappedValue.queueName).tag(s.wrappedValue.queueId)
                    }
                    ForEach(queues, id: \.value) { q in Text(q.label).tag(q.value) }
                }
                .help(L("htask.queueHelp"))
                Picker(L("htask.type"), selection: s.type) { ForEach(types, id: \.value) { o in Text(o.label).tag(o.value) } }
                Picker(L("htask.priority"), selection: s.priority) { ForEach(priorities, id: \.value) { o in Text(o.label).tag(o.value) } }
                Picker(L("htask.due"), selection: s.dueDays) {
                    ForEach([0, 1, 3, 7], id: \.self) { d in Text(L("htask.due.\(d)")).tag(d) }
                }
                Toggle(L("htask.autoAdd"), isOn: s.autoAdd).help(L("htask.autoAddHelp"))
                if NotionTasks.shared.settings.ready {
                    Picker(L("htask.destination"), selection: s.destination) {
                        Text(L("htask.dest.notion")).tag("notion"); Text(L("htask.dest.hubspot")).tag("hubspot")
                    }
                    .help(L("htask.destinationHelp"))
                }
                HStack {
                    Group {
                        if store.busy { PNSpinner(size: 14); Text(L("common.checking")) }
                        else if !store.error.isEmpty { Text(store.error).foregroundStyle(PN.C.danger).lineLimit(2) }
                        else if let at = store.syncedAt { Text(L("htask.status", ["n": "\(store.shown.count)", "ago": ago(at)])) }
                        else { Text(L("htask.notChecked")) }
                    }
                    .font(PN.Font.caption).foregroundStyle(PN.C.text3)
                    Spacer()
                    Button(L("htask.refresh")) { store.refresh() }.buttonStyle(.pnSecondary).disabled(store.busy)
                }
                if !loadErr.isEmpty { Text(loadErr).font(PN.Font.caption).foregroundStyle(PN.C.danger) }
            }
        } header: { Text(L("htask.title")).settingsAnchor("int.hubspot.tasks") } footer: {
            Text(L("htask.footer")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        }
        .onAppear { load() }
        .onChange(of: model.config.crm.tasks) { store.refresh() }    // the live check follows every change
    }

    private func ago(_ d: Date) -> String {
        let f = RelativeDateTimeFormatter(); f.locale = Locale(identifier: L10n.lang == "uk" ? "uk_UA" : "en_US"); f.unitsStyle = .short
        return f.localizedString(for: d, relativeTo: Date())
    }

    /// Owners, queues, and the portal's own task types and priorities (HubSpot's defaults when a list cannot be read).
    private func load() {
        guard let hs = CRMService.shared.connector() as? HubSpotConnector else { return }
        Task {
            do {
                owners = try await hs.owners()
                queues = (try? await hs.taskOptions("hs_queue_membership_ids")) ?? []
                if let ts = try? await hs.taskOptions("hs_task_type"), !ts.isEmpty { types = ts }
                if let ps = try? await hs.taskOptions("hs_task_priority"), !ps.isEmpty { priorities = ps }
                loadErr = ""
            } catch { loadErr = (error as? CRMError)?.message ?? error.localizedDescription }
            if s.wrappedValue.enabled { store.refresh() }
        }
    }
}
