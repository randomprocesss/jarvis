"""The audit of the knowledge base. No model use. Writes state/kb_audit.json and prints a
summary; the report for the owner is written from it.

  A. inventory    every source of knowledge: how much of it is in the knowledge base, left out by rule, or out of reach
  B. citations    every chat note cited by a workstream; every source note filed (or filed under none, listed)
  C. trace        sampled numbers in the workstream notes found in the note each one cites
  D. secrets      the vault's secret patterns over everything the knowledge base holds (names and counts only, no values)
  E. retrieval    questions with a known answer (current facts sampled from the ledger, plus the owner's own in
                  audit_questions.json in the knowledge base): does what Jarvis retrieves (project_brief) carry the answer?

    kb_audit.py run
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths, screens
import collections, datetime as dt, glob, json, os, random, re, sys

HUB = str(paths.home())
B = os.path.join(HUB, 'brain')
paths.use_tools()
import kb_guard as G  # noqa: E402


def _j(p, d):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def inventory():
    X, S = os.path.join(B, 'sources_extra'), os.path.join(B, 'sources')
    noted = lambda pre: (sum(1 for f in glob.glob(os.path.join(X, pre + '*')) if os.path.exists(os.path.join(S, os.path.basename(f).rsplit('.', 1)[0] + '.md'))),
                         len(glob.glob(os.path.join(X, pre + '*'))))
    scopes = collections.Counter((v or {}).get('scope') for v in _j(os.path.join(HUB, 'state', 'archive_scope.json'), {}).values())
    live = _j(os.path.join(HUB, 'state', 'kb_live.json'), {})
    inv = {
        'Claude Code chats (archived)': {'chats': len(glob.glob(os.path.join(B, 'raw_archive', 'claude_projects', '**', '*.jsonl'), recursive=True)),
                                         'labels': dict(scopes), 'chat notes': len(glob.glob(os.path.join(B, 'sessions', '*.md')))},
        'documents (d_)': dict(zip(('noted', 'pieces'), noted('d_'))),
        'chat extras (a_)': dict(zip(('noted', 'pieces'), noted('a_'))),
        'screenshots (c_)': dict(zip(('noted', 'pieces'), noted('c_'))),
        'Jarvis records (j_)': dict(zip(('noted', 'pieces'), noted('j_'))),
        'notebooks (e_)': dict(zip(('noted', 'pieces'), noted('e_'))),
        'live sources (l_)': dict(zip(('noted', 'pieces'), noted('l_'))),
        'tickets': {k: {'tickets': (v or {}).get('tickets'), 'expected': (v or {}).get('expected')} for k, v in live.items() if k.startswith('jira')},
        'wiki pages': {'pages': len(glob.glob(os.path.join(X, 'l_conf_*'))), 'listed': len(live.get('confluence_pages') or [])},
        'Slack channels': {n: v.get('messages') for n, v in (live.get('slack') or {}).items()},
        'Slack DMs': {'people': len((live.get('slack_dms') or {}).get('people') or {}), 'messages': sum(((live.get('slack_dms') or {}).get('people') or {}).values())},
        'mail': {'threads': len(glob.glob(os.path.join(X, 'l_mail_*'))), 'messages found': len(live.get('mail_found') or {})},
        'Teams': live.get('teams'), 'meetings': live.get('meetings'), 'SharePoint': live.get('sharepoint'),
        'repositories': {'units': len(glob.glob(os.path.join(X, 'l_repo_*')))},
        'raw data files (catalogued, full text searchable)': {'files': len(glob.glob(os.path.join(B, 'sources_raw', '*.txt'))), 'catalogue parts': len(glob.glob(os.path.join(X, 'f_catalog_*')))},
        'parked for a later read (searchable now)': {'files': len(glob.glob(os.path.join(B, 'sources_extra_later', '*.txt')))},
        'people notes': len(glob.glob(os.path.join(str(paths.kb()), 'people', '*.md'))),
        'workstream notes': {'workstreams': len([t for t in _j(os.path.join(str(paths.kb()), 'topic_map.json'), []) if not t.get('parent')]),
                             'sub-notes': len([t for t in _j(os.path.join(str(paths.kb()), 'topic_map.json'), []) if t.get('parent')])},
    }
    try:
        import sqlite3
        c = sqlite3.connect(os.path.join(HUB, 'state', 'kb_search.db'))
        inv['search index'] = dict(c.execute('SELECT kind, count(*) FROM files GROUP BY kind').fetchall())
    except Exception:
        pass
    return inv


def citations():
    tm = _j(os.path.join(str(paths.kb()), 'topic_map.json'), [])
    cited = {s.split(':', 1)[1] for t in tm for s in t.get('sources') or [] if ':' in s}
    filing = _j(os.path.join(HUB, 'state', 'topics_filing.json'), {})
    ses = [os.path.basename(f)[:-3] for f in glob.glob(os.path.join(B, 'sessions', '*.md'))]
    src = [os.path.basename(f)[:-3] for f in glob.glob(os.path.join(B, 'sources', '*.md')) if not f.endswith('.cut.md')]
    head = lambda d, i: open(os.path.join(B, d, i + '.md'), errors='replace').read(600)
    ses_un = [i for i in ses if i not in cited]
    ses_un_reason = collections.Counter('personal or no project content' if any(x in head('sessions', i) for x in ('personal (omitted)', 'no project content'))
                                        else ('filed under none' if filing.get(i) == [] else ('not filed yet' if i not in filing else 'filed, not yet folded')) for i in ses_un)
    src_none = [i for i in src if filing.get(i) == []]
    src_unfolded = [i for i in src if filing.get(i) and i not in cited]
    return {'chat notes': len(ses), 'cited by a workstream': len(ses) - len(ses_un), 'not cited, by reason': dict(ses_un_reason),
            'source notes': len(src), 'filed under a workstream': sum(1 for i in src if filing.get(i)), 'filed under none': len(src_none),
            'filed under none (prefixes)': dict(collections.Counter(i.split('_')[0] for i in src_none)),
            'filed but not cited yet': len(src_unfolded), 'examples filed under none': src_none[:12]}


def trace(n=150, seed=24):
    """Bullets of the workstream notes that cite a note and hold a number: is the number in the cited note?"""
    rows = []
    for f in glob.glob(os.path.join(str(paths.kb()), 'topics', '*.md')):
        for line in open(f, errors='replace').read().splitlines():
            ids = re.findall(r'\[\[([^\]|#]+)', line)
            bare = re.sub(r'\[\[[^\]]*\]\]', ' ', line)       # 24 Oct: digits inside a cited id are not facts of the line
            bare = re.sub(r'`[^`]*`', ' ', bare)                # nor are file and table names
            nums = [v for k, v in G.facts(bare) if k == 'n' and len(re.sub(r'\D', '', v)) >= 2]
            if ids and nums:
                rows.append((os.path.basename(f)[:-3], line, ids, nums))
    random.Random(seed).shuffle(rows)
    out, ok = [], 0
    for ws, line, ids, nums in rows[:n]:
        texts = ''
        for i in ids:
            for d in ('sessions', 'sources'):
                p = os.path.join(B, d, i + '.md')
                if os.path.exists(p):
                    texts += open(p, errors='replace').read()
            for p in glob.glob(os.path.join(B, 'sources_extra', i + '.*')) + glob.glob(os.path.join(str(paths.kb()), 'topics', i + '.md')) + \
                    glob.glob(os.path.join(str(paths.claude_memory()), i + '.md')):
                texts += open(p, errors='replace').read()                # memory notes and workstream notes are cited too
        norm = lambda s: s.replace(',', '').replace('$', '')
        found = [v for v in nums if norm(v) in norm(texts)]
        good = len(found) == len(nums)
        ok += good
        if not good:
            out.append({'workstream': ws, 'numbers': nums, 'not found in the cited notes': [v for v in nums if v not in found], 'cites': ids[:3],
                        'line': line.strip()[:220], 'cited notes exist': bool(texts)})
    return {'bullets checked': min(n, len(rows)), 'all numbers found in the cited notes': ok, 'of bullets with numbers and citations': len(rows),
            'not traced': out[:15]}


def secrets():
    pats = screens.secret_patterns()
    hits = collections.Counter(); files = collections.defaultdict(set)
    roots = [str(paths.kb())] + [os.path.join(B, d) for d in ('sessions', 'sources', 'sources_extra', 'sources_extra_later', 'sources_raw')]
    for r in roots:
        for f in glob.glob(os.path.join(r, '**', '*'), recursive=True):
            if not os.path.isfile(f) or '/versions/' in f:
                continue
            t = open(f, errors='replace').read()
            for name, rx in pats:
                if rx.search(t):
                    hits[name] += 1; files[name].add(os.path.relpath(f, B))
    return {'patterns': len(pats), 'files with a hit, by pattern': dict(hits), 'examples': {k: sorted(v)[:5] for k, v in files.items()}}


def questions(n=7, seed=24):
    """[(question, [what the answer must hold])]: the owner's own (audit_questions.json in the knowledge base, a list of
    [question, [words]]), then current facts sampled from the ledger: what the fact is about, and its number."""
    out = [(str(q), [str(w) for w in want]) for q, want in _j(os.path.join(str(paths.kb()), 'audit_questions.json'), []) if q and want]
    recs = []
    try:
        for line in open(os.path.join(str(paths.kb()), 'ledger', 'records.jsonl'), errors='replace'):
            r = json.loads(line)
            if r.get('kind') == 'fact' and r.get('status') == 'current' and r.get('subject'):
                stmt = re.sub(r'\b[A-Z][A-Z0-9]*-\d+\b', ' ', r.get('statement') or '')     # a ticket or order number is not the fact
                nums = [v for k, v in G.facts(stmt) if k == 'n' and len(re.sub(r'\D', '', v)) >= 2]
                if nums:                                     # the number the statement gives first
                    recs.append((' '.join(x for x in (r['subject'], r.get('scope') or '') if x), [min(nums, key=lambda v: stmt.find(v) % 10**9)]))
    except (OSError, ValueError):
        pass
    random.Random(seed).shuffle(recs)
    return out + recs[:max(0, n - len(out))]


def retrieval():
    from jarvis import mcp as W
    out = []
    for q, want in questions():
        r = W.project_brief({'topic': q})
        out.append({'question': q, 'expected': want, 'answer carries it': all(w.lower() in r.lower() for w in want), 'chars': len(r)})
    return {'questions': len(out), 'answered from the knowledge base': sum(x['answer carries it'] for x in out), 'detail': out}


def run():
    rep = {'at': dt.datetime.now().isoformat(timespec='seconds')}
    for name, fn in (('inventory', inventory), ('citations', citations), ('trace', trace), ('secrets', secrets), ('retrieval', retrieval)):
        try:
            rep[name] = fn()
        except Exception as e:
            rep[name] = {'error': f'{e.__class__.__name__}: {str(e)[:200]}'}
        print(f'== {name}\n' + json.dumps(rep[name], indent=1, default=str)[:3000], flush=True)
    json.dump(rep, open(os.path.join(HUB, 'state', 'kb_audit.json'), 'w'), indent=1, default=str)
    return rep


if __name__ == '__main__':
    if sys.argv[1:2] == ['run']:
        run()
    else:
        print(__doc__)
