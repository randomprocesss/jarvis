// Enrolment: recording his wake-word clips and training his personal wake model.

import AppKit
import WebKit
import AVFoundation
import Speech

extension VoiceLayer {
    // MARK: enrolment

    func enrol(_ step: String, _ n: Int) {
        let fm = FileManager.default
        switch step {
        case "begin":
            if training { return }
            lastEnrolAt = Date()
            if testTag != nil { vlog("test: stopped (enrolment)"); finishTest() }      // his enrolment always wins over a test
            if awake { goSleep("command") }
            stopSpeaking()
            cancelRecording()
            try? fm.removeItem(atPath: enrolDir)
            try? fm.createDirectory(atPath: "\(enrolDir)/pos", withIntermediateDirectories: true)
            try? fm.createDirectory(atPath: "\(enrolDir)/neg", withIntermediateDirectories: true)
            enrolling = true                      // pauses wake detection
            vlog("enrol: begin")
            updateEngine()
            if !engineOn { emitError("The microphone is not available.") }
        case "clip":
            guard enrolling, !training else { return }
            lastEnrolAt = Date()
            startRecording(Recording(kind: "clip", n: n, max: 32000))      // 2.0 s
        case "talk_start":
            guard enrolling, !training else { return }
            startRecording(Recording(kind: "talk", n: 0, max: 16000 * 240))
        case "talk_stop":
            if let r = recording, r.kind == "talk" { endRecording(r) }
        case "train":
            guard enrolling, !training else { return }
            cancelRecording()
            train()
        case "cancel":
            guard enrolling || training else { return }
            vlog("enrol: cancel")
            cancelRecording()
            if let p = trainProc, p.isRunning { p.terminate() }
            try? fm.removeItem(atPath: enrolDir)
            if !training { endEnrolment() }
        default: break
        }
    }

    func startRecording(_ r: Recording) {
        cancelRecording()
        if !engineOn { updateEngine() }
        guard engineOn else { emitError("The microphone is not available."); return }
        recording = r
        tapLock.lock(); tapRec = r; tapLock.unlock()
    }

    func cancelRecording() {
        tapLock.lock(); tapRec = nil; tapLock.unlock()
        _ = recording?.finish()
        recording = nil
    }

    func endRecording(_ r: Recording) {
        tapLock.lock(); if tapRec === r { tapRec = nil }; tapLock.unlock()
        if recording === r { recording = nil }
        guard let s = r.finish() else { return }
        if r.kind == "clip" {
            // Raw (unprocessed) mic levels are low, so a word is judged against the room, not a fixed level:
            // clearly louder than the quiet part of the clip, and word-length (0.12–1.8 s of voiced frames).
            let st = clipStats(s), lvl = st.peak
            let ok = s.count >= 16000 && st.peak >= 0.0025 && st.peak >= 4 * max(st.floor, 0.0002) && st.voiced >= 6 && st.voiced <= 90
            vlog(String(format: "enrol clip %d peak %.4f floor %.4f voiced %d ok=%@", r.n, st.peak, st.floor, st.voiced, ok ? "yes" : "no"))
            let path = "\(enrolDir)/pos/clip_\(r.n).wav"
            if ok { _ = writeWav(s, path) } else { try? FileManager.default.removeItem(atPath: path) }
            emit(["type": "enrol_clip", "n": r.n, "ok": ok, "level": (lvl * 1000).rounded() / 1000])
        } else {
            let secs = Double(s.count) / 16000
            if s.count >= 16000 { _ = writeWav(s, "\(enrolDir)/neg/talk.wav") }
            emit(["type": "enrol_talk", "ok": secs >= 20, "secs": (secs * 10).rounded() / 10])
        }
    }

    func train() {
        training = true
        vlog("enrol: training")
        let dir = enrolDir
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: voicePython)
            p.arguments = ["\(voiceCode)/train.py", dir]
            p.currentDirectoryURL = URL(fileURLWithPath: voiceDir)
            let out = Pipe()
            p.standardOutput = out
            p.standardError = vlogHandle ?? FileHandle.nullDevice
            p.standardInput = FileHandle.nullDevice
            var result: [String: Any]
            do {
                try p.run()
                DispatchQueue.main.async { self.trainProc = p }
                let data = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                let lines = String(decoding: data, as: UTF8.self).split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
                if let last = lines.last(where: { $0.hasPrefix("{") }),
                   let o = (try? JSONSerialization.jsonObject(with: Data(last.utf8))) as? [String: Any] {
                    result = o
                } else {
                    result = ["ok": false, "error": "Training stopped without a result (exit \(p.terminationStatus))."]
                }
            } catch {
                result = ["ok": false, "error": "Training could not start."]
            }
            DispatchQueue.main.async { self.trainDone(result) }
        }
    }

    func trainDone(_ r: [String: Any]) {
        training = false
        trainProc = nil
        var ev: [String: Any] = ["type": "enrol_result", "ok": (r["ok"] as? Bool) ?? false]
        if let t = r["threshold"] { ev["threshold"] = t }
        if let h = r["held_out_recall"] { ev["held_out_recall"] = h }
        if let e = r["error"] { ev["error"] = e }
        vlog("enrol: result ok=\(ev["ok"] ?? false)")
        emit(ev)
        try? FileManager.default.removeItem(atPath: enrolDir)    // no clip outlives the enrolment
        endEnrolment()
    }

    func endEnrolment() {
        enrolling = false
        restartWake()                              // picks up personal.npz by itself
        updateEngine()
        emitState()
    }
}
