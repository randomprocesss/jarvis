#!/usr/bin/env python3
"""KB structure plan S4: the shape check of every workstream note, in code; violations are listed, never fixed by itself.

Shapes (as the notes are built today):
  parent with sub-notes   In one paragraph · Sub-notes · Open questions
  whole note (no subs)    In one paragraph · Current position · How we got here · Decisions · Superseded · Methods, rules and
                          lessons · Data and code · Deliverables · People · Open questions
  sub-note --current-position: Current position (+ Superseded) · --history: How we got here (+ Superseded) ·
  --decisions-methods: Decisions / Methods · --data-deliverables-people (or --data-code): Data and code / Deliverables / People
Checks: missing required heading; '*(added ...)*' bullets left after a tidy; 'Carried over' or 'Updates DATE' sections left;
a number in Current position with no [[citation]] on its line; the same line twice; a line the ledger marks superseded still
under Current position; ledger records contradicted by their source. Writes lint.md (in the knowledge base) and state/kb_lint.json.

    kb_lint.py run
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import collections, datetime as dt, glob, json, os, re

HUB = str(paths.home())
KBD = str(paths.kb())
TOP = os.path.join(KBD, 'topics')
REQ = {'parent': ['In one paragraph', 'Sub-notes', 'Open questions'],
       'whole': ['In one paragraph', 'Current position', 'Decisions', 'Open questions'],
       'current-position': ['Current position'], 'history': ['How we got here'],
       'decisions-methods': [], 'data-deliverables-people': [], 'data-code': []}


def _sections(t):
    out, name = collections.OrderedDict(), ''
    for line in t.split('\n'):
        if line.startswith('## '):
            name = line[3:].strip(); out[name] = []
        elif name:
            out[name].append(line)
    return out


def run():
    recs = []
    p = os.path.join(KBD, 'ledger', 'records.jsonl')
    if os.path.exists(p):
        recs = [json.loads(l) for l in open(p)]
    sup_quotes = {r['quote'].strip() for r in recs if r['status'] == 'superseded' and r['section'].lower().startswith('current')}
    issues = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(TOP, '*.md'))):
        slug = os.path.basename(f)[:-3]
        t = open(f, errors='replace').read()
        secs = _sections(t)
        kind = slug.split('--', 1)[1] if '--' in slug else ('parent' if 'Sub-notes' in secs else 'whole')
        for h in REQ.get(kind, []):
            if h not in secs:
                issues[slug].append(f'missing heading "{h}"')
        n_added = t.count('*(added ')
        if n_added:
            issues[slug].append(f'{n_added} added bullets not yet tidied in')
        for h in secs:
            if h.startswith(('Carried over', 'Updates 20')):
                issues[slug].append(f'section "{h}" left to fold in')
        cp = secs.get('Current position') or []
        unc = [l for l in cp if re.search(r'\d', l) and l.strip().startswith(('-', '*', '|')) and '[[' not in l and len(l) > 30]
        if unc:
            issues[slug].append(f'{len(unc)} lines with numbers in Current position cite no source, e.g. "{unc[0].strip()[:120]}"')
        seen, dup = set(), 0
        for line in t.split('\n'):
            k = re.sub(r'\s+', ' ', line.strip().lower())
            if len(k) > 40:
                dup += k in seen
                seen.add(k)
        if dup:
            issues[slug].append(f'{dup} lines appear twice')
        stale = [l for l in cp if l.strip() in sup_quotes]
        if stale:
            issues[slug].append(f'{len(stale)} superseded lines still under Current position')
    con = [r for r in recs if r.get('checked') == 'contradicted' or r.get('contradicts')]
    for r in con:
        issues[r['note']].append(f"ledger {r['id']}: " + (f"source says {r.get('source_says')}" if r.get('source_says') else
                                                          f"disagrees with {', '.join(r.get('contradicts') or [])}"))
    L = ['---', 'topic: lint', 'title: Knowledge base shape check', f'updated: {dt.date.today()}', '---', '# Knowledge base shape check', '',
         f'*{dt.date.today()} · {sum(len(v) for v in issues.values())} issues in {len(issues)} notes, from tools/kb_lint.py (code; listed, '
         f'never fixed by itself).*', '']
    for slug, xs in sorted(issues.items(), key=lambda kv: -len(kv[1])):
        L += [f'## [[{slug}]] ({len(xs)})', ''] + [f'- {x}' for x in xs] + ['']
    open(os.path.join(KBD, 'ledger', 'lint.md') if os.path.isdir(os.path.join(KBD, 'ledger')) else os.path.join(KBD, 'lint.md'), 'w').write('\n'.join(L) + '\n')
    c = collections.Counter(re.sub(r'\d+', 'N', x.split(',')[0].split(':')[0])[:50] for xs in issues.values() for x in xs)
    json.dump({'at': dt.datetime.now().isoformat(timespec='seconds'), 'notes': len(issues), 'issues': sum(len(v) for v in issues.values()),
               'kinds': dict(c.most_common())}, open(os.path.join(HUB, 'state', 'kb_lint.json'), 'w'), indent=1)
    print(f'{sum(len(v) for v in issues.values())} issues in {len(issues)} notes'); print(c.most_common(10))


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['run']:
        run()
    else:
        print(__doc__)
