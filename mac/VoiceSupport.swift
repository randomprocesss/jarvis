// The voice layer's building blocks: paths and settings files, the log, child processes (LineProcess), the TTS helpers
// (TTSHelper: Kokoro, the Chatterbox voices), wake-word clips (Recording), spoken lines (Utt), turn metrics (TurnRec) and
// audio helpers.

import AppKit
import WebKit
import AVFoundation
import Speech

// MARK: - Voice layer
// Mic -> AVAudioEngine (voice processing = echo cancellation) -> 16 kHz int16 -> wake.py (child) and SFSpeechRecognizer
// (on device). Speech out goes through the same engine (player node) so the canceller removes it from the mic.
// Audio is never written to disk, except the enrolment clips (deleted after training or cancel).

let voiceDir = "\(dataHome)/voice"                    // the owner's voice data: settings, their voice print, models, timings
let voiceCode = "\(repoDir)/voice"                    // the helper scripts, part of the code
let voicePython = "\(dataHome)/envs/voice/bin/python"
let ttsPython = "\(dataHome)/envs/tts/bin/python"
let settingsPath = "\(voiceDir)/settings.json"
let enrolDir = "\(voiceDir)/enrol"
let naturalPath = "\(voiceDir)/natural.json"
// Chatterbox voices ("Most human"): id -> config. "natural_female" copies a synthetic Orpheus mia clip (2 Oct), with mood clips
// and the Orpheus reaction library (voice/tts2/natural_tts.py). Delete a config to drop the voice from the picker.
let naturalPaths = ["natural": naturalPath, "natural_female": "\(voiceDir)/natural_female.json"]
func isNatural(_ v: String) -> Bool { naturalPaths[v] != nil }
let danielEnhanced = "com.apple.voice.enhanced.en-GB.Daniel"
let danielCompact = "com.apple.voice.compact.en-GB.Daniel"
let previewLine = "Good morning. Five things need you today."

let vlogQ = DispatchQueue(label: "jarvis.log")
let vlogHandle: FileHandle? = {
    try? FileManager.default.createDirectory(atPath: "\(dataHome)/logs", withIntermediateDirectories: true)
    let p = "\(dataHome)/logs/voice.log"
    let fm = FileManager.default
    if let a = try? fm.attributesOfItem(atPath: p), let s = a[.size] as? Int, s > 2_000_000 { try? fm.removeItem(atPath: p) }
    if !fm.fileExists(atPath: p) { fm.createFile(atPath: p, contents: nil) }
    let h = FileHandle(forWritingAtPath: p)
    _ = try? h?.seekToEnd()
    return h
}()
let vlogFmt: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss.SSS"; return f }()

/// Event log only (never transcripts, never audio).
func vlog(_ s: String) {
    let now = Date()
    vlogQ.async { try? vlogHandle?.write(contentsOf: Data("\(vlogFmt.string(from: now)) \(s)\n".utf8)) }
}

final class Once {
    private let l = NSLock()
    private var done = false
    func fire() -> Bool { l.lock(); defer { l.unlock() }; if done { return false }; done = true; return true }
}

/// A child process spoken to in lines (or raw bytes) on stdin, answering in lines on stdout. Writes happen on its own
/// serial queue, so the audio thread never blocks and a closed pipe is never written after it is closed.
final class LineProcess {
    let proc = Process()
    private let inPipe = Pipe(), outPipe = Pipe()
    private let ioQ = DispatchQueue(label: "jarvis.child.io")
    private let lock = NSLock()
    private var inflight = 0
    private var closed = false
    private var buf = Data()
    var onLine: ((String) -> Void)?
    var onExit: (() -> Void)?

    init(_ exe: String, _ args: [String]) {
        proc.executableURL = URL(fileURLWithPath: exe)
        proc.arguments = args
        proc.currentDirectoryURL = URL(fileURLWithPath: voiceDir)
        proc.standardInput = inPipe
        proc.standardOutput = outPipe
        proc.standardError = vlogHandle ?? FileHandle.nullDevice
    }

