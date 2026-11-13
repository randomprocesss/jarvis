// Speaker check for voice confirmations (HARNESS.md Phase 3, 5 Oct 2026). A send, a Jira post or an approval by voice
// goes ahead only on HIS "yes": after a short final (6 words or fewer), the utterance's 16 kHz audio (kept in memory only)
// goes to speaker.py, which scores it against his voiceprint (voice/speaker_print.npy, threshold in speaker_print.json,
// calibrated on 400 other speakers: 0.7% pass). The page gets {type:"speaker", turn, score, ok}. The audio file is deleted
// as soon as it is scored; only the score is logged.
import Foundation

extension VoiceLayer {
    func startSpeakerHelper() {
        guard speakerProc == nil, !quitting, FileManager.default.fileExists(atPath: "\(voiceDir)/speaker_print.json") else { return }
        let p = LineProcess(voicePython, ["\(voiceCode)/speaker.py", "serve"])
        p.onLine = { [weak self] l in self?.speakerLine(l) }
        p.onExit = { [weak self] in
            self?.speakerProc = nil
            vlog("speaker helper exited")
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in self?.startSpeakerHelper() }
        }
        if p.start() { speakerProc = p; vlog("speaker helper: starting") } else { vlog("speaker helper could not start") }
    }

    func speakerCheck(_ turn: String) {
        tapLock.lock(); let pcm = uttPCM; tapLock.unlock()
        guard let p = speakerProc, pcm.count >= 5600 else {             // under 0.35 s: too short to tell
            emit(["type": "speaker", "turn": turn, "ok": false, "why": speakerProc == nil ? "no helper" : "too short"]); return
        }
        let dir = "\(voiceDir)/tmp"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let path = "\(dir)/spk_\(turn).raw"
        var f = pcm.map { Float($0) / 32768.0 }
        let data = Data(bytes: &f, count: f.count * 4)
        guard FileManager.default.createFile(atPath: path, contents: data, attributes: [.posixPermissions: 0o600]) else { return }
        // the room's noise floor goes with the clip: speaker.py compares the clip's speech peaks with it (5 Oct: the floor
        // rose ~5x at 19:29 and both the transcript and the voice score fell apart)
        tapLock.lock(); let floor = Double(vadFloor); tapLock.unlock()
        spkPending.append((turn: turn, path: path, snr: 0))
        p.send(floor > 0 ? "\(path)\t\(String(format: "%.6f", floor))" : path)
    }

    func speakerLine(_ l: String) {
        guard !spkPending.isEmpty else { return }
        let (turn, path, _) = spkPending.removeFirst()
        try? FileManager.default.removeItem(atPath: path)
        let parts = l.split(separator: " ").map(String.init)
        if parts.count >= 3, parts[0] == "score", let s = Double(parts[1]) {
            let ok = parts[2] == "1"
            let snr = parts.first(where: { $0.hasPrefix("snr=") }).flatMap { Double($0.dropFirst(4)) } ?? 0
            let noisy = !ok && snr > 0 && snr < 3          // too noisy to tell: say so, never "that wasn't you"
            vlog(String(format: "speaker %.3f %@ (voice/room %.1f)", s, ok ? "ok" : (noisy ? "too noisy" : "not him"), snr))
            var e: [String: Any] = ["type": "speaker", "turn": turn, "score": s, "ok": ok]
            if noisy { e["why"] = "noisy" }
            emit(e)
        } else {
            vlog("speaker: \(l)")
            emit(["type": "speaker", "turn": turn, "ok": false, "why": "no score"])
        }
    }
}
