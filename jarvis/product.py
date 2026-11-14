"""The pieces that make Jarvis a product and not a folder of scripts: looking around on made-up data, taking a copy of
everything, erasing everything, and updating to a newer version. Each happens only on the owner's press.

    demo_start(b)       POST /api/demo_start     build the made-up data and open a second, separate Jarvis on it
    demo_leave(b)       POST /api/demo_leave     (asked of the demo itself) close the demo and go back to Setup
    erase_preview(q)    GET  /api/erase_preview  what "erase everything" would remove, with sizes
    export(b)           POST /api/export         a zip of everything Jarvis keeps, written to the owner's home folder
    erase(b)            POST /api/erase          {"confirm": "ERASE", "mode": "trash" | "forever"}
    update_state(q)     GET  /api/update         this version, and whether the repository has a newer release
    update(b)           POST /api/update         move to the newest release, then restart

No usage data, crash report or "check for updates" call ever leaves the computer on its own. The update check is one
`git ls-remote` of the repository this copy was cloned from, with the owner's own git sign-in, at most once a day.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config, paths, procs

GIT_ENV = {'GIT_TERMINAL_PROMPT': '0'}
PARTS = [  # top-level folders of the data home, as the owner would call them
    ('brain', 'The knowledge base and the notes it was built from'), ('roundups', 'Inbox round-ups'), ('work', 'Work files and drafts'),
    ('state', 'Jarvis\'s records: what it did, what it learned, trust levels'), ('voice', 'Voice settings, conversations and your wake-word model'),
    ('tickets', 'Ticket drafts'), ('logs', 'Logs'), ('secrets', 'Locked copies of files that held a secret'), ('envs', 'Python environments of the optional parts'),
    ('repos', 'Read-only copies of your repositories'), ('prwatch', 'The pull-request watch\'s snapshots'), ('demo', 'The made-up demo data'),
    ('config.toml', 'Your settings')]


def is_demo() -> bool:
    return os.environ.get('JARVIS_DEMO') == '1'


def main_port() -> int:
    try:
        return int(os.environ.get('JARVIS_MAIN_PORT') or os.environ.get('JARVIS_PORT') or config.load().get('server.port') or 8765)
    except (TypeError, ValueError):
        return 8765


def _later(fn, secs=0.6):
    t = threading.Timer(secs, fn)
    t.daemon = True
    t.start()


# ---------------------------------------------------------------- demo
def demo_start(b=None):
    """Build the made-up data in its own data home and start a second Jarvis on it, one port up. It has no Claude, runs
    nothing in the background and refuses every send: a place to look around, nothing more."""
    if is_demo():
        return {'ok': True, 'url': '/#/today'}, 200
    from . import demo
    port = main_port() + 1
    h = procs.health(port)
    if not h:
        home = demo.build(str(paths.demo_home()))
        env = dict(os.environ, JARVIS_HOME=home, JARVIS_DEMO='1', JARVIS_NO_BACKGROUND='1', JARVIS_CLAUDE=os.path.join(home, 'no-claude'),
                   JARVIS_MAIN_PORT=str(main_port()), CLAUDE_CONFIG_DIR=os.path.join(home, 'claude'))
        env.pop('JARVIS_PORT', None)
        procs.spawn_detached([paths.python(), '-m', 'jarvis.server', '--port', str(port)], env=env, cwd=str(paths.REPO), log=str(paths.logs('demo.log')))
        h = procs.wait_up(port, 20)
    if not h:
        return {'ok': False, 'error': f'The demo did not start. Its log says why: {paths.logs("demo.log")}'}, 500
    return {'ok': True, 'url': f'http://localhost:{port}/#/today', 'port': port}, 200


def demo_leave(b=None):
    """Asked of the demo itself: say where the owner's own Jarvis is, then end."""
    if not is_demo():
        return {'ok': False, 'error': 'This is not the demo.'}, 400
    _later(lambda: os._exit(0))
    return {'ok': True, 'url': f'http://localhost:{main_port()}/#/setup'}, 200


def demo_stop():
    """Stop a running demo (before an erase or an update)."""
    h = procs.health(main_port() + 1, 1.0)
    if h and h.get('pid') and h.get('demo'):      # only a demo: never another server that happens to use that port
        return procs.stop(h['pid'], 5)
    return False


