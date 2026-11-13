"""The policy gate (autonomy plan, part D; build spec 2). Pure code, no model.

decide(action) says whether one outward action may run WITHOUT the owner's press, and names every check it failed. Today
(12 Nov 2026) nothing calls it to act: every class is at L1 or L2, so decide() refuses everything, and the only user is
would(), which records what the gate would have said for each reply Jarvis drafts. A level is raised only by their press in
the Jarvis page (set_level(by='page')), never above the ceiling they set on 12 Nov (tools/ladder.py: replies to leads L3,
customer L2, production L0), and to L3 only once the class has earned it (20 replies in a row sent as drafted). Code may
lower a level, never raise one.

Acting (build spec 2, pieces 4 to 7; built 12 Nov 2026 evening, SWITCHED OFF: no class is at L3, so tick() ends at once):
  hold()         a reply that passes every check is never sent at once: it waits HOLD_S (10 minutes) in
                 state/held_actions.json, signed with the server's page key, and they are told
  stop()         their stop always wins (also across a restart: the entry is on disk); it lowers the class one level
  release_due()  only the server's own timer calls it. It checks the signature, that the text is still the held text, and
                 decide() AGAIN at that moment (a lowered level, the kill switch, quiet hours); it claims the entry on disk
                 before sending, so two timers or a restart cannot send twice; the send is slack_send.start, which itself
                 refuses a second send of the same reply
  A worker cannot reach any of this: no HTTP route releases; hold and release refuse inside a worker process
  (JARVIS_PROC); a crafted queue entry has no valid signature; the send lane's own ticket check stays in force.

An action: {'kind': 'slack_reply' | 'jira_comment', 'rid', 'aid', 'to', 'where', 'channel_id', 'thread_ts', 'text'}.

The checks, all of which must hold for allow=True:
  kill            state/KILL is not set
  level           the class's level is L3 or more (and never above its ceiling)
  audience        the class is not customer or unknown, whatever the level table says
  proof           every number in the reply is tied to an output or a receipt, and no scope gap is open (tools/proof.py)
  review          the item's independent review says ok (a reply with numbers and no review is refused)
  commitment      the text makes no promise or date ("I will", "by Friday", "tomorrow", "next week", a deadline)
  links           every link in the text is one of the item's own attachments (nothing copied from the incoming message)
  screens         no secret pattern and no listed personal word in the text
  first_contact   Jarvis has sent to this person or thread before (a first message is never automatic)
  rate            under 3 released an hour and 10 a day for the class
  quiet           not between 22:30 and 07:00
"""
from jarvis import paths
import datetime as dt, fcntl, hashlib, hmac, json, os, re, sqlite3, sys

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'jarvis.db')
KILL = os.path.join(HUB, 'state', 'KILL')
ORDER = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5']
RATE_HOUR, RATE_DAY = 3, 10
HELD = os.path.join(HUB, 'state', 'held_actions.json')
HOLD_S = 600                                             # the hold before a reply goes out by itself: 10 minutes
RELEASE_KINDS = ('slack_reply', 'jira_comment')          # what the release path can send (13 Nov: a Jira comment too, through the same worker his Post press uses)
RECHECK = (30 * 60, 24 * 3600)                           # a reply the ladder sent is read back after 30 minutes and after a day
COMMIT = re.compile(r"\b(i\s?(?:will|'ll)|we\s?(?:will|'ll)|will (?:send|share|do|get|have|finish|complete|deliver|fix|update|push|check)|"
                    r"by (?:mon|tue|wed|thu|fri|sat|sun)\w*|by (?:eod|end of (?:day|week|month)|tomorrow|tonight|today|next \w+|\d{1,2}(?:st|nd|rd|th)?\b)|"
                    r"tomorrow|tonight|next week|deadline|promise|commit(?:ted)? to|you(?:'ll| will) have)\b", re.I)
URL = re.compile(r'https?://[^\s>)\]]+')
SECRET = re.compile(r'(?i)(password|passwd|secret|api[_-]?key|token|bearer)\s*[:=]\s*\S{6,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abprs]-[A-Za-z0-9-]{10,}')


