"""The governor (HARNESS.md §6.5, Phase 4, 5 Oct 2026): spending that keeps the owner's own work first. Measured, not guessed.

What it knows:
  window       Claude's own numbers. Every rate_limit_event a Jarvis Claude process prints (status, 5-hour and 7-day
               utilization, reset times) is noted here (note_window). With nothing fresh and a decision waiting, one tiny probe.
  usage        every Claude Code transcript on this Mac (~/.claude/projects, subagents included), scanned incrementally:
               weighted tokens per day and per session. weighted = input + 1.25 x cache writes + 0.1 x cache reads + 5 x output,
               times a model factor (Opus 1.67, Sonnet 1, Haiku 0.33). A price-shaped measure, not the plan's own meter.
  typical day  median weighted tokens over the active days of the last 14 (today excluded).
  estimate     per task class (profile or kind, plus origin): median and p90 of finished tasks; defaults until 10 samples.

Rules (admit / rail):
  asked        never held by the governor. At or above their 10% rail (p90 >= 10% of a typical day) Jarvis warns first (rail).
  inbox        background inbox work waits only when the 5-hour window is nearly used (>= 85%) or Claude says allowed_warning.
  initiatives  in their work hours (settings; weekdays 09-19 unless changed) at most 10% of a typical day per day, and the 5-hour window under 60%.
               Outside work hours: while the week keeps its reserve (7-day under 70%). Always when the 5-hour window resets
               within 45 min with room left (under 60%): unused room is lost at the reset.
  learning     (memory consolidation) at most 5% of a typical day per day.
  limited      rejected: nothing starts (spine/limits.py pauses the queue already).
"""
from jarvis import paths
import datetime as dt, glob, json, os, re, statistics, subprocess, threading, time

HUB = str(paths.home())
DIR = os.path.join(HUB, 'state', 'governor')
WINDOW = os.path.join(DIR, 'window.json')
SCAN = os.path.join(DIR, 'scan.json')
PROJECTS = str(paths.claude_chats())
RAIL = 0.10                      # his rule: warn at 10% of a day's usage
CAPS = {'initiative': 0.10, 'learning': 0.05, 'watchers': 0.15}


def own_work_held():
    """(False, week): Jarvis's own work has no extra weekly hold (The owner, 5 Oct: "just the development should stop, jarvis
    should keep working properly"); the usual rules in admit() apply."""
    return False, window().get('seven_day')
DEFAULT_EST = {'item': (1_500_000, 4_000_000), 'initiative': (1_200_000, 3_000_000), 'run-roundup': (800_000, 2_000_000),
               'run-work': (3_000_000, 8_000_000), 'review-fix': (800_000, 2_000_000), 'approved-actions': (400_000, 1_200_000)}
MODEL_W = (('opus', 1.67), ('fable', 1.67), ('sonnet', 1.0), ('haiku', 0.33))
_lock = threading.Lock()
_scan_lock = threading.Lock()


def _now():
    return time.time()