# ---------------------------------------------------------------- what Jarvis keeps
def _size(p: Path) -> int:
    if p.is_symlink():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(str(p)):
        for f in files:
            try:
                fp = os.path.join(root, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _own_skills():
    """The worker skills Jarvis installed into Claude Code's skills folder (the ones that carry its mark)."""
    try:
        from . import skillset
        root = paths.claude_skills()
        return [root / n for n in skillset.names() if (root / n).is_dir()]
    except Exception:  # noqa: BLE001
        return []


def _own_runs():
    """Claude Code's records of Jarvis's own processing runs (they ran inside the data home)."""
    own, out = paths.own_runs_prefix(), []
    try:
        for d in paths.claude_chats().iterdir():
            if d.is_dir() and (d.name == own or d.name.startswith(own + '-')):
                out.append(d)
    except OSError:
        pass
    return out


def _safe_home():
    """The data home, only if it is plainly Jarvis's own folder. Erasing never trusts a setting blindly."""
    h = paths.home().resolve()
    bad = {Path.home().resolve(), Path(str(paths.REPO)).resolve(), Path(os.sep)}
    if h in bad or len(h.parts) < 3 or Path.home().resolve() in h.parents and len(h.relative_to(Path.home().resolve()).parts) == 0:
        return None
    if Path(str(paths.REPO)).resolve() in h.parents or h in Path(str(paths.REPO)).resolve().parents:
        return None
    if not ((h / 'config.toml').exists() or (h / 'state').is_dir()):
        return None
    return h


def erase_preview(q=None):
    h = paths.home()
    rows, known = [], {n for n, _ in PARTS}
    for name, what in PARTS:
        p = h / name
        if p.exists():
            rows.append({'name': name, 'what': what, 'bytes': _size(p)})
    for p in sorted(h.iterdir()) if h.is_dir() else []:
        if p.name not in known and not p.name.startswith('.'):
            rows.append({'name': p.name, 'what': 'Other files Jarvis made', 'bytes': _size(p)})
    extra = []
    sk = _own_skills()
    if sk:
        extra.append({'name': 'skills', 'what': f'The {len(sk)} worker skills Jarvis put into Claude Code', 'bytes': sum(_size(s) for s in sk)})
    runs = _own_runs()
    if runs:
        extra.append({'name': 'runs', 'what': "Claude Code's records of Jarvis's own runs", 'bytes': sum(_size(r) for r in runs)})
    return {'home': str(h), 'rows': rows, 'also': extra, 'bytes': sum(r['bytes'] for r in rows + extra),
            'stays': 'Your own Claude Code chats, your work folders and everything in your apps stay exactly as they are. '
                     'The Jarvis program itself (this folder of code) stays too.',
            'trash': _trash_place(h)[1], 'safe': _safe_home() is not None}


def export(b=None):
    """A zip of everything in the data home (without the demo data, the Python environments and the repository copies,
    which can all be made again), written next to the owner's other files so they can keep or move it."""
    h = paths.home()
    if not h.is_dir():
        return {'ok': False, 'error': 'There is nothing to copy yet.'}, 400
    out = Path.home() / f"Jarvis copy {dt.date.today().isoformat()}"
    n = 1
    while Path(str(out) + '.zip').exists():
        n += 1
        out = Path.home() / f"Jarvis copy {dt.date.today().isoformat()} ({n})"
    import zipfile
    skip = {'demo', 'envs', 'repos'}
    count = 0
    with zipfile.ZipFile(str(out) + '.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(str(h)):
            rel = os.path.relpath(root, str(h))
            if rel == '.':
                dirs[:] = [d for d in dirs if d not in skip]
            for f in files:
                fp = os.path.join(root, f)
                if os.path.islink(fp):
                    continue
                try:
                    z.write(fp, os.path.join('jarvis-data', os.path.relpath(fp, str(h))))
                    count += 1
                except OSError:
                    pass
    try:
        os.chmod(str(out) + '.zip', 0o600)
    except OSError:
        pass
    return {'ok': True, 'path': str(out) + '.zip', 'files': count, 'bytes': os.path.getsize(str(out) + '.zip')}, 200


def _trash_place(h: Path):
    """(folder to move into, how to say it). The system's Trash where there is one; else a dated folder beside the home."""
    t = paths.trash()
    try:
        inside = h.resolve() == t.resolve() or h.resolve() in t.resolve().parents
    except OSError:
        inside = True
    if not inside and (sys.platform == 'darwin' or t.is_dir()):
        return t, 'the Trash'
    return h.parent, f'a folder named "Jarvis erased {dt.date.today().isoformat()}" in {h.parent}'


def erase(b):
    """Remove everything Jarvis keeps about the owner. Default: into the Trash, where it can still be taken back. The server
    ends afterwards; the next start is a new install."""
    b = b or {}
    if is_demo():
        return {'ok': False, 'error': 'This is the demo. Leave it to erase your own data.'}, 400
    if str(b.get('confirm') or '') != 'ERASE':
        return {'ok': False, 'error': 'Type ERASE (in capitals) to confirm.'}, 400
    mode = 'forever' if b.get('mode') == 'forever' else 'trash'
    h = _safe_home()
    if h is None:
        return {'ok': False, 'error': f'{paths.home()} does not look like a Jarvis data folder, so nothing was removed.'}, 400
    demo_stop()
    stamp = dt.datetime.now().strftime('%Y-%m-%d %H%M%S')
    things = [(h, 'Jarvis data')] + [(s, 'Jarvis skill ' + s.name) for s in _own_skills()] + [(r, 'Jarvis runs ' + r.name[-24:]) for r in _own_runs()]
    where = ''
    try:
        if mode == 'forever':
            for p, _ in things:
                shutil.rmtree(str(p), ignore_errors=False)
            where = 'erased for good'
        else:
            base, said = _trash_place(h)
            if said != 'the Trash':
                base = base / f"Jarvis erased {dt.date.today().isoformat()}"
            base.mkdir(parents=True, exist_ok=True)
            for p, label in things:
                shutil.move(str(p), str(base / f'{label} {stamp}'))
            where = 'moved to ' + said
    except OSError as e:
        return {'ok': False, 'error': f'Not everything could be removed ({e.__class__.__name__}: {e}). What is left is still in {h}.'}, 500
    _later(lambda: os._exit(0), 1.0)                  # this server's data is gone: it ends; the next start is a new install
    return {'ok': True, 'where': where, 'mode': mode}, 200


# ---------------------------------------------------------------- updates
def version(repo=None) -> str:
    try:
        return open(os.path.join(str(repo or paths.REPO), 'VERSION'), encoding='utf-8').read().strip()[:20]
    except OSError:
        return ''


def _v(tag):
    m = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(tag).strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _git(repo, *args, timeout=60):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, **GIT_ENV))


