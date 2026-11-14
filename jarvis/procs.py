"""Starting, finding and stopping the processes Jarvis runs, the same way on macOS, Linux and Windows."""
import json
import os
import signal
import subprocess
import time
import urllib.request


def spawn_detached(cmd, env=None, cwd=None, log=None):
    """Start `cmd` so that it outlives this process (its own session or process group). Output goes to the file `log`
    (appended) or nowhere. Returns the Popen object."""
    out = open(log, 'a') if log else subprocess.DEVNULL
    kw = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT if log else subprocess.DEVNULL)
    if os.name == 'nt':
        kw['creationflags'] = getattr(subprocess, 'DETACHED_PROCESS', 0x8) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200)
    else:
        kw['start_new_session'] = True
    return subprocess.Popen(cmd, **kw)


def alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True)
        return str(pid) in (r.stdout or '')
    try:                                             # a child of ours that has ended stays listed until it is collected
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except (ChildProcessError, OSError):
        pass                                         # not our child: the signal below tells
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop(pid, wait=10.0) -> bool:
    """Ask a process to end, and wait for it. True when it is gone."""
    if not alive(pid):
        return True
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        pass
    t0 = time.time()
    while time.time() - t0 < wait:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


def health(port, timeout=2.0):
    """What the Jarvis server on `port` says about itself, or None when nothing answers there."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{int(port)}/healthz', timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8', 'replace'))
            return d if isinstance(d, dict) and d.get('ok') else None
    except Exception:  # noqa: BLE001  nothing there, or not Jarvis
        return None


def wait_up(port, secs=20.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        h = health(port, 1.0)
        if h:
            return h
        time.sleep(0.3)
    return None
