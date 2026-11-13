#!/usr/bin/env python3
"""KB plan Step 4, Jarvis's records (16 Oct 2026). Jarvis's processing transcripts are never read (their results are
recorded here and they carry the text they processed); its records are: the items it noted (needs), the commitments
people made, its desk entries, goals, investigations, research findings, initiatives, org items, its tasks and inbox
items, and every task record and write-up it produced (rendered by world/vault._work).

    kb_jarvis_records.py build    render them into units in state/step4_staging/ (no model use; outside the brain)
    kb_jarvis_records.py stage    each unit through the personal filter, then into brain/sources_extra/ for kb_extra
Personal, admin and setup rows are left out by their scope label; unlabelled rows rely on the personal filter.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import glob, json, os, re, sqlite3, sys

HUB = str(paths.home())
STAGING = os.path.join(HUB, 'state', 'step4_staging')
EXTRA = os.path.join(HUB, 'brain', 'sources_extra')
UNIT = 150_000
KEEP = ('work', None)                                     # scopes that are project work (None: unlabelled, filtered later)


def _db():
    return sqlite3.connect(f"file:{os.path.join(HUB, 'state', 'jarvis.db')}?mode=ro", uri=True)


def _rows(c, sql):
    cur = c.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _short(v, n=600):
    v = re.sub(r'\s+', ' ', str(v or '')).strip()
    return v if len(v) <= n else v[:n] + ' ...'


def _render(r, fields):
    return ' | '.join(f'{k}: {_short(r.get(k))}' for k in fields if r.get(k) not in (None, '', '[]', '{}'))


def _write(name, head, lines):
    """Split into units of about UNIT characters: <name>-<k>.txt."""
    os.makedirs(STAGING, exist_ok=True)
    parts, cur, size = [], [], 0
    for l in lines:
        if cur and size + len(l) > UNIT:
            parts.append(cur); cur, size = [], 0
        cur.append(l); size += len(l) + 1
    if cur:
        parts.append(cur)
    for k, p in enumerate(parts, 1):
        open(os.path.join(STAGING, f'j_{name}-{k:02d}.txt'), 'w').write(
            f'session jarvis-records-{name}-{k} | {head} (part {k} of {len(parts)})\n\n' + '\n'.join(p) + '\n')
    return len(parts)


def build():
    c = _db()
    out = {}
    t = {
        'needs': ("items Jarvis noted while watching their work: what is needed, why, what it unblocks, its state",
                  "SELECT * FROM needs ORDER BY created", ['created', 'kind', 'statement', 'target', 'why', 'unblocks', 'assumed', 'topic', 'state', 'note', 'quote', 'source_ref']),
        'commitments': ("commitments people made in their work threads: who owes what to whom, by when, and whether it was done",
                        "SELECT * FROM commitments ORDER BY created", ['created', 'who', 'to_whom', 'what', 'due', 'state', 'check_verdict', 'check_note', 'quote', 'source_ref']),
        'desk': ("Jarvis's desk: work it noticed on its own, with numbers and evidence, held for the fortnightly brief",
                 "SELECT * FROM desk ORDER BY created", ['created', 'kind', 'topic', 'title', 'summary', 'numbers', 'evidence', 'refs', 'state', 'confidence']),
        'plans': ("Jarvis's goals, investigations, research findings, initiatives and organisation notes about the project",
                  None, None),
        'tasks': ("tasks Jarvis ran for them and the inbox items they came from: what was asked and how each ended",
                  "SELECT * FROM tasks ORDER BY created", ['created', 'title', 'kind', 'state', 'ask_quote', 'note', 'ended', 'exit']),
    }
    for name, (head, sql, fields) in t.items():
        lines = []
        if name == 'plans':
            for tbl, f in (('goals', ['created', 'statement', 'source', 'state', 'success']),
                           ('investigations', ['created', 'goal', 'why', 'question', 'state', 'steps_done', 'passed']),
                           ('research_findings', ['created', 'title', 'url', 'kind', 'problem', 'claim', 'evidence', 'applies', 'caveat', 'state']),
                           ('initiatives', ['created', 'class', 'title', 'plan', 'state', 'result']),
                           ('org_items', ['created', 'kind', 'what', 'who', 'place', 'quote'])):
                lines += [f'[{tbl}] ' + _render(r, f) for r in _rows(c, f'SELECT * FROM {tbl} ORDER BY created')]
        else:
            rows = _rows(c, sql)
            if 'scope' in (rows[0] if rows else {}):
                rows = [r for r in rows if r.get('scope') in KEEP]
            lines = [_render(r, fields) for r in rows]
            if name == 'tasks':
                lines += ['[inbox item] ' + _render(r, ['created', 'kind', 'src', 'who', 'title', 'work_status', 'done'])
                          for r in _rows(c, 'SELECT * FROM items ORDER BY created')]
        out[name] = (_write(name, head, [l for l in lines if l]), len(lines))
    # every task record and write-up Jarvis produced, rendered (and filtered) as the vault does, by month
    from jarvis.world import vault
    real = vault._pfilter
    vault._pfilter = lambda x: x                          # filtered in `stage`, together with everything else
    try:
        by = {}
        for rel, text, _ in vault._work():
            m = re.match(r'^Work/(?:files/)?(\d{4}-\d{2})', rel)
            by.setdefault(m.group(1) if m else 'undated', []).append(f'--- {rel[:-3]}\n{text}')
    finally:
        vault._pfilter = real
    for month, lines in sorted(by.items()):
        out[f'work-{month}'] = (_write(f'work-{month}', f"Jarvis's task records and write-ups, {month}", lines), len(lines))
    for k, (units, n) in out.items():
        print(f'{k:16s} {n:5d} rows -> {units} units')
    print(f'{sum(os.path.getsize(f) for f in glob.glob(os.path.join(STAGING, "*.txt"))) / 1e6:.1f} MB in {STAGING}')


def stage():
    paths.use_tools()
    import personal_deep_check as P
    os.makedirs(EXTRA, exist_ok=True)
    n = 0
    for f in sorted(glob.glob(os.path.join(STAGING, 'j_*.txt'))):
        t = P.filter_text(open(f, errors='replace').read())
        open(os.path.join(EXTRA, os.path.basename(f)), 'w').write(t)
        os.remove(f); n += 1
    print(f'{n} units filtered into {EXTRA}')


if __name__ == '__main__':
    {'build': build, 'stage': stage}[sys.argv[1] if len(sys.argv) > 1 else 'build']()