def _load(p, d):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def _save(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(o, open(p + '.tmp', 'w'))
    os.replace(p + '.tmp', p)


# ------------------------------------------------------------------ the window (Claude's own meter)
def note_window(info):
    """A rate_limit_event's rate_limit_info, from any Jarvis Claude process."""
    if not isinstance(info, dict):
        return
    w = info.get('unifiedWindows') or {}
    rec = {'at': _now(), 'status': info.get('status'), 'type': info.get('rateLimitType'),
           'five_hour': (w.get('five_hour') or {}).get('utilization'), 'five_hour_reset': (w.get('five_hour') or {}).get('resetsAt') or info.get('resetsAt'),
           'seven_day': (w.get('seven_day') or {}).get('utilization'), 'seven_day_reset': (w.get('seven_day') or {}).get('resetsAt')}
    with _lock:
        _save(WINDOW, rec)
        hist = os.path.join(DIR, 'window_history.ndjson')
        with open(hist, 'a') as f:
            f.write(json.dumps(rec) + '\n')


def probe():
    """One tiny Claude call to read the meter (a few hundred tokens). Only when a decision waits and nothing is fresh."""
    try:
        from jarvis import config
        p = subprocess.run([paths.claude_bin() or 'claude', '-p', 'reply with just: ok', '--model', config.load().get('engine.quick_model'),
                            '--output-format', 'stream-json',
                            '--verbose', '--strict-mcp-config', '--tools', ''], capture_output=True, text=True, timeout=60,
                           cwd=HUB, stdin=subprocess.DEVNULL, env=dict(os.environ, JARVIS_PROC='1', JARVIS_LANE='governor'))
        for line in p.stdout.splitlines():
            if '"rate_limit_event"' in line:
                note_window(json.loads(line).get('rate_limit_info'))
                return True
    except Exception:
        pass
    return False


def window(max_age=1200, may_probe=False, sync=False):
    """sync (24 Oct): probe now and wait for it. A caller that gates spending on the line uses it: the threaded probe died
    with every short-lived process that started it, and its throttle then blocked the next probes, so the week read 40%
    for 20 minutes while it was at 48%."""
    w = _load(WINDOW, {})
    if sync and (not w or _now() - w.get('at', 0) > max_age):
        probe()
        w = _load(WINDOW, {})
    elif (not w or _now() - w.get('at', 0) > max_age) and may_probe:
        last = _load(os.path.join(DIR, 'probe.json'), {}).get('at', 0)
        if _now() - last > 900:                       # at most one probe per 15 min, off the caller's path (the next
            _save(os.path.join(DIR, 'probe.json'), {'at': _now()})     # dispatch tick sees its answer)
            threading.Thread(target=probe, daemon=True, name='governor-probe').start()
    if w:
        for k in ('five_hour_reset', 'seven_day_reset'):          # a window that has reset since: its utilization is 0
            if w.get(k) and _now() > w[k]:
                w[k.replace('_reset', '')] = 0.0
        w['age_s'] = round(_now() - w.get('at', 0))
    return w


# ------------------------------------------------------------------ usage from transcripts
def _weight(u, model):
    f = next((x for k, x in MODEL_W if k in str(model or '').lower()), 1.0)
    return f * ((u.get('input_tokens') or 0) + 1.25 * (u.get('cache_creation_input_tokens') or 0)
                + 0.1 * (u.get('cache_read_input_tokens') or 0) + 5 * (u.get('output_tokens') or 0))


def _local_day(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00')).astimezone().date().isoformat()
    except ValueError:
        return None


def scan(days=15):
    """Incremental: new bytes of every transcript touched in the last `days` days. Returns the scan state."""
    with _scan_lock:
        st = _load(SCAN, {'files': {}})
        files = st['files']
        cut = _now() - days * 86400
        chats = glob.glob(os.path.join(PROJECTS, '*', '*.jsonl')) + glob.glob(os.path.join(PROJECTS, '*', '*', 'subagents', '*.jsonl'))
        for p in chats:
            try:
                size, mt = os.path.getsize(p), os.path.getmtime(p)
            except OSError:
                continue
            if mt < cut:
                continue
            rec = files.get(p) or {'off': 0, 'days': {}, 'w': 0.0, 'ids': []}
            if size < rec['off']:                               # rewritten: start over
                rec = {'off': 0, 'days': {}, 'w': 0.0, 'ids': []}
            if size == rec['off']:
                files[p] = rec
                continue
            seen = set(rec.get('ids') or [])
            with open(p, 'rb') as f:
                f.seek(rec['off'])
                data = f.read()
            end = data.rfind(b'\n') + 1                         # only whole lines; a half-written last line waits
            for raw in data[:end].split(b'\n'):
                if b'"usage"' not in raw or b'"assistant"' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                m = d.get('message') or {}
                mid = m.get('id') or d.get('requestId')
                if not isinstance(m.get('usage'), dict) or (mid and mid in seen):
                    continue
                if mid:
                    seen.add(mid)
                w = _weight(m['usage'], m.get('model'))
                day = _local_day(d.get('timestamp'))
                if day:
                    rec['days'][day] = rec['days'].get(day, 0.0) + w
                rec['w'] += w
            rec['off'] += end
            rec['ids'] = list(seen)[-200:]
            rec['project'] = os.path.basename(os.path.dirname(p)) if '/subagents/' not in p else os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(p))))
            rec['session'] = os.path.basename(p)[:-6] if '/subagents/' not in p else os.path.basename(os.path.dirname(os.path.dirname(p)))
            files[p] = rec
        for p in [p for p, r in files.items() if not os.path.exists(p)]:
            files.pop(p)
        st['at'] = _now()
        _save(SCAN, st)
        return st


