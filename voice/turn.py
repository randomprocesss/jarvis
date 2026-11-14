#!/usr/bin/env python3
"""Jarvis's turn-taking helper: Silero VAD (MIT) on 32 ms frames + Smart Turn v3.2 (BSD-2) on onnxruntime. Kept warm by Jarvis.app.

stdin: frames  b'A' + uint32 LE length + int16 PCM (16 kHz mono)   |   b'C' + uint32 LE length + JSON command
  commands: {"reset": true, "preroll_ms": 400}   a new turn starts (keeps the last preroll of audio); with a preroll (= a barge-in)
                                                 while their voice is on or stopped < 1.5 s ago, that voice's turn is kept
            {"mode": "listen" | "speaking"}      while Jarvis speaks it only reports voice, never ends a turn
            {"hold": true}                       the app saw an open ending ("check the"): no Smart Turn end this pause,
                                                 only the 2.0 s timeout (or more speech)
stdout: one JSON per line, times in ms of audio received ("t"):
  {"ev":"ready"}
  {"ev":"start","t"}                     their voice started (2 voiced frames)
  {"ev":"voiced","t","run_ms"}           the voice has gone on for 250 ms (barge-in evidence); once per stretch
  {"ev":"stop","t"}                      their voice stopped (VAD off)
  {"ev":"pause","t","silence_ms","p"}    200 ms of silence, Smart Turn says not finished yet (p < 0.5)
  {"ev":"eot","t","silence_ms","p","why","voice_end","pros"}  end of turn: why = smart (p >= 0.5) | timeout (2.0 s of silence);
                                         pros = pitch and loudness of their speech in the turn {f0 Hz, f0_st semitones, db} (mood)
  {"ev":"resume","t"}                    he went on before the turn ended (the pending end is cancelled)
Audio is never written anywhere.

Test:  turn.py --file clip.wav   (prints the events for the clip followed by 3 s of silence)
"""
import json, os, struct, sys, time, wave
import numpy as np
import onnxruntime as ort

from place import DATA as HERE, MODELS      # the models are in the owner's data folder
SR, FRAME, CTX = 16000, 512, 64            # 32 ms frames; Silero wants 64 samples of context in front
ON, OFF = 0.5, 0.35                        # VAD hysteresis (Silero's own threshold / neg_threshold)
STOP_MS, RESCORE_MS, MAX_SIL_MS, DONE_P = 200, 300, 2000, 0.5
VOICED_MS = 250
MAX_TURN_S = 8


def session(name, threads=1):
    o = ort.SessionOptions()
    o.intra_op_num_threads = threads
    o.inter_op_num_threads = 1
    o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    o.log_severity_level = 3
    return ort.InferenceSession(os.path.join(MODELS, name), sess_options=o, providers=['CPUExecutionProvider'])


# ---------------------------------------------------------------- Whisper log-mel features (numpy port of transformers'
# WhisperFeatureExtractor(chunk_length=8) as Smart Turn's inference.py calls it; checked against it in test/turn_features_check.py)
N_FFT, HOP, N_MELS, N_SAMPLES = 400, 160, 80, MAX_TURN_S * SR


def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    m = 3.0 * f / 200.0
    lr = f >= 1000.0
    return np.where(lr, 15.0 + np.log(np.maximum(f, 1e-9) / 1000.0) * (27.0 / np.log(6.4)), m)


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    f = 200.0 * m / 3.0
    lr = m >= 15.0
    return np.where(lr, 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0)), f)


