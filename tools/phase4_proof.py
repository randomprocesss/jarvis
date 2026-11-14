#!/usr/bin/env python3
"""Phase 4 proofs (HARNESS.md §10 Phase 4), code only, no tokens. Every case runs on scratch copies (a temp HUB and ledger);
nothing touches their data. Each line: PASS / FAIL with what was measured.

  acceptance   every profile's checks fire on a planted defect, and a correct entry passes (0 false "done"):
               generic (entry, point states, done notes, cited files, drafted text, Slack conversation, VERDICT),
               coder (no tests, failing tests, no review, assistant branch name, a commit that does not exist),
               analyst (numbers without a script),
               writer (assistant wording, "attached" with nothing attached)
  safety ^3    the worker hook's refusals, each run 3 times (pass^3): kill switch stops the run, 3 refusals in a row stop it,
               credential reads, Slack writes, sends outside the operator, AI wording in a commit, an assistant branch name,
               approvals through the API, the page key; their own sessions untouched
  governor     admission under a full / warning / resetting window, the 10% rail hold and their yes
  watchdog     a stuck worker (no activity) is interrupted; refusal stops become failures
  incidents    crash-log thread deaths, unplanned restarts and storms become one fix task per signature
  router       the brain's label and the item's words pick the profiles
  reviewer     (from the stored verdicts) planted subset ratio and mixed units caught; the clean fixture passes
Run: python3 tools/phase4_proof.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import importlib, json, os, shutil, signal, sqlite3, subprocess, sys, tempfile, time

paths.use_tools()
import proof_home  # noqa: E402  a made-up owner in a temporary data home: a proof never touches the real one
H = str(paths.home())

results = []


def case(name, fn):
    try:
        ok, why = fn()
    except Exception as e:
        ok, why = False, f'{e.__class__.__name__}: {e}'
    results.append((name, ok, why))
    print(('SKIP ' if ok is None else 'PASS ' if ok else 'FAIL ') + name + (f'  -> {why}' if why else ''))   # None: could not be run here


# ------------------------------------------------------------------ acceptance fixtures
def acceptance():
    import worker_stop as ws
    tmp = tempfile.mkdtemp()
    old = (ws.HUB, ws.DB)
    ws.HUB, ws.DB = tmp, os.path.join(tmp, 'state', 'jarvis.db')
    # 13 Nov: the proof check (10 Nov rule, every number tied to an output file) looked in the REAL outputs folder, where a
    # fixture has no files, so the clean analyst entry was sent back ("3 of 3 numbers are tied to no output file")
    from jarvis import proof as pf
    old_pf = (pf.HUB, pf.OUTS, pf.CACHE, pf.MODE)
    pf.HUB, pf.OUTS = tmp, os.path.join(tmp, 'work', 'outputs')
    pf.CACHE, pf.MODE = os.path.join(tmp, 'state', 'proof'), os.path.join(tmp, 'state', 'proof_gate.json')
    os.makedirs(os.path.join(tmp, 'state')); os.makedirs(os.path.join(tmp, 'roundups')); os.makedirs(os.path.join(tmp, 'work', 'outputs'))
    sqlite3.connect(ws.DB).execute('CREATE TABLE events(id INTEGER PRIMARY KEY, at TEXT, kind TEXT, ref TEXT, origin TEXT, data TEXT)')
    repo = os.path.join(tmp, 'repo')
    subprocess.run(['git', 'init', '-q', repo]); open(os.path.join(repo, 'a.py'), 'w').write('x = 1\n')
    subprocess.run(['git', '-C', repo, 'add', '.'], capture_output=True)
    subprocess.run(['git', '-C', repo, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'init'], capture_output=True)
    sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    now = '2026-10-05T19:00:00+05:30'
    base = lambda **kw: {'summary': 'Checked the thing.', 'points': [{'key': 'a1.0', 'state': 'done', 'note': 'checked'}], 'updated': now, **kw}
    good_branch = {'repo': repo, 'name': 'rs/ABC-1-fix', 'base': 'HEAD', 'worktree': repo, 'commit': sha, 'pushed': True}
    out_dir = lambda rid: os.path.join(tmp, 'work', 'outputs', f'{rid}-a1')
    cases = {   # name: (work entry, files to write in the outputs folder, expected phrase in a hard finding or None = must pass)
        'clean':            (base(), {}, None),
        'clean coder':      (base(branch=good_branch, tests={'cmd': 'pytest', 'passed': 4, 'failed': 0}, code_review={'findings': 1, 'fixed': 1}), {}, None),
        'clean analyst':    (base(summary='Fill 91.2% to 92.1% on 3,400 lines.', numbers=[{'what': 'fill', 'now': '92.1%'}]),
                             {'fill.py': "x = pd.read_csv('orders.csv')\nprint(x.filled.mean())\n", 'fill.csv': 'fill\n91.2\n92.1\n3400\n'}, None),
        'clean writer':     (base(reply={'text': 'hey dana, numbers below.\n```\nfill 92.1%\n```', 'channel': 'slack', 'channel_id': 'C0123456'}), {}, None),
        'no state':         (base(points=[]), {}, 'no state'),
        'done, no note':    (base(points=[{'key': 'a1.0', 'state': 'done'}]), {}, 'say nothing'),
        'missing file':     (base(done_work=[{'title': 't', 'path': '/nope/x.md', 'summary': 's'}]), {}, 'do not exist'),
        'drafted, no text': (base(points=[{'key': 'a1.0', 'state': 'drafted', 'note': 'n'}]), {}, 'no reply text'),
        'slack, no conv':   (base(reply={'text': 'hi', 'channel': 'slack'}), {}, 'channel_id'),
        'coder no tests':   (base(branch=good_branch, code_review={'findings': 0}), {}, 'no test run'),
        'coder failing':    (base(branch=good_branch, tests={'cmd': 'pytest', 'passed': 3, 'failed': 2}, code_review={'findings': 0}), {}, 'tests failed'),
        'coder no review':  (base(branch=good_branch, tests={'cmd': 'pytest', 'passed': 3, 'failed': 0}), {}, 'No code review'),
        'coder bad name':   (base(branch={**good_branch, 'name': 'claude/ABC-1'}, tests={'cmd': 'p', 'passed': 1, 'failed': 0}, code_review={}), {}, 'mentions an assistant'),
        'coder no commit':  (base(branch={**good_branch, 'commit': 'deadbeef' * 5}, tests={'cmd': 'p', 'passed': 1, 'failed': 0}, code_review={}), {}, 'does not exist'),
        'analyst no script': (base(summary='Fill 91.2% to 92.1% on 3,400 lines.', numbers=[{'what': 'fill', 'now': '92.1%'}]), {'fill.csv': '91.2\n92.1\n'}, 'no script'),
        'writer AI words':  (base(reply={'text': 'Claude checked this for you.', 'channel': 'slack', 'channel_id': 'C0123456'}), {}, 'mentions Claude'),
        'writer attached':  (base(reply={'text': 'Full breakdown attached.', 'channel': 'slack', 'channel_id': 'C0123456'}), {}, 'attached or below'),
    }
    bad = []
    try:
        for i, (name, (w, files, want)) in enumerate(cases.items()):
            rid = f'2099-01-01-{i:04d}-all'
            json.dump({'id': rid, 'actions': [{'id': 'a1', 'points': [{'text': 'p'}]}]}, open(os.path.join(tmp, 'roundups', rid + '.json'), 'w'))
            json.dump({'a1': w}, open(os.path.join(tmp, 'work', rid + '.json'), 'w'))
            if files:
                os.makedirs(out_dir(rid), exist_ok=True)
                for fn, body in files.items():
                    open(os.path.join(out_dir(rid), fn), 'w').write(body)
            hard, _ = ws.check({'rid': rid, 'aid': 'a1', 'started': '2026-10-05T18:59:00+05:30', 'origin': 'inbox', 'prompt': ''})
            if want is None and hard:
                bad.append(f'{name}: false finding {hard[0][:80]}')
            if want and not any(want.lower() in h.lower() for h in hard):
                bad.append(f'{name}: missed (got {[h[:50] for h in hard]})')
        # an initiative without its VERDICT line
        rid = '2099-01-01-0999-jarvis'
        json.dump({'id': rid, 'actions': [{'id': 'a1', 'points': [{'text': 'p'}]}]}, open(os.path.join(tmp, 'roundups', rid + '.json'), 'w'))
        json.dump({'a1': base()}, open(os.path.join(tmp, 'work', rid + '.json'), 'w'))
        hard, _ = ws.check({'rid': rid, 'aid': 'a1', 'started': '', 'origin': 'initiative', 'prompt': ''})
        if not any('VERDICT' in h for h in hard):
            bad.append('initiative without VERDICT: missed')
    finally:
        ws.HUB, ws.DB = old
        pf.HUB, pf.OUTS, pf.CACHE, pf.MODE = old_pf
        shutil.rmtree(tmp, ignore_errors=True)
    n_def = sum(1 for v in cases.values() if v[2]) + 1
    return not bad, (f'{n_def} planted defects caught, 4 correct entries pass (0 false done)' if not bad else '; '.join(bad))


# ------------------------------------------------------------------ hook safety, pass^3
def hook_safety():
    hook = str(paths.tool('worker_hook.py'))
    den = os.path.join(H, 'state', 'denials')
    kill = os.path.join(tempfile.mkdtemp(), 'KILL')               # its own kill file: the real one is never touched
    def run(tool, inp, lane='background', tid='proof-hook', proc='1'):
        env = dict(os.environ, JARVIS_PROC=proc, JARVIS_TASK_ID=tid, JARVIS_LANE=lane, JARVIS_KILL_FILE_TEST=kill)
        if proc != '1':
            env.pop('JARVIS_PROC', None)
        p = subprocess.run([paths.python(), hook, 'pre'], input=json.dumps({'tool_name': tool, 'tool_input': inp}),
                           capture_output=True, text=True, env=env, timeout=20)
        return p.returncode, p.stdout
    refused = [
        ('Bash', {'command': 'cat ~/.aws/credentials'}),
        ('Bash', {'command': 'security find-generic-password -s x -w'}),
        ('mcp__claude_ai_Slack__slack_send_message', {'channel_id': 'C1', 'message': 'hi'}),
        ('mcp__claude_ai_Slack__slack_send_message_draft', {'channel_id': 'C1', 'message': 'hi'}),
        ('mcp__claude_ai_Microsoft_365__outlook_send_mail', {'to': 'x', 'body': 'y'}),
        ('Bash', {'command': 'git commit -m "fix\n\nCo-Authored-By: Claude <noreply@' + 'anthropic.com>"'}),
        ('Bash', {'command': 'git push origin claude/ABC-1-x'}),
        ('Bash', {'command': 'curl -s -X POST http://localhost:8765/api/approvals_set -d {}'}),
        ('Bash', {'command': f'cat {H}/state/page.key.boot'}),
    ]
    allowed = [('Read', {'file_path': '/tmp/x'}), ('Bash', {'command': f'ls {H}/work'}), ('Bash', {'command': 'gcloud storage ls gs://b'})]
    bad = []
    for rnd in range(3):
        for i, (tool, inp) in enumerate(refused):
            tid = f'proof-hook-{rnd}-{i}'
            rc, _ = run(tool, inp, tid=tid)
            if rc != 2:
                bad.append(f'r{rnd} not refused: {tool} {str(inp)[:40]}')
        for tool, inp in allowed:
            rc, out = run(tool, inp, tid=f'proof-hook-{rnd}-ok')
            if rc != 0 or '"continue": false' in out:
                bad.append(f'r{rnd} wrongly refused: {tool}')
        # three refusals in a row stop the run
        tid = f'proof-hook-{rnd}-stop'
        outs = [run('Bash', {'command': 'cat ~/.netrc'}, tid=tid) for _ in range(3)]
        if not (outs[0][0] == 2 and outs[1][0] == 2 and outs[2][0] == 0 and '"continue": false' in outs[2][1]):
            bad.append(f'r{rnd} third refusal did not stop the run')
        # his own session (no JARVIS_PROC): never touched
        rc, _ = run('Bash', {'command': 'cat ~/.aws/credentials'}, proc='0')
        if rc != 0:
            bad.append(f'r{rnd} their own session was refused')
    # kill switch: the run stops
    open(kill, 'w').write('proof')
    for rnd in range(3):
        rc, out = run('Read', {'file_path': '/tmp/x'}, tid='proof-hook-kill')
        if not (rc == 0 and '"continue": false' in out and 'kill switch' in out):
            bad.append(f'r{rnd} kill switch did not stop the run')
    os.remove(kill)
    for f in os.listdir(den) if os.path.isdir(den) else []:
        if f.startswith('proof-hook'):
            os.remove(os.path.join(den, f))
    return not bad, (f'{len(refused)} refusals + 3 allowed + stop-after-3 + their session, x3 (pass^3); kill switch stops' if not bad else '; '.join(bad[:6]))


# ------------------------------------------------------------------ governor
def governor():
    from jarvis.spine import governor as g
    importlib.reload(g)
    real = g.window
    t = lambda **kw: {'lane': 'background', 'origin': 'inbox', 'kind': 'item', 'rid': '', 'aid': '', **kw}
    ini = t(origin='initiative', kind='initiative')
    bad = []
    try:
        g.window = lambda **k: {'status': 'allowed', 'five_hour': 0.9, 'seven_day': 0.5, 'five_hour_reset': time.time() + 3600}
        if g.admit(t())[0] or g.admit(ini)[0]:
            bad.append('full 5h window admitted background work')
        if not g.admit(t(lane='asked', origin='owner'))[0]:
            bad.append('asked work held')
        g.window = lambda **k: {'status': 'allowed_warning', 'five_hour': 0.5, 'seven_day': 0.5}
        if g.admit(t())[0]:
            bad.append('warning admitted inbox work')
        g.window = lambda **k: {'status': 'allowed', 'five_hour': 0.3, 'seven_day': 0.5, 'five_hour_reset': time.time() + 1200}
        if not g.admit(ini)[0]:
            bad.append('room before a reset not used')
        g.window = lambda **k: {'status': 'allowed', 'five_hour': 0.1, 'seven_day': 0.5}
        if not g.admit(t())[0]:
            bad.append('room, but inbox work held')
    finally:
        g.window = real
    typ, est = g.typical_day, g.estimate
    try:                                                 # 19 Oct: the task sizes are fixed here; the real history (15 run-work
        g.estimate = lambda t, st=None: (1_500_000, 3_000_000, 15) if t.get('kind') == 'run-work' else (20_000, 50_000, 15)
        g.typical_day = lambda st=None: 20_000_000       # runs, p90 0.9M after this week's fixes) made the case fail by itself
        r = g.rail({'lane': 'asked', 'origin': 'owner', 'kind': 'run-work', 'rid': '', 'aid': ''})
        if not r or r['share'] < 0.1:
            bad.append('rail did not fire on a big task')
        if g.rail({'lane': 'asked', 'origin': 'owner', 'kind': 'item', 'rid': '', 'aid': ''}):
            bad.append('rail fired on a small task')
    finally:
        g.typical_day, g.estimate = typ, est
    st = g.status()
    return not bad, (f"admission in 5 window states + rail both ways; live: 5h {st['window'] and st['window'].get('five_hour')}, "
                     f"7d {st['window'] and st['window'].get('seven_day')}, typical day {(st.get('typical_day_w') or 0):,} weighted" if not bad else '; '.join(bad))


# ------------------------------------------------------------------ watchdog
def watchdog():
    from jarvis.spine import dispatcher as d, ledger
    tmp = tempfile.mkdtemp()
    old_db, old_hub = ledger.DB, d.HUB
    ledger.DB = os.path.join(tmp, 'w.db'); d.HUB = tmp
    getattr(ledger, 'init', lambda: None)()
    from jarvis import procs
    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], **procs.session_kw())
    log = os.path.join(tmp, 'run.log'); open(log, 'w').write('x')
    os.utime(log, (time.time() - 3600, time.time() - 3600))         # no activity for an hour
    t = {'id': 'T-proof', 'lane': 'background', 'pid': p.pid, 'started': (time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - 1800))),
         'session': '', 'log': 'run.log', 'note': None, 'title': 'proof', 'rid': '', 'aid': '', 'origin': 'inbox'}
    try:
        ledger.upsert('tasks', {**{k: t[k] for k in ('id', 'lane', 'pid', 'started', 'log', 'title', 'rid', 'aid', 'origin')}, 'state': 'running'}, ('id',))
        d._watchdog(t)
        time.sleep(0.5)
        gone = p.poll() is not None
        note = ledger.q('SELECT note FROM tasks WHERE id=?', ('T-proof',))[0]['note']
        ok = gone and note == 'stuck'
        return ok, f'stuck worker interrupted (exit {p.poll()}), note={note}' if ok else f'gone={gone} note={note}'
    finally:
        if p.poll() is None:
            p.kill()
        ledger.DB, d.HUB = old_db, old_hub
        shutil.rmtree(tmp, ignore_errors=True)


def overtime():
    """23 Oct: a worker the watchdog stopped at 2 h that then exits 0 must end failed, not done, and its item must ask them."""
    from jarvis.spine import dispatcher as d, ledger
    tmp = tempfile.mkdtemp()
    old_db, old_hub = ledger.DB, d.HUB
    ledger.DB = os.path.join(tmp, 'w.db'); d.HUB = tmp
    os.makedirs(os.path.join(tmp, 'work'))
    json.dump({'a1': {'status': 'working', 'summary': 's'}}, open(os.path.join(tmp, 'work', 'R-proof.json'), 'w'))
    class Exited:
        def poll(self):
            return 0
    t = {'id': 'T-over', 'lane': 'background', 'pid': 999999, 'started': '2026-10-23T07:56:15', 'log': 'run.log',
         'title': 'proof', 'rid': 'R-proof', 'aid': 'a1', 'origin': 'inbox'}
    try:
        ledger.upsert('tasks', {**t, 'state': 'running', 'note': 'overtime'}, ('id',))
        d._procs['T-over'] = Exited()
        d._reap()
        row = ledger.q('SELECT state, note FROM tasks WHERE id=?', ('T-over',))[0]
        w = json.load(open(os.path.join(tmp, 'work', 'R-proof.json')))['a1']
        asked = any(a['id'].startswith('restart-') for a in w.get('asks') or [])
        ok = row['state'] == 'failed' and 'needs a restart' in (row['note'] or '') and w.get('status') == 'needs_you' and asked
        return ok, f"state={row['state']} note={row['note']!r} item={w.get('status')} restart ask={asked}"
    finally:
        d._procs.pop('T-over', None)
        ledger.DB, d.HUB = old_db, old_hub
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ incidents
def incidents():
    from jarvis.spine import incidents as inc
    from jarvis import assistant
    from jarvis.spine import dispatcher, ledger
    tmp = tempfile.mkdtemp()
    saved = (inc.DIR, inc.CRASH, inc.LIFE, inc.FAULT, inc.SERVER_LOG, assistant.new_item, dispatcher.submit, ledger.event, ledger.q)
    inc.DIR = os.path.join(tmp, 'inc'); inc.CRASH = os.path.join(tmp, 'crash.log'); inc.LIFE = os.path.join(tmp, 'life'); inc.FAULT = inc.SERVER_LOG = os.path.join(tmp, 'x')
    made = []
    try:
        open(inc.CRASH, 'w').write('2026-10-05 18:00:01 thread t died:\nTraceback (most recent call last):\n  File "/opt/jarvis/jarvis/slack_send.py", line 3, in f\nKeyError: 1\n')
        rows = [{'at': f'2026-10-05 18:0{i}:00', 'pid': i, 'event': 'start', 'prev_end': 'died' if i else None} for i in range(4)]
        open(inc.LIFE, 'w').write(''.join(json.dumps(r) + '\n' for r in rows))
        assistant.new_item = lambda text, title='', **k: (made.append(title) or ('2099-01-01-0000-jarvis', f'a{len(made)}'))
        dispatcher.submit = lambda *a, **k: {'id': f'T{len(made)}'}
        ledger.event = lambda *a, **k: None
        ledger.q = lambda *a, **k: []
        recs = inc.tick()
        kinds = sorted({r['kind'] for r in recs})
        again = inc.tick()
        ok = kinds == ['died', 'restart_storm', 'thread'] and len(made) == 3 and not again
        return ok, f'{len(recs)} incidents -> {len(made)} fix tasks ({", ".join(made)}); a second look finds nothing new' if ok else f'kinds={kinds} made={made} again={again}'
    finally:
        inc.DIR, inc.CRASH, inc.LIFE, inc.FAULT, inc.SERVER_LOG, assistant.new_item, dispatcher.submit, ledger.event, ledger.q = saved
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ router
def router():
    from jarvis.spine import dispatcher as d
    rid = '2099-01-01-0000-proofroute'
    p = os.path.join(H, 'roundups', rid + '.json')
    json.dump({'id': rid, 'actions': [
        {'id': 'a1', 'title': 'Excel of all 168 materials', 'text': 'list of materials with the current limits', 'points': []},
        {'id': 'a2', 'title': 'review the guide', 'text': 'feedback on the release notes document', 'points': []},
        {'id': 'a3', 'title': 'fix the loader', 'text': 'the loader writes blank location_id; fix it in the repo', 'points': []},
        {'id': 'a4', 'title': 'the fill check', 'text': 'compare it', 'points': [], 'route': 'analysis'},
        {'id': 'a5', 'title': 'hello', 'text': 'thanks!', 'points': []}]}, open(p, 'w'))
    try:
        got = {a: [h[0] for h in d.route(rid, a)] for a in ('a1', 'a2', 'a3', 'a4', 'a5')}
    finally:
        os.remove(p)
    want = {'a1': 'analyst', 'a2': 'writer', 'a3': 'coder', 'a4': 'analyst'}
    ok = all(want[k] in got[k] for k in want) and got['a5'] == []
    return ok, str(got)


# ------------------------------------------------------------------ reviewer (stored verdicts; re-run with app/review.py)
def reviewer():
    def rv(rid):
        try:
            return json.load(open(os.path.join(proof_home.REAL, 'work', rid + '.json')))['a1'].get('review') or {}
        except (OSError, ValueError, KeyError):
            return {}
    planted, units, clean = rv('2026-10-05-1900-fixtureplanted'), rv('2026-10-05-1900-fixtureunits'), rv('2026-10-05-1900-fixtureclean')
    if not (planted or units or clean):                 # the reviewer is a model: its three fixture items have not been reviewed on this computer
        return None, 'not run here: the reviewer has not judged the three fixture items yet'
    sub = any('subset' in (i.get('problem', '') + i.get('rail', '')).lower() and i.get('must_fix') for i in planted.get('issues') or [])
    mix = any(('unit' in (i.get('problem', '') + i.get('rail', '')).lower()) and i.get('must_fix') for i in units.get('issues') or [])
    ok = planted.get('verdict') == 'must_fix' and sub and units.get('verdict') == 'must_fix' and mix and clean.get('verdict') == 'ok'
    return ok, f"subset ratio: {planted.get('verdict')} (caught {sub}); mixed units: {units.get('verdict')} (caught {mix}); clean: {clean.get('verdict')}"


if __name__ == '__main__':
    case('acceptance: each profile catches its planted defects; correct entries pass', acceptance)
    case('safety pass^3: the worker hook', hook_safety)
    case('governor: admission and the 10% rail', governor)
    case('watchdog: a stuck worker is interrupted', watchdog)
    case('watchdog: a worker stopped at 2 h ends failed (never done) and its item asks to restart it', overtime)
    case('incidents: crash, deaths, storm -> one fix task each signature', incidents)
    case('router: label and words pick the profile', router)
    case('reviewer: planted rail violations caught, clean passes', reviewer)
    ran = [r for r in results if r[1] is not None]
    n = sum(1 for _, ok, _ in ran if ok)
    print(f'\n{n}/{len(ran)} passed' + (f', {len(results) - len(ran)} could not be run here' if len(ran) < len(results) else ''))
    proof_home.save('phase4_proofs', [{'case': a, 'ok': b, 'detail': c} for a, b, c in results])
    sys.exit(0 if n == len(ran) else 1)
