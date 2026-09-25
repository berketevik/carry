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
                    let root = Bundle.main.resourceURL!.appendingPathComponent("python")
                    p.executableURL = root.appendingPathComponent("bin/python3")
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
                    guard let byte = try self.output!.read(upToCount: 1), !byte.isEmpty else { throw UIError("Core stopped. Refresh to reconnect; check the result before repeating a write.") }
                    if byte.first == 10 { break }
                    response.append(byte)
                    if response.count > 16_000_000 { throw UIError("Core response exceeded the app limit.") }
                }
                guard let object = try JSONSerialization.jsonObject(with: response) as? [String: Any] else { throw UIError("Invalid core response.") }
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
        case "github_cli_missing": return "This build is missing GitHub support. Use the complete Carry pilot package."
        case "github_access_failed": return "GitHub could not read this repository. Sign in and check that your account has access."
        case "github_unreachable", "github_download_failed": return "GitHub could not be reached. Check your connection and retry."
        case "invalid_github_repository": return "Enter a GitHub repository URL or owner/repository."
        case "github_no_markdown_in_folder": return "No Markdown files were found in that folder. Check the repository, branch and folder."
        case "model_download_failed", "model_download_incomplete": return "The model download did not finish. Check your connection and try again."
        case "reranker_install_failed": return "The relevance model could not be installed. Try again; the embedding model may already be ready."
        case "ollama_runtime_missing": return "This build is missing its model runtime. Use the complete Carry pilot package or install Ollama."
        case "ollama_start_failed": return "The local model service could not start. Open Ollama and retry."
        case "workspace_not_initialized": return "This folder has no Carry workspace. Create one or open an existing workspace folder."
        case "workspace_already_initialized": return "This folder already has a workspace. Use Open workspace."
        case "duplicate_source_id": return "That source ID is already in use. Choose a different ID."
        case "invalid_source_id": return "Use lowercase letters, numbers, hyphens or underscores for the source ID."
        case "nested_source_roots": return "This folder overlaps an existing source. Choose a separate folder."
        case "state_dir_inside_source_root": return "Keep Carry’s workspace folder outside your Markdown source folders."
        case "settings_changed_since_preview", "settings_changed_during_apply": return "Settings changed after the preview. Refresh and review a new preview. If setup was interrupted, use its rollback entry."
        case "settings_changed_since_install", "settings_changed_during_rollback": return "Settings changed after setup. Rollback stopped to preserve those edits. Review the changed files before disconnecting."
        case "existing_carry_connection_conflict", "existing_carry_hook_conflict": return "This project already has a different Carry connection. Review or roll back that setup before replacing it."
        case "preview_again_required": return "The preview expired when the core restarted or the workspace changed. Preview the connection again."
        case "proposal_changed_since_review", "target_content_changed", "target_revision_changed", "record_changed_during_review_commit": return "The draft or its source changed. Select the decision again and review the new diff before accepting."
        case "client_unavailable": return "The client CLI was not found. Install it or select its executable path."
        case "unsupported_version": return "Update the client: Claude Code 2.1.196 or newer, or Codex CLI 0.153.4 or newer, is required."
        case "hooks_disabled": return "Codex hooks are disabled. Enable hooks in Codex before opting in to capture."
        default: return message
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    static let worker = Worker()
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
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
    private var lastJobState = ""
    func tick() {
        guard !workspace.isEmpty, !busy else { return }
        if ["starting", "waiting_for_browser"].contains(str(githubLogin, "state")) {
            run("github_login_status", clearError: false) { value in
                self.githubLogin = value
                if str(value, "state") == "connected" { self.loadRepositories() }
            }
        } else {
            run("maintenance_status", clearError: false) { value in
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
    var activity: [[String: Any]] { (snapshot["activity"] as? [[String: Any]] ?? []).sorted { str($0,"updated") > str($1,"updated") } }
    var adapters: [[String: Any]] { (snapshot["capture"] as? [String: Any])?["adapters"] as? [[String: Any]] ?? [] }
    var connections: [[String: Any]] { snapshot["connections"] as? [[String: Any]] ?? [] }
    func run(_ action: String, _ fields: [String: Any] = [:], clearError: Bool = true, then: (([String: Any]) -> Void)? = nil) {
        guard !busy else { return }
        busy = true; if clearError { error = "" }
        var request = fields
        request["action"] = action; request["workspace"] = workspace
        AppDelegate.worker.call(request) { result in
            DispatchQueue.main.async {
                self.busy = false
                switch result {
                case .success(let value): then?(value)
                case .failure(let e): self.error = e.localizedDescription
                }
            }
        }
    }
    func refresh() {
        guard !workspace.isEmpty else { return }
        run("snapshot") {
            self.snapshot = $0
            if ProcessInfo.processInfo.environment["CARRY_MEASURE_STARTUP"] == "1" { print("carry_workspace_ready"); fflush(stdout) }
        }
    }
    func chooseWorkspace(create: Bool) {
        guard let path = folder(create ? "Choose or create a separate folder for Carry’s index and settings" : "Open a Carry workspace folder") else { return }
        workspace = path; snapshot = [:]; review = [:]; preview = [:]; localTest = "Not tested"
        let loaded: ([String: Any]) -> Void = { value in
            if ProcessInfo.processInfo.environment["CARRY_DESKTOP_WORKSPACE"] == nil { UserDefaults.standard.set(path, forKey: "workspace") }
            self.snapshot = value; self.refresh()
        }
        run(create ? "initialize" : "snapshot", then: loaded)
    }
    func openSource(_ sid: String, _ path: String) {
        run("source", ["source_id": sid, "path": path]) { value in
            if let path = value["path"] as? String, !NSWorkspace.shared.open(URL(fileURLWithPath: path)) { self.error = "No application could open this Markdown file. Choose a Markdown editor in Finder." }
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

func str(_ value: [String: Any], _ key: String, _ fallback: String = "—") -> String {
    guard let item = value[key], !(item is NSNull) else { return fallback }
    return String(describing: item)
}
func pretty(_ value: Any) -> String {
    guard JSONSerialization.isValidJSONObject(value), let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]), let text = String(data: data, encoding: .utf8) else { return "—" }
    return text
}
@MainActor func folder(_ title: String) -> String? {
    let panel = NSOpenPanel()
    panel.message = title; panel.canChooseDirectories = true; panel.canChooseFiles = false
    panel.canCreateDirectories = true; panel.allowsMultipleSelection = false
    return panel.runModal() == .OK ? panel.url?.path : nil
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

struct Overview: View {
    @ObservedObject var model: Model
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Heading(title: "Your context, carried.", subtitle: "Local Markdown. Decisions you can trace, review and correct.")
                if model.snapshot.isEmpty {
                    GroupBox("Start with a workspace") {
                        VStack(alignment: .leading, spacing: 14) {
                            Text("Choose a separate folder for Carry’s settings and disposable index. Add your Markdown sources on the Sources screen.")
                            Text("Notes stay on this Mac. Passages requested by Claude Code or Codex may reach the model you use there. Carry sends no analytics.").foregroundStyle(.secondary)
                            HStack { Button("Create workspace") { model.chooseWorkspace(create: true) }.buttonStyle(.borderedProminent)
                                Button("Open workspace") { model.chooseWorkspace(create: false) } }
                        }.padding(12)
                    }
                } else {
                    HStack(alignment: .top, spacing: 18) {
                        metric("Index", str(model.snapshot["index"] as? [String: Any] ?? [:], "state"), "A rebuild reads your sources.")
                        metric("Review", str(model.snapshot["review"] as? [String: Any] ?? [:], "pending", "0") + " pending", "Drafts never become facts by themselves.")
                        metric("Retrieval", str(model.snapshot["embedding"] as? [String: Any] ?? [:], "name"), "Hashing is lexical, not semantic.")
                    }
                    GroupBox("Connection health") {
                        VStack(alignment: .leading, spacing: 12) {
                            LabeledContent("Local MCP test", value: model.localTest)
                            Text("A local test checks Carry’s MCP server. Native client trust and persisted capture are verified separately.").foregroundStyle(.secondary)
                            ForEach(model.adapters.indices, id: \.self) { i in
                                let adapter = model.adapters[i]
                                LabeledContent(str(adapter,"client").capitalized + " capture", value: str(adapter,"state"))
                            }
                            if model.adapters.isEmpty { Text("Capture is off. Connect a client and opt in to user prompts to enable it.") }
                        }.padding(12)
                    }
                    HStack {
                        Button("Rebuild index") { model.run("index") { model.notice = "Index: " + str($0,"status"); model.refresh() } }
                        Button("Test local MCP") { model.run("connection_test") { model.localTest = str($0,"mcp_local_test"); model.refresh() } }
                        Button("Probe provider") { model.run("snapshot", ["probe": true]) { model.snapshot = $0 } }
                    }
                    DisclosureGroup("Diagnostics") { Text(pretty(model.snapshot.filter { $0.key != "activity" })).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                }
            }.padding(30).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    func metric(_ title: String, _ value: String, _ detail: String) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                Text(title).foregroundStyle(.secondary)
                Text(value).font(.title2.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct Sources: View {
    @ObservedObject var model: Model
    @State private var sourceID = "notes"
    @State private var writable = false
    @State private var selectedModel = "accurate_multilingual"
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Heading(title: "Sources", subtitle: "Connect local Markdown folders or GitHub repositories. Choose what your assistant can search.")
                ForEach(model.sources.indices, id: \.self) { i in
                    let source = model.sources[i]
                    SourceCard(model: model, source: source)
                }
                GitHubSourceForm(model: model)
                GroupBox("Add a source") {
                    VStack(alignment: .leading, spacing: 12) {
                        TextField("Source ID (letters, numbers, hyphen)", text: $sourceID)
                        Toggle("Allow Carry to write records in this folder", isOn: $writable)
                        Button("Choose folder and add") {
                            if let root = folder("Choose an existing Markdown folder or create a Carry records folder") {
                                model.run("add_source", ["source_id": sourceID, "root": root, "writable": writable]) { _ in model.notice = "Source added. Indexing in the background."; model.refresh() }
                            }
                        }.buttonStyle(.borderedProminent)
                    }.padding(12)
                }
                GroupBox("Local search model") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Search works offline by keyword. Install a multilingual model for questions in Turkish and English; Carry downloads the model and rebuilds the index for you.")
                        Picker("Search model", selection: $selectedModel) {
                            Text("Accurate multilingual search · embedding + relevance model").tag("accurate_multilingual")
                            Text("EmbeddingGemma · multilingual").tag("embeddinggemma")
                            Text("Qwen3 Embedding 0.6B · multilingual").tag("qwen3-embedding:0.6b")
                            Text("Nomic Embed Text · legacy").tag("nomic-embed-text")
                        }
                        HStack {
                            Button("Download and enable model") { model.run("model_setup", ["model": selectedModel]) { model.job = $0; model.notice = "The model is downloading. You can keep using Carry." } }
                            Button("Use offline keyword search") { model.run("embedding", ["provider": "hashing"]) { model.snapshot = $0; model.notice = "Rebuilding for keyword search." } }
                        }
                        Text("Accurate multilingual search downloads roughly 3 GB of models. The smaller embedding-only options rank passages without the additional relevance check. Models run on this Mac.").font(.caption).foregroundStyle(.secondary)
                    }.padding(12)
                }
            }.padding(30)
        }.disabled(model.snapshot.isEmpty)
    }
}

struct GitHubSourceForm: View {
    @ObservedObject var model: Model
    @State private var repository = ""
    @State private var githubID = "team"
    @State private var branch = ""
    @State private var contentFolder = ""
    var body: some View {
                GroupBox("Connect a GitHub knowledge base") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Choose a repository you can access. Carry keeps a read-only copy and checks for updates every five minutes while it is running.")
                        HStack {
                            Button("Sign in to GitHub") { model.run("github_login") { model.githubLogin = $0 } }
                            Button("Load my repositories") { model.loadRepositories() }
                            Text(model.githubAccount).foregroundStyle(.secondary)
                        }
                        if let code = model.githubLogin["user_code"] as? String {
                            HStack {
                                Text("Enter this code: " + code).font(.title3.monospaced()).textSelection(.enabled)
                                Button("Copy code and open GitHub") {
                                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(code, forType: .string)
                                    NSWorkspace.shared.open(URL(string: "https://github.com/login/device")!)
                                }
                                Button("Cancel sign-in") { model.run("github_login_cancel") { model.githubLogin = $0 } }
                            }
                        }
                        if str(model.githubLogin, "state") == "failed" { Text("Sign-in did not finish. Try again.").foregroundStyle(.red) }
                        if !model.repositories.isEmpty {
                            Picker("Repository", selection: $repository) {
                                Text("Select a repository").tag("")
                                ForEach(model.repositories.indices, id: \.self) { i in
                                    Text(str(model.repositories[i], "name")).tag(str(model.repositories[i], "name"))
                                }
                            }
                            if let page = model.nextRepositoryPage { Button("Load more repositories") { model.loadRepositories(page: page) } }
                        }
                        TextField("Repository URL or owner/repository", text: $repository)
                        HStack {
                            TextField("Source name", text: $githubID)
                            TextField("Branch (default if blank)", text: $branch)
                            TextField("Knowledge folder (whole repo if blank)", text: $contentFolder)
                        }
                        Button("Connect and index") {
                            model.run("github_connect", ["source_id": githubID, "repository": repository, "branch": branch, "folder": contentFolder]) {
                                model.job = $0; model.notice = "Connecting your repository in the background."; model.refresh()
                            }
                        }.buttonStyle(.borderedProminent).disabled(repository.isEmpty || githubID.isEmpty)
                        Text("GitHub remains the source of truth. Carry does not push changes to the repository.").font(.caption).foregroundStyle(.secondary)
                    }.padding(12)
                }
    }
}

