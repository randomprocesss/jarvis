"""One ledger is the truth (HARNESS.md §4, Phase 2, 5 Oct 2026): state/jarvis.db, SQLite in WAL mode.

What lives here (and only here):
  tasks        every worker run Jarvis starts: lane (asked | background | operator), origin, state, pid, session, log
  actions      every action that goes through the gateway: tier, origin, payload hash, idempotency key, receipt
  events       append-only feed: what happened, for triggers and the audit trail
  items        a mirror of the inbox (round-ups + Claude's work files + ticks), refreshed when those files change;
               the JSON files stay where the skills write them, the ledger is how code and the brain read them
  sources, catalog_events            what data exists anywhere they can reach, and what changed (spine/world/catalog.py)
  needs, commitments, goals, initiatives   the project brain's open questions and what Jarvis does about them
  cursors, mined                      watcher cursors; which transcripts/notes were already mined for needs
Every helper opens its own short connection (safe across the server's threads); writes retry on a busy database.
"""
from jarvis import paths
import datetime as dt, glob, hashlib, json, os, sqlite3, threading, time

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'jarvis.db')
_init_lock = threading.Lock()
_ready = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT, origin TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, at);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, rid TEXT, aid TEXT, title TEXT, kind TEXT, lane TEXT, origin TEXT,
  state TEXT, created TEXT, started TEXT, ended TEXT, pid INTEGER, session TEXT, log TEXT, exit INTEGER, ask_quote TEXT,
  initiative_id TEXT, heartbeat_at TEXT, attempts INTEGER DEFAULT 0, prompt TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state, lane);
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, at TEXT, type TEXT, tier TEXT, origin TEXT, payload TEXT, hash TEXT,
  idem TEXT, state TEXT, receipt TEXT, ended TEXT, why TEXT);
