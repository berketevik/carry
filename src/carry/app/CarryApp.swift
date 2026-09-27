import SwiftUI
import AppKit

// The bridge owns one child. Private pipes have no address to expose and close
// on app exit. Failed writes are never replayed automatically.
final class Worker {
    private let queue = DispatchQueue(label: "carry.core")
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    func stop() { process?.terminate() }
    func call(_ request: [String: Any], completion: @escaping (Result<[String: Any], Error>) -> Void) {
        queue.async {
            do {
                if self.process?.isRunning != true {
                    let p = Process(), stdin = Pipe(), stdout = Pipe()
                    // A packaged app carries its own Python; one built by `carry app install`
                    // points at the installed Carry's interpreter instead.
                    let resources = Bundle.main.resourceURL!
                    var python = resources.appendingPathComponent("python/bin/python3")
                    if !FileManager.default.fileExists(atPath: python.path),
                       let text = try? String(contentsOf: resources.appendingPathComponent("python-path.txt"), encoding: .utf8) {
                        let path = text.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !path.isEmpty { python = URL(fileURLWithPath: path) }
                    }
                    p.executableURL = python
                    p.arguments = ["-I", "-B", "-u", "-m", "carry.desktop"]
                    p.standardInput = stdin; p.standardOutput = stdout
                    p.standardError = FileHandle.nullDevice
                    p.currentDirectoryURL = Bundle.main.resourceURL
                    try p.run()
                    self.process = p; self.input = stdin.fileHandleForWriting; self.output = stdout.fileHandleForReading
                }
                let current = self.process!
                let timer = DispatchSource.makeTimerSource(queue: .global())
                timer.schedule(deadline: .now() + 120)
                timer.setEventHandler { if current.isRunning { current.terminate() } }
                timer.resume()
                defer { timer.cancel() }
                var data = try JSONSerialization.data(withJSONObject: request)
                data.append(10)
                try self.input!.write(contentsOf: data)
                var response = Data()
                while true {
                    guard let byte = try self.output!.read(upToCount: 1), !byte.isEmpty else { throw UIError(L("Core stopped. Refresh to reconnect; check the result before repeating a write.")) }
                    if byte.first == 10 { break }
                    response.append(byte)
                    if response.count > 16_000_000 { throw UIError(L("Core response exceeded the app limit.")) }
                }
                guard let object = try JSONSerialization.jsonObject(with: response) as? [String: Any] else { throw UIError(L("Invalid core response.")) }
                if object["ok"] as? Bool != true { throw UIError(object["error"] as? String ?? "Core request failed.") }
                completion(.success(object["result"] as? [String: Any] ?? [:]))
            } catch {
                self.process?.terminate(); self.process = nil
                completion(.failure(error))
            }
        }
    }
}

struct UIError: LocalizedError {
    var message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? {
        switch message {
        case "github_cli_missing": return L("This build is missing GitHub support. Use the complete Carry pilot package.")
        case "github_access_failed": return L("GitHub could not read this repository. Sign in and check that your account has access.")
        case "github_unreachable", "github_download_failed": return L("GitHub could not be reached. Check your connection and retry.")
        case "invalid_github_repository": return L("Enter a GitHub repository URL or owner/repository.")
        case "github_no_markdown_in_folder": return L("No Markdown files were found in that folder. Check the repository, branch and folder.")
        case "model_download_failed", "model_download_incomplete": return L("The model download did not finish. Check your connection and try again.")
        case "reranker_install_failed": return L("The relevance model could not be installed. Try again; the embedding model may already be ready.")
        case "ollama_runtime_missing": return L("This build is missing its model runtime. Use the complete Carry pilot package or install Ollama.")
        case "ollama_start_failed": return L("The local model service could not start. Open Ollama and retry.")
        case "workspace_not_initialized": return L("This folder has no Carry workspace. Create one or open an existing workspace folder.")
        case "workspace_already_initialized": return L("This folder already has a workspace. Use Open workspace.")
        case "duplicate_source_id": return L("That source ID is already in use. Choose a different ID.")
        case "invalid_source_id": return L("Use lowercase letters, numbers, hyphens or underscores for the source ID.")
        case "nested_source_roots": return L("This folder overlaps an existing source. Choose a separate folder.")
        case "state_dir_inside_source_root": return L("Keep Carry’s workspace folder outside your Markdown source folders.")
        case "settings_changed_since_preview", "settings_changed_during_apply": return L("Settings changed after the preview. Refresh and review a new preview. If setup was interrupted, use its rollback entry.")
        case "settings_changed_since_install", "settings_changed_during_rollback": return L("Settings changed after setup. Rollback stopped to preserve those edits. Review the changed files before disconnecting.")
        case "existing_carry_connection_conflict", "existing_carry_hook_conflict": return L("This project already has a different Carry connection. Review or roll back that setup before replacing it.")
        case "preview_again_required": return L("The preview expired when the core restarted or the workspace changed. Preview the connection again.")
        case "proposal_changed_since_review", "target_content_changed", "target_revision_changed", "record_changed_during_review_commit": return L("The draft or its source changed. Select the decision again and review the new diff before accepting.")
        case "client_unavailable": return L("The client CLI was not found. Install it or select its executable path.")
        case "unsupported_version": return L("Update the client: Claude Code 2.1.196 or newer, or Codex CLI 0.153.4 or newer, is required.")
        case "hooks_disabled": return L("Codex hooks are disabled. Enable hooks in Codex before opting in to capture.")
        case "vault_target_not_empty": return L("That folder already has notes. Choose an empty folder for a new vault, or add your existing notes folder as a source below.")
        case "vault_target_not_directory": return L("Choose a folder, not a file, for the new vault.")
        case "vault_changed_since_preview": return L("The vault folder changed after the preview. Preview again before creating the vault.")
        case "workspace_vault_source_read_only": return L("This vault is connected read-only. Prompt capture needs a writable vault source.")
        case "invalid_vault_request": return L("Choose a language and whether to record prompts, then preview again.")
        case "typesafe_key_not_saved": return L("The key could not be saved in the Keychain. Paste it again; it must not be empty.")
        case "source_root_missing": return L("This source folder is not available. Reconnect the drive or check the folder in Settings › Sources.")
        case "source_file_missing", "note_not_found": return L("That note no longer exists at this path. Refresh the vault list.")
        case "note_too_large": return L("This file is too large to preview here. Open it in your editor.")
        case "setting_out_of_range", "invalid_setting": return L("One of the values is outside its allowed range. Check the numbers and save again.")
        case "invalid_schedule_time": return L("Choose a valid time for the nightly harvest.")
        case "no_sources": return L("Add a Markdown folder or vault in Settings › Sources first.")
        default: return message
        }
    }
}

// MARK: - Localization
// English strings are the keys; Turkish lives in `TR` below. Switching language only
// re-renders views, so every visible string goes through L().

enum Lang {
    static var code: String = UserDefaults.standard.string(forKey: "language") ?? (Locale.preferredLanguages.first?.hasPrefix("tr") == true ? "tr" : "en")
    static var locale: Locale { Locale(identifier: code == "tr" ? "tr_TR" : "en_US") }
}
func L(_ s: String) -> String { Lang.code == "tr" ? (TR[s] ?? s) : (EN[s] ?? s) }
func L(_ s: String, _ args: CVarArg...) -> String { String(format: L(s), locale: Lang.locale, arguments: args) }
/// Display name for common frontmatter values (type, status, sensitivity); the raw value stays in Properties.
func metaValue(_ value: String) -> String { Lang.code == "tr" ? (TR["meta." + value] ?? value) : value }
func languageName(_ vaultLanguage: String) -> String { vaultLanguage == "Turkish" ? "Türkçe" : vaultLanguage == "English" ? "English" : vaultLanguage }
func modeTitle(_ tag: String) -> String? {
    switch tag {
    case "semantic_jev": return T("By meaning + TypeSafe Jev check · Ollama + Jev", "Anlamına göre + TypeSafe Jev kontrolü · Ollama + Jev")
    case "semantic_assistant": return T("By meaning + my own assistant checks · Ollama + Claude/GPT", "Anlamına göre + kendi asistanım kontrol eder · Ollama + Claude/GPT")
    case "keyword_jev": return T("By words + TypeSafe Jev check · Jev", "Kelimelerine göre + TypeSafe Jev kontrolü · Jev")
    case "keyword_assistant": return T("By words + my own assistant checks · Claude/GPT", "Kelimelerine göre + kendi asistanım kontrol eder · Claude/GPT")
    case "accurate_multilingual": return T("By meaning + large model on this Mac · Ollama + local model", "Anlamına göre + bu Mac'te büyük model · Ollama + yerel model")
    default: return nil
    }
}

func presetLabel(_ preset: String) -> String {
    if let title = modeTitle(preset) { return title }
    switch preset {
    case "semantic_jev": return T("Search by meaning + online check", "Anlama göre arama + çevrimiçi kontrol")
    case "semantic_assistant": return T("Search by meaning", "Anlama göre arama")
    case "keyword_jev": return T("Word matching + online check", "Kelime eşleştirme + çevrimiçi kontrol")
    case "keyword_assistant": return T("Word matching", "Kelime eşleştirme")
    case "accurate_multilingual": return L("Accurate multilingual search")
    case "custom": return L("Custom configuration")
    default: return preset.isEmpty ? "—" : preset
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    static let worker = Worker()
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    /// Set the icon ourselves: Dock and Stage Manager can keep a stale cached icon for an
    /// app rebuilt in place.
    func applicationDidFinishLaunching(_ notification: Notification) {
        if let icon = Bundle.main.image(forResource: "Carry") { NSApp.applicationIconImage = icon }
    }
    func applicationWillTerminate(_ notification: Notification) { Self.worker.stop() }
}

@MainActor final class Model: ObservableObject {
    @Published var workspace = ProcessInfo.processInfo.environment["CARRY_DESKTOP_WORKSPACE"] ?? UserDefaults.standard.string(forKey: "workspace") ?? ""
    @Published var snapshot: [String: Any] = [:]
    @Published var review: [String: Any] = [:]
    @Published var preview: [String: Any] = [:]
    @Published var busy = false
    @Published var error = ""
    @Published var notice = ""
    @Published var localTest = "Not tested"
    @Published var githubLogin: [String: Any] = [:]
    @Published var repositories: [[String: Any]] = []
    @Published var nextRepositoryPage: Int? = nil
    @Published var githubAccount = ""
    @Published var job: [String: Any] = [:]
    // Navigation, settings and the vault browser.
    @Published var page = ProcessInfo.processInfo.environment["CARRY_START_PAGE"] ?? "Overview"
    @Published var language = Lang.code {
        didSet { Lang.code = language; UserDefaults.standard.set(language, forKey: "language") }
    }
    @Published var settingsTab = "Search"
    @Published var settings: [String: Any] = [:]
    @Published var vault: [String: Any] = [:]
    @Published var files: [[String: Any]] = []
    @Published var vaultSource = ""
    @Published var vaultFilter = "mine"
    @Published var note: [String: Any] = [:]
    @Published var backlinks: [String] = []
    @Published var harvest: [String: Any] = [:]
    @Published var assistants: [String: Any] = [:]
    @Published var onboarding = false
    @Published var onboardingStep = 0
    @Published var showGuide = false
    @Published var showNewNote = false
    // Back/forward through opened notes: (source, path).
    @Published var history: [(String, String)] = []
    @Published var historyIndex = -1
    private var noteToken = 0
    private var lastJobState = ""
    func tick() {
        guard !workspace.isEmpty, !busy else { return }
        if ["starting", "waiting_for_browser"].contains(str(githubLogin, "state")) {
            run("github_login_status", clearError: false, quiet: true) { value in
                self.githubLogin = value
                if str(value, "state") == "connected" { self.loadRepositories() }
            }
        } else if harvest["running"] as? Bool == true {
            run("harvest_status", clearError: false, quiet: true) { value in
                self.harvest = value
                if value["running"] as? Bool != true {
                    let code = value["exit_code"] as? Int
                    let drafts = (value["drafts"] as? [String] ?? []).count
                    if let code, code != 0 { self.notice = L("Harvest stopped with an error. See the log on the Harvest tab.") }
                    else if drafts > 0 { self.notice = T("\(drafts) new draft(s) from your chats. Go through them on the Review page.", "Sohbetlerinizden \(drafts) yeni taslak çıktı. İncele sayfasında gözden geçirebilirsiniz.") }
                    else { self.notice = T("No new drafts: there were no new chats with something worth keeping.", "Yeni taslak çıkmadı: saklanmaya değer bir şey içeren yeni sohbet yoktu.") }
                    self.loadVault()
                }
            }
        } else {
            run("maintenance_status", clearError: false, quiet: true) { value in
                self.job = value
                let state = str(value, "state")
                if self.lastJobState != state && state != "running" { self.refresh() }
                self.lastJobState = state
            }
        }
    }
    func loadRepositories(page: Int = 1) {
        run("github_repositories", ["page": page]) { value in
            let found = value["repositories"] as? [[String: Any]] ?? []
            self.repositories = page == 1 ? found : self.repositories + found
            self.nextRepositoryPage = value["next_page"] as? Int
            self.githubAccount = "GitHub connected"
        }
    }
    var sources: [[String: Any]] { snapshot["workspace"] as? [String: Any] == nil ? [] : (snapshot["workspace"] as! [String: Any])["sources"] as? [[String: Any]] ?? [] }
    var localSources: [[String: Any]] { sources.filter { ($0["github"] as? [String: Any])?.isEmpty != false } }
    var activity: [[String: Any]] { (snapshot["activity"] as? [[String: Any]] ?? []).sorted { str($0,"updated") > str($1,"updated") } }
    var adapters: [[String: Any]] { (snapshot["capture"] as? [String: Any])?["adapters"] as? [[String: Any]] ?? [] }
    var connections: [[String: Any]] { snapshot["connections"] as? [[String: Any]] ?? [] }
    var pendingReview: Int { Int(str(snapshot["review"] as? [String: Any] ?? [:], "pending", "0")) ?? 0 }
    var vaultFields: [String: Any] { vaultSource.isEmpty ? [:] : ["source_id": vaultSource] }
    /// `quiet` requests (polling, browsing) neither lock the window nor wait for a busy one.
    func run(_ action: String, _ fields: [String: Any] = [:], clearError: Bool = true, quiet: Bool = false, then: (([String: Any]) -> Void)? = nil) {
        request(action, fields, clearError: clearError, quiet: quiet, orElse: nil, then: then)
    }
    /// `run` with a failure handler in place of the error banner.
    func request(_ action: String, _ fields: [String: Any], clearError: Bool, quiet: Bool, orElse: ((Error) -> Void)?, then: (([String: Any]) -> Void)?) {
        if !quiet { guard !busy else { return }; busy = true }
        if clearError { error = "" }
        var request = fields
        let asked = workspace
        request["action"] = action; request["workspace"] = asked
        AppDelegate.worker.call(request) { result in
            DispatchQueue.main.async {
                if !quiet { self.busy = false }
                guard self.workspace == asked else { return }
                switch result {
                case .success(let value): then?(value)
                case .failure(let e): if let orElse { orElse(e) } else { self.error = e.localizedDescription }
                }
            }
        }
    }
    func refresh() {
        guard !workspace.isEmpty else { onboarding = true; return }
        request("snapshot", [:], clearError: true, quiet: false, orElse: { e in
            if ["workspace_not_initialized", "workspace_config_unreadable"].contains((e as? UIError)?.message ?? "") { self.snapshot = [:]; self.onboarding = true }
            else { self.error = e.localizedDescription }
        }) {
            self.snapshot = $0
            if self.sources.isEmpty { self.onboarding = true }
            self.loadAssistants()
            self.loadSettings(); self.loadVault(); self.reloadNote()
            self.run("harvest_status", clearError: false, quiet: true) { self.harvest = $0 }
            if ProcessInfo.processInfo.environment["CARRY_MEASURE_STARTUP"] == "1" { print("carry_workspace_ready"); fflush(stdout) }
        }
    }
    static var defaultWorkspace: String {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Carry").path
    }
    var notesRoot: String { str(vault, "root", str(assistants, "root", "")) }
    func loadAssistants() { run("assistants", clearError: false, quiet: true) { self.assistants = $0 } }
    func startOnboarding() { onboardingStep = 0; onboarding = true }
    func finishOnboarding() { onboarding = false; page = "Overview"; UserDefaults.standard.set(true, forKey: "onboarded"); refresh() }
    /// First run: Carry's settings folder is created in Application Support without asking;
    /// people only choose where their notes live.
    func prepareWorkspace(_ done: @escaping () -> Void) {
        if !workspace.isEmpty && !snapshot.isEmpty { done(); return }
        let path = workspace.isEmpty ? Model.defaultWorkspace : workspace
        try? FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: true)
        workspace = path
        request("initialize", [:], clearError: true, quiet: false, orElse: { e in
            if (e as? UIError)?.message == "workspace_already_initialized" { self.run("snapshot") { self.snapshot = $0; self.persistWorkspace(); done() } }
            else { self.error = e.localizedDescription }
        }, then: { value in self.snapshot = value; self.persistWorkspace(); done() })
    }
    func persistWorkspace() {
        if ProcessInfo.processInfo.environment["CARRY_DESKTOP_WORKSPACE"] == nil { UserDefaults.standard.set(workspace, forKey: "workspace") }
    }
    func loadSettings() {
        run("settings", clearError: false, quiet: true) { value in
            self.settings = value
            if self.vaultSource.isEmpty { self.vaultSource = str(value, "default_vault", "") }
        }
    }
    func loadVault() {
        guard !localSources.isEmpty else { vault = [:]; files = []; return }
        // A reply for a source the user has since left is dropped; before the default vault
        // is known (empty selection) any reply is the default's.
        let current: ([String: Any]) -> Bool = { self.vaultSource.isEmpty || self.vaultSource == str($0, "source_id", "") }
        run("vault_overview", vaultFields, clearError: false, quiet: true) { value in if current(value) { self.vault = value } }
        run("vault_browse", vaultFields, clearError: false, quiet: true) { value in if current(value) { self.files = value["files"] as? [[String: Any]] ?? [] } }
    }
    /// Shows another source in Vault: the open note and its list belong to the old one.
    func switchVault(_ sid: String) {
        guard sid != vaultSource else { return }
        vaultSource = sid; closeNote(); files = []; vault = [:]; loadVault()
    }
    var noteSource: String { str(note, "source_id", vaultSource) }
    /// Opens a note of `source` (the current vault by default). Responses that arrive after
    /// the user has moved on to another note or source are dropped.
    func openNote(_ path: String, source: String? = nil, record: Bool = true) {
        let sid = source ?? vaultSource
        if page != "Review" { page = "Vault" }
        if !sid.isEmpty && sid != vaultSource { vaultSource = sid; files = []; vault = [:]; loadVault() }
        noteToken += 1
        let token = noteToken
        var fields: [String: Any] = ["path": path]
        if !sid.isEmpty { fields["source_id"] = sid }
        run("vault_note", fields, quiet: true) { value in
            guard token == self.noteToken else { return }
            self.note = value; self.backlinks = []
            if record {
                if self.historyIndex < self.history.count - 1 { self.history.removeSubrange((self.historyIndex + 1)...) }
                self.history.append((sid, path)); self.historyIndex = self.history.count - 1
            }
            self.run("vault_backlinks", fields, clearError: false, quiet: true) { links in
                guard token == self.noteToken else { return }
                self.backlinks = links["backlinks"] as? [String] ?? []
            }
        }
    }
    func followLink(_ target: String) {
        let sid = noteSource, from = str(note, "path", ""), token = noteToken
        var fields: [String: Any] = ["target": target, "from": from]
        if !sid.isEmpty { fields["source_id"] = sid }
        run("vault_resolve", fields, quiet: true) { value in
            guard token == self.noteToken else { return }  // the user opened something else meanwhile
            self.openNote(str(value, "path"), source: sid)
        }
    }
    var canGoBack: Bool { historyIndex > 0 }
    var canGoForward: Bool { historyIndex >= 0 && historyIndex < history.count - 1 }
    func goBack() { guard canGoBack else { return }; historyIndex -= 1; openNote(history[historyIndex].1, source: history[historyIndex].0, record: false) }
    func goForward() { guard canGoForward else { return }; historyIndex += 1; openNote(history[historyIndex].1, source: history[historyIndex].0, record: false) }
    func closeNote() { noteToken += 1; note = [:]; backlinks = [] }
    /// Re-reads the open note after a refresh; a note deleted in the editor closes with a notice.
    func reloadNote() {
        guard !note.isEmpty else { return }
        let path = str(note, "path"), sid = noteSource
        noteToken += 1
        let token = noteToken
        request("vault_note", ["path": path, "source_id": sid], clearError: false, quiet: true, orElse: { e in
            guard token == self.noteToken else { return }
            // Only a missing file closes the view; other failures keep the preview and say why.
            if (e as? UIError)?.message == "source_file_missing" { self.closeNote(); self.notice = L("The open note was moved or deleted outside Carry, so it was closed.") }
            else { self.error = e.localizedDescription }
        }, then: { value in
            guard token == self.noteToken else { return }
            self.note = value
        })
    }
    /// Approve several drafts; the summary says how many were skipped and why.
    func approveMany(_ paths: [String], then: (() -> Void)? = nil) {
        guard !paths.isEmpty else { return }
        run("note_approve_many", vaultFields.merging(["paths": paths]) { $1 }) { value in
            let approved = (value["approved"] as? [String] ?? []).count
            let skipped = value["skipped"] as? [[String: Any]] ?? []
            let locked = skipped.filter { str($0, "reason") == "locked" }.count
            let raw = skipped.filter { str($0, "reason") == "raw" }.count
            var parts = [T("\(approved) draft(s) approved.", "\(approved) taslak onaylandı.")]
            if locked > 0 { parts.append(T("\(locked) locked note(s) left as they are.", "\(locked) kilitli not olduğu gibi bırakıldı.")) }
            if raw > 0 { parts.append(T("\(raw) raw record(s) (chat logs, clips) are not approved; they stay as source material.", "\(raw) ham kayıt (sohbet kaydı, kırpıntı) onaylanmaz; kaynak malzeme olarak kalır.")) }
            let other = skipped.count - locked - raw
            if other > 0 { parts.append(T("\(other) could not be approved.", "\(other) tanesi onaylanamadı.")) }
            self.notice = parts.joined(separator: " ")
            self.loadVault(); self.reloadNote(); then?()
        }
    }
    /// The draft after `path` in its own list (newest first): the review queue for an inbox draft,
    /// every approvable draft otherwise.
    func nextDraft(after path: String) -> String? {
        let inQueue = files.first { str($0, "path") == path }?["review"] as? Bool == true
        let drafts = files.filter { $0[inQueue ? "review" : "approvable"] as? Bool == true }
            .sorted { num($0, "modified") > num($1, "modified") }.map { str($0, "path") }
        guard let i = drafts.firstIndex(of: path) else { return drafts.first { $0 != path } }
        return drafts.dropFirst(i + 1).first ?? drafts.prefix(i).first
    }
    func startHarvest() {
        run("harvest_now") { value in
            self.harvest = value
            self.notice = value["started"] as? Bool == false ? L("A harvest is already running.") : L("Harvest started in the background. New drafts appear in the inbox (+/).")
        }
    }
    func showVault(_ filter: String) { vaultFilter = filter == "all" ? "mine" : filter; page = "Vault" }
    var ownNotes: Int { Int(str(vault, "total", "0")) ?? 0 }
    var anyAssistantConnected: Bool { ["claude", "codex"].contains { (assistants[$0] as? [String: Any])?["connected"] as? Bool == true } }
    func chooseWorkspace(create: Bool) {
        guard let path = folder(create ? L("Choose or create a separate folder for Carry’s index and settings") : L("Open a Carry workspace folder")) else { return }
        workspace = path; snapshot = [:]; review = [:]; preview = [:]; localTest = "Not tested"
        settings = [:]; vault = [:]; files = []; closeNote(); vaultSource = ""; history = []; historyIndex = -1; harvest = [:]
        let loaded: ([String: Any]) -> Void = { value in
            if ProcessInfo.processInfo.environment["CARRY_DESKTOP_WORKSPACE"] == nil { UserDefaults.standard.set(path, forKey: "workspace") }
            self.snapshot = value; self.refresh()
        }
        run(create ? "initialize" : "snapshot", then: loaded)
    }
    func openSource(_ sid: String, _ path: String) {
        run("source", ["source_id": sid, "path": path]) { value in
            if let path = value["path"] as? String, !NSWorkspace.shared.open(URL(fileURLWithPath: path)) { self.error = L("No application could open this Markdown file. Choose a Markdown editor in Finder.") }
        }
    }
    func inspect(_ record: [String: Any]) {
        review = [:]
        run("review", ["record_id": str(record,"record_id")]) { self.review = $0 }
    }
    func finish(_ action: String) {
        run(action, ["record_id": str(review,"record_id"), "revision": review["revision"] ?? 0, "review_token": str(review,"review_token")]) { value in
            self.notice = str(value,"status", str(value,"state"))
            self.review = [:]; self.refresh()
        }
    }
}

var busyNotice: String { L("Carry is finishing another background task (indexing or a download). Nothing was started; try again when the progress bar disappears.") }

func str(_ value: [String: Any], _ key: String, _ fallback: String = "—") -> String {
    guard let item = value[key], !(item is NSNull) else { return fallback }
    return String(describing: item)
}
func num(_ value: [String: Any], _ key: String, _ fallback: Double = 0) -> Double {
    if let n = value[key] as? NSNumber { return n.doubleValue }
    return Double(str(value, key, "")) ?? fallback
}
func pretty(_ value: Any) -> String {
    guard JSONSerialization.isValidJSONObject(value), let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]), let text = String(data: data, encoding: .utf8) else { return "—" }
    return text
}
func ago(_ timestamp: Double) -> String {
    let f = RelativeDateTimeFormatter(); f.unitsStyle = .abbreviated; f.locale = Lang.locale
    return f.localizedString(for: Date(timeIntervalSince1970: timestamp), relativeTo: Date())
}
@MainActor func folder(_ title: String) -> String? {
    let panel = NSOpenPanel()
    panel.message = title; panel.canChooseDirectories = true; panel.canChooseFiles = false
    panel.canCreateDirectories = true; panel.allowsMultipleSelection = false
    return panel.runModal() == .OK ? panel.url?.path : nil
}
func obsidianURL(_ absolute: String) -> URL? {
    var allowed = CharacterSet.alphanumerics; allowed.insert(charactersIn: "-._~/")
    guard let path = absolute.addingPercentEncoding(withAllowedCharacters: allowed) else { return nil }
    return URL(string: "obsidian://open?path=" + path)
}
var obsidianInstalled: Bool { NSWorkspace.shared.urlForApplication(toOpen: URL(string: "obsidian://open")!) != nil }

// MARK: - Markdown

/// Inline Markdown with Obsidian [[links]] turned into in-app links (carry-note:target).
func inline(_ text: String) -> AttributedString {
    var s = text
    if let re = try? NSRegularExpression(pattern: #"!?\[\[([^\]|#]+)(#[^\]|]*)?(?:\|([^\]]*))?\]\]"#) {
        let ns = s as NSString
        var out = "", last = 0
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            let target = ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespaces)
            let alias = m.range(at: 3).location != NSNotFound ? ns.substring(with: m.range(at: 3)) : target
            let encoded = target.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? target
            out += "[" + alias.replacingOccurrences(of: "]", with: "") + "](carry-note:" + encoded + ")"
            last = m.range.location + m.range.length
        }
        out += ns.substring(from: last)
        s = out
    }
    return (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
}

