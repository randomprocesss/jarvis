"""Never die silently (HARNESS.md §7, Phase 1, 5 Oct 2026).

install() is called once by server.py at start:
  - faulthandler writes every thread's stack to state/server.fault.log on a hard crash, and on `kill -USR1 <pid>` (a hung
    server can be diagnosed without killing it);
  - an exception that escapes the main thread is logged with its traceback, then the process exits 70, so the supervisor
    (launchd com.jarvis.spine, or the app's watchdog) restarts it instead of it hanging half-alive;
  - an exception in a background thread is logged (it used to vanish), and /healthz names the threads still alive;
  - state/server-<port>.pid lets the app's watchdog kill a server that is listening but no longer answering.
healthz() is the body of GET /healthz: cheap, no file or model calls, so a slow brain never makes the server look dead.
"""
from jarvis import paths
import datetime as dt, faulthandler, json, os, re, signal, subprocess, sys, threading, time, traceback

STATE = str(paths.home() / 'state')
PIDFILE = os.path.join(STATE, 'server-{port}.pid')      # per port: a test copy never overwrites the live one's
FAULT = os.path.join(STATE, 'server.fault.log')
CRASH = os.path.join(STATE, 'server.crash.log')
STARTED = time.time()
_beats = {}
_fault_file = None


def _stamp():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _log(text):
    """To stderr (server.log) and the crash log; never raises."""
    line = f'{_stamp()} {text}'
    try:
        sys.stderr.write(line + '\n'); sys.stderr.flush()
    except Exception:
        pass
    try:
        with open(CRASH, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


LIFE = os.path.join(STATE, 'lifecycle.ndjson')     # start / clean stop per server process: a start after no clean stop = it died


def _life(ev, **kw):
    try:
        with open(LIFE, 'a') as f:
            f.write(json.dumps({'at': _stamp(), 'pid': os.getpid(), 'event': ev, **kw}) + '\n')
    except Exception:
        pass


def boot_time():
    """When the Mac last booted (epoch seconds), or None. One sysctl call, read once at server start."""
    try:
        out = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, text=True, timeout=5).stdout
        return int(re.search(r'sec = (\d+)', out).group(1))
    except Exception:
        return None


ALIVE = os.path.join(STATE, 'server-alive.json')   # {pid: last time it was seen alive}, rewritten every ALIVE_EVERY s
ALIVE_EVERY = 60
MAC_GAP = 1800       # alive this close before the boot = it was running when the Mac went down (a flat battery may take a while to be plugged in)


def _alive_loop():
    """Stamps this server as alive (a few recent pids kept, so a test copy on another port never erases the live one)."""
    while True:
        try:
            try:
                seen = json.load(open(ALIVE))
            except (OSError, ValueError):
                seen = {}
            seen[str(os.getpid())] = time.time()
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-5:])
            with open(ALIVE + '.tmp', 'w') as f:
                json.dump(seen, f)
            os.replace(ALIVE + '.tmp', ALIVE)
        except Exception:
            pass
        time.sleep(ALIVE_EVERY)


def last_exit(boot=None):
    """How the previous server process ended: 'clean' (SIGTERM: a redeploy, the app's launch restart, a normal Mac
    shutdown), 'mac_restart' (no clean stop, the Mac booted after it started, and it was still alive within MAC_GAP of
    that boot: the whole Mac went down with it, e.g. the battery ran flat on 24 Oct, a forced power-off or a kernel panic;
    nothing in Jarvis's code to fix), 'died' (no clean stop otherwise: a crash, a hang the app killed, a kill -9; also
    when the boot time or its last sign of life is unknown) or None (no earlier start). Read once at boot."""
    try:
        rows = [json.loads(l) for l in open(LIFE) if l.strip()]
    except (OSError, ValueError):
        return None, None
    starts = [r for r in rows if r.get('event') == 'start' and r.get('pid') != os.getpid()]
    if not starts:
        return None, None
    prev = starts[-1]
    if any(r.get('event') == 'stop' and r.get('pid') == prev['pid'] for r in rows):
        return 'clean', prev
    try:
        began = time.mktime(time.strptime(prev['at'], '%Y-%m-%d %H:%M:%S'))
    except (KeyError, ValueError):
        began = None
    try:
        seen = json.load(open(ALIVE)).get(str(prev['pid']))
    except (OSError, ValueError, AttributeError):
        seen = None
    if boot and began and seen and boot > began and seen >= boot - MAC_GAP:
        return 'mac_restart', prev
    return 'died', prev


