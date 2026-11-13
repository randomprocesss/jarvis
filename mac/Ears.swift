// Better ears (the owner, 2 Oct 2026): Apple's newer on-device speech-to-text, SpeechAnalyzer + SpeechTranscriber (macOS 26+,
// runs on the Neural Engine), behind the same small interface the old SFSpeechRecognizer request had, so the turn-taking
// code (partials, endAudio -> final, cancel, the finishing request) works unchanged. settings.json "ears": true turns it
// on; anything that fails falls back to SFSpeechRecognizer for that utterance. Audio is never stored.
import AVFoundation
import Speech

/// What the turn-taking code needs from a recognition request + its task, for either engine.
protocol RecRequest: AnyObject { func append(_ b: AVAudioPCMBuffer); func endAudio() }
protocol RecTask: AnyObject { func cancel() }
struct RecResult { let text: String; let isFinal: Bool }

extension SFSpeechAudioBufferRecognitionRequest: RecRequest {}
extension SFSpeechRecognitionTask: RecTask {}

enum Ears {
    /// The locale the new engine uses (en-IN when it has it), and whether its model is installed and ready.
    nonisolated(unsafe) static var locale: Locale?
    nonisolated(unsafe) static var ready = false
    nonisolated(unsafe) static var preparing = false
    /// Diagnostics (2 Oct): the app sets this to vlog. Each request logs its life once: start, audio, words, end.
    nonisolated(unsafe) static var log: (String) -> Void = { _ in }
    nonisolated(unsafe) static var seq = 0

    /// Once per launch, in the background: pick the locale and make sure its on-device model is installed.
    static func prepare(_ log: @escaping (String) -> Void) {
        guard #available(macOS 26.0, *), !ready, !preparing else { return }
        preparing = true
        Task {
            defer { preparing = false }
            var loc: Locale?
            for id in ["en-IN", "en-GB", "en-US"] {
                if let l = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: id)) { loc = l; break }
            }
            guard let l = loc else { log("ears: no supported English locale; staying on the old recognizer"); return }
            do {
                let t = SpeechTranscriber(locale: l, transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults], attributeOptions: [])
                if let req = try await AssetInventory.assetInstallationRequest(supporting: [t]) {
                    log("ears: downloading the \(l.identifier) speech model (one time)")
                    try await req.downloadAndInstall()
                }
                locale = l; ready = true
                log("ears: SpeechAnalyzer ready (\(l.identifier), on device)")
            } catch {
                log("ears: model install failed (\(error.localizedDescription)); staying on the old recognizer")
            }
        }
    }
}

/// The live request's audio, kept for the second pass at the turn end ("ears" switch): every buffer the old recognizer
/// hears (the tap makes a fresh buffer each time, so they are kept as they are), at most a minute.
final class TeeRequest: RecRequest {
    let inner: RecRequest
    private let lock = NSLock()
    private var bufs: [AVAudioPCMBuffer] = []
    private var secs = 0.0
    init(_ inner: RecRequest) { self.inner = inner }
    func append(_ b: AVAudioPCMBuffer) {
        inner.append(b)
        lock.lock(); if secs < 60 { bufs.append(b); secs += Double(b.frameLength) / b.format.sampleRate }; lock.unlock()
    }
    func endAudio() { inner.endAudio() }
    func audio() -> [AVAudioPCMBuffer] { lock.lock(); defer { lock.unlock() }; return bufs }
}

/// One utterance through SpeechAnalyzer: append() from the audio thread, endAudio() to finish (a final result follows),
/// cancel() to drop it. Results come back as (text so far, isFinal, error), like the old recognitionTask callback.
@available(macOS 26.0, *)
final class EarsRequest: RecRequest, RecTask {
    private let stream: AsyncStream<AnalyzerInput>
    private let cont: AsyncStream<AnalyzerInput>.Continuation
    private let onResult: (RecResult?, Error?) -> Void
    private let lock = NSLock()
    private var fmt: AVAudioFormat?
    private var conv: AVAudioConverter?
    private var convFrom: AVAudioFormat?
    private var pending: [AVAudioPCMBuffer] = []
    private var analyzer: SpeechAnalyzer?
    private var done = false, ended = false
    private let id: Int, born = Date()
    private var nIn = 0, nPushed = 0, nRes = 0, convFail = 0

    private func ms() -> Int { Int(Date().timeIntervalSince(born) * 1000) }

    init(locale: Locale, vocabulary: [String], onResult: @escaping (RecResult?, Error?) -> Void) {
        (stream, cont) = AsyncStream<AnalyzerInput>.makeStream(bufferingPolicy: .unbounded)
        self.onResult = onResult
        Ears.seq += 1; id = Ears.seq
        Ears.log("ears #\(id): created")
        Task { await self.run(locale: locale, vocabulary: vocabulary) }
    }

    deinit { Ears.log("ears #\(id): released after \(ms()) ms (audio in \(nIn), pushed \(nPushed), results \(nRes), conversion failures \(convFail))") }

