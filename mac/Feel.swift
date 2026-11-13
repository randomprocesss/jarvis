// Feel (the owner, 3 Oct 2026: "do the emotion"): how he sounds beyond his words, from two on-device signals.
//  1. Here: macOS's built-in sound classifier (SoundAnalysis, 303 sounds, no download) on his voice while he talks: a laugh
//     (laughter, chuckle, giggle, belly laugh, snicker) or a sigh. Tested on 53 expressive clips: sighs and groans scored
//     0.82-0.96, plain speech stayed under 0.5. (SenseVoice, tried first, called every one of them neutral.)
//  2. turn.py: the pitch and loudness of each turn; jarvis_human.mood_hint compares them with his own running averages.
// Only his voice: nothing is fed while Jarvis is speaking (its own <sigh> and <chuckle> would count). Audio is never stored.
import AVFoundation
import SoundAnalysis

final class Feel: NSObject, SNResultsObserving {
    static let shared = Feel()
    static let laughs: Set<String> = ["laughter", "chuckle_chortle", "giggling", "belly_laugh", "snicker"]
    private let q = DispatchQueue(label: "jarvis.feel")
    private let lock = NSLock()
    private var analyzer: SNAudioStreamAnalyzer?
    private var fmt: AVAudioFormat?
    private var pos: AVAudioFramePosition = 0
    private var failed = false
    private var laugh = 0.0, sigh = 0.0, laughAt = Date.distantPast, sighAt = Date.distantPast

    /// A copy of one mono mic buffer (from the audio thread), while he is being listened to and Jarvis is silent.
    func feed(_ b: AVAudioPCMBuffer) {
        q.async { [self] in
            if failed { return }
            if analyzer == nil || fmt != b.format {
                let a = SNAudioStreamAnalyzer(format: b.format)
                do {
                    let r = try SNClassifySoundRequest(classifierIdentifier: .version1)
                    r.windowDuration = CMTimeMakeWithSeconds(1.0, preferredTimescale: 48000)
                    r.overlapFactor = 0.5
                    try a.add(r, withObserver: self)
                } catch {
                    failed = true; vlog("feel: the sound classifier is unavailable: \(error)"); return
                }
                analyzer = a; fmt = b.format; pos = 0
            }
            analyzer?.analyze(b, atAudioFramePosition: pos)
            pos += AVAudioFramePosition(b.frameLength)
        }
    }

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let r = result as? SNClassificationResult else { return }
        var l = 0.0, s = 0.0
        for c in r.classifications {
            if Feel.laughs.contains(c.identifier) { l = max(l, c.confidence) } else if c.identifier == "sigh" { s = max(s, c.confidence) }
        }
        let now = Date()
        lock.lock()
        if l >= 0.3 && (l >= laugh || now.timeIntervalSince(laughAt) > 12) { laugh = l; laughAt = now }   // a stale one never blocks a new one
        if s >= 0.3 && (s >= sigh || now.timeIntervalSince(sighAt) > 12) { sigh = s; sighAt = now }
        lock.unlock()
    }

    func request(_ request: SNRequest, didFailWithError error: Error) { vlog("feel: classifier error: \(error)") }

    /// The strongest laugh and sigh heard in the last `within` seconds (this turn), then cleared. Results trail the audio by
    /// up to a second, so this is read when the turn's words are sent, not reset when listening restarts.
    func take(within: Double = 12) -> (laugh: Double, sigh: Double) {
        lock.lock(); defer { laugh = 0; sigh = 0; lock.unlock() }
        let now = Date()
        return (now.timeIntervalSince(laughAt) < within ? laugh : 0, now.timeIntervalSince(sighAt) < within ? sigh : 0)
    }
}
