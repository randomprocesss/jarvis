#!/usr/bin/env python3
"""Jarvis wake-word helper. Reads 16 kHz mono int16 PCM on stdin (from Jarvis.app), prints one line per detection:
    WAKE <model> <score>
Audio is never written anywhere. Runs openWakeWord's feature pipeline (melspectrogram -> speech embedding, Apache-2.0)
directly on onnxruntime + numpy, single-threaded to keep CPU low.

Model: the personal one-word "Jarvis" model (personal.npz, made by train.py from the owner's enrolment) when it exists,
otherwise the pretrained "Hey Jarvis" model (hey_jarvis_v0.1.onnx, CC BY-NC-SA, personal use).

Also usable for tests:  wake.py --file clip.wav [--model hey|personal|path.npz]   (prints scores, exit 0)
"""
import os, sys, time, wave
import numpy as np
import onnxruntime as ort

from place import DATA, MODELS      # code is this folder; the models and the owner's own wake model are in their data folder

HERE = DATA
PERSONAL = os.path.join(DATA, 'personal.npz')
CHUNK = 1280            # 80 ms at 16 kHz: one embedding step
HEY_THRESHOLD = 0.5


def session(name):
    o = ort.SessionOptions()
    o.intra_op_num_threads = 1
    o.inter_op_num_threads = 1
    o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    o.log_severity_level = 3
    return ort.InferenceSession(os.path.join(MODELS, name), sess_options=o, providers=['CPUExecutionProvider'])


class Features:
    """openWakeWord's streaming AudioFeatures, trimmed: 80 ms chunks in, one 96-d embedding out per chunk."""

    def __init__(self):
        self.mel = session('melspectrogram.onnx')
        self.emb = session('embedding_model.onnx')
        self.mel_in = self.mel.get_inputs()[0].name
        self.emb_in = self.emb.get_inputs()[0].name
        self.reset()

    def reset(self):
        self.raw = np.zeros(0, dtype=np.int16)
        self.mels = np.ones((76, 32), dtype=np.float32)
        self.embs = np.zeros((0, 96), dtype=np.float32)

    def push(self, chunk):
        """chunk: exactly CHUNK int16 samples. Returns the new embedding (96,)."""
        self.raw = np.concatenate([self.raw, chunk])[-(CHUNK + 160 * 3):]
        x = self.raw.astype(np.float32)[None, :]
        spec = np.squeeze(self.mel.run(None, {self.mel_in: x})[0]) / 10 + 2
        self.mels = np.vstack([self.mels, spec])[-200:]
        win = self.mels[-76:].astype(np.float32)[None, :, :, None]
        e = np.squeeze(self.emb.run(None, {self.emb_in: win})[0]).astype(np.float32)
        self.embs = np.vstack([self.embs, e[None]])[-32:]
        return e

    def window(self):
        """the last 16 embeddings (1.28 s), or None while warming up"""
        return self.embs[-16:] if len(self.embs) >= 16 else None


class HeyJarvis:
    name = 'hey_jarvis'

    def __init__(self):
        self.s = session('hey_jarvis_v0.1.onnx')
        self.i = self.s.get_inputs()[0].name
        self.threshold = HEY_THRESHOLD

    def score(self, w):
        return float(np.squeeze(self.s.run(None, {self.i: w[None].astype(np.float32)})[0]))


class Personal:
    """Logistic regression over the standardised, flattened 16x96 window (see train.py)."""
    name = 'personal'

    def __init__(self, path=PERSONAL):
        d = np.load(path)
        self.mu, self.sd, self.w, self.b = d['mu'], d['sd'], d['w'], float(d['b'])
        self.threshold = float(d['threshold'])

    def score(self, w):
        z = (w.reshape(-1) - self.mu) / self.sd
        return float(1 / (1 + np.exp(-(z @ self.w + self.b))))


def load_model(which=None):
    if which in (None, 'auto'):
        which = 'personal' if os.path.exists(PERSONAL) else 'hey'
    if which == 'hey':
        return HeyJarvis()
    return Personal(PERSONAL if which == 'personal' else which)


def read_wav(path):
    with wave.open(path, 'rb') as f:
        assert f.getframerate() == 16000 and f.getnchannels() == 1 and f.getsampwidth() == 2, 'need 16 kHz mono int16'
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)


def scores_for(audio, model, feats=None):
    """Score every 80 ms step of an int16 clip (with 1.3 s of silence before it so the window can fill)."""
    feats = feats or Features()
    feats.reset()
    a = np.concatenate([np.zeros(16000 + 4800, np.int16), audio, np.zeros(8000, np.int16)])
    out = []
    for k in range(0, len(a) - CHUNK + 1, CHUNK):
        feats.push(a[k:k + CHUNK])
        w = feats.window()
        if w is not None:
            out.append(model.score(w))
    return np.array(out)


def main_stream(model):
    feats = Features()
    buf = b''
    last = 0.0
    sys.stdout.write(f'READY {model.name}\n'); sys.stdout.flush()
    src = sys.stdin.buffer
    while True:
        data = src.read(CHUNK * 2 - len(buf)) if len(buf) < CHUNK * 2 else b''
        if not data and len(buf) < CHUNK * 2:
            break  # the app closed the pipe (quit): stop
        buf += data
        if len(buf) < CHUNK * 2:
            continue
        chunk = np.frombuffer(buf[:CHUNK * 2], dtype=np.int16)
        buf = buf[CHUNK * 2:]
        feats.push(chunk)
        w = feats.window()
        if w is None:
            continue
        s = model.score(w)
        now = time.monotonic()
        if s >= model.threshold and now - last > 1.5:
            last = now
            sys.stdout.write(f'WAKE {model.name} {s:.3f}\n'); sys.stdout.flush()


if __name__ == '__main__':
    args = sys.argv[1:]
    which = args[args.index('--model') + 1] if '--model' in args else None
    m = load_model(which)
    if '--file' in args:
        sc = scores_for(read_wav(args[args.index('--file') + 1]), m)
        print(f'{m.name} max={sc.max():.3f} threshold={m.threshold:.3f} detected={bool((sc >= m.threshold).any())}')
    else:
        main_stream(m)