def install(port=8765):
    global _fault_file
    os.makedirs(STATE, exist_ok=True)
    _fault_file = open(FAULT, 'a')
    _fault_file.write(f'--- {_stamp()} server start, pid {os.getpid()}\n'); _fault_file.flush()
    boot = boot_time()
    how, prev = last_exit(boot)
    _life('start', prev_pid=(prev or {}).get('pid'), prev_end=how, boot=boot)
    if how == 'died':
        _log(f"previous server (pid {prev['pid']}, started {prev['at']}) ended without a clean stop")
    elif how == 'mac_restart':
        _log(f"previous server (pid {prev['pid']}, started {prev['at']}) stopped when the Mac went down "
             f"(booted {dt.datetime.fromtimestamp(boot).strftime('%Y-%m-%d %H:%M:%S')}, no clean shutdown)")
    threading.Thread(target=_alive_loop, name='alive', daemon=True).start()   # after last_exit read the previous stamp

    def on_term(signum, frame):
        _life('stop', how='SIGTERM')
        os._exit(0)
    signal.signal(signal.SIGTERM, on_term)
    faulthandler.enable(file=_fault_file, all_threads=True)
    if hasattr(signal, 'SIGUSR1') and hasattr(faulthandler, 'register'):      # not on Windows: a dump on request is a POSIX signal
        faulthandler.register(signal.SIGUSR1, file=_fault_file, all_threads=True, chain=False)

    def main_hook(t, v, tb):
        _log('FATAL uncaught exception, exiting 70 so the supervisor restarts the server:\n' + ''.join(traceback.format_exception(t, v, tb)))
        os._exit(70)

    def thread_hook(a):
        if a.exc_type is SystemExit:
            return
        name = a.thread.name if a.thread else '?'
        _log(f'thread {name} died:\n' + ''.join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))

    sys.excepthook = main_hook
    threading.excepthook = thread_hook
    pf = PIDFILE.format(port=port)
    with open(pf + '.tmp', 'w') as f:
        f.write(str(os.getpid()))
    os.replace(pf + '.tmp', pf)


def beat(name):
    """A background loop says it is alive (its age shows in /healthz)."""
    _beats[name] = time.time()


def healthz():
    now = time.time()
    names = sorted({t.name for t in threading.enumerate()})
    return {'ok': True, 'pid': os.getpid(), 'uptime_s': round(now - STARTED), 'threads': len(threading.enumerate()),
            'thread_names': names[:40], 'beats': {k: round(now - v) for k, v in _beats.items()},
            'supervisor': 'launchd' if os.environ.get('XPC_SERVICE_NAME', '').startswith('com.jarvis') else 'app'}


# ------------------------------------------------------------------ deep /healthz (Phase 4, HARNESS §7)
PROVIDERS = {}            # set by the server: {'tracker': token_works} (functions health cannot import without a cycle)
_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        v = fn()
    except Exception as e:
        v = {'error': str(e)[:120]}
    _cache[key] = (time.time(), v)
    return v


def _gcloud():
    """Does gcloud have a working login? The token goes nowhere: it is never read or kept. Only asked when the owner's
    settings list it (health.watch_logins)."""
    import shutil, subprocess
    g = shutil.which('gcloud')
    if not g:
        return {'ok': False, 'account': False}
    acct = subprocess.run([g, 'auth', 'list', '--filter=status:ACTIVE', '--format=value(account)'],
                          capture_output=True, text=True, timeout=20).stdout.strip()
    ok = subprocess.run([g, 'auth', 'print-access-token'], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=30).returncode == 0
    return {'ok': bool(acct) and ok, 'account': bool(acct)}


def _connectors():
    try:
        d = json.load(open(os.path.join(STATE, 'connectors.json')))
    except (OSError, ValueError):
        return {}
    return {'age_s': round(time.time() - d.get('at', 0)), 'servers': {s.get('name'): s.get('status') for s in d.get('servers') or []}}


