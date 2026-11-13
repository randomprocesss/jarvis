"""From incident to fix (HARNESS.md §7, Phase 4, 5 Oct 2026). Every crash, hang or restart storm becomes a harness-diagnosis
task: a background worker reads the evidence, finds the cause, makes the fix on a branch in a separate git worktree of the
Jarvis repo (never the live folder), runs the regression suite there and stops. The merge waits for the owner.

What counts (code, no model):
  died           the server started and the one before it had no clean stop (spine/health.py lifecycle): a crash, a hang
                 the app killed, a kill -9. A redeploy and the app's launch restart send SIGTERM, which is a clean stop.
  thread         a background thread died with an exception (server.crash.log).
  fatal          an exception escaped the main thread (server.crash.log), or faulthandler wrote a fatal dump.
  restart_storm  3 or more 'died' starts within 10 minutes (told at once, loud).
  mac_restart    the server had no clean stop because the whole Mac went down with it (the Mac booted after that server
                 started and the server was alive up to 30 minutes before the boot: a flat battery, a forced power-off, a panic). Recorded with its evidence, but no fix task: there is no code to fix
                 (24 Oct 18:18: the battery ran flat, and a 2-hour fix worker was started for it).
A 'died' incident quotes only the fault-log section that the dead server wrote (24 Oct: the last 3,000 characters held a
16 Oct dump from another server, which read as this one's stack).
Each incident has a signature (kind + the last frame of its traceback). One fix task per signature per day; an incident
whose signature already has a fix waiting for their merge only adds to that item's evidence.
"""
from jarvis import paths
import datetime as dt, hashlib, json, os, re

HUB = str(paths.home())
STATE = os.path.join(HUB, 'state')
DIR = os.path.join(STATE, 'incidents')
CRASH = os.path.join(STATE, 'server.crash.log')
FAULT = os.path.join(STATE, 'server.fault.log')
LIFE = os.path.join(STATE, 'lifecycle.ndjson')
SERVER_LOG = str(paths.logs('server.log'))