struct SourceCard: View {
    @ObservedObject var model: Model
    let source: [String: Any]
    @State private var exclusions = ""
    @State private var files: [String] = []
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack { Text(str(source,"source_id")).font(.headline); Spacer(); Text(source["writable"] as? Bool == true ? "Read + write in carry/" : "Read only").foregroundStyle(.secondary) }
                if let github = source["github"] as? [String: Any], let repo = github["repository"] as? String {
                    Text(repo + " · " + str(github, "branch"))
                    HStack {
                        Text("Commit " + String(str(github, "commit").prefix(8))).font(.caption.monospaced())
                        if let synced = github["synced_at"] as? Double { Text(Date(timeIntervalSince1970: synced), style: .relative).font(.caption); Text("ago").font(.caption) }
                        Button("Sync now") { model.run("github_sync", ["source_id": str(source, "source_id")]) { model.job = $0 } }
                    }
                } else { Text(str(source,"root")).textSelection(.enabled).font(.callout) }
                DisclosureGroup("Included files and exclusions") {
                    VStack(alignment: .leading, spacing: 8) {
                        TextField("Exclude paths or patterns, separated by commas", text: $exclusions)
                        Button("Save exclusions") {
                            model.run("source_exclusions", ["source_id": str(source,"source_id"), "exclude": exclusions.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }]) { model.job = $0; model.refresh() }
                        }
                        Button("Show included Markdown files") {
                            model.run("source_files", ["source_id": str(source,"source_id")]) { files = $0["files"] as? [String] ?? [] }
                        }
                        if !files.isEmpty {
                            Text("\(files.count) files included")
                            ScrollView { LazyVStack(alignment: .leading) { ForEach(files, id: \.self) { Text($0).font(.caption).textSelection(.enabled) } } }.frame(maxHeight: 160)
                        }
                    }.padding(.top, 8)
                }
            }.padding(10).onAppear { exclusions = (source["exclude"] as? [String] ?? []).joined(separator: ", ") }
        }
    }
}

