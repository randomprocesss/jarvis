"""Full-text search over every piece of the owner's screened work text: the knowledge-base notes,
the raw text the source notes were read from, the archived work chats and the memory notes. The backstop for "nothing
missed": a detail a summary left out can still be found and quoted with its source.

Work only: the chat archive holds work chats alone (personal and admin chats moved out, personal lines dropped:
archive_scope, personal_deep_check), and nothing outside these screened folders is indexed. No model use.
Index: state/kb_search.db (SQLite FTS5), updated by file change time.

    kb_search.py build              index new and changed files, drop removed ones
    kb_search.py q <words...>       the best passages with their source
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import datetime as dt, glob, json, os, re, sqlite3, sys

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'kb_search.db')
B = os.path.join(HUB, 'brain')
MEMORY = str(paths.claude_memory())
KB = str(paths.kb())
CHUNK = 2000


def corpora():
    """(path, kind) for every file the index holds."""
    for f in glob.glob(os.path.join(str(paths.kb()), '*.md')) + glob.glob(os.path.join(str(paths.kb()), 'topics', '*.md')):
        yield f, 'workstream' if '/topics/' in f else 'project'
    for f in glob.glob(os.path.join(str(paths.kb()), 'people', '*.md')):
        yield f, 'person'                              # how each person wants things done
    for f in glob.glob(os.path.join(str(paths.kb()), 'ledger', '*.md')):
        yield f, 'ledger'                              # records per workstream, each with its check
    for f in glob.glob(os.path.join(str(paths.kb()), 'entities', '*', '*.md')):
        yield f, 'entity'                              # one page per ticket, table, code, place, pull request
    for f in glob.glob(os.path.join(str(paths.kb()), 'owner', '*.md')):
        yield f, 'owner'                               # what the owner knows, how they decide, how they work
    for f in glob.glob(os.path.join(B, 'sessions', '*.md')):
        yield f, 'chat note'
    for f in glob.glob(os.path.join(B, 'sources', '*.md')):
        yield f, 'source note'
    for f in glob.glob(os.path.join(B, 'sources_extra_later', '*.txt')):
        yield f, 'raw document'                         # parked for a later read: searchable meanwhile
    for f in glob.glob(os.path.join(B, 'sources_extra', '*')):
        if os.path.isfile(f) and not os.path.basename(f).startswith('.'):
            yield f, 'raw document'
    for f in glob.glob(os.path.join(B, 'sources_raw', '*.txt')):
        yield f, 'raw document'                         # Step 5: raw data files, catalogued, full text here
    for f in glob.glob(os.path.join(B, 'raw_archive', 'claude_projects', '**', '*.jsonl'), recursive=True):
        yield f, 'raw chat'
    for f in glob.glob(os.path.join(MEMORY, '*.md')):
        yield f, 'memory'


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript('''
        CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, kind TEXT);
        CREATE TABLE IF NOT EXISTS chunkmap (path TEXT, rid INTEGER);
        CREATE INDEX IF NOT EXISTS chunkmap_path ON chunkmap(path);
        CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(text, title UNINDEXED, path UNINDEXED, kind UNINDEXED,
                                                           date UNINDEXED, loc UNINDEXED, tokenize='porter unicode61');''')
    return c


def _chunks(text):
    """Pieces of about CHUNK characters, cut at a line end where one is near."""
    i = 0
    while i < len(text):
        j = min(len(text), i + CHUNK)
        if j < len(text):
            k = text.rfind('\n', i + CHUNK // 2, j)
            j = k + 1 if k > 0 else j
        yield i, text[i:j]
        i = j


def _note(path):
    t = open(path, errors='replace').read()
    title = (re.search(r'^title: (.*)$', t, re.M) or re.search(r'^# (.*)$', t, re.M))
    date = re.search(r'(20\d\d-\d\d-\d\d)', os.path.basename(path)) or re.search(r'^updated: (\S+)', t, re.M)
    return [(title.group(1) if title else os.path.basename(path), date.group(1) if date else '', f'chars {i}', c) for i, c in _chunks(t)]


def _chat(path):
    """One chat transcript as passages: each record's own text (their words, Claude's replies, commands and their output)."""
    sid = os.path.basename(path)[:-6]
    out, buf, first, day = [], '', None, ''
    for n, line in enumerate(open(path, errors='replace')):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get('message') or {}
        c = msg.get('content')
        parts = [c] if isinstance(c, str) else []
        for x in c if isinstance(c, list) else []:
            if not isinstance(x, dict):
                continue
            if x.get('type') == 'text':
                parts.append(x.get('text') or '')
            elif x.get('type') == 'tool_use':
                parts.append(f"[{x.get('name')}] " + json.dumps(x.get('input') or {}, ensure_ascii=False)[:20000])
            elif x.get('type') == 'tool_result':
                r = x.get('content')
                parts.append(r if isinstance(r, str) else ' '.join(y.get('text', '') for y in r or [] if isinstance(y, dict)))
        t = '\n'.join(p for p in parts if p).strip()
        if not t:
            continue
        who = {'user': 'OWNER/TOOL', 'assistant': 'CLAUDE'}.get(d.get('type'), d.get('type') or '')
        day = day or (d.get('timestamp') or '')[:10]
        first = n if first is None else first
        buf += f'{who}: {t}\n'
        if len(buf) >= CHUNK:
            out.append((f'chat {sid}', day, f'records {first}-{n}', buf)); buf, first = '', None
    if buf:
        out.append((f'chat {sid}', day, f'records {first}-end', buf))
    return out


def build():
    c = _db()
    seen, new, gone = set(), 0, 0
    known = dict(c.execute('SELECT path, mtime FROM files'))
    for path, kind in corpora():
        seen.add(path)
        m = os.path.getmtime(path)
        if known.get(path) == m:
            continue
        try:
            rows = _chat(path) if kind == 'raw chat' else _note(path)
        except OSError:
            continue
        old = [r for (r,) in c.execute('SELECT rid FROM chunkmap WHERE path = ?', (path,))]
        c.executemany('DELETE FROM docs WHERE rowid = ?', [(r,) for r in old])
        c.execute('DELETE FROM chunkmap WHERE path = ?', (path,))
        for title, date, loc, text in rows:
            cur = c.execute('INSERT INTO docs (text, title, path, kind, date, loc) VALUES (?, ?, ?, ?, ?, ?)', (text, title, path, kind, date, loc))
            c.execute('INSERT INTO chunkmap VALUES (?, ?)', (path, cur.lastrowid))
        c.execute('INSERT OR REPLACE INTO files VALUES (?, ?, ?)', (path, m, kind))
        new += 1
        if new % 200 == 0:
            c.commit(); print(f'  indexed {new} files', flush=True)
    for path in set(known) - seen:                       # removed or moved out (a personal chat leaves the archive)
        old = [r for (r,) in c.execute('SELECT rid FROM chunkmap WHERE path = ?', (path,))]
        c.executemany('DELETE FROM docs WHERE rowid = ?', [(r,) for r in old])
        c.execute('DELETE FROM chunkmap WHERE path = ?', (path,)); c.execute('DELETE FROM files WHERE path = ?', (path,))
        gone += 1
    c.commit()
    n = c.execute('SELECT count(*) FROM files').fetchone()[0]
    print(f'{dt.datetime.now():%H:%M} index: {new} files added or changed, {gone} removed, {n} files in all')
    return new


def search(query, limit=8, kinds=None):
    """[{kind, title, date, source, where, passage}] best first. Every word must appear; if none match, any word may."""
    if not os.path.exists(DB):
        return []
    words = [w for w in re.findall(r'[A-Za-z0-9_$%.\-]+', query) if len(w) >= 2]
    if not words:
        return []
    c = sqlite3.connect(DB)
    q = lambda op: f' {op} '.join('"' + w.replace('"', '') + '"' for w in words)
    where = (' AND kind IN (' + ','.join('?' * len(kinds)) + ')') if kinds else ''
    for op in ('AND', 'OR'):
        rows = c.execute(f"SELECT kind, title, date, path, loc, snippet(docs, 0, '«', '»', ' … ', 60) FROM docs "
                         f"WHERE docs MATCH ?{where} ORDER BY bm25(docs) LIMIT ?", [q(op)] + list(kinds or []) + [limit]).fetchall()
        if rows:
            break
    home = os.path.expanduser('~')
    out = []
    for k, t, d, p, l, s in rows:
        r = {'kind': k, 'title': t, 'date': d, 'source': p.replace(home, '~'), 'where': l, 'passage': s}
        r.update(evidence(p, k, d))
        out.append(r)
    return out


_P = {}
READS_AS = {   # 6 Nov (structure plan A1, the owner: "otherwise claude code will quietly give wrong answers"): what a passage is
    'current': 'workstream note: the settled view',
    'history': 'workstream history: how it got here, not the current value',
    'summary': 'project-wide summary note',
    'as-said': 'what was said in a chat or task on that date; it may have been corrected later',
    'record': 'a record as read from its system on that date',
    'document': 'a document as written on that date',
    'memory': "a memory note (The owner's corrections, rules and final results)",
    'superseded': 'SUPERSEDED: do not quote as current',
    'profile': 'a profile note (how a person works)',
}


def evidence(path, kind, date=''):
    """{status, reads_as, data_cutoffs} for a passage, from props.json (in the knowledge base) (tools/kb_props.py)."""
    pp = os.path.join(str(paths.kb()), 'props.json')
    try:
        m = os.path.getmtime(pp)
        if _P.get('m') != m:
            _P.update(m=m, d=json.load(open(pp)))
    except (OSError, ValueError):
        _P.setdefault('d', {})
    base = os.path.basename(path)[:-3] if path.endswith('.md') else ''
    rel = path[len(KB):].replace(os.sep, '/') if path.startswith(KB) else ''          # where it sits inside the knowledge base
    key = ('person:' + base if rel.startswith('/people/') else 'owner:' + base if rel.startswith('/owner/') else
           'memory:' + base if path.startswith(MEMORY) else base)
    p = (_P.get('d') or {}).get(key)
    if rel.startswith('/ledger/'):
        return {'status': 'ledger', 'reads_as': 'ledger page: one record per number or decision, each with its source and whether the number was found there; superseded and contradicted records are marked'}
    if rel.startswith('/entities/'):
        return {'status': 'entity', 'reads_as': 'entity page, built in code from the notes and the sources as read; each mention on it says what kind of evidence it is'}
    if not p:
        if kind in ('raw chat',):
            return {'status': 'raw', 'reads_as': 'raw chat text, word for word as it was said then; it may have been corrected later'}
        if kind in ('raw document',):
            return {'status': 'raw', 'reads_as': 'raw text of a file or record, as written then'}
        return {'status': 'unknown', 'reads_as': 'not classified yet'}
    e = {'status': p['status'], 'reads_as': READS_AS.get(p['status'], p['status'])}
    if p.get('data_cutoffs'):
        e['data_cutoffs'] = p['data_cutoffs'][-4:]
    return e


if __name__ == '__main__':
    if sys.argv[1:2] == ['build']:
        build()
    elif sys.argv[1:2] == ['q']:
        for r in search(' '.join(sys.argv[2:])):
            print(f"[{r['kind']} · {r['title']} · {r['date']} · {r['source']} {r['where']}]\n  reads as: {r.get('reads_as')}"
                  + (f" · data cut-off {', '.join(r['data_cutoffs'])}" if r.get('data_cutoffs') else '') + f"\n  {r['passage']}\n")
    else:
        print(__doc__)
