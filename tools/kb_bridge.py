#!/usr/bin/env python3
"""One brain, stage 1: one read path (autonomy plan part A, build spec 1; the owner, 12 Nov 2026: "continue working in full force").

Jarvis keeps its own open questions (jarvis.db `needs`), promises (`commitments`) and goals. The knowledge base ledger
holds the facts, decisions and open points parsed from the notes. Neither read the other, so "what is still open on lead
time" had two answers. This writes Jarvis's rows as ledger records in the ledger's own shape
(ledger/jarvis_records.jsonl (in the knowledge base), origin "jarvis"), so the fact index (kb_facts), project_brief and the ledger pages
answer from one place. Code only: nothing in jarvis.db changes, no model is called, nothing is sent anywhere.

What goes in:
  open questions  needs that are open, work scope and untainted. Each carries its triage verdict (tools/needs_triage.py):
                  "open" (judged still open, with a priority and a workstream), "unclear", or "not triaged" (noted since the
                  last triage). Only the ones judged open are put in the fact index; the rest are in the file, marked.
  promises        commitments that are open and work scope, with their verdict (tools/promises_triage.py): "owed",
                  "unclear" or "not triaged". Only the owed ones are indexed.
  goals           every goal; Jarvis inferred them from Jira epics, and the record says so until the owner confirms them.
Ids are the jarvis.db ids, so a re-run rewrites the same records and a row closed in jarvis.db is gone at the next build.
The same secret patterns and personal words that screen the vault screen each record; a hit is left out and counted.

    kb_bridge.py build      jarvis_records.jsonl, the goals page, and the two triage pages refreshed
    kb_bridge.py check      the counts side by side: jarvis.db, the file, the index
    kb_bridge.py selftest   the spec's test on a temporary database
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import collections, datetime as dt, json, os, re, sqlite3, sys

HUB = str(paths.home())
paths.use_tools()
DB = os.path.join(HUB, 'state', 'jarvis.db')
OUT = os.path.join(str(paths.kb()), 'ledger')
JREC = os.path.join(OUT, 'jarvis_records.jsonl')
NEED_KIND = {'open_decision': 'a decision not yet taken', 'event_need': 'an awaited event', 'data_need': 'missing data',
             'answer_need': 'an unanswered question', 'access_need': 'missing access'}


def _jsonl(p):
    out = {}
    try:
        for l in open(p):
            try:
                r = json.loads(l); out[r['id']] = r           # the last verdict of an id stands
            except (ValueError, KeyError):
                continue
    except OSError:
        pass
    return out


def _rec(**k):
    base = {'id': None, 'kind': None, 'subject': None, 'statement': None, 'metric': None, 'value': None, 'unit': None, 'scope': None,
            'data_cutoff': None, 'as_of': None, 'final': None, 'who': None, 'why': None, 'status': 'current', 'superseded_by': [],
            'contradicts': [], 'note': None, 'section': 'Jarvis', 'quote': None, 'sources': [], 'workstream': '', 'entities': [],
            'checked': None, 'origin': 'jarvis'}
    base.update(k)
    return base


def records(db=DB, state=None):
    """The records, built from jarvis.db and the two triage files. `state` = the folder of the triage files (tests)."""
    state = state or os.path.join(HUB, 'state')
    nt, pt = _jsonl(os.path.join(state, 'needs_triage.jsonl')), _jsonl(os.path.join(state, 'promises_triage.jsonl'))
    if not os.path.exists(db):
        return []                                        # Jarvis has not run yet: it has no questions, promises or goals
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True); con.row_factory = sqlite3.Row
    out = []
    for r in con.execute("SELECT * FROM needs WHERE state='open' AND scope='work' AND COALESCE(tainted,0)=0 ORDER BY created"):
        t = nt.get(r['id']) or {}
        v = t.get('verdict') if t.get('verdict') in ('open', 'unclear') else 'not triaged'
        out.append(_rec(id=r['id'], kind='open', subject=(r['topic'] or '').replace('-', ' ') or None, statement=r['statement'],
                        why=(t.get('why') if v == 'open' else None) or r['why'] or None, as_of=str(r['created'] or '')[:10] or None,
                        note='jarvis:' + str(r['source_ref'] or ''), quote=r['quote'], sources=[r['source_ref']] if r['source_ref'] else [],
                        workstream=t.get('workstream') or '', need_kind=r['kind'], target=r['target'] or None, unblocks=r['unblocks'] or None,
                        triage=v, priority=t.get('priority') if v == 'open' else None, indexed=v == 'open'))
    for r in con.execute("SELECT * FROM commitments WHERE state='open' AND scope='work' ORDER BY created"):
        t = pt.get(r['id']) or {}
        v = t.get('verdict') if t.get('verdict') in ('owed', 'unclear') else 'not triaged'
        due = f" (due: {r['due']})" if r['due'] else ''
        out.append(_rec(id=r['id'], kind='promise', subject=f"{r['who']} owes {r['to_whom']}", who=r['who'], to_whom=r['to_whom'], due=r['due'] or None,
                        statement=f"{r['who']} owes {r['to_whom']}: {r['what']}{due}", why=t.get('why') if v == 'owed' else None,
                        as_of=str(r['created'] or '')[:10] or None, note='jarvis:' + str(r['source_ref'] or ''), quote=r['quote'],
                        sources=[r['source_ref']] if r['source_ref'] else [], triage=v, indexed=v == 'owed'))
    for r in con.execute("SELECT * FROM goals WHERE dismissed_at IS NULL ORDER BY created"):
        try:
            src = json.loads(r['source'] or '{}')
        except ValueError:
            src = {}
        key = src.get('key') or ''
        out.append(_rec(id=r['id'], kind='goal', subject=f'goal {key}'.strip(), statement=r['statement'], as_of=str(r['created'] or '')[:10] or None,
                        final=False if r['state'] == 'inferred' else None, note='jarvis:' + (f'jira:{key}' if key else 'goals'),
                        sources=[f'jira:{x}' for x in src.get('issues') or []][:8], entities=[f'ticket:{x}' for x in src.get('issues') or []][:8],
                        goal_state=r['state'], measure=r['success'] or None, review_at=r['review_at'] or None, triage=r['state'], indexed=True))
    con.close()
    return out


def build():
    import kb_ledger as KL
    recs = records()
    screens = KL._mem_screens()
    if screens is None:
        raise RuntimeError('bridge: the secret and personal screens could not be loaded; nothing written')
    keep = [r for r in recs if not any(rx.search(' '.join(str(r.get(k) or '') for k in ('statement', 'quote', 'why', 'subject'))) for rx in screens)]
    tmp = JREC + '.tmp'
    with open(tmp, 'w') as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(tmp, JREC)
    goals = [r for r in keep if r['kind'] == 'goal']
    L = ['# Goals', '', f"*{dt.date.today()} · {len(goals)} goals in Jarvis's records. Each was inferred by Jarvis from a Jira epic and its tickets; "
         f"none is confirmed by the owner yet, so none has a measure or a review date. Built by tools/kb_bridge.py.*", '',
         '| Goal | From | State | Measure | Review |', '|---|---|---|---|---|']
    for r in goals:
        L.append(f"| {r['statement'][:260].replace('|', '/')} [{r['id']}] | {(r['subject'] or '').replace('goal ', '')} | {r['goal_state']} | "
                 f"{r.get('measure') or 'not set'} | {r.get('review_at') or 'not set'} |")
    open(os.path.join(OUT, 'goals.md'), 'w').write('\n'.join(L) + '\n')
    for mod in ('needs_triage', 'promises_triage'):                     # the two pages follow the same rows
        try:
            __import__(mod).page()
        except Exception as e:
            print(f'{mod}.page failed: {e.__class__.__name__}: {str(e)[:100]}')
    c = collections.Counter((r['kind'], r['triage']) for r in keep)
    print(f"jarvis records {len(keep)} ({len(recs) - len(keep)} left out by the screens): {dict(sorted(c.items()))}; "
          f"in the fact index: {sum(1 for r in keep if r.get('indexed'))}")
    return keep


def check():
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    n = con.execute("SELECT count(*) FROM needs WHERE state='open' AND scope='work' AND COALESCE(tainted,0)=0").fetchone()[0]
    nt = con.execute("SELECT count(*) FROM needs WHERE state='open' AND scope='work' AND COALESCE(tainted,0)!=0").fetchone()[0]
    p = con.execute("SELECT count(*) FROM commitments WHERE state='open' AND scope='work'").fetchone()[0]
    g = con.execute("SELECT count(*) FROM goals WHERE dismissed_at IS NULL").fetchone()[0]
    con.close()
    f = collections.Counter(json.loads(l)['kind'] for l in open(JREC)) if os.path.exists(JREC) else {}
    idx = {}
    try:
        import kb_facts as KF
        c2 = sqlite3.connect(f'file:{KF.DB}?mode=ro', uri=True)
        idx = dict(c2.execute("SELECT kind, count(*) FROM facts WHERE note LIKE 'jarvis:%' GROUP BY 1").fetchall()); c2.close()
    except Exception:
        pass
    print(f"jarvis.db: open work needs {n} (+{nt} tainted, left out), open work promises {p}, goals {g}")
    print(f"file     : open {f.get('open', 0)}, promise {f.get('promise', 0)}, goal {f.get('goal', 0)}")
    print(f"index    : {idx}")
    return n == f.get('open', 0) and p == f.get('promise', 0) and g == f.get('goal', 0)


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    db = os.path.join(d, 'j.db')
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE needs (id, created, kind, statement, target, why, unblocks, assumed, recognizer, origin, tainted, source_ref, quote, topic, state, "
                "goal_id, last_checked, candidates, dismissed_at, note, updated, scope)")
    con.execute("CREATE TABLE commitments (id, created, who, to_whom, what, due, state, origin, source_ref, quote, dismissed_at, updated, scope, checked_at, "
                "check_verdict, check_note, nudged_at)")
    con.execute("CREATE TABLE goals (id, created, statement, source, state, success, review_at, dismissed_at, updated)")
    row = lambda i, state, scope, tainted=0: (i, '2026-11-01', 'data_need', f'need {i}', '', '', '', '', '', 'mined', tainted, 'src', 'q', 't-' + i, state,
                                              None, None, None, None, None, '2026-11-01', scope)
    for r in (row('n1', 'open', 'personal'), row('n2', 'open', 'work', 1), row('n3', 'merged', 'work'), row('n4', 'open', 'work'), row('n5', 'open', 'work'),
              row('n6', 'open', 'work')):
        con.execute('INSERT INTO needs VALUES (' + ','.join('?' * 22) + ')', r)
    con.commit()
    a = records(db, d)
    assert [r['id'] for r in a] == ['n4', 'n5', 'n6'], [r['id'] for r in a]              # 6 needs, 3 records
    assert json.dumps(a) == json.dumps(records(db, d))                                  # a re-run gives the same
    con.execute("UPDATE needs SET state='answered' WHERE id='n5'"); con.commit(); con.close()
    assert [r['id'] for r in records(db, d)] == ['n4', 'n6']                             # a closed need is gone
    print('selftest ok: 6 needs -> 3 records; re-run identical; a closed need disappears')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['build']:
        build()
    elif a[:1] == ['check']:
        print('agree' if check() else 'DIFFER')
    elif a[:1] == ['selftest']:
        selftest()
    else:
        print(__doc__)
