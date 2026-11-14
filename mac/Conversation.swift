// The conversation layer. Every piece sits behind a switch in
// voice/settings.json and the old behaviour stays when it is off:
//   smart_turn      turn ends from turn.py (Silero VAD + Smart Turn v3) instead of the fixed ~1.2 s silence; speculative start
//   backchannel     barge-in = 250 ms of his voice + real words (not "yeah"/"mm-hmm", not our own echo); "yeah" while Jarvis
//                   talks is not sent as a question; playback stops at once; the page learns what he actually heard
//   cached_openers  openers/fillers made in the current voice at start-up and kept in memory; the app times them (one place);
//                   smooth joins (fades + an even gap)
//   lexicon         pronunciation lexicon + text normalization before TTS; short first clause; (one voice per conversation is always on)
//   proactive       Jarvis may speak first (a voice-started task finished / a new approval), at most once per 10 minutes
//   echo_cancel     (experiment, default off) speech plays through the voice-processing engine so the mic cancels it
// Plus the test hooks ("test_hooks": true only). State lives in VoiceLayer.swift (class VoiceLayer).
import AppKit
import AVFoundation
import Speech

/// One clip of a test stream (already at the tap's rate) and where its voice starts and ends.
final class FeedClip {
    let buf: AVAudioPCMBuffer, voiceStart: Int, voiceEnd: Int
    var onDone: (() -> Void)?
    init(buf: AVAudioPCMBuffer, voiceStart: Int, voiceEnd: Int) { self.buf = buf; self.voiceStart = voiceStart; self.voiceEnd = voiceEnd }
}

/// Opener / progress bookkeeping while one turn waits for its answer.
final class TurnWait {
    let turn: String, eot: Date
    var openerDone = false, narrations = 0, lastNarration = Date.distantPast
    var progress = "", saidProgress = ""
    init(turn: String, eot: Date) { self.turn = turn; self.eot = eot }
}

extension VoiceLayer {

    // MARK: - test hooks
    // Drop a JSON command at voice/test/cmd.json:
    //   {"cmd":"feed","path":"<16 kHz wav>","tag":"t1","wake":true,"when":"now"|"speaking","delay_ms":700,
    //    "then":{"path":"<wav>","delay_ms":600}}      (then = a second clip once Jarvis speaks: barge-in tests)
    //   {"cmd":"speak","text":"...","kind":"answer"}   {"cmd":"sleep"}   {"cmd":"stop_speaking"}
    //   {"cmd":"proactive","text":"..."}   {"cmd":"engine_restart"} (only when idle)   feed + "quiet": true = finals never reach the brain
    //   {"cmd":"speak","text":"...","voice":"natural_female"}   {"cmd":"voices"} (picker labels to voice.log)
    // A test is ONE continuous synthetic stream through the same path as the mic (the mic is muted until it ends). The final text
    // of that SYNTHETIC audio and the turn timings go to voice/test/out/<tag>.json.