def deep():
    from jarvis import config
    cfg = config.load()
    out = healthz()
    now = time.time()
    beats = out['beats']
    problems = []
    try:
        from jarvis import assistant
        out['brains'] = assistant.POOL.status()
    except Exception as e:
        out['brains'] = {'error': str(e)[:120]}
    try:
        from jarvis.spine import ledger
        rows = ledger.q("SELECT lane, state, COUNT(*) AS n FROM tasks WHERE state IN ('running','queued','paused','held') GROUP BY lane, state")
        out['dispatcher'] = {'tick_age_s': beats.get('heartbeat'), 'lanes': {f"{r['lane']}.{r['state']}": r['n'] for r in rows}}
        out['incidents_24h'] = len(ledger.q("SELECT 1 FROM events WHERE kind='incident' AND at >= ?",
                                            ((dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec='seconds'),)))
    except Exception as e:
        out['dispatcher'] = {'error': str(e)[:120]}
    if out['uptime_s'] > 120 and (beats.get('heartbeat') is None or beats['heartbeat'] > 300):   # not in the first 2 minutes
        problems.append(('heartbeat', "Jarvis's scheduler has stalled: nothing new starts until the server restarts."))
    try:
        from jarvis.spine import watchers
        lr = watchers.last_roundup_at()
        age = (now - lr) if lr else None                 # 0 = never ran (or the file is unreadable)
        out['watchers'] = {'roundup_age_s': round(age) if age is not None else None, 'governor_scan_age_s': beats.get('governor')}
        n = dt.datetime.now()
        if age is not None and age > 3 * 3600 and cfg.in_work_hours(n):
            problems.append(('roundup', f"No inbox check for {round(age / 3600)} hours: the round-up is not running."))
    except Exception as e:
        out['watchers'] = {'error': str(e)[:120]}
    out['logins'] = {}
    if 'gcloud' in (cfg.get('health.watch_logins') or []):
        g = _cached('gcloud', 600, _gcloud)
        out['logins']['gcloud'] = 'ok' if g.get('ok') else ('no login' if not g.get('account') else 'expired')
    if 'tracker' in PROVIDERS:
        out['logins']['tracker'] = 'ok' if _cached('tracker', 600, PROVIDERS['tracker']) is True else 'refused'
    try:                                                  # 12 Nov: Jarvis's own login (notify.check_login writes it every 10 minutes)
        cl = json.load(open(os.path.join(STATE, 'claude_cli_login.json')))
    except (OSError, ValueError):
        cl = {}
    out['logins']['claude_cli'] = 'unknown' if not cl else ('ok' if cl.get('ok') else 'logged out')
    if cl and cl.get('ok') is False:
        problems.append(('claude_cli', "I'm logged out, so no inbox check or background work runs. Run `claude auth login` in a terminal."))
    if out['logins'].get('gcloud', 'ok') != 'ok':
        problems.append(('gcloud', "Run `gcloud auth login` when you have a minute: checks that need it wait until then."))
    c = _connectors()
    srv = c.get('servers') or {}
    need = {k: srv.get(k) for k in cfg.get('sources.expected') or []}   # the ones the owner's work uses; the rest are not their concern
    out['connectors'] = {'age_s': c.get('age_s'), 'needed': need, 'others_connected': sum(1 for k, v in srv.items() if k not in need and v == 'connected')}
    bad = [k for k, v in need.items() if v is not None and v not in ('connected', 'pending')]
    if bad and (c.get('age_s') or 1e9) < 3600:
        problems.append(('connectors:' + ','.join(sorted(bad)), f"These connectors are not connected: {', '.join(b.replace('claude.ai ', '') for b in sorted(bad))}. Reconnect them in Claude's settings when you can."))
    try:
        from jarvis.spine import governor
        out['window'] = governor.status()
    except Exception as e:
        out['window'] = {'error': str(e)[:120]}
    out['kill'] = os.path.exists(os.path.join(STATE, 'KILL'))
    out['problems'] = [{'key': k, 'say': s} for k, s in problems]
    return out


def report_problems(d=None):
    """Each problem once a day into the ledger ('health_problem'); the page says it (server task_alerts)."""
    from jarvis.spine import ledger
    d = d or deep()
    cut = (dt.datetime.now() - dt.timedelta(hours=24)).isoformat(timespec='seconds')
    for p in d.get('problems') or []:
        if not ledger.q("SELECT 1 FROM events WHERE kind='health_problem' AND ref=? AND at >= ?", (p['key'], cut)):
            ledger.event('health_problem', p['key'], 'health', {'why': p['say']})
    return d