def day_totals(st=None):
    st = st or _load(SCAN, {'files': {}})
    out = {}
    for r in st['files'].values():
        for d, w in (r.get('days') or {}).items():
            out[d] = out.get(d, 0.0) + w
    return out


def session_totals(st=None):
    st = st or _load(SCAN, {'files': {}})
    out = {}
    for r in st['files'].values():
        s = r.get('session')
        if s:
            out[s] = out.get(s, 0.0) + r.get('w', 0.0)
    return out


def session_day(session, day, st=None):
    st = st or _load(SCAN, {'files': {}})
    return sum((r.get('days') or {}).get(day, 0.0) for r in st['files'].values() if r.get('session') == session)


def typical_day(st=None):
    """Median weighted tokens over active days (>= 5% of the busiest) in the last 14, today excluded."""
    today = dt.date.today().isoformat()
    cut = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    vals = [w for d, w in day_totals(st).items() if cut <= d < today]
    if not vals:
        return None
    top = max(vals)
    act = [v for v in vals if v >= 0.05 * top]
    return statistics.median(act) if act else None


# ------------------------------------------------------------------ tasks
def is_trial(t):
    """A step of a research trial (world/research.py): the task of an investigation whose origin is 'research'. The task
    keeps kind 'investigation' so the worker's stop checks and the activity lists treat it as before."""
    if t.get('kind') == 'research_trial':
        return True
    if t.get('kind') != 'investigation' or not t.get('id'):
        return False
    try:
        from jarvis.spine import ledger
        return bool(ledger.q("SELECT 1 FROM investigations WHERE task_id=? AND origin='research'", (t['id'],)))
    except Exception:
        return False


def purpose(t):
    if t.get('lane') in ('asked', 'operator'):
        return 'asked'
    if is_trial(t):
        return 'inbox'                               # 13 Nov: a trial of outside research is work he asked for ("automatically", "try everything")
    if t.get('origin') in ('initiative', 'autopilot') or t.get('kind') in ('initiative', 'self-improve'):
        return 'initiative'                          # Jarvis's own work (its checks, and improving itself) share one budget
    if t.get('kind') == 'run-roundup':
        return 'watchers'
    return 'inbox'


def task_class(t):
    from jarvis.spine import ledger
    prof = ''
    if t.get('rid'):
        ev = ledger.q("SELECT data FROM events WHERE kind='routed' AND ref=? ORDER BY at DESC LIMIT 1", (f"{t['rid']}|{t['aid']}",))
        if ev:
            try:
                prof = '+'.join(json.loads(ev[0]['data']).get('profiles') or [])
            except (ValueError, TypeError):
                prof = ''
    return f"{t.get('kind') or 'item'}:{prof or 'generic'}:{purpose(t)}"


def estimate(t, st=None):
    """(median, p90, n) weighted tokens for a task like t, from finished tasks of its class (then its kind)."""
    from jarvis.spine import ledger
    tot = session_totals(st)
    done = ledger.q("SELECT * FROM tasks WHERE state='done' AND session IS NOT NULL AND session != ''")
    cls = task_class(t)
    def stats(rows):
        v = sorted(tot[r['session']] for r in rows if tot.get(r['session'], 0) > 0)
        if len(v) < 10:
            return None
        return statistics.median(v), v[min(len(v) - 1, int(0.9 * len(v)))], len(v)
    for rows in ([r for r in done if task_class(r) == cls], [r for r in done if (r.get('kind') or 'item') == (t.get('kind') or 'item')]):
        s = stats(rows)
        if s:
            return s
    m, p = DEFAULT_EST.get(t.get('kind') or 'item', DEFAULT_EST['item'])
    return m, p, 0


def spent_today(kind, st=None):
    """Weighted tokens spent today by tasks of one purpose (initiative, watchers, inbox) or by consolidation (learning)."""
    from jarvis.spine import ledger
    st = st or _load(SCAN, {'files': {}})
    today = dt.date.today().isoformat()
    if kind == 'learning':
        return sum(_load(os.path.join(DIR, 'learning.json'), {}).get(today, {}).values()) if os.path.exists(os.path.join(DIR, 'learning.json')) else 0.0
    rows = ledger.q("SELECT * FROM tasks WHERE (created >= ? OR started >= ?) AND session IS NOT NULL", (today, today))
    return sum(session_day(r['session'], today, st) for r in rows if purpose(r) == kind)


