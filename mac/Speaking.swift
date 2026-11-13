// Speaking: the voices (Siri/system, Kokoro, the Chatterbox "Most human" voices), settings, the speech queue,
// synthesis and playback.

import AppKit
import WebKit
import AVFoundation
import Speech

extension VoiceLayer {
    // MARK: speaking

    var appleFallback: String { AVSpeechSynthesisVoice(identifier: danielEnhanced) != nil ? danielEnhanced : danielCompact }

    /// Siri's neural voices are the most human on this Mac and work through AVSpeechSynthesizer: British first (once
    /// downloaded in Spoken Content > Manage Voices), then any other English one, male (Akash) before female.
    var siriVoices: [AVSpeechSynthesisVoice] {
        AVSpeechSynthesisVoice.speechVoices().filter { $0.language.hasPrefix("en") && $0.identifier.contains("siri") }.sorted { a, b in
            let rank = { (v: AVSpeechSynthesisVoice) -> Int in (v.language == "en-GB" ? 0 : 2) + (v.gender == .male ? 0 : 1) }
            return rank(a) != rank(b) ? rank(a) < rank(b) : a.identifier < b.identifier
        }
    }
    func siriLabel(_ v: AVSpeechSynthesisVoice) -> String {
        let n = String(v.identifier.split(separator: ".").last ?? "")
        return "Siri " + (n.isEmpty || n.hasPrefix("Voice") ? v.name : n)
    }

    var defaultVoice: String {
        if let v = siriVoices.first { return "apple:" + v.identifier }
        return AVSpeechSynthesisVoice(identifier: danielEnhanced) != nil ? "apple:" + danielEnhanced : (kokoro.st != .failed ? "kokoro:bm_george" : "apple:" + danielCompact)
    }

    var currentVoice: String {
        if let v = savedVoice, voiceUsable(v) { return v }
        return defaultVoice
    }

    func naturalConfig(_ id: String = "natural") -> (python: String, script: String, label: String)? {
        guard let path = naturalPaths[id], let d = FileManager.default.contents(atPath: path),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
              let py = o["python"] as? String, let sc = o["script"] as? String else { return nil }
        return ((py as NSString).expandingTildeInPath, (sc as NSString).expandingTildeInPath, (o["label"] as? String) ?? "Most natural (slower)")
    }

    func naturalHelper(_ id: String = "natural") -> TTSHelper? {
        if let n = naturals[id] { return n }
        guard let c = naturalConfig(id) else { return nil }
        let n = TTSHelper(name: id, python: c.python, script: c.script)
        n.onPath = { [weak self] p in self?.ttsDirs.insert((p as NSString).deletingLastPathComponent) }
        n.onChange = { [weak self] in self?.emitState() }
        naturals[id] = n
        return n
    }

    /// How fast Jarvis talks (1.0 = the voice's own pace). the first owner found every voice slow, so a little brisker by default.
    var speechRate: Float {
        if let r = readSettings()["speech_rate"] as? NSNumber { return min(1.4, max(0.8, r.floatValue)) }
        return 1.1
    }

    func voiceUsable(_ v: String) -> Bool {
        if v.hasPrefix("kokoro:") { return kokoro.st != .failed && ["bm_george", "bm_fable", "bm_lewis"].contains(String(v.dropFirst(7))) }
        if v.hasPrefix("apple:") { return AVSpeechSynthesisVoice(identifier: String(v.dropFirst(6))) != nil }
        if isNatural(v) { return naturalConfig(v) != nil && naturals[v]?.st != .failed }
        return false
    }