enum MDKind { case heading(Int), paragraph, bullet(Int, String), quote, code, rule }
struct MDBlock { let kind: MDKind; let text: String }

func markdownBlocks(_ text: String) -> [MDBlock] {
    var blocks: [MDBlock] = [], paragraph: [String] = [], code: [String]? = nil, table: [String] = []
    func flush() {
        if !paragraph.isEmpty { blocks.append(MDBlock(kind: .paragraph, text: paragraph.joined(separator: "\n"))); paragraph = [] }
        if !table.isEmpty { blocks.append(MDBlock(kind: .code, text: table.joined(separator: "\n"))); table = [] }
    }
    for line in text.components(separatedBy: "\n") {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("```") {
            if let c = code { blocks.append(MDBlock(kind: .code, text: c.joined(separator: "\n"))); code = nil } else { flush(); code = [] }
            continue
        }
        if code != nil { code!.append(line); continue }
        if trimmed.hasPrefix("|") { if !paragraph.isEmpty { let t = table; table = []; flush(); table = t }; table.append(line); continue }
        if !table.isEmpty { flush() }
        if trimmed.isEmpty { flush(); continue }
        if trimmed == "---" || trimmed == "***" { flush(); blocks.append(MDBlock(kind: .rule, text: "")); continue }
        if let hashes = trimmed.firstIndex(where: { $0 != "#" }), trimmed.hasPrefix("#"), trimmed[hashes] == " " {
            flush(); blocks.append(MDBlock(kind: .heading(trimmed.distance(from: trimmed.startIndex, to: hashes)), text: String(trimmed[hashes...]).trimmingCharacters(in: .whitespaces))); continue
        }
        let indent = (line.prefix(while: { $0 == " " || $0 == "\t" }).reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }) / 2
        let isItem = trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") || trimmed.hasPrefix("+ ") || trimmed.range(of: #"^\d+\. "#, options: .regularExpression) != nil
        if !isItem, paragraph.isEmpty, line.first == " " || line.first == "\t", let last = blocks.last, case .bullet(let level, let marker) = last.kind {
            blocks[blocks.count - 1] = MDBlock(kind: .bullet(level, marker), text: last.text + " " + trimmed)
            continue
        }
        if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") || trimmed.hasPrefix("+ ") {
            flush()
            var item = String(trimmed.dropFirst(2)), marker = "•"
            if item.hasPrefix("[ ] ") { marker = "☐"; item = String(item.dropFirst(4)) }
            else if item.lowercased().hasPrefix("[x] ") { marker = "☑"; item = String(item.dropFirst(4)) }
            blocks.append(MDBlock(kind: .bullet(indent, marker), text: item)); continue
        }
        if let dot = trimmed.firstIndex(of: "."), dot > trimmed.startIndex, trimmed[..<dot].allSatisfy(\.isNumber), trimmed[trimmed.index(after: dot)...].hasPrefix(" ") {
            flush(); blocks.append(MDBlock(kind: .bullet(indent, String(trimmed[...dot])), text: String(trimmed[trimmed.index(dot, offsetBy: 2)...]))); continue
        }
        if trimmed.hasPrefix(">") { flush(); blocks.append(MDBlock(kind: .quote, text: String(trimmed.drop(while: { $0 == ">" || $0 == " " })))); continue }
        paragraph.append(line)
    }
    if let c = code { blocks.append(MDBlock(kind: .code, text: c.joined(separator: "\n"))) }
    flush()
    return blocks
}

struct MarkdownText: View {
    let blocks: [MDBlock]
    init(_ text: String) { blocks = markdownBlocks(text) }
    var body: some View {
        LazyVStack(alignment: .leading, spacing: 8) {
            ForEach(blocks.indices, id: \.self) { i in block(blocks[i]) }
        }.textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
    }
    @ViewBuilder func block(_ b: MDBlock) -> some View {
        switch b.kind {
        case .heading(let level):
            Text(inline(b.text)).font(level == 1 ? .title2.bold() : level == 2 ? .title3.bold() : .headline).padding(.top, level <= 2 ? 10 : 4)
        case .paragraph:
            Text(inline(b.text)).fixedSize(horizontal: false, vertical: true)
        case .bullet(let indent, let marker):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker).foregroundStyle(.secondary)
                Text(inline(b.text)).fixedSize(horizontal: false, vertical: true)
            }.padding(.leading, CGFloat(indent) * 16)
        case .quote:
            Text(inline(b.text)).italic().foregroundStyle(.secondary).padding(.leading, 12)
                .overlay(alignment: .leading) { Rectangle().fill(Color.secondary.opacity(0.4)).frame(width: 3) }
        case .code:
            ScrollView(.horizontal, showsIndicators: false) {
                Text(b.text).font(.system(.callout, design: .monospaced)).padding(10)
            }.frame(minWidth: 0, idealWidth: 0, maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 6))
        case .rule:
            Divider()
        }
    }
}

// MARK: - Shared pieces

/// Lays its children out left to right and moves to a new line when the width runs out,
/// so buttons, chips and badges are never cut off with “…” in a narrow window.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8
    private func size(_ view: LayoutSubview, _ maxWidth: CGFloat) -> CGSize {
        let ideal = view.sizeThatFits(.unspecified)
        return ideal.width <= maxWidth ? ideal : view.sizeThatFits(ProposedViewSize(width: maxWidth, height: nil))
    }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        // Asked for an ideal size (no width offered), report the widest single child: a whole
        // row laid out in one line would make split views open wider than the window.
        let maxWidth = proposal.width ?? subviews.map { $0.sizeThatFits(.unspecified).width }.max() ?? 0
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0, widest: CGFloat = 0
        for view in subviews {
            let s = size(view, maxWidth)
            if x > 0 && x + s.width > maxWidth { y += line + lineSpacing; x = 0; line = 0 }
            x += s.width + spacing; line = max(line, s.height); widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, maxWidth), height: y + line)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, line: CGFloat = 0
        for view in subviews {
            let s = size(view, bounds.width)
            if x > bounds.minX && x + s.width > bounds.maxX { y += line + lineSpacing; x = bounds.minX; line = 0 }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing; line = max(line, s.height)
        }
    }
}

struct Heading: View {
    let title: String; let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.largeTitle.bold())
            Text(subtitle).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }.padding(.bottom, 14)
    }
}

struct Badge: View {
    let text: String; var color: Color = .secondary
    var body: some View {
        Text(text).font(.caption.weight(.medium)).padding(.horizontal, 7).padding(.vertical, 2)
            .foregroundStyle(color).background(color.opacity(0.12)).clipShape(Capsule())
    }
}

struct Tile: View {
    let title: String; let value: String; let detail: String
    var action: (() -> Void)? = nil
    var body: some View {
        let content = GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Text(title).foregroundStyle(.secondary)
                Text(value).font(.title2.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.6)
                HStack(alignment: .bottom) {
                    Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    if action != nil { Image(systemName: "arrow.right.circle").foregroundStyle(Color.accentColor) }
                }
            }.padding(8).frame(maxWidth: .infinity, minHeight: 92, maxHeight: 92, alignment: .topLeading)
        }
        if let action { Button(action: action) { content }.buttonStyle(.plain).help(L("Show in Vault")) } else { content }
    }
}

// MARK: - Plain-language pieces for first-time users

/// Inline two-language text for the explanatory copy (short labels go through L()).
func T(_ en: String, _ tr: String) -> String { Lang.code == "tr" ? tr : en }

func pageName(_ key: String) -> String {
    switch key {
    case "Overview": return T("Home", "Ana sayfa")
    case "Vault": return T("Notes", "Notlar")
    case "Search": return T("Search notes", "Notlarda ara")
    case "Review": return T("Review", "İncele")
    case "Proposals": return T("Change proposals", "Değişiklik önerileri")
    default: return T("Settings", "Ayarlar")
    }
}
func tabName(_ key: String) -> String {
    switch key {
    case "Search": return T("Search", "Arama")
    case "Sources": return T("Note folders", "Not klasörleri")
    case "Harvest": return T("Chat notes", "Sohbet notları")
    case "Clients": return T("Assistants", "Asistanlar")
    default: return T("General", "Genel")
    }
}

/// A short, friendly explanation at the top of a page: what it is and what to do here.
struct PageIntro: View {
    let icon: String; let title: String; let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon).font(.system(size: 26)).foregroundStyle(Color.accentColor).frame(width: 36)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.largeTitle.bold())
                Text(text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }.padding(.bottom, 10)
    }
}

/// ⓘ next to a term: explains it in a popover.
struct InfoButton: View {
    let text: String
    @State private var shown = false
    var body: some View {
        Button { shown.toggle() } label: { Image(systemName: "questionmark.circle") }
            .buttonStyle(.borderless).foregroundStyle(.secondary)
            .popover(isPresented: $shown, arrowEdge: .bottom) {
                Text(text).padding(14).frame(width: 300, alignment: .leading).fixedSize(horizontal: false, vertical: true)
            }
    }
}

let glossary: [(String, String, String, String)] = [
    ("Notes folder", "Not klasörü",
     "The folder where your notes live as plain text files (Markdown, .md). Apps like Obsidian call it a vault. Carry only reads it unless you allow otherwise.",
     "Notlarınızın düz metin dosyaları (Markdown, .md) olarak durduğu klasör. Obsidian gibi uygulamalar buna \u{201C}vault\u{201D} (kasa) der. Siz izin vermedikçe Carry yalnızca okur."),
    ("Assistant", "Asistan",
     "An AI tool such as Claude Code or Codex. Once connected, it looks in your notes before answering.",
     "Claude Code veya Codex gibi bir yapay zekâ aracı. Bağlandığında, cevap vermeden önce notlarınıza bakar."),
    ("Search data", "Arama için hazırlanan bilgiler",
     "What Carry prepares from your notes so it can find the right one quickly. It updates by itself when notes change and can always be rebuilt.",
     "Carry'nin doğru notu hızlıca bulmak için notlarınızdan hazırladığı bilgiler. Notlar değişince kendiliğinden güncellenir ve her zaman yeniden oluşturulabilir."),
    ("Chat notes", "Sohbet notları",
     "When a chat with your assistant ends, Carry picks out decisions, facts and unfinished work and saves them as drafts for you.",
     "Asistanınızla bir sohbet bittiğinde Carry kararları, bilgileri ve yarım kalan işleri seçip sizin için taslak olarak kaydeder."),
    ("Inbox", "Gelen kutusu",
     "The + folder, where new chat drafts and captures collect. What waits for you there is on the Review page.",
     "Yeni sohbet taslaklarının ve kayıtların toplandığı + klasörü. Orada sizi bekleyenler İncele sayfasındadır."),
    ("Draft", "Taslak",
     "A note you have not checked yet. Chat drafts in your Inbox wait for you on the Review page. Notes your assistants write elsewhere are marked as drafts too, but they are searchable and never wait for you; approve one when you have checked it.",
     "Henüz kontrol etmediğiniz not. Gelen kutusundaki sohbet taslakları İncele sayfasında sizi bekler. Asistanlarınızın başka yerlere yazdığı notlar da taslak işaretlidir ama aranabilir ve sizi beklemez; kontrol ettiğinizde onaylayabilirsiniz."),
    ("Change proposals", "Değişiklik önerileri",
     "Corrections an assistant proposes to notes it may not change directly. You accept or reject each one.",
     "Asistanın doğrudan değiştiremediği notlar için önerdiği düzeltmeler. Her birini siz kabul eder ya da reddedersiniz."),
]

struct GuideSheet: View {
    @ObservedObject var model: Model
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack { Text(T("How Carry works", "Carry nasıl çalışır?")).font(.title.bold()); Spacer(); Button(T("Close", "Kapat")) { dismiss() }.keyboardShortcut(.cancelAction) }.padding(24)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(T("Carry gives your AI assistant a memory: your own notes. When you ask Claude Code or Codex something, it first looks in your notes; when a chat ends, Carry saves what was decided as a draft note. Your notes stay on this Mac, in a folder you choose.",
                           "Carry, yapay zekâ asistanınıza bir hafıza verir: kendi notlarınız. Claude Code veya Codex'e bir şey sorduğunuzda önce notlarınıza bakar; sohbet bitince Carry alınan kararları taslak not olarak kaydeder. Notlarınız bu Mac'te, sizin seçtiğiniz bir klasörde kalır."))
                        .fixedSize(horizontal: false, vertical: true)
                    FlowLayout {
                        Button(T("Write my first note", "İlk notumu yaz")) { dismiss(); model.showNewNote = true }
                        Button(T("Use it with my assistant", "Asistanımla kullan")) { dismiss(); model.page = "Settings"; model.settingsTab = "Clients" }
                        Button(T("Review chat drafts", "Sohbet taslaklarını incele")) { dismiss(); model.page = "Review" }
                    }
                    Divider()
                    ForEach(glossary.indices, id: \.self) { i in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(T(glossary[i].0, glossary[i].1)).font(.headline)
                            Text(T(glossary[i].2, glossary[i].3)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Button(T("Run the setup guide again", "Kurulum rehberini yeniden aç")) { dismiss(); model.startOnboarding() }
                }.padding(24)
            }
        }.frame(width: 560, height: 620)
    }
}

/// Opens Terminal in the notes folder and starts the assistant there (macOS asks once to allow it).
@MainActor func launchAssistant(_ client: String, in root: String) -> String? {
    let quoted = "'" + root.replacingOccurrences(of: "'", with: "'\\''") + "'"
    let command = "cd \(quoted) && \(client)"
    let escaped = command.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
    var error: NSDictionary?
    NSAppleScript(source: "tell application \"Terminal\"\nactivate\ndo script \"\(escaped)\"\nend tell")?.executeAndReturnError(&error)
    return error == nil ? nil : T("Terminal could not be opened. Allow Carry to control Terminal in System Settings › Privacy & Security › Automation.",
                                  "Terminal açılamadı. Sistem Ayarları › Gizlilik ve Güvenlik › Otomasyon bölümünden Carry'nin Terminal'i kontrol etmesine izin verin.")
}

/// Write a first note without leaving Carry.
struct NewNoteSheet: View {
    @ObservedObject var model: Model
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var text = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(T("New note", "Yeni not")).font(.title2.bold())
            Text(T("Write anything your assistant should know: a project, a decision, how you like things done. You can edit it later in any text app.",
                   "Asistanınızın bilmesini istediğiniz her şeyi yazın: bir proje, bir karar, işlerin nasıl yapılmasını sevdiğiniz. Daha sonra herhangi bir metin uygulamasında düzenleyebilirsiniz."))
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextField(T("Title, e.g. Website redesign", "Başlık, örn. Web sitesi yenileme"), text: $title).textFieldStyle(.roundedBorder)
            TextEditor(text: $text).font(.body).frame(minHeight: 180).overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.3)))
            HStack {
                Spacer()
                Button(T("Cancel", "Vazgeç")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(T("Save note", "Notu kaydet")) {
                    model.run("note_create", ["title": title, "body": text]) { value in
                        dismiss(); model.refresh(); model.openNote(str(value, "path"))
                        model.notice = T("Note saved. Carry adds it to search in a few seconds.", "Not kaydedildi. Carry birkaç saniye içinde aramaya ekler.")
                    }
                }.buttonStyle(.borderedProminent).disabled(title.trimmingCharacters(in: .whitespaces).isEmpty).keyboardShortcut(.defaultAction)
            }
        }.padding(24).frame(width: 560)
    }
}

// MARK: - First run

struct Onboarding: View {
    @ObservedObject var model: Model
    @State private var notesLanguage = Lang.code == "tr" ? "Turkish" : "English"
    let steps = [("Welcome", "Tanışma"), ("Your notes", "Notlarınız"), ("Assistant", "Asistan"), ("Ready", "Hazır")]
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 18) {
                HStack(spacing: 8) {
                    Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 30, height: 30)
                    Text("Carry").font(.title2.bold())
                }
                Spacer()
                ForEach(steps.indices, id: \.self) { i in
                    HStack(spacing: 6) {
                        Image(systemName: i < model.onboardingStep ? "checkmark.circle.fill" : (i == model.onboardingStep ? "circle.inset.filled" : "circle"))
                            .foregroundStyle(i <= model.onboardingStep ? Color.accentColor : Color.secondary)
                        Text(T(steps[i].0, steps[i].1)).font(.callout).foregroundStyle(i == model.onboardingStep ? .primary : .secondary)
                    }
                }
                Spacer()
                Picker("", selection: $model.language) { Text("Türkçe").tag("tr"); Text("English").tag("en") }.pickerStyle(.segmented).labelsHidden().frame(width: 150)
            }.padding(20)
            Divider()
            if !model.error.isEmpty {
                HStack { Text(model.error).textSelection(.enabled); Spacer(); Button { model.error = "" } label: { Image(systemName: "xmark") }.buttonStyle(.plain) }
                    .padding(12).foregroundStyle(.red).background(Color.red.opacity(0.07))
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    switch model.onboardingStep {
                    case 0: welcome
                    case 1: notes
                    case 2: assistant
                    default: ready
                    }
                }.padding(40).frame(maxWidth: 780, alignment: .leading).frame(maxWidth: .infinity)
            }
            if model.busy { HStack { ProgressView().controlSize(.small); Text(T("Working…", "Hazırlanıyor…")) }.padding(10) }
        }.disabled(model.busy)
    }

    func point(_ icon: String, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon).font(.title2).foregroundStyle(Color.accentColor).frame(width: 34)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    var welcome: some View {
        VStack(alignment: .leading, spacing: 22) {
            Text(T("Welcome to Carry", "Carry'ye hoş geldiniz")).font(.largeTitle.bold())
            Text(T("Carry turns your notes into a memory for your AI assistant.",
                   "Carry, notlarınızı yapay zekâ asistanınızın hafızasına dönüştürür.")).font(.title3).foregroundStyle(.secondary)
            Text(T("Carry works with Claude Code and Codex on this Mac. It does not connect to the regular Claude or ChatGPT chat apps.",
                   "Carry bu Mac'te Claude Code ve Codex ile çalışır. Claude'un ya da ChatGPT'nin normal sohbet uygulamalarına bağlanmaz."))
                .padding(10).background(Color.accentColor.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 8)).fixedSize(horizontal: false, vertical: true)
            point("brain.head.profile", T("Your assistant uses your notes", "Asistanınız notlarınızdan yararlanır"),
                  T("When you ask a question, your connected assistant can look up the related parts of your notes through Carry.",
                    "Bir soru sorduğunuzda bağlı asistanınız, Carry üzerinden notlarınızın ilgili bölümlerine bakabilir."))
            point("text.bubble", T("Chats turn into notes", "Sohbetlerden not çıkar"),
                  T("When chat notes are on, Carry saves the decisions and to-dos from your chats as drafts for you to check.",
                    "Sohbet notları açıkken Carry, sohbetlerinizdeki kararları ve yapılacak işleri kontrol etmeniz için taslak olarak kaydeder."))
            point("lock.shield", T("Your files stay on your Mac", "Dosyalarınız Mac'inizde kalır"),
                  T("Your notes are ordinary text files in a folder you choose. Only the parts related to a question go to the assistant you use; some search options also use an online service, and they say so.",
                    "Notlarınız seçtiğiniz bir klasörde duran sıradan metin dosyalarıdır. Yalnızca sorunuzla ilgili bölümler kullandığınız asistana gider; bazı arama seçenekleri ayrıca çevrimiçi bir hizmet kullanır ve bunu açıkça belirtir."))
            HStack {
                Button(T("Let's start", "Başlayalım")) { model.prepareWorkspace { model.onboardingStep = 1 } }.buttonStyle(.borderedProminent).controlSize(.large).keyboardShortcut(.defaultAction)
                Spacer()
                Button(T("Choose my existing Carry setup…", "Mevcut Carry kurulumumu seç…")) { model.chooseWorkspace(create: false) }.buttonStyle(.link)
            }.padding(.top, 8)
        }
    }

    var notes: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(T("Where should your notes live?", "Notlarınız nerede dursun?")).font(.largeTitle.bold())
            Text(T("Carry needs one folder for your notes. You can start fresh, or use notes you already have.",
                   "Carry'nin notlarınız için bir klasöre ihtiyacı var. Sıfırdan başlayabilir ya da mevcut notlarınızı kullanabilirsiniz.")).foregroundStyle(.secondary)
            if let first = model.localSources.first {
                GroupBox {
                    HStack {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                        VStack(alignment: .leading) { Text(T("A notes folder is already connected", "Bağlı bir not klasörünüz zaten var")).font(.headline); Text(str(first, "root")).font(.caption).foregroundStyle(.secondary) }
                        Spacer()
                        Button(T("Continue with it", "Bununla devam et")) { model.onboardingStep = 2 }.buttonStyle(.borderedProminent)
                    }.padding(8)
                }
            }
            HStack(alignment: .top, spacing: 18) {
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Image(systemName: "sparkles.rectangle.stack").font(.largeTitle).foregroundStyle(Color.accentColor)
                        Text(T("Create a new notes folder", "Yeni bir not klasörü oluştur")).font(.title3.bold())
                        Text(T("Carry prepares a folder for your notes, with a short guide for your assistant and tidy sub-folders. You write your first note right after.",
                               "Carry notlarınız için bir klasör hazırlar: içinde asistanınız için kısa bir kılavuz ve düzenli alt klasörler olur. Hemen ardından ilk notunuzu yazabilirsiniz."))
                            .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        Picker(T("Language of chat notes", "Sohbet notlarının dili"), selection: $notesLanguage) { Text("Türkçe").tag("Turkish"); Text("English").tag("English") }.pickerStyle(.segmented)
                        Text(T("Location: ", "Konum: ") + suggestedFolder).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        FlowLayout {
                            Button(T("Create my notes folder", "Not klasörümü oluştur")) { createNotes(at: suggestedFolder) }.buttonStyle(.borderedProminent)
                            Button(T("Create somewhere else…", "Başka konumda oluştur…")) { if let f = folder(T("Choose or create an empty folder for your notes", "Notlarınız için boş bir klasör seçin ya da oluşturun")) { createNotes(at: f) } }
                        }
                    }.padding(12).frame(maxWidth: .infinity, minHeight: 280, alignment: .topLeading)
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Image(systemName: "folder.badge.person.crop").font(.largeTitle).foregroundStyle(Color.accentColor)
                        Text(T("I have .md note files", "Bilgisayarımda .md not dosyalarım var")).font(.title3.bold())
                        Text(T("Choose the folder with notes whose file names end in .md (for example an Obsidian folder). Apple Notes, Word and PDF files are not added this way. Your notes' contents are not changed.",
                               "Dosya adı .md ile biten notların bulunduğu klasörü seçin (örneğin bir Obsidian klasörü). Apple Notlar, Word ve PDF dosyaları bu yolla eklenmez. Notlarınızın içeriği değiştirilmez."))
                            .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                        Button(T("Choose my notes folder…", "Not klasörümü seç…")) { useExisting() }.buttonStyle(.borderedProminent)
                    }.padding(12).frame(maxWidth: .infinity, minHeight: 280, alignment: .topLeading)
                }
            }
            Button(T("Back", "Geri")) { model.onboardingStep = 0 }.buttonStyle(.link)
        }
    }

    var suggestedFolder: String {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let base = T("Carry Notes", "Carry Notları")
        var candidate = docs.appendingPathComponent(base)
        var n = 2
        while let items = try? FileManager.default.contentsOfDirectory(atPath: candidate.path), !items.filter({ !$0.hasPrefix(".") }).isEmpty {
            candidate = docs.appendingPathComponent("\(base) \(n)"); n += 1
        }
        return candidate.path
    }

    func createNotes(at target: String) {
        try? FileManager.default.createDirectory(atPath: target, withIntermediateDirectories: true)
        model.run("vault_preview", ["target": target, "language": notesLanguage, "capture": false]) { plan in
            model.run("vault_apply", ["id": str(plan, "id")]) { _ in
                model.vaultSource = ""; model.refresh(); model.onboardingStep = 2
            }
        }
    }

    func useExisting() {
        guard let root = folder(T("Choose the folder that holds your notes", "Notlarınızın durduğu klasörü seçin")) else { return }
        // Check before connecting: a folder without .md files would leave the user with an empty app.
        var count = 0
        if let walker = FileManager.default.enumerator(atPath: root) {
            while let item = walker.nextObject() as? String { if item.lowercased().hasSuffix(".md") { count += 1; if count > 2000 { break } } }
        }
        if count == 0 {
            model.error = T("No .md note files were found in that folder. Choose another folder, or create a new notes folder on the left.",
                            "Bu klasörde .md not dosyası bulunamadı. Başka bir klasör seçin ya da soldan yeni bir not klasörü oluşturun.")
            return
        }
        model.notice = T("\(count) note file(s) found.", "\(count) not dosyası bulundu.")
        let taken = Set(model.sources.map { str($0, "source_id") })
        var sid = "notes", n = 2
        while taken.contains(sid) { sid = "notes-\(n)"; n += 1 }
        model.run("add_source", ["source_id": sid, "root": root, "writable": false]) { _ in
            model.vaultSource = ""; model.refresh(); model.onboardingStep = 2
        }
    }

    var assistant: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(T("Connect your assistant", "Asistanınızı bağlayın")).font(.largeTitle.bold())
            Text(T("An assistant is an AI tool you talk to, such as Claude Code or Codex. Connecting one is enough.",
                   "Asistan, konuştuğunuz bir yapay zekâ aracıdır; Claude Code veya Codex gibi. Birini bağlamanız yeterli.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            AssistantCard(model: model, client: "claude")
            AssistantCard(model: model, client: "codex")
            FlowLayout {
                Button(T("Continue", "Devam")) { model.onboardingStep = 3 }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                if !model.anyAssistantConnected { Button(T("Skip for now", "Şimdilik atla")) { model.onboardingStep = 3 } }
                Button(T("Back", "Geri")) { model.onboardingStep = 1 }.buttonStyle(.link)
            }
        }.onAppear { model.loadAssistants() }
    }

    var ready: some View {
        let root = model.notesRoot
        let connected = ["claude", "codex"].filter { (model.assistants[$0] as? [String: Any])?["connected"] as? Bool == true }
        return VStack(alignment: .leading, spacing: 20) {
            Text(connected.isEmpty ? T("Your notes folder is ready", "Not klasörünüz hazır") : T("You're all set", "Hazırsınız")).font(.largeTitle.bold())
            if connected.isEmpty {
                Text(T("No assistant is connected yet, so nothing can search your notes for now. You can connect one any time under Settings › Assistants.",
                       "Henüz bir asistan bağlı değil; şimdilik notlarınızda arama yapan bir şey yok. İstediğiniz zaman Ayarlar › Asistanlar'dan bağlayabilirsiniz."))
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Button(T("Connect an assistant", "Asistan bağla")) { model.onboardingStep = 2 }
            } else {
                Text(T("Here is how you use Carry day to day:", "Carry'yi günlük olarak şöyle kullanırsınız:")).foregroundStyle(.secondary)
            }
            if model.ownNotes == 0 {
                point("square.and.pencil", T("Write your first note", "İlk notunuzu yazın"),
                      T("Your assistant can only use what is written down. Start with something it should know: a project, a decision, a preference.",
                        "Asistanınız yalnızca yazılı olanı kullanabilir. Bilmesini istediğiniz bir şeyle başlayın: bir proje, bir karar, bir tercih."))
                Button(T("Write my first note", "İlk notumu yaz")) { model.showNewNote = true }.buttonStyle(.borderedProminent).padding(.leading, 48)
            }
            if !connected.isEmpty {
                point("play.circle.fill", T("Start your assistant with your notes", "Asistanınızı notlarınızla başlatın"),
                      T("Carry opens Terminal in your notes folder and starts it. The first time, it may ask permission to use Carry: allow it.",
                        "Carry, Terminal'i not klasörünüzde açıp asistanı başlatır. İlk seferde Carry'yi kullanmak için izin isteyebilir: izin verin."))
                FlowLayout {
                    ForEach(connected, id: \.self) { client in
                        let name = client == "claude" ? "Claude Code" : "Codex"
                        Button(T("Start \(name)", "\(name)'u başlat")) { if let e = launchAssistant(client, in: root) { model.error = e } }.buttonStyle(.borderedProminent)
                    }
                }.padding(.leading, 48)
                DisclosureGroup(T("Prefer to type it yourself?", "Kendiniz yazmak ister misiniz?")) {
                    Text("cd \"\(root)\" && \(connected[0])").font(.system(.callout, design: .monospaced)).textSelection(.enabled).padding(8).background(Color.secondary.opacity(0.1)).clipShape(RoundedRectangle(cornerRadius: 6))
                }.padding(.leading, 48)
                point("text.bubble", T("Ask as you normally would", "Her zamanki gibi sorun"),
                      T("Your assistant looks up the related parts of your notes before it answers.",
                        "Asistanınız cevap vermeden önce notlarınızın ilgili bölümlerine bakar."))
                point("tray", T("Check the drafts now and then", "Taslaklara arada bir göz atın"),
                      T("After chats, Carry puts what is worth keeping into your Inbox. Go through it on the Review page and approve what is right.",
                        "Sohbetlerden sonra Carry saklanmaya değer olanları Gelen kutunuza koyar. İncele sayfasında gözden geçirip doğru olanları onaylayın."))
            }
            FlowLayout {
                Button(T("Open the home page", "Ana sayfayı aç")) { model.finishOnboarding() }.controlSize(.large).keyboardShortcut(.defaultAction)
                if !root.isEmpty { Button(T("Show notes folder in Finder", "Not klasörünü Finder'da göster")) { NSWorkspace.shared.open(URL(fileURLWithPath: root)) } }
            }.padding(.top, 6)
        }
    }
}