CREATE INDEX IF NOT EXISTS actions_idem ON actions(idem, at);
CREATE TABLE IF NOT EXISTS items(rid TEXT, aid TEXT, kind TEXT, src TEXT, who TEXT, title TEXT, created TEXT, work_status TEXT,
  has_work INTEGER, done INTEGER, seen INTEGER, needs_you INTEGER, updated TEXT, PRIMARY KEY(rid, aid));
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, system TEXT, kind TEXT, name TEXT, path TEXT, columns TEXT, rows INTEGER,
  bytes INTEGER, files INTEGER, newest TEXT, first_seen TEXT, last_seen TEXT, last_changed TEXT, fingerprint TEXT, local INTEGER,
  digest TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS catalog_events(id INTEGER PRIMARY KEY, at TEXT, source_id TEXT, kind TEXT, detail TEXT, matched INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS needs(id TEXT PRIMARY KEY, created TEXT, kind TEXT, statement TEXT, target TEXT, why TEXT, unblocks TEXT,
  assumed TEXT, recognizer TEXT, origin TEXT, tainted INTEGER DEFAULT 0, source_ref TEXT, quote TEXT, topic TEXT, state TEXT,
  goal_id TEXT, last_checked TEXT, candidates TEXT, dismissed_at TEXT, note TEXT, updated TEXT, scope TEXT);
CREATE TABLE IF NOT EXISTS commitments(id TEXT PRIMARY KEY, created TEXT, who TEXT, to_whom TEXT, what TEXT, due TEXT, state TEXT,
  origin TEXT, source_ref TEXT, quote TEXT, dismissed_at TEXT, updated TEXT, scope TEXT);
CREATE TABLE IF NOT EXISTS goals(id TEXT PRIMARY KEY, created TEXT, statement TEXT, source TEXT, state TEXT, success TEXT,
  review_at TEXT, dismissed_at TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS initiatives(id TEXT PRIMARY KEY, created TEXT, class TEXT, need_id TEXT, trigger TEXT, title TEXT,
  plan TEXT, origin_rank INTEGER, autonomy TEXT, state TEXT, task_id TEXT, result TEXT, decided_at TEXT, outcome_pending INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cursors(name TEXT PRIMARY KEY, value TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS mined(ref TEXT PRIMARY KEY, at TEXT, hash TEXT, n INTEGER);
"""


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def connect(path=None):
    p = path or DB
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=10, isolation_level=None)       # autocommit; explicit BEGIN where needed
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    if p not in _ready:
        with _init_lock:
            if p not in _ready:
                c.execute('PRAGMA journal_mode=WAL')
                c.executescript(SCHEMA)
                _ready.add(p)
    return c


def q(sql, args=(), path=None):
    """Rows as dicts."""
    c = connect(path)
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def x(sql, args=(), path=None):
    """One write; returns lastrowid. Retries a few times if the database is busy."""
    for i in range(5):
        c = connect(path)
        try:
            return c.execute(sql, args).lastrowid
        except sqlite3.OperationalError as e:
            if 'locked' not in str(e) or i == 4:
                raise
            time.sleep(0.2 * (i + 1))
        finally:
            c.close()


def upsert(table, row, key, path=None):
    """Insert or update by primary key (only the given columns change)."""
    cols = list(row)
    sets = ', '.join(f'{k}=excluded.{k}' for k in cols if k not in key)
    sql = (f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)}) "
           f"ON CONFLICT({', '.join(key)}) DO " + (f'UPDATE SET {sets}' if sets else 'NOTHING'))
    return x(sql, [row[k] if not isinstance(row[k], (dict, list)) else json.dumps(row[k], ensure_ascii=False) for k in cols], path)


def event(kind, ref='', origin='', data=None, path=None):
    """Append one event (never raises: the feed must not break the thing it records)."""
    try:
        return x('INSERT INTO events(at, kind, ref, origin, data) VALUES(?,?,?,?,?)',
                 (now(), kind, ref, origin, json.dumps(data, ensure_ascii=False, default=str) if data is not None else None), path)
    except Exception:
        return None


def cursor(name, value=None, path=None):
    """Read (value=None) or set a watcher cursor."""
    if value is None:
        r = q('SELECT value FROM cursors WHERE name=?', (name,), path)
        return r[0]['value'] if r else None
    x('INSERT INTO cursors(name, value, at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, at=excluded.at',
      (name, str(value), now()), path)
    return value


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ the inbox mirror
def _load(p, default):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def ingest_items(path=None):
    """Mirror round-ups (last 14 days) + work files + ticks into `items`. Cheap (a few hundred rows); called when the files
    change. Returns (rows, new_items): new_items = (rid, aid) seen for the first time (the background lane's trigger)."""
    st = _load(os.path.join(HUB, 'roundups', 'state.json'), {})
    work = {os.path.basename(f)[:-5]: _load(f, {}) for f in glob.glob(os.path.join(HUB, 'work', '20*.json'))}
    known = {(r['rid'], r['aid']) for r in q('SELECT rid, aid FROM items', (), path)}
    cut = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    rows, new = 0, []
    for f in sorted(glob.glob(os.path.join(HUB, 'roundups', '20*.json'))):
        rid = os.path.basename(f)[:-5]
        if rid[:10] < cut:
            continue
        d = _load(f, None)
        if not isinstance(d, dict):
            continue
        for a in d.get('actions') or []:
            aid = str(a.get('id') or '')
            if not aid:
                continue
            w = (work.get(rid) or {}).get(aid) or {}
            pts = a.get('points') or []
            ticks = st.get(rid) or {}
            done = bool(pts) and all(ticks.get(f'{aid}.{i}') for i in range(len(pts)))
            needs_you = any(p.get('state') in ('needs_you', 'decide') for p in w.get('points') or [] if isinstance(p, dict))
            upsert('items', {'rid': rid, 'aid': aid, 'kind': d.get('kind') or '', 'src': a.get('src') or d.get('kind') or '',
                             'who': str(a.get('who') or '')[:80], 'title': str(a.get('title') or a.get('text') or '')[:200],
                             'created': d.get('window_end') or rid[:10], 'work_status': str(w.get('status') or ''),
                             'has_work': int(bool(w)), 'done': int(done), 'seen': int(bool(ticks.get(f'{aid}.seen'))),
                             'needs_you': int(needs_you), 'updated': now()}, ('rid', 'aid'), path)
            rows += 1
            if (rid, aid) not in known:
                new.append((rid, aid))
    if not known:                       # the first ingest is the baseline: what already exists is not "new"
        new = []
    return rows, new
