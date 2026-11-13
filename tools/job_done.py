#!/usr/bin/env python3
"""Called by run_job.py's wrapper when a long job ends: asks Jarvis to start work on the item that owns the job right away
(collect the result, write it up, set the point states, start the next queued job), instead of waiting for the next routine run.
The item is read from the job folder name: work/outputs/<round-up id>-<action id>/... ; anything else is ignored."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import json, os, re, sys, urllib.request
OUTS = os.path.realpath(str(paths.home() / 'work/outputs'))
d = os.path.realpath(sys.argv[1]) if len(sys.argv) > 1 else ''
if not d.startswith(OUTS + os.sep):
    sys.exit(0)
m = re.match(r'^(\d{4}-\d{2}-\d{2}-\d{4}-[a-z]+)-(a\d+)$', os.path.relpath(d, OUTS).split(os.sep)[0])
if not m:
    sys.exit(0)
from jarvis import config
base = f"http://localhost:{config.load().get('server.port')}"
rq = urllib.request.Request(base + '/api/jarvis_task', method='POST', headers={'Content-Type': 'application/json', 'Origin': base},   # the server refuses a call without its own origin
                            data=json.dumps({'type': 'start_task', 'rid': m.group(1), 'aid': m.group(2)}).encode())
try:
    print(urllib.request.urlopen(rq, timeout=8).read().decode()[:300])
except Exception as e:
    print('could not reach Jarvis:', e)
