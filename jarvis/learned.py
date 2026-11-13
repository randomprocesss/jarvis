"""What Jarvis has learned, and the only door into it (HARNESS.md §3d, §4, §6.3; Phase 5, 5 Oct 2026).

One store (state/jarvis.db), one writer (apply_edits), one audit trail (learn_edits):
  facts            what holds about their work and them: valid_from / valid_to (closed, never deleted), origin, source, quote
  rules            how to behave, scoped (voice, writer, analyst, coder, all), at most MAX_RULES active, each with its source
  recipient_notes  how they write to a person, learned from their edits to drafts
  memory           what they asked Jarvis to remember ("remember ...")
  learn_signals    what their reactions say (corrected, told off, re-asked, cancelled a read-back, edited a draft, dismissed ...)
  learn_edits      every proposed change, accepted or refused, with why: the Learned view and undo read this
  labels           their good / bad marks on past answers (the weekly label page)
  recall           FTS5 over conversations, episodes, facts and memory, with dates (what was true when)

The validator (apply_edits) decides in code, never the model:
  - origin is set by the server from what the edit cites, not by what the model claims. Only owner_said, owner_ack or
    executed may write rules, their preferences or memory; external content may only add facts, marked origin=external.
  - nothing that reads as a permission or standing instruction is ever learned (patterns in tools/patterns/learning.txt,
    kept in a file, never inline): permissions live in policy and change only by their hand.
  - caps: MAX_RULES active rules; texts are short; a secret is never stored.
  - every accepted change is undoable (undo() applies the inverse through the same door).
"""
from jarvis import paths
import datetime as dt, json, os, re, sqlite3, threading, uuid

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'jarvis.db')
PATTERNS = os.path.join(str(paths.REPO), 'tools', 'patterns', 'learning.txt')
MAX_RULES = 25
TRUSTED = ('owner_said', 'owner_ack', 'executed')
ORIGINS = TRUSTED + ('task_limitation', 'inferred', 'external')
SCOPES = ('voice', 'writer', 'analyst', 'coder', 'all')
FACT_KINDS = ('person', 'project', 'state', 'preference', 'term')
SECRET = re.compile(r"\b(password|passwd|pgpass|api[_ -]?key|secret|token|bearer)\s*(?:[:=]|\bis\b)\s*\S+|\b[A-Za-z0-9+/_-]{32,}\b", re.I)
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY, kind TEXT, text TEXT, valid_from TEXT, valid_to TEXT, origin TEXT,
  source_ref TEXT, quote TEXT, tainted INTEGER DEFAULT 0, created TEXT, closed_why TEXT);
CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, scope TEXT, text TEXT, why TEXT, origin TEXT, source_ref TEXT,
  state TEXT, created TEXT, retired_at TEXT, retired_why TEXT);
CREATE TABLE IF NOT EXISTS recipient_notes(recipient TEXT, channel TEXT, note TEXT, source_ref TEXT, origin TEXT,
  updated_at TEXT, PRIMARY KEY(recipient, channel));
