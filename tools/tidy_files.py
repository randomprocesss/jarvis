#!/usr/bin/env python3
"""Keep Jarvis's data folder in order. Nothing is deleted.

Old backup copies leave the folders where the live state sits and go to ONE archive, by month, with their original path
kept under it; every move is written to a manifest so anything can be found or put back.

    tidy_files.py plan     what would move (nothing changes)
    tidy_files.py run      move it, append to archive/MOVED.tsv, rewrite MAP.md
    tidy_files.py map      only rewrite MAP.md
    tidy_files.py undo <manifest date>   put the moves of that day back (YYYY-MM-DD)

What counts as a backup: a name with .bak, .bak_x, .bak-x, .before_x, .before-x or .orig. What moves: a backup older than
7 days (a newer one is still someone's way back) in the top level of the data folder, state/ and brain/. What never
moves: anything in work/, voice/, roundups/, logs/, the secrets folder (each belongs to its own flow), and the live
state files, wherever they sit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config, paths
import collections, datetime as dt, os, re, shutil, sys, time

HUB = str(paths.home())
ARCH = os.path.join(HUB, 'archive')
MANIFEST = os.path.join(ARCH, 'MOVED.tsv')
BACKUP = re.compile(r'\.bak($|[_\-.])|\.before[_\-]|\.orig$', re.I)
ROOTS = ['', 'state', 'brain']                          # '' = the top level itself (files only)
SKIP = ('/.git', '/__pycache__', '/archive', '/secrets', '/backups')
KEEP_DAYS = 7


def _old(p, now):
    return now - os.path.getmtime(p) > KEEP_DAYS * 86400


def plan():
    """[(source, destination)]: old backups to the archive, by month."""
    now, out = time.time(), []
    for root in ROOTS:
        base = os.path.join(HUB, root) if root else HUB
        if not os.path.isdir(base):
            continue
        walker = [(base, [], [f for f in os.listdir(base) if os.path.isfile(os.path.join(base, f))])] if not root else os.walk(base)
        for dp, dn, fn in walker:
            if any(s + '/' in dp + '/' for s in SKIP):
                dn[:] = []; continue
            for f in fn:
                p = os.path.join(dp, f)
                if BACKUP.search(f) and os.path.isfile(p) and not os.path.islink(p) and _old(p, now):
                    month = dt.date.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m')
                    out.append((p, os.path.join(ARCH, 'backups', month, os.path.relpath(p, HUB))))
    return out


def run(moves=None):
    moves = moves if moves is not None else plan()
    os.makedirs(ARCH, exist_ok=True)
    today, n, size = dt.date.today().isoformat(), 0, 0
    with open(MANIFEST, 'a') as mf:
        for src, dst in moves:
            if not os.path.exists(src) or os.path.exists(dst):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            sz = os.path.getsize(src)
            shutil.move(src, dst)
            mf.write(f'{today}\t{os.path.relpath(src, HUB)}\t{os.path.relpath(dst, HUB)}\t{sz}\n')
            n += 1; size += sz
    print(f'moved {n} entries, {size / 1e6:.1f} MB, into {ARCH} (manifest {MANIFEST})')
    write_map()
    return n


def undo(day):
    rows = [l.rstrip('\n').split('\t') for l in open(MANIFEST) if l.startswith(day + '\t')] if os.path.exists(MANIFEST) else []
    n = 0
    for _, src, dst, _ in reversed(rows):
        s, d = os.path.join(HUB, src), os.path.join(HUB, dst)
        if os.path.exists(d) and not os.path.exists(s):
            os.makedirs(os.path.dirname(s), exist_ok=True); shutil.move(d, s); n += 1
    print(f'put back {n} of {len(rows)} moves of {day}')


def _what():
    kb = str(config.load().get('knowledge.name') or 'main')
    return [
        ('config.toml', 'the settings: who the owner is, their work, the engine, the sources, what is switched on'),
        ('state/', 'live state the code reads and writes: jarvis.db, the json state files, state/oneoff/ (one-off scripts and the '
                   'backups of the last 7 days), state/secrets/ (never printed, never mirrored)'),
        ('brain/', f'the knowledge base: {kb}/topics (workstream notes), {kb}/ledger (facts, relations, pages), {kb}/entities, sessions '
                   'and sources (one note per chat and document), kb_build.log'),
        ('work/', "Jarvis's work: one <id>.json per inbox item and outputs/<id>-<a>/ with everything a worker made (every run's evidence)"),
        ('roundups/', 'the inbox round-ups (one json per check of the connected apps)'),
        ('voice/', 'the voice layer, when switched on: recordings, conversations (kept 30 days), wake-word and speech models'),
        ('logs/', 'the logs of the server and its timed jobs'),
        ('skills/', "skills Jarvis drafted from accepted work, waiting for the owner's yes"),
        ('live_fetch/', 'where the reads of the live sources run (their transcripts are never read as work)'),
        ('archive/', 'the one archive: backups/<month>/<original path> (old backup copies) and MOVED.tsv (every move: date, from, to, bytes)'),
    ]


def _size(p):
    n = 0
    for dp, _, fs in os.walk(p):
        for f in fs:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return f'{n / 1e9:.1f} GB' if n >= 1e9 else f'{n / 1e6:.1f} MB' if n >= 1e5 else f'{n / 1e3:.0f} KB'


def write_map():
    L = ["# Where things live in Jarvis's data folder", '',
         f'*Written by tools/tidy_files.py on {dt.date.today()}. Run `tidy_files.py map` to refresh it. Nothing here is edited by hand.*', '',
         '## Folders', '', '| Folder | What it holds | Size |', '|---|---|---|']
    for name, what in _what():
        d = os.path.join(HUB, name.rstrip('/'))
        L.append(f"| `{name}` | {what} | {_size(d) if os.path.isdir(d) else ''} |")
    L += ['', '## Rules', '',
          '- A backup before a change goes next to nothing live: `state/oneoff/<file>.bak_<mmdd>`. After 7 days `tidy_files.py run` moves it to `archive/backups/<month>/`.',
          '- A one-off script goes in `state/oneoff/`, never beside the tools.',
          "- Nothing is deleted. To find something that moved: look for its name in `archive/MOVED.tsv`. To put a day's moves back: `tidy_files.py undo <date>`.",
          '- `work/`, `voice/`, `roundups/` and `logs/` keep their own files, backups included.', '',
          '## Tools (what each one is for)', '']
    groups = collections.OrderedDict([('Knowledge base', re.compile(r'^(kb_|archive_s|extract_sessions|find_context|personal_)')),
                                      ('Proof, levels and the scorecard', re.compile(r'^(phase\d|shadow_score|scorecard|policy_safety|replay_exam|needs_triage|promises_triage|jarvis_eval|jarvis_smoke|grounding_eval|autopilot_cases)')),
                                      ('Jarvis at work (hooks, workers, groups)', re.compile(r'^(worker_|worklock|job_done|run_job|forge|groups|redeploy|gate\b|leakcheck)')),
                                      ('Other', re.compile(r'.'))])
    rows = collections.defaultdict(list)
    tools = str(paths.REPO / 'tools')
    for f in sorted(os.listdir(tools)):
        if not f.endswith(('.py', '.sh')) or BACKUP.search(f):
            continue
        t = open(os.path.join(tools, f), errors='replace').read(3000)
        m = re.search(r'"""(.+?)(?:\n\n|""")', t, re.S) or re.search(r'^#\s*(?!!)(.+)$', t, re.M)
        doc = re.sub(r'\s+', ' ', m.group(1)).strip()[:170] if m else ''
        rows[next(g for g, rx in groups.items() if rx.search(f))].append((f, doc))
    for g in groups:
        if rows[g]:
            L += [f'### {g}', ''] + [f'- `{f}`: {doc}' for f, doc in rows[g]] + ['']
    os.makedirs(HUB, exist_ok=True)
    open(os.path.join(HUB, 'MAP.md'), 'w').write('\n'.join(L) + '\n')
    print('wrote MAP.md')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['plan']:
        mv = plan()
        by = collections.Counter('/'.join(os.path.relpath(s, HUB).split('/')[:2]) for s, _ in mv)
        print(len(mv), 'entries would move:'); [print(f'  {k:40s} {v}') for k, v in by.most_common(25)]
    elif a[:1] == ['run']:
        run()
    elif a[:1] == ['map']:
        write_map()
    elif a[:1] == ['undo'] and len(a) > 1:
        undo(a[1])
    else:
        print(__doc__)
