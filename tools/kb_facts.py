#!/usr/bin/env python3
"""The fact ledger as an answer source: one answer path.

A summary note holds the first screens of a topic; a question about one number is usually answered by one checked record
in the ledger (ledger/records.jsonl in the knowledge base: statement, value, unit, scope, data cut-off, status, source).
Without this index such questions come back "not found" although the fact is on file.

    kb_facts.py build            a full-text index of the records (ledger/facts.db (in the knowledge base)); cheap, code only
    kb_facts.py find "question"  the best matching records
find(question, k) is what project_brief and the kb_facts tool call. Current records come first; a superseded record that
matches is shown too, marked SUPERSEDED, so the old value is recognised and never given as current.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import json, os, re, sqlite3, sys

HUB = str(paths.home())
REC = os.path.join(str(paths.kb()), 'ledger', 'records.jsonl')
DB = os.path.join(str(paths.kb()), 'ledger', 'facts.db')
JREC = os.path.join(str(paths.kb()), 'ledger', 'jarvis_records.jsonl')   # 12 Nov: Jarvis's own open questions, promises and
#                                                                              goals in the ledger's shape (tools/kb_bridge.py)
JWORD = {'open': 'open question still open', 'promise': 'promise owed', 'goal': 'goal'}
STOP = set('the a an of to for and or in on at is are was were be been it its this that with by from as not no what which who when how '
           'many much does do did has have had will would should could can may than then there their our your after before '
           'about into out per each all any both between during under over only also still given show shows shown number '
           'count total value figure'.split())


def _nums(s):
    """Numbers as plain digit strings, so "1,234,567" and "1234567" are the same word."""
    return ' '.join(x.replace(',', '') for x in re.findall(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?', str(s or '')))


FLAGS = os.path.join(os.path.dirname(DB), 'facts_flags.json')
CORR = os.path.join(os.path.dirname(DB), 'corrections.jsonl')


def correct(rid, verdict, evidence, correct_text='', why='', by=''):
    """13 Nov: a record the number check disputed, judged against its original evidence and kept on file
    (ledger/corrections.jsonl (in the knowledge base)), so the verdict outlasts every rebuild. verdict: right (the record holds; the
    warning goes) | wrong (correct_text says what is true, and answers show that instead). A record replaced by a later
    one goes through kb_contradictions.settle(..., new_set=True) instead."""
    import datetime as dt
    if verdict not in ('right', 'wrong') or not str(evidence).strip() or (verdict == 'wrong' and not str(correct_text).strip()):
        raise ValueError('a correction needs right|wrong, the evidence, and for wrong the correct text')
    row = {'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'id': rid, 'verdict': verdict,
           'correct': str(correct_text).strip(), 'why': str(why).strip(), 'evidence': str(evidence).strip(), 'by': by}
    with open(CORR, 'a') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
    _write_flags()
    return row


def _write_flags():
    """13 Nov: records the number check marked 'contradicted' (a cited source states another value) and not yet settled by a
    checked correction: {id: what the source says}. lines() prints the warning with the record, so an answer never uses one
    of them as plain fact."""
    flags, judged = {}, {}
    if os.path.exists(CORR):                             # checked corrections (correct()): the newest verdict per record wins
        for l in open(CORR):
            try:
                c = json.loads(l)
            except ValueError:
                continue
            judged[c['id']] = c
    for l in open(REC):
        try:
            r = json.loads(l)
        except ValueError:
            continue
        c = judged.get(r.get('id'))
        if r.get('status') == 'current' and c and c['verdict'] == 'wrong':
            flags[r['id']] = f"CORRECTED (checked {c['at'][:10]}): {c['correct']} [{c['evidence'][:160]}]"[:420]
        elif r.get('status') == 'current' and r.get('checked') == 'contradicted' and not c:
            v = r.get('verdict') or {}
            flags[r['id']] = 'DISPUTED: the number check found its source says: ' + str(r.get('source_says') or v.get('source_says') or 'a cited source states another value')[:300]
    tmp = FLAGS + '.tmp'
    json.dump(flags, open(tmp, 'w')); os.replace(tmp, FLAGS)
    return len(flags)


_FL = {}


def _flags():
    try:
        m = os.path.getmtime(FLAGS)
    except OSError:
        return {}
    if _FL.get('m') != m:
        _FL.update(m=m, d=json.load(open(FLAGS)))
    return _FL['d']


def build():
    try:
        _write_flags()
    except (OSError, ValueError):
        pass
    tmp = DB + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("CREATE VIRTUAL TABLE facts USING fts5(id UNINDEXED, body, nums, kind UNINDEXED, status UNINDEXED, workstream UNINDEXED, "
                "cutoff UNINDEXED, note UNINDEXED, superseded_by UNINDEXED, statement UNINDEXED, contradicts UNINDEXED, tokenize='unicode61')")
    n = 0
    with con:
        for l in open(REC):
            try:
                r = json.loads(l)
            except ValueError:
                continue
            text = ' '.join(str(r.get(k) or '') for k in ('subject', 'statement', 'metric', 'value', 'unit', 'scope', 'who', 'why', 'quote'))
            con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (r.get('id'), text, _nums(text), r.get('kind') or '', r.get('status') or '', r.get('workstream') or '',
                         r.get('data_cutoff') or r.get('as_of') or '', r.get('note') or '', ','.join(r.get('superseded_by') or []), r.get('statement') or '',
                         ','.join(r.get('contradicts') or []) if r.get('status') == 'current' else ''))
            n += 1
        try:
            jl = open(JREC)
        except OSError:
            jl = []
        for l in jl:                                     # only the ones judged still open or owed, and the goals
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if not r.get('indexed'):
                continue
            text = JWORD.get(r.get('kind'), '') + ' ' + ' '.join(str(r.get(k) or '') for k in ('subject', 'statement', 'who', 'why', 'quote', 'workstream'))
            con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (r.get('id'), text, _nums(text), r.get('kind') or '', 'current', r.get('workstream') or '', r.get('as_of') or '',
                         r.get('note') or 'jarvis:', '', (f"[priority {r['priority']}] " if r.get('priority') else '') + (r.get('statement') or ''), ''))
            n += 1
    con.close()
    os.replace(tmp, DB)
    return n


def _fresh():
    try:
        if not os.path.exists(DB) or os.path.getmtime(DB) < max(os.path.getmtime(REC), os.path.getmtime(JREC) if os.path.exists(JREC) else 0):
            build()
        return True
    except OSError:
        return False


def find(question, k=8, superseded=3):
    """[{id, kind, status, workstream, cutoff, note, superseded_by, statement}] best first: current records, then up to
    `superseded` old ones that match."""
    if not _fresh():
        return []
    q = str(question or '')
    words = [w for w in re.findall(r'[A-Za-z][A-Za-z0-9_$%-]{2,}', q) if w.lower() not in STOP]
    # a hyphenated word ("smooth-demand group") was once taken as one phrase
    # ("smooth demand", never in the record), short codes like H1 / P50 / Q4 were dropped, and "miss" never met "missed"
    # (the index has no stemming). Hyphenated words now also count by their parts, short letter+digit codes are kept, and
    # a word of 5+ letters also matches by its stem as a prefix.
    words += [p for w in words if '-' in w for p in w.split('-') if len(p) >= 3 and p.lower() not in STOP]
    words += re.findall(r'\b[A-Za-z]{1,2}\d{1,3}\b', q)
    stems = [re.sub(r'(ing|ed|es|s)$', '', w.lower()) for w in words if w.isalpha() and len(w) >= 5]
    nums = [x for x in _nums(q).split() if len(x) >= 2]
    if not words and not nums:
        return []
    esc = lambda w: '"' + w.replace('"', '') + '"'
    parts = []
    if words:
        terms = [esc(w) for w in dict.fromkeys(words)] + [esc(t) + '*' for t in dict.fromkeys(stems) if len(t) >= 4]
        parts.append('body : (' + ' OR '.join(terms) + ')')
    if nums:
        parts.append('nums : (' + ' OR '.join(esc(x) for x in dict.fromkeys(nums)) + ')')
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    q = ("SELECT id, kind, status, workstream, cutoff, note, superseded_by, statement, {c} bm25(facts, 0, 1.0, 2.5) AS s FROM facts "
         "WHERE facts MATCH ? ORDER BY s LIMIT 60")
    try:
        rows = con.execute(q.format(c='contradicts,'), (' OR '.join(parts),)).fetchall()
    except sqlite3.OperationalError:
        try:                                             # an index built before 12 Nov has no conflicts column
            rows = [r[:8] + ('',) + r[8:] for r in con.execute(q.format(c=''), (' OR '.join(parts),)).fetchall()]
        except sqlite3.OperationalError:
            rows = []
    con.close()
    keys = ('id', 'kind', 'status', 'workstream', 'cutoff', 'note', 'superseded_by', 'statement', 'contradicts')
    recs = [dict(zip(keys, r[:9])) for r in rows]
    cur = [r for r in recs if r['status'] == 'current'][:k]
    old = [r for r in recs if r['status'] != 'current'][:superseded]
    return cur + old


def _statements(ids):
    """{id: (statement, note)} for the given record ids."""
    ids = list(dict.fromkeys(ids))
    if not ids or not _fresh():
        return {}
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        rows = con.execute(f"SELECT id, statement, note FROM facts WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    return {i: (st, note) for i, st, note in rows}


def lines(question, k=8):
    """The records as text for an answer: one line each, saying what it is."""
    out = []
    recs = find(question, k)
    now = _statements([x for r in recs for k in ('superseded_by', 'contradicts') for x in (r.get(k) or '').split(',') if x])
    for r in recs:
        tag = 'current' if r['status'] == 'current' else f"SUPERSEDED by {r['superseded_by'] or 'a later record'}: do not give as current"
        if r['status'] == 'current' and r.get('contradicts'):   # 12 Nov: an unsettled conflict is said with the record
            tag = f"current, but IN CONFLICT with {r['contradicts']} (not settled: check the source before using either)"
        elif r['status'] == 'current' and r['id'] in _flags():
            fl = _flags()[r['id']]
            tag = (f"current in the ledger, but {fl}: give the corrected value, not this one" if fl.startswith('CORRECTED')
                   else f"current, but {fl} (check the source before using it)")
        if str(r['note']).startswith('jarvis:'):         # Jarvis's own record, not a line parsed from a note
            what = {'open': 'OPEN QUESTION, judged still open', 'promise': 'PROMISE still owed', 'goal': 'GOAL (inferred by Jarvis from Jira, not confirmed)'}
            line = (f"- [{r['id']} | {what.get(r['kind'], r['kind'])} | {r['workstream'] or 'no workstream'}{' | noted ' + r['cutoff'] if r['cutoff'] else ''} | "
                    f"Jarvis's record, from {r['note'][7:] or 'its own work'}] {r['statement'][:420]}")
            out.append(line)
            continue
        line = f"- [{r['id']} | {r['kind']} | {tag} | {r['workstream']}{' | data cut-off ' + r['cutoff'] if r['cutoff'] else ''} | from the note {r['note']}] {r['statement'][:420]}"
        for x in (r['superseded_by'] or '').split(','):
            if x in now:                                 # 12 Nov: the replacing record is shown with the old one, so the old
                line += f"\n    -> in force now [{x}, from the note {now[x][1]}]: {now[x][0][:300]}"   # value is never the only thing read
        if r['status'] == 'current':
            for x in (r.get('contradicts') or '').split(',')[:2]:
                if x in now:
                    line += f"\n    -> conflicts with [{x}, from the note {now[x][1]}]: {now[x][0][:300]}"
        out.append(line)
    return '\n'.join(out)


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['build']:
        print(f'{build():,} records indexed in {DB}')
    elif a[:1] == ['find'] and len(a) > 1:
        print(lines(' '.join(a[1:])) or 'no record matches')
    else:
        print(__doc__)
