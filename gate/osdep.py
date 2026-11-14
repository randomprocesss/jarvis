"""What the deploy gate needs from the operating system, in one place: a lock on a file, a child in its own process
group that can be stopped as a whole, and keeping the computer awake while a review runs.

These exist on macOS and Linux. On Windows there is no flock and no process group to signal, so `why_not()` says the gate
is not available there, and every command of the gate says so and stops before it does anything. Nothing else in gate/
may import fcntl or call killpg: it goes through here.
"""
import os
import shutil
import signal
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:                                  # Windows
    fcntl = None


def why_not():
    """'' when the gate can run on this computer, else the reason in one sentence."""
    if fcntl is None or not hasattr(os, 'killpg'):
        return ('The deploy gate is not available on Windows yet: it needs file locks and process groups, which this '
                'Python has only on macOS and Linux.')
    return ''


def _need():
    why = why_not()
    if why:
        raise RuntimeError(why)


def lock(f, wait=True):
    """Take the lock on an open file. With wait=False: True when taken, False when another process holds it."""
    _need()
    if wait:
        fcntl.flock(f, fcntl.LOCK_EX)
        return True
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def unlock(f):
    if fcntl is not None:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass


def held(path):
    """Does another process hold the lock on this file right now? The lock dies with its process, so a crash never
    leaves one behind."""
    if fcntl is None or not os.path.exists(str(path)):
        return False
    with open(str(path), 'a') as f:
        if lock(f, wait=False):
            unlock(f)
            return False
        return True


def spawn(cmd, **kw):
    """Start a child in its own process group (and session), so stop() reaches everything it started."""
    _need()
    return subprocess.Popen(cmd, start_new_session=True, **kw)


def stop(proc, grace=10):
    """Stop a process started with spawn(), and everything in its group: ask first, wait up to `grace` seconds for it
    to end, then insist. Processes the user may not signal are skipped; the rest still get the signal."""
    if not hasattr(os, 'killpg'):
        return
    pid = getattr(proc, 'pid', proc)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(int(pid), sig)
        except (ProcessLookupError, PermissionError, ValueError, TypeError):
            pass
        if sig == signal.SIGTERM:
            try:
                proc.wait(timeout=grace) if hasattr(proc, 'wait') else time.sleep(grace)
            except subprocess.TimeoutExpired:
                pass
    if hasattr(proc, 'wait'):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def keep_awake(pid):
    """Keep the computer awake while this process lives (a review cut off by sleep is a wasted try). Only where the system
    has a tool for it (macOS); elsewhere nothing happens."""
    if sys.platform == 'darwin' and shutil.which('caffeinate'):
        try:
            subprocess.Popen(['caffeinate', '-dimsu', '-w', str(pid)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass
