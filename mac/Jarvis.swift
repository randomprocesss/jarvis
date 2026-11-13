// Jarvis.app: a small native window around the local Jarvis web app (build with ./build.sh).
// This file: the server start, the web view and its bridge, the app delegate, the voice menu and main. The voice lives in
// VoiceLayer.swift (state, launch, page bridge), Listening.swift, Speaking.swift, Conversation.swift, Enrolment.swift,
// VoiceSupport.swift (helpers), Office.swift, SpeechText.swift and Ears.swift. The server is started if needed, never stopped.
import AppKit
import WebKit
import AVFoundation
import Speech
import Security

let appURL = URL(string: "http://localhost:\(port)/#/")!

/// The page key (HARNESS.md Phase 3, 5 Oct 2026): only this app's window may send, approve or write. A new random key every
/// launch, held in memory only (no Keychain: a self-signed rebuild made macOS ask for Keychain access each time).
/// The server learns it once, at start, from a 0600 file it deletes at once (never via environment: `ps -E` shows that), and
/// the page sends it as X-Jarvis-Key on every send/approve/write. At launch the app restarts a running server so that the
/// server holds this launch's key (workers run in their own sessions and are picked up again by the new server).
let pageKey: String = {
    var bytes = [UInt8](repeating: 0, count: 32)
    _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
    return bytes.map { String(format: "%02x", $0) }.joined()
}()

/// Hands the key to a server that is about to start: a 0600 file the server reads and deletes on boot.
func writeBootKey() {
    try? FileManager.default.createDirectory(atPath: "\(dataHome)/state", withIntermediateDirectories: true)
    let path = "\(dataHome)/state/page.key.boot"
    FileManager.default.createFile(atPath: path, contents: pageKey.data(using: .utf8), attributes: [.posixPermissions: 0o600])
}

// MARK: - Server

func serverListening() -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return false }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = port.bigEndian
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    let r = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
    }
    return r == 0
}

/// Starts the server detached (own session via nohup + setsid-like double fork through /bin/sh), output appended to server.log.
func startServerIfNeeded() {
    if serverListening() { return }
    guard !repoDir.isEmpty else { return }               // built without its code folder: nothing to start (the page says so)
    try? FileManager.default.createDirectory(atPath: "\(dataHome)/logs", withIntermediateDirectories: true)
    writeBootKey()
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/bin/sh")
    // The trailing & plus nohup detaches it from this app, so quitting Jarvis never stops the server.
    p.arguments = ["-c", "cd \(shq(repoDir)) && JARVIS_HOME=\(shq(dataHome)) nohup \(shq(serverPython)) -m jarvis.server >> \(shq(dataHome + "/logs/server.log")) 2>&1 < /dev/null &"]
    try? p.run()
    p.waitUntilExit()
}

/// At launch: a server left running from before holds an older page key, so it is stopped (SIGTERM, as tools/redeploy.sh
/// does) and started again with this launch's key. Workers live in their own sessions and are picked up by the new server.
func restartServerForKey() {
    if serverListening() {
        let pidPath = "\(dataHome)/state/server-\(port).pid"
        if let s = try? String(contentsOfFile: pidPath, encoding: .utf8), let pid = Int32(s.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 1 {
            kill(pid, SIGTERM)
            watchdogLog("launch: restarting server \(pid) so it holds this launch's page key")
        }
        for _ in 0..<40 where serverListening() { usleep(250_000) }
    }
    startServerIfNeeded()
}

/// The watchdog (every 15 s, one at a time): not listening -> start it; listening but /healthz unanswered 3 times in a row
/// (~45 s) -> the server is hung: dump its stacks (SIGUSR1 -> state/server.fault.log, so the hang can be diagnosed), kill it,
/// start a fresh one (HARNESS.md Phase 1, 5 Oct). A busy brain never trips it: /healthz never touches the brain.
let superviseQ = DispatchQueue(label: "jarvis.supervise")
var healthFails = 0

func watchdogLog(_ s: String) {
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(s)\n"
    let path = "\(dataHome)/state/watchdog.log"
    if let h = FileHandle(forWritingAtPath: path) { h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); h.closeFile() }
    else { try? line.write(toFile: path, atomically: true, encoding: .utf8) }
}

func serverHealthy() -> Bool {
    var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/healthz")!, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4)
    req.httpMethod = "GET"
    let sem = DispatchSemaphore(value: 0)
    var ok = false
    URLSession.shared.dataTask(with: req) { _, r, _ in ok = (r as? HTTPURLResponse)?.statusCode == 200; sem.signal() }.resume()
    _ = sem.wait(timeout: .now() + 5)
    return ok
}

