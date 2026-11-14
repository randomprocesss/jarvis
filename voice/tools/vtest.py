#!/usr/bin/env python3
"""Drive Jarvis.app's test hooks (settings.json "test_hooks": true). Synthetic audio only.
  vtest.py feed <wav> --tag T [--engine] [--when speaking] [--say TEXT] [--no-wake] [--delay MS] [--tail MS] [--wait S] [--quiet]
           [--then <wav> --then-delay MS]   (a second clip once Jarvis speaks: barge-in tests)
  vtest.py sleep | stop
Prints the test's final text (synthetic) and the turn's metrics line."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from place import DATA as V      # noqa: E402  the owner's voice data folder
CMD, OUT, MET = os.path.join(V, 'test', 'cmd.json'), os.path.join(V, 'test', 'out'), os.path.join(V, 'metrics.ndjson')
FEEDS = os.path.join(V, 'test', 'feeds')


def drop(o):
    while os.path.exists(CMD):
        time.sleep(0.05)
    tmp = CMD + '.tmp'
    json.dump(o, open(tmp, 'w')); os.rename(tmp, CMD)
    t0 = time.time()
    while os.path.exists(CMD) and time.time() - t0 < 5:
        time.sleep(0.05)
    if os.path.exists(CMD):
        os.remove(CMD); sys.exit('the app did not take the command (is Jarvis.app running with test_hooks on?)')


def arg(k, d=None):
    a = sys.argv
    return a[a.index(k) + 1] if k in a else d


def main():
    c = sys.argv[1]
    if c in ('sleep', 'stop'):
        drop({'cmd': 'sleep' if c == 'sleep' else 'stop_speaking'}); return
    wav = sys.argv[2]
    wav = wav if os.path.isabs(wav) else os.path.join(FEEDS, wav)
    tag = arg('--tag', os.path.splitext(os.path.basename(wav))[0])
    o = {'cmd': 'feed', 'path': wav, 'tag': tag, 'wake': '--no-wake' not in sys.argv, 'when': arg('--when', 'now')}
    for k, n in (('--delay', 'delay_ms'), ('--tail', 'tail_ms')):
        if arg(k): o[n] = float(arg(k))
    if arg('--say'): o['say'] = arg('--say')
    if '--quiet' in sys.argv: o['quiet'] = True          # finals never reach the brain (speech-to-text accuracy runs)
    if '--engine' in sys.argv:                             # Listening is off: start the engine for tests first (it stops itself later)
        drop({'cmd': 'feed', 'path': wav, 'engine': True}); time.sleep(2.5)
    if arg('--then'):
        tw = arg('--then'); tw = tw if os.path.isabs(tw) else os.path.join(FEEDS, tw)
        o['then'] = {'path': tw, 'delay_ms': float(arg('--then-delay', 600)), 'tail_ms': float(arg('--then-tail', 2500))}
        if '--then-after-speech' in sys.argv: o['then']['after'] = 'speech'      # a follow-up once the answer has finished
    if arg('--voice'): o['voice'] = arg('--voice')                                # this test conversation's voice only
    outp = os.path.join(OUT, tag + '.json')
    if os.path.exists(outp): os.remove(outp)
    drop(o)
    wait = float(arg('--wait', 60))
    t0 = time.time()
    while not os.path.exists(outp) and time.time() - t0 < wait:
        time.sleep(0.2)
    if not os.path.exists(outp):
        print(json.dumps({'tag': tag, 'error': 'no result file'})); return
    r = json.load(open(outp))
    tids = list(dict.fromkeys(r.get('turns') or []))     # a corrected turn is listed twice: wait for each id once
    rows = {}
    while tids and time.time() - t0 < wait:
        for line in open(MET):
            for tid in tids:
                if '"%s"' % tid in line:
                    rows[tid] = json.loads(line)
        if len(rows) == len(tids): break
        time.sleep(0.5)
    print(json.dumps({'tag': tag, 'finals': r.get('finals') or [], 'metrics': [rows.get(t) for t in tids], 'out': r}, sort_keys=True))


if __name__ == '__main__':
    main()