def _load(p, d):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def _save(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(o, open(p + '.tmp', 'w'), indent=1)
    os.replace(p + '.tmp', p)


def _tail(path, n=4000):
    try:
        with open(path, 'rb') as f:
            f.seek(max(0, os.path.getsize(f.name) - n))
            return f.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def _fault_section(pid, n=3000):
    """What server `pid` wrote to the fault log: from its '--- <time> server start, pid <pid>' line to the next start line
    (the end of it when longer than n). Says so when that server wrote no dump."""
    try:
        text = open(FAULT, errors='replace').read()
    except OSError:
        return '(no fault log)'
    m = re.search(rf'^--- [^\n]* server start, pid {pid}\n(.*?)(?=^--- |\Z)', text, re.M | re.S)
    if not m:
        return f'(no start line for pid {pid} in the fault log)'
    body = m.group(1).strip()
    return body[-n:] if body else (f'(none: pid {pid} wrote no dump, so it did not crash inside Python; it was killed '
                                   f'(kill -9, a memory kill) or the Mac stopped)')


def _boot(b):
    return dt.datetime.fromtimestamp(b).strftime('%Y-%m-%d %H:%M:%S') if b else 'unknown'


def signature(kind, text):
    """kind + the deepest app frame (file:function) + the exception type: the same bug twice gives the same signature."""
    frames = re.findall(r'File "([^"]*[/\\](?:jarvis|tools)[/\\][^"]+)", line \d+, in (\w+)', text)
    exc = re.findall(r'^(\w+(?:Error|Exception|Interrupt)):', text, re.M)
    key = kind + '|' + (f'{os.path.basename(frames[-1][0])}:{frames[-1][1]}' if frames else '') + '|' + (exc[-1] if exc else '')
    return key, hashlib.sha1(key.encode()).hexdigest()[:10]


def found():
    """New incidents since the last look: [(kind, evidence text)]. Moves the read offsets forward."""
    st = _load(os.path.join(DIR, 'offsets.json'), {})
    out = []
    # crash log: FATAL and thread deaths, each block until the next timestamped line
    if os.path.exists(CRASH):
        size = os.path.getsize(CRASH)
        off = st.get('crash', 0) if st.get('crash', 0) <= size else 0
        with open(CRASH, errors='replace') as f:
            f.seek(off)
            new = f.read()
        st['crash'] = size
        for blk in re.split(r'\n(?=\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} )', new):
            if 'FATAL uncaught exception' in blk:
                out.append(('fatal', blk[:6000]))
            elif re.search(r'thread \S+ died:', blk):
                out.append(('thread', blk[:6000]))
    # lifecycle: starts after a death, and storms of them
    rows = []
    try:
        rows = [json.loads(l) for l in open(LIFE) if l.strip()]
    except (OSError, ValueError):
        pass
    seen = st.get('life', 0)
    died = [(i, r) for i, r in enumerate(rows) if r.get('event') == 'start' and r.get('prev_end') == 'died' and i >= seen]
    st['life'] = len(rows)
    for i, r in died:
        out.append(('died', f"server pid {r.get('pid')} started {r.get('at')}; the previous server (pid {r.get('prev_pid')}) ended "
                            f"without a clean stop.\n\nfault log of pid {r.get('prev_pid')}:\n{_fault_section(r.get('prev_pid'))}"
                            f"\n\nserver log (end):\n{_tail(SERVER_LOG, 3000)}"))
    for i, r in enumerate(rows):
        if r.get('event') == 'start' and r.get('prev_end') == 'mac_restart' and i >= seen:
            out.append(('mac_restart', f"server pid {r.get('pid')} started {r.get('at')}; the previous server (pid {r.get('prev_pid')}) "
                                       f"stopped when the Mac went down without a clean shutdown (Mac booted {_boot(r.get('boot'))})."
                                       f"\n\nfault log of pid {r.get('prev_pid')}:\n{_fault_section(r.get('prev_pid'))}"
                                       f"\n\nserver log (end):\n{_tail(SERVER_LOG, 3000)}"))
    starts = [dt.datetime.fromisoformat(r['at'].replace(' ', 'T')) for r in rows if r.get('event') == 'start' and r.get('prev_end') == 'died']
    if died and len([t for t in starts if (starts[-1] - t).total_seconds() <= 600]) >= 3:
        out.append(('restart_storm', f"{len(starts)} unplanned restarts, 3+ within 10 minutes (last {starts[-1]}).\n\n"
                                     f"server log (end):\n{_tail(SERVER_LOG, 4000)}"))
    _save(os.path.join(DIR, 'offsets.json'), st)
    return out


def fix_prompt(rid, aid, path, inc_id):
    wt, repo = str(paths.worktree(f'fix-{inc_id}')), str(paths.REPO)
    return (f"Use the jarvis-inbox-work skill on round-up {rid} item {aid} only. This is Jarvis's own incident: internal work, nothing "
            f"goes to anyone. Read the evidence in {path}. Find the cause in the Jarvis code (the repository is {repo}; the "
            f"server is jarvis/server.py). Reproduce it where you can. Fix it on a branch in a separate worktree, never in the live "
            f"folder (the running server uses it): `git -C \"{repo}\" worktree add \"{wt}\" -b fix/incident-{inc_id}`, edit "
            f"there, then run the regression suite against the fix: `\"{paths.python()}\" \"{wt}/tools/jarvis_eval.py\"` "
            f"(all cases must pass), and commit on the branch. The commit message is about the change only (no assistant or AI "
            f"wording: the hook refuses it). Do not merge, do not restart the server, do not touch the live folder. In the work "
            f"entry: the cause with the evidence lines, the fix, the suite result, and one needs_you point: 'Merge "
            f"fix/incident-{inc_id}?'. If you cannot find the cause, say what you checked and what would show it next time.")


def handle(kind, text, now=None):
    """Record one incident; start its fix task unless one for the same signature is open or ran today. Returns the record."""
    from jarvis.spine import ledger, dispatcher
    from jarvis import assistant
    key, sig = signature(kind, text)
    now = now or dt.datetime.now()
    idx = _load(os.path.join(DIR, 'index.json'), {})
    inc_id = now.strftime('%Y%m%d-%H%M%S') + '-' + sig[:4]
    path = os.path.join(DIR, inc_id + '.md')
    os.makedirs(DIR, exist_ok=True)
    with open(path, 'w') as f:
        f.write(f"# Incident {inc_id}\n\nkind: {kind}\nsignature: {key}\nat: {now.isoformat(timespec='seconds')}\n\n```\n{text[:12000]}\n```\n")
    rec = {'id': inc_id, 'kind': kind, 'sig': sig, 'key': key, 'at': now.isoformat(timespec='seconds'), 'path': path}
    prev = idx.get(sig)
    ledger.event('incident', inc_id, 'health', {'kind': kind, 'sig': key, 'path': path, 'repeat_of': (prev or {}).get('id')})
    if kind == 'mac_restart':                            # the Mac went down: evidence kept, no fix task (nothing to fix)
        return {**rec, 'task': None}
    open_task = prev and ledger.q("SELECT 1 FROM tasks WHERE id=? AND state IN ('queued','running','paused','held')", (prev.get('task') or '',))
    if prev and (open_task or prev.get('at', '')[:10] == now.date().isoformat()):
        prev.setdefault('repeats', []).append(inc_id)
        idx[sig] = prev
        _save(os.path.join(DIR, 'index.json'), idx)
        return {**rec, 'task': None, 'repeat_of': prev.get('id')}
    title = {'died': 'Jarvis server died', 'thread': 'A Jarvis background thread died', 'fatal': 'Jarvis server crashed',
             'restart_storm': 'Jarvis server keeps restarting'}[kind]
    rid, aid = assistant.new_item(f"{title}. Find the cause and propose a fix on a branch (evidence: {path}).", title, desk=True)
    t = dispatcher.submit('background', fix_prompt(rid, aid, path, inc_id), rid, aid, title=title, origin='incident', kind='harness-fix')
    rec['task'] = t['id'] if t else None
    rec['item'] = f'{rid}|{aid}'
    idx[sig] = rec
    _save(os.path.join(DIR, 'index.json'), idx)
    return rec


def tick():
    """Called by the heartbeat: look for new incidents and handle each (a storm is also said at once: task_alerts)."""
    out = []
    for kind, text in found():
        try:
            out.append(handle(kind, text))
        except Exception as e:
            from jarvis.spine import ledger
            ledger.event('incident_error', '', 'health', {'kind': kind, 'error': str(e)[:300]})
    return out