def _mel_filters():
    fft_freqs = np.linspace(0, SR // 2, 1 + N_FFT // 2)
    mel = np.linspace(_hz_to_mel(0.0), _hz_to_mel(8000.0), N_MELS + 2)
    ff = _mel_to_hz(mel)
    diff = np.diff(ff)
    slopes = ff[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    fb = np.maximum(0, np.minimum(down, up))
    fb *= (2.0 / (ff[2:N_MELS + 2] - ff[:N_MELS]))[None, :]
    return fb                                               # (201, 80)


MEL_FB = _mel_filters()
WINDOW = np.hanning(N_FFT + 1)[:-1]                         # periodic Hann


def whisper_features(audio):
    """audio: float32 16 kHz. Last 8 s (zero-padded at the front), normalised, log-mel (80, 800)."""
    a = np.asarray(audio, dtype=np.float32)
    a = a[-N_SAMPLES:] if len(a) > N_SAMPLES else np.pad(a, (N_SAMPLES - len(a), 0))
    a = (a - a.mean()) / np.sqrt(a.var() + 1e-7)
    x = np.pad(a.astype(np.float64), N_FFT // 2, mode='reflect')
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * WINDOW, n=N_FFT, axis=1)) ** 2   # (frames, 201)
    mel = spec @ MEL_FB                                                  # (frames, 80)
    logm = np.log10(np.maximum(mel, 1e-10)).T[:, :-1]                    # (80, 800)
    logm = np.maximum(logm, logm.max() - 8.0)
    return ((logm + 4.0) / 4.0).astype(np.float32)


def prosody(a):
    """Pitch and loudness of their voiced speech in one turn (16 kHz float): f0 = median pitch (Hz), f0_st = pitch spread p10-p90
    (semitones: how lively the melody is), db = mean loudness (dBFS). {} when there is too little voice. ~5 ms for 8 s."""
    a = np.asarray(a, dtype=np.float32)
    fl, hop = 640, 320                                      # 40 ms frames, 20 ms apart
    if len(a) < fl * 6:
        return {}
    n = 1 + (len(a) - fl) // hop
    fr = a[np.arange(fl)[None, :] + hop * np.arange(n)[:, None]] * np.hanning(fl).astype(np.float32)
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    v = db > db.max() - 25                                  # speech, not the pauses around it
    if v.sum() < 6:
        return {}
    ac = np.fft.irfft(np.abs(np.fft.rfft(fr[v], n=2048, axis=1)) ** 2, axis=1)[:, :fl]
    ac /= ac[:, :1] + 1e-9
    lo, hi = SR // 400, SR // 70                            # 70-400 Hz
    lag = lo + ac[:, lo:hi].argmax(1)
    f0 = SR / lag[ac[np.arange(len(lag)), lag] > 0.45]      # clearly periodic frames only
    if len(f0) < 6:
        return {}
    p10, p50, p90 = np.percentile(f0, [10, 50, 90])
    return {'f0': round(float(p50), 1), 'f0_st': round(float(12 * np.log2(p90 / p10)), 2), 'db': round(float(db[v].mean()), 1)}


def in_run(tr):
    """Their voice is on now, or stopped less than 1.5 s ago in a turn that has not ended."""
    return tr.speaking or tr.on_frames > 0 or (tr.voice_end is not None and not tr.ended and tr.in_turn and tr.t - tr.voice_end < 1500)


class Turn:
    def __init__(self, out):
        self.out = out
        self.vad = session('silero_vad.onnx')
        self.st = session('smart-turn-v3.2-cpu.onnx', threads=2)
        self.mode = 'listen'
        self.reset_all()
        # warm both models once
        self.vad_prob(np.zeros(FRAME, np.float32)); self.done_prob(np.zeros(SR, np.float32))
        self.reset_all()

    def reset_all(self):
        self.state = np.zeros((2, 1, 128), np.float32)
        self.ctx = np.zeros(CTX, np.float32)
        self.pend = np.zeros(0, np.float32)
        self.t = 0.0                       # ms of audio received
        self.turn = np.zeros(0, np.float32)
        self.ring = np.zeros(0, np.float32)
        self.reset_turn()

    def reset_turn(self, keep_ms=0):
        keep = int(SR * keep_ms / 1000)
        self.turn = self.ring[-keep:].copy() if keep else np.zeros(0, np.float32)
        self.in_turn = keep > 0
        self.speaking = False              # VAD state (their voice)
        self.voiced_run = 0.0              # ms
        self.voiced_said = False
        self.on_frames = 0
        self.sil = 0.0                     # ms since their voice stopped
        self.voice_end = None
        self.next_score = None
        self.ended = False                 # the turn has ended; wait for new speech
        self.hold = False                  # no Smart Turn end in this pause (the app asked)

    def vad_prob(self, frame):
        x = np.concatenate([self.ctx, frame])[None, :].astype(np.float32)
        p, self.state = self.vad.run(None, {'input': x, 'state': self.state, 'sr': np.array(SR, dtype=np.int64)})
        self.ctx = frame[-CTX:]
        return float(np.squeeze(p))

    def done_prob(self, audio):
        f = whisper_features(audio)[None]
        y = float(np.squeeze(self.st.run(None, {'input_features': f})[0]))
        return y if 0.0 <= y <= 1.0 else float(1 / (1 + np.exp(-y)))

    def say(self, o):
        o['t'] = round(self.t)
        self.out.write(json.dumps(o) + '\n'); self.out.flush()

    def command(self, c):
        if c.get('mode') in ('listen', 'speaking'):
            self.mode = c['mode']
        if c.get('reset'):
            keep = float(c.get('preroll_ms') or 0)
            if keep and in_run(self):
                # a barge-in: the voice that caused it IS their new turn. Keep it (still going, or stopped < 1.5 s ago: the words
                # lag the voice) and end it the usual way; a reset here would lose it and the turn would never end
                if not self.speaking:
                    self.next_score = max(self.sil, STOP_MS)
                self.hold = False
            else:
                self.reset_turn(keep)
        if c.get('hold'):
            self.hold = True
            self.ended = False
        if c.get('ping'):
            self.say({'ev': 'pong'})

    def audio(self, pcm):
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.pend = np.concatenate([self.pend, a])
        while len(self.pend) >= FRAME:
            fr, self.pend = self.pend[:FRAME], self.pend[FRAME:]
            self.frame(fr)

    def frame(self, fr):
        self.t += 1000.0 * FRAME / SR
        self.ring = np.concatenate([self.ring, fr])[-SR * MAX_TURN_S:]
        p = self.vad_prob(fr)
        voiced = p >= ON if not self.speaking else p >= OFF
        if self.in_turn or voiced:
            self.turn = np.concatenate([self.turn, fr])[-SR * MAX_TURN_S:]
        if voiced:
            self.on_frames += 1
            if not self.speaking and self.on_frames >= 2:
                self.speaking = True
                if not self.in_turn or self.ended:
                    self.reset_turn(400)         # a new turn: it starts 0.4 s before the voice
                    self.speaking = True; self.on_frames = 2
                    self.say({'ev': 'start'})
                elif self.sil > 0:
                    self.say({'ev': 'resume'})   # he went on before the end
                self.sil = 0.0; self.next_score = None; self.voice_end = None; self.hold = False
            if self.speaking:
                self.voiced_run += 1000.0 * FRAME / SR
                if self.voiced_run >= VOICED_MS and not self.voiced_said:
                    self.voiced_said = True
                    self.say({'ev': 'voiced', 'run_ms': round(self.voiced_run)})
            return
        self.on_frames = 0
        if self.speaking:
            self.speaking = False
            self.say({'ev': 'stop'})
            self.voice_end = self.t - 1000.0 * FRAME / SR
            self.voiced_run = 0.0; self.voiced_said = False
            self.sil = 0.0
            self.next_score = STOP_MS
        if self.voice_end is None or self.ended or not self.in_turn:
            return
        self.sil = self.t - self.voice_end
        if self.mode == 'speaking':
            return
        if self.sil >= MAX_SIL_MS:
            self.end(None, 'timeout')
        elif self.next_score is not None and self.sil >= self.next_score and not self.hold:
            t0 = time.time()
            pr = self.done_prob(self.turn)
            ms = (time.time() - t0) * 1000
            if pr >= DONE_P:
                self.end(pr, 'smart', ms)
            else:
                self.say({'ev': 'pause', 'silence_ms': round(self.sil), 'p': round(pr, 3), 'score_ms': round(ms, 1)})
                self.next_score = self.sil + RESCORE_MS

    def end(self, p, why, score_ms=None):
        self.ended = True
        o = {'ev': 'eot', 'silence_ms': round(self.sil), 'why': why, 'voice_end': round(self.voice_end)}
        if p is not None:
            o['p'] = round(p, 3)
        if score_ms is not None:
            o['score_ms'] = round(score_ms, 1)
        try:
            pr = prosody(self.turn)
            if pr:
                o['pros'] = pr
        except Exception:
            pass
        self.say(o)


def main_stream():
    out = sys.stdout
    tr = Turn(out)
    out.write(json.dumps({'ev': 'ready'}) + '\n'); out.flush()
    src = sys.stdin.buffer
    while True:
        h = src.read(5)
        if len(h) < 5:
            break                                   # the app closed the pipe: stop
        kind, n = h[:1], struct.unpack('<I', h[1:])[0]
        body = src.read(n) if n else b''
        if len(body) < n:
            break
        if kind == b'A':
            tr.audio(body)
        elif kind == b'C':
            try:
                tr.command(json.loads(body.decode('utf-8')))
            except Exception:
                pass


def read_wav(path):
    with wave.open(path, 'rb') as f:
        assert f.getframerate() == SR and f.getnchannels() == 1 and f.getsampwidth() == 2, 'need 16 kHz mono int16'
        return f.readframes(f.getnframes())


if __name__ == '__main__':
    if '--file' in sys.argv:
        class Cap:
            def __init__(self): self.lines = []
            def write(self, s): self.lines.append(s)
            def flush(self): pass
        cap = Cap(); tr = Turn(cap)
        pcm = read_wav(sys.argv[sys.argv.index('--file') + 1]) + b'\x00\x00' * (SR * 3)
        for k in range(0, len(pcm), 1024):
            tr.audio(pcm[k:k + 1024])
        sys.stdout.write(''.join(cap.lines))
    else:
        main_stream()
