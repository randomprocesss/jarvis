#!/usr/bin/env python3
"""Train Jarvis's PERSONAL one-word "Jarvis" wake model from an enrolment.

    train.py <enrol_dir> [--out personal.npz] [--keep-clips] [--holdout-voice NAME] [--exclude-voice NAME]

<enrol_dir>/pos/*.wav  ~30 clips of the owner saying "Jarvis" (16 kHz mono int16, about 2 s each)
<enrol_dir>/neg/*.wav  their normal talk (the read paragraph, 60-90 s)

Features are openWakeWord's speech embeddings (wake.Features) over sliding 1.28 s windows. Positives: windows that end
just after each "Jarvis" (found by energy), augmented with noise, gain and slight speed changes. Negatives: their normal
talk, other macOS voices (several saying "Jarvis", so the model answers to THEIR voice only), normal sentences, silence
and noise. A logistic regression (numpy, L2) learns them in seconds; the threshold is tuned for near-zero false wakes on
the negatives. The raw clips are deleted afterwards unless --keep-clips. Prints one JSON line with the result.
"""
import glob, json, os, subprocess, sys, shutil
import numpy as np
import wake

HERE = wake.HERE
CACHE = os.path.join(HERE, 'say_cache')       # synthetic negatives (stock voices), regenerated if missing
NEG_VOICES = ['Samantha', 'Karen', 'Moira', 'Fred', 'Albert', 'Aman', 'Ralph', 'Kathy', 'Eddy (English (US))',
              'Flo (English (UK))', 'Grandpa (English (UK))', 'Rishi', 'Tessa', 'Daniel']
NEG_SENTENCES = ['Jarvis', 'Jarvis', 'Hey Jarvis', 'Jarvis, open the inbox', 'Where is the March report?',
                 'I will send the reply after lunch.', 'The tickets are ready for review this afternoon.',
                 'Can you check the September numbers with Dana?', 'Let us move the meeting to Thursday.']
rng = np.random.default_rng(7)


def say_clip(voice, text):
    os.makedirs(CACHE, exist_ok=True)
    key = (voice + '_' + text).replace(' ', '_').replace('(', '').replace(')', '').replace(',', '').replace('?', '').replace('.', '')
    p = os.path.join(CACHE, key[:80] + '.wav')
    if not os.path.exists(p):
        r = subprocess.run(['say', '-v', voice, text, '-o', p, '--data-format=LEI16@16000'], capture_output=True)
        if r.returncode != 0 or not os.path.exists(p):
            return None
    return wake.read_wav(p)


def word_span(a):
    """start/end sample of the loudest continuous speech in a clip (20 ms frames, energy above 25% of the peak)."""
    f = 320
    n = len(a) // f
    if n == 0:
        return 0, len(a)
    e = np.sqrt((a[:n * f].astype(np.float32).reshape(n, f) ** 2).mean(1))
    on = np.where(e > max(e.max() * 0.25, 200))[0]
    if not len(on):
        return 0, len(a)
    return int(on[0] * f), int((on[-1] + 1) * f)


def augment(a):
    """slight speed change (0.92-1.08), gain (0.25-3x: raw mic levels vary a lot) and low noise"""
    sp = rng.uniform(0.92, 1.08)
    idx = np.arange(0, len(a) - 1, sp)
    b = np.interp(idx, np.arange(len(a)), a.astype(np.float32))
    b = b * np.exp(rng.uniform(np.log(0.25), np.log(3.0))) + rng.normal(0, rng.uniform(20, 250), len(b))
    return np.clip(b, -32768, 32767).astype(np.int16)


def windows(feats, a, pre=16000 + 4800):
    """all (end_sample, 16x96 window) pairs for a clip with `pre` samples of silence in front"""
    feats.reset()
    x = np.concatenate([np.zeros(pre, np.int16), a, np.zeros(6400, np.int16)])
    out = []
    for k in range(0, len(x) - wake.CHUNK + 1, wake.CHUNK):
        feats.push(x[k:k + wake.CHUNK])
        w = feats.window()
        if w is not None:
            out.append((k + wake.CHUNK - pre, w.reshape(-1).copy()))
    return out


def pos_windows(feats, a):
    s, e = word_span(a)
    a = a[max(0, s - 1600):e + 1600]
    s, e = 1600 if s >= 1600 else s, len(a) - 1600
    return [w for end, w in windows(feats, a) if e - 640 <= end <= e + 4500]


def neg_windows(feats, a, step=1):
    return [w for _, w in windows(feats, a)][::step]


