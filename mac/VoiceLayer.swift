// class VoiceLayer: all of the voice's state, launch and quit, and the bridge to the page (messages in, events out).
// Its methods live in extensions: Listening.swift, Speaking.swift, Conversation.swift, Enrolment.swift.

import AppKit
import WebKit
import AVFoundation
import Speech

// MARK: the voice layer

final class VoiceLayer: Voice {
    weak var appDelegate: AppDelegate?
    weak var ctl: WebController?

    // state
    var listening = UserDefaults.standard.object(forKey: "JarvisListening") as? Bool ?? true
    var awake = false, speaking = false, busy = false, enrolling = false, training = false
    var pttActive = false            // a push-to-talk utterance holds the mic while listening is off
    var pttHeld = false              // Option+Space is down
    var awakeVia = ""                // what woke it: word | ptt | test
    var lastActivity = Date()
    var lastState = ""
    var quitting = false
    var timer: Timer?
    var keyMonitor: Any?
    var activity: NSObjectProtocol?

    // audio in
    var engine = AVAudioEngine()          // replaced by the engine that actually started (see tryEngine)
    var player = AVAudioPlayerNode()
    var engineOn = false
    var engineStartedAt = Date.distantPast
    var configRestarts: [Date] = []
    let fmt16 = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    let playFmt = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1)!
    var plainEngine: AVAudioEngine?
    var plainPlayer: AVAudioPlayerNode?
    var outIsMain = false
    var chime: AVAudioPCMBuffer?

    // shared with the audio thread (tapLock)
    let tapLock = NSLock()
    var tapRequest: RecRequest?
    var tapFinReq: RecRequest?   // a request finishing a turn still hears 250 ms (its decoder reaches the last word)
    var tapHold: [AVAudioPCMBuffer]?       // audio kept (in memory only) until the next request starts, then replayed into it
    var lastEnrolAt = Date.distantPast
    var tapWake: LineProcess?
    var tapRec: Recording?

    // wake helper
    var wake: LineProcess?
    var wakeReady = false
    var wakeModel = FileManager.default.fileExists(atPath: "\(voiceDir)/personal.npz") ? "personal" : "hey_jarvis"
    var wakeExits = 0

    // recognition
    var recognizer: SFSpeechRecognizer?
    var request: RecRequest?          // SFSpeechRecognizer's or SpeechAnalyzer's (Ears.swift, settings "ears")
    var task: RecTask?
    var recognizing = false
    var recGen = 0
    var recErrors: [Date] = []
    var partial = ""
    var partialAt = Date()
    var reqStart = Date()
    var partialShown = true
    var barged = false
    var spoken = Set<String>()
    var lastErrorText = ""
    var lastErrorAt = Date.distantPast

    // enrolment
    var recording: Recording?
    var trainProc: Process?

    // speech out
    let kokoro = TTSHelper(name: "kokoro", python: ttsPython, script: "\(voiceCode)/tts.py")
    var naturals: [String: TTSHelper] = [:]
    let synth = AVSpeechSynthesizer()
    var queue: [Utt] = []
    var playing: Utt?
    var synthBusy = false
    var speakGen = 0
    var savedVoice: String?
    var ttsDirs = Set<String>()
    var outLatency: Double = 0             // seconds from scheduling a buffer to sound (output engine + time-pitch)

    // settings.json, re-read when it changes (flags: see flag())
    var settingsCache: [String: Any] = [:]
    var settingsMTime = Date.distantPast

    // metrics
    var turnCur: TurnRec?                  // the turn being answered
    var lastDrain = Date.distantPast       // the speech queue last ran empty
    var bargeTurn: TurnRec?, bargeAt = Date.distantPast
    // energy VAD on the tap (tapLock): used for user_stop until the turn helper (Silero) gives its own
    var vadFloor: Float = 0.001
    var vadLastVoice = Date.distantPast, vadOnset = Date.distantPast, vadVoiced = false

    // ---- voice upgrade 2 Oct (methods in Conversation.swift) ----
    // test hooks (settings.json "test_hooks": true only): voice/test/cmd.json in the data home. A test is ONE continuous synthetic
    // stream (its clips, digital silence between them); the mic is muted for the whole test.
    var tapTest = false                    // tapLock: mic buffers are dropped while a test feeds the pipeline
    var feedTimer: DispatchSourceTimer?
    var feedVoiceEnd: Date?                // when the current clip's last voiced frame went in
    var feedVoiceStart: Date?              // ... and its first one (barge-in onset in tests)
    var testTag: String?, testOut: [String: Any] = [:]
    let feedQ = DispatchQueue(label: "jarvis.testfeed", qos: .userInteractive)
    let feedLock = NSLock()
    var feedClips: [FeedClip] = []         // feedLock
    var feedPos = 0                        // feedLock
    var testGen = 0
    // turn helper (turn.py: Silero VAD + Smart Turn v3)
    var turnProc: LineProcess?
    var speakerProc: LineProcess?                   // speaker.py serve: is this short "yes" his voice? (Speaker.swift)
    var spkPending = [(turn: String, path: String, snr: Double)]()   // replies being scored; snr = his speech / the room floor
    var uttPCM = [Int16]()                          // tapLock: 16 kHz audio of the current request (memory only, 8 s max)
    var turnReady = false
    var turnExits = 0
    var tapTurn: LineProcess?              // tapLock
    var turnMode = ""
    var vadOn = false                      // his voice is on right now (per the turn helper)
    var vadRunStart = Date.distantPast     // this stretch of voice began
    var vadRunDuringSpeech = false         // ... while Jarvis was speaking (a "yeah" then is a backchannel)
    var vadVoiced250 = false               // this stretch has lasted 250 ms
    var lastEot = Date.distantPast
    var finRq: RecRequest?   // the request being finalized after a turn end
    var finTask: RecTask?
    var finGen = -1
    var finTurn: TurnRec?
    var finSent: String?                   // the text already sent for it (latest partial), if the final came late
    var finTimer: DispatchWorkItem?
    var earsHold: AnyObject?
    var reqVoiced = 0.0, reqVoicedRMS = 0.0   // tapLock: seconds of his voice in the current request, and loudness x seconds
    var uttStart: Date?, lastCue = Date.distantPast, lastCueText = ""   // his current utterance (turn helper start .. eot), last "mm-hm"
    var testEngine = false       // the engine runs only for tests (Listening is off)     // the second pass ("ears") running for the turn that just ended
    var finPartial = ""                    // the latest words of the request being finalized
    var lexiconCache: [String: String] = [:], lexiconMTime = Date.distantPast
    var lastCacheTry = Date.distantPast
    var specTurn: String?, specAt = Date.distantPast    // a turn that may still be dropped if he goes on within 600 ms
    var pendingAction = false              // the page has an action waiting for yes/cancel: "yeah" is an answer then
    // what he actually heard (barge-in): clauses fully played since the turn began + the playing one
    var playStart = Date.distantPast
    var playDur: Double = 0
    var heardTexts: [String] = []
    var muteUntilNext = false
    // openers + fillers kept in memory, in the current voice
    var lineCache: [String: AVAudioPCMBuffer] = [:]
    var cacheVoice = ""
    var cacheBuilding = false
    var lastOpener = ""
    var wait: TurnWait?
    var convVoice: String?                 // one voice per conversation
    // proactive
    var lastProactive = Date.distantPast
    // lines to say when it suits him (say_soon / proactive): prio urgent | asked | fyi, see soonTick
    var soonQ: [(text: String, at: Date, prio: String)] = []
    var mainTP: AVAudioUnitTimePitch?      // time-pitch in the main (voice-processing) graph, for the echo_cancel output path
    var plainTP: AVAudioUnitTimePitch?     // time-pitch in the output-only graph (the usual path)
    // pace matching (3 Oct, brain item 5): his words per second this turn vs his running average; the answer's pace follows
    // a little when he is quicker than usual (square root, 1.0-1.08; never slower than his speech_rate).
    var paceMul: Float = 1.0
    var wpsBase = 0.0, wpsN = 0
    var eotPros: [String: Any]?            // turn.py's pitch/loudness of the turn that just ended (for the final's "voice")
    var echoCancelBuilt = false            // the running engine was built with echo_cancel
    // (batch 2, 13:xx) barge-in on a short run whose words arrive after the voice stopped ("Wait, wait")
    var lastRunEnd = Date.distantPast
    // tests: a feed with "quiet": true marks its finals so the page does not ask the brain (STT accuracy runs)
    var testQuiet = false
    // other speech-to-text engine in turn.py (settings.json "stt_engine" != "apple"): its text replaces Apple's at a turn end
    var turnStt = ""                       // the engine turn.py has loaded ("" = none)
    var sttAsked = ""                      // the engine last asked of turn.py
    var altTurn: TurnRec?, altSent = false, altApple = ""
    var lastPlayEnd = Date.distantPast, lastPlayTurn: String?, lastPlayKind = ""   // player gap counter
    // echo meter (tapLock): the mic's level while Jarvis speaks, per output path (echo_cancel experiment; numbers only)
    var echoMeter = false, echoSum = 0.0, echoN = 0

    init(delegate d: AppDelegate) {
        appDelegate = d
        ctl = d.ctl
        super.init()
    }

    // MARK: launch / quit

    func launch() {
        vlog("launch; listening=\(listening)")
        savedVoice = readSettings()["voice"] as? String
        kokoro.onChange = { [weak self] in self?.emitState(); if self?.kokoro.st == .ready { self?.buildCache() } }
        kokoro.onPath = { [weak self] p in self?.ttsDirs.insert((p as NSString).deletingLastPathComponent) }
        kokoro.start()
        if let sv = savedVoice, isNatural(sv) { naturalHelper(sv)?.start() }
        startWake()
        startTurnHelper()
        startSpeakerHelper()
        makeRecognizer()
        Ears.log = { vlog($0) }
        if flag("ears") { Ears.prepare { vlog($0) } }      // the new recognizer's model, ready before the first words
        Office.shared.start(self)

        let t = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(t, forMode: .common)
        timer = t

        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp]) { [weak self] e in
            guard let s = self, e.keyCode == 49 else { return e }      // space
            let m = e.modifierFlags.intersection(.deviceIndependentFlagsMask)
            if e.type == .keyDown && m.contains(.option) && !m.contains(.command) && !m.contains(.control) {
                if !e.isARepeat { s.pttKeyDown() }
                return nil
            }
            if e.type == .keyUp && s.pttHeld { s.pttKeyUp(); return nil }
            return e
        }

        NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification, object: nil, queue: .main) { [weak self] _ in
            self?.shutdown()
        }
        NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: .main) { [weak self] n in
            guard let self = self, let e = n.object as? AVAudioEngine else { return }
            if e === self.engine {
                // never restart from inside the post itself; let it return first
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in self?.engineConfigChanged() }
            }
            else if e === self.plainEngine {       // output device changed under the output-only engine: rebuild it next time
                vlog("output configuration changed")
                if !self.outIsMain && (self.playing != nil || !self.queue.isEmpty) { self.stopSpeaking() }
                self.plainEngine?.stop(); self.plainEngine = nil; self.plainPlayer = nil; self.plainTP = nil
            }
        }

        // Permissions: the mic first, then speech recognition; both are reported in 'state'.
        AVCaptureDevice.requestAccess(for: .audio) { _ in
            DispatchQueue.main.async {
                vlog("mic: \(self.micStatus)")
                self.emitState()
                self.updateEngine()
                SFSpeechRecognizer.requestAuthorization { _ in
                    DispatchQueue.main.async { vlog("speech: \(self.speechStatus)"); self.emitState() }
                }
            }
        }
    }

    func shutdown() {
        if quitting { return }
        quitting = true
        vlog("quit")
        if let tr = turnCur { closeTurn(tr) }
        finishTest()
        feedTimer?.cancel()
        stopRecognition()
        speakGen += 1
        queue.removeAll()
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        if engineOn { engine.inputNode.removeTap(onBus: 0); engine.stop(); engineOn = false }
        plainEngine?.stop()
        tapLock.lock(); tapWake = nil; tapRequest = nil; tapRec = nil; tapLock.unlock()
        wake?.stop(grace: 0); wake = nil
        turnProc?.stop(grace: 0); turnProc = nil
        kokoro.kill()
        naturals.values.forEach { $0.kill() }
        if let p = trainProc, p.isRunning { p.terminate() }
        if enrolling || training { try? FileManager.default.removeItem(atPath: enrolDir) }
        for d in ttsDirs { rmdir(d) }   // tts.py's temp dir, empty once every line is deleted
    }

    func openPanel(_ what: String) {
        if let w = appDelegate?.window { w.makeKeyAndOrderFront(nil) }
        NSApp.activate(ignoringOtherApps: true)
        emit(["type": "open", "what": what])
    }

    // MARK: page bridge

    override func handle(type: String, body: [String: Any]) {
        switch type {
        case "hello":
            busy = false; lastActivity = Date()          // the page (re)loaded: whatever it was waiting on is gone
            if enrolling || training { handle(type: "enrol", body: ["step": "cancel"]) }
            emitState(force: true)
        case "listen": setListening((body["on"] as? Bool) ?? true)
        case "ptt_start": pttStart()
        case "ptt_stop": pttStop()
        case "sleep": if awake { goSleep("command") } else { emitState() }
        case "busy":
            busy = (body["on"] as? Bool) ?? false
            pendingAction = (body["pending"] as? Bool) ?? false
            lastActivity = Date()
        case "progress":
            onProgress((body["text"] as? String) ?? "", turn: body["turn"] as? String)
        case "capture_screen":
            let cid = (body["id"] as? String) ?? ""
            Office.shared.capture { [weak self] d in
                var e = d; e["type"] = "screen_capture"; e["id"] = cid
                self?.emit(e)
            }
        case "proactive":
            proactive((body["text"] as? String) ?? "")
        case "say_soon":
            saySoon((body["text"] as? String) ?? "", prio: (body["prio"] as? String) ?? "asked")
        case "set_flag":
            // only the switches the page shows (voice settings); the rest are changed in settings.json
            guard let n = body["name"] as? String, ["proactive", "office_quiet"].contains(n), let on = body["on"] as? Bool else { return }
            saveSettings([n: on]); vlog("flag \(n) = \(on)"); emitState()
        case "lexicon_add":
            guard let w = body["word"] as? String, let say = body["say"] as? String else { return }
            let ok = SpeechText.addToLexicon("\(voiceDir)/lexicon.json", word: w, say: say)
            vlog("lexicon: word \(ok ? "added" : "refused")")
            emit(["type": "lexicon_added", "ok": ok])
        case "speak":
            let id = (body["id"] as? String) ?? (body["id"].map { "\($0)" } ?? UUID().uuidString)
            enqueueSpeak(id: id, text: (body["text"] as? String) ?? "", kind: (body["kind"] as? String) ?? "other", turn: body["turn"] as? String)
        case "turn":
            // page-side timings of a voice turn (ms since 1970, same clock): req_sent, first_token, fast_path
            guard let t = body["turn"] as? String, let tr = turnCur, tr.id == t, !tr.written else { return }
            if let v = (body["req_sent"] as? NSNumber)?.doubleValue, tr.reqSent == nil { tr.reqSent = v.rounded() }
            if let v = (body["first_token"] as? NSNumber)?.doubleValue, tr.firstToken == nil { tr.firstToken = v.rounded() }
            if (body["fast_path"] as? Bool) == true { tr.fastPath = true }
            if (body["done"] as? Bool) == true { tr.pageDone = true }
            if let k = body["info"] as? [String: Any] {
                // numbers and switches only, plus a few short labels (which tier answered); never text he said or heard
                for (a, b) in k where b is NSNumber || (["tier", "brain"].contains(a) && ((b as? String)?.count ?? 99) <= 12) { tr.info[a] = b }
            }
        case "stop_speaking": stopSpeaking()
        case "voices": emitVoices()
        case "preview":
            let v = (body["voice"] as? String).flatMap { voiceUsable($0) ? $0 : nil } ?? currentVoice
            stopSpeaking()
            if isNatural(v) { naturalHelper(v)?.start() }
            enqueueSpeak(id: "preview", text: previewLine, voice: v)
        case "set_voice":
            guard let v = body["voice"] as? String, voiceUsable(v) else { emit(["type": "error", "text": "That voice is not available."]); return }
            savedVoice = v
            saveSettings(["voice": v])
            if isNatural(v) { naturalHelper(v)?.start() }
            if convVoice == nil { DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in self?.buildCache() } }   // else at the next conversation
            emitState()
        case "enrol": enrol((body["step"] as? String) ?? "", (body["n"] as? NSNumber)?.intValue ?? 0)
        default: break
        }
    }

    func emit(_ ev: [String: Any]) {
        guard let d = try? JSONSerialization.data(withJSONObject: ev), let s = String(data: d, encoding: .utf8) else { return }
        ctl?.js("window.jarvisVoice && window.jarvisVoice.onEvent(\(s))")
    }

    func emitError(_ text: String) {
        if text == lastErrorText && Date().timeIntervalSince(lastErrorAt) < 30 { return }
        lastErrorText = text; lastErrorAt = Date()
        vlog("error: \(text)")
        emit(["type": "error", "text": text])
    }

    var micStatus: String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "granted"
        case .notDetermined: return "undetermined"
        default: return "denied"
        }
    }

    var speechStatus: String {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: return "granted"
        case .notDetermined: return "undetermined"
        default: return "denied"
        }
    }

    func emitState(force: Bool = false) {
        updateBypass()          // every change of awake/speaking passes through here
        updateTap()             // ... and the turn helper's feed + mode
        let d: [String: Any] = ["type": "state", "listening": listening, "awake": awake, "speaking": speaking,
                                "model": wakeModel == "personal" ? "personal" : "hey_jarvis",
                                "mic": micStatus, "speech": speechStatus, "voice": currentVoice, "flags": flagsDict(),
                                "office": Office.shared.dict]
        guard let j = try? JSONSerialization.data(withJSONObject: d, options: [.sortedKeys]), let s = String(data: j, encoding: .utf8) else { return }
        if !force && s == lastState { return }
        lastState = s
        ctl?.js("window.jarvisVoice && window.jarvisVoice.onEvent(\(s))")
    }

    /// Ways to build the capture graph, tried in order on a fresh engine until one starts. Voice processing on macOS is
    /// picky about graph order; the last two are fallbacks (no echo reference for speech / no voice processing at all).
    static let engineModes = ["output graph, then voice processing", "voice processing, then output graph",
                              "voice processing, mono output", "voice processing, input only", "no voice processing"]
    var engineMode = -1
    var vpActive = false
    var playerOnMain = false
    // Voice processing costs ~18% of a core on this Mac (neural DSP over the 9-channel mic array). While Jarvis is
    // asleep and silent only the wake word listens, and echo cancellation has nothing to cancel, so the processing is
    // bypassed (still enabled) and switched back on at wake, push-to-talk or speech. Enrolment records in the same
    // idle condition the wake word is later detected in.
    var bypassed = false
    var bypassBroken = false
    var bypassStrikes = 0
    var bypassToggledAt = Date.distantPast
    var tapRMS: Float = 0
    var tapRate: Double = 0
}