    func emitVoices() {
        var l: [[String: String]] = []
        for v in siriVoices {
            let lang = Locale(identifier: "en").localizedString(forIdentifier: v.language) ?? v.language
            l.append(["id": "apple:" + v.identifier, "label": siriLabel(v), "detail": "Most human · Siri voice · \(lang)"])
        }
        if kokoro.st != .failed {
            l.append(["id": "kokoro:bm_george", "label": "George", "detail": "British, calm · made on this Mac"])
            l.append(["id": "kokoro:bm_fable", "label": "Fable", "detail": "British, lighter · made on this Mac"])
            l.append(["id": "kokoro:bm_lewis", "label": "Lewis", "detail": "British, lower · made on this Mac"])
        }
        let apple = AVSpeechSynthesisVoice.speechVoices().filter { v in
            v.language.hasPrefix("en") && !v.identifier.contains("siri")
                && (v.quality != .default || v.identifier == danielCompact)
        }.sorted { a, b in
            if a.identifier == danielEnhanced { return true }
            if b.identifier == danielEnhanced { return false }
            return a.name < b.name
        }
        for v in apple {
            let lang = Locale(identifier: "en").localizedString(forIdentifier: v.language) ?? v.language
            l.append(["id": "apple:" + v.identifier, "label": v.name, "detail": "System voice · \(lang)"])
        }
        for id in ["natural", "natural_female"] {
            if let c = naturalConfig(id), naturals[id]?.st != .failed {
                l.append(["id": id, "label": c.label, "detail": "Most lifelike · laughs and sighs · made on this Mac"])
            }
        }
        emit(["type": "voices", "list": l, "current": currentVoice])
    }

