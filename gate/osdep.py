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
    """Stop a process started with spawn(), and everything it left in its group: ask, wait up to `grace` seconds for the
    group to empty, then insist. `proc` is the process, or its id. With nothing of the group left, nothing happens (so
    it is also the sweep after a child that ended by itself)."""
    if not hasattr(os, 'killpg'):
        return
    try:
        pid = int(getattr(proc, 'pid', proc))
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return                                       # nothing of it is left
    except (PermissionError, ValueError, TypeError):
        return                                       # not ours to signal
    end = time.monotonic() + grace
    while time.monotonic() < end:
        if not (hasattr(proc, 'poll') and proc.poll() is None):      # the child itself has ended (or is not ours to ask)
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return                               # and so has everything it started
            except PermissionError:
                pass
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
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
