// Native view/bridge smoke runner. Built only with -D CARRY_VIEW_TESTS, inside a
// separate test bundle. Renders actual SwiftUI views; does not emulate UI clicks.
import SwiftUI
import AppKit

@MainActor func waitUntil(_ predicate: () -> Bool) throws {
    let deadline = Date().addingTimeInterval(30)
    while !predicate() && Date() < deadline {
        RunLoop.main.run(until: Date().addingTimeInterval(0.01))
    }
    if !predicate() { throw UIError("Native test timed out") }
}

@MainActor func render<V: View>(_ view: V, to url: URL) throws {
    let host = NSHostingView(rootView: view.preferredColorScheme(.light).background(Color.white))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 800),
                          styleMask: [.titled], backing: .buffered, defer: false)
    window.contentView = host
    window.appearance = NSAppearance(named: .aqua)
    host.frame = NSRect(x: 0, y: 0, width: 1100, height: 800)
    window.layoutIfNeeded()
    RunLoop.main.run(until: Date().addingTimeInterval(0.15))
    host.layoutSubtreeIfNeeded()
    guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { throw UIError("No view bitmap") }
    host.cacheDisplay(in: host.bounds, to: bitmap)
    guard let data = bitmap.representation(using: .png, properties: [:]) else { throw UIError("No PNG representation") }
    try data.write(to: url)
    // NSWindow may self-release on close; let this test's local ownership end.
    window.isReleasedWhenClosed = false
    window.close()
}

@main struct NativeTests {
    @MainActor static func main() throws {
        let application = NSApplication.shared
        application.setActivationPolicy(.prohibited)
        application.appearance = NSAppearance(named: .aqua)
        guard let output = ProcessInfo.processInfo.environment["CARRY_VIEW_TEST_OUTPUT"],
              ProcessInfo.processInfo.environment["CARRY_DESKTOP_WORKSPACE"] != nil else { throw UIError("Explicit synthetic test paths required") }
        let destination = URL(fileURLWithPath: output)
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        let model = Model()
        model.refresh()
        try waitUntil { !model.busy }
        guard model.error.isEmpty, !model.snapshot.isEmpty else { throw UIError(model.error) }
        try render(Overview(model: model), to: destination.appendingPathComponent("overview.png"))
        try render(Sources(model: model), to: destination.appendingPathComponent("sources.png"))
        model.repositories = [["name": "example/knowledge-base", "branch": "main", "private": true]]
        try render(GitHubSourceForm(model: model), to: destination.appendingPathComponent("github.png"))
        try render(Search(model: model), to: destination.appendingPathComponent("search.png"))
        try render(Connections(model: model), to: destination.appendingPathComponent("connections.png"))
        guard let draft = model.activity.first(where: { str($0,"state") == "draft" }) else { throw UIError("Synthetic draft required") }
        model.inspect(draft)
        try waitUntil { !model.busy }
        guard str(model.review,"diff").contains("October 22") else { throw UIError("Correction diff missing") }
        try render(Activity(model: model), to: destination.appendingPathComponent("review.png"))
        model.finish("accept")
        try waitUntil { !model.busy }
        guard model.error.isEmpty, model.activity.contains(where: { str($0,"record_id") == str(draft,"record_id") && str($0,"state") == "accepted" }) else { throw UIError("Acceptance did not reach the app model") }
        try render(Activity(model: model), to: destination.appendingPathComponent("accepted.png"))
        var response: [String: Any] = [:]
        model.run("connection_test") { response = $0 }
        try waitUntil { !model.busy }
        guard str(response,"mcp_local_test") == "passed" else { throw UIError("Local MCP probe failed") }
        AppDelegate.worker.stop()
        // Next action must start a new core and recover canonical accepted state.
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        model.refresh()
        try waitUntil { !model.busy }
        guard model.error.isEmpty, !model.snapshot.isEmpty else { throw UIError("Worker did not recover") }
        let evidence: [String: Any] = ["native_view_rendering": true, "review_diff": true,
            "accept_via_app_model_and_worker": true, "worker_restart": true, "mcp_probe": true,
            "mouse_keyboard_ui_automation": false]
        try JSONSerialization.data(withJSONObject: evidence, options: [.prettyPrinted, .sortedKeys]).write(to: destination.appendingPathComponent("results.json"))
        AppDelegate.worker.stop()
        print("Native view/bridge tests passed")
    }
}