/// One assistant: installed? connected to the notes folder? One button to connect it.
struct AssistantCard: View {
    @ObservedObject var model: Model
    let client: String
    @State private var preview: [String: Any] = [:]
    var name: String { client == "claude" ? "Claude Code" : "Codex" }
    var info: [String: Any] { model.assistants[client] as? [String: Any] ?? [:] }
    var installURL: URL { URL(string: client == "claude" ? "https://docs.anthropic.com/en/docs/claude-code/overview" : "https://github.com/openai/codex")! }
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Image(systemName: client == "claude" ? "sparkle" : "terminal").font(.title3).foregroundStyle(Color.accentColor).frame(width: 26)
                    Text(name).font(.headline)
                    Spacer()
                    if info["connected"] as? Bool == true { Badge(text: T("Set up", "Bağlantı ayarlandı"), color: .green) }
                    else if info["installed"] as? Bool == false { Badge(text: T("Not installed", "Kurulu değil")) }
                    else if !info.isEmpty { Badge(text: T("Not connected", "Bağlı değil"), color: .orange) }
                }
                if info["connected"] as? Bool == true {
                    Text(T("\(name) can search your notes. The first time you start it here, it may ask permission to use Carry: allow it.",
                           "\(name) notlarınızda arama yapabilir. Buradan ilk başlattığınızda Carry'yi kullanmak için izin isteyebilir: izin verin.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Button(T("Start \(name) with my notes", "\(name)'u notlarımla başlat")) { if let e = launchAssistant(client, in: model.notesRoot) { model.error = e } }.disabled(model.notesRoot.isEmpty)
                } else if info["installed"] as? Bool == false {
                    Text(T("\(name) was not found on this Mac.", "\(name) bu Mac'te bulunamadı.")).foregroundStyle(.secondary)
                    FlowLayout {
                        Link(T("Open install steps", "Kurulum adımlarını aç"), destination: installURL)
                        Button(T("Check again", "Yeniden kontrol et")) { model.loadAssistants() }.buttonStyle(.link)
                    }
                } else if preview.isEmpty {
                    Text(T("Lets \(name) search your notes. Carry adds one small settings file to your notes folder; you can undo it any time.",
                           "\(name)'in notlarınızda arama yapmasını sağlar. Carry, not klasörünüze küçük bir ayar dosyası ekler; istediğiniz zaman geri alabilirsiniz.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Button(T("Connect \(name)", "\(name)'i bağla")) {
                        model.run("connection_preview", ["client": client, "project": model.notesRoot, "source_id": "", "prompts": false, "proposals": false, "executable": ""]) { preview = $0 }
                    }.buttonStyle(.borderedProminent).disabled(model.notesRoot.isEmpty)
                } else {
                    Text(T("Carry will set up the connection in this folder. Your notes will not change.", "Carry bu klasörde asistan bağlantısını ayarlayacak. Notlarınız değişmeyecek.")).fontWeight(.medium)
                    DisclosureGroup(T("Technical details", "Teknik ayrıntılar")) {
                        Text(str(preview, "summary", "")).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    FlowLayout {
                        Button(T("Connect", "Bağla")) {
                            model.run("connection_apply", ["id": str(preview, "id")]) { _ in
                                preview = [:]; model.loadAssistants(); model.refresh()
                                model.notice = T("\(name) is connected. Open it in your notes folder; the first time it asks you to approve Carry.", "\(name) bağlandı. Not klasörünüzde açın; ilk seferde Carry'yi onaylamanızı ister.")
                            }
                        }.buttonStyle(.borderedProminent)
                        Button(T("Cancel", "Vazgeç")) { preview = [:] }
                    }
                }
            }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - Home: setup status in plain words

struct SetupChecklist: View {
    @ObservedObject var model: Model
    var index: [String: Any] { model.snapshot["index"] as? [String: Any] ?? [:] }
    var schedule: [String: Any] { model.vault["schedule"] as? [String: Any] ?? [:] }
    var assistantConnected: Bool { ["claude", "codex"].contains { (model.assistants[$0] as? [String: Any])?["connected"] as? Bool == true } }
    var searchReady: Bool { str(index, "state") == "fresh" }
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 10) {
                    Image(systemName: searchReady && assistantConnected ? "checkmark.seal.fill" : "exclamationmark.circle.fill")
                        .font(.title2).foregroundStyle(searchReady && assistantConnected ? .green : .orange)
                    Text(!(searchReady && assistantConnected) ? T("Almost there. Finish the steps marked below.", "Neredeyse hazır. Aşağıda işaretli adımları tamamlayın.")
                         : model.ownNotes == 0 ? T("Setup is complete. Start by adding your first note.", "Kurulum tamamlandı. İlk notunuzu ekleyerek başlayın.")
                         : T("All set. Your assistant can search your \(str(index, "indexed_files", "0")) notes.", "Her şey hazır. Asistanınız \(str(index, "indexed_files", "0")) notunuzda arama yapabiliyor.")).font(.headline)
                }
                Divider()
                row(!model.localSources.isEmpty, T("Notes folder", "Not klasörü"),
                    model.localSources.isEmpty ? T("None yet", "Henüz yok") : str(model.vault, "root", str(model.localSources.first ?? [:], "root")),
                    action: model.localSources.isEmpty ? (T("Add", "Ekle"), { model.startOnboarding() }) : nil)
                row(searchReady, T("Search", "Arama"),
                    searchReady ? (str(index, "indexed_files", "0") == "0" ? T("Ready (no notes to search yet)", "Hazır (henüz aranacak not yok)") : T("Ready", "Hazır")) : (str(index, "state") == "unavailable" ? T("Not built yet", "Henüz hazır değil") : T("Updating after recent changes…", "Son değişikliklerden sonra güncelleniyor…")),
                    action: str(index, "state") == "unavailable" ? (T("Build now", "Şimdi hazırla"), { model.run("index") { model.job = $0 } }) : nil,
                    pending: !searchReady && str(index, "state") != "unavailable")
                ForEach(["claude", "codex"], id: \.self) { client in
                    let info = model.assistants[client] as? [String: Any] ?? [:]
                    let name = client == "claude" ? "Claude Code" : "Codex"
                    if info["installed"] as? Bool == true || info["connected"] as? Bool == true {
                        row(info["connected"] as? Bool == true, name,
                            info["connected"] as? Bool == true ? T("Set up", "Bağlantı ayarlandı") : T("Not connected yet", "Henüz bağlı değil"),
                            action: info["connected"] as? Bool == true ? nil : (T("Connect", "Bağla"), { model.page = "Settings"; model.settingsTab = "Clients" }))
                    }
                }
                let nightly = schedule["installed"] as? Bool == true && schedule["this_workspace"] as? Bool != false
                let chatEnd = model.assistants["chat_end"] as? Bool == true
                let time = String(format: "%02d:%02d", Int(num(schedule, "hour")), Int(num(schedule, "minute")))
                row(nightly || chatEnd, T("Chat notes", "Sohbet notları"),
                    chatEnd && nightly ? T("When a chat ends, and every evening at \(time)", "Sohbet bitince ve her akşam \(time)")
                    : chatEnd ? T("When a chat ends", "Sohbet bitince")
                    : nightly ? T("Every evening at \(time)", "Her akşam \(time)")
                    : T("Off: your chats are not turned into notes", "Kapalı: sohbetleriniz nota dönüşmüyor"),
                    action: nightly || chatEnd ? nil : (T("Turn on", "Aç"), { model.page = "Settings"; model.settingsTab = "Harvest" }), optional: true)
            }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    func row(_ done: Bool, _ title: String, _ detail: String, action: (String, () -> Void)? = nil, pending: Bool = false, optional: Bool = false) -> some View {
        HStack(spacing: 10) {
            Image(systemName: done ? "checkmark.circle.fill" : (pending ? "arrow.triangle.2.circlepath" : (optional ? "circle" : "exclamationmark.circle")))
                .foregroundStyle(done ? .green : (optional || pending ? .secondary : .orange)).frame(width: 18)
            Text(title).frame(width: 130, alignment: .leading)
            Text(detail).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            Spacer()
            if let action { Button(action.0, action: action.1) }
        }
    }
}

struct HowToCard: View {
    @AppStorage("hideHowTo") private var hidden = false
    var body: some View {
        if !hidden {
            GroupBox {
                VStack(alignment: .leading, spacing: 10) {
                    HStack { Label(T("How to use Carry", "Carry nasıl kullanılır?"), systemImage: "lightbulb").font(.headline); Spacer(); Button(T("Hide", "Gizle")) { hidden = true }.buttonStyle(.link) }
                    Text(T("1. Open Claude Code or Codex in your notes folder.   2. Ask as usual; it checks your notes first.   3. After chats, review the drafts Carry puts in your Inbox.",
                           "1. Claude Code veya Codex'i not klasörünüzde açın.   2. Her zamanki gibi sorun; önce notlarınıza bakar.   3. Sohbetlerden sonra Carry'nin Gelen kutusuna koyduğu taslakları gözden geçirin."))
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

// MARK: - Overview

// MARK: - Review: one place to go through drafts

/// What waits for the owner: inbox drafts (chat digests, captures). The rule lives in Python
/// (vaultview.awaits_review); assistants' notes elsewhere stay drafts without queueing here.
func reviewable(_ files: [[String: Any]]) -> [[String: Any]] {
    files.filter { $0["review"] as? Bool == true }.sorted { num($0, "modified") > num($1, "modified") }
}

struct ReviewView: View {
    @ObservedObject var model: Model
    @State private var folder = ""
    @State private var picked: Set<String> = []
    @State private var confirmAll = false
    func top(_ f: [String: Any]) -> String { str(f, "path").contains("/") ? String(str(f, "path").split(separator: "/")[0]) : "" }
    var all: [[String: Any]] { reviewable(model.files) }
    var drafts: [[String: Any]] { folder.isEmpty ? all : all.filter { top($0) == folder } }
    /// What bulk approval may touch: chat digests are decided item by item, never approved wholesale.
    var bulk: [[String: Any]] { drafts.filter { $0["approvable"] as? Bool == true } }
    var folders: [(String, Int)] {
        var counts: [String: Int] = [:]
        for f in all { counts[top(f), default: 0] += 1 }
        return counts.sorted { $0.value > $1.value }.map { ($0.key, $0.value) }
    }
    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 12) {
                Text(pageName("Review")).font(.title.bold())
                Text(T("What waits in your Inbox. Open a chat draft and accept, fix or skip each item; accepted items go to that day's log. Other captures you approve as a whole. Notes your assistants write elsewhere are searchable as they are and do not wait here; find them with the Drafts filter on the Notes page.",
                       "Gelen kutunuzda bekleyenler. Bir sohbet taslağını açıp her maddeyi kabul edin, düzeltin ya da atlayın; kabul edilenler o günün log'una gider. Diğer kayıtları bütün olarak onaylarsınız. Asistanlarınızın başka klasörlere yazdığı notlar olduğu gibi aranabilir ve burada beklemez; Notlar sayfasındaki Taslaklar filtresiyle bulursunuz."))
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if model.pendingReview > 0 {
                    Button { model.page = "Proposals" } label: {
                        Label(T("\(model.pendingReview) change proposal(s) wait for your answer", "\(model.pendingReview) değişiklik önerisi cevabınızı bekliyor"), systemImage: "exclamationmark.bubble")
                            .frame(maxWidth: .infinity, alignment: .leading).padding(10).background(Color.orange.opacity(0.1)).clipShape(RoundedRectangle(cornerRadius: 8))
                    }.buttonStyle(.plain)
                }
                if all.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(T("Nothing to review", "İncelenecek bir şey yok"), systemImage: "checkmark.circle").font(.headline).foregroundStyle(.green)
                        Text(T("New drafts appear here after your chats and your assistants' work.", "Sohbetlerinizden ve asistanlarınızın çalışmalarından sonra yeni taslaklar burada görünür.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }.padding(.top, 20)
                    Spacer()
                } else {
                    if folders.count > 1 {
                        Picker(T("Folder", "Klasör"), selection: $folder) {
                            Text(T("All folders (\(all.count))", "Tüm klasörler (\(all.count))")).tag("")
                            ForEach(folders, id: \.0) { Text("\(folderLabel($0.0.isEmpty ? "(root)" : $0.0)) (\($0.1))").tag($0.0) }
                        }.onChange(of: folder) { picked = [] }
                    }
                    if !bulk.isEmpty {
                        FlowLayout {
                            Button(T("Approve all (\(bulk.count))…", "Hepsini onayla (\(bulk.count))…")) { confirmAll = true }.buttonStyle(.borderedProminent)
                            Button(picked.count == bulk.count ? T("Clear selection", "Seçimi temizle") : T("Select all", "Tümünü seç")) {
                                picked = picked.count == bulk.count ? [] : Set(bulk.map { str($0, "path") })
                            }.buttonStyle(.link)
                        }
                    }
                    List {
                        ForEach(drafts.map { str($0, "path") }, id: \.self) { path in
                            let f = drafts.first { str($0, "path") == path } ?? [:]
                            HStack(alignment: .top, spacing: 10) {
                                if f["approvable"] as? Bool == true {
                                    Button { if picked.contains(path) { picked.remove(path) } else { picked.insert(path) } } label: {
                                        Image(systemName: picked.contains(path) ? "checkmark.square.fill" : "square").font(.title3).foregroundStyle(picked.contains(path) ? Color.accentColor : .secondary)
                                    }.buttonStyle(.plain).help(T("Select", "Seç"))
                                } else {
                                    Image(systemName: "checklist").font(.title3).foregroundStyle(.orange).help(T("Decide item by item", "Madde madde karar verin"))
                                }
                                Button { model.openNote(path) } label: {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(str(f, "title", str(f, "name"))).fontWeight(.medium).lineLimit(2)
                                        HStack { Text(folderLabel(str(f, "folder", "(root)"))); Spacer(); Text(ago(num(f, "modified"))) }.font(.caption).foregroundStyle(.secondary)
                                    }.contentShape(Rectangle())
                                }.buttonStyle(.plain)
                            }.padding(.vertical, 3)
                            .listRowBackground(str(model.note, "path", "") == path ? Color.accentColor.opacity(0.15) : Color.clear)
                        }
                    }.listStyle(.inset)
                    if !picked.isEmpty {
                        HStack {
                            Text(T("\(picked.count) selected", "\(picked.count) seçili")).fontWeight(.medium)
                            Spacer()
                            Button(T("Approve selected", "Seçilenleri onayla")) { model.approveMany(Array(picked)) { picked = [] } }.buttonStyle(.borderedProminent)
                        }.padding(10).background(Color.accentColor.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }.padding(18).frame(minWidth: 300, idealWidth: 360, maxWidth: 460)
            NoteView(model: model).frame(minWidth: 380, idealWidth: 480, maxWidth: .infinity)
        }
        .confirmationDialog(T("Approve \(bulk.count) drafts?", "\(bulk.count) taslak onaylansın mı?"), isPresented: $confirmAll) {
            Button(T("Approve all", "Hepsini onayla")) { model.approveMany(bulk.map { str($0, "path") }) }
        } message: {
            Text(T("Their draft mark is removed; the text does not change. Approve only what you trust without reading.",
                   "Taslak işaretleri kaldırılır; metin değişmez. Yalnızca okumadan güvendiğiniz taslakları onaylayın."))
        }
        .onAppear(perform: openFirst)
        .onChange(of: all.count) { openFirst() }  // the list may arrive after the page opens
    }
    func openFirst() {
        let paths = Set(all.map { str($0, "path") })
        if !paths.contains(str(model.note, "path", "")), let first = all.first { model.openNote(str(first, "path")) }
    }
}

/// A chat digest, item by item: accept, fix or skip. Items already recorded, done or dropped
/// need no decision and stay folded. Accepted items go to the log of the day they were said.
struct DigestItemsView: View {
    @ObservedObject var model: Model
    let path: String
    @State private var items: [[String: Any]] = []
    @State private var editing = ""
    @State private var fixed = ""
    @State private var showFolded = false
    var open: [[String: Any]] { items.filter { str($0, "decision", "") == "" } }
    var decided: [[String: Any]] { items.filter { ["accepted", "fixed", "skipped"].contains(str($0, "decision", "")) } }
    var folded: [[String: Any]] { items.filter { str($0, "decision", "") == "folded" } }
    func sectionName(_ key: String) -> String {
        switch key {
        case "conflict": return T("Conflicts with a note", "Bir notla çelişiyor")
        case "review": return T("May have been withdrawn later", "Sonradan geri alınmış olabilir")
        case "resolved": return T("Apparently done", "Yapılmış görünüyor")
        case "dropped": return T("Apparently dropped", "Vazgeçilmiş görünüyor")
        case "known": return T("Already recorded", "Zaten kayıtlı")
        default: return T("New", "Yeni")
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(open.isEmpty ? T("Every item is decided.", "Tüm maddeler karara bağlandı.")
                               : T("\(open.count) item(s) from this chat wait for you. Accept what is right: it is saved to that day's log.",
                                   "Bu sohbetten \(open.count) madde sizi bekliyor. Doğru olanı kabul edin: o günün log'una kaydedilir."),
                  systemImage: "checklist").foregroundStyle(.orange).fontWeight(.medium)
            ForEach(open.map { str($0, "id") }, id: \.self) { id in
                card(open.first { str($0, "id") == id } ?? [:])
            }
            if !decided.isEmpty {
                Text(T("\(decided.count) decided", "\(decided.count) madde karara bağlandı")).font(.caption).foregroundStyle(.secondary)
            }
            if !folded.isEmpty {
                DisclosureGroup(isExpanded: $showFolded) {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(folded.map { str($0, "id") }, id: \.self) { id in
                            Text(foldedLine(folded.first { str($0, "id") == id } ?? [:])).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }.padding(.top, 4)
                } label: {
                    Text(T("\(folded.count) need no decision (already recorded, done or dropped)", "\(folded.count) madde karar gerektirmiyor (zaten kayıtlı, yapılmış ya da vazgeçilmiş)")).font(.callout)
                }
            }
        }
        .padding(12).background(Color.orange.opacity(0.05)).clipShape(RoundedRectangle(cornerRadius: 10))
        .onAppear(perform: load)
    }
    @ViewBuilder func header(_ it: [String: Any]) -> some View {
        HStack(spacing: 6) {
            Badge(text: str(it, "tag"), color: str(it, "section") == "conflict" ? .red : .accentColor)
            if str(it, "section") != "new" { Text(sectionName(str(it, "section"))).font(.caption).foregroundStyle(.secondary) }
            if str(it, "match", "") != "" && str(it, "section") != "conflict" && str(it, "match_text", "") == "" {
                Text("↔ " + str(it, "match").trimmingCharacters(in: CharacterSet(charactersIn: "[]"))).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    func quoteLine(_ it: [String: Any]) -> String {
        let at: String = str(it, "exchange", "") != "" ? T("  · exchange ", "  · mesaj ") + str(it, "exchange") : ""
        return "“" + str(it, "quote") + "”" + at
    }
    @ViewBuilder func buttons(_ it: [String: Any]) -> some View {
        let id = str(it, "id")
        FlowLayout(spacing: 8) {
            if editing == id {
                Button(T("Save and accept", "Kaydet ve kabul et")) { decide(id, "fix", fixed) }.buttonStyle(.borderedProminent)
                    .disabled(fixed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button(T("Cancel", "Vazgeç")) { editing = "" }
            } else {
                Button(T("Accept", "Kabul et")) { decide(id, "accept") }.buttonStyle(.borderedProminent)
                Button(T("Fix…", "Düzelt…")) { editing = id; fixed = str(it, "statement") }
                Button(T("Skip", "Atla")) { decide(id, "skip") }
            }
        }
    }
    /// Both sides of a conflict: what the chat said and what the note says, with a way to open the note.
    /// A related note that was checked and is not a conflict gets the same view, in a calm colour.
    @ViewBuilder func noteSide(_ it: [String: Any]) -> some View {
        let name = str(it, "match").trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        let conflict = str(it, "section") == "conflict"
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(conflict ? T("Your note says", "Notunuzda yazan") : T("Related note (checked: not a conflict)", "İlgili not (kontrol edildi: çelişki değil)"))
                    .font(.caption.weight(.semibold)).foregroundStyle(conflict ? .red : .secondary)
                Text(name).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                if str(it, "match_path", "") != "" {
                    Button(T("Open note", "Notu aç")) { model.openNote(str(it, "match_path")) }.buttonStyle(.link).font(.caption)
                }
            }
            Text(str(it, "match_text", "") != "" ? str(it, "match_text") : T("(Carry could not find the line; open the note.)", "(Carry satırı bulamadı; notu açın.)"))
                .font(.callout).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            if str(it, "why", "") != "" {
                Text((conflict ? T("Why it conflicts: ", "Neden çelişiyor: ") : T("Why: ", "Neden: ")) + str(it, "why"))
                    .font(.caption).fixedSize(horizontal: false, vertical: true)
            }
            if conflict {
                Text(T("Accepting saves the chat's version to the log and leaves the note as it is; you decide which one is right.",
                       "Kabul ederseniz sohbetteki hâli log'a yazılır, not olduğu gibi kalır; hangisinin doğru olduğuna siz karar verin."))
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }.padding(8).background((conflict ? Color.red : Color.secondary).opacity(0.05)).clipShape(RoundedRectangle(cornerRadius: 6))
    }
    @ViewBuilder func card(_ it: [String: Any]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            header(it)
            if str(it, "section") == "conflict" && editing != str(it, "id") {
                Text(T("This chat says", "Bu sohbette geçen")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            }
            if editing == str(it, "id") {
                TextField(T("Corrected text", "Düzeltilmiş metin"), text: $fixed, axis: .vertical).textFieldStyle(.roundedBorder)
            } else {
                Text(str(it, "statement")).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            }
            if str(it, "quote", "") != "" {
                Text(quoteLine(it)).font(.caption).italic().foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if str(it, "section") == "conflict" || (str(it, "match", "") != "" && str(it, "match_text", "") != "") { noteSide(it) }
            buttons(it)
        }.padding(10).background(Color.orange.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 8))
    }
    func foldedLine(_ it: [String: Any]) -> String { "• " + sectionName(str(it, "section")) + ": " + str(it, "statement") }
    func load() {
        model.run("digest_items", ["path": path, "source_id": model.noteSource], quiet: true) { v in items = v["items"] as? [[String: Any]] ?? [] }
    }
    func decide(_ id: String, _ decision: String, _ text: String? = nil) {
        var fields: [String: Any] = ["path": path, "source_id": model.noteSource, "item": id, "decision": decision]
        if let text { fields["text"] = text }
        let next = model.nextDraft(after: path)
        model.run("digest_decide", fields) { v in
            editing = ""
            let logged = str(v, "logged", "")
            if v["done"] as? Bool == true {
                model.loadVault()
                model.notice = T("Every item is decided; the digest left the review list.", "Tüm maddeler karara bağlandı; taslak inceleme listesinden çıktı.")
                if let next { model.openNote(next) } else { model.reloadNote() }
            } else {
                load(); model.reloadNote()
                if !logged.isEmpty { model.notice = T("Saved to \(logged).", "\(logged) dosyasına kaydedildi.") }
            }
        }
    }
}

/// A large, clearly clickable card for the few things people come to Carry to do.
struct ActionCard: View {
    let icon: String; let title: String; let detail: String
    var highlight = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: icon).font(.system(size: 26)).foregroundStyle(highlight ? Color.white : Color.accentColor).frame(width: 34)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.title3.weight(.semibold)).foregroundStyle(highlight ? Color.white : Color.primary)
                    Text(detail).font(.callout).foregroundStyle(highlight ? Color.white.opacity(0.9) : Color.secondary).fixedSize(horizontal: false, vertical: true).multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").foregroundStyle(highlight ? Color.white.opacity(0.8) : Color.secondary)
            }
            .padding(16).frame(maxWidth: .infinity, minHeight: 88, alignment: .topLeading)
            .background(highlight ? Color.accentColor : Color.secondary.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 12))
            .contentShape(Rectangle())
        }.buttonStyle(.plain)
    }
}

/// One sentence when everything works; the full checklist only when something needs doing.
struct StatusSummary: View {
    @ObservedObject var model: Model
    @State private var details = false
    var index: [String: Any] { model.snapshot["index"] as? [String: Any] ?? [:] }
    var ready: Bool { str(index, "state") == "fresh" && model.anyAssistantConnected && !model.localSources.isEmpty }
    var body: some View {
        if ready && !details {
            HStack(spacing: 10) {
                Image(systemName: "checkmark.seal.fill").foregroundStyle(.green).font(.title3)
                Text(model.ownNotes == 0 ? T("Setup is complete. Start by writing your first note.", "Kurulum tamamlandı. İlk notunuzu yazarak başlayın.")
                     : T("All set. Your assistant can search your \(str(index, "indexed_files", "0")) notes.", "Her şey hazır. Asistanınız \(str(index, "indexed_files", "0")) notunuzda arama yapabiliyor."))
                Spacer()
                Button(T("Details", "Ayrıntılar")) { details = true }.buttonStyle(.link)
            }.padding(12).background(Color.green.opacity(0.07)).clipShape(RoundedRectangle(cornerRadius: 10))
        } else {
            VStack(alignment: .trailing, spacing: 4) {
                SetupChecklist(model: model)
                if ready { Button(T("Hide", "Gizle")) { details = false }.buttonStyle(.link).font(.caption) }
            }
        }
    }
}

struct Overview: View {
    @ObservedObject var model: Model
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                if model.snapshot.isEmpty {
                    PageIntro(icon: "tray.full", title: T("Welcome to Carry", "Carry'ye hoş geldiniz"),
                              text: T("Carry turns your notes into a memory for your AI assistant.", "Carry, notlarınızı yapay zekâ asistanınızın hafızasına dönüştürür."))
                    Button(T("Start setup", "Kuruluma başla")) { model.startOnboarding() }.buttonStyle(.borderedProminent).controlSize(.large)
                } else {
                    Text(pageName("Overview")).font(.largeTitle.bold())
                    StatusSummary(model: model)
                    let toReview = reviewable(model.files).count
                    HStack(spacing: 16) {
                        ActionCard(icon: "checkmark.circle", title: T("Review", "İncele"),
                                   detail: toReview > 0 ? T("\(toReview) draft(s) wait for you", "\(toReview) taslak sizi bekliyor") : T("Nothing waits for you", "Bekleyen bir şey yok"),
                                   highlight: toReview > 0) { model.page = "Review" }
                        ActionCard(icon: "magnifyingglass", title: T("Search notes", "Notlarda ara"),
                                   detail: T("Ask a question, see what Carry finds", "Bir soru sorun, Carry'nin ne bulduğunu görün")) { model.page = "Search" }
                        ActionCard(icon: "square.and.pencil", title: T("New note", "Yeni not"),
                                   detail: T("Write something your assistant should know", "Asistanınızın bilmesini istediğiniz bir şey yazın")) { model.showNewNote = true }
                    }
                    HStack(alignment: .top, spacing: 18) {
                        RecentChats(model: model).frame(maxWidth: .infinity)
                        GroupBox(T("Recently changed", "Son değişenler")) {
                            VStack(alignment: .leading, spacing: 6) {
                                let recent = Array((model.vault["recent"] as? [[String: Any]] ?? []).prefix(6))
                                ForEach(recent.indices, id: \.self) { i in
                                    let f = recent[i]
                                    Button { model.openNote(str(f, "path")) } label: {
                                        HStack {
                                            VStack(alignment: .leading, spacing: 1) {
                                                Text(str(f, "title", str(f, "name"))).lineLimit(2)
                                                Text(folderLabel(str(f, "folder", "(root)"))).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                            }
                                            Spacer()
                                            Text(ago(num(f, "modified"))).font(.caption).foregroundStyle(.secondary)
                                        }.contentShape(Rectangle())
                                    }.buttonStyle(.plain)
                                }
                                if recent.isEmpty { Text(T("No notes yet.", "Henüz not yok.")).foregroundStyle(.secondary) }
                                else { Button(T("All notes", "Tüm notlar")) { model.showVault("recent") }.buttonStyle(.link).font(.callout).padding(.top, 4) }
                            }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
                        }.frame(width: 360)
                    }
                }
            }.padding(30).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// Folder names people did not choose themselves get a word of explanation.
func folderLabel(_ name: String) -> String {
    switch name {
    case "+": return T("+ (Inbox)", "+ (Gelen kutusu)")
    case "(root)": return T("Main folder", "Ana klasör")
    case "workbench": return T("workbench (background work)", "workbench (arka plan işleri)")
    default: return name
    }
}

struct RecentChats: View {
    @ObservedObject var model: Model
    var body: some View {
        let chats = model.vault["chats"] as? [String: Any] ?? [:]
        let opens = chats["open"] as? [[String: Any]] ?? []
        let decisions = chats["decisions"] as? [[String: Any]] ?? []
        let commits = chats["commits"] as? [[String: Any]] ?? []
        VStack(alignment: .leading, spacing: 18) {
            GroupBox(T("Unfinished work from your chats", "Sohbetlerinizden yarım kalan işler")) {
                VStack(alignment: .leading, spacing: 10) {
                    if opens.isEmpty { Text(T("Carry has not found any to-dos in your chats yet.", "Carry henüz sohbetlerinizden yapılacak iş çıkarmadı.")).foregroundStyle(.secondary) }
                    ForEach(opens.indices, id: \.self) { i in item(opens[i], icon: "circle", color: .orange) }
                }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
            }
            if !decisions.isEmpty {
                GroupBox(T("Recent decisions from your chats", "Sohbetlerinizden son kararlar")) {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(decisions.indices, id: \.self) { i in item(decisions[i], icon: "checkmark.seal", color: .green) }
                    }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }
    func item(_ entry: [String: Any], icon: String, color: Color) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: icon).foregroundStyle(color).font(.caption)
            VStack(alignment: .leading, spacing: 3) {
                Text(str(entry, "text")).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Text(str(entry, "day")).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    if let digest = entry["digest"] as? String { Button(T("Where it came from", "Nereden geldi?")) { model.openNote(digest) }.buttonStyle(.link).font(.caption) }
                }
            }
        }
    }
}

struct HarvestSummary: View {
    @ObservedObject var model: Model
    var body: some View {
        let schedule = model.vault["schedule"] as? [String: Any] ?? [:]
        let running = model.harvest["running"] as? Bool == true
        GroupBox(T("Chat notes", "Sohbet notları")) {
            VStack(alignment: .leading, spacing: 10) {
                if schedule["installed"] as? Bool == true && schedule["this_workspace"] as? Bool == false {
                    Text(T("The evening run is set up for another Carry setup on this Mac.", "Akşam otomatik not çıkarma, bu Mac'teki başka bir Carry kurulumu için açık.")).foregroundStyle(.secondary)
                } else if schedule["installed"] as? Bool == true {
                    Text(T("Every evening at \(String(format: "%02d:%02d", Int(num(schedule, "hour")), Int(num(schedule, "minute")))) · drafts in \(languageName(str(model.vault, "draft_language", "English")))", "Her akşam \(String(format: "%02d:%02d", Int(num(schedule, "hour")), Int(num(schedule, "minute")))) · taslak dili: \(languageName(str(model.vault, "draft_language", "English")))")
                         + ((model.assistants["chat_end"] as? Bool == true) ? T(". Also when a chat ends.", ". Sohbet bitince de.") : "."))
                    if schedule["loaded"] as? Bool == false { Text(L("launchd has not loaded this job. Save the schedule again on the Harvest tab.")).foregroundStyle(.orange).font(.callout) }
                } else if model.assistants["chat_end"] as? Bool == true { Text(T("Notes are taken when a chat ends. The nightly run is off.", "Sohbet bitince not çıkarılır. Gecelik çalışma kapalı.")) }
                else { Text(T("Off. Turn on the nightly run in settings so your chats become notes.", "Kapalı. Sohbetlerinizin nota dönüşmesi için ayarlardan gecelik çalışmayı açın.")) }
                if !running, model.harvest["drafts"] != nil {
                    let drafts = (model.harvest["drafts"] as? [String] ?? []).count
                    FlowLayout {
                        Text(drafts > 0 ? T("Last run: \(drafts) new draft(s).", "Son çalıştırma: \(drafts) yeni taslak.") : T("Last run: no new drafts.", "Son çalıştırma: yeni taslak yok.")).font(.callout).foregroundStyle(.secondary)
                        if drafts > 0 { Button(T("Review", "İncele")) { model.page = "Review" }.buttonStyle(.link) }
                    }
                }
                FlowLayout {
                    if running { ProgressView().controlSize(.small); Text(L("Harvest running…")).font(.callout) }
                    else { Button(L("Run harvest now")) { model.startHarvest() } }
                    Button(T("Settings", "Ayarlar")) { model.page = "Settings"; model.settingsTab = "Harvest" }
                }
            }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - Vault

struct VaultView: View {
    @ObservedObject var model: Model
    @State private var query = ""
    @State private var sort = "modified"
    @State private var selecting = false
    @State private var selected: Set<String> = []
    @State private var draftFolder = ""
    @State private var confirmAll = false
    var folders: [String] { (model.vault["folders"] as? [[String: Any]] ?? []).map { str($0, "name") }.filter { $0 != "(root)" } }
    var filtered: [[String: Any]] {
        let week = Date().timeIntervalSince1970 - 7 * 86400
        let q = query.lowercased().trimmingCharacters(in: .whitespaces)
        var list = model.files.filter { f in
            switch model.vaultFilter {
            case "mine": return f["system"] as? Bool != true
            case "system": return f["system"] as? Bool == true
            case "root": return str(f, "folder", "").isEmpty && f["system"] as? Bool != true
            case "recent": return num(f, "modified") >= week && f["system"] as? Bool != true
            case "inbox": return str(f, "path").hasPrefix("+/")
            case "drafts": return f["draft"] as? Bool == true && f["system"] as? Bool != true && (draftFolder.isEmpty || topFolder(f) == draftFolder)
            case "excluded": return f["indexed"] as? Bool == false
            case let x where x.hasPrefix("folder:"): return str(f, "path").hasPrefix(String(x.dropFirst(7)) + "/")
            default: return true
            }
        }
        if !q.isEmpty { list = list.filter { (str($0, "path") + " " + str($0, "title", "") + " " + str($0, "summary", "") + " " + str($0, "type", "")).lowercased().contains(q) } }
        return sort == "name" ? list.sorted { str($0, "title", str($0, "name")).localizedStandardCompare(str($1, "title", str($1, "name"))) == .orderedAscending } : list.sorted { num($0, "modified") > num($1, "modified") }
    }
    func topFolder(_ f: [String: Any]) -> String { str(f, "path").contains("/") ? String(str(f, "path").split(separator: "/")[0]) : "" }
    var approvable: [String] { filtered.filter { $0["approvable"] as? Bool == true }.map { str($0, "path") } }
    /// Drafts folder by folder, so a whole group can be approved at once.
    var draftFolders: [(String, Int)] {
        var counts: [String: Int] = [:]
        for f in model.files where f["draft"] as? Bool == true && f["system"] as? Bool != true { counts[topFolder(f), default: 0] += 1 }
        return counts.sorted { $0.value > $1.value }.map { ($0.key, $0.value) }
    }
    @ViewBuilder var bulkBar: some View {
        if ["drafts", "inbox"].contains(model.vaultFilter) {
            VStack(alignment: .leading, spacing: 8) {
                if model.vaultFilter == "drafts" && draftFolders.count > 1 {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            chip(T("All", "Hepsi"), draftFolders.reduce(0) { $0 + $1.1 }, "")
                            ForEach(draftFolders, id: \.0) { chip(folderLabel($0.0.isEmpty ? "(root)" : $0.0), $0.1, $0.0) }
                        }
                    }
                }
                if selecting {
                    HStack {
                        Text(T("\(selected.count) selected", "\(selected.count) seçili")).font(.callout)
                        Spacer()
                        Button(T("Select all", "Tümünü seç")) { selected = Set(approvable) }.buttonStyle(.link)
                        Button(T("Approve selected", "Seçilenleri onayla")) { model.approveMany(Array(selected)) { selected = []; selecting = false } }.buttonStyle(.borderedProminent).disabled(selected.isEmpty)
                        Button(T("Cancel", "Vazgeç")) { selected = []; selecting = false }
                    }
                } else if !approvable.isEmpty {
                    HStack {
                        Button(T("Select…", "Seç…")) { selecting = true }
                        Button(T("Approve all \(approvable.count) shown…", "Görünen \(approvable.count) taslağın hepsini onayla…")) { confirmAll = true }
                    }
                    Text(T("Tip: open a draft and use “Approve and next” (⌘↩) to read them in a row.", "İpucu: bir taslağı açıp “Onayla, sonrakine geç” (⌘↩) ile sırayla okuyabilirsiniz.")).font(.caption).foregroundStyle(.secondary)
                }
            }
            .onChange(of: model.vaultFilter) { selecting = false; selected = []; draftFolder = "" }
            .confirmationDialog(T("Approve \(approvable.count) drafts?", "\(approvable.count) taslak onaylansın mı?"), isPresented: $confirmAll) {
                Button(T("Approve all", "Hepsini onayla")) { model.approveMany(approvable) }
            } message: {
                Text(T("Their draft mark is removed; the text does not change. Locked notes and raw records (chat logs, clips) are left as they are. Approve only what you trust without reading.",
                       "Taslak işaretleri kaldırılır; metin değişmez. Kilitli notlar ve ham kayıtlar (sohbet kaydı, kırpıntı) olduğu gibi kalır. Yalnızca okumadan güvendiğiniz taslakları onaylayın."))
            }
        }
    }
    func chip(_ title: String, _ count: Int, _ folder: String) -> some View {
        Button("\(title) \(count)") { draftFolder = folder; selected = [] }
            .buttonStyle(.bordered).controlSize(.small).tint(draftFolder == folder ? .accentColor : .secondary)
    }
    func row(_ f: [String: Any], selected isOpen: Bool) -> some View {
        let path = str(f, "path")
        let canPick = selecting && approvable.contains(path)
        return Button { if selecting { if canPick { if selected.contains(path) { selected.remove(path) } else { selected.insert(path) } } } else { model.openNote(path) } } label: {
            HStack(alignment: .top, spacing: 8) {
            if selecting {
                Image(systemName: selected.contains(path) ? "checkmark.square.fill" : "square").foregroundStyle(canPick ? Color.accentColor : .secondary.opacity(0.4))
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    if f["draft"] as? Bool == true { Circle().fill(Color.orange).frame(width: 6, height: 6).help(L("Draft")) }
                    Text(str(f, "title", str(f, "name"))).fontWeight(.medium).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    if str(f, "index_state") == "excluded" { Image(systemName: "eye.slash").font(.caption2).foregroundStyle(.secondary).help(L("Not indexed: excluded from search")) }
                    if str(f, "index_state") == "pending" { Image(systemName: "clock.arrow.circlepath").font(.caption2).foregroundStyle(.orange).help(L("Waiting to be indexed")) }
                    if f["locked"] as? Bool == true { Image(systemName: "lock").font(.caption2).foregroundStyle(.secondary) }
                }
                HStack {
                    Text(folderLabel(str(f, "folder", "(root)"))).lineLimit(1)
                    Spacer()
                    Text(ago(num(f, "modified")))
                }.font(.caption).foregroundStyle(.secondary)
            }
            }.padding(.vertical, 3).contentShape(Rectangle())
        }.buttonStyle(.plain).listRowBackground(isOpen && !selecting ? Color.accentColor.opacity(0.18) : Color.clear)
    }
    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 10) {
                if model.localSources.count > 1 {
                    Picker(T("Folder", "Klasör"), selection: Binding(get: { model.vaultSource }, set: { model.switchVault($0) })) {
                        ForEach(model.localSources.indices, id: \.self) { i in Text(URL(fileURLWithPath: str(model.localSources[i], "root")).lastPathComponent).tag(str(model.localSources[i], "source_id")) }
                    }
                }
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text(T("All notes Carry can read. Click one to read it on the right.", "Carry'nin okuyabildiği tüm notlar. Birine tıklayın, sağda okuyun.")).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    InfoButton(text: T("Orange dot: draft, not approved yet.\nCrossed eye: left out of search; Carry does not include it in search results.\nCircular arrows: changed; search picks it up in a moment.\nLock: marked as locked; assistants must not edit it.\nThis box finds notes by name; to search inside notes use “Search notes”.",
                                       "Turuncu nokta: taslak, henüz onaylanmadı.\nÇizili göz: aramaya dahil değil; Carry arama sonuçlarına eklemez.\nDönen oklar: değişti; arama birazdan günceller.\nKilit: kilitli işaretli; asistanlar düzenlememeli.\nBu kutu notları adıyla bulur; notların içinde aramak için “Notlarda ara”yı kullanın."))
                }
                HStack {
                    TextField(T("Find by name, folder or summary", "Ada, klasöre ya da özete göre bul"), text: $query).textFieldStyle(.roundedBorder)
                    Button { model.showNewNote = true } label: { Label(T("New note", "Yeni not"), systemImage: "square.and.pencil") }
                }
                VStack(alignment: .leading, spacing: 8) {
                    Picker(L("Show"), selection: $model.vaultFilter) {
                        Text(T("My notes", "Kendi notlarım")).tag("mine"); Text(T("Changed this week", "Bu hafta değişenler")).tag("recent"); Text(T("Inbox", "Gelen kutusu")).tag("inbox")
                        Text(T("Drafts", "Taslaklar")).tag("drafts"); Text(T("Left out of search", "Aramaya dahil olmayanlar")).tag("excluded")
                        Text(T("Setup files and templates", "Kurulum dosyaları ve şablonlar")).tag("system"); Text(T("Everything", "Hepsi")).tag("all")
                        Divider()
                        Text(T("Main folder", "Ana klasör")).tag("root")
                        ForEach(folders, id: \.self) { Text(T("Folder: ", "Klasör: ") + folderLabel($0)).tag("folder:" + $0) }
                    }.labelsHidden().fixedSize().frame(maxWidth: .infinity, alignment: .leading)
                    Picker(L("Sort"), selection: $sort) { Text(T("Newest", "Son değişen")).tag("modified"); Text("A–Z").tag("name") }.pickerStyle(.segmented).labelsHidden().frame(width: 150)
                }
                if model.vaultFilter == "drafts" || model.vaultFilter == "inbox" {
                    Button { model.page = "Review" } label: {
                        Label(T("To approve drafts, open Review", "Taslakları onaylamak için İncele'yi açın"), systemImage: "checkmark.circle")
                    }.buttonStyle(.link)
                }
                let rows = filtered
                let shown = Array(rows.prefix(3000))
                Text(rows.count > shown.count ? L("Showing %d of %d notes. Narrow the filter to see the rest.", shown.count, rows.count) : L("%d notes", rows.count)).font(.caption).foregroundStyle(.secondary)
                if model.vault["truncated"] as? Bool == true { Text(L("This vault has more files than Carry lists (20,000).")).font(.caption).foregroundStyle(.orange) }
                if rows.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(model.vaultFilter == "inbox" ? T("The Inbox is empty. New chat drafts will show up here.", "Gelen kutusu boş. Yeni sohbet taslakları burada görünecek.")
                             : T("No notes match this filter.", "Bu filtreye uyan not yok.")).foregroundStyle(.secondary)
                        if model.vaultFilter != "mine" || !query.isEmpty { Button(T("Clear filter", "Filtreyi temizle")) { model.vaultFilter = "mine"; query = "" }.buttonStyle(.link) }
                        if model.ownNotes == 0 { Button(T("Write my first note", "İlk notumu yaz")) { model.showNewNote = true } }
                    }.padding(.top, 10)
                    Spacer()
                }
                ScrollViewReader { proxy in
                    List {
                        ForEach(shown.map { str($0, "path") }, id: \.self) { path in
                            let f = shown.first { str($0, "path") == path } ?? [:]
                            row(f, selected: str(model.note, "path", "") == path).id(path)
                        }
                    }.listStyle(.inset)
                    .onChange(of: str(model.note, "path", "")) { if !str(model.note, "path", "").isEmpty { withAnimation { proxy.scrollTo(str(model.note, "path", "")) } } }
                }
            }.padding(16).frame(minWidth: 270, idealWidth: 320, maxWidth: 440)
            NoteView(model: model).frame(minWidth: 380, idealWidth: 480, maxWidth: .infinity)
        }
    }
}