struct Search: View {
    @ObservedObject var model: Model
    @State private var query = ""
    @State private var result: [String: Any] = [:]
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Heading(title: "Search your knowledge", subtitle: "Try a real question and inspect the passages your assistant will receive.")
                HStack {
                    TextField("Ask a question in Turkish or English", text: $query).textFieldStyle(.roundedBorder)
                    Button("Search") { model.run("recall", ["query": query]) { result = $0 } }.buttonStyle(.borderedProminent).disabled(query.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if str(result, "status") == "no_evidence" { Text("No sufficiently relevant evidence found. Try a more specific question or check the included files.") }
                if str(result, "status") == "unavailable" { Text("The index is not ready yet. Check the background task status.") }
                let hits = result["evidence"] as? [[String: Any]] ?? []
                ForEach(hits.indices, id: \.self) { i in
                    let hit = hits[i]
                    GroupBox(str(hit, "title")) {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(str(hit, "text")).textSelection(.enabled)
                            if let url = hit["url"] as? String, let link = URL(string: url) { Link("Open this version on GitHub", destination: link) }
                            else { Button("Open source") { model.openSource(str(hit,"source_id"), str(hit,"path")) } }
                        }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                if !hits.isEmpty { Text("These are source passages. Check that they answer your question before relying on them.").font(.caption).foregroundStyle(.secondary) }
            }.padding(30)
        }.disabled(model.snapshot.isEmpty)
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
                Heading(title: "Connect your clients", subtitle: "Review exact project settings before applying. The client still controls its own trust approvals.")
                GroupBox("Project connection") {
                    VStack(alignment: .leading, spacing: 12) {
                        Picker("Client", selection: $client) { Text("Claude Code").tag("claude"); Text("Codex").tag("codex") }.pickerStyle(.segmented)
                        HStack { Text(project.isEmpty ? "No project selected" : project).lineLimit(2).textSelection(.enabled); Spacer(); Button("Choose project") { if let p = folder("Select the client’s project folder") { project = p; model.preview = [:] } } }
                        TextField("Client executable (optional; auto-detect by default)", text: $executable)
                        Picker("Write destination", selection: $source) {
                            Text("Choose a writable source").tag("")
                            ForEach(model.sources.filter { $0["writable"] as? Bool == true }.indices, id: \.self) { i in
                                let sources = model.sources.filter { $0["writable"] as? Bool == true }
                                Text(str(sources[i],"source_id")).tag(str(sources[i],"source_id"))
                            }
                        }
                        Toggle("Capture whole user prompts in this project", isOn: $prompts)
                        Text("Only user prompts are captured. Replies and tool output are excluded. Masking is best effort. Each event becomes an unaccepted draft.").font(.caption).foregroundStyle(.secondary)
                        Toggle("Allow this client to propose draft decisions", isOn: $proposals)
                        Text("Unchecked options leave existing opt-ins unchanged. Pausing configured capture also pauses this client’s proposals.").font(.caption).foregroundStyle(.secondary)
                        Text("These files are project-local (.mcp.json / .claude or .codex). Their paths may appear in version control; inspect them before sharing your project.").font(.caption).foregroundStyle(.secondary)
                        Button("Preview connection changes") {
                            model.run("connection_preview", ["client": client, "project": project, "source_id": source, "prompts": prompts, "proposals": proposals, "executable": executable]) { model.preview = $0 }
                        }.buttonStyle(.borderedProminent).disabled(project.isEmpty || ((prompts || proposals) && source.isEmpty))
                    }.padding(12)
                }
                if !model.preview.isEmpty {
                    GroupBox("Exact changes · " + str(model.preview,"client")) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(str(model.preview,"summary", "No changes")).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                            Text(str(model.preview,"trust")).font(.callout)
                            Button("Apply these changes") { model.run("connection_apply", ["id": str(model.preview,"id")]) { model.notice = str($0,"trust"); model.preview = [:]; model.refresh() } }.buttonStyle(.borderedProminent)
                        }.padding(12)
                    }
                }
                ForEach(model.adapters.indices, id: \.self) { i in
                    let adapter = model.adapters[i]
                    GroupBox(str(adapter,"client").capitalized) {
                        VStack(alignment: .leading, spacing: 10) {
                            LabeledContent("Capture capability", value: str(adapter["capability"] as? [String: Any] ?? [:],"state"))
                            LabeledContent("Persisted capture", value: str(adapter,"state"))
                            if let receipt = adapter["receipt"] as? [String: Any] {
                                Text("Last successful event: " + str(receipt,"last_success_at")).font(.caption)
                                if let sid = receipt["source_id"] as? String, let path = receipt["event_path"] as? String { Button("Open raw event") { model.openSource(sid, path) } }
                            }
                            if let action = adapter["action"] as? String { Text(action).font(.caption).foregroundStyle(.secondary) }
                            Button(str(adapter,"state") == "paused" ? "Resume capture" : "Pause capture") {
                                model.run("pause", ["client": str(adapter,"client"), "paused": str(adapter,"state") != "paused"]) { _ in model.refresh() }
                            }
                        }.padding(12)
                    }
                }
                Text("Native test: restart the selected client, complete its project and hook approvals, then submit ‘Carry connection test — synthetic Cedar note.’ Refresh here. Only a persisted receipt counts as capture success.").font(.callout).foregroundStyle(.secondary)
                ForEach(model.connections.indices, id: \.self) { i in
                    let connection = model.connections[i]
                    HStack {
                        VStack(alignment: .leading) { Text(str(connection,"client") + " · " + str(connection,"state")); Text(str(connection,"project")).font(.caption).textSelection(.enabled) }
                        Spacer()
                        if str(connection,"state") != "rolled_back" {
                            Button("Roll back setup") { model.run("connection_rollback", ["id": str(connection,"id")]) { _ in model.notice = "Setup rolled back. Restart the client. Existing records are retained."; model.refresh() } }
                        }
                    }
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
                Text("Activity").font(.title.bold())
                Toggle("Pending review only", isOn: $pendingOnly)
                ForEach(model.adapters.indices, id: \.self) { i in
                    let adapter = model.adapters[i]
                    VStack(alignment: .leading, spacing: 4) {
                        Text(str(adapter,"client").capitalized + " · " + str(adapter,"state")).font(.caption.weight(.medium))
                        if let receipt = adapter["receipt"] as? [String: Any] {
                            Text(str(receipt,"at")).font(.caption2).foregroundStyle(.secondary)
                            if let sid = receipt["source_id"] as? String, let path = receipt["event_path"] as? String { Button("Inspect captured event") { model.openSource(sid, path) }.font(.caption) }
                        }
                        if let action = adapter["action"] as? String { Text(action).font(.caption2).foregroundStyle(.secondary) }
                    }
                }
                if entries.isEmpty { Text("No decisions here yet. Connect a client to capture a draft.").foregroundStyle(.secondary).padding(.top, 20) }
                List(entries.indices, id: \.self) { i in
                    let item = entries[i]
                    Button { model.inspect(item) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(str(item,"title", str(item,"path"))).lineLimit(2)
                            Text(str(item,"state").capitalized + " · rev " + str(item,"revision")).font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
                    }.buttonStyle(.plain).accessibilityLabel("Review " + str(item,"state") + " " + str(item,"record_id"))
                }.listStyle(.inset)
            }.padding(24).frame(minWidth: 240, idealWidth: 300, maxWidth: 360)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if model.review.isEmpty {
                        Heading(title: "Review a decision", subtitle: "Select an event to inspect its source and proposed changes. Accepting commits only the exact revision you reviewed.")
                    } else {
                        Heading(title: str(model.review,"state").capitalized + " decision", subtitle: str(model.review,"source_id") + " · revision " + str(model.review,"revision"))
                        Text(str(model.review,"record_id")).font(.caption.monospaced()).textSelection(.enabled)
                        if let conflict = model.review["conflict"] as? String { Text("Conflict: " + conflict).foregroundStyle(.red) }
                        if let target = model.review["target"] as? [String: Any] { Text("Corrects " + str(target,"source_id") + ":" + str(target,"path") + " · rev " + str(target,"revision")).font(.callout).textSelection(.enabled) }
                        Text(str(model.review,"content")).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                        GroupBox("Change from current decision") { Text(str(model.review,"diff")).font(.system(.callout, design: .monospaced)).textSelection(.enabled).padding(10).frame(maxWidth: .infinity, alignment: .leading) }
                        Button("Open decision Markdown") { model.openSource(str(model.review,"source_id"), str(model.review,"path")) }
                        let refs = model.review["sources"] as? [String] ?? []
                        ForEach(refs, id: \.self) { ref in
                            Button("Open source: " + ref) {
                                let parts = ref.split(separator: ":", maxSplits: 1).map(String.init)
                                if parts.count == 2 { model.openSource(parts[0], parts[1]) }
                            }
                        }
                        if str(model.review,"state") == "draft" {
                            Text("History stays on disk. If the draft or its target changes, acceptance is refused; reload the decision before reviewing again.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }.padding(28).frame(maxWidth: .infinity, alignment: .leading)
            }.safeAreaInset(edge: .bottom) {
                if str(model.review,"state") == "draft" {
                    HStack {
                        Button("Accept this revision") { model.finish("accept") }.buttonStyle(.borderedProminent).disabled(model.review["conflict"] is String)
                        Button("Reject draft") { model.finish("reject") }
                        Spacer()
                        Text("Revision " + str(model.review,"revision")).font(.caption).foregroundStyle(.secondary)
                    }.padding(16).background(Color(nsColor: .windowBackgroundColor))
                }
            }.frame(minWidth: 390)
        }
    }
}