func superviseServer() {
    if !serverListening() {
        healthFails = 0
        watchdogLog("server not listening: starting it")
        startServerIfNeeded()
        return
    }
    if serverHealthy() { healthFails = 0; return }
    healthFails += 1
    watchdogLog("healthz unanswered (\(healthFails)/3)")
    guard healthFails >= 3 else { return }
    healthFails = 0
    let pidPath = "\(dataHome)/state/server-\(port).pid"
    if let s = try? String(contentsOfFile: pidPath, encoding: .utf8), let pid = Int32(s.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 1 {
        kill(pid, SIGUSR1)
        usleep(800_000)
        kill(pid, SIGKILL)
        watchdogLog("server \(pid) hung: stacks dumped, killed")
    }
    for _ in 0..<20 where serverListening() { usleep(250_000) }
    startServerIfNeeded()
}

// MARK: - Window + web view

final class WebController: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    let web: WKWebView
    var retries = 0

    override init() {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        cfg.applicationNameForUserAgent = "Jarvis/1.0"
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        cfg.mediaTypesRequiringUserActionForPlayback = []   // 13 Nov: the landing's muted film starts by itself (local pages only)
        let ucc = WKUserContentController()
        cfg.userContentController = ucc
        web = WKWebView(frame: .zero, configuration: cfg)
        super.init()
        ucc.add(self, name: "jarvis")
        // A tiny bridge: window.jarvisApp.post({type, ...}); the voice layer adds window.jarvisVoice on top of it.
        let js = """
        window.jarvisApp = { native: true, key: "\(pageKey)", post: function (m) { window.webkit.messageHandlers.jarvis.postMessage(m); } };
        """
        ucc.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        web.underPageBackgroundColor = NSColor(srgbRed: 0x01/255.0, green: 0x14/255.0, blue: 0x1a/255.0, alpha: 1)
    }

    func load() { web.load(URLRequest(url: appURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)) }

    func retry() {
        retries += 1
        if retries > 20 { showError(); return }   // ~10 s at 0.5 s steps
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.load() }
    }

    func showError() {
        let html = """
        <html><body style="background:#01141a;color:#cfe9f2;font:15px -apple-system;display:grid;place-items:center;height:100vh;margin:0">
        <div style="text-align:center;opacity:.85">Jarvis could not reach its server on port \(port).<br><br>
        <span style="opacity:.6">Check logs/server.log in your Jarvis data folder, then press ⌘R.</span></div></body></html>
        """
        web.loadHTMLString(html, baseURL: nil)
    }

    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { retry() }
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) { retries = 0 }

    static func isLocal(_ u: URL) -> Bool {
        (u.host == "localhost" || u.host == "127.0.0.1") && (u.port == Int(port))
    }

    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let u = a.request.url else { return decisionHandler(.allow) }
        if WebController.isLocal(u) || u.scheme == "about" || u.scheme == "data" || u.scheme == "blob" { return decisionHandler(.allow) }
        if ["http", "https", "mailto", "slack"].contains(u.scheme ?? "") { NSWorkspace.shared.open(u) }
        decisionHandler(.cancel)
    }

    // target=_blank / window.open
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for a: WKNavigationAction, windowFeatures f: WKWindowFeatures) -> WKWebView? {
        if let u = a.request.url {
            if WebController.isLocal(u) { w.load(URLRequest(url: u)) } else if ["http", "https", "mailto", "slack"].contains(u.scheme ?? "") { NSWorkspace.shared.open(u) }
        }
        return nil
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let d = m.body as? [String: Any], let type = d["type"] as? String else { return }
        switch type {
        case "reveal":
            if let p = d["path"] as? String {
                let path = (p as NSString).expandingTildeInPath
                let real = (path as NSString).resolvingSymlinksInPath, low = real.lowercased()
                let blocked = ["/.ssh", "/library/keychains", "pgpass", "/.aws", "/.gnupg", "credentials", "/state/secrets", "page.key"].contains { low.contains($0) }
                if !blocked && real.hasPrefix(home + "/") && FileManager.default.fileExists(atPath: real) {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: real)])
                }
            }
        default:
            Voice.shared?.handle(type: type, body: d)
        }
    }

    func js(_ s: String) { web.evaluateJavaScript(s, completionHandler: nil) }
}

