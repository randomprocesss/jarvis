#!/bin/sh
# Restart the Jarvis server with new code, safely, on a Mac where Jarvis.app looks after it. The server is never started
# from a shell here: the app starts it, and its watchdog starts a new one within 15 s whenever none is listening.
#  1. if Jarvis.app is not running, open it (it starts the server itself)
#  2. stop the running server; the app's watchdog starts the new one
#  3. wait for /healthz (max 60 s); exit 1 if it does not come back
# The port comes from JARVIS_PORT (the server sets it for what it starts), else 8765; the data home from JARVIS_HOME.
PORT="${JARVIS_PORT:-8765}"
DATA="${JARVIS_HOME:-$HOME/.jarvis}"
APP="${JARVIS_APP:-$HOME/Applications/Jarvis.app}"
PIDF="$DATA/state/server-$PORT.pid"
PY="${JARVIS_PYTHON:-python3}"
if ! pgrep -f 'Jarvis.app/Contents/MacOS/Jarvis' >/dev/null; then
  echo "Jarvis.app was not running: opening it"; open "$APP"; sleep 5
fi
# A pid file can be stale (the script would stop nothing and take the old server for the new one): ask the server itself.
old=$(curl -s -m 2 "http://127.0.0.1:$PORT/healthz" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["pid"])' 2>/dev/null)
[ -z "$old" ] && old=$(cat "$PIDF" 2>/dev/null)
[ -n "$old" ] && kill "$old" 2>/dev/null
i=0
while [ $i -lt 60 ]; do
  new=$(curl -s -m 2 "http://127.0.0.1:$PORT/healthz" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["pid"])' 2>/dev/null)
  if [ -n "$new" ] && [ "$new" != "$old" ]; then echo "server back (pid $new) after ${i}s"; exit 0; fi
  sleep 1; i=$((i+1))
done
echo "SERVER DID NOT COME BACK in 60 s"; exit 1
