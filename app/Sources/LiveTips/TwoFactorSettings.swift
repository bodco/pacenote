import AppKit
import CoreImage.CIFilterBuiltins
import SwiftUI
import UniformTypeIdentifiers
import LiveTipsCore

// PB-12 (owner 25.11): Settings > Account > Two-factor sign-in. Turn on with a QR (or the key typed by hand) and the
// first good code, 10 recovery codes shown once with Download, new recovery codes, replace the phone or the app, turn
// off; each change after it is on asks for a current code or a recovery code. The optional Touch ID or Mac password
// before the saved session is used lives here too (Keychain only, never sent). The server checks every code; the
// secret is shown once here and kept nowhere on this Mac.

struct TwoFactorSection: View {
    @ObservedObject private var acct = PacenoteAccount.shared
    @State private var sheet: TwoFactorSheet.Kind?
    @State private var lockProblem: String?
    @State private var lockBusy = false

    var body: some View {
        if acct.me?.auth != nil {
            let st = acct.me?.auth?.twoFactor
            Section {
                if let st { twoFactorRows(st) }
                lockRow
            } header: { Text(L("account.2fa.head")).settingsAnchor("acc.2fa") }
            .sheet(item: $sheet) { k in TwoFactorSheet(kind: k, email: acct.session?.email ?? "") { sheet = nil } }
            .onAppear { openAsked() }
            .onChange(of: acct.twoFactorAsk) { _, _ in openAsked() }
        }
    }

    /// The offer after a support reset, or the organization's requirement: the setup opens by itself.
    private func openAsked() {
        guard acct.twoFactorAsk != .none, sheet == nil, acct.me?.auth?.twoFactor?.enabled == false else { return }
        acct.twoFactorAsk = .none
        sheet = .enable
    }

