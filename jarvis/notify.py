"""Pings to the owner. On a Mac, Jarvis adds a reminder, due at once, to a "Jarvis" list in the Reminders app; iCloud shows it
on their iPhone. No phone number, no third-party service, nothing but a short title and line. On Linux it is a desktop
notification (notify-send) when that is installed. Where neither exists, nothing is sent and the log says so.

What pings (checked every minute by the server; never in the quiet hours of settings, work.quiet; settings notify.enabled
switches it off):
  - an approval waiting for them that was not there before;
  - a long job (tools/run_job.py) that finished or failed;
  - a decision or question a routine or worker left for them (a `decide`/`needs_you` point), at once, once per question;
  - more things needing them than at the last check, at most once an hour;
  - Jarvis's own login gone (a logged-out `claude` makes every background run fail silently): checked every 10 minutes
    with `claude auth status`; one ping per outage, held until the quiet hours end; one more when the login is back. The
    state is also in state/claude_cli_login.json for the page.
State: notify_state.json in the data home (what was already pinged), so a restart never repeats a ping.
"""
from jarvis import config, paths
import datetime as dt, json, os, shutil, subprocess, sys, threading, time

HUB = str(paths.home())
STATE = os.path.join(HUB, 'notify_state.json')
LIST = 'Jarvis'


def _load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def _save(p, o):
    tmp = p + '.tmp'
    json.dump(o, open(tmp, 'w'), indent=1)
    os.replace(tmp, p)


def _as(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def ping(title, body=''):
    """One ping, the way this computer can. Returns (ok, error)."""
    if sys.platform == 'darwin':
        return _ping_reminders(title, body)
    if shutil.which('notify-send'):
        try:
            p = subprocess.run(['notify-send', '--app-name', 'Jarvis', title[:120], body[:400]], capture_output=True, text=True, timeout=20)
            return p.returncode == 0, (p.stderr or '').strip()[:200]
        except Exception as e:
            return False, str(e)[:200]
    return False, 'no way to ping on this computer'


def _ping_reminders(title, body=''):
    """One reminder, due now, in the Jarvis list (made if missing). Returns (ok, error)."""
    script = f'''
tell application "Reminders"
  if not (exists list {_as(LIST)}) then make new list with properties {{name:{_as(LIST)}}}
  make new reminder at end of list {_as(LIST)} with properties {{name:{_as(title[:120])}, body:{_as(body[:400])}, remind me date:((current date) + 5)}}
end tell'''
    try:
        p = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=20)
        return p.returncode == 0, (p.stderr or '').strip()[:200]
    except Exception as e:
        return False, str(e)[:200]


def _settings():
    c = config.load()
    return {'enabled': c.get('notify.enabled') is not False, 'quiet': str(c.get('work.quiet') or '22:30-07:00')}


def _quiet(cfg, now=None):
    now = (now or dt.datetime.now()).time()
    start, _, end = cfg['quiet'].partition('-')
    a, b = dt.time.fromisoformat(start), dt.time.fromisoformat(end)
    return (now >= a or now < b) if a > b else (a <= now < b)