    func start() -> Bool {
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }
            self?.take(d)
        }
        proc.terminationHandler = { [weak self] _ in DispatchQueue.main.async { self?.onExit?() } }
        do { try proc.run(); return true } catch { vlog("could not start \(proc.executableURL?.path ?? "?"): \(error)"); return false }
    }

    private func take(_ d: Data) {
        buf.append(d)
        while let i = buf.firstIndex(of: 10) {
            let line = String(decoding: buf[buf.startIndex..<i], as: UTF8.self)
            buf.removeSubrange(buf.startIndex...i)
            DispatchQueue.main.async { [weak self] in self?.onLine?(line) }
        }
    }

    /// Queue bytes for stdin; dropped (never blocking) when the child is behind by more than `maxInflight` writes.
    func feed(_ d: Data, maxInflight: Int = 40) {
        lock.lock()
        if closed || inflight >= maxInflight { lock.unlock(); return }
        inflight += 1
        lock.unlock()
        let h = inPipe.fileHandleForWriting
        ioQ.async {
            try? h.write(contentsOf: d)
            self.lock.lock(); self.inflight -= 1; self.lock.unlock()
        }
    }

    func send(_ line: String) { feed(Data((line + "\n").utf8), maxInflight: 100_000) }

    /// Close stdin (the helpers exit on EOF) and terminate shortly after if needed.
    func stop(grace: Double = 2) {
        onExit = nil; onLine = nil
        lock.lock(); closed = true; lock.unlock()
        let h = inPipe.fileHandleForWriting
        ioQ.async { try? h.close() }
        let p = proc
        if grace <= 0 { if p.isRunning { p.terminate() }; return }
        DispatchQueue.global().asyncAfter(deadline: .now() + grace) { if p.isRunning { p.terminate() } }
    }
}

/// tts.py-protocol helper (Kokoro, or the optional "natural" one), kept warm.
final class TTSHelper {
    enum St { case off, starting, ready, failed }
    let name: String, python: String, script: String
    private(set) var st: St = .off
    private var proc: LineProcess?
    private var pending: [String: (String?) -> Void] = [:]
    private var waiting: [String] = []
    private var exits = 0
    var quitting = false
    var onChange: (() -> Void)?
    var onPath: ((String) -> Void)?

    init(name: String, python: String, script: String) { self.name = name; self.python = python; self.script = script }

    func start() {
        guard st == .off, !quitting else { return }
        guard FileManager.default.isExecutableFile(atPath: python), FileManager.default.fileExists(atPath: script) else {
            vlog("\(name): helper missing"); st = .failed; failAll(); onChange?(); return
        }
        st = .starting
        let p = LineProcess(python, [script])
        p.onLine = { [weak self] l in self?.line(l) }
        p.onExit = { [weak self] in self?.exited() }
        guard p.start() else { st = .failed; failAll(); onChange?(); return }
        proc = p
        vlog("\(name): starting")
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak self] in
            guard let self = self, self.proc === p, self.st == .starting else { return }
            vlog("\(self.name): not ready after 60 s")
            p.stop(grace: 0); self.proc = nil; self.st = .failed; self.failAll(); self.onChange?()
        }
    }

    private func line(_ l: String) {
        guard let o = (try? JSONSerialization.jsonObject(with: Data(l.utf8))) as? [String: Any] else { return }
        if (o["ready"] as? Bool) == true {
            st = .ready
            let w = waiting; waiting = []
            for s in w { proc?.send(s) }
            vlog("\(name): ready")
            onChange?()
            return
        }
        let path = o["path"] as? String
        if let p = path { onPath?(p) }
        guard let id = o["id"] as? String, let cb = pending.removeValue(forKey: id) else {
            if let p = path { try? FileManager.default.removeItem(atPath: p) }   // late answer: nobody wants it
            return
        }
        if path == nil { vlog("\(name): error \(o["error"] ?? "?")") }
        cb(path)
    }

    /// done(path) on main; nil on any failure. The caller deletes the file.
    func request(_ text: String, voice: String?, _ done: @escaping (String?) -> Void) {
        if st == .off { start() }
        if st == .failed { done(nil); return }
        let id = UUID().uuidString
        var r: [String: Any] = ["id": id, "text": text, "speed": 1.0]
        if let v = voice { r["voice"] = v }
        guard let d = try? JSONSerialization.data(withJSONObject: r), let s = String(data: d, encoding: .utf8) else { done(nil); return }
        pending[id] = done
        if st == .ready { proc?.send(s) } else { waiting.append(s) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in
            if let cb = self?.pending.removeValue(forKey: id) { vlog("\(self?.name ?? ""): request timed out"); cb(nil) }
        }
    }

    private func exited() {
        proc = nil
        st = .off
        failAll()
        exits += 1
        vlog("\(name): exited")
        if quitting { return }
        if exits <= 3 { DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.start() } } else { st = .failed }
        onChange?()
    }

    private func failAll() {
        let p = pending; pending = [:]; waiting = []
        for (_, cb) in p { cb(nil) }
    }

    func kill() { quitting = true; proc?.stop(grace: 0); proc = nil }
}