    @ViewBuilder private func twoFactorRows(_ st: TwoFactor.State) -> some View {
        if st.enabled {
            HStack(spacing: 10) {
                Image(systemName: "lock.shield.fill").foregroundStyle(PN.C.turn).frame(width: 18)
                VStack(alignment: .leading, spacing: 2) {
                    Text(onLine(st))
                    Text(L("account.2fa.left", ["n": "\(st.recoveryLeft)"])).font(PN.Font.caption)
                        .foregroundStyle(st.recoveryLeft <= 2 ? PN.C.pending : PN.C.text3)
                }
                Spacer()
            }
            HStack {
                Button(L("account.2fa.replace")) { sheet = .replace }.buttonStyle(.pnSecondary)
                Button(L("account.2fa.regenerate")) { sheet = .regenerate }.buttonStyle(.pnTertiary)
                Spacer()
                Button(L("account.2fa.off")) { sheet = .disable }.buttonStyle(.pnTertiary).disabled(st.required)
                    .help(st.required ? L("account.2fa.err.required") : "")
            }
        } else {
            HStack(spacing: 10) {
                Image(systemName: "lock.shield").foregroundStyle(PN.C.text2).frame(width: 18)
                Text(L("account.2fa.offLine")).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button(L("account.2fa.on")) { sheet = .enable }.buttonStyle(.pnPrimary)
            }
            if st.required {
                Label(L("account.2fa.required"), systemImage: "exclamationmark.triangle.fill").font(PN.Font.caption)
                    .foregroundStyle(PN.C.pending).fixedSize(horizontal: false, vertical: true)
            } else if st.offer {
                Label(L("account.2fa.offer"), systemImage: "info.circle").font(PN.Font.caption)
                    .foregroundStyle(PN.C.text2).fixedSize(horizontal: false, vertical: true)
            }
        }
        if acct.me?.auth?.password == false {
            Text(L("account.2fa.googleNote")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        }
        Text(L("account.2fa.lost")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
    }

    private func onLine(_ st: TwoFactor.State) -> String {
        guard let d = st.enabledAt else { return L("account.2fa.onNoDate") }
        let f = DateFormatter(); f.dateStyle = .medium; f.timeStyle = .none; f.locale = Locale(identifier: L10n.lang == "uk" ? "uk_UA" : "en_US")
        return L("account.2fa.onLine", ["when": f.string(from: d)])
    }

    private var lockRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle(isOn: Binding(get: { acct.signInLock }, set: { on in
                lockBusy = true; lockProblem = nil
                Task { lockProblem = await acct.setSignInLock(on); lockBusy = false }
            })) { Text(L("account.lock.toggle")) }
            .disabled(lockBusy)
            Text(L("account.lock.sub")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            if let p = lockProblem {
                Label(p, systemImage: "exclamationmark.circle.fill").font(PN.Font.caption).foregroundStyle(PN.C.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// One change: turn on, replace, new recovery codes, turn off. Each step is one small screen.
struct TwoFactorSheet: View {
    enum Kind: String, Identifiable { case enable, replace, regenerate, disable; var id: String { rawValue } }
    enum Phase: Equatable { case proof, loading, scan(TwoFactor.Setup), codes([String]), done, failed }
    let kind: Kind
    let email: String
    let close: () -> Void
    @ObservedObject private var acct = PacenoteAccount.shared
    @State private var phase: Phase = .proof
    @State private var input = ""
    @State private var busy = false
    @State private var error: String?
    @State private var saved = false
    @FocusState private var focused: Bool

    private var title: String {
        switch kind {
        case .enable: return L("account.2fa.on")
        case .replace: return L("account.2fa.replace")
        case .regenerate: return L("account.2fa.regenerate")
        case .disable: return L("account.2fa.off")
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: PN.Space.x3) {
            Text(title).pnSection()
            switch phase {
            case .proof: proofStep
            case .loading: HStack { PNSpinner(size: 16); Spacer() }
            case .scan(let s): scanStep(s)
            case .codes(let c): codesStep(c)
            case .done: doneStep
            case .failed: EmptyView()
            }
            if let e = error {
                Label { Text(e).fixedSize(horizontal: false, vertical: true) } icon: { Image(systemName: "exclamationmark.circle.fill") }
                    .font(PN.Font.caption).foregroundStyle(PN.C.danger)
            }
            if phase == .failed { HStack { Spacer(); Button(L("common.close"), action: close).buttonStyle(.pnPrimary).keyboardShortcut(.defaultAction) } }
        }
        .padding(PN.Space.x6)
        .frame(width: 460)
        .onAppear { if kind == .enable { startSetup(nil) } }
        .onDisappear { input = "" }
        .interactiveDismissDisabled(isCodes)
    }

    private var isCodes: Bool { if case .codes = phase { return true }; return false }

    // MARK: steps

    /// A current code or a recovery code, before replace, new codes or turning off.
    @ViewBuilder private var proofStep: some View {
        Text(L(kind == .disable ? "account.2fa.proof.off" : "account.2fa.proof")).font(PN.Font.caption).foregroundStyle(PN.C.text2)
            .fixedSize(horizontal: false, vertical: true)
        codeInput(L("account.2fa.proof.field"), placeholder: "123 456")
        Text(L("account.2fa.lost")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
        buttons(primary: kind == .disable ? L("account.2fa.off") : L("common.continue"), destructive: kind == .disable) { submitProof() }
    }

    /// The QR, the key by hand and the first code.
    @ViewBuilder private func scanStep(_ s: TwoFactor.Setup) -> some View {
        Text(L("account.2fa.scan")).font(PN.Font.caption).foregroundStyle(PN.C.text2).fixedSize(horizontal: false, vertical: true)
        HStack(alignment: .top, spacing: PN.Space.x4) {
            if let img = Self.qr(s.uri) {
                Image(nsImage: img).interpolation(.none).resizable().frame(width: 168, height: 168)
                    .padding(6).background(Color.white).accessibilityLabel(L("account.2fa.qr.a11y"))
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(L("account.2fa.key")).font(PN.Font.caption).foregroundStyle(PN.C.text2)
                Text(TwoFactor.groupedKey(s.secret)).font(PN.sys(13, .medium, mono: true)).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Button(L("account.2fa.key.copy")) {
                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(s.secret, forType: .string)
                }.buttonStyle(.pnTertiary)
                Text(L("account.2fa.key.type")).font(PN.Font.caption).foregroundStyle(PN.C.text3).fixedSize(horizontal: false, vertical: true)
            }
        }
        if kind == .replace {
            Text(L("account.2fa.replace.note")).font(PN.Font.caption).foregroundStyle(PN.C.text2).fixedSize(horizontal: false, vertical: true)
        }
        codeInput(L("account.2fa.first"), placeholder: "123 456")
        buttons(primary: L("account.2fa.confirm"), destructive: false) { confirm(s) }
    }

    /// The 10 recovery codes, once: Download, Copy, and Done only after one of them (or a tick).
    @ViewBuilder private func codesStep(_ codes: [String]) -> some View {
        Text(L("account.2fa.codes.intro")).font(PN.Font.caption).foregroundStyle(PN.C.text2).fixedSize(horizontal: false, vertical: true)
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], alignment: .leading, spacing: 6) {
            ForEach(codes, id: \.self) { c in Text(c).font(PN.sys(13, .medium, mono: true)).textSelection(.enabled) }
        }
        .padding(PN.Space.x3).background(PN.C.surface2).clipShape(RoundedRectangle(cornerRadius: PN.r(6)))
        HStack {
            Button(L("account.2fa.codes.download")) { download(codes) }.buttonStyle(.pnSecondary)
            Button(L("account.2fa.codes.copy")) {
                NSPasteboard.general.clearContents(); NSPasteboard.general.setString(codes.joined(separator: "\n"), forType: .string); saved = true
            }.buttonStyle(.pnTertiary)
            Spacer()
        }
        Toggle(L("account.2fa.codes.saved"), isOn: $saved).toggleStyle(.pnCheckbox).font(PN.Font.caption)
        HStack {
            Spacer()
            Button(L("common.close")) { close() }.buttonStyle(.pnPrimary).keyboardShortcut(.defaultAction).disabled(!saved)
        }
    }

    @ViewBuilder private var doneStep: some View {
        Text(L(kind == .disable ? "account.2fa.done.off" : "account.2fa.done.replace")).fixedSize(horizontal: false, vertical: true)
        HStack { Spacer(); Button(L("common.close"), action: close).buttonStyle(.pnPrimary).keyboardShortcut(.defaultAction) }
    }

    // MARK: parts

    private func codeInput(_ label: String, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(PN.Font.caption).foregroundStyle(PN.C.text2)
            TextField(placeholder, text: $input).textFieldStyle(.roundedBorder).font(PN.sys(17, .semibold, mono: true))
                .textContentType(.oneTimeCode).focused($focused).accessibilityLabel(label)
                .onAppear { focused = true }
        }
    }

    private func buttons(primary: String, destructive: Bool, _ action: @escaping () -> Void) -> some View {
        HStack {
            Spacer()
            if busy { PNSpinner(size: 16) }
            Button(L("common.cancel"), action: close).buttonStyle(.pnTertiary).keyboardShortcut(.cancelAction)
            Button(primary, role: destructive ? .destructive : nil, action: action)
                .buttonStyle(.pnPrimary).keyboardShortcut(.defaultAction).disabled(busy)
        }
    }

    private func work(_ f: @escaping () async throws -> Void) {
        guard !busy else { return }
        busy = true; error = nil
        Task {
            defer { busy = false }
            do { try await f() } catch { self.error = error.localizedDescription }
        }
    }

    private func submitProof() {
        guard let p = TwoFactor.proof(input) else { error = L("account.2fa.proof.bad"); focused = true; return }
        switch kind {
        case .replace: startSetup(p)
        case .regenerate: work { let c = try await acct.twoFactorRegenerate(proof: p); input = ""; phase = c.isEmpty ? .done : .codes(c) }
        case .disable: work { try await acct.twoFactorDisable(proof: p); input = ""; phase = .done }
        case .enable: break
        }
    }

    private func startSetup(_ p: TwoFactor.Proof?) {
        if kind == .enable { phase = .loading }
        work {
            do { let s = try await acct.twoFactorSetup(proof: p); input = ""; phase = .scan(s) }
            catch { if kind == .enable { phase = .failed }; throw error }
        }
    }

    private func confirm(_ s: TwoFactor.Setup) {
        guard TwoFactor.codeComplete(input) else { error = L("auth.2fa.code.field"); focused = true; return }
        work {
            let c = try await acct.twoFactorConfirm(setupID: s.id, code: input)
            input = ""
            phase = c.isEmpty ? .done : .codes(c)
        }
    }

    private func download(_ codes: [String]) {
        let p = NSSavePanel()
        p.allowedContentTypes = [.plainText]
        p.nameFieldStringValue = "pacenote-recovery-codes.txt"
        guard p.runModal() == .OK, let url = p.url else { return }
        let text = TwoFactor.recoveryFile(codes: codes, heading: [L("account.2fa.file.head", ["email": email]), L("account.2fa.file.note")])
        do { try Data(text.utf8).write(to: url, options: .atomic); saved = true } catch { self.error = error.localizedDescription }
    }

    /// The QR of the otpauth URI, drawn here (CoreImage); nothing leaves the Mac for it.
    static func qr(_ s: String) -> NSImage? {
        let f = CIFilter.qrCodeGenerator()
        f.message = Data(s.utf8)
        f.correctionLevel = "M"
        guard let out = f.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)) else { return nil }
        let rep = NSCIImageRep(ciImage: out)
        let img = NSImage(size: rep.size)
        img.addRepresentation(rep)
        return img
    }
}
