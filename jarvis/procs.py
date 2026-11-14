"""Starting, finding and stopping the processes Jarvis runs, the same way on macOS, Linux and Windows."""
import json
import os
import signal
import subprocess
import sys
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


# ---------------------------------------------------------------- the same on every system
# Locks, process groups and detached starts differ between macOS/Linux and Windows. Everything in Jarvis that needs them
# comes here, so there is one place that knows the difference.

def session_kw():
    """The keyword arguments for subprocess.Popen that start a process in a group of its own (so it can be paused or
    stopped with everything it started)."""
    if os.name == 'nt':
        return {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200)}
    return {'start_new_session': True}


def signal_group(pid, sig):
    """Send `sig` to a process and everything it started. On Windows, a stop ends the whole tree, and an interrupt is a
    Ctrl+Break to the process group (the process must have been started with session_kw())."""
    pid = int(pid)
    if os.name != 'nt':
        os.killpg(os.getpgid(pid), sig)
        return
    if sig == getattr(signal, 'SIGINT', None):
        os.kill(pid, getattr(signal, 'CTRL_BREAK_EVENT', 1))
    else:
        r = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
        if r.returncode not in (0, 128):
            raise ProcessLookupError(pid)


def flock(f, block=True):
    """Lock an open file for this process alone; with block=False, raise BlockingIOError when another holds it."""
    if os.name != 'nt':
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        return
    import msvcrt
    f.seek(0)
    while True:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if not block:
                raise BlockingIOError('locked by another process')
            time.sleep(0.2)


def funlock(f):
    if os.name != 'nt':
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)
        return
    import msvcrt
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def exit_file_wrapper(xf, cmd):
    """`cmd`, run so that its exit code is written to the file `xf` when it ends (read by the dispatcher even after a
    restart of the server). A small Python wrapper, so it works the same on every system."""
    code = ('import subprocess, sys\n'
            'try:\n    rc = subprocess.call(sys.argv[2:])\nexcept KeyboardInterrupt:\n    rc = 130\n'
            'open(sys.argv[1], "w").write(str(rc))\nsys.exit(rc)\n')
    return [sys.executable or 'python3', '-c', code, xf] + list(cmd)


def open_path(target):
    """Open a file, folder or address with the system's own program."""
    if sys.platform == 'darwin':
        return subprocess.run(['open', target], capture_output=True, timeout=20).returncode == 0
    if os.name == 'nt':
        try:
            os.startfile(target)  # noqa: S606  the owner's own file, opened at their press
            return True
        except OSError:
            return False
    return subprocess.run(['xdg-open', target], capture_output=True, timeout=20).returncode == 0


def others_running(pattern):
    """Is another process running whose command line matches the regular expression `pattern`? (Not this process.)
    False when it cannot be told on this computer."""
    import re
    me = os.getpid()
    try:
        if os.name == 'nt':
            r = subprocess.run(['powershell', '-NoProfile', '-Command',
                                'Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }'],
                               capture_output=True, text=True, timeout=20)
            rows = r.stdout.splitlines()
        else:
            r = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=20)
            rows = r.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return False
    rx = re.compile(pattern)
    for row in rows:
        pid, _, cmd = row.strip().partition(' ')
        if pid.isdigit() and int(pid) != me and rx.search(cmd):
            return True
    return False