    func readSettings() -> [String: Any] {
        guard let d = FileManager.default.contents(atPath: settingsPath),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return [:] }
        return o
    }

    /// settings.json, re-read only when the file changes.
    func settings() -> [String: Any] {
        let m = ((try? FileManager.default.attributesOfItem(atPath: settingsPath))?[.modificationDate] as? Date) ?? .distantPast
        if m != settingsMTime { settingsMTime = m; settingsCache = readSettings() }
        return settingsCache
    }

    /// Each new piece of the voice upgrade sits behind one switch in settings.json (missing = its default).
    static let flagNames = ["warm_brain", "fast_model", "smart_turn", "backchannel", "cached_openers", "lexicon", "proactive", "ears"]
    static let flagDefaults: [String: Bool] = ["warm_brain": true, "fast_model": true, "smart_turn": true, "listen_cues": true]
    func flag(_ k: String) -> Bool { (settings()[k] as? Bool) ?? (VoiceLayer.flagDefaults[k] ?? false) }
    func flagsDict() -> [String: Any] {
        var d: [String: Any] = [:]
        for k in VoiceLayer.flagNames { d[k] = flag(k) }
        d["stt_engine"] = (settings()["stt_engine"] as? String) ?? "apple"
        return d
    }

    // test hooks, turn-taking, barge-in, openers, lexicon, proactive: Conversation.swift

    func saveSettings(_ patch: [String: Any]) {
        if FileManager.default.fileExists(atPath: settingsPath) && readSettings().isEmpty,
           let d = FileManager.default.contents(atPath: settingsPath), !d.isEmpty {
            emitError("settings.json could not be read, so the voice was not saved."); return   // never clobber other keys
        }
        var s = readSettings()
        for (k, v) in patch { s[k] = v }
        guard let d = try? JSONSerialization.data(withJSONObject: s, options: [.prettyPrinted, .sortedKeys]) else { return }
        try? d.write(to: URL(fileURLWithPath: settingsPath), options: .atomic)
    }

    func enqueueSpeak(id: String, text: String, voice: String? = nil, kind: String = "other", turn: String? = nil) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty || quitting {
            emit(["type": "speak_start", "id": id]); emit(["type": "speak_end", "id": id]); return
        }
        // Office quiet mode (Office.swift): no sound; the page shows the line as a caption. Openers/fillers are skipped; a
        // line held "speaking" for about its reading time keeps the read-back window and the turn flow as they are.
        if voice == nil && Office.shared.quiet {
            if kind == "opener" || kind == "filler" { emit(["type": "speak_start", "id": id]); emit(["type": "speak_end", "id": id]); return }
            emit(["type": "speak_start", "id": id, "quiet": true])
            let secs = min(6.0, 0.6 + Double(t.count) * 0.045)
            DispatchQueue.main.asyncAfter(deadline: .now() + secs) { [weak self] in self?.emit(["type": "speak_end", "id": id, "quiet": true]) }
            return
        }
        // one voice per conversation (a voice chosen meanwhile starts with the next one); a preview names its own voice
        let v = voice ?? (convVoice.flatMap { voiceUsable($0) ? $0 : nil } ?? currentVoice)
        let body = String(t.prefix(600))
        // A cached opener/filler/cue plays at once.
        if let b = cachedBuffer(body, v) {
            let u = Utt(id: id, text: body, voice: v)
            u.kind = kind; u.turn = turn; u.buf = b; u.started = true
            queue.append(u)
            pumpPlay()
            return
        }
        // The natural voice makes a whole clip before it can play, so long sentences go in as clauses: the first one is
        // ready in well under a second and the next ones are made while it plays. With the lexicon switch on, the first
        // sentence of an answer also starts with a short clause (<= 6 words) in Kokoro and the natural voice.
        var pieces = isNatural(v) ? clauses(body) : [body]
        if flag("lexicon") && kind == "answer" && (isNatural(v) || v.hasPrefix("kokoro:")),
           let t0 = turn, !answerStarted(t0), let firstPiece = pieces.first {
            pieces = firstClause(firstPiece) + pieces.dropFirst()
        }
        for (i, piece) in pieces.enumerated() {
            let u = Utt(id: id, text: piece, voice: v)
            u.first = i == 0; u.last = i == pieces.count - 1
            u.kind = kind; u.turn = turn
            queue.append(u)
        }
        pumpSynth()
        pumpPlay()
    }

    /// Has any of this turn's answer been queued or played yet?
    func answerStarted(_ turn: String) -> Bool {
        if queue.contains(where: { $0.turn == turn && $0.kind == "answer" }) { return true }
        if let p = playing, p.turn == turn, p.kind == "answer" { return true }
        if let tr = turnCur, tr.id == turn, tr.answerAudio != nil { return true }
        return false
    }

    /// Splits a long sentence after commas, semicolons or dashes into pieces of at least ~28 characters.
    func clauses(_ t: String) -> [String] {
        if t.count < 70 { return [t] }
        var out: [String] = [], cur = ""
        for ch in t {
            cur.append(ch)
            if (ch == "," || ch == ";" || ch == "—") && cur.count >= 28 { out.append(cur.trimmingCharacters(in: .whitespaces)); cur = "" }
        }
        let rest = cur.trimmingCharacters(in: .whitespaces)
        if !rest.isEmpty { if rest.count < 14, let l = out.popLast() { out.append(l + " " + rest) } else { out.append(rest) } }
        return out.isEmpty ? [t] : out
    }

    func pumpSynth() {
        guard !synthBusy, let u = queue.first(where: { !$0.started }) else { return }
        u.started = true
        u.synthStart = Date()
        synthBusy = true
        let g = speakGen
        synthesize(u.text, u.voice) { [weak self] b in
            guard let self = self, g == self.speakGen else { return }
            self.synthBusy = false
            if let b = b { u.buf = b } else { u.failed = true }
            // TTS time of the answer's first piece (ms, and its length in words; never the words)
            if u.kind == "answer", let t = u.turn, let tr = self.turnCur, tr.id == t, !tr.written, tr.info["tts_ms"] == nil, let s0 = u.synthStart {
                tr.info["tts_ms"] = (Date().timeIntervalSince(s0) * 1000).rounded(); tr.info["tts_words"] = words(u.text).count
            }
            self.pumpPlay()
            self.pumpSynth()
        }
    }

    func loadSpeech(_ path: String) -> AVAudioPCMBuffer? {
        defer { try? FileManager.default.removeItem(atPath: path) }    // synthesized speech only; gone once in memory
        return loadAudioFile(path, to: playFmt).map { flag("cached_openers") ? polished($0) : padded($0, 0.14) }
    }

    func synthesize(_ text0: String, _ voice: String, raw: Bool = true, _ done: @escaping (AVAudioPCMBuffer?) -> Void) {
        let text = raw ? forEar(text0) : text0          // lexicon + numbers/dates/times as words (lexicon switch)
        if voice.hasPrefix("kokoro:") {
            kokoro.request(text, voice: String(voice.dropFirst(7))) { [weak self] path in
                guard let self = self else { return }
                if let p = path, let b = self.loadSpeech(p) { done(b); return }
                vlog("kokoro line failed; using the system voice")
                self.appleSynth(text, self.appleFallback, done)
            }
        } else if isNatural(voice), let n = naturalHelper(voice) {
            n.request(text, voice: nil) { [weak self] path in
                guard let self = self else { return }
                if let p = path, let b = self.loadSpeech(p) { done(b); return }
                vlog("natural line failed; falling back")
                self.synthesize(text, self.kokoro.st != .failed ? "kokoro:bm_george" : "apple:" + self.appleFallback, raw: false, done)
            }
        } else {
            appleSynth(text, voice.hasPrefix("apple:") ? String(voice.dropFirst(6)) : appleFallback, done)
        }
    }

    func appleSynth(_ text: String, _ id: String, _ done: @escaping (AVAudioPCMBuffer?) -> Void) {
        let u = AVSpeechUtterance(string: text)
        u.voice = AVSpeechSynthesisVoice(identifier: id) ?? AVSpeechSynthesisVoice(identifier: danielCompact) ?? AVSpeechSynthesisVoice(language: "en-GB")
        u.rate = AVSpeechUtteranceDefaultSpeechRate
        u.preUtteranceDelay = 0.05
        u.postUtteranceDelay = 0.1
        let once = Once()
        var parts: [AVAudioPCMBuffer] = []
        let fmt = playFmt
        synth.write(u) { b in
            guard let pb = b as? AVAudioPCMBuffer else { return }
            if pb.frameLength == 0 {
                let pol = Voice.shared.map { ($0 as? VoiceLayer)?.flag("cached_openers") ?? false } ?? false
                let out = convertAll(parts, to: fmt).map { pol ? (Voice.shared as? VoiceLayer)?.polished($0) ?? $0 : padded($0, 0.12) }
                parts = []
                DispatchQueue.main.async { if once.fire() { done(out) } }
            } else if let c = copyBuffer(pb) {
                parts.append(c)
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { if once.fire() { vlog("system voice timed out"); done(nil) } }
    }

    /// Speech always plays through a plain output-only engine at full quality. (Playing it through the voice-processing unit
    /// let echo cancellation remove it from the mic, but that unit outputs call-quality audio: Daniel sounded nothing like
    /// the Mac's Daniel. Self-hearing is handled instead by ignoring the wake word while speaking and the echo-word filter.)
    func outPlayer() -> AVAudioPlayerNode? {
        // echo_cancel (experiment, off by default): speech goes through the voice-processing engine, so its echo canceller
        // has the reference and removes Jarvis from the mic. Only when that engine was built for it (see tryEngine).
        if echoCancelBuilt, engineOn, engine.isRunning, playerOnMain, vpActive, let tp = mainTP {
            outIsMain = true
            outLatency = engine.outputNode.presentationLatency + tp.latency
            return player
        }
        outIsMain = false
        if plainEngine == nil {
            // player -> time-pitch (speaking pace without changing the pitch; settings.json "speech_rate", default 1.1) -> output
            let e = AVAudioEngine(), p = AVAudioPlayerNode(), tp = AVAudioUnitTimePitch()
            tp.rate = speechRate
            e.attach(p); e.attach(tp)
            e.connect(p, to: tp, format: playFmt)
            e.connect(tp, to: e.mainMixerNode, format: playFmt)
            plainEngine = e; plainPlayer = p; plainTP = tp
        }
        guard let e = plainEngine else { return nil }
        if !e.isRunning {
            do { try e.start() } catch { vlog("output engine failed: \(error)"); return nil }
            let tpLat = e.attachedNodes.compactMap { ($0 as? AVAudioUnitTimePitch)?.latency }.first ?? 0
            outLatency = e.outputNode.presentationLatency + tpLat
        }
        return plainPlayer
    }

    func pumpPlay() {
        guard playing == nil else { return }
        guard let u = queue.first else { speechDrained(); return }
        if u.failed {
            queue.removeFirst()
            if u.first { emit(["type": "speak_start", "id": u.id]) }
            if u.last { emit(["type": "speak_end", "id": u.id]) }
            emitError("Jarvis could not say a line.")
            pumpPlay()
            return
        }
        guard let b = u.buf else { return }      // still being made
        guard let p = outPlayer() else { u.failed = true; pumpPlay(); return }
        unmuteIfNeeded()
        // the answer (and its opener) follows his pace this turn; lines he did not just ask for keep the usual pace
        let rate = speechRate * ((u.kind == "answer" || u.kind == "opener") ? paceMul : 1)
        (outIsMain ? mainTP : plainTP)?.rate = rate
        // player gaps inside one answer: the next piece of the same answer starts after the previous one ended
        if u.kind == "answer", let t = u.turn, t == lastPlayTurn, lastPlayKind == "answer", let tr = turnCur, tr.id == t, !tr.written {
            let gap = Date().timeIntervalSince(lastPlayEnd)
            if gap > 0.08 {
                tr.info["gaps"] = ((tr.info["gaps"] as? Int) ?? 0) + 1
                tr.info["gap_ms"] = ((tr.info["gap_ms"] as? Double) ?? 0) + (gap * 1000).rounded()
            }
        }
        queue.removeFirst()
        playing = u
        playStart = Date(); playDur = Double(b.frameLength) / b.format.sampleRate / Double(max(rate, 0.5))
        spoken.formUnion(words(u.text))
        if let t = u.turn, let tr = turnCur, tr.id == t, !tr.written {
            let at = nowMs() + (outLatency * 1000).rounded()
            if tr.firstAudio == nil { tr.firstAudio = at }
            if u.kind == "opener" { tr.opener = true }
            if u.kind == "answer" && tr.answerAudio == nil { tr.answerAudio = at }
        }
        // anything said for the waiting turn before its answer (the page's escalation line, an opener) counts as its opener
        // and as the latest narration, so the app never stacks a second opener or a filler right behind it
        if let w = wait, let t = u.turn, t == w.turn, u.kind != "answer" { w.openerDone = true; if u.kind == "filler" { w.lastNarration = Date() } }
        if !speaking { speaking = true; emitState() }
        tapLock.lock(); if !echoMeter { echoMeter = true; echoSum = 0; echoN = 0 }; tapLock.unlock()
        if u.first { emit(["type": "speak_start", "id": u.id]) }
        let g = speakGen
        p.scheduleBuffer(b, at: nil, options: [], completionCallbackType: .dataPlayedBack) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, g == self.speakGen, self.playing === u else { return }
                self.playing = nil
                self.lastPlayEnd = Date(); self.lastPlayTurn = u.turn; self.lastPlayKind = u.kind
                if u.turn != nil && u.turn == self.turnCur?.id { self.heardTexts.append(u.text) }   // he heard all of it
                if u.last { self.emit(["type": "speak_end", "id": u.id]) }
                self.pumpPlay()
            }
        }
        if !p.isPlaying { p.play() }
    }

    func speechDrained() {
        lastDrain = Date()
        tapLock.lock(); let (es, en) = (echoSum, echoN); let wasMeter = echoMeter; echoMeter = false; let fl = vadFloor; tapLock.unlock()
        if wasMeter && en >= 20 {
            vlog(String(format: "mic level while speaking %.5f over %d buffers (output %@, room floor %.5f)", es / Double(en), en, outIsMain ? "voice processing" : "plain", fl))
        }
        guard speaking else { return }
        speaking = false
        lastActivity = Date()
        emitState()
        vlog("speech drained (recognizing=\(recognizing), words heard meanwhile: \(words(partial).count))")
        if recognizing && !partial.isEmpty && !partialShown {
            let novel = words(partial).filter { !spoken.contains($0) }.joined(separator: " ")
            if isEcho(partial) { startRecognition() }        // only heard ourselves: start clean
            else if flag("backchannel") && !pendingAction && backchannelDuringSpeech(novel) {
                vlog("backchannel while Jarvis spoke: not a question")   // "yeah", "mm-hmm" while it talked
                startRecognition()
            }
            else { partialAt = Date(); showPartial() }
        }
        let g = speakGen
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            guard g == self.speakGen, !self.speaking, self.queue.isEmpty, self.playing == nil, let e = self.plainEngine, e.isRunning else { return }
            self.plainPlayer?.stop(); e.stop()
        }
    }

    func stopSpeaking() {
        lastDrain = Date()
        tapLock.lock(); echoMeter = false; tapLock.unlock()
        speakGen += 1
        synthBusy = false
        queue.removeAll()
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        if engine.isRunning && playerOnMain { player.stop() }
        if let e = plainEngine, e.isRunning { plainPlayer?.stop() }
        if let u = playing { playing = nil; emit(["type": "speak_end", "id": u.id]) }
        if speaking {
            speaking = false
            lastActivity = Date()
            emitState()
            if recognizing && !barged && !partial.isEmpty && !partialShown && isEcho(partial) { startRecognition() }
        }
    }

}