    private func run(locale: Locale, vocabulary: [String]) async {
        do {
            let t = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults], attributeOptions: [])
            let a = SpeechAnalyzer(modules: [t])
            guard let f = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [t]) else { throw NSError(domain: "ears", code: 1, userInfo: [NSLocalizedDescriptionKey: "no audio format"]) }
            if !vocabulary.isEmpty {
                let ctx = AnalysisContext(); ctx.contextualStrings[.general] = vocabulary
                try? await a.setContext(ctx)
            }
            let (held, wasEnded) = install(format: f, analyzer: a)
            Ears.log("ears #\(id): analyzer ready at \(ms()) ms, format \(Int(f.sampleRate)) Hz \(f.commonFormat.rawValue) ch \(f.channelCount); \(held.count) buffers waiting; ended already: \(wasEnded)")
            for b in held { push(b) }
            if wasEnded { cont.finish() }      // the audio ended before the analyzer was ready: close it only now, after the held audio
            // results: final pieces are kept, the volatile tail is replaced; "text so far" = finals + current tail
            let reader = Task { [weak self] in
                var finals = "", tail = ""
                do {
                    for try await r in t.results {
                        let s = String(r.text.characters).trimmingCharacters(in: .whitespaces)
                        self?.countResult(s, r.isFinal)
                        if r.isFinal { finals = [finals, s].filter { !$0.isEmpty }.joined(separator: " "); tail = "" } else { tail = s }
                        let now = [finals, tail].filter { !$0.isEmpty }.joined(separator: " ")
                        if !now.isEmpty { self?.deliver(RecResult(text: now, isFinal: false), nil) }
                    }
                    self?.note("results ended")
                    self?.deliver(RecResult(text: finals.isEmpty ? tail : finals, isFinal: true), nil)
                } catch {
                    self?.note("results error: \(error)")
                    self?.deliver(nil, error)
                }
            }
            try await a.start(inputSequence: stream)
            Ears.log("ears #\(id): analyzer started at \(ms()) ms")
            // endAudio() came before the analyzer existed: finish it now that it does
            if wasEnded { try? await a.finalizeAndFinishThroughEndOfInput() }
            _ = reader
        } catch {
            Ears.log("ears #\(id): start failed at \(ms()) ms: \(error)")
            deliver(nil, error)
        }
    }

    private func note(_ what: String) { Ears.log("ears #\(id): \(what) at \(ms()) ms (results \(nRes))") }
    private func countResult(_ s: String, _ fin: Bool) {
        nRes += 1
        if nRes == 1 { Ears.log("ears #\(id): first words at \(ms()) ms: \"\(s.prefix(40))\"") }
        if fin { Ears.log("ears #\(id): final piece at \(ms()) ms: \"\(s.prefix(60))\"") }
    }

    /// The analyzer and its format are known: hand back what arrived meanwhile (a plain, synchronous critical section).
    private func install(format f: AVAudioFormat, analyzer a: SpeechAnalyzer) -> ([AVAudioPCMBuffer], Bool) {
        lock.lock(); defer { lock.unlock() }
        fmt = f; analyzer = a
        let held = pending; pending = []
        return (held, ended)
    }

    private func deliver(_ r: RecResult?, _ e: Error?) {
        lock.lock(); let stop = done; if (r?.isFinal ?? false) || e != nil { done = true }; lock.unlock()
        if !stop { onResult(r, e) }
    }

    /// From the audio tap: converted to the analyzer's format (held until that format is known).
    func append(_ b: AVAudioPCMBuffer) {
        lock.lock()
        nIn += 1
        if nIn == 1 { let f = b.format; Ears.log("ears #\(id): first audio at \(ms()) ms (\(Int(f.sampleRate)) Hz, \(f.channelCount) ch, \(b.frameLength) frames)") }
        if done || ended { lock.unlock(); return }
        if fmt == nil { pending.append(b); lock.unlock(); return }
        lock.unlock()
        push(b)
    }

    private func push(_ b: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
        guard let f = fmt else { return }
        if b.format == f { nPushed += 1; cont.yield(AnalyzerInput(buffer: b)); return }
        if conv == nil || convFrom != b.format { conv = AVAudioConverter(from: b.format, to: f); convFrom = b.format }
        guard let c = conv else { convFail += 1; if convFail == 1 { Ears.log("ears #\(id): no converter from \(b.format) to \(f)") }; return }
        let cap = AVAudioFrameCount(Double(b.frameLength) * f.sampleRate / b.format.sampleRate) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: f, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        c.convert(to: out, error: &err) { _, st in
            if fed { st.pointee = .noDataNow; return nil }
            fed = true; st.pointee = .haveData; return b
        }
        if err == nil && out.frameLength > 0 { nPushed += 1; cont.yield(AnalyzerInput(buffer: out)) }
        else { convFail += 1; if convFail == 1 { Ears.log("ears #\(id): conversion failed: \(err?.localizedDescription ?? "0 frames out")") } }
    }

    /// No more audio: the analyzer finishes the words it has and the results end with a final.
    func endAudio() {
        lock.lock(); if ended { lock.unlock(); return }; ended = true; let a = analyzer; lock.unlock()
        Ears.log("ears #\(id): endAudio at \(ms()) ms (audio in \(nIn), pushed \(nPushed), results \(nRes))")
        // before the analyzer exists, its held audio is still to be pushed: run() closes the stream after that (2 Oct: closing
        // it here dropped every held buffer, so a short utterance never got a final)
        guard let a = a else { return }
        cont.finish()
        Task { try? await a.finalizeAndFinishThroughEndOfInput() }
    }

    func cancel() {
        lock.lock(); let a = analyzer; let was = done; done = true; ended = true; lock.unlock()
        if !was { Ears.log("ears #\(id): cancelled at \(ms()) ms (audio in \(nIn), pushed \(nPushed), results \(nRes))") }
        cont.finish()
        if let a = a { Task { await a.cancelAndFinishNow() } }
    }
}