/// A recording in progress (enrolment only). Appended on the audio thread, finished on main.
final class Recording {
    let kind: String, n: Int, max: Int
    private let lock = NSLock()
    private var samples: [Int16] = []
    private var done = false
    init(kind: String, n: Int, max: Int) { self.kind = kind; self.n = n; self.max = max; samples.reserveCapacity(min(max, 16000 * 30)) }
    /// true when full
    func add(_ p: UnsafePointer<Int16>, _ k: Int) -> Bool {
        lock.lock(); defer { lock.unlock() }
        if done { return false }
        let take = min(k, max - samples.count)
        if take > 0 { samples.append(contentsOf: UnsafeBufferPointer(start: p, count: take)) }
        return samples.count >= max
    }
    func finish() -> [Int16]? {
        lock.lock(); defer { lock.unlock() }
        if done { return nil }
        done = true
        let s = samples; samples = []
        return s
    }
}

/// One queued sentence.
final class Utt {
    let id: String, voice: String
    var text: String
    var buf: AVAudioPCMBuffer?
    var failed = false
    var started = false
    var first = true, last = true          // a sentence split into clauses: speak_start on the first piece, speak_end on the last
    var kind = "other"                     // opener | filler | answer | readback | other (metrics + filler timing)
    var turn: String?                      // the voice turn this line answers (metrics)
    var synthStart: Date?                  // when its synthesis began (TTS first-audio metric)
    init(id: String, text: String, voice: String) { self.id = id; self.text = text; self.voice = voice }
}

// MARK: metrics (timings and flags only, never words) -> voice/metrics.ndjson in the data home, one line per turn

let metricsPath = "\(voiceDir)/metrics.ndjson"
let metricsQ = DispatchQueue(label: "jarvis.metrics")
func nowMs() -> Double { (Date().timeIntervalSince1970 * 1000).rounded() }
func ms(_ d: Date) -> Double { (d.timeIntervalSince1970 * 1000).rounded() }

func writeMetric(_ o: [String: Any]) {
    guard let d = try? JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]) else { return }
    metricsQ.async {
        let fm = FileManager.default
        if let a = try? fm.attributesOfItem(atPath: metricsPath), let s = a[.size] as? Int, s > 5_000_000 {
            try? fm.removeItem(atPath: metricsPath + ".1"); try? fm.moveItem(atPath: metricsPath, toPath: metricsPath + ".1")
        }
        if !fm.fileExists(atPath: metricsPath) { fm.createFile(atPath: metricsPath, contents: nil) }
        guard let h = FileHandle(forWritingAtPath: metricsPath) else { return }
        defer { try? h.close() }
        _ = try? h.seekToEnd()
        try? h.write(contentsOf: d + Data("\n".utf8))
    }
}

/// One spoken turn: his last voiced frame -> turn end -> transcript -> request -> first server event -> first audio -> answer audio.
final class TurnRec {
    let id: String
    var src = "live"
    var userStop: Double?, eot: Double?, sttFinal: Double?, reqSent: Double?, firstToken: Double?, firstAudio: Double?, answerAudio: Double?
    var barge = false, bargeStopMs: Double?, bargeOnsetMs: Double?, falseBarge = false, cutoff = false, fastPath = false, opener = false
    var info: [String: Any] = [:]          // flags in force and small facts (eot probability, speculative, engine), never words
    var written = false
    var pageDone = false                   // the page has the whole answer (or handled it locally)
    init(id: String) { self.id = id }

    func json() -> [String: Any] {
        var o: [String: Any] = ["turn": id, "src": src]
        let day = DateFormatter(); day.dateFormat = "yyyy-MM-dd"
        o["day"] = day.string(from: Date(timeIntervalSince1970: (eot ?? nowMs()) / 1000))
        let f: [(String, Double?)] = [("user_stop", userStop), ("eot", eot), ("stt_final", sttFinal), ("req_sent", reqSent),
                                      ("first_token", firstToken), ("first_audio", firstAudio), ("answer_audio", answerAudio)]
        for (k, v) in f { if let v = v { o[k] = v } }
        o["barge"] = barge; o["false_barge"] = falseBarge; o["cutoff"] = cutoff; o["fast_path"] = fastPath; o["opener"] = opener
        if let b = bargeStopMs { o["barge_stop_ms"] = b }
        if let b = bargeOnsetMs { o["barge_onset_ms"] = b }
        // derived latencies (ms) for the stats panel
        var lat: [String: Double] = [:]
        if let u = userStop {
            if let x = eot { lat["eot"] = x - u }
            if let x = firstAudio { lat["first_audio"] = x - u }
            if let x = answerAudio { lat["answer_audio"] = x - u }
        }
        if let e = eot, let s = sttFinal { lat["stt"] = s - e }
        if let r = reqSent, let t = firstToken { lat["token"] = t - r }
        if let r = reqSent, let a = answerAudio { lat["req_to_answer_audio"] = a - r }
        o["lat"] = lat
        if !info.isEmpty { o["info"] = info }
        return o
    }
}

// MARK: audio helpers

