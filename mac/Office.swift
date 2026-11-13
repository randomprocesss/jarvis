// Office mode (the owner, 2 Oct 2026: "I work from office and connect another monitor screen"; "do all the office monitor
// things properly"). An external display means "at the office":
//   A. Jarvis moves to the external display as a slim side panel (the page shows its Office view), and every window
//      size/position is remembered per display setup (settings.json "window_frames").
//   B. Quiet: with people around, spoken answers become on-screen captions unless headphones/AirPods are the output
//      (settings.json "office_quiet", default true). Openers and fillers are skipped while quiet.
//   C. Lid closed: the MacBook's own mic is cut off in hardware, so Jarvis listens through another input (headset,
//      AirPods, the monitor's or webcam's mic) and says so when there is none.
//   D. "This" on any screen: on request (a question with "this/that/screen/window…") the window he is looking at, on
//      any display, is captured and read with on-device OCR (Vision). Never stored; needs Screen Recording permission.
//   E. The page's Office view doubles as the big-screen dashboard when the window is wide.
import AppKit
import AVFoundation
import CoreAudio
import ScreenCaptureKit
import Vision

final class Office {
    static let shared = Office()
    weak var layer: VoiceLayer?
    private(set) var external = false        // an external display is connected
    private(set) var builtinOn = true        // the laptop's own screen is on (false = lid closed)
    private(set) var headphones = false      // output is a headset / AirPods / wired headphones
    private(set) var inputName = ""
    private(set) var outputName = ""
    private(set) var noMic = false
    private var lastOtherPid: pid_t = 0      // the app he was in before Jarvis (what "this" refers to)
    private(set) var lastAppSwitch = Date.distantPast   // any app came to the front (a natural break, for speaking up)
    private var placedFor = ""               // display setup the window was last placed for
    private var saveWork: DispatchWorkItem?
    private var audioWork: DispatchWorkItem?
    private var lastInputID: AudioDeviceID = 0
    private var silentSince: Date?
    private var silentWarned = false