struct NoteView: View {
    @ObservedObject var model: Model
    @State private var confirmTrash = false
    var body: some View {
        let note = model.note
        if note.isEmpty {
            VStack(spacing: 10) {
                Image(systemName: "doc.text.magnifyingglass").font(.system(size: 40)).foregroundStyle(.secondary)
                Text(T("Pick a note on the left to read it here.", "Okumak için soldan bir not seçin.")).foregroundStyle(.secondary)
                Text(T("Blue links open the linked note. To change a note, use “Open in editor” or Obsidian; Carry notices the change by itself.",
                       "Mavi bağlantılar ilgili notu açar. Bir notu değiştirmek için “Editörde aç”ı ya da Obsidian'ı kullanın; Carry değişikliği kendisi fark eder.")).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 360)
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            let front = note["frontmatter"] as? [String: Any] ?? [:]
            let absolute = str(note, "absolute", "")
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 4) {
                        Button { model.goBack() } label: { Image(systemName: "chevron.left") }.disabled(!model.canGoBack).help(L("Back")).keyboardShortcut("[", modifiers: .command)
                        Button { model.goForward() } label: { Image(systemName: "chevron.right") }.disabled(!model.canGoForward).help(L("Forward")).keyboardShortcut("]", modifiers: .command)
                        Spacer()
                        Button { model.closeNote() } label: { Image(systemName: "xmark") }.help(L("Close note")).keyboardShortcut(.cancelAction)
                    }.buttonStyle(.borderless)
                    Text(str(note, "title")).font(.title.bold()).textSelection(.enabled)
                    Text(str(note, "path")).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                    FlowLayout(spacing: 6) {
                        if let t = front["type"] as? String { Badge(text: metaValue(t), color: .accentColor) }
                        if let s = front["status"] as? String { Badge(text: metaValue(s)) }
                        if front["draft"] as? Bool == true { Badge(text: L("draft"), color: .orange) }
                        if front["lock"] as? Bool == true { Badge(text: L("locked"), color: .red) }
                        if let s = front["sensitivity"] as? String { Badge(text: T("Privacy label: ", "Gizlilik etiketi: ") + metaValue(s), color: .purple) }
                        switch str(note, "index_state") {
                        case "excluded": Button { model.page = "Settings"; model.settingsTab = "Sources" } label: { Badge(text: L("not indexed")) }.buttonStyle(.plain).help(T("This file or folder is left out of search. Click to change what is searched.", "Bu dosya ya da klasör aramanın dışında bırakılmış. Aranacakları değiştirmek için tıklayın."))
                        case "pending": Badge(text: L("waiting to be indexed"), color: .orange)
                        default: Badge(text: L("indexed"), color: .green)
                        }
                        Text(L("modified ") + ago(num(note, "modified"))).font(.caption).foregroundStyle(.secondary)
                    }
                    if let summary = front["summary"] as? String { Text(summary).italic().foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).textSelection(.enabled) }
                    let listed = model.files.first { str($0, "path") == str(note, "path") } ?? [:]
                    let inQueue = listed["review"] as? Bool == true
                    if inQueue && listed["digest"] as? Bool == true {
                        DigestItemsView(model: model, path: str(note, "path")).id(str(note, "path"))
                    } else if front["draft"] as? Bool == true && front["lock"] as? Bool != true {
                        VStack(alignment: .leading, spacing: 10) {
                            Label(inQueue ? T("Draft: read it, and approve it if it is right.", "Taslak: okuyun, doğruysa onaylayın.")
                                          : T("Written by an assistant, not checked by you. It is already searchable; approve it once you have checked it.", "Bir asistan yazdı, siz kontrol etmediniz. Zaten aranabilir; kontrol ettiğinizde onaylayın."),
                                  systemImage: "checkmark.seal").foregroundStyle(inQueue ? .orange : .secondary).fontWeight(.medium)
                            FlowLayout(spacing: 8) {
                                Button(T("Approve and next", "Onayla, sonrakine geç")) {
                                    let current = str(note, "path"), next = model.nextDraft(after: current)
                                    model.run("note_approve", ["path": current, "source_id": model.noteSource]) { _ in
                                        model.loadVault()
                                        if let next { model.openNote(next) } else { model.reloadNote() }
                                        model.notice = next == nil ? T("Approved. That was the last draft.", "Onaylandı. Bu son taslaktı.") : T("Approved. Showing the next draft.", "Onaylandı. Sıradaki taslak gösteriliyor.")
                                    }
                                }.buttonStyle(.borderedProminent).keyboardShortcut(.return, modifiers: .command).help("⌘↩")
                                Button(T("Approve", "Onayla")) {
                                    model.run("note_approve", ["path": str(note, "path"), "source_id": model.noteSource]) { _ in
                                        model.reloadNote(); model.loadVault(); model.notice = T("Approved. The note is no longer a draft.", "Onaylandı. Not artık taslak değil.")
                                    }
                                }
                                Button(T("Skip", "Atla")) { if let next = model.nextDraft(after: str(note, "path")) { model.openNote(next) } }
                                Button(T("Move to Trash…", "Çöp kutusuna taşı…"), role: .destructive) { confirmTrash = true }
                            }
                        }.padding(12).background(Color.orange.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    FlowLayout {
                        if obsidianInstalled { Button(L("Open in Obsidian"), systemImage: "arrow.up.forward.app") { if let u = obsidianURL(absolute) { NSWorkspace.shared.open(u) } } }
                        Button(T("Open in default app", "Varsayılan uygulamada aç"), systemImage: "square.and.pencil") { NSWorkspace.shared.open(URL(fileURLWithPath: absolute)) }
                        Button(L("Show in Finder"), systemImage: "folder") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: absolute)]) }
                        Button(L("Copy path"), systemImage: "doc.on.doc") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(absolute, forType: .string) }
                    }.controlSize(.small)
                    if !front.isEmpty {
                        DisclosureGroup(T("Note details (\(front.count))", "Not bilgileri (\(front.count))")) {
                            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 6) {
                                ForEach(front.keys.sorted(), id: \.self) { key in
                                    GridRow {
                                        Text(key).font(.callout).foregroundStyle(.secondary)
                                        Text(inline(value(front[key]))).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }.padding(.top, 6).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    Divider()
                    MarkdownText(bodyWithoutTitle(note))
                    let backlinks = model.backlinks
                    let links = note["links"] as? [String] ?? []
                    if !backlinks.isEmpty || !links.isEmpty { Divider().padding(.top, 10) }
                    if !backlinks.isEmpty {
                        GroupBox(T("Notes that link here (\(backlinks.count))", "Bu nota bağlantı veren notlar (\(backlinks.count))")) {
                            VStack(alignment: .leading, spacing: 4) { ForEach(backlinks, id: \.self) { p in Button(p) { model.openNote(p, source: model.noteSource) }.buttonStyle(.link) } }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    if !links.isEmpty {
                        GroupBox(T("Notes this one links to (\(links.count))", "Bu notun bağlantı verdiği notlar (\(links.count))")) {
                            VStack(alignment: .leading, spacing: 4) { ForEach(links, id: \.self) { l in Button(l) { model.followLink(l) }.buttonStyle(.link) } }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }.padding(28).frame(maxWidth: 860, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
            }.id(str(note, "path"))
            .confirmationDialog(T("Move this draft to the Trash?", "Bu taslak çöp kutusuna taşınsın mı?"), isPresented: $confirmTrash) {
                Button(T("Move to Trash", "Çöp kutusuna taşı"), role: .destructive) {
                    NSWorkspace.shared.recycle([URL(fileURLWithPath: absolute)]) { _, error in
                        DispatchQueue.main.async {
                            if let error { model.error = error.localizedDescription } else { model.closeNote(); model.loadVault(); model.notice = T("Moved to the Trash. You can put it back from the Trash in Finder.", "Çöp kutusuna taşındı. Finder'daki Çöp Sepeti'nden geri alabilirsiniz.") }
                        }
                    }
                }
            } message: { Text(T("You can restore it from the Trash in Finder.", "Finder'daki Çöp Sepeti'nden geri alabilirsiniz.")) }
            .environment(\.openURL, OpenURLAction { url in
                if url.scheme == "carry-note" {
                    model.followLink(url.absoluteString.dropFirst("carry-note:".count).removingPercentEncoding ?? "")
                    return .handled
                }
                return .systemAction
            })
        }
    }
    /// Most notes repeat their title as the first heading; the header already shows it.
    func bodyWithoutTitle(_ note: [String: Any]) -> String {
        let body = str(note, "body", "")
        let lines = body.components(separatedBy: "\n")
        guard let first = lines.firstIndex(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }),
              lines[first].trimmingCharacters(in: .whitespaces) == "# " + str(note, "title") else { return body }
        return lines[(first + 1)...].joined(separator: "\n")
    }
    func value(_ v: Any?) -> String {
        if let list = v as? [Any] { return list.map { String(describing: $0) }.joined(separator: ", ") }
        if let v, !(v is NSNull) { return String(describing: v) }
        return "—"
    }
}

// MARK: - Settings

struct SettingsView: View {
    @ObservedObject var model: Model
    let tabs = ["Search", "Sources", "Harvest", "Clients", "General"]
    var body: some View {
        VStack(spacing: 0) {
            Picker(L("Settings"), selection: $model.settingsTab) { ForEach(tabs, id: \.self) { Text(tabName($0)).tag($0) } }
                .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 560).padding(.top, 18).padding(.bottom, 4)
            ZStack {
                // Search stays alive while hidden so unsaved edits survive a tab switch.
                SearchSettings(model: model).opacity(model.settingsTab == "Search" ? 1 : 0).allowsHitTesting(model.settingsTab == "Search")
                switch model.settingsTab {
                case "Sources": Sources(model: model)
                case "Harvest": HarvestSettings(model: model)
                case "Clients": Connections(model: model)
                case "General": AdvancedSettings(model: model)
                default: EmptyView()
                }
            }
        }.disabled(model.snapshot.isEmpty)
    }
}

struct SearchSettings: View {
    @ObservedObject var model: Model
    @State private var preset = ""
    @State private var topK = 8
    @State private var maxChars = 10000
    @State private var perDocument = 2
    @State private var minScore = 0.5
    @State private var autoRefresh = true
    @State private var refreshSeconds = 60
    @State private var githubSeconds = 300
    var retrieval: [String: Any] { model.settings["retrieval"] as? [String: Any] ?? [:] }
    /// Only the fields the user changed: a hidden field (the Jev threshold under another
    /// ranker) keeps whatever value it has.
    var changes: [String: Any] {
        var out: [String: Any] = [:]
        if Int(num(retrieval, "top_k")) != topK { out["top_k"] = topK }
        if Int(num(retrieval, "max_chars")) != maxChars { out["max_chars"] = maxChars }
        if Int(num(retrieval, "max_per_document")) != perDocument { out["max_per_document"] = perDocument }
        if abs(num(retrieval, "reranker_min_score") - minScore) > 0.001 { out["reranker_min_score"] = minScore }
        if (retrieval["auto_refresh"] as? Bool ?? true) != autoRefresh { out["auto_refresh"] = autoRefresh }
        if Int(num(retrieval, "refresh_seconds")) != refreshSeconds { out["refresh_seconds"] = refreshSeconds }
        if Int(num(retrieval, "github_sync_seconds")) != githubSeconds { out["github_sync_seconds"] = githubSeconds }
        return out
    }
    var dirty: Bool {
        Int(num(retrieval, "top_k")) != topK || Int(num(retrieval, "max_chars")) != maxChars || Int(num(retrieval, "max_per_document")) != perDocument ||
        abs(num(retrieval, "reranker_min_score") - minScore) > 0.001 || (retrieval["auto_refresh"] as? Bool ?? true) != autoRefresh ||
        Int(num(retrieval, "refresh_seconds")) != refreshSeconds || Int(num(retrieval, "github_sync_seconds")) != githubSeconds
    }
    func load() {
        preset = str(model.settings, "preset", "keyword_assistant")
        topK = Int(num(retrieval, "top_k", 8)); maxChars = Int(num(retrieval, "max_chars", 10000)); perDocument = Int(num(retrieval, "max_per_document", 2))
        minScore = num(retrieval, "reranker_min_score", 0.5); autoRefresh = retrieval["auto_refresh"] as? Bool ?? true
        refreshSeconds = Int(num(retrieval, "refresh_seconds", 60)); githubSeconds = Int(num(retrieval, "github_sync_seconds", 300))
    }
    /// The two answers (how to find, who checks) make up the preset.
    var finder: String {
        get { ["semantic_jev", "semantic_assistant", "accurate_multilingual"].contains(preset) ? "semantic" : "keyword" }
        nonmutating set { preset = Self.preset(finder: newValue, checker: checker) }
    }
    var checker: String {
        get { preset.hasSuffix("_jev") ? "jev" : preset == "accurate_multilingual" ? "local" : "assistant" }
        nonmutating set { preset = Self.preset(finder: finder, checker: newValue) }
    }
    static func preset(finder: String, checker: String) -> String {
        switch (finder, checker) {
        case (_, "local"): return "accurate_multilingual"
        case ("semantic", "jev"): return "semantic_jev"
        case ("semantic", _): return "semantic_assistant"
        case (_, "jev"): return "keyword_jev"
        default: return "keyword_assistant"
        }
    }
    func option(isOn: Bool, title: String, uses: [String], short: String, long: String, badge: String?, pick: @escaping () -> Void) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Button(action: pick) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: isOn ? "largecircle.fill.circle" : "circle").foregroundStyle(isOn ? Color.accentColor : .secondary).font(.title3)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) { Text(title).font(.headline); if let badge { Badge(text: badge, color: .accentColor) } }
                        Text(short).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        FlowLayout(spacing: 4) { Text(T("Uses:", "Kullanır:")).font(.caption).foregroundStyle(.secondary); ForEach(uses, id: \.self) { Badge(text: $0) } }
                    }
                    Spacer(minLength: 0)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain)
            InfoButton(text: long).font(.title3).padding(.top, 2)
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageIntro(icon: "magnifyingglass", title: T("Search", "Arama"),
                          text: T("How Carry finds the right notes when your assistant asks. Now: \(presetLabel(str(model.settings, "preset", ""))).",
                                  "Asistanınız sorduğunda Carry'nin doğru notları nasıl bulduğu. Şu an: \(presetLabel(str(model.settings, "preset", "")))."))
                GroupBox(T("How should Carry search?", "Carry nasıl arasın?")) {
                    VStack(alignment: .leading, spacing: 16) {
                        Text(T("1. How should it find notes?", "1. Notları nasıl bulsun?")).font(.headline)
                        option(isOn: finder == "semantic", title: T("By meaning", "Anlamına göre"), uses: ["Ollama"],
                               short: T("Also finds notes written with other words (“meeting” finds “call”).", "Başka kelimelerle yazılmış notları da bulur (“toplantı” diye sorunca “görüşme”yi de)."),
                               long: T("The free Ollama app runs a small language model (embeddinggemma) on this Mac: a 0.6 GB download, about 0.7 GB of memory while searching, unloaded when idle. Your notes never leave the Mac for this step.",
                                       "Ücretsiz Ollama uygulaması bu Mac'te küçük bir dil modeli (embeddinggemma) çalıştırır: 0,6 GB indirme, arama sırasında yaklaşık 0,7 GB bellek, boştayken bellekten çıkar. Bu adımda notlarınız Mac'ten çıkmaz."),
                               badge: T("recommended", "önerilen")) { finder = "semantic" }
                        option(isOn: finder == "keyword", title: T("By words", "Kelimelerine göre"), uses: [T("built in", "yerleşik")],
                               short: T("Finds notes that contain the words of the question. Nothing to install.", "Sorudaki kelimeleri içeren notları bulur. Kurulacak bir şey yok."),
                               long: T("Built into Carry, nothing runs in the background. Misses notes that say the same thing in completely different words.",
                                       "Carry'nin içinde yerleşik; arka planda bir şey çalışmaz. Aynı şeyi tamamen başka kelimelerle anlatan notları kaçırabilir."),
                               badge: nil) { finder = "keyword"; if checker == "local" { checker = "jev" } }
                        Divider()
                        Text(T("2. Who checks the results?", "2. Sonuçları kim kontrol etsin?")).font(.headline)
                        Text(T("The check keeps only passages that really answer the question, so your assistant is not misled.", "Kontrol, yalnızca soruyu gerçekten yanıtlayan parçaları tutar; böylece asistanınız yanılmaz.")).font(.caption).foregroundStyle(.secondary)
                        option(isOn: checker == "jev", title: "TypeSafe Jev", uses: [T("online service", "çevrimiçi hizmet"), T("access key", "erişim anahtarı")],
                               short: T("Most accurate and fastest (about half a second). Needs a TypeSafe key.", "En isabetli ve en hızlı (yaklaşık yarım saniye). TypeSafe anahtarı gerekir."),
                               long: T("A specialised service checks which passages answer the question and drops passages that try to instruct an AI. The question and up to 32 found passages go to TypeSafe in the US (secrets masked, best effort; TypeSafe says it does not train on them). In Carry's 36-question test, meaning + Jev put the right note first 34 times, the best result. Chat drafts are checked there too: masked chat excerpts and note passages go to TypeSafe while they are compared with your notes.",
                                       "Bu işe özel bir hizmet, hangi parçaların soruyu yanıtladığını kontrol eder ve yapay zekâya talimat vermeye çalışan parçaları eler. Soru ve en fazla 32 bulunan parça ABD'deki TypeSafe'e gider (gizli bilgiler elden geldiğince maskelenir; TypeSafe bunlarla model eğitmediğini belirtiyor). Carry'nin 36 soruluk testinde anlam + Jev doğru notu 34 kez ilk sıraya koydu; en iyi sonuç buydu. Sohbet taslakları da orada denetlenir: notlarınızla karşılaştırılırken maskelenmiş sohbet alıntıları ve not parçaları TypeSafe'e gider."),
                               badge: T("recommended", "önerilen")) { checker = "jev" }
                        option(isOn: checker == "assistant", title: T("My own assistant (Claude or GPT)", "Kendi asistanım (Claude ya da GPT)"), uses: ["Claude Code: Claude Haiku", "Codex: GPT"],
                               short: T("No extra service or key: the assistant you already use sorts the results.", "Ek hizmet ya da anahtar yok: zaten kullandığınız asistan sonuçları ayıklar."),
                               long: T("Carry hands the assistant a few more passages and it decides which ones matter. In Claude Code a small Claude model (Haiku) does this in a helper step; in Codex, Codex's own GPT model does it. It runs on your existing subscription, with nothing extra leaving your Mac beyond what already goes to that assistant. A little slower and it uses a few more tokens per question. After a chat, the same assistant compares its drafts with your notes (already recorded or a real conflict).",
                                       "Carry asistana biraz daha fazla parça verir; hangilerinin önemli olduğuna asistan karar verir. Claude Code'da bunu yardımcı bir adımda küçük bir Claude modeli (Haiku) yapar; Codex'te Codex'in kendi GPT modeli yapar. Mevcut aboneliğinizle çalışır; o asistana zaten gidenin ötesinde Mac'inizden ek bir şey çıkmaz. Biraz daha yavaştır ve soru başına biraz daha fazla token harcar. Sohbetten sonra taslakları notlarınızla da aynı asistan karşılaştırır (zaten kayıtlı mı, gerçek bir çelişki mi)."),
                               badge: nil) { checker = "assistant" }
                        option(isOn: checker == "local", title: T("A large model on this Mac", "Bu Mac'te büyük bir model"), uses: [T("2 GB local model", "2 GB yerel model")],
                               short: T("Nothing goes online for the check. Heavy: for Macs with 16 GB memory or more; needs “By meaning”.", "Kontrol için hiçbir şey çevrimiçine gitmez. Ağır: 16 GB ve üzeri belleği olan Mac'ler için; “Anlamına göre” ile çalışır."),
                               long: T("A 2 GB relevance model (BGE reranker) runs on this Mac. It needs about 3 GB of free memory while searching and the first search after a pause is slow. On 8 GB Macs it can freeze the computer, so it is not recommended there.",
                                       "2 GB'lık bir alaka modeli (BGE reranker) bu Mac'te çalışır. Arama sırasında yaklaşık 3 GB boş bellek ister; bir aradan sonraki ilk arama yavaştır. 8 GB'lık Mac'lerde bilgisayarı dondurabildiği için önerilmez."),
                               badge: nil) { checker = "local"; finder = "semantic" }
                        Divider()
                        FlowLayout(spacing: 8) {
                            Text(T("Your choice:", "Seçiminiz:")).fontWeight(.medium)
                            Text(presetLabel(preset))
                            if preset == str(model.settings, "preset", "") { Badge(text: T("in use", "kullanılıyor"), color: .green) }
                        }
                        if preset != str(model.settings, "preset", "") {
                            FlowLayout {
                                Button(T("Use this", "Bunu kullan")) {
                                    model.run("model_setup", ["model": preset]) { model.job = $0; model.notice = $0["started"] as? Bool == false ? busyNotice : L("Switching search mode. Semantic modes download a model and rebuild the index; you can keep using Carry.") }
                                }.buttonStyle(.borderedProminent)
                                Button(T("Cancel", "Vazgeç")) { load() }
                            }
                        }
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                }
                if preset.contains("jev") || str(model.settings, "preset", "").contains("jev") { JevForm(model: model) }
                else { DisclosureGroup(T("TypeSafe access key", "TypeSafe erişim anahtarı")) { JevForm(model: model).padding(.top, 8) } }
                DisclosureGroup(T("Advanced", "Gelişmiş")) {
                    VStack(alignment: .leading, spacing: 16) {
                        GroupBox(L("Results handed to your assistant")) {
                            VStack(alignment: .leading, spacing: 10) {
                                Stepper(L("Passages per search: %d", topK), value: $topK, in: 1...40)
                                Stepper(T("Most text sent to the assistant: \(maxChars) characters", "Asistana gönderilecek en fazla metin: \(maxChars) karakter"), value: $maxChars, in: 1000...60000, step: 1000)
                                Stepper(L("Passages from one note: %d", perDocument), value: $perDocument, in: 1...10)
                                if str(retrieval, "reranker", "") == "jev" {
                                    HStack {
                                        Text(L("Jev relevance threshold"))
                                        Slider(value: $minScore, in: 0...1, step: 0.05).frame(maxWidth: 260)
                                        Text(String(format: "%.2f", locale: Lang.locale, minScore)).monospacedDigit()
                                    }
                                    Text(L("Higher returns fewer, surer passages. 0.50 is the measured default.")).font(.caption).foregroundStyle(.secondary)
                                }
                            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        }
                        GroupBox(L("Keeping the index fresh")) {
                            VStack(alignment: .leading, spacing: 10) {
                                Toggle(L("Re-index automatically when notes change"), isOn: $autoRefresh)
                                Stepper(L("Check for changes every %d s", refreshSeconds), value: $refreshSeconds, in: 10...3600, step: 10).disabled(!autoRefresh)
                                Stepper(L("Sync GitHub sources every %d min", githubSeconds / 60), value: $githubSeconds, in: 60...86400, step: 60)
                            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }.padding(.top, 8)
                }
            }.padding(30).frame(maxWidth: .infinity, alignment: .leading)
        }.safeAreaInset(edge: .bottom) {
            if dirty {
                HStack {
                    Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                    Text(L("Unsaved changes"))
                    Spacer()
                    Button(L("Discard")) { load() }
                    Button(L("Save changes")) { model.run("settings_update", ["retrieval": changes]) { model.settings = $0; model.notice = L("Search settings saved. They apply to the next search.") } }.buttonStyle(.borderedProminent).keyboardShortcut("s", modifiers: .command)
                }.padding(14).background(.bar)
            }
        }.onAppear(perform: load).onChange(of: pretty(model.settings["retrieval"] ?? [:]) + str(model.settings, "preset")) { load() }
    }
}