func copyBuffer(_ b: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
    guard b.frameLength > 0, let c = AVAudioPCMBuffer(pcmFormat: b.format, frameCapacity: b.frameLength) else { return nil }
    c.frameLength = b.frameLength
    let src = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: b.audioBufferList))
    let dst = UnsafeMutableAudioBufferListPointer(c.mutableAudioBufferList)
    for (s, d) in zip(src, dst) {
        if let sp = s.mData, let dp = d.mData { memcpy(dp, sp, Int(min(s.mDataByteSize, d.mDataByteSize))) }
    }
    return c
}

/// Convert (and join) buffers of one format into `fmt`.
func convertAll(_ parts: [AVAudioPCMBuffer], to fmt: AVAudioFormat) -> AVAudioPCMBuffer? {
    let parts = parts.filter { $0.frameLength > 0 }
    guard let first = parts.first else { return nil }
    if parts.count == 1 && first.format == fmt { return first }
    guard let conv = AVAudioConverter(from: first.format, to: fmt) else { return nil }
    let total = parts.reduce(0) { $0 + Double($1.frameLength) }
    let cap = AVAudioFrameCount(total * fmt.sampleRate / first.format.sampleRate) + 4096
    guard let out = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: cap) else { return nil }
    var i = 0
    var err: NSError?
    let st = conv.convert(to: out, error: &err) { _, s in
        if i < parts.count { s.pointee = .haveData; i += 1; return parts[i - 1] }
        s.pointee = .endOfStream
        return nil
    }
    if st == .error || out.frameLength == 0 { vlog("convert failed: \(err?.localizedDescription ?? "")"); return nil }
    return out
}

/// Mono float buffer with `secs` of silence appended and an optional gain.
func padded(_ b: AVAudioPCMBuffer, _ secs: Double, gain: Float = 1) -> AVAudioPCMBuffer {
    let pad = AVAudioFrameCount(secs * b.format.sampleRate)
    guard let src = b.floatChannelData, let c = AVAudioPCMBuffer(pcmFormat: b.format, frameCapacity: b.frameLength + pad),
          let dst = c.floatChannelData else { return b }
    c.frameLength = b.frameLength + pad
    let n = Int(b.frameLength)
    for ch in 0..<Int(b.format.channelCount) {
        for i in 0..<n { dst[ch][i] = src[ch][i] * gain }
        for i in n..<Int(c.frameLength) { dst[ch][i] = 0 }
    }
    return c
}

func loadAudioFile(_ path: String, to fmt: AVAudioFormat) -> AVAudioPCMBuffer? {
    guard let f = try? AVAudioFile(forReading: URL(fileURLWithPath: path)), f.length > 0,
          let b = AVAudioPCMBuffer(pcmFormat: f.processingFormat, frameCapacity: AVAudioFrameCount(f.length)) else { return nil }
    do { try f.read(into: b) } catch { return nil }
    return convertAll([b], to: fmt)
}

func writeWav(_ s: [Int16], _ path: String) -> Bool {
    var d = Data()
    func u32(_ v: UInt32) { var x = v.littleEndian; withUnsafeBytes(of: &x) { d.append(contentsOf: $0) } }
    func u16(_ v: UInt16) { var x = v.littleEndian; withUnsafeBytes(of: &x) { d.append(contentsOf: $0) } }
    let bytes = UInt32(s.count * 2)
    d.append(contentsOf: Array("RIFF".utf8)); u32(36 + bytes); d.append(contentsOf: Array("WAVE".utf8))
    d.append(contentsOf: Array("fmt ".utf8)); u32(16); u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16)
    d.append(contentsOf: Array("data".utf8)); u32(bytes)
    s.withUnsafeBufferPointer { d.append(UnsafeBufferPointer(start: UnsafeRawPointer($0.baseAddress!).assumingMemoryBound(to: UInt8.self), count: s.count * 2)) }
    return FileManager.default.createFile(atPath: path, contents: d)
}

/// Loudest 20 ms frame, RMS as a fraction of full scale.
/// Per-clip loudness in 20 ms frames: the loudest frame, the room's floor (20th percentile) and how many frames carry voice.
func clipStats(_ s: [Int16]) -> (peak: Double, floor: Double, voiced: Int) {
    var rms: [Double] = []
    var i = 0
    while i + 320 <= s.count {
        var acc = 0.0
        for k in i..<(i + 320) { let v = Double(s[k]); acc += v * v }
        rms.append((acc / 320).squareRoot() / 32768)
        i += 320
    }
    guard let peak = rms.max() else { return (0, 0, 0) }
    let floor = rms.sorted()[rms.count / 5]
    let gate = max(peak * 0.25, floor * 3)
    return (peak, floor, rms.filter { $0 >= gate }.count)
}

func words(_ s: String) -> [String] {
    s.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }
}
