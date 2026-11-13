// Listening: the listening switch and the audio engine (voice processing, bypass, the tap), the wake word, push-to-talk
// and speech recognition.

import AppKit
import WebKit
import AVFoundation
import Speech

extension VoiceLayer {
    // MARK: listening switch + engine

    func setListening(_ on: Bool) {
        listening = on
        UserDefaults.standard.set(on, forKey: "JarvisListening")
        VoiceMenu.shared.sync(on)
        vlog("listening \(on)")
        if !on && awake && !pttActive { goSleep("command") }
        updateEngine()
        emitState()
    }

    func updateEngine() {
        let want = (listening || enrolling || pttActive || (testEngine && testTag != nil)) && micStatus == "granted" && !quitting
        if want { startEngine() } else { stopEngine() }
        updateTap()
    }

    func updateTap() {
        let turnFeed = turnWanted ? turnProc : nil
        tapLock.lock()
        tapWake = (engineOn && listening && !enrolling && wakeReady) ? wake : nil
        tapRequest = recognizing ? request : nil
        tapTurn = turnFeed
        tapLock.unlock()
        syncTurnMode()
    }


    func startEngine() {
        guard !engineOn, !quitting else { return }
        let all = Array(0..<VoiceLayer.engineModes.count)
        let order = engineMode >= 0 ? [engineMode] + all.filter { $0 != engineMode } : all
        for m in order where tryEngine(m) {
            engineMode = m
            engineOn = true
            engineStartedAt = Date()
            if activity == nil { activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiatedAllowingIdleSystemSleep], reason: "Listening") }
            updateTap()
            emitState()
            return
        }
        emitError("The microphone could not start.")
    }

    func tryEngine(_ m: Int) -> Bool {
        let e = AVAudioEngine(), p = AVAudioPlayerNode()
        Office.shared.applyInput(to: e)
        let vp = m <= 3, withPlayer = m != 3
        let ec = flag("echo_cancel") && m == 0          // experiment: speech through this engine (player -> time-pitch -> mixer)
        var tp: AVAudioUnitTimePitch?
        if m == 0 {
            e.attach(p)
            if ec {
                let t = AVAudioUnitTimePitch(); t.rate = speechRate; e.attach(t)
                e.connect(p, to: t, format: playFmt); e.connect(t, to: e.mainMixerNode, format: playFmt); tp = t
            } else { e.connect(p, to: e.mainMixerNode, format: playFmt) }
        }
        let input = e.inputNode
        if vp {
            do { try input.setVoiceProcessingEnabled(true) } catch { vlog("mode \(m): voice processing refused: \(error)"); return false }
            if #available(macOS 14.0, *) {
                input.voiceProcessingOtherAudioDuckingConfiguration =
                    AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: ObjCBool(!ec), duckingLevel: .min)
            }
            // never let Apple's automatic gain control move the Mac's input volume: it kept pulling it down to ~27-29 %, so
            // his voice reached Jarvis too quietly to enrol (2 Oct 19:23, 100 % -> 27 % when voice processing switched on)
            input.isVoiceProcessingAGCEnabled = false
        }
        if m == 1 || m == 2 || m == 4 { e.attach(p); e.connect(p, to: e.mainMixerNode, format: playFmt) }
        if m == 2 {
            let rate = e.outputNode.outputFormat(forBus: 0).sampleRate
            if rate > 0, let mf = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1) { e.connect(e.mainMixerNode, to: e.outputNode, format: mf) }
        }
        let inFmt = input.outputFormat(forBus: 0)
        guard inFmt.sampleRate > 0, inFmt.channelCount > 0, installTap(on: input, format: inFmt) else {
            vlog("mode \(m): no usable input format (\(inFmt))"); return false
        }
        e.prepare()
        do { try e.start() } catch {
            vlog("mode \(m) (\(VoiceLayer.engineModes[m])) failed: \((error as NSError).code)")
            input.removeTap(onBus: 0)
            e.stop()
            return false
        }
        vlog("engine: mode \(m) (\(VoiceLayer.engineModes[m])), input \(inFmt.channelCount) ch @ \(Int(inFmt.sampleRate)) Hz, VP=\(vp)" +
             (ec ? ", speech through it (echo_cancel), output \(e.outputNode.outputFormat(forBus: 0))" : ""))
        engine = e; player = p; vpActive = vp; playerOnMain = withPlayer
        mainTP = tp; echoCancelBuilt = ec
        bypassed = false
        return true
    }

    func installTap(on input: AVAudioInputNode, format inFmt: AVAudioFormat) -> Bool {
        guard let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: inFmt.sampleRate, channels: 1, interleaved: false),
              let conv = AVAudioConverter(from: mono, to: fmt16) else { return false }
        let f16 = fmt16
        var warned = false
        tapRate = inFmt.sampleRate
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 2048, format: inFmt) { [weak self] buf, _ in
            guard let self = self else { return }
            let n = Int(buf.frameLength)
            guard n > 0, let src = buf.floatChannelData else {
                if !warned { warned = true; vlog("tap: unexpected input format \(buf.format)") }
                return
            }
            guard let m = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: AVAudioFrameCount(n)), let dst = m.floatChannelData?[0] else { return }
            m.frameLength = AVAudioFrameCount(n)
            if buf.format.isInterleaved {
                let st = Int(buf.format.channelCount)
                for i in 0..<n { dst[i] = src[0][i * st] }
            } else {
                memcpy(dst, src[0], n * MemoryLayout<Float>.size)       // channel 0 of the voice-processed input
            }
            self.tapLock.lock(); let test = self.tapTest; self.tapLock.unlock()
            if test { return }                     // a test file is feeding the pipeline instead of the mic
            self.processMono(m, conv, f16)
        }
        return true
    }

    /// Everything downstream of the mic, for one mono float buffer at the tap's rate: recognizer, energy VAD, 16 kHz int16 for
    /// the helpers. Called on the audio thread (or the test feeder's queue while a test file stands in for the mic).
    func processMono(_ m: AVAudioPCMBuffer, _ conv: AVAudioConverter, _ f16: AVAudioFormat) {
        let n = Int(m.frameLength)
        guard n > 0, let dst = m.floatChannelData?[0] else { return }
        var acc: Float = 0
        for i in 0..<n { acc += dst[i] * dst[i] }
        let rms = (acc / Float(n)).squareRoot()
        let now = Date()
        tapLock.lock()
        let rq = tapRequest, wk = tapWake, rec = tapRec, tt = tapTurn, fq = tapFinReq
        let holding = tapHold != nil         // between a turn end and the next request: keep it for the replay, in order
        if holding, tapHold!.count < 200, let c = copyBuffer(m) { tapHold!.append(c) }
        tapRMS = rms
        // energy VAD: an adaptive floor (falls at once, rises slowly); voiced = clearly above it
        if rms < vadFloor { vadFloor = max(rms, 0.00005) } else { vadFloor = min(vadFloor * 1.002 + 0.0000002, 0.02) }
        let voiced = rms > max(vadFloor * 3.5, 0.0015)
        if voiced && rq != nil { reqVoiced += Double(n) / m.format.sampleRate; reqVoicedRMS += Double(rms) * Double(n) / m.format.sampleRate }
        if voiced {
            if !vadVoiced && now.timeIntervalSince(vadLastVoice) > 0.3 { vadOnset = now }
            vadLastVoice = now
        }
        vadVoiced = voiced
        if echoMeter && !tapTest { echoSum += Double(rms); echoN += 1 }
        let feel = (rq != nil || fq != nil) && !echoMeter        // his voice only: never while Jarvis speaks
        tapLock.unlock()
        if feel, let c = copyBuffer(m) { Feel.shared.feed(c) }
        if !holding { rq?.append(m) }
        fq?.append(m)
        let cap = AVAudioFrameCount(Double(n) * 16000 / m.format.sampleRate) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: f16, frameCapacity: cap) else { return }
        var given = false
        var err: NSError?
        _ = conv.convert(to: out, error: &err) { _, s in
            if given { s.pointee = .noDataNow; return nil }
            given = true; s.pointee = .haveData; return m
        }
        let k = Int(out.frameLength)
        guard k > 0, let p = out.int16ChannelData?[0] else { return }
        wk?.feed(Data(bytes: p, count: k * 2))
        tt?.feed(VoiceLayer.turnFrame(p, k))
        if rq != nil {                                   // his utterance, for the speaker check of a short "yes" (Speaker.swift)
            tapLock.lock()
            if uttPCM.count < 16000 * 8 { uttPCM.append(contentsOf: UnsafeBufferPointer(start: p, count: k)) }
            tapLock.unlock()
        }
        if let r = rec, r.add(p, k) { DispatchQueue.main.async { self.endRecording(r) } }
    }

    func updateBypass() {
        guard engineOn, vpActive, !bypassBroken else { return }
        let want = !awake && !speaking && playing == nil && !pttActive
        guard want != bypassed else { return }
        if want && Date().timeIntervalSince(engineStartedAt) < 2 {
            // let a freshly started device settle first, so its own reconfiguration is never blamed on the bypass
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.1) { [weak self] in self?.updateBypass() }
            return
        }
        engine.inputNode.isVoiceProcessingBypassed = want
        bypassed = want
        bypassToggledAt = Date()
        vlog("voice processing \(want ? "bypassed (idle)" : "active")")
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self = self, self.engineOn else { return }
            self.tapLock.lock(); let r = self.tapRMS; self.tapLock.unlock()
            vlog(String(format: "input level %.5f (bypassed=%@)", r, self.bypassed ? "yes" : "no"))
        }
    }

    func stopEngine() {
        guard engineOn else { return }
        stopRecognition()
        if outIsMain && (playing != nil || !queue.isEmpty) { stopSpeaking() }
        if playerOnMain { player.stop() }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        engineOn = false
        testEngine = false
        if let a = activity { ProcessInfo.processInfo.endActivity(a); activity = nil }
        vlog("engine stopped")
        updateTap()
        emitState()
    }

    /// The engine stops itself when the device's channel count or rate changes (voice processing does this to itself
    /// right after a start: 7 <-> 9 channels here). Nodes stay connected, so restart the SAME engine with a tap in the
    /// new input format; a fresh engine would only reconfigure the device again. Rebuild only if that fails.
    func engineConfigChanged() {
        guard engineOn, !quitting else { return }
        let e = engine
        let inFmt = e.inputNode.outputFormat(forBus: 0)
        vlog("audio configuration changed (running=\(e.isRunning), input now \(inFmt.channelCount) ch @ \(Int(inFmt.sampleRate)) Hz)")
        if Date().timeIntervalSince(bypassToggledAt) < 1.5 && !bypassBroken {
            bypassStrikes += 1
            if bypassStrikes >= 2 {
                bypassBroken = true
                vlog("bypass toggling reconfigures the audio; voice processing stays on from now")
                if bypassed { e.inputNode.isVoiceProcessingBypassed = false; bypassed = false }
            }
        }
        configRestarts = configRestarts.filter { Date().timeIntervalSince($0) < 60 } + [Date()]
        if configRestarts.count > 10 {
            stopEngine()
            emitError("The audio device keeps changing; listening is paused.")
            return
        }
        if outIsMain && (playing != nil || !queue.isEmpty) { stopSpeaking() }   // the player stopped with the engine
        if e.isRunning { return }
        let oldRate = tapRate
        e.inputNode.removeTap(onBus: 0)
        if inFmt.sampleRate > 0, inFmt.channelCount > 0, installTap(on: e.inputNode, format: inFmt) {
            e.prepare()
            do {
                try e.start()
                engineStartedAt = Date()
                bypassed = vpActive && e.inputNode.isVoiceProcessingBypassed
                vlog("engine restarted in place")
                if recognizing && inFmt.sampleRate != oldRate { startRecognition() }   // the request's format changed
                updateTap()
                return
            } catch {
                vlog("in-place restart failed: \((error as NSError).code)")
            }
        }
        let wasRecognizing = recognizing
        stopEngine()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            self.updateEngine()
            if wasRecognizing && self.awake && self.engineOn { self.startRecognition() }
        }
    }

    // MARK: wake word

    func startWake() {
        guard wake == nil, !quitting else { return }
        let p = LineProcess(voicePython, ["\(voiceCode)/wake.py"])
        p.onLine = { [weak self] l in self?.wakeLine(l) }
        p.onExit = { [weak self] in self?.wakeExited() }
        wakeReady = false
        if p.start() { wake = p } else { emitError("The wake-word helper could not start.") }
    }

    func wakeLine(_ l: String) {
        let parts = l.split(separator: " ").map(String.init)
        guard let head = parts.first else { return }
        if head == "READY" {
            wakeReady = true
            wakeModel = parts.count > 1 ? parts[1] : "hey_jarvis"
            vlog("wake helper ready (\(wakeModel))")
            updateTap(); emitState()
        } else if head == "WAKE" {
            onWakeWord(parts.count > 2 ? parts[2] : "")
        }
    }

    func wakeExited() {
        vlog("wake helper exited")
        wake = nil; wakeReady = false
        updateTap()
        wakeExits += 1
        if quitting { return }
        if wakeExits <= 5 { DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.startWake() } }
        else { emitError("The wake-word helper keeps stopping.") }
    }

    func restartWake() {
        if let w = wake { wake = nil; wakeReady = false; updateTap(); w.stop() }
        wakeExits = 0
        startWake()
    }

    func onWakeWord(_ score: String) {
        guard listening, engineOn, !awake, !speaking, !recognizing, !enrolling, !pttActive else { return }
        if Office.shared.inCall { vlog("wake word \(score) ignored: on a call"); return }     // never answer something said in a call
        vlog("wake word \(score)")
        beginUtterance(via: "word")
    }

    func playChime() {
        if chime == nil, let b = loadAudioFile("/System/Library/Sounds/Tink.aiff", to: playFmt) { chime = padded(b, 0, gain: 0.3) }
        if engineOn, engine.isRunning, playerOnMain, let c = chime {
            player.scheduleBuffer(c, at: nil, options: [], completionHandler: nil)
            if !player.isPlaying { player.play() }
        } else if let s = NSSound(named: "Tink") {
            s.volume = 0.3; s.play()
        }
    }

    func beginUtterance(via: String) {
        playChime()
        awakeVia = via
        if !awake && convVoice == nil { convVoice = currentVoice }     // one voice per conversation
        awake = true
        turnCmd(["reset": true])
        lastActivity = Date()
        startRecognition()
        emit(["type": "wake", "via": via])
        emitState()
    }

    // MARK: push-to-talk

    func pttKeyDown() {
        if !listening { setListening(true) }
        pttHeld = true
        pttStart()
    }

    func pttKeyUp() {
        pttHeld = false
        if recognizing { finishUtterance() }
    }

    func pttStart() {
        if enrolling { return }
        vlog(pttHeld ? "push-to-talk (Option+Space)" : "push-to-talk (page)")
        if speaking || playing != nil || !queue.isEmpty { stopSpeaking(); emit(["type": "barge"]) }
        if !listening { pttActive = true }
        updateEngine()
        guard engineOn else {
            pttActive = false; pttHeld = false
            emitError(micStatus == "granted" ? "The microphone could not start." : "Jarvis needs microphone access (System Settings > Privacy & Security > Microphone).")
            return
        }
        stopRecognition()
        beginUtterance(via: "ptt")
    }

    func pttStop() {
        pttHeld = false
        if recognizing { finishUtterance() }
    }

    // MARK: recognition

    func makeRecognizer() {
        for id in ["en-IN", "en-US"] {
            if let r = SFSpeechRecognizer(locale: Locale(identifier: id)), r.supportsOnDeviceRecognition {
                recognizer = r; vlog("recognizer \(id) (on device)"); return
            }
        }
        recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
        vlog("recognizer: no on-device locale found")
    }

    func startRecognition() {
        stopRecognition()
        guard engineOn, !quitting else { return }
        guard speechStatus == "granted" else {
            emitError("Jarvis needs speech recognition access (System Settings > Privacy & Security > Speech Recognition).")
            return
        }
        // better ears (Ears.swift): the old recognizer still gives the live words (it is quick, and turn-taking needs them);
        // with "ears" on, the request's audio is also kept, and at the turn end SpeechAnalyzer reads the whole utterance in
        // ~0.1 s and its text is the turn (finishSmart). Streaming SpeechAnalyzer live was tried first: words 1.6-4 s late.
        if flag("ears") && !Ears.ready { Ears.prepare { vlog($0) } }
        if recognizer == nil || !(recognizer!.supportsOnDeviceRecognition) { makeRecognizer() }
        guard let r = recognizer, r.isAvailable else { emitError("On-device speech recognition is not available right now."); return }
        let rq = SFSpeechAudioBufferRecognitionRequest()
        rq.requiresOnDeviceRecognition = true
        rq.shouldReportPartialResults = true
        rq.addsPunctuation = true
        rq.contextualStrings = vocabulary()
        recGen += 1
        let g = recGen
        partial = ""; partialAt = Date(); reqStart = Date(); partialShown = true; barged = false
        tapLock.lock(); reqVoiced = 0; reqVoicedRMS = 0; uttPCM.removeAll(keepingCapacity: true); tapLock.unlock()
        if !speaking { spoken.removeAll() }
        request = flag("ears") && Ears.ready ? TeeRequest(rq) : rq
        recognizing = true
        task = r.recognitionTask(with: rq) { [weak self] res, err in
            let rr = res.map { RecResult(text: $0.bestTranscription.formattedString, isFinal: $0.isFinal) }
            DispatchQueue.main.async { self?.onRecognition(g, rr, err) }
        }
        updateTap()
    }

    func stopRecognition() {
        guard recognizing || task != nil else { return }
        recGen += 1
        recognizing = false
        let rq = request, t = task
        request = nil; task = nil
        updateTap()
        rq?.endAudio()
        t?.cancel()
    }

    func onRecognition(_ g: Int, _ res: RecResult?, _ err: Error?) {
        if finGen >= 0 && g == finGen { onFinalizing(res, err); return }    // the request finishing a turn (smart_turn)
        guard g == recGen, recognizing else { return }
        if let res = res {
            let t = res.text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty && t != partial {
                partial = t; partialAt = Date(); lastActivity = Date()
                recErrors.removeAll()
                if speaking {
                    partialShown = false          // probably our own voice; shown only if it turns out not to be
                    checkBarge()
                } else {
                    showPartial()
                }
            }
            if res.isFinal {
                if speaking && !barged { startRecognition() } else { finishUtterance() }
                return
            }
        }
        if let e = err as NSError? {
            vlog("recognition ended (\(e.domain) \(e.code))")
            // only failures right after a start mean the recognizer is broken; later ones are ordinary endings
            if Date().timeIntervalSince(reqStart) < 1.5 { recErrors = recErrors.filter { Date().timeIntervalSince($0) < 20 } + [Date()] }
            if !partial.isEmpty && !speaking { finishUtterance(); return }
            stopRecognition()
            if recErrors.count >= 5 { emitError("Speech recognition keeps failing."); if awake { goSleep("quiet") }; return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                if self.awake && self.engineOn && !self.recognizing { self.startRecognition() }
            }
        }
    }

    func cleanedPartial() -> String {
        guard barged else { return partial }
        // after a barge-in, drop the leading words that were Jarvis's own (echo)
        var toks = partial.split(separator: " ").map(String.init)
        while let f = toks.first, !words(f).isEmpty, words(f).allSatisfy({ spoken.contains($0) }) { toks.removeFirst() }
        let s = toks.joined(separator: " ")
        return s.isEmpty ? partial : s
    }

    func showPartial() {
        partialShown = true
        emit(["type": "partial", "text": cleanedPartial()])
    }

    func novelWords(_ s: String) -> (all: Int, novel: Int) {
        let w = words(s)
        return (w.count, w.filter { !spoken.contains($0) }.count)
    }

    func isEcho(_ s: String) -> Bool {
        let (all, novel) = novelWords(s)
        return all == 0 || novel == 0 || Double(novel) < 0.5 * Double(all)
    }

    func checkBarge() {
        if flag("backchannel") && turnReady { checkBarge2(); return }
        let (all, novel) = novelWords(partial)
        guard all >= 3, novel >= 3, Double(novel) >= 0.6 * Double(all) else { return }
        vlog("barge-in")
        let t0 = Date()
        stopSpeaking()
        noteBarge(t0)
        barged = true
        emit(["type": "barge"])
        showPartial()
    }

    /// Metrics for a barge-in: decision -> playback stopped (incl. output latency), and his voice onset -> stopped.
    func noteBarge(_ decided: Date) {
        // with the output muted at once the only delay left is the device's own latency; else the whole output path's
        let stopped = Date().addingTimeInterval(muteUntilNext ? (plainEngine?.outputNode.presentationLatency ?? outLatency) : outLatency)
        guard let tr = turnCur, !tr.written else { return }
        tr.barge = true
        tr.bargeStopMs = (stopped.timeIntervalSince(decided) * 1000).rounded()
        tapLock.lock(); var onset = vadOnset; tapLock.unlock()
        if testTag != nil, let fs = feedVoiceStart { onset = fs } else if turnReady && vadOn { onset = vadRunStart }
        if stopped.timeIntervalSince(onset) < 10 { tr.bargeOnsetMs = (stopped.timeIntervalSince(onset) * 1000).rounded() }
        bargeTurn = tr; bargeAt = Date()
    }

    // + what the recognizer makes of "mm-hmm" ("Eminem", "mhmm") and other one-sound acknowledgements
    static let backchannel: Set<String> = ["yeah", "yes", "yep", "mm", "mhm", "mmhmm", "mm-hmm", "uh-huh", "uhhuh", "uh", "huh", "right", "okay", "ok", "sure", "hmm", "hm", "mmm", "ah", "oh", "aha",
                                           "eminem", "mhmm", "mmhm", "hmhm", "hmmm", "yup", "ya", "yah", "alright", "um", "umm", "ahh"]
    /// Only backchannel words ("yeah", "mm-hmm", "right" ...), or nothing at all.
    func onlyBackchannel(_ s: String) -> Bool {
        let w = s.lowercased().components(separatedBy: CharacterSet.letters.union(CharacterSet(charactersIn: "-")).inverted).filter { !$0.isEmpty }
        return w.allSatisfy { VoiceLayer.backchannel.contains($0) || VoiceLayer.backchannel.contains($0.replacingOccurrences(of: "-", with: "")) }
    }
    /// Said while Jarvis spoke and not a barge-in: only backchannel words, or one lone word that is not "stop"/"wait"/... (the
    /// recognizer turns a murmur into a random word). Such a thing is never sent to the brain as a question.
    func backchannelDuringSpeech(_ s: String) -> Bool {
        if onlyBackchannel(s) { return true }
        let w = words(s)
        return w.count == 1 && !VoiceLayer.interruptWords.contains(w[0])
    }

    /// Words the recognizer should expect: the built-in ones + voice/vocab.json ("words": [...]) + the lexicon's spellings
    /// (people in the round-ups, the owner's own words). At most 100 (Apple's advice).
    func vocabulary() -> [String] {
        var out = ["Jarvis", "Jira", "Slack", "Confluence", "inbox", "approve", "decline"]
        if let d = FileManager.default.contents(atPath: "\(voiceDir)/vocab.json"),
           let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any], let w = o["words"] as? [String] { out += w }
        out += lexiconWords().keys.sorted()
        var seen = Set<String>()
        return Array(out.filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }.prefix(100))
    }

    /// Opens the metrics record of a new turn (closing the previous one). Returns its id.
    func openTurn(eot: Date, sttFinal: Date, userStop: Date? = nil) -> TurnRec {
        if let old = turnCur, !old.written { closeTurn(old) }
        let tr = TurnRec(id: "t" + String(Int(Date().timeIntervalSince1970 * 1000) % 100_000_000))
        tr.eot = ms(eot); tr.sttFinal = ms(sttFinal)
        tapLock.lock(); let lastVoice = vadLastVoice; tapLock.unlock()
        // his last voiced frame: exact for a test clip, else the turn helper's (Silero), else the energy VAD's
        if let fe = feedVoiceEnd, eot.timeIntervalSince(fe) < 15 { tr.userStop = ms(fe); tr.src = "test"; feedVoiceEnd = nil }
        else if testTag != nil { tr.src = "test"; tr.userStop = ms(userStop ?? lastVoice) }
        else if let u = userStop { tr.userStop = ms(u) }
        else if eot.timeIntervalSince(lastVoice) < 6 { tr.userStop = ms(lastVoice) }
        else { tr.userStop = ms(partialAt) }
        tr.info["endpoint"] = "silence"
        for k in VoiceLayer.flagNames { tr.info["f_" + k] = flag(k) }
        turnCur = tr
        return tr
    }

    func closeTurn(_ tr: TurnRec) {
        guard !tr.written else { return }
        tr.written = true
        if tr.answerAudio != nil {
            vlog(String(format: "player gaps in the answer: %d (%.0f ms)", (tr.info["gaps"] as? Int) ?? 0, (tr.info["gap_ms"] as? Double) ?? 0))
        }
        writeMetric(tr.json())
        if testTag != nil { testOut["turn"] = tr.json() }
    }

    func finishUtterance() {
        let text = cleanedPartial().trimmingCharacters(in: .whitespacesAndNewlines)
        stopRecognition()
        if let bt = bargeTurn {          // what came after a barge-in decides whether it was a false one
            if text.isEmpty || onlyBackchannel(text) || isEcho(text) { bt.falseBarge = true }
            bargeTurn = nil
        }
        if !text.isEmpty {
            lastActivity = Date()
            vlog("final (\(words(text).count) words)")
            let now = Date()
            let tr = openTurn(eot: now, sttFinal: now)
            if pttHeld || pttActive { tr.info["endpoint"] = "ptt" }
            testRecord(final: text, turn: tr.id)
            emit(finalEvent(text, tr, spec: false))
            if words(text).count <= 6 { speakerCheck(tr.id) }
            startWait(tr)
        }
        if !text.isEmpty { turnCmd(["reset": true]) }
        if pttActive && !listening {
            // one-shot push-to-talk while listening is off: hand the mic back
            pttActive = false; pttHeld = false
            if awake { awake = false; emit(["type": "sleep", "reason": "ptt_done"]) }
            updateEngine(); emitState()
            return
        }
        if awake && engineOn { startRecognition() }   // conversation mode: follow-ups need no wake word
    }

    func goSleep(_ reason: String) {
        guard awake else { return }
        vlog("sleep (\(reason))")
        awake = false
        convVoice = nil; wait = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.buildCache() }   // a voice chosen meanwhile
        stopRecognition()
        pttActive = false; pttHeld = false
        emit(["type": "sleep", "reason": reason])
        updateEngine()
        emitState()
    }

    func tick() {
        if quitting { return }
        let now = Date()
        if engineOn { tapLock.lock(); let lvl = tapRMS; tapLock.unlock(); Office.shared.noteLevel(Double(lvl)) }
        pollTestCmd()
        if let tr = turnCur, !tr.written, let e = tr.eot {
            let eot = Date(timeIntervalSince1970: e / 1000)
            // cut-off: he started again within 1.5 s of the turn end, before any of Jarvis's audio (so it is not our own echo)
            if !tr.cutoff && !turnActive && now.timeIntervalSince(eot) < 3 {
                tapLock.lock(); let on = vadOnset; tapLock.unlock()
                let gap = on.timeIntervalSince(eot)
                let beforeAudio = tr.firstAudio.map { on.timeIntervalSince1970 * 1000 < $0 } ?? true
                if gap > 0.05 && gap <= 1.5 && beforeAudio { tr.cutoff = true; vlog(String(format: "cut-off: he went on %.0f ms after the turn end", gap * 1000)) }
            }
            // written once the answer is complete and has been heard (2 s of quiet after it), or after 90 s at the latest (deep answers)
            let quiet = playing == nil && queue.isEmpty && !synthBusy && now.timeIntervalSince(lastDrain) > 2
            if (tr.pageDone && quiet && bargeTurn !== tr && now.timeIntervalSince(eot) > 2) || now.timeIntervalSince(eot) > 90 { closeTurn(tr) }
        }
        if let bt = bargeTurn, now.timeIntervalSince(bargeAt) > 10 { bt.falseBarge = true; bargeTurn = nil }   // a barge-in that said nothing
        fillerTick(now)
        soonTick()
        if flag("cached_openers") {
            // openers are rendered one at a time and only while nobody is talking (never in front of a live answer)
            if cacheVoice != (convVoice ?? currentVoice) && !awake { if now.timeIntervalSince(lastCacheTry) > 2 { lastCacheTry = now; buildCache() } }
            else { cacheStep() }
        }
        if recognizing {
            let age = now.timeIntervalSince(reqStart)
            if !partial.isEmpty {
                // the fixed silence endpoint; with smart_turn the turn helper decides and this is only a 3 s safety net
                let endAfter = flag("smart_turn") && turnReady && tapTurn != nil ? 3.0 : 1.0
                if !speaking && playing == nil && !pttHeld && now.timeIntervalSince(partialAt) >= endAfter { finishUtterance(); return }
            } else if !speaking && !pttHeld && age >= 7 {
                // nothing said: stop this request; in conversation mode a fresh one keeps listening
                if pttActive && !listening { finishUtterance(); return }
                startRecognition()
            }
            if recognizing && age >= 55 {                       // recognition tasks end at about a minute
                if !partial.isEmpty && !speaking { finishUtterance(); return }
                startRecognition()
            }
        }
        if awake && !busy && !speaking && !pttHeld && playing == nil && queue.isEmpty && now.timeIntervalSince(lastActivity) >= 30 {
            goSleep("quiet")
        }
    }

}