def check(needs_count=None, log=print):
    cfg = _settings()
    if not cfg['enabled']:
        return
    st = _load(STATE, {'approvals': [], 'jobs': [], 'needs': 0, 'needs_at': ''})
    seed_asks = 'asks' not in st            # first check with this feature: what is already open is not news
    st.setdefault('asks', [])
    first = not os.path.exists(STATE)
    out = []
    # approvals waiting for him
    ap = _load(os.path.join(HUB, 'approvals.json'), {'items': []}).get('items', [])
    for a in ap:
        if a.get('status') == 'pending' and a.get('id') not in st['approvals']:
            st['approvals'].append(a['id'])
            out.append(('Jarvis: approval needed', a.get('title') or 'A request waits for your OK'))
    # long jobs that ended
    outs = os.path.join(HUB, 'work', 'outputs')
    for root, _, files in os.walk(outs):
        if 'job.json' not in files:
            continue
        j = _load(os.path.join(root, 'job.json'), {})
        if j.get('state') in ('done', 'failed') and j.get('ended'):
            key = os.path.relpath(root, outs) + '@' + j['ended']
            if key not in st['jobs']:
                st['jobs'].append(key)
                ok = j.get('state') == 'done'
                out.append((f"Jarvis: job {'finished' if ok else 'failed'}", (j.get('check') or os.path.relpath(root, outs))[:200]))
    # decisions/questions left for him: needs_fn may give [(key, title), ...] (one key per open question)
    if isinstance(needs_count, list):
        asks = needs_count
        for k, title in asks:
            if k not in st['asks']:
                st['asks'].append(k)
                if not seed_asks:
                    out.append(('Jarvis: your decision', title or 'An item needs your decision'))
        st['asks'] = st['asks'][-300:]
        needs_count = len({k.split('#', 1)[0] for k, _ in asks})
    # more things need him (at most hourly)
    if needs_count is not None:
        last = st.get('needs_at') or ''
        hour_ok = not last or (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() > 3600
        if needs_count > st.get('needs', 0) and hour_ok:
            out.append(('Jarvis', f'{needs_count} thing{"s" if needs_count != 1 else ""} need you'))
            st['needs_at'] = dt.datetime.now().isoformat(timespec='seconds')
        st['needs'] = needs_count
    st['approvals'] = st['approvals'][-300:]; st['jobs'] = st['jobs'][-300:]
    _save(STATE, st)
    if first or _quiet(cfg):
        return                      # the first run only records what is already there; quiet hours send nothing
    for t, b in out:
        ok, err = ping(t, b)
        log(f'notify: {t} - {b[:60]} ({"sent" if ok else "failed: " + err})')


LOGIN = os.path.join(HUB, 'state', 'claude_cli_login.json')
CLAUDE = (paths.claude_bin() or 'claude')


def cli_logged_in():
    """True / False, or None when it cannot be told (the CLI missing or hung): an unknown never pings."""
    try:
        r = subprocess.run([CLAUDE, 'auth', 'status'], capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL)
        v = json.loads(r.stdout or '{}').get('loggedIn')
        return v if isinstance(v, bool) else None
    except Exception:
        return None


def check_login(log=print, probe=cli_logged_in):
    """Jarvis's own login: one ping when it goes (after quiet hours if it went at night), one when it is back."""
    cfg = _settings()
    ok = probe()
    if ok is None:
        return None
    st = _load(LOGIN, {'ok': True, 'since': None, 'pinged': False})
    now = dt.datetime.now().isoformat(timespec='seconds')
    if ok:
        if st.get('ok') is False:                        # back
            h = (dt.datetime.now() - dt.datetime.fromisoformat(st['since'])).total_seconds() / 3600 if st.get('since') else 0
            log(f'notify: the claude CLI login is back after {h:.1f} h')
            if st.get('pinged') and cfg['enabled'] and not _quiet(cfg):
                ping('Jarvis is working again', f'Login restored after {h:.1f} h; the missed inbox checks run now.')
        _save(LOGIN, {'ok': True, 'since': None, 'pinged': False, 'checked': now})
        return True
    if st.get('ok') is not False:
        st = {'ok': False, 'since': now, 'pinged': False}
        log('notify: the claude CLI is logged out; no background run works until `claude auth login`')
    st['checked'] = now
    if not st.get('pinged') and cfg['enabled'] and not _quiet(cfg):
        sent, err = ping('Jarvis is logged out', 'No inbox check or background work runs until you run "claude auth login" in a terminal.')
        st['pinged'] = bool(sent)
        log(f'notify: Jarvis is logged out ({"sent" if sent else "failed: " + err})')
    _save(LOGIN, st)
    return False


def start(needs_fn=None, log=print):
    def loop():
        n = 0
        while True:
            try:
                if n % 6 == 0:
                    check(needs_fn() if needs_fn else None, log)
                if n % 60 == 3:                          # every 10 minutes, first at 30 s after the start
                    check_login(log)
            except Exception as e:
                log(f'notify: {e}')
            n += 1
            time.sleep(10)
    threading.Thread(target=loop, daemon=True, name='notify').start()