    func pollTestCmd() {
        let p = "\(voiceDir)/test/cmd.json"
        guard FileManager.default.fileExists(atPath: p) else { return }
        let d = FileManager.default.contents(atPath: p)
        try? FileManager.default.removeItem(atPath: p)
        guard (settings()["test_hooks"] as? Bool) == true else { vlog("test: hooks are off"); return }
        guard let d = d, let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any], let c = o["cmd"] as? String else { return }
        vlog("test: \(c)")
        switch c {
        case "feed":
            guard let path = o["path"] as? String else { return }
            // never in the owner's way: no test while he enrols or talks to Jarvis
            if enrolling || training || Date().timeIntervalSince(lastEnrolAt) < 180 || (awake && awakeVia != "test" && Date().timeIntervalSince(lastActivity) < 90) {
                vlog("test: refused (Jarvis is in use)"); return
            }
            // "engine": true = with Listening off, start the engine for tests only (the mic is muted while a clip feeds; it stops
            // again 20 s after the last test). This command only starts it; the next feed runs the clip.
            if !engineOn, (o["engine"] as? Bool) == true, !listening {
                startEngine(); testEngine = engineOn
                vlog("test: engine \(engineOn ? "started for tests (Listening stays off)" : "did not start")"); return
            }
            guard engineOn else { vlog("test: engine off"); return }
            let tag = (o["tag"] as? String) ?? "feed"
            let wake = (o["wake"] as? Bool) ?? true, when = (o["when"] as? String) ?? "now"
            let delay = ((o["delay_ms"] as? NSNumber)?.doubleValue ?? (wake && !awake ? 700 : 300)) / 1000
            finishTest()
            testGen += 1
            let g = testGen
            testTag = tag; testOut = ["tag": tag, "path": path, "started": nowMs()]
            testQuiet = (o["quiet"] as? Bool) ?? false       // finals marked quiet: the page does not ask the brain
            guard startTestStream() else { finishTest(); return }
            if wake && !awake { beginUtterance(via: "test") }
            if let tv = o["voice"] as? String, voiceUsable(tv) {          // this test conversation only; his own choice is untouched
                convVoice = tv
                if isNatural(tv) { naturalHelper(tv)?.start() }
            }
            if let line = o["say"] as? String { enqueueSpeak(id: "test-say", text: line, kind: "answer", turn: turnCur?.id) }
            let then = o["then"] as? [String: Any]
            let go: () -> Void = { [weak self] in
                DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                    guard let self = self, self.testGen == g else { return }
                    let chain: (() -> Void)? = then.map { th in { [weak self] in
                        guard let self = self, self.testGen == g, let p2 = th["path"] as? String else { return }
                        let d2 = ((th["delay_ms"] as? NSNumber)?.doubleValue ?? 600) / 1000
                        let afterSpeech = (th["after"] as? String) == "speech"   // a follow-up once the answer has finished
                        self.whenSpeaking(g) {
                            self.testOut["then_at_speech"] = nowMs()
                            let go2 = {
                                DispatchQueue.main.asyncAfter(deadline: .now() + d2) {
                                    guard self.testGen == g else { return }
                                    self.testOut["then_fed"] = nowMs()
                                    if !self.queueClip(p2, onDone: { [weak self] in self?.scheduleFinish(g) }) { self.finishTest() }
                                }
                            }
                            if afterSpeech { self.whenQuiet(g, go2) } else { go2() }
                        }
                    } }
                    if !self.queueClip(path, onDone: chain ?? { [weak self] in self?.scheduleFinish(g) }) { self.finishTest() }
                }
            }
            if when == "speaking" { whenSpeaking(g) { self.testOut["fed_while_speaking"] = true; go() } } else { go() }
        case "speak":
            // "voice": speak in that voice (a check of a voice; his own choice is not changed)
            let v = (o["voice"] as? String).flatMap { voiceUsable($0) ? $0 : nil }
            if let v = v, isNatural(v) { naturalHelper(v)?.start() }
            enqueueSpeak(id: "test-" + String(UUID().uuidString.prefix(6)), text: (o["text"] as? String) ?? "", voice: v, kind: (o["kind"] as? String) ?? "other", turn: turnCur?.id)
        case "voices":
            // the picker's list, labels only, to the log (checks that a voice entry is offered)
            var labels: [String] = []
            for id in ["natural", "natural_female"] where naturalConfig(id) != nil && naturals[id]?.st != .failed { labels.append(id + "=" + (naturalConfig(id)?.label ?? "")) }
            vlog("test: voices offered: \(labels.joined(separator: ", ")); current \(currentVoice)")
        case "sleep": goSleep("command")
        case "stop_speaking": stopSpeaking()
        case "proactive": proactive((o["text"] as? String) ?? "")     // as the page would post it (task done / new approval)
        case "engine_restart":
            // rebuilds the audio graph (an echo_cancel change takes effect); refused while he uses Jarvis
            if enrolling || training || awake || speaking || playing != nil || recognizing { vlog("test: engine restart refused (in use)"); return }
            stopEngine()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in self?.updateEngine() }
        default: break
        }
    }

    /// Runs `f` once Jarvis is playing something (gives up after 40 s).
    func whenSpeaking(_ g: Int, _ f: @escaping () -> Void, waited: Double = 0) {
        guard testGen == g else { return }
        if playing != nil { f(); return }
        if waited > 40 { vlog("test: nothing was spoken"); finishTest(); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.03) { [weak self] in self?.whenSpeaking(g, f, waited: waited + 0.03) }
    }

    /// Runs `f` once Jarvis has gone quiet (nothing playing, queued or being made; gives up after 90 s).
    func whenQuiet(_ g: Int, _ f: @escaping () -> Void, waited: Double = 0) {
        guard testGen == g else { return }
        if playing == nil && queue.isEmpty && !synthBusy && !speaking { f(); return }
        if waited > 90 { vlog("test: never went quiet"); finishTest(); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in self?.whenQuiet(g, f, waited: waited + 0.05) }
    }

    /// Starts the synthetic stream: 1024-frame chunks at the tap's rate in real time, clips when queued, digital silence otherwise.
    func startTestStream() -> Bool {
        if feedTimer != nil { return true }
        guard tapRate > 0, let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: tapRate, channels: 1, interleaved: false),
              let conv = AVAudioConverter(from: mono, to: fmt16) else { vlog("test: engine not ready (rate \(tapRate))"); return false }
        let rate = tapRate, chunk = 1024, f16 = fmt16
        feedLock.lock(); feedClips = []; feedPos = 0; feedLock.unlock()
        tapLock.lock(); tapTest = true; tapLock.unlock()
        let t = DispatchSource.makeTimerSource(queue: feedQ)
        t.schedule(deadline: .now(), repeating: Double(chunk) / rate, leeway: .microseconds(500))
        t.setEventHandler { [weak self] in
            guard let self = self, let b = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: AVAudioFrameCount(chunk)), let d = b.floatChannelData?[0] else { return }
            b.frameLength = AVAudioFrameCount(chunk)
            var vStart: Date?, vEnd: Date?, finished: FeedClip?
            self.feedLock.lock()
            if let c = self.feedClips.first, let src = c.buf.floatChannelData?[0] {
                let total = Int(c.buf.frameLength), pos = self.feedPos
                for k in 0..<chunk { let j = pos + k; d[k] = j < total ? src[j] : 0 }
                let now = Date()
                if pos <= c.voiceStart && c.voiceStart < pos + chunk { vStart = now.addingTimeInterval(-Double(pos + chunk - c.voiceStart) / rate) }
                if pos <= c.voiceEnd && c.voiceEnd < pos + chunk { vEnd = now.addingTimeInterval(-Double(pos + chunk - c.voiceEnd) / rate) }
                self.feedPos += chunk
                if self.feedPos >= total { finished = c; self.feedClips.removeFirst(); self.feedPos = 0 }
            } else {
                for k in 0..<chunk { d[k] = 0 }
            }
            self.feedLock.unlock()
            self.processMono(b, conv, f16)
            if vStart != nil || vEnd != nil || finished != nil {
                DispatchQueue.main.async {
                    if let s = vStart { self.feedVoiceStart = s; self.testOut["voice_start"] = ms(s) }
                    if let e = vEnd { self.feedVoiceEnd = e; self.testOut["voice_end"] = ms(e) }
                    if let f = finished { self.testOut["clip_done"] = nowMs(); f.onDone?() }
                }
            }
        }
        feedTimer = t
        t.resume()
        return true
    }

    func stopTestStream() {
        feedTimer?.cancel(); feedTimer = nil
        feedLock.lock(); feedClips = []; feedPos = 0; feedLock.unlock()
        tapLock.lock(); tapTest = false; tapLock.unlock()
    }

    /// Loads a clip into the running stream. Its voiced span = frames >= 10 % of the loudest 20 ms frame's RMS.
    func queueClip(_ path: String, onDone: (() -> Void)?) -> Bool {
        guard tapRate > 0, let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: tapRate, channels: 1, interleaved: false),
              let buf = loadAudioFile(path, to: mono), let src = buf.floatChannelData?[0] else {
            vlog("test: could not load \((path as NSString).lastPathComponent)"); return false
        }
        let total = Int(buf.frameLength), fr = Int(tapRate * 0.02)
        var frames: [Float] = []
        var i = 0
        while i + fr <= total { var a: Float = 0; for k in i..<(i + fr) { a += src[k] * src[k] }; frames.append((a / Float(fr)).squareRoot()); i += fr }
        let peak = frames.max() ?? 0
        let first = (frames.firstIndex { $0 >= peak * 0.1 } ?? 0) * fr
        let last = ((frames.lastIndex { $0 >= peak * 0.1 } ?? 0) + 1) * fr
        let c = FeedClip(buf: buf, voiceStart: first, voiceEnd: min(last, total - 1))
        c.onDone = onDone
        feedLock.lock(); feedClips.append(c); feedLock.unlock()
        return true
    }

    /// The test ends once its last turn has been written (answer heard), or after 60 s.
    func scheduleFinish(_ g: Int, _ waited: Double = 0) {
        guard testTag != nil, testGen == g else { return }
        let open = turnCur.map { !$0.written } ?? false
        if (!open && waited >= 3) || waited > 60 { finishTest(); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in self?.scheduleFinish(g, waited + 0.5) }
    }

    /// Writes voice/test/out/<tag>.json for the last test (synthetic audio only) and gives the mic back.
    func finishTest() {
        stopTestStream()
        guard let tag = testTag else { return }
        testTag = nil
        if let tr = turnCur { testOut["turn"] = tr.json() }
        let dir = "\(voiceDir)/test/out"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let safe = tag.filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        if let d = try? JSONSerialization.data(withJSONObject: testOut, options: [.prettyPrinted, .sortedKeys]) {
            try? d.write(to: URL(fileURLWithPath: "\(dir)/\(safe.isEmpty ? "feed" : safe).json"))
        }
        testOut = [:]
        feedVoiceStart = nil
        testQuiet = false
        if awake && awakeVia == "test" { goSleep("test done") }
        if testEngine {
            let g = testGen
            DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
                guard let self = self, self.testEngine, self.testGen == g, self.testTag == nil, !self.listening, !self.pttActive, !self.awake else { return }
                self.testEngine = false; self.stopEngine(); vlog("test: engine stopped (tests over, Listening is off)")
            }
        }
    }

    func testRecord(final text: String, turn: String) {
        guard testTag != nil else { return }
        testOut["final"] = text
        testOut["finals"] = ((testOut["finals"] as? [String]) ?? []) + [text]
        testOut["turns"] = ((testOut["turns"] as? [String]) ?? []) + [turn]
    }

    // MARK: - turn helper (turn.py)

    func startTurnHelper() {
        guard turnProc == nil, !quitting, voiceExtrasReady(), FileManager.default.fileExists(atPath: "\(voiceCode)/turn.py") else { return }
        let p = LineProcess(voicePython, ["\(voiceCode)/turn.py"])
        p.onLine = { [weak self] l in self?.turnLine(l) }
        p.onExit = { [weak self] in self?.turnExited() }
        turnReady = false
        if p.start() { turnProc = p; vlog("turn helper: starting") } else { vlog("turn helper could not start") }
    }

    func turnExited() {
        vlog("turn helper exited")
        turnProc = nil; turnReady = false; turnMode = ""; vadOn = false
        updateTap()
        turnExits += 1
        if !quitting && turnExits <= 5 { DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.startTurnHelper() } }
    }

    func turnCmd(_ o: [String: Any]) {
        guard let p = turnProc, turnReady, let j = try? JSONSerialization.data(withJSONObject: o) else { return }
        var d = Data([0x43])
        var n = UInt32(j.count).littleEndian
        withUnsafeBytes(of: &n) { d.append(contentsOf: $0) }
        d.append(j)
        p.feed(d, maxInflight: 100_000)
    }

    static func turnFrame(_ p: UnsafePointer<Int16>, _ k: Int) -> Data {
        var d = Data(capacity: 5 + k * 2)
        d.append(0x41)
        var n = UInt32(k * 2).littleEndian
        withUnsafeBytes(of: &n) { d.append(contentsOf: $0) }
        d.append(Data(bytes: p, count: k * 2))
        return d
    }

    /// Is the turn helper in use right now (fed with audio)?
    var turnActive: Bool { turnReady && (flag("smart_turn") || flag("backchannel")) }
    var turnWanted: Bool { turnActive && engineOn && !enrolling && (awake || speaking || playing != nil || pttActive) }

    func syncTurnMode() {
        let m = (speaking || playing != nil) && !barged ? "speaking" : "listen"
        if m != turnMode { turnMode = m; turnCmd(["mode": m]) }
        if turnReady {
            let e = sttEngine
            if e != sttAsked { sttAsked = e; turnCmd(["stt": e]) }
        }
    }

    /// settings.json "stt_engine": "apple" (default) or an engine turn.py can load.
    var sttEngine: String { (settings()["stt_engine"] as? String) ?? "apple" }
    /// The other engine is asked for and loaded: its text decides a smart turn end (Apple still gives the live words).
    var altStt: Bool { flag("smart_turn") && sttEngine != "apple" && turnStt == sttEngine }

    func turnLine(_ l: String) {
        guard let o = (try? JSONSerialization.jsonObject(with: Data(l.utf8))) as? [String: Any], let ev = o["ev"] as? String else { return }
        switch ev {
        case "ready":
            turnReady = true; turnMode = ""; turnStt = ""; sttAsked = ""
            vlog("turn helper ready")
            updateTap(); syncTurnMode()
        case "stt_ready":
            turnStt = (o["engine"] as? String) ?? ""
            vlog("turn helper: speech-to-text \(turnStt.isEmpty ? "off" : turnStt + " ready")")
        case "stt":
            onAltStt((o["text"] as? String) ?? "", ms: (o["ms"] as? NSNumber)?.doubleValue)
        case "start", "resume":
            if uttStart == nil { uttStart = Date() }
            vadOn = true; vadRunStart = Date(); vadVoiced250 = false
            vadRunDuringSpeech = speaking || playing != nil
            voiceStarted(resumed: ev == "resume")
        case "voiced":
            vadVoiced250 = true
            if speaking || playing != nil { checkBarge() }
        case "stop", "pause":
            if vadOn { lastRunEnd = Date() }
            vadOn = false
            if ev == "pause" { DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) { [weak self] in self?.maybeCue() } }
        case "eot":
            vadOn = false
            uttStart = nil
            vlog("turn helper: end (\(o["why"] ?? "?"), p=\(o["p"] ?? "-"), after \(o["silence_ms"] ?? "?") ms of silence)")
            onEot(o)
        default: break
        }
    }

    /// His voice (re)started. Right after a turn end this means he wasn't done: within 1.5 s, while nothing of the answer has
    /// been handed to the voice yet (an opener may be playing: it stops), the turn is dropped and the page merges it with what
    /// follows; once the answer is on its way it counts as a cut-off. (3 Oct: 106 of 124 resumes came within 0.6 s and were
    /// merged; the rest, 0.6-1.5 s, were answered half-heard. Waiting longer at every turn end would slow all answers instead.)
    func voiceStarted(resumed: Bool) {
        guard turnActive, let tr = turnCur, !tr.written, let e = tr.eot else { return }
        let eot = Date(timeIntervalSince1970: e / 1000), gap = Date().timeIntervalSince(eot)
        guard gap > 0, gap <= 1.5, !vadRunDuringSpeech else { return }      // a voice that starts while we talk may be our echo
        let answerSent = queue.contains { $0.kind == "answer" && $0.turn == tr.id } || (playing?.kind == "answer" && playing?.turn == tr.id)
        if tr.answerAudio == nil && !answerSent && flag("smart_turn") && tr.info["endpoint"] as? String == "smart" {
            vlog(String(format: "turn dropped: he went on %.0f ms after the turn end%@", gap * 1000, playing != nil ? " (opener stopped)" : ""))
            tr.info["dropped"] = true
            tr.cutoff = true
            if specTurn == tr.id { specTurn = nil }
            wait = nil
            emit(["type": "turn_abort", "turn": tr.id])
            if playing != nil || !queue.isEmpty { stopSpeaking() }
            return
        }
        if !tr.cutoff && (tr.firstAudio == nil || ms(vadRunStart) < (tr.firstAudio ?? 0)) {
            tr.cutoff = true
            vlog(String(format: "cut-off: he went on %.0f ms after the turn end", gap * 1000))
        }
    }

    static let openEndings: Set<String> = ["and", "or", "but", "so", "because", "if", "the", "a", "an", "to", "of", "for", "with", "in", "on",
                                           "at", "from", "um", "umm", "uh", "uhm", "er", "erm", "like", "that", "which", "than", "my", "your",
                                           "our", "their", "about", "into", "also", "then", "is", "are", "was", "were", "could", "would", "can", "will"]
    func endsOpen(_ text: String) -> Bool {
        guard let last = words(text).last else { return true }
        return VoiceLayer.openEndings.contains(last)
    }

    /// turn.py says the turn is over.
    func onEot(_ o: [String: Any]) {
        guard flag("smart_turn"), recognizing, !pttHeld else { vlog("turn end ignored (not listening)"); return }
        if (speaking || playing != nil) && !barged { vlog("turn end ignored (Jarvis is speaking)"); return }   // only his voice ends a turn
        let text = cleanedPartial().trimmingCharacters(in: .whitespacesAndNewlines)
        let why = (o["why"] as? String) ?? "", p = (o["p"] as? NSNumber)?.doubleValue
        let sil = (o["silence_ms"] as? NSNumber)?.doubleValue ?? 200
        if text.isEmpty {                                        // "Umm": the recognizer keeps nothing
            vlog(String(format: "turn end with no words yet: listening on (request %.0f s old, last words %.0f s ago, speaking=%@)",
                        Date().timeIntervalSince(reqStart), Date().timeIntervalSince(partialAt), speaking || playing != nil ? "yes" : "no"))
            return
        }
        if why == "smart" && endsOpen(text) { turnCmd(["hold": true]); vlog("turn end held (open ending)"); return }
        if flag("backchannel") && vadRunDuringSpeech && backchannelDuringSpeech(text) && !pendingAction {
            vlog("backchannel while Jarvis spoke: not a question")
            startRecognition(); return
        }
        // his last voiced frame (Silero) = now - the silence it counted - the scoring time - ~20 ms of audio buffering
        let score = (o["score_ms"] as? NSNumber)?.doubleValue ?? 0
        eotPros = o["pros"] as? [String: Any]
        finishSmart(text: text, userStop: Date().addingTimeInterval(-(sil + score + 20) / 1000), p: p, why: why)
    }

    /// Ends the utterance from a turn.py end: the request finalizes in the background (its final, or the latest words after
    /// 150 ms, become the turn), while a fresh request already listens for what comes next. p >= 0.8 sends the words at once.
    func finishSmart(text: String, userStop: Date, p: Double?, why: String) {
        // The recognizer's words lag his voice by a few hundred ms. Only words that changed after his voice ended can go early;
        // and the finishing request keeps hearing for 250 ms before endAudio, so its decoder reaches the last word.
        let fresh = partialAt > userStop.addingTimeInterval(0.05)
        if testTag != nil { testOut["eot_partial"] = text; testOut["partial_age_ms"] = (Date().timeIntervalSince(partialAt) * 1000).rounded() }
        let rq = request, t = task, g = recGen
        // "ears": SpeechAnalyzer's read of the whole utterance decides (fewer wrong words, ~0.1 s); turn.py's engine, if set, wins
        let tee = rq as? TeeRequest
        let ears = !altStt && tee != nil && flag("ears") && Ears.ready && Ears.locale != nil
        let alt = altStt || ears              // the other engine's text decides; never send Apple's words early then
        let spec = !alt && why == "smart" && (p ?? 0) >= 0.8 && fresh
        request = nil; task = nil; recognizing = false
        // a new recognition task would cancel this one (one task at a time), so the next request starts only once this one has
        // finished; what he says meanwhile is held in memory and replayed into it
        tapLock.lock(); tapFinReq = rq; tapHold = []; tapLock.unlock()
        updateTap()
        finTask?.cancel()
        finRq = rq; finTask = t; finGen = g; finSent = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            guard let self = self else { return }
            self.tapLock.lock(); if self.tapFinReq === rq { self.tapFinReq = nil }; self.tapLock.unlock()
            rq?.endAudio()
        }
        let now = Date()
        lastEot = now; lastActivity = now
        if let bt = bargeTurn { if onlyBackchannel(text) || isEcho(text) { bt.falseBarge = true }; bargeTurn = nil }
        let tr = openTurn(eot: now, sttFinal: now, userStop: userStop)
        if let p = eotPros { tr.info["pros"] = p }; eotPros = nil
        tr.info["endpoint"] = "smart"; tr.info["eot_why"] = why; tr.info["spec"] = spec; tr.info["words_fresh"] = fresh
        if let p = p { tr.info["eot_p"] = (p * 1000).rounded() / 1000 }
        finTurn = tr
        finPartial = text
        if alt {
            altTurn = tr; altSent = false; altApple = text; tr.info["stt"] = ears ? "ears" : sttEngine
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in self?.altTimeout(tr) }
            if ears, let tee = tee { earsPass(tee.audio(), tr) }
        }
        if spec { sendFinal(text, tr, spec: true) }
        let w = DispatchWorkItem { [weak self] in
            guard let self = self, self.finGen == g, let ft = self.finTurn, ft === tr, self.altTurn !== tr else { return }
            if self.finSent == nil { tr.info["stt"] = "partial"; self.sendFinal(self.finPartial, tr, spec: false) }
        }
        finTimer = w
        DispatchQueue.main.asyncAfter(deadline: .now() + (fresh ? 0.15 : 0.45), execute: w)   // 150 ms budget; 450 when the words lag
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in self?.resumeListening(g) }   // at the latest
    }

    /// After a turn end: the finishing request is done (final, error or 0.7 s), so the next one can start (conversation mode:
    /// follow-ups need no wake word) with the audio held since the turn end.
    func resumeListening(_ g: Int) {
        guard finGen == g else { return }
        if finSent == nil, let tr = finTurn, altTurn !== tr, !finPartial.isEmpty { tr.info["stt"] = "partial"; sendFinal(finPartial, tr, spec: false) }
        finTask?.cancel(); finTask = nil; finRq = nil; finGen = -1; finTurn = nil
        tapLock.lock(); tapFinReq = nil; tapLock.unlock()
        if awake && engineOn && !recognizing {
            startRecognition()
            tapLock.lock()
            let held = tapHold ?? []
            tapHold = nil
            if let rq = request { for b in held { rq.append(b) } }
            tapLock.unlock()
            if testTag != nil { testOut["replayed_ms"] = held.reduce(0.0) { $0 + Double($1.frameLength) / $1.format.sampleRate * 1000 }.rounded() }
        } else {
            tapLock.lock(); tapHold = nil; tapLock.unlock()
        }
    }

    /// The finalizing request's own result (its gen is finGen).
    func onFinalizing(_ res: RecResult?, _ err: Error?) {
        let t = res.map { $0.text.trimmingCharacters(in: .whitespacesAndNewlines) } ?? ""
        let isFinal = res?.isFinal ?? false
        if testTag != nil {          // synthetic test audio only: how the finishing request's words arrive after the turn end
            let since = finTurn.flatMap { $0.eot }.map { nowMs() - $0 } ?? -1
            testOut["fin_events"] = ((testOut["fin_events"] as? [String]) ?? []) + [String(format: "%+.0f ms %@ %@%@", since, isFinal ? "final" : "partial", t, err != nil ? " (error)" : "")]
        }
        guard let tr = finTurn else { return }
        if altTurn === tr && !altSent {
            let nt = stripEcho(t)
            if !nt.isEmpty { altApple = nt }
            if isFinal || err != nil { resumeListening(finGen) }
            return
        }
        if !isFinal && err == nil {
            // new words after the turn end: the lagging last word has arrived; send them now if nothing went yet
            let nt = stripEcho(t)
            if !nt.isEmpty && words(nt) != words(finPartial) {
                finPartial = nt
                if finSent == nil { finTimer?.cancel(); finTimer = nil; tr.info["stt"] = "late_partial"; sendFinal(nt, tr, spec: false) }
            }
            return
        }
        let text = t.isEmpty ? finPartial : stripEcho(t)
        finTimer?.cancel(); finTimer = nil
        if let sent = finSent {
            if words(sent) != words(text) && tr.answerAudio == nil && !text.isEmpty {
                tr.info["corrected"] = true
                vlog("final corrected the words sent early")
                emit(["type": "final_update", "text": text, "turn": tr.id])
                testRecord(final: text, turn: tr.id)
            }
        } else if !text.isEmpty {
            tr.info["stt"] = "final"
            sendFinal(text, tr, spec: false)
        }
        resumeListening(finGen)
    }

    func stripEcho(_ s: String) -> String {
        guard barged else { return s }
        var toks = s.split(separator: " ").map(String.init)
        while let f = toks.first, !words(f).isEmpty, words(f).allSatisfy({ spoken.contains($0) }) { toks.removeFirst() }
        let r = toks.joined(separator: " ")
        return r.isEmpty ? s : r
    }

    func sendFinal(_ text: String, _ tr: TurnRec, spec: Bool) {
        finSent = text
        tr.sttFinal = nowMs()
        if spec { specTurn = tr.id; specAt = Date() }
        vlog("final (\(words(text).count) words, smart\(spec ? ", sent early" : ""))")
        testRecord(final: text, turn: tr.id)
        emit(finalEvent(text, tr, spec: spec))
        if words(text).count <= 6 { speakerCheck(tr.id) }      // a short reply may be a confirmation: check the voice
        startWait(tr)
    }

    /// His words per second this turn against his running average -> paceMul for the answer (1.0-1.08: faster when he is). Too little speech to tell
    /// (under 4 words or 1.2 s) keeps the usual pace. The average starts from the brain's own (voice/jarvis_voice.json "wps").
    func updatePace(words n: Int, secs: Double) {
        paceMul = 1
        guard n >= 4, secs >= 1.2, testTag == nil else { return }
        if wpsN == 0, let d = FileManager.default.contents(atPath: "\(voiceDir)/jarvis_voice.json"),
           let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let w = (o["wps"] as? NSNumber)?.doubleValue, w > 0.5 {
            wpsBase = w; wpsN = 5
        }
        let wps = Double(n) / secs
        // only ever brisker: never below his own speech_rate (3 Oct: "way too slow"; he had asked for a brisker voice)
        if wpsN >= 5 { paceMul = Float(min(1.08, max(1.0, (wps / wpsBase).squareRoot()))) }
        wpsBase = wpsN == 0 ? wps : wpsBase * 0.9 + wps * 0.1
        wpsN += 1
    }

    /// The 'final' event for the page; test finals say so (and "quiet" ones must not reach the brain).
    func finalEvent(_ text: String, _ tr: TurnRec, spec: Bool) -> [String: Any] {
        var e: [String: Any] = ["type": "final", "text": text, "turn": tr.id, "spec": spec]
        tapLock.lock(); let vs = reqVoiced, vr = reqVoicedRMS; tapLock.unlock()
        // how he sounded: seconds and loudness; a laugh or sigh (Feel.swift); pitch and loudness of the turn (turn.py "pros")
        var v: [String: Any] = vs > 0.3 ? ["secs": (vs * 100).rounded() / 100, "rms": (vr / vs * 100000).rounded() / 100000] : [:]
        let fl = Feel.shared.take()
        if fl.laugh >= 0.3 { v["laugh"] = (fl.laugh * 100).rounded() / 100; tr.info["laugh"] = v["laugh"] }
        if fl.sigh >= 0.3 { v["sigh"] = (fl.sigh * 100).rounded() / 100; tr.info["sigh"] = v["sigh"] }
        if let p = tr.info["pros"] as? [String: Any] { v["pros"] = p }
        if !v.isEmpty { e["voice"] = v }
        updatePace(words: words(text).count, secs: vs)
        if paceMul != 1 { tr.info["pace"] = (Double(paceMul) * 1000).rounded() / 1000 }
        if testTag != nil { e["test"] = true; if testQuiet { e["quiet"] = true } }
        return e
    }

    /// turn.py's own transcript of the turn that just ended (settings.json "stt_engine").
    func onAltStt(_ text: String, ms: Double?) {
        guard let tr = altTurn, !altSent else { return }
        let t = stripEcho(text.trimmingCharacters(in: .whitespacesAndNewlines))
        if t.isEmpty { return }                              // nothing usable: Apple's words go at the timeout
        altSent = true
        if let m = ms { tr.info["stt_ms"] = m.rounded() }
        if testTag != nil { testOut["apple_text"] = altApple }   // synthetic test audio only: both engines' words
        finSent = nil
        sendFinal(t, tr, spec: false)
    }

    /// The second pass ("ears"): the utterance's audio so far, all at once, into SpeechAnalyzer; its final is the turn's text.
    /// The turn helper ended the turn after >= 224 ms of silence, so the audio already holds the last word.
    func earsPass(_ bufs: [AVAudioPCMBuffer], _ tr: TurnRec) {
        guard #available(macOS 26.0, *), let loc = Ears.locale, !bufs.isEmpty else { return }
        let t0 = Date()
        let er = EarsRequest(locale: loc, vocabulary: []) { [weak self] res, err in
            guard err != nil || (res?.isFinal ?? false) else { return }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.earsHold = nil
                let ms = Date().timeIntervalSince(t0) * 1000
                if let e = err { vlog(String(format: "ears: second pass failed after %.0f ms: %@", ms, e.localizedDescription)); return }
                let text = res?.text ?? ""
                vlog(String(format: "ears: second pass %.0f ms (%d words, %@)", ms, words(text).count, words(text) == words(self.altApple) ? "same as Apple" : "differs from Apple"))
                guard self.altTurn === tr else { return }
                self.onAltStt(text, ms: ms)
            }
        }
        earsHold = er                          // kept until its final: its reader holds it only weakly
        for b in bufs { er.append(b) }
        er.endAudio()
    }

    func altTimeout(_ tr: TurnRec) {
        guard altTurn === tr, !altSent else { return }
        altSent = true
        let who = (tr.info["stt"] as? String) == "ears" ? "the second pass" : sttEngine
        tr.info["stt"] = "apple_fallback"
        vlog("speech-to-text: \(who) gave nothing in 600 ms, Apple's words used")
        if !altApple.isEmpty { finSent = nil; sendFinal(altApple, tr, spec: false) }
    }

    // MARK: - barge-in (backchannel flag)

    static let interruptWords: Set<String> = ["stop", "wait", "hold", "no", "jarvis", "cancel", "pause", "enough", "hang", "sorry", "actually", "excuse", "quiet"]

    /// ≥ 250 ms of his voice AND words that are neither only a backchannel nor our own echo.
    func checkBarge2() {
        guard speaking || playing != nil else { return }
        let running = vadOn && (vadVoiced250 || Date().timeIntervalSince(vadRunStart) >= 0.25)
        let justEnded = !vadOn && vadVoiced250 && Date().timeIntervalSince(lastRunEnd) < 1.5   // "Wait, wait": words lag the voice
        guard running || justEnded else { return }
        let all = words(partial)
        let novel = all.filter { !spoken.contains($0) }
        let real = novel.filter { !VoiceLayer.backchannel.contains($0) && !VoiceLayer.backchannel.contains($0.replacingOccurrences(of: "-", with: "")) }
        guard !real.isEmpty else { return }
        if real.count < 2 && !real.contains(where: { VoiceLayer.interruptWords.contains($0) }) { return }
        if Double(novel.count) < 0.5 * Double(all.count) && real.count < 3 { return }     // mostly our own words: echo
        doBarge()
    }

    func doBarge() {
        vlog("barge-in")
        let t0 = Date()
        let heard = heardText()
        muteOutput()
        stopSpeaking()
        noteBarge(t0)
        barged = true
        emit(["type": "barge", "heard": heard])
        turnMode = "listen"; turnCmd(["mode": "listen"]); turnCmd(["reset": true, "preroll_ms": 600])
        showPartial()
    }

    /// The clauses he heard in full this turn, plus the playing one cut at the word it had reached.
    func heardText() -> String {
        var parts = heardTexts
        if let u = playing, playDur > 0 {
            let frac = min(1, max(0, Date().timeIntervalSince(playStart) - outLatency) / playDur)
            let w = u.text.split(separator: " ")
            let n = Int((Double(w.count) * frac).rounded(.down))
            if n > 0 { parts.append(w.prefix(n).joined(separator: " ") + (n < w.count ? "…" : "")) }
        }
        return String(parts.joined(separator: " ").prefix(600))
    }

    /// Silence now, including what the time-pitch unit still holds; the next line turns the sound back on.
    func muteOutput() {
        if let e = plainEngine { e.mainMixerNode.outputVolume = 0; muteUntilNext = true }
        if outIsMain && engine.isRunning { engine.mainMixerNode.outputVolume = 0; muteUntilNext = true }
    }

    func unmuteIfNeeded() {
        guard muteUntilNext else { return }
        muteUntilNext = false
        plainEngine?.attachedNodes.forEach { ($0 as? AVAudioUnitTimePitch)?.reset() }
        mainTP?.reset()
        plainEngine?.mainMixerNode.outputVolume = 1
        if engine.isRunning { engine.mainMixerNode.outputVolume = 1 }
    }

    // MARK: - openers + fillers (cached_openers flag): the app owns the timing of everything said while he waits

    static let openers = ["On it.", "Let me check.", "Sure.", "Hmm, one second.", "Right."]
    static let cues = ["Mm-hm.", "Right.", "Okay."]          // said softly in his pauses while he talks at length
    static var cachedLines: [String] { openers + cues }

    /// Openers/fillers in the current voice, kept in memory and on disk (voice/opener_cache/<voice>/<hash>.caf: synthesized
    /// speech only, a one-time cost per voice). Loads what is on disk at once; the rest is rendered by cacheStep(), one line at a
    /// time and only while Jarvis is idle, so a render never stands in front of a live answer.
    func buildCache(force: Bool = false) {
        guard flag("cached_openers"), !quitting else { return }
        let v = convVoice ?? currentVoice
        if v != cacheVoice || force { lineCache = [:]; cacheVoice = v }
        var loaded = 0
        for l in VoiceLayer.cachedLines where lineCache[l] == nil {
            let p = cacheFile(v, l)
            if FileManager.default.fileExists(atPath: p), let b = loadAudioFile(p, to: playFmt) { lineCache[l] = b; loaded += 1 }
        }
        if loaded > 0 { vlog("openers: \(lineCache.count)/\(VoiceLayer.cachedLines.count) in \(v) (from disk)") }
        cacheStep()
    }

    func cacheFile(_ v: String, _ text: String) -> String {
        var h: UInt64 = 1469598103934665603                  // FNV-1a: a stable name per line
        for c in text.utf8 { h ^= UInt64(c); h = h &* 1099511628211 }
        return "\(voiceDir)/opener_cache/" + v.filter { $0.isLetter || $0.isNumber || $0 == "_" || $0 == "-" } + "/\(String(h, radix: 16)).caf"
    }

    var cacheIdle: Bool {
        !awake && queue.isEmpty && playing == nil && !synthBusy && wait == nil && !enrolling && Date().timeIntervalSince(lastActivity) > 5
    }

    func cacheStep() {
        guard flag("cached_openers"), !cacheBuilding, !quitting, cacheIdle else { return }
        let v = cacheVoice
        guard !v.isEmpty, v == (convVoice ?? currentVoice) else { return }
        if v.hasPrefix("kokoro:") && kokoro.st != .ready { return }
        guard let l = VoiceLayer.cachedLines.first(where: { lineCache[$0] == nil }) else { return }
        cacheBuilding = true
        let t0 = Date()
        synthesize(l, v) { [weak self] b in
            guard let self = self else { return }
            self.cacheBuilding = false
            guard self.cacheVoice == v, let b = b else { return }
            self.lineCache[l] = b
            let p = self.cacheFile(v, l)
            try? FileManager.default.createDirectory(atPath: (p as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
            if let f = try? AVAudioFile(forWriting: URL(fileURLWithPath: p), settings: b.format.settings) { try? f.write(from: b) }
            if self.lineCache.count == VoiceLayer.cachedLines.count {
                vlog(String(format: "openers: %d lines ready in %@ (last one %.1f s)", self.lineCache.count, v, Date().timeIntervalSince(t0)))
            }
        }
    }

    func cachedBuffer(_ text: String, _ voice: String) -> AVAudioPCMBuffer? {
        guard flag("cached_openers"), voice == cacheVoice else { return nil }
        return lineCache[text.trimmingCharacters(in: .whitespaces)]
    }

    func startWait(_ tr: TurnRec) {
        heardTexts = []
        guard flag("cached_openers") else { return }
        wait = TurnWait(turn: tr.id, eot: Date(timeIntervalSince1970: (tr.eot ?? nowMs()) / 1000))
    }

    /// From the page: what the brain is doing now (tool use), for a spoken progress line.
    func onProgress(_ text: String, turn: String?) {
        guard let w = wait, turn == nil || turn == w.turn else { return }
        w.progress = String(text.prefix(60))
    }

    /// Every 0.2 s while a turn waits: one varied opener at eot + 500 ms, then at most one real progress line after 6 s.
    /// Fast paths and instant answers get none.
    func fillerTick(_ now: Date) {
        guard let w = wait else { return }
        guard flag("cached_openers"), let tr = turnCur, tr.id == w.turn, !tr.written, tr.answerAudio == nil, !tr.fastPath, !tr.pageDone,
              now.timeIntervalSince(w.eot) < 60 else { wait = nil; return }
        guard playing == nil, queue.isEmpty else { return }
        if vadOn { return }                                   // he is talking: say nothing
        if !w.openerDone {
            guard now.timeIntervalSince(w.eot) >= 0.5 else { return }
            w.openerDone = true
            var pick = VoiceLayer.openers.randomElement() ?? "Sure."
            while pick == lastOpener && VoiceLayer.openers.count > 1 { pick = VoiceLayer.openers.randomElement() ?? pick }
            lastOpener = pick
            speakLine(pick, kind: "opener", turn: w.turn)
            return
        }
        // Like a person waiting with him: the opener, then at most ONE line, and only when it says something real (what it is
        // reading now). No "two more seconds / almost there" loop (the owner, 3 Oct): after 20 s the page moves a slow question to
        // the background instead.
        guard w.narrations < 1, now.timeIntervalSince(w.eot) >= 6, now.timeIntervalSince(lastDrain) >= 1.5 else { return }
        let p0 = w.progress, generic = ["working on it", "digging into it"].contains(p0.lowercased())
        guard !p0.isEmpty, !generic, p0 != w.saidProgress else { return }
        w.narrations += 1; w.lastNarration = now; w.saidProgress = p0
        let p = p0.prefix(1).lowercased() + p0.dropFirst()
        enqueueSpeak(id: "fill-\(w.narrations)", text: "I'm \(p) now.", kind: "filler", turn: w.turn)
    }

    /// While he talks at length (10 s+ in this utterance), a soft "mm-hm" in one of his pauses, at most every 8 s, like a person
    /// listening (the owner, 3 Oct). Only in a conversation, never while Jarvis speaks, only from the cache (instant), played straight
    /// on the player like the chime (not the speech queue, so turn-taking is untouched). settings.json "listen_cues": false = off.
    func maybeCue() {
        guard flag("listen_cues"), awake, !vadOn, let us = uttStart, Date().timeIntervalSince(us) >= 10,
              Date().timeIntervalSince(lastCue) >= 8, !speaking, playing == nil, queue.isEmpty, wait == nil,
              engineOn, engine.isRunning, playerOnMain else { return }
        let v = convVoice ?? currentVoice
        var pick = VoiceLayer.cues.randomElement() ?? "Mm-hm."
        if lastCueText == pick { pick = VoiceLayer.cues.first { $0 != pick } ?? pick }
        guard let b = cachedBuffer(pick, v), let soft = AVAudioPCMBuffer(pcmFormat: b.format, frameCapacity: b.frameLength),
              let src = b.floatChannelData, let dst = soft.floatChannelData else { return }
        soft.frameLength = b.frameLength
        for ch in 0..<Int(b.format.channelCount) { for i in 0..<Int(b.frameLength) { dst[ch][i] = src[ch][i] * 0.6 } }
        lastCue = Date(); lastCueText = pick
        player.scheduleBuffer(soft, at: nil, options: [], completionHandler: nil)
        if !player.isPlaying { player.play() }
        vlog("listening cue: \(pick)")
    }

    /// A cached line if there is one (plays in well under 50 ms), else made now.
    func speakLine(_ text: String, kind: String, turn: String?) {
        enqueueSpeak(id: "\(kind)-\(Int(Date().timeIntervalSince1970 * 1000) % 1_000_000)", text: text, kind: kind, turn: turn)
    }

    /// Smooth joins: trim the silence a voice leaves at either end, 10 ms fade in, 15 ms fade out, then an even 150 ms gap.
    func polished(_ b: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        guard let src = b.floatChannelData?[0], b.frameLength > 0 else { return b }
        let n = Int(b.frameLength), rate = b.format.sampleRate
        var peak: Float = 0
        for i in 0..<n { peak = max(peak, abs(src[i])) }
        let gate = max(peak * 0.0056, 0.0005)                    // about -45 dB under the peak
        var a = 0, z = n - 1
        while a < n && abs(src[a]) < gate { a += 1 }
        while z > a && abs(src[z]) < gate { z -= 1 }
        let margin = Int(rate * 0.01)
        a = max(0, a - margin); z = min(n - 1, z + margin)
        let len = z - a + 1, pad = Int(rate * 0.15)
        guard len > 0, let c = AVAudioPCMBuffer(pcmFormat: b.format, frameCapacity: AVAudioFrameCount(len + pad)), let dst = c.floatChannelData?[0] else { return b }
        c.frameLength = AVAudioFrameCount(len + pad)
        let fin = min(len, Int(rate * 0.010)), fout = min(len, Int(rate * 0.015))
        for i in 0..<len {
            var g: Float = 1
            if i < fin { g = Float(i) / Float(max(fin, 1)) }
            if i >= len - fout { g = min(g, Float(len - 1 - i) / Float(max(fout, 1))) }
            dst[i] = src[a + i] * g
        }
        for i in len..<(len + pad) { dst[i] = 0 }
        return c
    }

    // MARK: - lexicon + first clause (lexicon flag)

    func lexiconWords() -> [String: String] {
        let p = "\(voiceDir)/lexicon.json"
        let m = ((try? FileManager.default.attributesOfItem(atPath: p))?[.modificationDate] as? Date) ?? .distantPast
        if m != lexiconMTime { lexiconMTime = m; lexiconCache = SpeechText.loadLexicon(p) }
        return lexiconCache
    }

    func forEar(_ text: String) -> String { flag("lexicon") ? SpeechText.normalize(text, lexicon: lexiconWords()) : text }

    /// The first sentence of an answer starts with a short clause (<= 6 words) so the first sound comes sooner; the cut is at a
    /// comma when there is one, else before a joining word, else after the 5th word of a long sentence.
    func firstClause(_ t: String) -> [String] {
        let w = t.split(separator: " ").map(String.init)
        guard w.count > 8 else { return [t] }
        for i in 1..<min(6, w.count - 2) where w[i - 1].hasSuffix(",") || w[i - 1].hasSuffix("—") || w[i - 1].hasSuffix(";") {
            return [w[0..<i].joined(separator: " "), w[i...].joined(separator: " ")]
        }
        let joins: Set<String> = ["and", "but", "so", "which", "because", "that", "so,"]
        for i in stride(from: min(6, w.count - 3), through: 3, by: -1) where joins.contains(w[i].lowercased()) {
            return [w[0..<i].joined(separator: " "), w[i...].joined(separator: " ")]
        }
        if w.count >= 12 { return [w[0..<5].joined(separator: " "), w[5...].joined(separator: " ")] }
        return [t]
    }

    // MARK: - speaking up on its own (3 Oct, brain item 4)
    // What was learned: interruptions cost least at natural breaks between tasks; an assistant that speaks only when the
    // thought is relevant and timely is preferred (CHI 2025 "Inner Thoughts": 82%); Alexa+ testers disliked chattiness.
    // So every line has a priority:
    //   urgent  work blocked or due now (connect the VPN, a reminder): the first quiet moment
    //   asked   what he asked for (a task he started, a background answer): a natural break, or after 45 s at a quiet moment
    //   fyi     news he did not ask for (a new decision or approval; the "proactive" switch): only at a natural break while he
    //           is at the Mac, at most once per 10 minutes, everything waiting said together in one line
    // Never while he talks or Jarvis speaks. Unsaid lines become a caption (urgent/asked after 5 minutes, fyi after 30).

    func proactive(_ text: String) {
        guard flag("proactive") else { vlog("proactive: off, not said"); return }
        saySoon(text, prio: "fyi")
    }

    func saySoon(_ text: String, prio: String = "asked") {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, t.count <= 400 else { return }
        soonQ.append((t, Date(), ["urgent", "asked", "fyi"].contains(prio) ? prio : "asked"))
        soonTick()
    }

    /// A natural break: he paused typing and the mouse 4 s to 10 min ago, switched apps in the last 8 s, or Jarvis finished
    /// talking to him in the last 30 s.
    func atBreakpoint(_ now: Date) -> Bool {
        let idle = Office.shared.idleSecs
        return (idle >= 4 && idle < 600) || now.timeIntervalSince(Office.shared.lastAppSwitch) < 8 || now.timeIntervalSince(lastDrain) < 30
    }

    func soonTick() {
        guard !soonQ.isEmpty else { return }
        let now = Date()
        soonQ.removeAll { q in
            guard now.timeIntervalSince(q.at) > (q.prio == "fyi" ? 1800 : 300) else { return false }
            emit(["type": "caption", "text": q.text]); return true
        }
        let talking = vadOn || (recognizing && !partial.isEmpty) || pttHeld || Date().timeIntervalSince(partialAt) < 2.5
        guard !soonQ.isEmpty, !talking, !speaking, playing == nil, queue.isEmpty, !enrolling, !training, wait == nil, !busy else { return }
        let bp = atBreakpoint(now)
        var line: String?, kind = ""
        if let i = soonQ.firstIndex(where: { $0.prio == "urgent" }) {
            line = soonQ.remove(at: i).text; kind = "urgent"
        } else if let i = soonQ.firstIndex(where: { $0.prio == "asked" && (bp || now.timeIntervalSince($0.at) > 45) }) {
            line = soonQ.remove(at: i).text; kind = "asked"
        } else if bp, Office.shared.idleSecs < 600, now.timeIntervalSince(lastProactive) >= 600, flag("proactive") {
            let fyi = soonQ.filter { $0.prio == "fyi" }.map { $0.text }
            if !fyi.isEmpty {
                soonQ.removeAll { $0.prio == "fyi" }
                lastProactive = now; kind = "fyi"
                line = fyi.count == 1 ? fyi[0] : fyiDigest(fyi)
                emit(["type": "proactive_said", "text": line ?? ""])
            }
        }
        guard let t = line else { return }
        vlog("say soon (\(kind)\(bp ? ", at a break" : "")): said (\(t.split(separator: " ").count) words)")
        enqueueSpeak(id: "soon-\(Int(now.timeIntervalSince1970 * 1000) % 1_000_000)", text: t, kind: "other")
    }

    /// Several fyi lines as one: "Two things. X needs your decision. And a new approval is waiting: Y."
    func fyiDigest(_ lines: [String]) -> String {
        let strip = { (s: String) -> String in
            var t = s.trimmingCharacters(in: .whitespaces)
            for p in ["Sir, ", "sir, "] where t.hasPrefix(p) { t = String(t.dropFirst(p.count)) }
            return t.prefix(1).uppercased() + t.dropFirst()
        }
        let n = ["", "One", "Two", "Three"], shown = Array(lines.prefix(3)).map(strip)
        var out = "\(lines.count <= 3 ? n[lines.count] : "A few") things. " + shown[0]
        for s in shown.dropFirst() { out += " And " + s.prefix(1).lowercased() + s.dropFirst() }
        if lines.count > 3 { out += " And \(lines.count - 3) more in your inbox." }
        return out
    }
}