/// Hook for the voice layer (filled in by Voice.swift-section below when present).
class Voice {
    static var shared: Voice?
    func handle(type: String, body: [String: Any]) {}
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    let ctl = WebController()

    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                         backing: .buffered, defer: false)
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.title = "Jarvis"
        w.appearance = NSAppearance(named: .darkAqua)
        w.backgroundColor = NSColor(srgbRed: 0x01/255.0, green: 0x14/255.0, blue: 0x1a/255.0, alpha: 1)
        w.minSize = NSSize(width: 380, height: 480)    // office mode: a slim side panel on the external display
        w.contentView = ctl.web
        w.center()
        w.setFrameAutosaveName("JarvisMainWindow")
        w.isReleasedWhenClosed = false
        w.delegate = self
        w.collectionBehavior.insert(.fullScreenPrimary)   // native full screen, like Safari/Mail (macOS keeps the notch strip black)
        window = w
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        DispatchQueue.global().async {
            restartServerForKey()              // the server must hold this launch's page key
            DispatchQueue.main.async { self.ctl.load() }
        }
        // The server can stop under the app (4 Oct: twice, killed with a Claude session that had restarted it): look every
        // 15 s and start it again, so it always belongs to the app and Jarvis never sits there deaf.
        // 5 Oct: also restarts a server that listens but no longer answers (superviseServer).
        Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { _ in superviseQ.async { superviseServer() } }
        appDidLaunch(self)
    }

    // office mode: every move/resize is remembered for the current display setup (Office.swift)
    func windowDidMove(_ n: Notification) { Office.shared.windowChanged() }
    func windowDidResize(_ n: Notification) { Office.shared.windowChanged() }

    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows f: Bool) -> Bool {
        if !f { window.makeKeyAndOrderFront(nil) }
        return true
    }

    // Closing the window keeps the app (and listening) alive; Quit ends it.
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }

    @objc func reload(_ s: Any?) { ctl.web.reloadFromOrigin() }

    func buildMenu() {
        let main = NSMenu()
        func item(_ t: String, _ a: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k); i.keyEquivalentModifierMask = mods; return i
        }
        let appMenu = NSMenu()
        appMenu.addItem(item("About Jarvis", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""))
        appMenu.addItem(.separator())
        extraAppMenuItems(appMenu)
        appMenu.addItem(item("Hide Jarvis", #selector(NSApplication.hide(_:)), "h"))
        appMenu.addItem(item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]))
        appMenu.addItem(item("Show All", #selector(NSApplication.unhideAllApplications(_:)), ""))
        appMenu.addItem(.separator())
        appMenu.addItem(item("Quit Jarvis", #selector(NSApplication.terminate(_:)), "q"))
        let a = NSMenuItem(); a.submenu = appMenu; main.addItem(a)

        let edit = NSMenu(title: "Edit")
        edit.addItem(item("Undo", Selector(("undo:")), "z"))
        edit.addItem(item("Redo", Selector(("redo:")), "z", [.command, .shift]))
        edit.addItem(.separator())
        edit.addItem(item("Cut", #selector(NSText.cut(_:)), "x"))
        edit.addItem(item("Copy", #selector(NSText.copy(_:)), "c"))
        edit.addItem(item("Paste", #selector(NSText.paste(_:)), "v"))
        edit.addItem(item("Select All", #selector(NSText.selectAll(_:)), "a"))
        let e = NSMenuItem(); e.submenu = edit; main.addItem(e)

        let view = NSMenu(title: "View")
        let r = item("Reload", #selector(reload(_:)), "r"); r.target = self
        view.addItem(r)
        view.addItem(.separator())
        view.addItem(item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.control, .command]))
        let v = NSMenuItem(); v.submenu = view; main.addItem(v)

        let win = NSMenu(title: "Window")
        win.addItem(item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"))
        win.addItem(item("Zoom", #selector(NSWindow.performZoom(_:)), ""))
        let wi = NSMenuItem(); wi.submenu = win; main.addItem(wi)
        NSApp.windowsMenu = win
        NSApp.mainMenu = main
    }
}

// Extension points for the voice layer.
var extraAppMenuItems: (NSMenu) -> Void = { m in VoiceMenu.shared.install(m) }
var appDidLaunch: (AppDelegate) -> Void = { d in
    signal(SIGPIPE, SIG_IGN)   // a helper that dies must never take the app with it
    let v = VoiceLayer(delegate: d)
    Voice.shared = v
    v.launch()
}

// MARK: menu

final class VoiceMenu: NSObject {
    static let shared = VoiceMenu()
    var listenItem: NSMenuItem?

    func install(_ m: NSMenu) {
        let l = NSMenuItem(title: "Listening", action: #selector(toggleListening(_:)), keyEquivalent: "")
        l.target = self
        l.state = (UserDefaults.standard.object(forKey: "JarvisListening") as? Bool ?? true) ? .on : .off
        let t = NSMenuItem(title: "Teach Jarvis Your Voice…", action: #selector(openEnrol(_:)), keyEquivalent: "")
        t.target = self
        let c = NSMenuItem(title: "Choose Jarvis's Voice…", action: #selector(openVoices(_:)), keyEquivalent: "")
        c.target = self
        m.addItem(l); m.addItem(t); m.addItem(c); m.addItem(.separator())
        listenItem = l
    }

    func sync(_ on: Bool) { listenItem?.state = on ? .on : .off }
    @objc func toggleListening(_ s: Any?) { if let v = Voice.shared as? VoiceLayer { v.setListening(!v.listening) } }
    @objc func openEnrol(_ s: Any?) { (Voice.shared as? VoiceLayer)?.openPanel("enrol") }
    @objc func openVoices(_ s: Any?) { (Voice.shared as? VoiceLayer)?.openPanel("voices") }
}

// MARK: - main

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