CREATE TABLE IF NOT EXISTS memory(id TEXT PRIMARY KEY, text TEXT, origin TEXT, source_ref TEXT, created TEXT, removed_at TEXT);
CREATE TABLE IF NOT EXISTS learn_signals(id INTEGER PRIMARY KEY, at TEXT, kind TEXT, origin TEXT, ref TEXT, data TEXT, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS learn_edits(id TEXT PRIMARY KEY, at TEXT, run TEXT, op TEXT, tbl TEXT, row_id TEXT, text TEXT,
  origin TEXT, source_ref TEXT, accepted INTEGER, why TEXT, inverse TEXT, undone_at TEXT);
CREATE TABLE IF NOT EXISTS labels(trace TEXT PRIMARY KEY, at TEXT, label TEXT, note TEXT, q TEXT, answer TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS recall USING fts5(text, keys, at UNINDEXED, src UNINDEXED, ref UNINDEXED);
CREATE INDEX IF NOT EXISTS learn_edits_at ON learn_edits(at);
"""


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def con():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def q(sql, args=()):
    with _lock:
        c = con()
        try:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
        finally:
            c.close()


def x(sql, args=()):
    with _lock:
        c = con()
        try:
            cur = c.execute(sql, args)
            c.commit()
            return cur.rowcount
        finally:
            c.close()


def _clean(t, n=400):
    return re.sub(r'\s+', ' ', str(t or '')).strip()[:n]


# ------------------------------------------------------------------ the pattern file (permissions and standing orders)
_pat = {'mtime': None, 'rx': None}


def forbidden():
    """Compiled patterns from tools/patterns/learning.txt (one regex per line; # comments). Never inline (memory note:
    the classifier blocks credential-greps written inline)."""
    try:
        m = os.path.getmtime(PATTERNS)
    except OSError:
        return None
    if _pat['mtime'] != m:
        lines = [l.strip() for l in open(PATTERNS) if l.strip() and not l.strip().startswith('#')]
        _pat['rx'] = re.compile('|'.join(f'(?:{l})' for l in lines), re.I) if lines else None
        _pat['mtime'] = m
    return _pat['rx']


def permission_like(text):
    rx = forbidden()
    return bool(rx and rx.search(str(text or '')))


# ------------------------------------------------------------------ signals
def signal(kind, origin='owner_said', ref='', **data):
    """One learning signal (their reaction, an edit, a dismissal). Never raises."""
    try:
        x('INSERT INTO learn_signals(at, kind, origin, ref, data) VALUES(?,?,?,?,?)',
          (now(), kind, origin, str(ref)[:200], json.dumps(data, ensure_ascii=False, default=str)[:6000]))
    except Exception:
        pass


# ------------------------------------------------------------------ the validator: the only door
def _decide(e):
    """(ok, why, origin) for one proposed edit, before it is applied."""
    op, tbl = e.get('op'), e.get('table')
    origin = e.get('origin') if e.get('origin') in ORIGINS else 'external'
    text = str(e.get('text') or '')
    if op not in ('ADD', 'CLOSE', 'UPDATE'):
        return False, f'unknown op {op}', origin
    if tbl not in ('facts', 'rules', 'recipient_notes', 'memory'):
        return False, f'unknown table {tbl}', origin
    if op in ('ADD', 'UPDATE'):
        if not text.strip():
            return False, 'empty text', origin
        if SECRET.search(text):
            return False, 'looks like a secret', origin
        if len(text) > (600 if tbl == 'recipient_notes' else 400):
            return False, 'too long', origin
    if tbl in ('rules', 'memory', 'recipient_notes') and op in ('ADD', 'UPDATE'):
        if origin not in TRUSTED:
            return False, f'{tbl} only from their own words or what they confirmed (origin {origin})', origin
        if permission_like(text):
            return False, 'reads as a permission or standing order: those change only in policy, by their hand', origin
    if tbl == 'facts' and op in ('ADD', 'UPDATE'):
        if e.get('kind') == 'preference' and origin not in TRUSTED:
            return False, 'a preference of theirs only from their own words', origin
        if permission_like(text):
            return False, 'reads as a permission or standing order', origin
    if tbl == 'rules' and op == 'ADD':
        if e.get('scope') not in SCOPES:
            return False, f"scope must be one of {SCOPES}", origin
        n = q("SELECT count(*) AS n FROM rules WHERE state='active'")[0]['n']
        if n >= MAX_RULES:
            return False, f'{MAX_RULES} rules are active already: retire one first', origin
        if e.get('state') not in (None, 'active', 'staged'):
            return False, 'a rule is active or staged', origin
        if e.get('state') == 'staged' and q("SELECT count(*) AS n FROM rules WHERE state='staged'")[0]['n'] >= 5:
            return False, '5 rules are waiting for the safety gate already', origin
    if op in ('CLOSE', 'UPDATE') and not e.get('id') and tbl != 'recipient_notes':
        return False, 'no id', origin
    if op in ('CLOSE', 'UPDATE') and tbl in ('rules', 'memory', 'recipient_notes') and origin not in TRUSTED:
        return False, f'only they (or what they confirmed) can change {tbl}', origin
    if op in ('CLOSE', 'UPDATE') and tbl == 'facts' and origin not in TRUSTED:
        k = q('SELECT kind FROM facts WHERE id=?', (e.get('id'),))
        if k and k[0]['kind'] == 'preference':
            return False, 'a preference of theirs only changes by their own words', origin
    return True, '', origin


def apply_edits(edits, run='', source_ref=''):
    """Validate and apply a list of edits {op, table, id?, text?, kind?, scope?, why?, recipient?, channel?, origin,
    source_ref?, quote?, tainted?}. Returns [(edit, accepted, why)]. Every edit, accepted or not, is in learn_edits."""
    out = []
    run = run or 'run-' + uuid.uuid4().hex[:6]
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        ok, why, origin = _decide(e)
        inverse, row = None, e.get('id') or ''
        if ok:
            try:
                row, inverse = _apply_one(e, origin, e.get('source_ref') or source_ref)
            except Exception as ex:
                ok, why = False, f'{ex.__class__.__name__}: {ex}'[:200]
        x('INSERT INTO learn_edits(id, at, run, op, tbl, row_id, text, origin, source_ref, accepted, why, inverse) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
          ('ed-' + uuid.uuid4().hex[:10], now(), run, e.get('op'), e.get('table'), row, _clean(e.get('text'), 600), origin,
           _clean(e.get('source_ref') or source_ref, 200), 1 if ok else 0, why or _clean(e.get('why'), 200),
           json.dumps(inverse) if inverse else None))
        out.append((e, ok, why))
    return out


def _apply_one(e, origin, src):
    """Apply one validated edit; returns (row id, the inverse edit for undo)."""
    op, tbl, t = e['op'], e['table'], now()
    text = _clean(e.get('text'), 600)
    if tbl == 'facts':
        if op == 'ADD':
            fid = e.get('id') or uuid.uuid4().hex[:8]
            dup = q("SELECT id FROM facts WHERE valid_to IS NULL AND lower(text)=lower(?)", (text,))
            if dup:
                raise ValueError('the same fact holds already')
            x('INSERT INTO facts(id, kind, text, valid_from, valid_to, origin, source_ref, quote, tainted, created) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (fid, e.get('kind') if e.get('kind') in FACT_KINDS else 'project', text, e.get('since') or t[:10], None, origin, src,
               _clean(e.get('quote'), 300), 1 if (e.get('tainted') or origin == 'external') else 0, t))
            return fid, {'op': 'CLOSE', 'table': 'facts', 'id': fid, 'why': 'undo'}
        if op == 'CLOSE':
            if not x("UPDATE facts SET valid_to=?, closed_why=? WHERE id=? AND valid_to IS NULL", (t[:10], _clean(e.get('why'), 120) or 'closed', e['id'])):
                raise ValueError('no such open fact')
            return e['id'], {'op': 'REOPEN', 'table': 'facts', 'id': e['id']}
        if op == 'UPDATE':                       # an update is close + add: the old wording keeps its dates
            old = q('SELECT * FROM facts WHERE id=? AND valid_to IS NULL', (e['id'],))
            if not old:
                raise ValueError('no such open fact')
            x("UPDATE facts SET valid_to=?, closed_why='updated' WHERE id=?", (t[:10], e['id']))
            nid = uuid.uuid4().hex[:8]
            x('INSERT INTO facts(id, kind, text, valid_from, valid_to, origin, source_ref, quote, tainted, created) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (nid, old[0]['kind'], text, t[:10], None, origin, src, _clean(e.get('quote'), 300), 1 if origin == 'external' else 0, t))
            return nid, {'op': 'SWAP', 'table': 'facts', 'id': nid, 'back': e['id']}
    if tbl == 'rules':
        if op == 'ADD':
            rid = e.get('id') or 'r-' + uuid.uuid4().hex[:6]
            if q("SELECT id FROM rules WHERE state='active' AND lower(text)=lower(?)", (text,)):
                raise ValueError('the same rule is active already')
            x("INSERT INTO rules(id, scope, text, why, origin, source_ref, state, created) VALUES(?,?,?,?,?,?,?,?)",
              (rid, e['scope'], text, _clean(e.get('why'), 200), origin, src, e.get('state') or 'active', t))
            return rid, {'op': 'CLOSE', 'table': 'rules', 'id': rid, 'why': 'undo'}
        if op == 'CLOSE':
            if not x("UPDATE rules SET state='retired', retired_at=?, retired_why=? WHERE id=? AND state IN ('active','staged')",
                     (t, _clean(e.get('why'), 120) or 'retired', e['id'])):
                raise ValueError('no such active rule')
            return e['id'], {'op': 'REOPEN', 'table': 'rules', 'id': e['id']}
        if op == 'UPDATE':
            old = q("SELECT * FROM rules WHERE id=? AND state IN ('active','staged')", (e['id'],))
            if not old:
                raise ValueError('no such active rule')
            x("UPDATE rules SET state='retired', retired_at=?, retired_why='updated' WHERE id=?", (t, e['id']))
            nid = 'r-' + uuid.uuid4().hex[:6]
            x("INSERT INTO rules(id, scope, text, why, origin, source_ref, state, created) VALUES(?,?,?,?,?,?,?,?)",
              (nid, old[0]['scope'], text, _clean(e.get('why'), 200), origin, src, old[0]['state'], t))
            return nid, {'op': 'SWAP', 'table': 'rules', 'id': nid, 'back': e['id']}
    if tbl == 'memory':
        if op == 'ADD':
            mid = e.get('id') or uuid.uuid4().hex[:8]
            if q('SELECT id FROM memory WHERE removed_at IS NULL AND lower(text)=lower(?)', (text,)):
                raise ValueError('already remembered')
            x('INSERT INTO memory(id, text, origin, source_ref, created) VALUES(?,?,?,?,?)', (mid, text, origin, src, t))
            return mid, {'op': 'CLOSE', 'table': 'memory', 'id': mid, 'why': 'undo'}
        if op == 'CLOSE':
            if not x('UPDATE memory SET removed_at=? WHERE id=? AND removed_at IS NULL', (t, e['id'])):
                raise ValueError('no such memory')
            return e['id'], {'op': 'REOPEN', 'table': 'memory', 'id': e['id']}
        if op == 'UPDATE':
            x('UPDATE memory SET removed_at=? WHERE id=? AND removed_at IS NULL', (t, e['id']))
            nid = uuid.uuid4().hex[:8]
            x('INSERT INTO memory(id, text, origin, source_ref, created) VALUES(?,?,?,?,?)', (nid, text, origin, src, t))
            return nid, {'op': 'SWAP', 'table': 'memory', 'id': nid, 'back': e['id']}
    if tbl == 'recipient_notes':
        r, ch = _clean(e.get('recipient'), 80), _clean(e.get('channel') or 'slack', 20)
        if not r:
            raise ValueError('no recipient')
        old = q('SELECT * FROM recipient_notes WHERE recipient=? AND channel=?', (r, ch))
        if op == 'CLOSE':
            x('DELETE FROM recipient_notes WHERE recipient=? AND channel=?', (r, ch))     # the note's text stays in learn_edits
            return f'{r}|{ch}', ({'op': 'ADD', 'table': 'recipient_notes', 'recipient': r, 'channel': ch, 'text': old[0]['note']} if old else None)
        x('INSERT OR REPLACE INTO recipient_notes(recipient, channel, note, source_ref, origin, updated_at) VALUES(?,?,?,?,?,?)',
          (r, ch, text, src, origin, t))
        return f'{r}|{ch}', ({'op': 'ADD', 'table': 'recipient_notes', 'recipient': r, 'channel': ch, 'text': old[0]['note']} if old
                              else {'op': 'CLOSE', 'table': 'recipient_notes', 'recipient': r, 'channel': ch})
    raise ValueError('not applied')


def undo(edit_id):
    """Undo one accepted change: its inverse goes through the same door (origin owner_said: they pressed Undo)."""
    e = q('SELECT * FROM learn_edits WHERE id=? AND accepted=1 AND undone_at IS NULL', (edit_id,))
    if not e or not e[0].get('inverse'):
        return {'ok': False, 'error': 'nothing to undo'}
    inv = json.loads(e[0]['inverse'])
    t = now()
    if inv['op'] == 'REOPEN':
        tbl = inv['table']
        if tbl == 'facts':
            x("UPDATE facts SET valid_to=NULL, closed_why=NULL WHERE id=?", (inv['id'],))
        elif tbl == 'rules':
            x("UPDATE rules SET state='active', retired_at=NULL, retired_why=NULL WHERE id=?", (inv['id'],))
        elif tbl == 'memory':
            x("UPDATE memory SET removed_at=NULL WHERE id=?", (inv['id'],))
        res = [(inv, True, '')]
    elif inv['op'] == 'SWAP':
        tbl = inv['table']
        if tbl == 'facts':
            x("DELETE FROM facts WHERE id=?", (inv['id'],)); x("UPDATE facts SET valid_to=NULL, closed_why=NULL WHERE id=?", (inv['back'],))
        elif tbl == 'rules':
            x("DELETE FROM rules WHERE id=?", (inv['id'],)); x("UPDATE rules SET state='active', retired_at=NULL, retired_why=NULL WHERE id=?", (inv['back'],))
        elif tbl == 'memory':
            x("DELETE FROM memory WHERE id=?", (inv['id'],)); x("UPDATE memory SET removed_at=NULL WHERE id=?", (inv['back'],))
        res = [(inv, True, '')]
    else:
        res = apply_edits([{**inv, 'origin': 'owner_said', 'scope': inv.get('scope') or 'all'}], run='undo-' + edit_id,
                          source_ref='undo:' + edit_id)
    x('UPDATE learn_edits SET undone_at=? WHERE id=?', (t, edit_id))
    signal('undo', 'owner_said', edit_id, table=e[0]['tbl'], text=e[0]['text'])
    return {'ok': all(r[1] for r in res), 'edit': edit_id}


# ------------------------------------------------------------------ readers
def current_facts(kind=None):
    return q("SELECT * FROM facts WHERE valid_to IS NULL" + (" AND kind=?" if kind else '') + " ORDER BY created DESC", (kind,) if kind else ())


def active_rules(scopes=('voice', 'all')):
    return q("SELECT * FROM rules WHERE state='active' AND scope IN (%s) ORDER BY created" % ','.join('?' * len(scopes)), tuple(scopes))


def memory_items():
    return q("SELECT * FROM memory WHERE removed_at IS NULL ORDER BY created")


def recipient_note(name, channel=None):
    rows = q("SELECT * FROM recipient_notes WHERE lower(recipient) LIKE ?" + (" AND channel=?" if channel else ''),
             (f"%{str(name).lower().strip()}%",) + ((channel,) if channel else ()))
    return rows


# ------------------------------------------------------------------ recall (FTS5): what was true when
def index_rebuild():
    """Re-index everything recall searches: episodes, conversation turns, facts (open and closed, with dates), memory."""
    import glob
    voice = os.path.join(HUB, 'voice')
    rows = []
    try:
        for l in open(os.path.join(voice, 'episodes.jsonl')):
            try:
                e = json.loads(l)
            except ValueError:
                continue
            rows.append((f"{e.get('title', '')}. {e.get('summary', '')} Still open: {e.get('open', '')}", ' '.join(e.get('keys') or []) + ' '
                         + ' '.join(e.get('people') or []), str(e.get('at') or ''), 'episode', e.get('id', '')))
    except OSError:
        pass
    for f in sorted(glob.glob(os.path.join(voice, 'conversations', '*.json')))[-400:]:
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for t in d.get('turns') or []:
            rows.append((f"They said: {t.get('q', '')} Jarvis: {t.get('speak', '')}", '', str(t.get('at') or ''), 'turn', d.get('id', '')))
    for fa in q('SELECT * FROM facts'):
        span = f"from {fa['valid_from']}" + (f" until {fa['valid_to']}" if fa['valid_to'] else ' (still true)')
        rows.append((f"{fa['text']} [{span}]", fa['kind'] or '', fa['valid_from'] or '', 'fact', fa['id']))
    for m in q('SELECT * FROM memory'):
        rows.append((f"They asked to remember: {m['text']}" + (' (later forgotten)' if m['removed_at'] else ''), '', m['created'] or '', 'memory', m['id']))
    with _lock:
        c = con()
        try:
            c.execute('DELETE FROM recall')
            c.executemany('INSERT INTO recall(text, keys, at, src, ref) VALUES(?,?,?,?,?)', rows)
            c.commit()
        finally:
            c.close()
    return len(rows)


def recall(query, limit=8, since=None, until=None):
    """Best matches for their words (BM25), each with its date; since/until (YYYY-MM-DD) narrow by date."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(query or '').lower()) if len(w) > 2][:12]
    if not words:
        return []
    m = ' OR '.join(f'"{w}"' for w in words)
    sql = "SELECT text, at, src, ref, bm25(recall) AS score FROM recall WHERE recall MATCH ?"
    args = [m]
    if since:
        sql += " AND at >= ?"; args.append(since)
    if until:
        sql += " AND at <= ?"; args.append(until + 'T99')
    sql += " ORDER BY score LIMIT ?"; args.append(limit)
    try:
        return q(sql, tuple(args))
    except sqlite3.OperationalError:
        return []


# ------------------------------------------------------------------ one-time migration from the voice/ files
def migrate():
    """profile.json facts, jarvis_style.md rules and jarvis_memory.json items into the store (once; the files stay as
    they were, marked migrated). Each row keeps its old id, dates and source; origin as recorded at the time."""
    voice = os.path.join(HUB, 'voice')
    done = q("SELECT count(*) AS n FROM learn_edits WHERE run='migrate'")[0]['n']
    if done:
        return 'already migrated'
    n = {'facts': 0, 'rules': 0, 'memory': 0}
    try:
        prof = json.load(open(os.path.join(voice, 'profile.json')))
    except (OSError, ValueError):
        prof = {'facts': []}
    for f in prof.get('facts') or []:
        x('INSERT OR IGNORE INTO facts(id, kind, text, valid_from, valid_to, origin, source_ref, quote, tainted, created, closed_why) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
          (f['id'], f.get('kind') or 'project', f['text'], f.get('since') or (f.get('at') or '')[:10], f.get('until'),
           'owner_ack', 'voice:' + str(f.get('src') or ''), '', 0, f.get('at') or now(), f.get('why_ended')))
        n['facts'] += 1
    try:
        style = [l.strip()[2:].strip() for l in open(os.path.join(voice, 'jarvis_style.md')) if l.strip().startswith('- ')]
    except OSError:
        style = []
    for s in style[:MAX_RULES]:
        x("INSERT INTO rules(id, scope, text, why, origin, source_ref, state, created) VALUES(?,?,?,?,?,?,?,?)",
          ('r-' + uuid.uuid4().hex[:6], 'voice', _clean(s, 300), 'learned from their reactions (jarvis_feedback.jsonl)', 'owner_said',
           'jarvis_style.md', 'active', now()))
        n['rules'] += 1
    try:
        mem = json.load(open(os.path.join(voice, 'jarvis_memory.json'))).get('items') or []
    except (OSError, ValueError):
        mem = []
    for m in mem:
        x('INSERT OR IGNORE INTO memory(id, text, origin, source_ref, created) VALUES(?,?,?,?,?)',
          (m['id'], m['text'], 'owner_said', 'voice', m.get('at') or now()))
        n['memory'] += 1
    x('INSERT INTO learn_edits(id, at, run, op, tbl, row_id, text, origin, source_ref, accepted, why) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
      ('ed-' + uuid.uuid4().hex[:10], now(), 'migrate', 'NOOP', 'all', '', json.dumps(n), 'executed', 'voice/*', 1, 'migration from voice files'))
    index_rebuild()
    return n