struct HarvestSettings: View {
    @ObservedObject var model: Model
    @State private var enabled = false
    @State private var time = Calendar.current.date(bySettingHour: 21, minute: 30, second: 0, of: Date())!
    @State private var confirmReplace = false
    var schedule: [String: Any] { model.settings["schedule"] as? [String: Any] ?? [:] }
    var foreign: Bool { schedule["installed"] as? Bool == true && schedule["this_workspace"] as? Bool == false }
    var language: Binding<String> {
        Binding(get: { str(model.settings, "draft_language", "English") },
                set: { value in model.run("harvest_language", ["language": value]) { model.settings = $0; model.loadVault(); model.notice = L("Draft language saved. It applies to every harvest: nightly, manual and at chat end.") } })
    }
    func load() {
        enabled = schedule["installed"] as? Bool == true && !foreign
        if enabled { time = Calendar.current.date(bySettingHour: Int(num(schedule, "hour", 21)), minute: Int(num(schedule, "minute", 30)), second: 0, of: Date()) ?? time }
    }
    func save(replace: Bool = false) {
        let c = Calendar.current.dateComponents([.hour, .minute], from: time)
        model.run("harvest_schedule", ["enabled": enabled, "hour": c.hour ?? 21, "minute": c.minute ?? 30, "replace": replace]) { value in
            var settings = model.settings; settings["schedule"] = value; model.settings = settings
            model.notice = enabled ? L("Nightly harvest scheduled.") : L("Nightly harvest turned off.")
            model.loadVault()
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageIntro(icon: "text.bubble", title: T("Chat notes", "Sohbet notları"),
                          text: T("After you chat with your assistant in your notes folder, Carry picks out what is worth keeping (decisions, facts, preferences, unfinished work) and saves it in your Inbox, marked as a draft. You check them and approve the right ones.",
                                  "Not klasörünüzde asistanınızla sohbet ettikten sonra Carry saklanmaya değer olanları (kararlar, bilgiler, tercihler, yarım kalan işler) seçip Gelen kutunuza taslak işaretiyle kaydeder. Doğruluklarını siz kontrol edip doğru olanları onaylarsınız."))
                GroupBox(L("Drafts")) {
                    VStack(alignment: .leading, spacing: 10) {
                        Picker(L("Language of drafts"), selection: language) { Text("Türkçe").tag("Turkish"); Text("English").tag("English") }.pickerStyle(.segmented).frame(maxWidth: 320)
                        Text(L("Used by every harvest: nightly, manual and at chat end.") + T(" Saved automatically.", " Otomatik kaydedilir.")).font(.caption).foregroundStyle(.secondary)
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox(L("Every evening")) {
                    VStack(alignment: .leading, spacing: 12) {
                        if foreign {
                            Text(L("The nightly harvest on this Mac belongs to another Carry workspace (%@). Saving here replaces it.", str(schedule, "workspace"))).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                        }
                        Toggle(L("Harvest finished chats every evening"), isOn: $enabled)
                        DatePicker(L("Time"), selection: $time, displayedComponents: .hourAndMinute).frame(maxWidth: 220).disabled(!enabled)
                        if let vault = schedule["vault"] as? String, !foreign { Text(L("Vault: ") + vault).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                        if schedule["installed"] as? Bool == true && schedule["loaded"] as? Bool == false && !foreign {
                            Text(L("launchd has not loaded this job. Save the schedule again on the Harvest tab.")).foregroundStyle(.orange).font(.callout)
                        }
                        Button(T("Save evening plan", "Akşam planını kaydet")) { if foreign { confirmReplace = true } else { save() } }.buttonStyle(.borderedProminent)
                        Text(L("Runs through launchd (%@) while you are logged in.", str(schedule, "path", "~/Library/LaunchAgents/local.carry.harvest.plist"))).font(.caption).foregroundStyle(.secondary)
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                }
                .confirmationDialog(L("Replace the other workspace’s nightly harvest?"), isPresented: $confirmReplace) {
                    Button(T("Move the evening run to this setup", "Akşam çalışmasını bu kuruluma taşı"), role: .destructive) { save(replace: true) }
                } message: { Text(L("There is one nightly harvest per Mac user. The other workspace stops harvesting at night; its chat-end hooks keep working.")) }
                GroupBox(L("When a chat ends")) {
                    VStack(alignment: .leading, spacing: 8) {
                        if model.assistants["chat_end"] as? Bool == true {
                            Label(T("On for this notes folder", "Bu not klasörü için açık"), systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                            Text(L("Claude Code and Codex call Carry when a chat in the vault ends, and the next chat starts with a short state pack of open items and decisions. These hooks are set up by `carry setup` or on the Clients tab; Codex asks you to approve each hook once.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        } else {
                            Label(T("Not set up for this notes folder", "Bu not klasörü için kurulu değil"), systemImage: "circle").foregroundStyle(.secondary)
                            Text(T("Notes folders created by Carry have this on from the start. For a folder you already had, turn on the evening run above: it collects the day's chats in one go.",
                                   "Carry'nin oluşturduğu not klasörlerinde bu baştan açıktır. Önceden sahip olduğunuz bir klasör için yukarıdan akşam çalışmasını açın: günün sohbetlerini tek seferde toplar.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox(L("Recent runs")) {
                    VStack(alignment: .leading, spacing: 10) {
                        let log = model.vault["harvest_log"] as? [String] ?? []
                        if log.isEmpty { Text(L("No runs logged yet.")).foregroundStyle(.secondary) }
                        else {
                            DisclosureGroup(T("Technical log", "Teknik kayıt")) {
                                Text(log.joined(separator: "\n")).font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 6)
                            }
                        }
                        FlowLayout {
                            if model.harvest["running"] as? Bool == true { ProgressView().controlSize(.small); Text(L("Harvest running…")) }
                            else { Button(L("Run harvest now")) { model.startHarvest() } }
                            Button(L("Open full log")) { NSWorkspace.shared.open(URL(fileURLWithPath: str(model.settings, "state_dir", model.workspace)).appendingPathComponent("harvest.log")) }
                            Button(L("Show inbox")) { model.showVault("inbox") }
                        }
                    }.padding(12)
                }
            }.padding(30).frame(maxWidth: .infinity, alignment: .leading)
        }.onAppear(perform: load).onChange(of: pretty(schedule)) { load() }
    }
}

struct AdvancedSettings: View {
    @ObservedObject var model: Model
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageIntro(icon: "gearshape", title: T("General", "Genel"), text: T("Language, help and troubleshooting.", "Dil, yardım ve sorun giderme."))
                GroupBox(L("Language")) {
                    VStack(alignment: .leading, spacing: 10) {
                        Picker(L("App language"), selection: $model.language) { Text("Türkçe").tag("tr"); Text("English").tag("en") }.pickerStyle(.segmented).frame(maxWidth: 320)
                        Text(T("Changes Carry's own screens. The language of chat notes is set on the Chat notes tab.", "Carry'nin ekranlarını değiştirir. Sohbet notlarının dili Sohbet notları sekmesinden ayarlanır.")).font(.caption).foregroundStyle(.secondary)
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox(T("Help", "Yardım")) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(T("New here, or setting up another Mac? The setup guide walks you through notes and assistants again. Nothing you already have is removed.",
                               "Yeni misiniz ya da başka bir Mac mi kuruyorsunuz? Kurulum rehberi notlar ve asistanlar adımlarından yeniden geçirir. Mevcut hiçbir şey silinmez.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        FlowLayout {
                            Button(T("Open the setup guide", "Kurulum rehberini aç")) { model.startOnboarding() }
                            Button(T("How Carry works", "Carry nasıl çalışır?")) { model.showGuide = true }
                            Button(T("Show the usage tips on Home again", "Ana sayfadaki kullanım ipuçlarını yeniden göster")) { UserDefaults.standard.set(false, forKey: "hideHowTo"); model.page = "Overview" }
                        }
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                }
                DisclosureGroup(T("Troubleshooting", "Sorun giderme")) {
                    VStack(alignment: .leading, spacing: 16) {
                        GroupBox(T("Carry's settings folder", "Carry'nin ayar klasörü")) {
                            VStack(alignment: .leading, spacing: 10) {
                                Text(model.workspace).font(.callout.monospaced()).textSelection(.enabled)
                                Text(T("Holds Carry's settings and search data. Your notes are never stored here. Search data can always be rebuilt; do not delete this folder.",
                                       "Carry'nin ayarlarını ve arama verilerini tutar. Notlarınız asla burada saklanmaz. Arama verileri her zaman yeniden oluşturulabilir; bu klasörü silmeyin.")).font(.caption).foregroundStyle(.secondary)
                                FlowLayout {
                                    Button(L("Show in Finder")) { NSWorkspace.shared.open(URL(fileURLWithPath: model.workspace)) }
                                    Button(T("Open another settings folder", "Başka bir ayar klasörü aç")) { model.chooseWorkspace(create: false) }
                                }
                            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        }
                        GroupBox(L("Index and connection health")) {
                            VStack(alignment: .leading, spacing: 12) {
                                LabeledContent(L("Retrieval provider"), value: str(model.snapshot["embedding"] as? [String: Any] ?? [:], "name") + " · " + str(model.snapshot["embedding"] as? [String: Any] ?? [:], "model"))
                                LabeledContent(L("Local MCP test"), value: L(model.localTest))
                                FlowLayout {
                                    Button(T("Rebuild note search", "Not aramasını yeniden hazırla")) { model.run("index") { model.job = $0; model.notice = L("Rebuilding the index in the background.") } }
                                    Button(T("Check Carry's search service", "Carry arama hizmetini kontrol et")) { model.run("connection_test") { model.localTest = str($0,"mcp_local_test"); model.refresh() } }
                                    Button(L("Probe provider")) { model.run("snapshot", ["probe": true]) { model.snapshot = $0 } }
                                }
                                Text(L("The local test checks Carry’s MCP server only. Client trust and capture are verified by the clients themselves.")).font(.caption).foregroundStyle(.secondary)
                            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        }
                        DisclosureGroup(L("Diagnostics")) { Text(pretty(model.snapshot.filter { $0.key != "activity" })).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    }.padding(.top, 8)
                }
            }.padding(30).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct Sources: View {
    @ObservedObject var model: Model
    @State private var sourceID = "notes"
    @State private var writable = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageIntro(icon: "folder", title: T("Note folders", "Not klasörleri"),
                          text: T("The folders Carry reads your notes from. Removing one here never deletes any file.", "Carry'nin notlarınızı okuduğu klasörler. Buradan kaldırmak hiçbir dosyayı silmez."))
                ForEach(model.sources.indices, id: \.self) { i in
                    let source = model.sources[i]
                    SourceCard(model: model, source: source).id(str(source, "source_id") + pretty(source))
                }
                FlowLayout {
                Button(T("Create a new notes folder…", "Yeni not klasörü oluştur…")) { model.onboardingStep = 1; model.onboarding = true }
                Button(T("Add a notes folder…", "Not klasörü ekle…")) {
                    if let root = folder(T("Choose a folder that holds notes (.md files)", "Not (.md dosyaları) içeren bir klasör seçin")) {
                        let taken = Set(model.sources.map { str($0, "source_id") })
                        var sid = "notes", n = 2
                        while taken.contains(sid) { sid = "notes-\(n)"; n += 1 }
                        model.run("add_source", ["source_id": sid, "root": root, "writable": false]) { _ in model.notice = T("Folder added. Carry is reading it in the background.", "Klasör eklendi. Carry arka planda okuyor."); model.refresh() }
                    }
                }.buttonStyle(.borderedProminent)
                }
                DisclosureGroup(T("Advanced sources: GitHub, custom settings", "Gelişmiş kaynaklar: GitHub, özel ayarlar")) {
                    VStack(alignment: .leading, spacing: 16) {
                        GitHubSourceForm(model: model)
                        GroupBox(T("Add a folder with custom settings", "Özel ayarlarla klasör ekle")) {
                            VStack(alignment: .leading, spacing: 12) {
                                TextField(L("Source ID (letters, numbers, hyphen)"), text: $sourceID)
                                Toggle(L("Allow Carry to write records in this folder"), isOn: $writable)
                                Button(L("Choose folder and add")) {
                                    if let root = folder(L("Choose an existing Markdown folder or create a Carry records folder")) {
                                        model.run("add_source", ["source_id": sourceID, "root": root, "writable": writable]) { _ in model.notice = L("Source added. Indexing in the background."); model.refresh() }
                                    }
                                }
                            }.padding(12)
                        }
                    }.padding(.top, 8)
                }
            }.padding(30)
        }.disabled(model.snapshot.isEmpty)
    }
}

struct JevForm: View {
    @ObservedObject var model: Model
    @State private var key = ""
    @State private var present: Bool? = nil
    var body: some View {
                GroupBox(L("Relevance judge · TypeSafe Jev (optional)")) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(L("Jev checks, in about half a second per search, which passages actually answer the question, and drops passages that try to instruct an AI. The question and up to 32 candidate passages (secrets masked, best effort) are sent to TypeSafe in the US; TypeSafe says it does not train on them. Without a key, your assistant's small model does this job instead.")).fixedSize(horizontal: false, vertical: true)
                        HStack {
                            Text(present == nil ? L("Key status unknown") : (present! ? L("Key saved in your Keychain") : L("No key saved"))).foregroundStyle(.secondary)
                            Button(L("Check")) { model.run("jev_status") { present = $0["key_present"] as? Bool } }
                        }
                        HStack {
                            SecureField(L("TypeSafe API key (console.typesafe.ai › API Keys)"), text: $key)
                            Button(L("Save key")) { model.run("jev_key", ["key": key]) { value in key = ""; present = value["key_present"] as? Bool; model.notice = L("Key saved in your Keychain. Choose a Jev search mode above to use it.") } }.disabled(key.isEmpty)
                        }
                        Text(L("The key is stored in the macOS Keychain, never in Carry's workspace files.")).font(.caption).foregroundStyle(.secondary)
                    }.padding(12).onAppear { present = model.settings["jev_key"] as? Bool }
                }
    }
}

struct GitHubSourceForm: View {
    @ObservedObject var model: Model
    @State private var repository = ""
    @State private var githubID = "team"
    @State private var branch = ""
    @State private var contentFolder = ""
    var body: some View {
                GroupBox(L("Connect a GitHub knowledge base")) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(L("Choose a repository you can access. Carry keeps a read-only copy and checks for updates every five minutes while it is running."))
                        FlowLayout {
                            Button(L("Sign in to GitHub")) { model.run("github_login") { model.githubLogin = $0 } }
                            Button(L("Load my repositories")) { model.loadRepositories() }
                            Text(L(model.githubAccount)).foregroundStyle(.secondary)
                        }
                        if let code = model.githubLogin["user_code"] as? String {
                            HStack {
                                Text(L("Enter this code: ") + code).font(.title3.monospaced()).textSelection(.enabled)
                                Button(L("Copy code and open GitHub")) {
                                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(code, forType: .string)
                                    NSWorkspace.shared.open(URL(string: "https://github.com/login/device")!)
                                }
                                Button(L("Cancel sign-in")) { model.run("github_login_cancel") { model.githubLogin = $0 } }
                            }
                        }
                        if str(model.githubLogin, "state") == "failed" { Text(L("Sign-in did not finish. Try again.")).foregroundStyle(.red) }
                        if !model.repositories.isEmpty {
                            Picker(L("Repository"), selection: $repository) {
                                Text(L("Select a repository")).tag("")
                                ForEach(model.repositories.indices, id: \.self) { i in
                                    Text(str(model.repositories[i], "name")).tag(str(model.repositories[i], "name"))
                                }
                            }
                            if let page = model.nextRepositoryPage { Button(L("Load more repositories")) { model.loadRepositories(page: page) } }
                        }
                        TextField(L("Repository URL or owner/repository"), text: $repository)
                        HStack {
                            TextField(L("Source name"), text: $githubID)
                            TextField(L("Branch (default if blank)"), text: $branch)
                            TextField(L("Knowledge folder (whole repo if blank)"), text: $contentFolder)
                        }
                        Button(L("Connect and index")) {
                            model.run("github_connect", ["source_id": githubID, "repository": repository, "branch": branch, "folder": contentFolder]) {
                                model.job = $0; model.notice = $0["started"] as? Bool == false ? busyNotice : L("Connecting your repository in the background."); model.refresh()
                            }
                        }.buttonStyle(.borderedProminent).disabled(repository.isEmpty || githubID.isEmpty)
                        Text(L("GitHub remains the source of truth. Carry does not push changes to the repository.")).font(.caption).foregroundStyle(.secondary)
                    }.padding(12)
                }
    }
}

struct VaultForm: View {
    @ObservedObject var model: Model
    @State private var language = "Turkish"
    @State private var capture = false
    @State private var plan: [String: Any] = [:]
    var body: some View {
                GroupBox(L("Create a personal vault")) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(L("Start a personal knowledge vault in an empty folder: the operating guide for your assistant, templates, folders and a Git repository. Carry adds it as a source and connects Claude Code and Codex inside that folder. To use notes you already have, add their folder as a source instead."))
                        Picker(L("Language of your notes"), selection: $language) { Text(L("Türkçe")).tag("Turkish"); Text(L("English")).tag("English") }.pickerStyle(.segmented).onChange(of: language) { plan = [:] }
                        Toggle(L("Record my prompts in this vault (sources/carry) for later review"), isOn: $capture).onChange(of: capture) { plan = [:] }
                        Text(L("Only your own prompts are recorded, never replies or tool output. Masking is best effort. Leave this off if you only want search.")).font(.caption).foregroundStyle(.secondary)
                        Button(L("Choose an empty folder and preview")) {
                            if let target = folder(L("Choose or create an empty folder for your vault")) {
                                model.run("vault_preview", ["target": target, "language": language, "capture": capture]) { plan = $0 }
                            }
                        }.buttonStyle(.borderedProminent)
                        if !plan.isEmpty {
                            let files = plan["files"] as? [[String: Any]] ?? []
                            Text(str(plan, "target")).font(.callout).textSelection(.enabled)
                            Text(L("%d files will be created. Nothing is written until you confirm.", files.count))
                            ScrollView { LazyVStack(alignment: .leading) { ForEach(files.indices, id: \.self) { i in Text(str(files[i], "status") + "  " + str(files[i], "rel")).font(.caption.monospaced()) } } }.frame(maxHeight: 140)
                            FlowLayout {
                                Button(L("Create vault")) {
                                    model.run("vault_apply", ["id": str(plan, "id")]) { value in
                                        plan = [:]
                                        model.notice = L("Vault created at %@. Open this folder in Claude Code or Codex, approve the Carry server, then open it in Obsidian if you use it. Indexing runs in the background.", str(value, "target"))
                                        model.refresh()
                                    }
                                }.buttonStyle(.borderedProminent)
                                Button(L("Cancel")) { plan = [:] }
                            }
                        }
                    }.padding(12)
                }
    }
}

struct SourceCard: View {
    @ObservedObject var model: Model
    let source: [String: Any]
    @State private var exclusions = ""
    @State private var writable = false
    @State private var files: [String] = []
    @State private var confirmRemove = false
    @State private var listed = false
    var sid: String { str(source, "source_id") }
    var isGitHub: Bool { (source["github"] as? [String: Any])?.isEmpty == false }
    var patterns: [String] { exclusions.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
    var dirty: Bool { writable != (source["writable"] as? Bool == true) || patterns != (source["exclude"] as? [String] ?? []) }
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                FlowLayout {
                    Image(systemName: isGitHub ? "network" : "folder.fill").foregroundStyle(Color.accentColor)
                    Text(isGitHub ? sid : URL(fileURLWithPath: str(source, "root")).lastPathComponent).font(.headline)
                    Badge(text: isGitHub ? "GitHub" : (source["writable"] as? Bool == true ? T("Carry may add records", "Carry kayıt ekleyebilir") : T("your notes are not changed", "notlarınız değiştirilmez")))
                    if source["root_available"] as? Bool == false { Badge(text: T("folder not found", "klasör bulunamadı"), color: .red) }
                    if !isGitHub { Button(T("Show notes", "Notları göster")) { model.switchVault(sid); model.page = "Vault" } }
                    Button(T("Remove from Carry…", "Carry'den çıkar…"), role: .destructive) { confirmRemove = true }
                }
                if source["root_available"] as? Bool == false && !isGitHub {
                    HStack {
                        Text(T("Carry cannot find this folder. It may have been moved, renamed or be on a disconnected drive.", "Carry bu klasörü bulamıyor. Taşınmış, adı değişmiş ya da bağlı olmayan bir diskte olabilir.")).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                        Button(T("Choose its new location…", "Klasörün yeni yerini seç…")) {
                            if let root = folder(T("Where is this folder now?", "Bu klasör şimdi nerede?")) {
                                model.run("source_remove", ["source_id": sid]) { _ in
                                    model.run("add_source", ["source_id": sid, "root": root, "writable": source["writable"] as? Bool == true]) { _ in model.refresh() }
                                }
                            }
                        }
                    }
                }
                if let github = source["github"] as? [String: Any], let repo = github["repository"] as? String {
                    Text(repo + " · " + str(github, "branch"))
                    HStack {
                        Text(L("Commit ") + String(str(github, "commit").prefix(8))).font(.caption.monospaced())
                        if let synced = github["synced_at"] as? Double { Text(Date(timeIntervalSince1970: synced), style: .relative).font(.caption); Text(L("ago")).font(.caption) }
                        Button(L("Sync now")) { model.run("github_sync", ["source_id": sid]) { model.job = $0 } }
                    }
                } else { Text(str(source,"root")).textSelection(.enabled).font(.callout).foregroundStyle(.secondary) }
                DisclosureGroup(T("Settings for this folder", "Bu klasörün ayarları")) {
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle(T("Carry may add its own records here (in a carry/ sub-folder)", "Carry buraya kendi kayıtlarını ekleyebilir (carry/ alt klasörüne)"), isOn: $writable).disabled(isGitHub)
                        HStack(spacing: 4) {
                            Text(T("Leave out of search", "Aramaya dahil etme")).fontWeight(.medium)
                            InfoButton(text: T("Folders or files listed here stay visible on the Notes page but your assistant never gets them. Separate with commas. Example: +, x, drafts/old",
                                               "Burada yazan klasör ya da dosyalar Notlar sayfasında görünür ama asistanınıza asla verilmez. Virgülle ayırın. Örnek: +, x, taslaklar/eski"))
                        }
                        TextField(L("Excluded paths or patterns, separated by commas"), text: $exclusions, axis: .vertical).lineLimit(1...4)
                        FlowLayout {
                            Button(L("Save")) {
                                model.run("settings_update", ["sources": [sid: ["writable": writable, "exclude": patterns]]]) { value in
                                    model.settings = value; model.notice = L("Source saved. Re-indexing in the background."); model.refresh()
                                }
                            }.buttonStyle(.borderedProminent).disabled(!dirty)
                            Button(T("List searchable files", "Aranabilir dosyaları listele")) { model.run("source_files", ["source_id": sid]) { files = $0["files"] as? [String] ?? []; listed = true } }
                        }
                        if listed && files.isEmpty { Text(T("No file in this folder is included in search. Check what is left out above.", "Bu klasörde aramaya dahil dosya yok. Yukarıda neyin dışarıda bırakıldığını kontrol edin.")).foregroundStyle(.secondary) }
                        if !files.isEmpty {
                            Text(L("%d files indexed", files.count))
                            ScrollView { LazyVStack(alignment: .leading) { ForEach(files, id: \.self) { Text($0).font(.caption).textSelection(.enabled) } } }.frame(maxHeight: 160)
                        }
                    }.padding(.top, 8)
                }
            }.padding(10)
            .onAppear {
                exclusions = (source["exclude"] as? [String] ?? []).joined(separator: ", ")
                writable = source["writable"] as? Bool == true
            }
            .confirmationDialog(T("Remove “\(URL(fileURLWithPath: str(source, "root")).lastPathComponent)” from Carry?", "“\(URL(fileURLWithPath: str(source, "root")).lastPathComponent)” Carry'den çıkarılsın mı?"), isPresented: $confirmRemove) {
                Button(L("Remove source"), role: .destructive) { model.run("source_remove", ["source_id": sid]) { value in model.settings = value; model.closeNote(); model.vaultSource = ""; model.notice = L("Source removed. Its files were not touched."); model.refresh() } }
            } message: { Text(L("Carry stops searching this source. Its folder and files stay exactly as they are.")) }
        }
    }
}

struct Search: View {
    @ObservedObject var model: Model
    var initialQuery: String? = nil
    @State private var query = ""
    @State private var asked = ""
    @State private var result: [String: Any] = [:]
    var diagnostics: [String: Any] { result["diagnostics"] as? [String: Any] ?? [:] }
    var frame: [String: Any]? { diagnostics["timeframe"] as? [String: Any] }
    var hits: [[String: Any]] { result["evidence"] as? [[String: Any]] ?? [] }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageIntro(icon: "text.magnifyingglass", title: T("Search notes", "Notlarda ara"),
                          text: T("Type a question and Carry shows the parts of your notes it would give your assistant. No chat answer is written here.",
                                  "Bir soru yazın; Carry asistanınıza vereceği not bölümlerini göstersin. Burada sohbet yanıtı oluşturulmaz."))
                HStack {
                    TextField(L("Ask a question in Turkish or English"), text: $query).textFieldStyle(.roundedBorder).onSubmit(search)
                    Button(Lang.code == "tr" ? "Ara" : "Search", action: search).buttonStyle(.borderedProminent).disabled(query.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                Label(T("Tip: time words such as “last week”, “yesterday”, “last 7 days” or “in September” limit the search to notes from those dates.",
                        "İpucu: “geçen hafta”, “dün”, “son 7 gün”, “Eylül'de” gibi zaman ifadeleri aramayı o tarihlerdeki notlarla sınırlar."), systemImage: "calendar")
                    .font(.callout).foregroundStyle(.secondary)
                if model.ownNotes == 0 {
                    FlowLayout {
                        Text(T("Add a note first; then there is something to search.", "Önce bir not ekleyin; sonra aranacak bir şey olur.")).foregroundStyle(.secondary)
                        Button(T("Write my first note", "İlk notumu yaz")) { model.showNewNote = true }
                    }
                } else if result.isEmpty {
                    FlowLayout(spacing: 8) {
                        Text(T("Try:", "Deneyin:")).foregroundStyle(.secondary)
                        ForEach([T("What did we do last week?", "Geçen hafta ne yaptık?"), T("What happened in the last 7 days?", "Son 7 günde neler oldu?"), T("Which tasks are not finished?", "Hangi işler tamamlanmadı?"), T("What are my preferences?", "Tercihlerim neler?")], id: \.self) { q in
                            Button(q) { query = q; search() }.buttonStyle(.bordered).controlSize(.small)
                        }
                    }
                }
                if !result.isEmpty { summary }
                if frame?["listing"] as? Bool == true { groupedByDay } else {
                    ForEach(hits.indices, id: \.self) { i in card(hits[i]) }
                }
                if !hits.isEmpty { Text(L("These are source passages. Check that they answer your question before relying on them.")).font(.caption).foregroundStyle(.secondary) }
            }.padding(30)
        }.disabled(model.snapshot.isEmpty)
        .onAppear { if let q = initialQuery, result.isEmpty { query = q; search() } }
    }

    /// One plain sentence on what Carry did with the question, and why there are this many results.
    @ViewBuilder var summary: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                if let f = frame {
                    HStack(spacing: 8) {
                        Image(systemName: "calendar").foregroundStyle(Color.accentColor)
                        Text(T("“\(str(f, "phrase"))” = \(rangeLabel(f))", "“\(str(f, "phrase"))” = \(rangeLabel(f))")).fontWeight(.semibold)
                    }
                    let docs = Int(num(f, "documents"))
                    if docs == 0 {
                        Text(T("No note is dated in this period. Carry reads dates from file names, dated headings and created/date fields.",
                               "Bu tarihlerde tarihlenmiş bir not yok. Carry tarihleri dosya adlarından, tarihli başlıklardan ve oluşturulma/tarih alanlarından okur.")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    } else if f["listing"] as? Bool == true && f["topic_filtered"] as? Bool == true {
                        Text(T("\(docs) notes from this period mention “\(str(f, "topic", str(f, "residual")))”; newest first. None of them answered the question directly, so check them yourself.",
                               "Bu tarihlerde “\(str(f, "topic", str(f, "residual")))” geçen \(docs) not var; en yeniden eskiye. Hiçbiri soruyu doğrudan yanıtlamadığı için kendiniz göz atın.")).fixedSize(horizontal: false, vertical: true)
                    } else if f["listing"] as? Bool == true {
                        Text(hits.count < docs
                             ? T("\(docs) notes are dated in this period; the newest \(hits.count) are shown, one passage from each.", "Bu tarihlerde \(docs) not var; en yeni \(hits.count) tanesi, her birinden bir bölümle gösteriliyor.")
                             : T("\(docs) notes are dated in this period; all are shown, newest first.", "Bu tarihlerde \(docs) not var; hepsi en yeniden eskiye gösteriliyor.")).fixedSize(horizontal: false, vertical: true)
                        if hits.count < docs { Text(T("To see the rest, ask about a shorter period (for example “yesterday”) or add a topic.", "Kalanları görmek için daha kısa bir dönem sorun (örneğin “dün”) ya da bir konu ekleyin.")).font(.caption).foregroundStyle(.secondary) }
                    } else {
                        let inside = Int(num(f, "inside"))
                        Text(inside > 0
                             ? T("First \(inside) result(s) from this period, then related notes from other dates (the question may be about something that happened then but was written down later).",
                                 "Önce bu dönemden \(inside) sonuç, ardından diğer tarihlerden ilgili notlar (soru o dönemde olan ama sonradan yazılan bir şey hakkında olabilir).")
                             : T("Nothing from this period answers the question directly; related notes from other dates are shown.",
                                 "Bu dönemden soruyu doğrudan yanıtlayan bir şey yok; diğer tarihlerden ilgili notlar gösteriliyor.")).fixedSize(horizontal: false, vertical: true)
                    }
                    FlowLayout(spacing: 6) {
                        Text(T("Other periods:", "Başka dönem:")).font(.caption).foregroundStyle(.secondary)
                        ForEach(periods, id: \.0) { p in
                            Button(p.0) { query = asked.replacingOccurrences(of: str(f, "phrase"), with: p.1); search() }.buttonStyle(.bordered).controlSize(.small)
                        }
                    }
                } else if str(result, "status") == "evidence" {
                    let rejected = Int(num(diagnostics, "rejected_candidates"))
                    Text(rejected > 0
                         ? T("Carry looked at \(hits.count + rejected) passages and kept the \(hits.count) that best fit the question; \(rejected) did not fit well enough.",
                             "Carry \(hits.count + rejected) not bölümüne baktı; soruya en iyi uyan \(hits.count) tanesini tuttu, \(rejected) tanesi yeterince uymadığı için elendi.")
                         : T("\(hits.count) passage(s) from your notes, best match first.", "Notlarınızdan \(hits.count) bölüm, en uygun olan önce.")).fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 4) {
                        Text(T("Why only a few?", "Neden az sonuç?")).font(.caption).foregroundStyle(.secondary)
                        InfoButton(text: T("Carry gives your assistant only passages that fit the question, so it is not misled by loosely related text. Name the topic, person or project, or add a time (“last week”), to get more.",
                                           "Carry asistanınıza yalnızca soruya uyan bölümleri verir; böylece asistan gevşek bağlantılı metinlerle yanılmaz. Daha fazla sonuç için konuyu, kişiyi ya da projeyi adıyla yazın veya bir zaman ekleyin (“geçen hafta”)."))
                    }
                }
                if str(result, "status") == "no_evidence" && frame == nil {
                    Text(T("No part of your notes answers this question closely enough.", "Notlarınızda bu soruyu yeterince yakından yanıtlayan bir bölüm bulunamadı.")).fontWeight(.medium)
                    Text(T("Try the name of the project or person, other words, or a time such as “last week”.", "Proje ya da kişinin adını, başka kelimeleri ya da “geçen hafta” gibi bir zamanı deneyin.")).foregroundStyle(.secondary)
                }
                if str(result, "status") == "unavailable" { Text(L("The index is not ready yet. Check the background task status.")) }
            }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    var periods: [(String, String)] {
        Lang.code == "tr"
            ? [("Dün", "dün"), ("Bu hafta", "bu hafta"), ("Geçen hafta", "geçen hafta"), ("Son 7 gün", "son 7 gün"), ("Son 30 gün", "son 30 gün")]
            : [("Yesterday", "yesterday"), ("This week", "this week"), ("Last week", "last week"), ("Last 7 days", "last 7 days"), ("Last 30 days", "last 30 days")]
    }