def fit(X, y, l2=1e-2, iters=400, lr=0.5):
    mu, sd = X.mean(0), X.std(0) + 1e-3
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1]), 0.0
    pw = (len(y) - y.sum()) / max(1, y.sum())          # balance the classes
    sw = np.where(y == 1, pw, 1.0)
    sw = sw / sw.mean()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        g = (p - y) * sw
        w -= lr * (Z.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return mu, sd, w, b


def score(mu, sd, w, b, X):
    return 1 / (1 + np.exp(-(((X - mu) / sd) @ w + b)))


def clip_max(feats, model, a):
    return float(wake.scores_for(a, model, feats).max())


def main():
    args = sys.argv[1:]
    d = args[0]
    out = args[args.index('--out') + 1] if '--out' in args else wake.PERSONAL
    hold = args[args.index('--holdout-voice') + 1] if '--holdout-voice' in args else None
    excl = args[args.index('--exclude-voice') + 1] if '--exclude-voice' in args else None   # tests: the stand-in speaker
    feats = wake.Features()
    pos = [wake.read_wav(p) for p in sorted(glob.glob(os.path.join(d, 'pos', '*.wav')))]
    talk = [wake.read_wav(p) for p in sorted(glob.glob(os.path.join(d, 'neg', '*.wav')))]
    if len(pos) < 8:
        print(json.dumps({'ok': False, 'error': f'only {len(pos)} "Jarvis" clips; need at least 8'})); return
    # hold back a fifth of the positives to check recall honestly
    order = rng.permutation(len(pos))
    test_pos = [pos[i] for i in order[:max(2, len(pos) // 5)]]
    train_pos = [pos[i] for i in order[max(2, len(pos) // 5):]]

    Xp = []
    for a in train_pos:
        Xp += pos_windows(feats, a)
        for _ in range(6):
            Xp += pos_windows(feats, augment(a))
    Xn = []
    for a in talk:                                         # their normal talk, in ~6 s pieces
        for k in range(0, len(a), 16000 * 6):
            Xn += neg_windows(feats, a[k:k + 16000 * 6])
    voices = [v for v in NEG_VOICES if v not in (hold, excl)]
    syn = []
    for v in voices:
        for t in NEG_SENTENCES:
            c = say_clip(v, t)
            if c is not None:
                syn.append(c)
                Xn += neg_windows(feats, c)
                Xn += neg_windows(feats, augment(c), step=2)
    noise = (rng.normal(0, 300, 16000 * 8)).astype(np.int16)
    Xn += neg_windows(feats, noise) + neg_windows(feats, np.zeros(16000 * 4, np.int16))
    X = np.array(Xp + Xn, dtype=np.float32)
    y = np.array([1] * len(Xp) + [0] * len(Xn), dtype=np.float64)
    mu, sd, w, b = fit(X, y)

    # threshold: just above the highest negative window (their talk + other voices), never below 0.5
    neg_s = score(mu, sd, w, b, np.array(Xn, dtype=np.float32))
    thr = float(min(0.97, max(0.5, np.max(neg_s) + 0.04)))
    # Written to a temp file first: it only replaces the live model when it really hears them (held-out recall >= 0.8),
    # so a poor enrolment can never take away the working "Hey Jarvis" fallback or an earlier good model.
    tmp = out + '.new.npz'
    np.savez(tmp, mu=mu.astype(np.float32), sd=sd.astype(np.float32), w=w.astype(np.float32), b=np.float32(b), threshold=np.float32(thr))
    model = wake.Personal(tmp)
    recall = float(np.mean([clip_max(feats, model, a) >= thr for a in test_pos]))
    if recall >= 0.8:
        os.replace(tmp, out)
    else:
        os.remove(tmp)
    res = {'ok': recall >= 0.8, 'model': out if recall >= 0.8 else None, 'threshold': round(thr, 3), 'held_out_recall': round(recall, 2),
           'train_pos_windows': len(Xp), 'neg_windows': len(Xn), 'neg_max': round(float(np.max(neg_s)), 3)}
    if hold:
        hv = [say_clip(hold, t) for t in ['Jarvis', 'Hey Jarvis', 'Jarvis, open the inbox']]
        res['holdout_voice'] = hold
        res['holdout_voice_max'] = round(max(clip_max(feats, model, a) for a in hv if a is not None), 3)
    if not res['ok']:
        res['error'] = f'It only recognised {round(recall * 100)}% of the test clips, so I kept the current model. Try again somewhere quieter.'
    if '--keep-clips' not in args:
        for sub in ('pos', 'neg'):
            shutil.rmtree(os.path.join(d, sub), ignore_errors=True)
        res['clips_deleted'] = True
    print(json.dumps(res))


if __name__ == '__main__':
    main()