    var active: Bool { external }
    /// Seconds since his last key press, click, scroll or mouse move (any app).
    var idleSecs: Double {
        [CGEventType.keyDown, .mouseMoved, .leftMouseDown, .rightMouseDown, .scrollWheel, .leftMouseDragged]
            .map { CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0) }.min() ?? 0
    }
    var clamshell: Bool { external && !builtinOn }
    var quiet: Bool { inCall || (active && !headphones && ((layer?.settings()["office_quiet"] as? Bool) ?? true)) }

    /// Call awareness (HARNESS.md Phase 3, 5 Oct 2026): another app is using a microphone (a Teams, Zoom, Slack huddle or
    /// FaceTime call). Then Jarvis speaks only as captions, even on headphones, and ignores its wake word, so it never talks
    /// over the call or answers something said in it. Checked every 2 s; the page is told when it changes.
    private(set) var inCall = false
    private var callTimer: Timer?

    private func micInUseElsewhere() -> Bool {
        var types: [AVCaptureDevice.DeviceType] = [.builtInMicrophone, .externalUnknown]
        if #available(macOS 14.0, *) { types = [.microphone, .external] }
        let mics = AVCaptureDevice.DiscoverySession(deviceTypes: types, mediaType: .audio, position: .unspecified).devices
        return mics.contains { $0.isInUseByAnotherApplication }
    }

    private func checkCall() {
        let now = micInUseElsewhere()
        guard now != inCall else { return }
        inCall = now
        layer?.emit(["type": "call", "active": now])
        vlogOffice(now ? "another app is using the mic: quiet for the call" : "the call ended: speaking again")
    }

    private func vlogOffice(_ s: String) { vlog(s) }

    func start(_ l: VoiceLayer) {
        layer = l
        callTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in self?.checkCall() }
        if let f = NSWorkspace.shared.frontmostApplication, f.bundleIdentifier != Bundle.main.bundleIdentifier { lastOtherPid = f.processIdentifier }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] n in
            self?.lastAppSwitch = Date()
            guard let app = n.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
                  app.bundleIdentifier != Bundle.main.bundleIdentifier else { return }
            self?.lastOtherPid = app.processIdentifier
        }
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            // displays settle over a second or so after a plug/unplug or lid change
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { self?.refreshDisplays() }
        }
        for sel in [kAudioHardwarePropertyDefaultInputDevice, kAudioHardwarePropertyDefaultOutputDevice, kAudioHardwarePropertyDevices] {
            var a = AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &a, DispatchQueue.main) { [weak self] _, _ in
                self?.audioChanged()
            }
        }
        refreshAudio(initial: true)
        refreshDisplays()
    }

    var dict: [String: Any] {
        ["active": active, "quiet": quiet, "quiet_setting": (layer?.settings()["office_quiet"] as? Bool) ?? true,
         "clamshell": clamshell, "headphones": headphones, "input": inputName,
         "output": outputName, "no_mic": noMic]
    }

    // MARK: displays + window (A, E)

    func activeDisplays() -> [CGDirectDisplayID] {
        var ids = [CGDirectDisplayID](repeating: 0, count: 16); var n: UInt32 = 0
        CGGetActiveDisplayList(16, &ids, &n)
        return Array(ids.prefix(Int(n)))
    }

    func displayKey() -> String {
        activeDisplays().map { d -> String in
            if let u = CGDisplayCreateUUIDFromDisplayID(d)?.takeRetainedValue(), let s = CFUUIDCreateString(nil, u) { return s as String }
            return String(d)
        }.sorted().joined(separator: "+")
    }

    func refreshDisplays() {
        let ds = activeDisplays()
        let ext = ds.contains { CGDisplayIsBuiltin($0) == 0 }
        let bin = ds.contains { CGDisplayIsBuiltin($0) != 0 }
        let changed = ext != external || bin != builtinOn
        external = ext; builtinOn = bin
        if changed { vlog("office: external=\(ext) laptop screen=\(bin) -> \(active ? (clamshell ? "office, lid closed" : "office") : "laptop")") }
        placeWindow()
        if changed { audioChanged() }      // a lid change moves the mic
        layer?.emitState()
    }

    func screenID(_ s: NSScreen) -> CGDirectDisplayID {
        (s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
    }

    func placeWindow() {
        guard let w = layer?.appDelegate?.window, !w.styleMask.contains(.fullScreen) else { return }
        let key = displayKey()
        guard key != placedFor else { return }
        placedFor = key
        if let saved = savedFrame(key) {
            w.setFrame(saved, display: true, animate: true)
            vlog("office: window restored for this display setup")
            return
        }
        guard active, let scr = NSScreen.screens.first(where: { CGDisplayIsBuiltin(screenID($0)) == 0 }) else { return }
        // first time on this setup: a slim panel on the right of the external display, full height
        let vf = scr.visibleFrame, width: CGFloat = 460
        w.setFrame(NSRect(x: vf.maxX - width, y: vf.minY, width: width, height: vf.height), display: true, animate: true)
        vlog("office: window placed as a side panel on the external display")
    }

    func savedFrame(_ key: String) -> NSRect? {
        guard let all = layer?.settings()["window_frames"] as? [String: Any], let a = all[key] as? [NSNumber], a.count == 4 else { return nil }
        let r = NSRect(x: a[0].doubleValue, y: a[1].doubleValue, width: a[2].doubleValue, height: a[3].doubleValue)
        // only if it is still on a screen that exists
        return NSScreen.screens.contains(where: { $0.frame.intersects(r) }) ? r : nil
    }

    /// The window moved or was resized: remember it for this display setup (debounced).
    func windowChanged() {
        guard let w = layer?.appDelegate?.window, !w.styleMask.contains(.fullScreen), placedFor == displayKey() else { return }
        saveWork?.cancel()
        let f = w.frame, key = placedFor
        let job = DispatchWorkItem { [weak self] in
            guard let l = self?.layer else { return }
            var all = (l.readSettings()["window_frames"] as? [String: Any]) ?? [:]
            all[key] = [f.origin.x, f.origin.y, f.size.width, f.size.height].map { Double($0) }
            l.saveSettings(["window_frames": all])
        }
        saveWork = job
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0, execute: job)
    }

    // MARK: audio devices (B, C)

    func caDevice(_ sel: AudioObjectPropertySelector) -> AudioDeviceID {
        var a = AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var id = AudioDeviceID(0); var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        return AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &id) == noErr ? id : 0
    }

    func caUInt(_ d: AudioDeviceID, _ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> UInt32 {
        var a = AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
        var v: UInt32 = 0; var size = UInt32(MemoryLayout<UInt32>.size)
        return AudioObjectGetPropertyData(d, &a, 0, nil, &size, &v) == noErr ? v : 0
    }

    func caName(_ d: AudioDeviceID) -> String {
        guard d != 0 else { return "" }
        var a = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var name: Unmanaged<CFString>?; var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(d, &a, 0, nil, &size, &name) == noErr, let n = name?.takeRetainedValue() else { return "" }
        return n as String
    }

    func hasInput(_ d: AudioDeviceID) -> Bool {
        var a = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams, mScope: kAudioDevicePropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        return d != 0 && AudioObjectGetPropertyDataSize(d, &a, 0, nil, &size) == noErr && size > 0
    }

    func allDevices() -> [AudioDeviceID] {
        var a = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        return AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &ids) == noErr ? ids : []
    }

    func isBuiltIn(_ d: AudioDeviceID) -> Bool { caUInt(d, kAudioDevicePropertyTransportType) == kAudioDeviceTransportTypeBuiltIn }

    func isHeadphones(_ d: AudioDeviceID) -> Bool {
        guard d != 0 else { return false }
        let t = caUInt(d, kAudioDevicePropertyTransportType), n = caName(d).lowercased()
        if t == kAudioDeviceTransportTypeBluetooth || t == kAudioDeviceTransportTypeBluetoothLE { return true }
        if t == kAudioDeviceTransportTypeBuiltIn && caUInt(d, kAudioDevicePropertyDataSource, kAudioDevicePropertyScopeOutput) == 0x6864706E { return true } // 'hdpn'
        return ["airpods", "headphone", "headset", "earbud", "buds", "jabra", "plantronics", "poly ", "bose", "sony wh", "beats"].contains { n.contains($0) }
    }

    /// With the lid closed the built-in mic hears nothing: the input Jarvis should use instead (headset first, then any other).
    func preferredInput() -> AudioDeviceID? {
        let def = caDevice(kAudioHardwarePropertyDefaultInputDevice)
        guard clamshell, def == 0 || isBuiltIn(def) else { return nil }
        let cands = allDevices().filter { hasInput($0) && !isBuiltIn($0) }
        return cands.first(where: { isHeadphones($0) }) ?? cands.first
    }

    func audioChanged() {
        audioWork?.cancel()
        let job = DispatchWorkItem { [weak self] in self?.refreshAudio(initial: false) }
        audioWork = job
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8, execute: job)
    }

    func refreshAudio(initial: Bool) {
        let out = caDevice(kAudioHardwarePropertyDefaultOutputDevice)
        let wasQuiet = quiet
        headphones = isHeadphones(out); outputName = caName(out)
        let pref = preferredInput(), def = caDevice(kAudioHardwarePropertyDefaultInputDevice)
        let inDev = pref ?? def
        inputName = caName(inDev)
        noMic = inDev == 0 || !hasInput(inDev) || (clamshell && pref == nil && isBuiltIn(inDev))
        if !initial {
            vlog("office: output \(outputName)\(headphones ? " (headphones)" : ""); input \(inputName.isEmpty ? "none" : inputName)\(pref != nil ? " (lid closed, not the built-in mic)" : "")")
            if wasQuiet != quiet { vlog("office: quiet \(quiet ? "on: captions instead of speech" : "off: speaking")") }
            if noMic { layer?.emitError(clamshell ? "The lid is closed, so the laptop's mic is off. Plug in a headset or AirPods, or use the monitor's mic." : "No microphone is available right now.") }
            if inDev != lastInputID && lastInputID != 0, let l = layer, l.engineOn {
                vlog("office: input device changed; restarting the microphone")
                l.stopEngine(); l.updateEngine()
            }
        }
        lastInputID = inDev
        silentSince = nil; silentWarned = false
        layer?.emitState()
    }

    /// Applied to each new capture engine before it starts: the lid-closed replacement mic, if any.
    func applyInput(to e: AVAudioEngine) {
        guard let d = preferredInput() else { return }
        do { try e.inputNode.auAudioUnit.setDeviceID(d); vlog("office: listening through \(caName(d))") }
        catch { vlog("office: could not switch the mic to \(caName(d)): \(error)") }
    }

    /// Called with each input level reading: a lid-closed mic that only ever hears digital silence gets one warning.
    func noteLevel(_ lvl: Double) {
        guard clamshell else { return }
        if lvl > 0 { silentSince = nil; return }
        if silentSince == nil { silentSince = Date() }
        if !silentWarned, let s = silentSince, Date().timeIntervalSince(s) > 12 {
            silentWarned = true
            layer?.emitError("Jarvis hears nothing with the lid closed. Plug in a headset or AirPods, or pick the monitor's mic in Sound settings.")
        }
    }

    // MARK: "this" on any screen (D)

    /// The frontmost window of the app he was last in (on any display), read with on-device OCR. Never saved.
    func capture(_ done: @escaping ([String: Any]) -> Void) {
        guard #available(macOS 14.0, *) else { done(["error": "needs macOS 14"]); return }
        if !CGPreflightScreenCaptureAccess() {
            _ = CGRequestScreenCaptureAccess()      // asks once; then it lives in System Settings
            done(["error": "permission", "text": ""]); return
        }
        let pid = lastOtherPid
        let list = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]) ?? []
        let front = list.first { w in
            (w[kCGWindowOwnerPID as String] as? Int32) == pid && (w[kCGWindowLayer as String] as? Int) == 0
                && (((w[kCGWindowBounds as String] as? [String: Any])?["Width"] as? Double) ?? 0) > 200
        }
        guard let wid = front?[kCGWindowNumber as String] as? UInt32 else { done(["error": "no window"]); return }
        SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) { content, err in
            guard let win = content?.windows.first(where: { $0.windowID == wid }) else {
                DispatchQueue.main.async { done(["error": err?.localizedDescription ?? "window not found"]) }; return
            }
            let filter = SCContentFilter(desktopIndependentWindow: win)
            let cfg = SCStreamConfiguration()
            cfg.width = Int(win.frame.width * 2); cfg.height = Int(win.frame.height * 2); cfg.showsCursor = false
            SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg) { img, err in
                guard let img = img else { DispatchQueue.main.async { done(["error": err?.localizedDescription ?? "capture failed"]) }; return }
                let req = VNRecognizeTextRequest { r, _ in
                    let obs = (r.results as? [VNRecognizedTextObservation]) ?? []
                    // reading order: top to bottom (Vision's y grows upwards), then left to right on the same line
                    let lines = obs.sorted { a, b in
                        abs(a.boundingBox.midY - b.boundingBox.midY) > 0.01 ? a.boundingBox.midY > b.boundingBox.midY : a.boundingBox.minX < b.boundingBox.minX
                    }.compactMap { $0.topCandidates(1).first?.string }
                    let text = String(lines.joined(separator: "\n").prefix(3500))
                    DispatchQueue.main.async {
                        done(["app": win.owningApplication?.applicationName ?? "", "title": win.title ?? "", "text": text])
                    }
                }
                req.recognitionLevel = .accurate
                req.usesLanguageCorrection = true
                DispatchQueue.global(qos: .userInitiated).async {
                    do { try VNImageRequestHandler(cgImage: img, options: [:]).perform([req]) }
                    catch { DispatchQueue.main.async { done(["error": "OCR failed: \(error.localizedDescription)"]) } }
                }
            }
        }
    }
}