    var groupedByDay: some View {
        let days = Array(Set(hits.map { str($0, "date", "") })).sorted(by: >)
        return ForEach(days, id: \.self) { day in
            VStack(alignment: .leading, spacing: 8) {
                Text(dayLabel(day)).font(.headline).padding(.top, 6)
                ForEach(hits.indices.filter { str(hits[$0], "date", "") == day }, id: \.self) { i in card(hits[i], showDate: false) }
            }
        }
    }

    func card(_ hit: [String: Any], showDate: Bool = true) -> some View {
        SearchHitCard(model: model, hit: hit, showDate: showDate)
    }

    func rangeLabel(_ f: [String: Any]) -> String {
        let s = dayLabel(str(f, "start"), weekday: false), e = dayLabel(str(f, "end"), weekday: false)
        return s == e ? s : s + " – " + e
    }

    func search() {
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        asked = query
        model.run("recall", ["query": query]) { result = $0 }
    }
}

/// "18 Eylül 2026 Cuma" from an ISO date, in the app language.
func dayLabel(_ iso: String, weekday: Bool = true) -> String {
    let parse = DateFormatter(); parse.dateFormat = "yyyy-MM-dd"; parse.locale = Locale(identifier: "en_US_POSIX")
    guard let date = parse.date(from: iso) else { return iso.isEmpty ? T("Undated", "Tarihsiz") : iso }
    let out = DateFormatter(); out.locale = Lang.locale; out.setLocalizedDateFormatFromTemplate(weekday ? "d MMMM yyyy EEEE" : "d MMMM yyyy")
    return out.string(from: date)
}