struct ContentView: View {
    @StateObject private var model = Model()
    @State private var page = "Overview"
    let pages = [("Overview", "square.grid.2x2"), ("Sources", "folder"), ("Search", "magnifyingglass"), ("Connections", "point.3.connected.trianglepath.dotted"), ("Activity & review", "clock.arrow.circlepath")]
    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading) {
                Label("Carry", systemImage: "tray.full.fill").font(.title.bold()).padding(20)
                List(selection: $page) { ForEach(pages, id: \.0) { item in Label(item.0, systemImage: item.1).tag(item.0).padding(.vertical, 4) } }.listStyle(.sidebar)
                Text("PRIVATE ALPHA · LOCAL").font(.caption2.weight(.semibold)).foregroundStyle(.secondary).padding(20)
            }.navigationSplitViewColumnWidth(min: 205, ideal: 220)
        } detail: {
            VStack(spacing: 0) {
                if !model.error.isEmpty { banner(model.error, color: .red) }
                if !model.notice.isEmpty { banner(model.notice, color: .secondary) }
                if str(model.job, "state") == "running" {
                    HStack {
                        ProgressView().controlSize(.small)
                        Text(str(model.job, "stage", "Preparing").replacingOccurrences(of: "_", with: " ").capitalized)
                        if let done = model.job["completed"] as? Double, let total = model.job["total"] as? Double, total > 0 { Text("\(Int(done / total * 100))%") }
                        Spacer()
                    }.padding(12)
                }
                if ["failed", "interrupted"].contains(str(model.job, "state")) {
                    banner(UIError(str(model.job, "error", "The background task was interrupted. Retry from Sources.")).localizedDescription, color: .red)
                }
                Group {
                    switch page {
                    case "Sources": Sources(model: model)
                    case "Search": Search(model: model)
                    case "Connections": Connections(model: model)
                    case "Activity & review": Activity(model: model)
                    default: Overview(model: model)
                    }
                }.disabled(model.busy)
                Divider()
                HStack {
                    if model.busy { ProgressView().controlSize(.small); Text("Working locally…") }
                    else { Image(systemName: "internaldrive"); Text(model.workspace.isEmpty ? "No workspace selected" : model.workspace).lineLimit(1).truncationMode(.middle).textSelection(.enabled) }
                    Spacer()
                }.font(.caption).foregroundStyle(.secondary).padding(10)
            }.toolbar {
                Button("Open workspace", systemImage: "folder") { model.chooseWorkspace(create: false) }.disabled(model.busy)
                Button("Refresh", systemImage: "arrow.clockwise") { model.refresh() }.disabled(model.busy || model.workspace.isEmpty)
            }
        }.frame(minWidth: 980, minHeight: 660).onReceive(Timer.publish(every: 2, on: .main, in: .common).autoconnect()) { _ in model.tick() }.onAppear {
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
        WindowGroup("Carry") { ContentView() }.defaultSize(width: 1160, height: 800)
    }
}
#endif
