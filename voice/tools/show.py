#!/usr/bin/env python3
"""Pretty-prints vtest.py output lines (stdin)."""
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line.startswith('{'): print(line); continue
    r = json.loads(line)
    print('==', r.get('tag'), r.get('error', ''), 'finals=', r.get('finals'))
    for m in r.get('metrics') or []:
        if not m: print('   (turn not written)'); continue
        L = m.get('lat') or {}
        print('   %s eot=%s first=%s answer=%s token=%s stt=%s | barge=%s stop=%s onset=%s false=%s cut=%s fast=%s opener=%s %s' % (
            m['turn'], L.get('eot'), L.get('first_audio'), L.get('answer_audio'), L.get('token'), L.get('stt'), m.get('barge'), m.get('barge_stop_ms'),
            m.get('barge_onset_ms'), m.get('false_barge'), m.get('cutoff'), m.get('fast_path'), m.get('opener'),
            {k: v for k, v in (m.get('info') or {}).items() if not k.startswith('f_')}))