struct SearchHitCard: View {
    @ObservedObject var model: Model
    let hit: [String: Any]
    let showDate: Bool
    @State private var expanded = false
    /// Indexed passages start with "Title - " or "Title > Heading" for search; the card already shows both.
    var passage: String {
        var text = str(hit, "text")
        let title = str(hit, "title"), heading = str(hit, "heading", "")
        for prefix in [title + " > " + heading, title + " - ", title] where !title.isEmpty && text.hasPrefix(prefix) {
            text = String(text.dropFirst(prefix.count)); break
        }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Text(str(hit, "title")).font(.headline).lineLimit(2)
                    Spacer()
                    if showDate, let day = hit["date"] as? String { Badge(text: dayLabel(day, weekday: false)) }
                    if hit["in_range"] as? Bool == false { Badge(text: T("other date", "başka tarih"), color: .orange) }
                    if let score = hit["relevance_score"] as? NSNumber {
                        let v = score.doubleValue
                        Badge(text: v >= 0.8 ? T("strong match", "güçlü eşleşme") : v >= 0.5 ? T("good match", "iyi eşleşme") : T("weak match", "zayıf eşleşme"),
                              color: v >= 0.8 ? .green : v >= 0.5 ? .accentColor : .secondary)
                    }
                }
                HStack(spacing: 6) {
                    Text(folderLabel(str(hit, "path").contains("/") ? String(str(hit, "path").split(separator: "/").dropLast().joined(separator: "/")) : "(root)"))
                    if let heading = hit["heading"] as? String, !heading.isEmpty, heading != "summary", heading != str(hit, "title") { Text("› " + heading).lineLimit(1) }
                }.font(.caption).foregroundStyle(.secondary)
                Text(passage).textSelection(.enabled).lineLimit(expanded ? nil : 6).fixedSize(horizontal: false, vertical: true)
                HStack {
                    if str(hit, "text").count > 420 { Button(expanded ? T("Show less", "Daha az göster") : T("Show more", "Devamını göster")) { expanded.toggle() }.buttonStyle(.link) }
                    Spacer()
                    if let url = hit["url"] as? String, let link = URL(string: url) { Link(L("Open this version on GitHub"), destination: link) }
                    else if model.localSources.contains(where: { str($0, "source_id") == str(hit, "source_id") }) {
                        Button(T("Open note", "Notu aç")) { model.openNote(str(hit, "path"), source: str(hit, "source_id")) }
                    }
                }
            }.padding(8).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct Connections: View {
    @ObservedObject var model: Model
    @State private var client = "claude"
    @State private var source = ""
    @State private var project = ""
    @State private var executable = ""
    @State private var prompts = false
    @State private var proposals = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageIntro(icon: "person.2.wave.2", title: T("Assistants", "Asistanlar"),
                          text: T("The AI tools that use your notes. Connect the ones you use; they can then search your notes whenever you open them in your notes folder.",
                                  "Notlarınızı kullanan yapay zekâ araçları. Kullandıklarınızı bağlayın; not klasörünüzde açtığınızda notlarınızda arama yapabilirler."))
                AssistantCard(model: model, client: "claude")
                AssistantCard(model: model, client: "codex")
                DisclosureGroup(T("Advanced: connect another project folder, record prompts, undo a connection", "Gelişmiş: başka bir proje klasörünü bağla, istemleri kaydet, bağlantıyı geri al")) {
                VStack(alignment: .leading, spacing: 18) {
                GroupBox(L("Project connection")) {
                    VStack(alignment: .leading, spacing: 12) {
                        Picker(L("Client"), selection: $client) { Text(L("Claude Code")).tag("claude"); Text(L("Codex")).tag("codex") }.pickerStyle(.segmented)
                        HStack { Text(project.isEmpty ? L("No project selected") : project).lineLimit(2).textSelection(.enabled); Spacer(); Button(L("Choose project")) { if let p = folder(L("Select the client’s project folder")) { project = p; model.preview = [:] } } }
                        TextField(L("Client executable (optional; auto-detect by default)"), text: $executable)
                        Picker(L("Write destination"), selection: $source) {
                            Text(L("Choose a writable source")).tag("")
                            ForEach(model.sources.filter { $0["writable"] as? Bool == true }.indices, id: \.self) { i in
                                let sources = model.sources.filter { $0["writable"] as? Bool == true }
                                Text(str(sources[i],"source_id")).tag(str(sources[i],"source_id"))
                            }
                        }
                        Toggle(L("Capture whole user prompts in this project"), isOn: $prompts)
                        Text(L("Only user prompts are captured. Replies and tool output are excluded. Masking is best effort. Each event becomes an unaccepted draft.")).font(.caption).foregroundStyle(.secondary)
                        Toggle(L("Allow this client to propose draft decisions"), isOn: $proposals)
                        Text(L("Unchecked options leave existing opt-ins unchanged. Pausing configured capture also pauses this client’s proposals.")).font(.caption).foregroundStyle(.secondary)
                        Text(L("These files are project-local (.mcp.json / .claude or .codex). Their paths may appear in version control; inspect them before sharing your project.")).font(.caption).foregroundStyle(.secondary)
                        Button(L("Preview connection changes")) {
                            model.run("connection_preview", ["client": client, "project": project, "source_id": source, "prompts": prompts, "proposals": proposals, "executable": executable]) { model.preview = $0 }
                        }.buttonStyle(.borderedProminent).disabled(project.isEmpty || ((prompts || proposals) && source.isEmpty))
                    }.padding(12)
                }
                if !model.preview.isEmpty {
                    GroupBox(L("Exact changes · ") + str(model.preview,"client")) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(str(model.preview,"summary", L("No changes"))).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                            Text(str(model.preview,"trust")).font(.callout)
                            Button(L("Apply these changes")) { model.run("connection_apply", ["id": str(model.preview,"id")]) { model.notice = str($0,"trust"); model.preview = [:]; model.refresh() } }.buttonStyle(.borderedProminent)
                        }.padding(12)
                    }
                }
                ForEach(model.adapters.indices, id: \.self) { i in
                    let adapter = model.adapters[i]
                    GroupBox(str(adapter,"client").capitalized) {
                        VStack(alignment: .leading, spacing: 10) {
                            LabeledContent(L("Capture capability"), value: str(adapter["capability"] as? [String: Any] ?? [:],"state"))
                            LabeledContent(L("Persisted capture"), value: str(adapter,"state"))
                            if let receipt = adapter["receipt"] as? [String: Any] {
                                Text(L("Last successful event: ") + str(receipt,"last_success_at")).font(.caption)
                                if let sid = receipt["source_id"] as? String, let path = receipt["event_path"] as? String { Button(L("Open raw event")) { model.openSource(sid, path) } }
                            }
                            if let action = adapter["action"] as? String { Text(action).font(.caption).foregroundStyle(.secondary) }
                            Button(str(adapter,"state") == "paused" ? L("Resume capture") : L("Pause capture")) {
                                model.run("pause", ["client": str(adapter,"client"), "paused": str(adapter,"state") != "paused"]) { _ in model.refresh() }
                            }
                        }.padding(12)
                    }
                }
                Text(L("Native test: restart the selected client, complete its project and hook approvals, then submit ‘Carry connection test — synthetic Cedar note.’ Refresh here. Only a persisted receipt counts as capture success.")).font(.callout).foregroundStyle(.secondary)
                ForEach(model.connections.indices, id: \.self) { i in
                    let connection = model.connections[i]
                    HStack {
                        VStack(alignment: .leading) { Text(str(connection,"client") + " · " + str(connection,"state")); Text(str(connection,"project")).font(.caption).textSelection(.enabled) }
                        Spacer()
                        if str(connection,"state") != "rolled_back" {
                            Button(L("Roll back setup")) { model.run("connection_rollback", ["id": str(connection,"id")]) { _ in model.notice = L("Setup rolled back. Restart the client. Existing records are retained."); model.refresh() } }
                        }
                    }
                }
                }.padding(.top, 8)
                }
            }.padding(30)
        }.disabled(model.snapshot.isEmpty)
    }
}

struct Activity: View {
    @ObservedObject var model: Model
    @State private var pendingOnly = false
    var entries: [[String: Any]] { model.activity.filter { !pendingOnly || str($0,"state") == "draft" } }
    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 14) {
                Button { model.page = "Review" } label: { Label(pageName("Review"), systemImage: "chevron.left") }.buttonStyle(.link)
                Text(pageName("Proposals")).font(.title.bold())
                Text(T("Changes your assistant proposes to your existing notes. Accept or reject each one here. (New chat drafts are on the Review page.)",
                       "Asistanınızın mevcut notlarınız için önerdiği değişiklikler. Her birini burada kabul edin ya da reddedin. (Yeni sohbet taslakları İncele sayfasındadır.)")).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Toggle(T("Only those waiting for approval", "Yalnız onay bekleyenler"), isOn: $pendingOnly)
                ForEach(model.adapters.indices, id: \.self) { i in
                    let adapter = model.adapters[i]
                    VStack(alignment: .leading, spacing: 4) {
                        Text(str(adapter,"client").capitalized + " · " + str(adapter,"state")).font(.caption.weight(.medium))
                        if let receipt = adapter["receipt"] as? [String: Any] {
                            Text(str(receipt,"at")).font(.caption2).foregroundStyle(.secondary)
                            if let sid = receipt["source_id"] as? String, let path = receipt["event_path"] as? String { Button(L("Inspect captured event")) { model.openSource(sid, path) }.font(.caption) }
                        }
                        if let action = adapter["action"] as? String { Text(action).font(.caption2).foregroundStyle(.secondary) }
                    }
                }
                if entries.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(model.activity.isEmpty ? T("No proposals have arrived yet.", "Henüz hiç öneri gelmedi.") : T("Nothing waits for your approval.", "Onayınızı bekleyen öneri yok.")).fontWeight(.medium)
                        Text(T("This page fills only when an assistant asks to correct one of your notes: instead of editing the note itself, it sends a proposal here for your yes or no. That happens rarely, so an empty page is normal.",
                               "Bu sayfa yalnızca bir asistan notlarınızdan birini düzeltmek istediğinde dolar: notu kendisi değiştirmek yerine öneriyi evet ya da hayır demeniz için buraya gönderir. Bu seyrek olur; sayfanın boş olması normaldir.")).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        Text(T("New chat drafts are reviewed on the Review page.", "Yeni sohbet taslaklarını İncele sayfasında inceleyebilirsiniz.")).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        Button(T("Open Review", "İncele'yi aç")) { model.page = "Review" }.buttonStyle(.link)
                        Button(T("Open drafts", "Taslakları aç")) { model.showVault("drafts") }.buttonStyle(.link)
                        if !model.anyAssistantConnected { Button(L("Connect a client")) { model.page = "Settings"; model.settingsTab = "Clients" }.buttonStyle(.link) }
                    }.padding(.top, 16)
                }
                List(entries.indices, id: \.self) { i in
                    let item = entries[i]
                    Button { model.inspect(item) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(str(item,"title", str(item,"path"))).lineLimit(2)
                            Text(str(item,"state").capitalized + " · rev " + str(item,"revision")).font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
                    }.buttonStyle(.plain).accessibilityLabel(L("Review") + " " + str(item,"state") + " " + str(item,"record_id"))
                }.listStyle(.inset)
            }.padding(24).frame(minWidth: 240, idealWidth: 300, maxWidth: 360)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if model.review.isEmpty && entries.isEmpty {
                        Heading(title: T("Nothing to review", "İncelenecek bir şey yok"), subtitle: T("When your assistant proposes a change to one of your notes, it appears on the left.", "Asistanınız notlarınızdan biri için değişiklik önerdiğinde solda görünür."))
                    } else if model.review.isEmpty {
                        Heading(title: T("Pick a proposal", "Bir öneri seçin"), subtitle: T("On the left, pick a proposal to see what it would change and where it came from. Accepting applies exactly the version you read.", "Soldan bir öneri seçin; neyi değiştireceğini ve nereden geldiğini görün. Kabul ettiğinizde tam olarak okuduğunuz sürüm uygulanır."))
                    } else {
                        Heading(title: str(model.review,"state").capitalized + " decision", subtitle: str(model.review,"source_id") + " · revision " + str(model.review,"revision"))
                        Text(str(model.review,"record_id")).font(.caption.monospaced()).textSelection(.enabled)
                        if let conflict = model.review["conflict"] as? String { Text(L("Conflict: ") + conflict).foregroundStyle(.red) }
                        if let target = model.review["target"] as? [String: Any] { Text(L("Corrects ") + str(target,"source_id") + ":" + str(target,"path") + " · rev " + str(target,"revision")).font(.callout).textSelection(.enabled) }
                        Text(str(model.review,"content")).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                        GroupBox(L("Change from current decision")) { Text(str(model.review,"diff")).font(.system(.callout, design: .monospaced)).textSelection(.enabled).padding(10).frame(maxWidth: .infinity, alignment: .leading) }
                        Button(L("Open decision Markdown")) { model.openSource(str(model.review,"source_id"), str(model.review,"path")) }
                        let refs = model.review["sources"] as? [String] ?? []
                        ForEach(refs, id: \.self) { ref in
                            Button(L("Open source: ") + ref) {
                                let parts = ref.split(separator: ":", maxSplits: 1).map(String.init)
                                if parts.count == 2 { model.openSource(parts[0], parts[1]) }
                            }
                        }
                        if str(model.review,"state") == "draft" {
                            Text(L("History stays on disk. If the draft or its target changes, acceptance is refused; reload the decision before reviewing again.")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }.padding(28).frame(maxWidth: .infinity, alignment: .leading)
            }.safeAreaInset(edge: .bottom) {
                if str(model.review,"state") == "draft" {
                    FlowLayout {
                        Button(L("Accept this revision")) { model.finish("accept") }.buttonStyle(.borderedProminent).disabled(model.review["conflict"] is String)
                        Button(L("Reject draft")) { model.finish("reject") }
                        Text(L("Revision ") + str(model.review,"revision")).font(.caption).foregroundStyle(.secondary)
                    }.padding(16).background(Color(nsColor: .windowBackgroundColor))
                }
            }.frame(minWidth: 390)
        }
    }
}

struct ContentView: View {
    @StateObject private var model = Model()
    let pages = [("Overview", "house"), ("Review", "checkmark.circle"), ("Vault", "books.vertical"), ("Search", "magnifyingglass"), ("Settings", "gearshape")]
    var body: some View {
        Group { if model.onboarding { Onboarding(model: model) } else {
        NavigationSplitView {
            VStack(alignment: .leading) {
                HStack(spacing: 10) {
                    Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 34, height: 34)
                    Text(L("Carry")).font(.title.bold())
                }.padding(20)
                List(selection: $model.page) {
                    ForEach(pages, id: \.0) { item in
                        Label(pageName(item.0), systemImage: item.1).tag(item.0).padding(.vertical, 4)
                            .badge(item.0 == "Review" ? reviewable(model.files).count + model.pendingReview : 0)
                    }
                }.listStyle(.sidebar)
                Button { model.showGuide = true } label: { Label(T("How Carry works", "Carry nasıl çalışır?"), systemImage: "questionmark.circle") }
                    .buttonStyle(.borderless).padding(.horizontal, 20).padding(.top, 8)
                Text(T("PILOT · LOCAL", "PİLOT · YEREL")).font(.caption2.weight(.semibold)).foregroundStyle(.secondary).padding(.horizontal, 20).padding(.bottom, 20).padding(.top, 4)
            }.navigationSplitViewColumnWidth(min: 190, ideal: 205)
        } detail: {
            VStack(spacing: 0) {
                if !model.error.isEmpty { banner(model.error, color: .red) }
                if !model.notice.isEmpty { banner(model.notice, color: .secondary) }
                if str(model.job, "state") == "running" {
                    HStack {
                        ProgressView().controlSize(.small)
                        Text(L(str(model.job, "stage", "Preparing").replacingOccurrences(of: "_", with: " ").capitalized))
                        if let done = model.job["completed"] as? Double, let total = model.job["total"] as? Double, total > 0 { Text("\(Int(done / total * 100))%") }
                        Spacer()
                    }.padding(12)
                }
                if ["failed", "interrupted"].contains(str(model.job, "state")) {
                    banner(UIError(str(model.job, "error", L("The background task was interrupted. Retry from Settings."))).localizedDescription, color: .red)
                }
                Group {
                    switch model.page {
                    case "Vault": VaultView(model: model)
                    case "Search": Search(model: model).disabled(model.busy)
                    case "Review": ReviewView(model: model)
                    case "Proposals": Activity(model: model).disabled(model.busy)
                    case "Settings": SettingsView(model: model).disabled(model.busy)
                    default: Overview(model: model)
                    }
                }
                // An explicit ideal width: pages must shrink to the window, not ask for their
                // one-line text width (which pushed the sidebar off-screen).
                .frame(minWidth: 560, idealWidth: 780, maxWidth: .infinity, maxHeight: .infinity)
                Divider()
                HStack {
                    if model.busy { ProgressView().controlSize(.small); Text(L("Working locally…")) }
                    else { Image(systemName: "internaldrive"); Text(model.workspace.isEmpty ? L("No workspace selected") : model.workspace).lineLimit(1).truncationMode(.middle).textSelection(.enabled) }
                    Spacer()
                    if !model.vault.isEmpty { Text(str(model.vault, "root")).lineLimit(1).truncationMode(.head) }
                }.font(.caption).foregroundStyle(.secondary).padding(10)
            }.navigationTitle(pageName(model.page)).toolbar {
                Button(L("Refresh"), systemImage: "arrow.clockwise") { model.refresh() }.disabled(model.busy || model.workspace.isEmpty)
            }
        } } }.environment(\.locale, Lang.locale).frame(minWidth: 1020, minHeight: 680)
        .sheet(isPresented: $model.showGuide) { GuideSheet(model: model) }
        .sheet(isPresented: $model.showNewNote) { NewNoteSheet(model: model) }.onReceive(Timer.publish(every: 2, on: .main, in: .common).autoconnect()) { _ in model.tick() }.onAppear {
            if ProcessInfo.processInfo.environment["CARRY_MEASURE_STARTUP"] == "1" { print("carry_ui_ready"); fflush(stdout) }
            model.refresh()
        }
    }
    func banner(_ text: String, color: Color) -> some View {
        HStack(alignment: .top) {
            Text(text).font(.callout).textSelection(.enabled); Spacer()
            Button { model.error = ""; model.notice = "" } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
        }.padding(12).foregroundStyle(color).background(color.opacity(0.07))
    }
}

#if !CARRY_VIEW_TESTS
@main struct CarryApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        WindowGroup("Carry") { ContentView() }.defaultSize(width: 1240, height: 820)
    }
}
#endif

// MARK: - Turkish