def update_state(q=None, repo=None, fresh=None):
    """This version and the newest release of the repository this copy was cloned from. Looked up at most once a day (or
    when the owner presses "Check now"); one `git ls-remote`, nothing else."""
    repo = str(repo or paths.REPO)
    cur = version(repo)
    out = {'current': cur, 'latest': '', 'available': False, 'checked': None, 'can_update': False, 'why_not': ''}
    if is_demo():
        out['why_not'] = 'This is the demo.'
        return out
    if not os.path.isdir(os.path.join(repo, '.git')):
        out['why_not'] = 'This copy was not made with git clone, so it cannot update itself. Download the new version instead.'
        return out
    p = str(paths.state('update.json'))
    try:
        st = json.load(open(p, encoding='utf-8'))
    except (OSError, ValueError):
        st = {}
    fresh = bool((q or {}).get('now')) if fresh is None else fresh
    if fresh or time.time() - float(st.get('ts') or 0) > 86400:
        try:
            r = _git(repo, 'ls-remote', '--tags', '--refs', 'origin', 'v*', timeout=30)
            if r.returncode == 0:
                tags = sorted((t for t in (l.rsplit('/', 1)[-1] for l in r.stdout.splitlines() if l.strip()) if _v(t)), key=_v)
                st = {'ts': time.time(), 'latest': tags[-1] if tags else '', 'error': ''}
            else:
                st = dict(st, ts=time.time(), error='The repository could not be reached (offline, or git is not signed in).')
        except (OSError, subprocess.SubprocessError):
            st = dict(st, ts=time.time(), error='The repository could not be reached.')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(st, f)
        os.replace(p + '.tmp', p)
    out['latest'] = str(st.get('latest') or '')
    out['checked'] = dt.datetime.fromtimestamp(st['ts']).astimezone().isoformat(timespec='seconds') if st.get('ts') else None
    out['error'] = str(st.get('error') or '')
    out['available'] = bool(_v(out['latest']) and _v(cur) and _v(out['latest']) > _v(cur))
    if out['available']:
        dirty = _git(repo, 'status', '--porcelain', '--untracked-files=no').stdout.strip()
        if dirty:
            out['why_not'] = 'Files of the program were changed by hand on this computer. Undo those changes (or keep them on your own branch) and the update can run.'
        else:
            out['can_update'] = True
    return out


def update(b=None, repo=None, restart=True):
    """Move to the newest release: keep a copy of the settings, bring in the release with a fast-forward only (never a
    forced change), then restart so the new code runs and carries the data forward."""
    if is_demo():
        return {'ok': False, 'error': 'This is the demo.'}, 400
    repo = str(repo or paths.REPO)
    st = update_state(repo=repo, fresh=True)
    if not st['available']:
        return {'ok': False, 'error': st.get('error') or 'This is already the newest version.'}, 400
    if not st['can_update']:
        return {'ok': False, 'error': st['why_not']}, 400
    tag, was = st['latest'], st['current']
    keep = paths.state('backups')
    keep.mkdir(parents=True, exist_ok=True)
    if paths.config_file().exists():
        shutil.copy2(str(paths.config_file()), str(keep / f'config-before-{tag}.toml'))
    head = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    r = _git(repo, 'fetch', '--quiet', '--tags', 'origin', timeout=180)
    if r.returncode != 0:
        return {'ok': False, 'error': 'The new version could not be fetched (offline, or git is not signed in).'}, 502
    r = _git(repo, 'merge', '--ff-only', '--quiet', tag, timeout=120)
    if r.returncode != 0:
        return {'ok': False, 'error': 'This copy has changes of its own that the new version does not include, so it was left as it is.'}, 409
    with open(str(paths.state('update.json')), 'w', encoding='utf-8') as f:
        json.dump({'ts': time.time(), 'latest': tag, 'error': '', 'updated_from': was, 'undo': head}, f)
    if restart:
        demo_stop()
        env = dict(os.environ)
        procs.spawn_detached([paths.python(), '-m', 'jarvis', 'restart', '--wait-for', str(os.getpid())], env=env, cwd=repo, log=str(paths.logs('update.log')))
        _later(lambda: os._exit(0), 1.0)
    return {'ok': True, 'from': was, 'to': version(repo) or tag, 'undo': head[:12]}, 200
