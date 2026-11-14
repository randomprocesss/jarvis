#!/usr/bin/env python3
"""Times POST /api/jarvis from the server's side: request -> ack, first progress, first speak sentence, final.
  brain_test.py "question" [--conv ID] [--port N]   (prints one JSON line; the conversation id is in it for follow-ups)"""
import json, os, sys, time, urllib.request
q = sys.argv[1]
conv = sys.argv[sys.argv.index('--conv') + 1] if '--conv' in sys.argv else None
port = sys.argv[sys.argv.index('--port') + 1] if '--port' in sys.argv else os.environ.get('JARVIS_PORT', '8765')
body = {'q': q, 'screen': {'route': 'home', 'page': 'Home', 'now': time.strftime('%a %H:%M')}}
if conv: body['conv'] = conv
req = urllib.request.Request(f'http://localhost:{port}/api/jarvis', data=json.dumps(body).encode(), method='POST',
                             headers={'Content-Type': 'application/json', 'Origin': f'http://localhost:{port}', 'Host': f'localhost:{port}'})
t0 = time.time(); out = {'q': q}
with urllib.request.urlopen(req, timeout=200) as r:
    for line in r:
        try: ev = json.loads(line)
        except Exception: continue
        t = round((time.time() - t0) * 1000)
        k = ev.get('type')
        if k == 'ack': out['ack_ms'] = t; out['conv'] = ev.get('conv')
        elif k == 'progress': out.setdefault('progress_ms', t); out.setdefault('progress', []).append(ev.get('text'))
        elif k == 'escalate': out['escalate_ms'] = t
        elif k == 'speak': out.setdefault('speak_ms', t); out.setdefault('speaks', []).append(ev.get('text'))
        elif k == 'final': out['final_ms'] = t; out['tier'] = ev.get('tier'); out['speak'] = ev.get('speak'); out['secs'] = ev.get('secs'); out['n_actions'] = len(ev.get('actions') or [])
        elif k == 'error': out['error'] = ev.get('error'); out['error_ms'] = t
print(json.dumps(out))
