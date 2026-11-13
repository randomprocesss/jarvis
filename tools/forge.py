#!/usr/bin/env python3
"""The tool forge: helpers Jarvis's workers build, kept for reuse.

A worker that writes a scanner, probe, parser or helper worth using again saves it here; the next worker lists what exists
before writing its own. Staged tools are T0: workers use them in their own runs. Promotion to trusted (state/forge/ in the
data home, which no worker may edit; what unattended jobs may call) needs the owner's press in the Learned view (T3), and
only a tool whose scan is clean. They are the owner's own tools: they live with their data, never in the repository.

    python tools/forge.py list                       what exists: name, purpose, usage, state, uses
    python tools/forge.py save <script.py> --name <kebab-name> --purpose "<one line>" --usage "<how to call it>"
    python tools/forge.py run <name> [-- args...]    runs it (cwd = the caller's current folder), counts the use
    python tools/forge.py show <name>                its manifest and scan

Scan (code, at save and again at promotion): the skill patterns (tools/skill_scan_patterns.txt) plus shell execution,
sockets and dynamic code. A tool with findings is saved but marked 'flagged': workers may read it, never run it via the forge.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import datetime as dt, hashlib, json, os, re, shutil, subprocess, sys

HUB = str(paths.home())
STAGE = os.path.join(HUB, 'skills-staging', 'tools')
TRUSTED = os.path.join(HUB, 'state', 'forge')
PATTERNS = os.path.join(str(paths.REPO), 'tools', 'skill_scan_patterns.txt')
EXTRA = [('shell_exec', r'shell\s*=\s*True|os\.system|os\.popen|pty\.spawn'), ('socket', r'\bimport\s+socket\b|socket\.socket'),
         ('dynamic_code', r'\beval\(|\bexec\(|__import__\(|compile\(.+exec'), ('delete', r'shutil\.rmtree|os\.remove\(|os\.unlink\(|rm\s+-rf')]
NAME = re.compile(r'^[a-z][a-z0-9-]{2,40}$')


def scan(text):
    hits = []
    pats = [tuple(l.split('\t', 1)) for l in open(PATTERNS).read().splitlines() if l.strip() and not l.startswith('#') and '\t' in l] + EXTRA
    for group, rx in pats:
        if group == 'tools_grant':
            continue                                   # (a skill frontmatter rule)
        m = re.search(rx, text, re.I | re.M)
        if m:
            hits.append([group, m.group(0)[:60]])
    return hits


def _all():
    out = []
    for root, state in ((TRUSTED, 'trusted'), (STAGE, None)):
        for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            mf = os.path.join(root, d, 'manifest.json')
            if os.path.exists(mf):
                m = json.load(open(mf))
                m['state'] = state or m.get('state', 'staged')
                m['dir'] = os.path.join(root, d)
                out.append(m)
    return out


def find(name):
    return next((m for m in _all() if m['name'] == name and m['state'] == 'trusted'), None) or next((m for m in _all() if m['name'] == name), None)


def save(path, name, purpose, usage):
    if not NAME.match(name or ''):
        return {'ok': False, 'error': 'name: kebab-case, 3-41 characters'}
    if not os.path.isfile(path) or not path.endswith('.py'):
        return {'ok': False, 'error': 'a .py file'}
    if not purpose.strip() or not usage.strip():
        return {'ok': False, 'error': 'say what it is for (--purpose) and how to call it (--usage)'}
    src = open(path).read()
    if len(src) > 60_000:
        return {'ok': False, 'error': 'too large for a helper (60k characters)'}
    if os.path.isdir(os.path.join(TRUSTED, name)):
        return {'ok': False, 'error': 'a trusted tool has that name: use it, or save under a new name'}
    d = os.path.join(STAGE, name)
    prev = json.load(open(os.path.join(d, 'manifest.json'))) if os.path.exists(os.path.join(d, 'manifest.json')) else {}
    os.makedirs(d, exist_ok=True)
    shutil.copyfile(path, os.path.join(d, 'tool.py'))
    hits = scan(src)
    m = {'name': name, 'purpose': purpose.strip()[:300], 'usage': usage.strip()[:400], 'state': 'flagged' if hits else 'staged', 'scan': hits,
         'sha': hashlib.sha1(src.encode()).hexdigest()[:12], 'saved': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
         'by_task': os.environ.get('JARVIS_TASK_ID', ''), 'from': os.path.abspath(path), 'uses': prev.get('uses', 0),
         'versions': prev.get('versions', 0) + 1}
    json.dump(m, open(os.path.join(d, 'manifest.json'), 'w'), indent=1)
    return {'ok': True, 'state': m['state'], 'scan': hits, 'dir': d}


def run(name, args):
    m = find(name)
    if not m:
        return 2, f'no tool named {name} (forge.py list)'
    if m['state'] == 'flagged':
        return 2, f'{name} is flagged by the scan ({m.get("scan")}): read it, do not run it through the forge'
    tool = os.path.join(m['dir'], 'tool.py')
    if hashlib.sha1(open(tool).read().encode()).hexdigest()[:12] != m.get('sha'):
        return 2, f'{name} changed after it was scanned: save it again'
    m['uses'] = m.get('uses', 0) + 1
    m['last_used'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    json.dump({k: v for k, v in m.items() if k not in ('dir',)}, open(os.path.join(m['dir'], 'manifest.json'), 'w'), indent=1)
    return subprocess.call([sys.executable, tool, *args]), ''


def promote(name):
    """Their press (T3, from the Learned view): a clean staged tool becomes trusted (state/forge/<name>/)."""
    d = os.path.join(STAGE, name)
    if not os.path.exists(os.path.join(d, 'manifest.json')):
        return {'ok': False, 'error': 'no such staged tool'}
    m = json.load(open(os.path.join(d, 'manifest.json')))
    hits = scan(open(os.path.join(d, 'tool.py')).read())
    if hits or m.get('state') != 'staged':
        return {'ok': False, 'error': 'the scan is not clean', 'scan': hits}
    dst = os.path.join(TRUSTED, name)
    if os.path.exists(dst):
        return {'ok': False, 'error': 'a trusted tool has that name'}
    os.makedirs(TRUSTED, exist_ok=True)
    shutil.copytree(d, dst)
    m.update(state='trusted', promoted=dt.datetime.now().astimezone().isoformat(timespec='seconds'))
    json.dump(m, open(os.path.join(dst, 'manifest.json'), 'w'), indent=1)
    os.makedirs(os.path.join(HUB, 'skills-archive'), exist_ok=True)
    shutil.move(d, os.path.join(HUB, 'skills-archive', 'tools-' + name + '-' + dt.date.today().isoformat()))
    return {'ok': True, 'path': dst}


def staged():
    """For the Learned view: staged and flagged tools, most used first."""
    return sorted([{k: v for k, v in m.items() if k != 'dir'} for m in _all() if m['state'] in ('staged', 'flagged')],
                  key=lambda m: -m.get('uses', 0))


def main(argv):
    if not argv or argv[0] not in ('list', 'save', 'run', 'show'):
        print(__doc__); return 2
    if argv[0] == 'list':
        for m in _all():
            print(f"{m['name']}  [{m['state']}, used {m.get('uses', 0)}x]  {m['purpose']}\n    usage: {m['usage']}")
        return 0
    if argv[0] == 'show':
        m = find(argv[1]) if len(argv) > 1 else None
        print(json.dumps({k: v for k, v in (m or {}).items()}, indent=1) if m else 'no such tool'); return 0 if m else 2
    if argv[0] == 'save':
        a = argv[1:]
        opt = lambda k: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else ''
        r = save(a[0] if a else '', opt('--name'), opt('--purpose'), opt('--usage'))
        print(json.dumps(r)); return 0 if r['ok'] else 2
    name = argv[1] if len(argv) > 1 else ''
    rest = argv[argv.index('--') + 1:] if '--' in argv else argv[2:]
    code, err = run(name, rest)
    if err:
        print(err, file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