def note_learning(cost_w):
    """Consolidation runs are one-shots without a ledger task: they report their own weighted tokens here."""
    today = dt.date.today().isoformat()
    with _lock:
        d = _load(os.path.join(DIR, 'learning.json'), {})
        day = d.setdefault(today, {})
        day[str(int(_now()))] = cost_w
        _save(os.path.join(DIR, 'learning.json'), {k: v for k, v in d.items() if k >= (dt.date.today() - dt.timedelta(days=14)).isoformat()})


def work_hours(now=None):
    """Inside the owner's usual working days and hours (settings: work.days, work.hours)."""
    from jarvis import config
    return config.load().in_work_hours(now)


def admit(t):
    """(ok, why) for a background task (inbox work or an initiative). Asked work never comes here."""
    p = purpose(t)
    if p == 'asked':
        return True, ''
    w = window(may_probe=True)
    st = _load(SCAN, {'files': {}})
    five, week = w.get('five_hour'), w.get('seven_day')
    if w.get('status') == 'rejected':
        return False, 'Claude usage limit'
    if p in ('inbox', 'watchers'):
        if five is not None and five >= 0.85:
            return False, f'5-hour window {five:.0%} used: inbox work waits for room'
        if w.get('status') == 'allowed_warning':
            return False, 'Claude warns the usage window is nearly used: inbox work waits for room'
        if p == 'watchers':
            typ = typical_day(st)
            if typ and spent_today('watchers', st) >= CAPS['watchers'] * typ:
                return False, 'watchers used their 15% of a day'
        return True, ''
    # initiatives (and the autopilot)
    reset_soon = bool(w.get('five_hour_reset')) and 0 < w['five_hour_reset'] - _now() < 45 * 60
    if five is not None and five < 0.60 and reset_soon and (week is None or week < 0.85):
        return True, 'window resets soon with room left'
    if five is not None and five >= 0.60:
        return False, f'5-hour window {five:.0%} used: checks wait'
    if work_hours():
        typ = typical_day(st)
        est = estimate(t, st)[0]
        if typ and spent_today('initiative', st) + est > CAPS['initiative'] * typ:
            return False, "today's 10% for Jarvis's own checks is used"
        return True, ''
    if week is not None and week >= 0.70:
        return False, f'7-day window {week:.0%} used: keeping the weekday reserve'
    return True, ''


def rail(t):
    """None, or the warning for an asked task whose p90 estimate is at or above 10% of a typical day."""
    st = _load(SCAN, {'files': {}})
    typ = typical_day(st)
    if not typ:
        return None
    med, p90, n = estimate(t, st)
    share = p90 / typ
    if share < RAIL:
        return None
    return {'share': round(share, 3), 'p90': int(p90), 'median': int(med), 'samples': n,
            'say': f"Heads up: this could take about {round(100 * med / typ)}%, up to {round(100 * share)}%, of a normal day's Claude usage."}


def status():
    st = _load(SCAN, {'files': {}})
    typ = typical_day(st)
    w = window()
    today = dt.date.today().isoformat()
    tot = day_totals(st)
    return {'window': {k: w.get(k) for k in ('status', 'five_hour', 'seven_day', 'age_s')} if w else None,
            'five_hour_resets': dt.datetime.fromtimestamp(w['five_hour_reset']).isoformat(timespec='minutes') if w.get('five_hour_reset') else None,
            'typical_day_w': int(typ) if typ else None, 'today_w': int(tot.get(today, 0)),
            'today_share': round(tot.get(today, 0) / typ, 2) if typ else None,
            'spent_today': {k: int(spent_today(k, st)) for k in ('inbox', 'initiative', 'watchers', 'learning')},
            'scan_age_s': round(_now() - st.get('at', 0)) if st.get('at') else None}


def loop():
    """Background: rescan transcripts every 10 min (incremental, a second or two)."""
    from jarvis.spine import health
    while True:
        try:
            scan()
            health.beat('governor')
        except Exception as e:
            print('governor scan:', e, flush=True)
        time.sleep(600)