let TR: [String: String] = [
    "Core stopped. Refresh to reconnect; check the result before repeating a write.": "Carry'nin arka plan hizmeti durdu. Yeniden bağlanmak için Yenile'ye basın; bir şey kaydediyorsanız tekrar denemeden önce kontrol edin.",
    "Core response exceeded the app limit.": "Çekirdeğin yanıtı uygulama sınırını aştı.",
    "Invalid core response.": "Çekirdekten geçersiz yanıt geldi.",
    "This build is missing GitHub support. Use the complete Carry pilot package.": "Bu sürümde GitHub desteği yok. Tam Carry pilot paketini kullanın.",
    "GitHub could not read this repository. Sign in and check that your account has access.": "GitHub bu repoyu okuyamadı. Giriş yapın ve hesabınızın erişimi olduğunu kontrol edin.",
    "GitHub could not be reached. Check your connection and retry.": "GitHub'a ulaşılamadı. Bağlantınızı kontrol edip tekrar deneyin.",
    "Enter a GitHub repository URL or owner/repository.": "Bir GitHub repo adresi ya da sahip/repo girin.",
    "No Markdown files were found in that folder. Check the repository, branch and folder.": "O klasörde Markdown dosyası bulunamadı. Repoyu, dalı ve klasörü kontrol edin.",
    "The model download did not finish. Check your connection and try again.": "Model indirmesi tamamlanmadı. Bağlantınızı kontrol edip tekrar deneyin.",
    "The relevance model could not be installed. Try again; the embedding model may already be ready.": "Alaka değerlendirme modeli kurulamadı. Tekrar deneyin; embedding modeli zaten hazır olabilir.",
    "This build is missing its model runtime. Use the complete Carry pilot package or install Ollama.": "Bu sürümde model çalışma ortamı yok. Tam Carry pilot paketini kullanın ya da Ollama kurun.",
    "The local model service could not start. Open Ollama and retry.": "Yerel model servisi başlatılamadı. Ollama'yı açıp tekrar deneyin.",
    "This folder has no Carry workspace. Create one or open an existing workspace folder.": "Bu klasörde Carry çalışma alanı yok. Yeni bir tane oluşturun ya da mevcut bir çalışma alanı klasörü açın.",
    "This folder already has a workspace. Use Open workspace.": "Bu klasörde zaten bir çalışma alanı var. “Çalışma alanını aç” seçeneğini kullanın.",
    "That source ID is already in use. Choose a different ID.": "Bu kaynak kimliği zaten kullanılıyor. Başka bir kimlik seçin.",
    "Use lowercase letters, numbers, hyphens or underscores for the source ID.": "Kaynak kimliğinde küçük harf, rakam, tire ya da alt çizgi kullanın.",
    "This folder overlaps an existing source. Choose a separate folder.": "Bu klasör mevcut bir kaynakla çakışıyor. Ayrı bir klasör seçin.",
    "Keep Carry’s workspace folder outside your Markdown source folders.": "Carry'nin çalışma alanı klasörünü Markdown kaynak klasörlerinizin dışında tutun.",
    "Settings changed after the preview. Refresh and review a new preview. If setup was interrupted, use its rollback entry.": "Önizlemeden sonra ayarlar değişti. Yenileyip yeni önizlemeyi inceleyin. Kurulum yarıda kaldıysa geri alma kaydını kullanın.",
    "Settings changed after setup. Rollback stopped to preserve those edits. Review the changed files before disconnecting.": "Kurulumdan sonra ayarlar değişti. O düzenlemeleri korumak için geri alma durduruldu. Bağlantıyı kesmeden önce değişen dosyaları inceleyin.",
    "This project already has a different Carry connection. Review or roll back that setup before replacing it.": "Bu projenin zaten farklı bir Carry bağlantısı var. Değiştirmeden önce o kurulumu inceleyin ya da geri alın.",
    "The preview expired when the core restarted or the workspace changed. Preview the connection again.": "Çekirdek yeniden başladığı ya da çalışma alanı değiştiği için önizlemenin süresi doldu. Bağlantıyı yeniden önizleyin.",
    "The draft or its source changed. Select the decision again and review the new diff before accepting.": "Taslak ya da kaynağı değişti. Kararı yeniden seçin ve kabul etmeden önce yeni farkı inceleyin.",
    "The client CLI was not found. Install it or select its executable path.": "İstemci CLI'ı bulunamadı. Kurun ya da çalıştırılabilir dosyanın yolunu seçin.",
    "Update the client: Claude Code 2.1.196 or newer, or Codex CLI 0.153.4 or newer, is required.": "İstemciyi güncelleyin: Claude Code 2.1.196 veya üstü ya da Codex CLI 0.153.4 veya üstü gerekiyor.",
    "Codex hooks are disabled. Enable hooks in Codex before opting in to capture.": "Codex hook'ları kapalı. Kayda katılmadan önce Codex'te hook'ları açın.",
    "That folder already has notes. Choose an empty folder for a new vault, or add your existing notes folder as a source below.": "O klasörde zaten notlar var. Yeni not klasörü için boş bir klasör seçin ya da mevcut not klasörünüzü ekleyin.",
    "Choose a folder, not a file, for the new vault.": "Yeni not klasörü için dosya değil, klasör seçin.",
    "The vault folder changed after the preview. Preview again before creating the vault.": "Klasör bu arada değişti. Lütfen yeniden deneyin.",
    "This vault is connected read-only. Prompt capture needs a writable vault source.": "Bu not klasörü yalnız okunur bağlı. İstem kaydı için Carry'nin yazabildiği bir klasör gerekir.",
    "Choose a language and whether to record prompts, then preview again.": "Bir dil ve istemlerin kaydedilip kaydedilmeyeceğini seçip yeniden önizleyin.",
    "The key could not be saved in the Keychain. Paste it again; it must not be empty.": "Anahtar Anahtar Zinciri'ne kaydedilemedi. Yeniden yapıştırın; boş olmamalı.",
    "This source folder is not available. Reconnect the drive or check the folder in Settings › Sources.": "Bu not klasörüne erişilemiyor. Diski yeniden bağlayın ya da Ayarlar › Not klasörleri'nden kontrol edin.",
    "That note no longer exists at this path. Refresh the vault list.": "Bu not artık bu yerde yok. Listeyi yenileyin.",
    "This file is too large to preview here. Open it in your editor.": "Bu dosya burada önizlemek için çok büyük. Editörünüzde açın.",
    "One of the values is outside its allowed range. Check the numbers and save again.": "Değerlerden biri izin verilen aralığın dışında. Sayıları kontrol edip yeniden kaydedin.",
    "Choose a valid time for the nightly harvest.": "Gecelik çalışma için geçerli bir saat seçin.",
    "Add a Markdown folder or vault in Settings › Sources first.": "Önce Ayarlar › Not klasörleri'nden bir not klasörü ekleyin.",
    "Semantic search · Jev judges": "En iyi (akıllı arama + Jev)",
    "Semantic search · your assistant checks": "Akıllı",
    "Keyword search · Jev judges": "Basit + Jev",
    "Keyword search · your assistant checks": "Basit",
    "Accurate multilingual search": "Hassas çok dilli",
    "Choose or create a separate folder for Carry’s index and settings": "Carry'nin indeksi ve ayarları için ayrı bir klasör seçin ya da oluşturun",
    "Open a Carry workspace folder": "Bir Carry çalışma alanı klasörü açın",
    "No application could open this Markdown file. Choose a Markdown editor in Finder.": "Bu Markdown dosyasını açabilecek bir uygulama bulunamadı. Finder'da bir Markdown editörü seçin.",
    "Carry is finishing another background task (indexing or a download). Nothing was started; try again when the progress bar disappears.": "Carry arka planda başka bir işi bitiriyor (notları tarama ya da indirme). Hiçbir şey başlatılmadı; ilerleme çubuğu kaybolunca tekrar deneyin.",
    "Show in Vault": "Notlarda göster",
    "Your context, carried.": "Bağlamınız, yanınızda.",
    "Local Markdown. Decisions you can trace, review and correct.": "Yerel Markdown. İzleyebileceğiniz, inceleyebileceğiniz ve düzeltebileceğiniz kararlar.",
    "Start with a workspace": "Bir çalışma alanıyla başlayın",
    "Choose a separate folder for Carry’s settings and disposable index. Add your Markdown sources in Settings › Sources.": "Carry'nin ayarları ve silinebilir indeksi için ayrı bir klasör seçin. Markdown kaynaklarınızı Ayarlar › Kaynaklar'dan ekleyin.",
    "Notes stay on this Mac. Passages requested by Claude Code or Codex may reach the model you use there. Carry sends no analytics.": "Notlar bu Mac'te kalır. Claude Code ya da Codex'in istediği pasajlar orada kullandığınız modele ulaşabilir. Carry analiz verisi göndermez.",
    "Create workspace": "Çalışma alanı oluştur",
    "Open workspace": "Çalışma alanını aç",
    "Overview": "Genel bakış",
    "Add a Markdown folder or vault in Settings › Sources.": "Ayarlar › Kaynaklar'dan bir Markdown klasörü ya da vault ekleyin.",
    "Notes": "Notlar",
    "%@ searchable by your assistant": "%@ tanesi asistanınız tarafından aranabilir",
    "Changed this week": "Bu hafta değişen",
    "Most recent first": "En yeniler önce",
    "Inbox (+/)": "Gelen kutusu (+/)",
    "Captures waiting to be routed": "Yerleştirilmeyi bekleyen kayıtlar",
    "Drafts": "Taslaklar",
    "Notes marked draft: true": "draft: true işaretli notlar",
    "Index": "Arama",
    "Search": "Arama",
    "Change in Settings › Search": "Ayarlar › Arama'dan değiştirin",
    "Recently changed": "Son değişenler",
    "No notes found.": "Not bulunamadı.",
    "Folders": "Klasörler",
    "Open items from recent chats": "Son sohbetlerden açık işler",
    "No open items. Harvest drafts them from finished chats.": "Açık iş yok. Harvest bunları biten sohbetlerden taslak olarak çıkarır.",
    "Recent decisions": "Son kararlar",
    "Recent commits (%d)": "Son commit'ler (%d)",
    "Open draft": "Taslağı aç",
    "Nightly harvest": "Gecelik sohbet notları",
    "Runs every evening at %@ · drafts in %@. Chats also harvest when they end.": "Her akşam %@'da çalışır · taslak dili: %@. Sohbet bittiğinde de not çıkarılır.",
    "Not scheduled. Chats still harvest when they end if the client hooks are set up.": "Gecelik çalışma kapalı. Sohbet bittiğinde yine de not çıkarılır.",
    "Run harvest now": "Sohbetlerden şimdi not çıkar",
    "Harvest started in the background. New drafts appear in the inbox (+/).": "Sohbetlerden not çıkarma başladı. Yeni taslaklar Gelen kutusunda görünecek.",
    "Harvest settings": "Sohbet notu ayarları",
    "Filter by name, folder or summary": "Ada, klasöre ya da özete göre süz",
    "Show": "Göster",
    "All notes": "Tüm notlar",
    "Not indexed": "İndekslenmeyenler",
    "Sort": "Sırala",
    "Sort by date or name": "Tarihe ya da ada göre sırala",
    "%d notes": "%d not",
    "Draft": "Taslak",
    "Not indexed: excluded from search": "Aramaya dahil değil",
    "Select a note to read it here.": "Okumak için bir not seçin.",
    "Links between notes open in place. Edit in Obsidian or your editor; Carry refreshes its index when files change.": "Notlar arasındaki bağlantılar burada açılır. Düzenlemeyi Obsidian'da ya da editörünüzde yapın; dosyalar değişince Carry indeksini yeniler.",
    "draft": "taslak",
    "locked": "kilitli",
    "not indexed": "aramaya dahil değil",
    "indexed": "aranabilir",
    "modified ": "değişti: ",
    "Open in Obsidian": "Obsidian'da aç",
    "Open in editor": "Editörde aç",
    "Show in Finder": "Finder'da göster",
    "Copy path": "Yolu kopyala",
    "Properties (%d)": "Özellikler (%d)",
    "Linked from (%d)": "Bu nota bağlananlar (%d)",
    "Links to (%d)": "Verdiği bağlantılar (%d)",
    "Settings": "Ayarlar",
    "How Carry finds passages for Claude Code and Codex. Now: %@.": "Carry'nin Claude Code ve Codex için pasajları nasıl bulduğu. Şu an: %@.",
    "Search mode": "Arama modu",
    "Semantic search · Jev judges (best measured; Ollama + TypeSafe key)": "Anlamsal arama · Jev değerlendirir (ölçümde en iyi; Ollama + TypeSafe anahtarı)",
    "Semantic search · your assistant checks (Ollama)": "Anlamsal arama · asistanınız kontrol eder (Ollama)",
    "Keyword search · Jev judges (no model on this Mac; TypeSafe key)": "Anahtar kelime araması · Jev değerlendirir (bu Mac'te model yok; TypeSafe anahtarı)",
    "Keyword search · your assistant checks (no download)": "Anahtar kelime araması · asistanınız kontrol eder (indirme yok)",
    "Accurate multilingual · local relevance model, 16 GB+ Macs": "Hassas çok dilli · yerel alaka değerlendirme modeli, 16 GB+ Mac'ler",
    "Embedding only · Qwen3 Embedding 0.6B": "Yalnız embedding · Qwen3 Embedding 0.6B",
    "Embedding only · Nomic Embed Text (legacy)": "Yalnız embedding · Nomic Embed Text (eski)",
    "Apply search mode": "Arama modunu uygula",
    "Switching search mode. Semantic modes download a model and rebuild the index; you can keep using Carry.": "Arama türü değiştiriliyor. Akıllı arama bir model indirip notlarınızı yeniden tarar; bu sırada Carry'yi kullanabilirsiniz.",
    "Semantic search uses about 0.7 GB while loaded and unloads when idle. Jev sends the question and up to 32 masked passages to TypeSafe.": "Anlamsal arama yüklüyken yaklaşık 0,7 GB kullanır, boştayken bellekten çıkar. Jev soruyu ve en fazla 32 maskelenmiş pasajı TypeSafe'e gönderir.",
    "Results handed to your assistant": "Asistanınıza verilen sonuçlar",
    "Passages per search: %d": "Arama başına metin parçası: %d",
    "Character budget: %d": "Karakter bütçesi: %d",
    "Passages from one note: %d": "Bir nottan en fazla parça: %d",
    "Jev relevance threshold": "Jev alaka eşiği",
    "Higher returns fewer, surer passages. 0.50 is the measured default.": "Yüksek değer daha az ama soruyla daha ilgili metin parçaları döndürür. Ölçülmüş varsayılan 0,50.",
    "Keeping the index fresh": "Aramayı güncel tutma",
    "Re-index automatically when notes change": "Notlar değişince aramayı kendiliğinden güncelle",
    "Check for changes every %d s": "Değişiklikleri %d sn'de bir kontrol et",
    "Sync GitHub sources every %d min": "GitHub klasörlerini %d dk'da bir eşitle",
    "Save changes": "Değişiklikleri kaydet",
    "Search settings saved. They apply to the next search.": "Arama ayarları kaydedildi. Bir sonraki aramada geçerli olur.",
    "Revert": "Geri al",
    "Carry reads finished Claude Code and Codex chats of your vault and drafts what deserves a note: decisions, facts, preferences and open items. Drafts land in the inbox (+/) for you to review; nothing is filed by itself.": "Carry, vault'unuzdaki biten Claude Code ve Codex sohbetlerini okur ve not olmayı hak edenleri taslak olarak çıkarır: kararlar, bilgiler, tercihler ve açık işler. Taslaklar incelemeniz için gelen kutusuna (+/) düşer; hiçbir şey kendiliğinden dosyalanmaz.",
    "Every evening": "Her akşam",
    "Harvest finished chats every evening": "Günün sohbetlerinden her akşam not çıkar",
    "Time": "Saat",
    "Language of drafts": "Taslakların dili",
    "Save schedule": "Kaydet",
    "Nightly harvest scheduled.": "Gecelik çalışma ayarlandı.",
    "Nightly harvest turned off.": "Gecelik çalışma kapatıldı.",
    "Runs through launchd (%@) while you are logged in.": "Mac'inizde oturumunuz açıkken çalışır.",
    "When a chat ends": "Bir sohbet bittiğinde",
    "Claude Code and Codex call Carry when a chat in the vault ends, and the next chat starts with a short state pack of open items and decisions. These hooks are set up by `carry setup` or on the Clients tab; Codex asks you to approve each hook once.": "Not klasörünüzde bir sohbet bittiğinde Claude Code ve Codex Carry'ye haber verir; bir sonraki sohbet, açık işler ve kararlardan oluşan kısa bir özetle başlar. Bu otomatik kurulumla gelir (`carry setup`); Codex bunu ilk seferde bir kez onaylamanızı ister.",
    "Recent runs": "Son çalışmalar",
    "No runs logged yet.": "Henüz kayıtlı çalışma yok.",
    "Open full log": "Kaydın tamamını aç",
    "Show inbox": "Gelen kutusunu göster",
    "General": "Genel",
    "Language, workspace, index maintenance and diagnostics.": "Dil, çalışma alanı, indeks bakımı ve tanılama.",
    "Language": "Dil",
    "App language": "Uygulama dili",
    "Changes the app’s interface. Harvest drafts use the language set on the Harvest tab.": "Uygulamanın arayüzünü değiştirir. Harvest taslakları Harvest sekmesindeki dili kullanır.",
    "Workspace": "Çalışma alanı",
    "Holds Carry’s settings and a disposable index. Your notes are never stored here.": "Carry'nin ayarlarını ve silinebilir indeksini tutar. Notlarınız asla burada saklanmaz.",
    "Open another workspace": "Başka bir çalışma alanı aç",
    "Create new workspace": "Yeni çalışma alanı oluştur",
    "Index and connection health": "Arama ve bağlantı durumu",
    "Retrieval provider": "Arama altyapısı",
    "Local MCP test": "Yerel MCP testi",
    "Rebuild index": "İndeksi yeniden oluştur",
    "Rebuilding the index in the background.": "Arama dizini arka planda yeniden oluşturuluyor.",
    "Test local MCP": "Yerel MCP'yi test et",
    "Probe provider": "Sağlayıcıyı yokla",
    "The local test checks Carry’s MCP server only. Client trust and capture are verified by the clients themselves.": "Bu test yalnız Carry'nin kendi bağlantı sunucusunu (MCP) kontrol eder. Asistanın onayı asistanın kendisinde yapılır.",
    "Diagnostics": "Tanılama",
    "Sources": "Kaynaklar",
    "The Markdown folders and GitHub repositories Carry searches. Removing a source never deletes its files.": "Carry'nin aradığı Markdown klasörleri ve GitHub repoları. Bir kaynağı kaldırmak dosyalarını asla silmez.",
    "Add a folder": "Klasör ekle",
    "Source ID (letters, numbers, hyphen)": "Kaynak kimliği (harf, rakam, tire)",
    "Allow Carry to write records in this folder": "Carry bu klasöre kayıt yazabilsin",
    "Choose folder and add": "Klasör seç ve ekle",
    "Choose an existing Markdown folder or create a Carry records folder": "Mevcut bir Markdown klasörü seçin ya da bir Carry kayıt klasörü oluşturun",
    "Source added. Indexing in the background.": "Kaynak eklendi. Arka planda indeksleniyor.",
    "Relevance judge · TypeSafe Jev (optional)": "TypeSafe Jev alaka kontrolü (isteğe bağlı)",
    "Jev checks, in about half a second per search, which passages actually answer the question, and drops passages that try to instruct an AI. The question and up to 32 candidate passages (secrets masked, best effort) are sent to TypeSafe in the US; TypeSafe says it does not train on them. Without a key, your assistant's small model does this job instead.": "Jev, her aramada yaklaşık yarım saniyede hangi metin parçalarının soruyu gerçekten yanıtladığını kontrol eder ve yapay zekâya talimat vermeye çalışan parçaları eler. Soru ve en fazla 32 parça (gizli bilgiler elden geldiğince maskelenir) ABD'deki TypeSafe'e gönderilir; TypeSafe bunlarla model eğitmediğini belirtiyor. Anahtar yoksa bu işi asistanınız kendisi yapar.",
    "Key status unknown": "Anahtar durumu bilinmiyor",
    "Key saved in your Keychain": "Anahtar Anahtar Zinciri'nde kayıtlı",
    "No key saved": "Kayıtlı anahtar yok",
    "Check": "Kontrol et",
    "TypeSafe API key (console.typesafe.ai › API Keys)": "TypeSafe API anahtarı (console.typesafe.ai › API Keys)",
    "Save key": "Anahtarı kaydet",
    "Key saved in your Keychain. Choose a Jev search mode above to use it.": "Anahtar Anahtar Zinciri'ne kaydedildi. Kullanmak için yukarıdan “En iyi” aramayı seçin.",
    "The key is stored in the macOS Keychain, never in Carry's workspace files.": "Anahtar macOS Anahtar Zinciri'nde saklanır, Carry'nin dosyalarında değil.",
    "Connect a GitHub knowledge base": "GitHub'daki bir bilgi tabanını bağla",
    "Choose a repository you can access. Carry keeps a read-only copy and checks for updates every five minutes while it is running.": "Erişiminiz olan bir repo seçin. Carry salt okunur bir kopya tutar ve çalışırken beş dakikada bir güncellemeleri kontrol eder.",
    "Sign in to GitHub": "GitHub'a giriş yap",
    "Load my repositories": "Repolarımı yükle",
    "Enter this code: ": "Bu kodu girin: ",
    "Copy code and open GitHub": "Kodu kopyala ve GitHub'ı aç",
    "Cancel sign-in": "Girişi iptal et",
    "Sign-in did not finish. Try again.": "Giriş tamamlanmadı. Tekrar deneyin.",
    "Repository": "Repo",
    "Select a repository": "Bir repo seçin",
    "Load more repositories": "Daha fazla repo yükle",
    "Repository URL or owner/repository": "Repo adresi ya da sahip/repo",
    "Source name": "Kısa ad",
    "Branch (default if blank)": "Dal (boşsa varsayılan)",
    "Knowledge folder (whole repo if blank)": "Bilgi klasörü (boşsa tüm repo)",
    "Connect and index": "Bağla ve tara",
    "Connecting your repository in the background.": "Reponuz arka planda bağlanıyor.",
    "GitHub remains the source of truth. Carry does not push changes to the repository.": "Doğruluk kaynağı GitHub olarak kalır. Carry repoya değişiklik göndermez.",
    "Create a personal vault": "Yeni not klasörü oluştur",
    "Start a personal knowledge vault in an empty folder: the operating guide for your assistant, templates, folders and a Git repository. Carry adds it as a source and connects Claude Code and Codex inside that folder. To use notes you already have, add their folder as a source instead.": "Boş bir klasörde yeni bir not klasörü başlatır: asistanınız için kılavuz, şablonlar ve düzenli alt klasörler. Carry onu ekler ve Claude Code ile Codex'i orada bağlar. Mevcut notlarınız için bunun yerine “Not klasörü ekle”yi kullanın.",
    "Language of your notes": "Notlarınızın dili",
    "Record my prompts in this vault (sources/carry) for later review": "Yazdıklarımı daha sonra incelemek için bu klasöre kaydet (sources/carry)",
    "Only your own prompts are recorded, never replies or tool output. Masking is best effort. Leave this off if you only want search.": "Yalnız sizin istemleriniz kaydedilir; yanıtlar ve araç çıktıları asla. Maskeleme elden geldiğince yapılır. Yalnız arama istiyorsanız kapalı bırakın.",
    "Choose an empty folder and preview": "Boş bir klasör seç ve önizle",
    "Choose or create an empty folder for your vault": "Notlarınız için boş bir klasör seçin ya da oluşturun",
    "%d files will be created. Nothing is written until you confirm.": "%d dosya oluşturulacak. Onaylayana kadar hiçbir şey yazılmaz.",
    "Create vault": "Not klasörünü oluştur",
    "Vault created at %@. Open this folder in Claude Code or Codex, approve the Carry server, then open it in Obsidian if you use it. Indexing runs in the background.": "Not klasörü oluşturuldu: %@. Bu klasörü Claude Code ya da Codex'te açın ve ilk seferde Carry'yi onaylayın.",
    "Cancel": "İptal",
    "read + write in carry/": "okuma + carry/ içine yazma",
    "read only": "salt okunur",
    "folder missing": "klasör yok",
    "Browse": "Göz at",
    "ago": "önce",
    "Sync now": "Şimdi eşitle",
    "Carry may write records (in a carry/ folder)": "Carry kayıt yazabilsin (carry/ klasörüne)",
    "Passages may be sent to the Jev judge": "Pasajlar Jev değerlendiricisine gönderilebilir",
    "Off keeps this source’s passages on this Mac; they are returned unjudged.": "Kapalıysa bu kaynağın pasajları bu Mac'te kalır ve değerlendirilmeden döner.",
    "Excluded paths or patterns, separated by commas": "Aramaya dahil edilmeyecek klasör ya da dosyalar, virgülle ayırın",
    "Excluded files stay visible in Vault but are never searched. Examples: +, x, workbench, *_index.md": "Hariç tutulan dosyalar Vault'ta görünür ama asla aranmaz. Örnekler: +, x, workbench, *_index.md",
    "Save": "Kaydet",
    "Source saved. Re-indexing in the background.": "Kaydedildi. Carry notları arka planda yeniden tarıyor.",
    "Show indexed files": "İndekslenen dosyaları göster",
    "Remove source…": "Kaynağı kaldır…",
    "%d files indexed": "%d dosya aranabilir",
    "Remove “%@” from Carry?": "Bu klasör Carry'den kaldırılsın mı?",
    "Remove source": "Klasörü kaldır",
    "Source removed. Its files were not touched.": "Klasör kaldırıldı. Dosyalarına dokunulmadı.",
    "Carry stops searching this source. Its folder and files stay exactly as they are.": "Carry bu klasörde aramayı bırakır. Klasör ve dosyalar olduğu gibi kalır.",
    "Search your knowledge": "Notlarınızda arayın",
    "Try a real question and inspect the passages your assistant will receive.": "Gerçek bir soru deneyin ve asistanınızın alacağı pasajları inceleyin.",
    "Ask a question in Turkish or English": "Türkçe ya da İngilizce bir soru yazın",
    "judged by %@": "değerlendiren: %@",
    "%@ candidates rejected": "%@ aday elendi",
    "No sufficiently relevant evidence found. Try a more specific question or check the included files.": "Bu soruyla yeterince ilgili bir not bulunamadı. Daha belirli bir soru deneyin.",
    "The index is not ready yet. Check the background task status.": "Arama henüz hazır değil; Carry notlarınızı tarıyor. Biraz sonra tekrar deneyin.",
    "Open this version on GitHub": "Bu sürümü GitHub'da aç",
    "Read in Vault": "Notlarda oku",
    "These are source passages. Check that they answer your question before relying on them.": "Bunlar notlarınızdan alınan parçalar. Güvenmeden önce sorunuzu gerçekten yanıtladıklarını kontrol edin.",
    "Clients": "İstemciler",
    "Connect Claude Code and Codex projects to Carry. Review exact project settings before applying; the client still controls its own trust approvals.": "Claude Code ve Codex projelerini Carry'ye bağlayın. Uygulamadan önce proje ayarlarını tam olarak inceleyin; güven onayları yine istemcinin elindedir.",
    "Project connection": "Proje bağlantısı",
    "Client": "Asistan",
    "No project selected": "Proje seçilmedi",
    "Choose project": "Proje seç",
    "Select the client’s project folder": "Asistanın açılacağı proje klasörünü seçin",
    "Client executable (optional; auto-detect by default)": "Asistan programının yolu (isteğe bağlı; normalde kendiliğinden bulunur)",
    "Write destination": "Kaydedilecek yer",
    "Choose a writable source": "Carry'nin yazabildiği bir klasör seçin",
    "Capture whole user prompts in this project": "Bu projede yazdıklarımın tamamını kaydet",
    "Only user prompts are captured. Replies and tool output are excluded. Masking is best effort. Each event becomes an unaccepted draft.": "Yalnız kullanıcı istemleri kaydedilir. Yanıtlar ve araç çıktıları hariçtir. Maskeleme elden geldiğince yapılır. Her olay kabul edilmemiş bir taslak olur.",
    "Allow this client to propose draft decisions": "Bu asistan karar taslağı önerebilsin",
    "Unchecked options leave existing opt-ins unchanged. Pausing configured capture also pauses this client’s proposals.": "İşaretlenmeyen seçenekler mevcut izinleri değiştirmez. Kaydı duraklatmak bu asistanın önerilerini de duraklatır.",
    "These files are project-local (.mcp.json / .claude or .codex). Their paths may appear in version control; inspect them before sharing your project.": "Bu dosyalar yalnız bu projeye aittir (.mcp.json / .claude ya da .codex). Projeyi paylaşmadan önce bunları kontrol edin.",
    "Preview connection changes": "Yapılacak değişiklikleri göster",
    "Exact changes · ": "Tam değişiklikler · ",
    "No changes": "Değişiklik yok",
    "Apply these changes": "Bu değişiklikleri uygula",
    "Capture capability": "Kayıt yeteneği",
    "Persisted capture": "Kalıcı kayıt",
    "Last successful event: ": "Son başarılı olay: ",
    "Open raw event": "Ham olayı aç",
    "Resume capture": "Kaydı sürdür",
    "Pause capture": "Kaydı duraklat",
    "Native test: restart the selected client, complete its project and hook approvals, then submit ‘Carry connection test — synthetic Cedar note.’ Refresh here. Only a persisted receipt counts as capture success.": "Test için: asistanı yeniden başlatın, istediği onayları verin ve ‘Carry connection test — synthetic Cedar note.’ yazıp gönderin. Sonra burada yenileyin.",
    "Roll back setup": "Kurulumu geri al",
    "Setup rolled back. Restart the client. Existing records are retained.": "Bağlantı geri alındı. Asistanı yeniden başlatın. Mevcut kayıtlar korunur.",
    "Activity": "Etkinlik",
    "Pending review only": "Yalnız inceleme bekleyenler",
    "Inspect captured event": "Kaydedilen olayı incele",
    "No decisions here yet. Connect a client to capture a draft.": "Burada henüz karar yok. Taslak kaydetmek için bir istemci bağlayın.",
    "Review": "İnceleme",
    "Review a decision": "Bir kararı inceleyin",
    "Select an event to inspect its source and proposed changes. Accepting commits only the exact revision you reviewed.": "Kaynağını ve önerilen değişiklikleri incelemek için bir olay seçin. Kabul etmek yalnız incelediğiniz revizyonu işler.",
    "Conflict: ": "Çakışma: ",
    "Corrects ": "Düzeltiyor: ",
    "Change from current decision": "Mevcut hâline göre değişiklik",
    "Open decision Markdown": "Öneri dosyasını aç",
    "Open source: ": "Kaynağı aç: ",
    "History stays on disk. If the draft or its target changes, acceptance is refused; reload the decision before reviewing again.": "Eski hâl diskte saklanır. Öneri ya da düzelttiği not bu arada değişirse kabul reddedilir; öneriyi yeniden açıp inceleyin.",
    "Accept this revision": "Bu öneriyi kabul et",
    "Reject draft": "Öneriyi reddet",
    "Revision ": "Revizyon ",
    "PRIVATE ALPHA · LOCAL": "ÖZEL ALFA · YEREL",
    "The background task was interrupted. Retry from Settings.": "Arka plandaki iş yarıda kaldı. Ayarlar'dan tekrar deneyin.",
    "Working locally…": "Çalışıyor…",
    "No workspace selected": "Kurulum yapılmadı",
    "Refresh": "Yenile",
    "%@ of %@ files": "%@ / %@ dosya",
    "%@ changed": "%@ değişti",
    "Not tested": "Test edilmedi",
    "passed": "geçti",
    "failed": "başarısız",
    "GitHub connected": "GitHub bağlı",
    "Fresh": "Hazır",
    "Stale": "Güncelleniyor",
    "Unavailable": "Hazır değil",
    "Preparing": "Hazırlanıyor",
    "Queued": "Sırada",
    "Checking Github": "GitHub kontrol ediliyor",
    "Downloading Github": "GitHub indiriliyor",
    "Downloading Model": "Model indiriliyor",
    "Downloading Reranker": "Sıralama modeli indiriliyor",
    "Indexing": "Notlar taranıyor",
    "Model Ready": "Model hazır",
    "Finished": "Bitti",
    "Failed": "Başarısız",
    "Harvest finished. New drafts, if any, are in the inbox (+/).": "Bitti. Yeni taslak çıktıysa Gelen kutusunda.",
    "Harvest stopped with an error. See the log on the Harvest tab.": "Bir hata oluştu. Ayrıntı için Sohbet notları sekmesindeki kayda bakın.",
    "The open note was moved or deleted outside Carry, so it was closed.": "Açık not Carry dışında taşındığı ya da silindiği için kapatıldı.",
    "A harvest is already running.": "Zaten çalışıyor.",
    "Show all changes": "Tüm değişiklikleri göster",
    "The nightly harvest on this Mac belongs to another Carry workspace.": "Bu Mac'teki gecelik çalışma başka bir Carry kurulumuna ait.",
    "launchd has not loaded this job. Save the schedule again on the Harvest tab.": "Gecelik çalışma macOS tarafından başlatılmamış. Sohbet notları sekmesinden yeniden kaydedin.",
    "Harvest running…": "Sohbetlerden not çıkarılıyor…",
    "Waiting to be indexed": "Değişti; arama birazdan güncellenecek",
    "Showing %d of %d notes. Narrow the filter to see the rest.": "%d / %d not gösteriliyor. Kalanlar için aramayı daraltın.",
    "This vault has more files than Carry lists (20,000).": "Bu klasörde Carry'nin gösterebildiğinden (20.000) fazla dosya var.",
    "Back": "Geri",
    "Forward": "İleri",
    "Close note": "Notu kapat",
    "waiting to be indexed": "arama birazdan güncellenecek",
    "Unsaved changes": "Kaydedilmemiş değişiklikler",
    "Discard": "Vazgeç",
    "Draft language saved. It applies to every harvest: nightly, manual and at chat end.": "Taslak dili kaydedildi. Tüm sohbet notlarında geçerli.",
    "Used by every harvest: nightly, manual and at chat end.": "Gecelik, elle başlatılan ve sohbet sonu tüm sohbet notlarında kullanılır.",
    "The nightly harvest on this Mac belongs to another Carry workspace (%@). Saving here replaces it.": "Bu Mac'teki gecelik çalışma başka bir Carry kurulumuna ait (%@). Burada kaydederseniz onun yerini alır.",
    "Replace the other workspace’s nightly harvest?": "Diğer kurulumun gecelik çalışması değiştirilsin mi?",
    "Replace": "Değiştir",
    "There is one nightly harvest per Mac user. The other workspace stops harvesting at night; its chat-end hooks keep working.": "Her Mac kullanıcısı için tek bir gecelik çalışma olabilir. Diğer kurulum geceleri not çıkarmayı bırakır; sohbet sonu notları çalışmaya devam eder.",
    "Off: this source’s passages are never sent to Jev. Search results can still reach your connected assistant and the model it uses.": "Kapalıysa bu kaynağın pasajları Jev'e hiç gönderilmez. Arama sonuçları yine de bağlı asistanınıza ve onun kullandığı modele iletilebilir.",
    "Relevance %@": "Alaka puanı %@",
    "No decision proposals waiting for approval.": "Onay bekleyen karar önerisi yok.",
    "Proposals come from connected clients (carry_propose). Harvest drafts and draft notes live in the vault instead.": "Öneriler bağlı asistanlardan gelir. Sohbet notları ve taslaklar ise Notlar sayfasında durur.",
    "Open vault drafts": "Taslakları aç",
    "Open inbox": "Gelen kutusunu aç",
    "Connect a client": "Asistan bağla",
    "meta.thing": "Kavram",
    "meta.person": "Kişi",
    "meta.statement": "Önerme",
    "meta.effort": "Çalışma",
    "meta.wiki-article": "Wiki makalesi",
    "meta.topic-index": "Konu dizini",
    "meta.source": "Kaynak",
    "meta.chat-raw": "Ham sohbet",
    "meta.daily": "Günlük",
    "meta.output": "Çıktı",
    "meta.memory": "Hafıza",
    "meta.stub": "Kısa not",
    "meta.on": "Aktif",
    "meta.ongoing": "Devam ediyor",
    "meta.simmering": "Demleniyor",
    "meta.sleeping": "Uykuda",
    "meta.public": "Herkese açık",
    "meta.personal": "Kişisel",
    "meta.private": "Özel",
    "meta.secret": "Gizli",
    "Custom configuration": "Özel ayar",
]

// Plainer English wording for keys whose original text uses internal terms.
let EN: [String: String] = [
    "Core stopped. Refresh to reconnect; check the result before repeating a write.": "Carry's background service stopped. Press Refresh to reconnect; if you were saving something, check it before trying again.",
    "Semantic search · Jev judges": "Best (smart search + Jev)",
    "Semantic search · your assistant checks": "Smart",
    "Keyword search · Jev judges": "Simple + Jev",
    "Keyword search · your assistant checks": "Simple",
    "Show in Vault": "Show in Notes",
    "Read in Vault": "Read in Notes",
    "Index": "Search",
    "Nightly harvest": "Nightly chat notes",
    "Harvest started in the background. New drafts appear in the inbox (+/).": "Started. New drafts will appear in your Inbox.",
    "Harvest settings": "Chat notes settings",
    "Not indexed: excluded from search": "Left out of search",
    "not indexed": "left out of search",
    "indexed": "searchable",
    "waiting to be indexed": "search updates in a moment",
    "Waiting to be indexed": "Changed; search updates in a moment",
    "Harvest finished chats every evening": "Take notes from the day's chats every evening",
    "Run harvest now": "Take notes from chats now",
    "Harvest running…": "Taking notes from chats…",
    "Harvest finished. New drafts, if any, are in the inbox (+/).": "Done. New drafts, if any, are in your Inbox.",
    "Harvest stopped with an error. See the log on the Harvest tab.": "Something went wrong. See the log on the Chat notes tab.",
    "Runs every evening at %@ · drafts in %@. Chats also harvest when they end.": "Runs every evening at %@ · drafts in %@. Notes are also taken when a chat ends.",
    "Not scheduled. Chats still harvest when they end if the client hooks are set up.": "The nightly run is off. Notes are still taken when a chat ends.",
    "Runs through launchd (%@) while you are logged in.": "Runs while you are logged in to your Mac.",
    "Create a personal vault": "Create a new notes folder",
    "Create vault": "Create notes folder",
    "Choose or create an empty folder for your vault": "Choose or create an empty folder for your notes",
    "Proposals come from connected clients (carry_propose). Harvest drafts and draft notes live in the vault instead.": "Proposals come from connected assistants. Chat notes and drafts are on the Notes page instead.",
    "Open vault drafts": "Open drafts",
    "Connect a client": "Connect an assistant",
    "The index is not ready yet. Check the background task status.": "Search is not ready yet; Carry is reading your notes. Try again in a moment.",
    "Fresh": "Ready",
    "Stale": "Updating",
    "Unavailable": "Not ready",
    "Indexing": "Reading notes",
    "Client": "Assistant",
    "Save schedule": "Save",
]
