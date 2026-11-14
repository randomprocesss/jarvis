"""Where the owner's Anthropic API key is kept, when they run Jarvis with a key in place of a Claude plan.

Never in a file of Jarvis's, never in the settings, never in a log. In order:
  1. ANTHROPIC_API_KEY in the environment Jarvis was started with: used as it is, nothing is stored.
  2. The system's password store: the macOS Keychain (`security`), or the Secret Service on Linux (`secret-tool`).
On a computer without either (Windows, a Linux server), set ANTHROPIC_API_KEY in your environment instead.

The key goes to the store through the tool's standard input, never on a command line (where other programs could see
it in the process list). Nothing here prints or returns the key except get(), which only the Claude launcher calls.

For tests only: JARVIS_KEYSTORE=file:<path> keeps the key in that file.
"""
import getpass
import os
import re
import shutil
import subprocess
import sys

SERVICE = 'jarvis-anthropic-api-key'
SHAPE = re.compile(r'^sk-ant-[A-Za-z0-9_-]{20,300}$')


class KeyError_(ValueError):
    """Why the key could not be kept or read. The message says what to do; it never contains the key."""


def _account():
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return 'jarvis'


def backend():
    """'env' | 'file' | 'keychain' | 'secret-tool' | '' (none here)."""
    t = os.environ.get('JARVIS_KEYSTORE', '')
    if t.startswith('file:'):
        return 'file'
    if sys.platform == 'darwin' and shutil.which('security'):
        return 'keychain'
    if sys.platform.startswith('linux') and shutil.which('secret-tool'):
        return 'secret-tool'
    return ''


def where():
    """In words, where the key is or would be kept."""
    if os.environ.get('ANTHROPIC_API_KEY'):
        return 'the ANTHROPIC_API_KEY setting of the environment Jarvis was started with'
    return {'file': 'a test file', 'keychain': 'your macOS Keychain', 'secret-tool': "your system's password store (Secret Service)"}.get(
        backend(), 'nowhere: this computer has no password store Jarvis can use. Set ANTHROPIC_API_KEY in your environment instead.')


def put(key):
    """Keep the key in the password store. Raises KeyError_ with what to do."""
    key = str(key or '').strip()
    if not SHAPE.match(key):
        raise KeyError_('That does not look like an Anthropic API key (they start with sk-ant-). Nothing was saved.')
    b = backend()
    if b == 'file':
        p = os.environ['JARVIS_KEYSTORE'][5:]
        os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(key)
        return
    if b == 'keychain':
        # `security -i` reads its commands from standard input, so the key never appears in the process list
        cmd = f'add-generic-password -U -s {SERVICE} -a "{_account()}" -l "Jarvis: Anthropic API key" -w "{key}"\n'
        r = subprocess.run(['security', '-i'], input=cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or 'error' in (r.stderr or '').lower():
            raise KeyError_('The Keychain did not take the key. Unlock your login keychain and try again.')
        return
    if b == 'secret-tool':
        r = subprocess.run(['secret-tool', 'store', '--label=Jarvis: Anthropic API key', 'service', SERVICE, 'account', _account()],
                           input=key, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise KeyError_('The password store did not take the key. Unlock it and try again.')
        return
    raise KeyError_(where())


def get():
    """The key, or '' when there is none. Only the Claude launcher calls this."""
    k = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if k:
        return k
    b = backend()
    try:
        if b == 'file':
            with open(os.environ['JARVIS_KEYSTORE'][5:]) as f:
                return f.read().strip()
        if b == 'keychain':
            r = subprocess.run(['security', 'find-generic-password', '-s', SERVICE, '-a', _account(), '-w'], capture_output=True, text=True, timeout=30)
            return r.stdout.strip() if r.returncode == 0 else ''
        if b == 'secret-tool':
            r = subprocess.run(['secret-tool', 'lookup', 'service', SERVICE, 'account', _account()], capture_output=True, text=True, timeout=30)
            return r.stdout.strip() if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''
    return ''


def has():
    return bool(get())


def remove():
    """Take the key out of the store (the environment setting, if any, is the owner's to remove)."""
    b = backend()
    if b == 'file':
        try:
            os.remove(os.environ['JARVIS_KEYSTORE'][5:])
        except OSError:
            pass
    elif b == 'keychain':
        subprocess.run(['security', 'delete-generic-password', '-s', SERVICE, '-a', _account()], capture_output=True, timeout=30)
    elif b == 'secret-tool':
        subprocess.run(['secret-tool', 'clear', 'service', SERVICE, 'account', _account()], capture_output=True, timeout=30)