def own_projects():
    """The ticket projects that are the owner's own (settings: tracker.own_projects). Only a comment on one of their tickets
    can ever go out by itself; with none listed, no comment ever does."""
    from jarvis import config
    return tuple(p for p in (str(x).strip().upper() for x in config.load().get('tracker.own_projects') or []) if re.fullmatch(r'[A-Z][A-Z0-9]{1,9}', p))


def _own_ticket(key):
    own = own_projects()
    return bool(own) and bool(re.fullmatch(r'(?:%s)-\d+' % '|'.join(own), str(key or '')))


def _con(db=None, ro=False):
    c = sqlite3.connect(f'file:{db or DB}?mode=ro', uri=True, timeout=10) if ro else sqlite3.connect(db or DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c


def _ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS ladder (cls TEXT PRIMARY KEY, level TEXT, since TEXT, set_by TEXT, why TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS policy_log (at TEXT, rid TEXT, aid TEXT, cls TEXT, level TEXT, allow INTEGER, mode TEXT, failed TEXT)")


def _now():
    return dt.datetime.now().astimezone()


def ceiling(cls):
    from jarvis import ladder
    return ladder.CEILING.get(cls, 'L1')


def default_level(cls):
    from jarvis import ladder
    return next((c[2] for c in ladder.CLASSES if c[0] == cls), 'L1')


def level_of(cls, db=None):
    """The class's level: what they set, else today's default; never above its ceiling."""
    try:
        con = _con(db, ro=True)
        r = con.execute("SELECT level FROM ladder WHERE cls=?", (cls,)).fetchone()
        con.close()
        lvl = r['level'] if r else default_level(cls)
    except sqlite3.OperationalError:
        lvl = default_level(cls)
    if lvl not in ORDER:
        lvl = 'L1'
    return min(lvl, ceiling(cls), key=ORDER.index)


def set_level(cls, level, by, why='', db=None, eligible=None):
    """Raise: only by='page' (their press, checked by the gateway), one step at a time, never above the ceiling. Lower: anyone."""
    from jarvis import ladder
    if cls not in ladder.CEILING or level not in ORDER:
        return {'ok': False, 'why': 'unknown class or level'}
    cur = level_of(cls, db)
    up = ORDER.index(level) > ORDER.index(cur)
    if up and by != 'page':
        return {'ok': False, 'why': 'a level is raised only by their press in the Jarvis page'}
    if up and ORDER.index(level) > ORDER.index(ceiling(cls)):
        return {'ok': False, 'why': f'above the ceiling they set for this class ({ceiling(cls)})'}
    if up and ORDER.index(level) - ORDER.index(cur) > 1:
        return {'ok': False, 'why': 'one level at a time'}
    if up and ORDER.index(level) >= ORDER.index('L3'):
        ok, why2 = earned(cls) if eligible is None else (bool(eligible), 'not earned')
        if not ok:
            return {'ok': False, 'why': why2}
    con = _con(db)
    with con:
        _ensure(con)
        con.execute("INSERT OR REPLACE INTO ladder (cls, level, since, set_by, why) VALUES (?,?,?,?,?)", (cls, level, _now().isoformat(timespec='seconds'), by, why[:300]))
    con.close()
    return {'ok': True, 'cls': cls, 'level': level, 'was': cur}


def earned(cls):
    """(True, '') when the class has earned L3: its last 20 scored replies went out exactly as drafted (tools/ladder.py)."""
    try:
        from jarvis import ladder
        row = next((c for c in ladder.ladder().get('classes') or [] if c.get('cls') == cls), None)
    except Exception as e:
        return False, f'the evidence could not be read ({str(e)[:80]})'
    if not row:
        return False, 'no evidence for this kind of action'
    if not row.get('eligible'):
        return False, f"not earned yet: {row.get('run') or 0} of {row.get('need') or 20} in a row sent as drafted"
    return True, ''


def class_of(action):
    from jarvis import ladder
    kind = action.get('kind') or 'slack_reply'
    if kind == 'jira_comment':
        return 'jira_comment'
    if kind != 'slack_reply':
        return kind if kind in ladder.CEILING else 'slack_unknown'
    who, where = str(action.get('to') or '').strip(), str(action.get('where') or '').strip()
    return ladder.class_of(f"slack:{where if where.startswith('#') else (who or where or 'unknown')}", ladder.roles())


def _entry(action):
    try:
        return json.load(open(os.path.join(HUB, 'work', str(action.get('rid')) + '.json'))).get(action.get('aid')) or {}
    except (OSError, ValueError):
        return {}


def _sent_before(action, db=None):
    """Has a reply of Jarvis's gone to this conversation before? (from the work entries; the action's own item does not count)"""
    import glob
    cid, to = str(action.get('channel_id') or ''), str(action.get('to') or '')
    jk = str(action.get('jira_key') or '') if action.get('kind') == 'jira_comment' else ''
    for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        rid = os.path.basename(f)[:-5]
        for aid, w in d.items():
            r = w.get('reply') if isinstance(w, dict) and isinstance(w.get('reply'), dict) else None
            if not r or not r.get('sent_at') or (rid == action.get('rid') and aid == action.get('aid')):
                continue
            if jk:                                       # a Jira comment: an earlier comment of Jarvis's on the SAME ticket
                if r.get('channel') == 'jira' and jk in (r.get('jira_key'), r.get('issue'), r.get('where')):
                    return True
                continue
            if (cid and r.get('channel_id') == cid) or (not cid and to and r.get('to') == to):
                return True
    return False


def _released(cls, hours, db=None):
    try:
        con = _con(db, ro=True)
        cut = (_now() - dt.timedelta(hours=hours)).isoformat(timespec='seconds')
        n = con.execute("SELECT COUNT(*) FROM policy_log WHERE cls=? AND mode='release' AND allow=1 AND at >= ?", (cls, cut)).fetchone()[0]
        con.close()
        return n
    except sqlite3.OperationalError:
        return 0


def decide(action, db=None, now=None, w=None):
    """{'allow', 'cls', 'level', 'checks': [{'name', 'ok', 'why'}], 'failed': [names]}. Never raises: a check that cannot
    run counts as failed."""
    now = now or _now()
    cls = class_of(action)
    lvl = level_of(cls, db)
    text = str(action.get('text') or '')
    w = w if w is not None else _entry(action)
    checks = []

    def add(name, ok, why=''):
        checks.append({'name': name, 'ok': bool(ok), 'why': why})

    add('kill', not os.path.exists(KILL), 'the kill switch is set')
    add('level', ORDER.index(lvl) >= ORDER.index('L3'), f'{cls} is at {lvl}: your press is needed')
    add('audience', cls not in ('slack_customer', 'slack_unknown', 'production', 'uat_write', 'merge'), f'{cls} always needs your press')
    if action.get('kind') == 'jira_comment':
        jk = str(action.get('jira_key') or '')
        add('jira_project', _own_ticket(jk),
            f"{jk or 'no ticket key'}: only a comment on a {' or '.join(own_projects()) or 'listed project'} ticket can ever go out by itself")
    try:
        from jarvis import proof
        v = proof.verdict(str(action.get('rid')), str(action.get('aid')), w)
        bad = [u['raw'] for u in v.get('untied') or [] if u.get('where') == 'reply']
        add('proof', not bad and not v.get('scope'), ('numbers tied to no output: ' + ', '.join(bad[:6])) if bad else '; '.join((v.get('scope') or [])[:2]))
    except Exception as e:
        add('proof', False, f'the proof check could not run: {str(e)[:80]}')
    rv = (w.get('review') or {}).get('verdict') if isinstance(w.get('review'), dict) else None
    try:
        from jarvis import proof as _p
        has_numbers = bool(_p.claims(text))
    except Exception:
        has_numbers = True
    add('review', rv == 'ok' or (rv is None and not has_numbers), 'the independent review has not said ok' if rv != 'ok' else '')
    m = COMMIT.search(text)
    add('commitment', not m, f'the text makes a promise or names a date ("{m.group(0)}")' if m else '')
    own = {str(a.get('url') or a.get('path') or '') for a in ((w.get('reply') or {}).get('attachments') or []) if isinstance(a, dict)}
    own |= {str((w.get('reply') or {}).get(k) or '') for k in ('canvas_url', 'slack_link')}
    strangers = [u for u in URL.findall(text) if not any(u.rstrip('.,') in o or o in u for o in own if o)]
    add('links', not strangers, f'a link that is not one of the item\'s own files: {strangers[0][:60]}' if strangers else '')
    words = []
    try:
        wl = str(paths.state('personal_words.txt'))      # the owner's own list of words that never go out (theirs: data home)
        pat = [x.strip() for x in open(wl) if x.strip() and not x.startswith('#')]
        words = [x for x in pat if re.search(r'\b' + re.escape(x) + r'\b', text, re.I)]
    except OSError:
        pass
    add('screens', not SECRET.search(text) and not words, 'a secret pattern' if SECRET.search(text) else (f'a listed personal word ({words[0]})' if words else ''))
    add('first_contact', _sent_before(action, db), 'the first message to this person or thread is never automatic')
    h, d = _released(cls, 1, db), _released(cls, 24, db)
    add('rate', h < RATE_HOUR and d < RATE_DAY, f'{h} released this hour, {d} today (limits {RATE_HOUR} and {RATE_DAY})')
    t = now.time()
    add('quiet', not (t >= dt.time(22, 30) or t < dt.time(7, 0)), 'quiet hours (22:30 to 07:00)')
    failed = [c['name'] for c in checks if not c['ok']]
    return {'allow': not failed, 'cls': cls, 'level': lvl, 'checks': checks, 'failed': failed}


def would(rid, aid, db=None):
    """What the gate says about the reply Jarvis drafted for this item, recorded once per version of the text (mode
    'would'). This is the evidence for the ladder: how often a reply would have passed every check but the level."""
    w = _entry({'rid': rid, 'aid': aid})
    r = w.get('reply') if isinstance(w.get('reply'), dict) else {}
    if not str(r.get('text') or '').strip():
        return None
    a = action_for(rid, aid, w)
    d = decide(a, db, w=w)
    try:
        con = _con(db)
        with con:
            _ensure(con)
            failed = ','.join(d['failed'])
            last = con.execute("SELECT failed FROM policy_log WHERE rid=? AND aid=? AND mode='would' ORDER BY at DESC LIMIT 1", (rid, aid)).fetchone()
            if not last or last['failed'] != failed:
                con.execute("INSERT INTO policy_log (at, rid, aid, cls, level, allow, mode, failed) VALUES (?,?,?,?,?,?,?,?)",
                            (_now().isoformat(timespec='seconds'), rid, aid, d['cls'], d['level'], int(d['allow']), 'would', failed))
        con.close()
    except sqlite3.Error:
        pass
    return d


# ------------------------------------------------------------------ acting: hold, stop, release (build spec 2, pieces 4-7)
def _sha(x):
    return hashlib.sha256(str(x).encode('utf-8', 'replace')).hexdigest()


def _secret():
    """The server's page key: in memory only, from the app's Keychain. No key (a worker, a script, a server not started by
    the app) means nothing is ever held or released."""
    try:
        from jarvis.spine import gateway
        return gateway.PAGE_KEY
    except Exception:
        return None


def _sign(e, key):
    msg = '|'.join(str(e.get(k)) for k in ('id', 'rid', 'aid', 'cls', 'text_sha', 'send_at'))
    return hmac.new(str(key).encode(), msg.encode(), 'sha256').hexdigest()


class _Queue:
    """The hold queue under a file lock: load, change, save as one step (two timers, or a timer and their stop)."""
    def __enter__(self):
        os.makedirs(os.path.dirname(HELD), exist_ok=True)
        self.f = open(HELD + '.lock', 'w'); fcntl.flock(self.f, fcntl.LOCK_EX)
        try:
            self.rows = json.load(open(HELD))
        except (OSError, ValueError):
            self.rows = []
        return self

    def save(self):
        tmp = HELD + '.tmp'
        json.dump(self.rows[-400:], open(tmp, 'w'), indent=1, ensure_ascii=False); os.replace(tmp, HELD)

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()


def held(states=None):
    try:
        rows = json.load(open(HELD))
    except (OSError, ValueError):
        rows = []
    return [e for e in rows if not states or e.get('state') in states]


def action_for(rid, aid, w=None):
    w = w if w is not None else _entry({'rid': rid, 'aid': aid})
    r = w.get('reply') if isinstance(w.get('reply'), dict) else {}
    if not str(r.get('text') or '').strip():
        return None
    jira = r.get('channel') == 'jira'
    return {'kind': 'jira_comment' if jira else 'slack_reply', 'rid': rid, 'aid': aid, 'to': r.get('to'), 'where': r.get('where'),
            'channel_id': r.get('channel_id'), 'thread_ts': r.get('thread_ts'), 'text': r.get('text'),
            'jira_key': str(r.get('jira_key') or r.get('issue') or r.get('where') or '').strip() if jira else None}


def _log(rid, aid, d, mode, allow, db=None):
    try:
        con = _con(db)
        with con:
            _ensure(con)
            con.execute("INSERT INTO policy_log (at, rid, aid, cls, level, allow, mode, failed) VALUES (?,?,?,?,?,?,?,?)",
                        (_now().isoformat(timespec='seconds'), rid, aid, d.get('cls'), d.get('level'), int(bool(allow)), mode, ','.join(d.get('failed') or [])))
        con.close()
    except sqlite3.Error:
        pass


def _ledger(e, state, receipt, db=None):
    """The action ledger (jarvis.db actions): what went out by itself, at which level, after which checks, and what undo means."""
    try:
        con = _con(db)
        with con:
            con.execute("INSERT INTO actions(at, type, tier, origin, payload, hash, idem, state, receipt, ended, why) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (e.get('held_at'), 'ladder/' + str(e.get('kind') or 'slack_reply'), e.get('level'), 'ladder',
                         json.dumps({k: e.get(k) for k in ('rid', 'aid', 'cls', 'to', 'where', 'first_line', 'checks')}, ensure_ascii=False)[:4000],
                         e.get('text_sha'), e.get('id'), state, json.dumps(receipt, ensure_ascii=False, default=str)[:1000], _now().isoformat(timespec='seconds'),
                         'held 10 minutes and not stopped; undo: a correction message in the same thread'))
        con.close()
    except sqlite3.Error:
        pass


def acting_on(db=None):
    """The classes that may act (L3 or more). Empty on 12 Nov 2026: nothing does."""
    try:
        con = _con(db, ro=True)
        rows = [r['cls'] for r in con.execute("SELECT cls FROM ladder")]
        con.close()
    except sqlite3.OperationalError:
        rows = []
    return {c: level_of(c, db) for c in rows if ORDER.index(level_of(c, db)) >= ORDER.index('L3')}


def _since(cls, db=None):
    try:
        con = _con(db, ro=True)
        r = con.execute("SELECT since FROM ladder WHERE cls=?", (cls,)).fetchone()
        con.close()
        return r['since'] if r else ''
    except sqlite3.OperationalError:
        return ''


def demote(cls, why, db=None):
    lvl = level_of(cls, db)
    if ORDER.index(lvl) >= ORDER.index('L3'):
        return set_level(cls, ORDER[ORDER.index(lvl) - 1], 'code', why, db)
    return {'ok': True, 'cls': cls, 'level': lvl, 'was': lvl}


def hold(rid, aid, db=None, now=None, tell=None):
    """A reply that passes every check goes into the hold queue, never straight out. {'held': bool, 'entry' | 'failed'}."""
    key = _secret()
    if os.environ.get('JARVIS_PROC'):
        return {'held': False, 'failed': ['not_the_server']}
    if not key:
        return {'held': False, 'failed': ['no_page_key']}
    w = _entry({'rid': rid, 'aid': aid})
    a = action_for(rid, aid, w)
    if not a:
        return {'held': False, 'failed': ['no_reply']}
    if a['kind'] not in RELEASE_KINDS:
        return {'held': False, 'failed': ['no_release_path']}
    if (w.get('reply') or {}).get('sent_at') or w.get('replied_by_owner'):
        return {'held': False, 'failed': ['already_answered']}
    d = decide(a, db, now, w)
    if not d['allow']:
        return {'held': False, 'failed': d['failed'], 'cls': d['cls']}
    if str(w.get('updated') or '') <= _since(d['cls'], db):
        return {'held': False, 'failed': ['drafted_before_the_level_was_raised'], 'cls': d['cls']}
    t = now or _now()
    with _Queue() as q:
        tsha = _sha(a['text'])
        for e in q.rows:                                  # one decision per version of a text: a stopped text is never held again
            if e.get('rid') == rid and e.get('aid') == aid and e.get('text_sha') == tsha:
                return {'held': e.get('state') == 'held', 'entry': e, 'already': True}
        e = {'id': 'h-' + _sha(f'{rid}|{aid}|{tsha}')[:12], 'rid': rid, 'aid': aid, 'kind': a['kind'], 'cls': d['cls'], 'level': d['level'], 'to': a.get('to'), 'where': a.get('where'),
             'first_line': str(a['text']).strip().split('\n')[0][:160], 'text_sha': tsha, 'held_at': t.isoformat(timespec='seconds'),
             'send_at': (t + dt.timedelta(seconds=HOLD_S)).isoformat(timespec='seconds'), 'state': 'held', 'checks': [c['name'] for c in d['checks']]}
        e['sig'] = _sign(e, key)
        q.rows.append(e); q.save()
    _log(rid, aid, d, 'hold', True, db)
    if tell:
        try:
            tell(e)
        except Exception:
            pass
    return {'held': True, 'entry': e}


def stop(hid, by='owner', db=None):
    """Their stop always wins. The entry stays on file as stopped, and the class drops one level."""
    with _Queue() as q:
        e = next((x for x in q.rows if x.get('id') == hid), None)
        if not e:
            return {'ok': False, 'why': 'no such held reply'}
        if e.get('state') != 'held':
            return {'ok': False, 'why': f"it is already {e.get('state')}"}
        e.update(state='stopped', stopped_at=_now().isoformat(timespec='seconds'), stopped_by=by)
        q.save()
    return {'ok': True, 'entry': e, 'level': demote(e['cls'], f'a held reply was stopped by {by}', db)}


def release_due(send=None, db=None, now=None):
    """The server's timer. Sends each held reply whose time has come, if it still passes everything. Returns what happened."""
    key = _secret()
    if os.environ.get('JARVIS_PROC') or not key:
        return []
    t = (now or _now())
    iso = t.isoformat(timespec='seconds')
    claimed, out, drop = [], [], []
    with _Queue() as q:
        for e in q.rows:
            if e.get('state') == 'releasing' and str(e.get('released_at') or '') < (t - dt.timedelta(minutes=5)).isoformat(timespec='seconds'):
                e.update(state='unclear', why='the send was started and its end was never recorded (a restart?); not retried')
                out.append((e['id'], 'unclear'))
            if e.get('state') == 'queued':                # a Jira comment handed to the worker: posted, or never recorded as posted
                if (_entry({'rid': e['rid'], 'aid': e['aid']}).get('reply') or {}).get('sent_at'):
                    e.update(state='sent', why=''); out.append((e['id'], 'sent'))
                elif str(e.get('released_at') or '') < (t - dt.timedelta(minutes=30)).isoformat(timespec='seconds'):
                    e.update(state='unclear', why='handed to the Jira worker and not recorded as posted within 30 minutes; not retried'); out.append((e['id'], 'unclear'))
                continue
            if e.get('state') != 'held' or str(e.get('send_at') or '9') > iso:
                continue
            if not hmac.compare_digest(str(e.get('sig') or ''), _sign(e, key)):
                e.update(state='refused', why='not put in the queue by this server (no valid signature)'); out.append((e['id'], 'refused')); continue
            if os.path.exists(KILL):
                continue                                  # the kill switch: it waits, it is not dropped
            w = _entry({'rid': e['rid'], 'aid': e['aid']})
            a = action_for(e['rid'], e['aid'], w)
            if not a or (w.get('reply') or {}).get('sent_at') or w.get('replied_by_owner'):
                e.update(state='gone', why='already sent or answered, or the reply is gone'); out.append((e['id'], 'gone')); continue
            if _sha(a['text']) != e.get('text_sha'):
                e.update(state='cancelled', why='the reply was changed after it was held'); out.append((e['id'], 'cancelled')); drop.append(e['cls']); continue
            d = decide(a, db, t, w)
            if not d['allow']:
                e.update(state='refused', why='at send time: ' + ', '.join(d['failed'])); out.append((e['id'], 'refused')); _log(e['rid'], e['aid'], d, 'release', False, db); continue
            e.update(state='releasing', released_at=iso)  # claimed on disk BEFORE the send: a second timer or a restart finds it claimed
            claimed.append((dict(e), a, d))
        q.save()
    for cls in drop:
        demote(cls, 'a held reply was edited before it went out', db)
    for e, a, d in claimed:
        try:
            res = (send or _send)(e['rid'], e['aid'], a['text'])
        except Exception as ex:
            res = {'ok': False, 'error': f'{ex.__class__.__name__}: {ex}'[:200]}
        ok = bool(isinstance(res, dict) and res.get('ok'))
        with _Queue() as q:
            x = next((y for y in q.rows if y.get('id') == e['id']), None)
            if x:
                x.update(state=('queued' if res.get('state') == 'queued' else 'sent') if ok else 'failed',
                         why='' if ok else str((res or {}).get('error') or 'the send did not start')[:200])
                q.save()
        _log(e['rid'], e['aid'], d, 'release', ok, db)
        _ledger(e, 'done' if ok else 'failed', res, db)
        out.append((e['id'], 'sent' if ok else 'failed'))
    return out


def _send(rid, aid, text):
    r = _entry({'rid': rid, 'aid': aid}).get('reply') or {}
    if r.get('channel') == 'jira':                       # the same worker his Post press uses (the Atlassian connector); recorded as the ladder's
        key = str(r.get('jira_key') or r.get('issue') or r.get('where') or '').strip()
        if not _own_ticket(key):
            return {'ok': False, 'error': f"no {' or '.join(own_projects()) or 'listed project'} ticket on the reply"}
        from jarvis import actions
        q = actions.queue_jira_comment(key, text, rid, aid, by='ladder')
        return {'ok': bool(q.get('ok')), 'state': 'queued', 'approval': q.get('id'), 'error': q.get('error')}
    from jarvis import slack_send
    return slack_send.start(rid, aid, text, by='ladder')


def _read_back(e, w):
    """The message as it stands in Slack now: its text; '' when it is gone; None when Slack could not be read in full."""
    r = w.get('reply') or {}
    ts, ch, thr = str(r.get('sent_ts') or ''), r.get('channel_id'), str(r.get('thread_ts') or '')
    if not ts or not ch:
        return None
    from jarvis import slack_send
    thr = thr if re.fullmatch(r'\d+\.\d+', thr) else ''
    msgs, complete = slack_send.read(ch, thr, oldest='' if thr else f'{float(ts) - 2:.6f}')
    m = next((x for x in msgs if x['ts'] == ts), None)
    return m['text'] if m else ('' if complete else None)


def _same_words(a, b):
    from jarvis import slack_send
    return slack_send.plain(a) == slack_send.plain(b)


def _recheck_due(e, t):
    if e.get('state') != 'sent' or e.get('kind', 'slack_reply') != 'slack_reply' or e.get('after') in ('edited', 'removed'):
        return False
    n = int(e.get('rechecks') or 0)
    try:
        return n < len(RECHECK) and t >= dt.datetime.fromisoformat(e['released_at']) + dt.timedelta(seconds=RECHECK[n])
    except (KeyError, TypeError, ValueError):
        return False


def recheck_sent(db=None, now=None, read=None):
    """13 Nov (the last piece of spec 2): a Slack reply the ladder sent is read back twice, after 30 minutes and after a
    day. If they changed it or took it down, the reply was not right, and the class drops one level. Three failed reads of
    one check are recorded as unread and nothing is lowered on a guess. (A Jira comment is not read back: Jarvis has no
    way to read Jira from the server.)"""
    if os.environ.get('JARVIS_PROC') or not _secret():
        return []
    t = now or _now()
    todo = [dict(e) for e in held(('sent',)) if _recheck_due(e, t)]
    out = []
    for e in todo:
        w = _entry({'rid': e['rid'], 'aid': e['aid']})
        r = w.get('reply') or {}
        sent = str(r.get('sent_full') or (r.get('send') or {}).get('text') or '')
        try:
            cur = (read or _read_back)(e, w) if sent else None
        except Exception:
            cur = None
        with _Queue() as q:
            x = next((y for y in q.rows if y.get('id') == e['id']), None)
            if not x:
                continue
            if cur is None:
                x['recheck_tries'] = int(x.get('recheck_tries') or 0) + 1
                if x['recheck_tries'] >= 3:
                    x.update(rechecks=int(x.get('rechecks') or 0) + 1, recheck_tries=0, after=x.get('after') or 'unread')
                    out.append((e['id'], 'unread'))
            else:
                after = 'same' if _same_words(sent, cur) else ('removed' if not str(cur).strip() else 'edited')
                x.update(rechecks=int(x.get('rechecks') or 0) + 1, recheck_tries=0, after=after, rechecked_at=t.isoformat(timespec='seconds'))
                out.append((e['id'], after))
            q.save()
        if out and out[-1] == (e['id'], 'edited') or out and out[-1] == (e['id'], 'removed'):
            demote(e['cls'], f"a reply that went out by itself was {out[-1][1]} afterwards", db)
    return out


def _candidates(days=3):
    import glob
    cut = (_now() - dt.timedelta(days=days)).isoformat(timespec='seconds')
    quiet = (_now() - dt.timedelta(seconds=120)).isoformat(timespec='seconds')
    for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for aid, w in d.items():
            r = w.get('reply') if isinstance(w, dict) and isinstance(w.get('reply'), dict) else None
            if not r or not str(r.get('text') or '').strip() or r.get('sent_at') or w.get('replied_by_owner') or r.get('dropped') or w.get('merged_into'):
                continue
            if not (cut <= str(w.get('updated') or '') <= quiet):      # drafted in the last 3 days, and the worker has been done for 2 minutes
                continue
            yield os.path.basename(f)[:-5], aid, w


def tick(db=None, tell=None, send=None):
    """The server's 30-second job. With no class at L3 and nothing held (12 Nov 2026) it returns 'off' at once."""
    if os.environ.get('JARVIS_PROC'):
        return 'not the server'
    on = acting_on(db)
    t = _now()
    if not on and not held(('held', 'releasing', 'queued')) and not any(_recheck_due(e, t) for e in held(('sent',))):
        return 'off'
    n = 0
    if on and not os.path.exists(KILL):
        for rid, aid, w in _candidates():
            a = action_for(rid, aid, w)
            if a and a['kind'] in RELEASE_KINDS and class_of(a) in on:
                r = hold(rid, aid, db, tell=tell)
                n += 1 if r.get('held') and not r.get('already') else 0
    return {'held': n, 'released': release_due(send, db), 'rechecked': recheck_sent(db)}
