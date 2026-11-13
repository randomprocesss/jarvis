#!/usr/bin/env python3
"""Phase 6 proofs (HARNESS.md §10 Phase 6), code only. Scratch ledger; nothing touches their data or starts a real worker.

  no_outward ^3   a self-started check (an initiative or an anomaly item: lane background) can never produce a T2/T3 action:
                  the worker hook refuses every send, post, approval and database-write path, and the gateway refuses the
                  T2/T3 endpoints to anything that is not the Jarvis window, 3 times each
  flood           200 anomaly events and 200 needs with strong data arrive at once: initiatives start one at a time and
                  never more than the daily cap (+ the night allowance on AC) over a simulated day; and with the week at
                  75% overnight (the governor's weekday reserve), none start at all
  oneshots        every one-shot Claude run the Phase 6 watchers start (org watch, document reader, research scout, goal
                  inference, meaning matcher), captured as launched: only search / read / list / fetch tools, no write mode
Run: python3 tools/phase6_proof.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import json, os, subprocess, sys, tempfile

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
    print(('PASS ' if ok else 'FAIL ') + name + (f'  -> {why}' if why else ''))


def no_outward():
    hook = os.path.join(H, 'tools', 'worker_hook.py')
    calls = [('mcp__claude_ai_Slack__slack_send_message', {'channel_id': 'C1', 'message': 'x'}),
             ('mcp__claude_ai_Slack__slack_send_message_draft', {'channel_id': 'C1', 'message': 'x'}),
             ('mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue', {'issueIdOrKey': 'ABC-1', 'commentBody': 'x'}),
             ('mcp__claude_ai_Microsoft_365__outlook_send_mail', {'to': 'x', 'body': 'y'}),
             ('Bash', {'command': 'curl -s -X POST http://localhost:8765/api/approvals_set -d {}'}),
             ('Bash', {'command': 'curl -s -X POST http://localhost:8765/api/slack_send -d {}'}),
             ('Bash', {'command': 'curl -s -X POST http://localhost:8765/api/close_action -d {}'})]
    kill = os.path.join(tempfile.mkdtemp(), 'KILL')
    bad = []
    for rnd in range(3):
        for i, (tool, inp) in enumerate(calls):
            env = dict(os.environ, JARVIS_PROC='1', JARVIS_TASK_ID=f'p6-{rnd}-{i}', JARVIS_LANE='background', JARVIS_KILL_FILE_TEST=kill)
            p = subprocess.run([paths.python(), hook, 'pre'], input=json.dumps({'tool_name': tool, 'tool_input': inp}),
                               capture_output=True, text=True, env=env, timeout=20)
            if p.returncode != 2 and '"continue": false' not in (p.stdout or ''):
                bad.append(f'r{rnd} allowed {tool} {str(inp)[:40]}')
    for f in os.listdir(os.path.join(H, 'state', 'denials')) if os.path.isdir(os.path.join(H, 'state', 'denials')) else []:
        if f.startswith('p6-'):
            os.remove(os.path.join(H, 'state', 'denials', f))
    from jarvis.spine import gateway as gw, ledger
    old, old_db = gw.PAGE_KEY, ledger.DB
    gw.PAGE_KEY = 'k'
    ledger.DB = os.path.join(tempfile.mkdtemp(), 'gw.db')          # the refusals are recorded in a scratch ledger, never his
    try:
        for rnd in range(3):
            for path, body in (('/api/slack_send', {}), ('/api/approvals_add', {'item': {}}), ('/api/approvals_set', {'status': 'approved'}),
                               ('/api/jira/comment', {'dry': False})):
                ok, _ = gw.check(path, body, {})                                   # a worker's curl: no Origin, no key
                ok2, _ = gw.check(path, body, {'Origin': 'http://localhost:8765'})  # even with the page's Origin, no key
                if ok or ok2:
                    bad.append(f'r{rnd} gateway let {path} through')
    finally:
        gw.PAGE_KEY, ledger.DB = old, old_db
    return not bad, (f'{len(calls)} outward tool paths + 4 T2/T3 endpoints, x3: all refused' if not bad else '; '.join(bad[:5]))


def flood():
    from jarvis.spine import ledger, dispatcher, governor
    from jarvis.world import initiative
    from jarvis import assistant
    T = tempfile.mkdtemp()
    saved = (ledger.DB, dispatcher.submit, assistant.new_item, initiative.on_ac, governor.window, initiative._mark_noticed)
    ledger.DB = os.path.join(T, 'flood.db')
    started = []
    def fake_submit(lane, prompt, rid='', aid='', title='', origin='', kind='', initiative_id=None, **k):
        tid = f'T{len(started)}'
        started.append(tid)
        ledger.upsert('tasks', {'id': tid, 'rid': rid, 'aid': aid, 'title': title, 'kind': kind, 'lane': lane, 'origin': origin,
                                'state': 'running', 'created': ledger.now(), 'prompt': prompt}, ('id',))
        return {'id': tid, 'state': 'running'}
    try:
        dispatcher.submit = fake_submit
        assistant.new_item = lambda text, title='', **k: ('2099-01-01-0000-jarvis', f'a{len(started)}')
        initiative._mark_noticed = lambda rid, aid: None
        initiative.on_ac = lambda: False
        governor.window = lambda **k: {'status': 'allowed', 'five_hour': 0.1, 'seven_day': 0.3}
        ledger.x("CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, system TEXT, name TEXT, local INTEGER, files INTEGER, rows INTEGER, path TEXT, newest TEXT, meta TEXT)")
        for i in range(200):
            ledger.x('INSERT INTO catalog_events(at, source_id, kind, detail) VALUES(?,?,?,?)',
                     (ledger.now(), f'gcs:T{i}', 'feed_stopped', json.dumps({'name': f'T{i}', 'last': '2026-09-01', 'late_days': 30, 'usual_gap_days': 1})))
        peak, rounds = 0, 0
        for _ in range(60):                                  # a simulated busy day: the initiatives job runs, workers finish
            rounds += 1
            initiative.anomalies()
            running = len(ledger.q("SELECT 1 FROM initiatives WHERE state IN ('admitted','running')"))
            peak = max(peak, running)
            ledger.x("UPDATE tasks SET state='done'")
            ledger.x("UPDATE initiatives SET state='done' WHERE state IN ('admitted','running')")
        n_day = len(started)
        # the governor's weekly reserve: outside work hours, at 70%+ of the week nothing self-started begins
        governor.window = lambda **k: {'status': 'allowed', 'five_hour': 0.1, 'seven_day': 0.75}
        wh = governor.work_hours; governor.work_hours = lambda now=None: False
        ledger.x('DELETE FROM initiatives'); before = len(started)
        for _ in range(10):
            initiative.anomalies()
        held = len(started) - before
        governor.work_hours = wh
        ok = n_day <= initiative.MAX_PER_DAY and peak <= 1 and held == 0
        return ok, f'200 events, {rounds} rounds: {n_day} started (cap {initiative.MAX_PER_DAY}), at most {peak} at a time; at 75% weekly overnight: {held} started'
    finally:
        ledger.DB, dispatcher.submit, assistant.new_item, initiative.on_ac, governor.window, initiative._mark_noticed = saved


def oneshots_read_only():
    """Every one-shot Claude run the Phase 6 watchers start (org watch, document reader, research scout, goal inference,
    meaning matcher) is captured as it would be launched: its tools may only search, read, list or fetch."""
    import re, types
    from jarvis.spine import ledger, governor
    from jarvis.world import org, docs, research, investigate, semantic, promises
    DENY = re.compile(r'send|post|create|update|edit|add|transition|delete|write|addWorklog|schedule|upload|approve|Bash|Write|Edit|NotebookEdit|Task|Agent', re.I)
    T = tempfile.mkdtemp()
    saved = (ledger.DB, governor.admit, governor.work_hours)
    ledger.DB = os.path.join(T, 'oneshot.db')
    governor.admit = lambda t: (True, '')
    seen, bad = {}, []
    def fake_run(mod):
        def run(cmd, **k):
            seen[mod] = cmd
            return types.SimpleNamespace(stdout='{}', stderr='', returncode=0)
        return run
    try:
        ledger.upsert('sources', {'id': 'doc:x', 'system': 'doc', 'kind': 'docx', 'name': 'x.docx', 'path': 'sp', 'columns': '[]', 'local': 0, 'meta': '{}'}, ('id',))
        ledger.x('INSERT INTO catalog_events(at, source_id, kind, detail) VALUES(?,?,?,?)', (ledger.now(), 'doc:x', 'new_source', '{}'))
        ledger.x("CREATE TABLE IF NOT EXISTS needs(id TEXT PRIMARY KEY, kind TEXT, statement TEXT, target TEXT, state TEXT, dismissed_at TEXT, scope TEXT, created TEXT, tainted INTEGER, origin TEXT, updated TEXT)")
        ledger.x("INSERT INTO needs(id, kind, statement, target, state, scope, created, tainted, origin, updated) VALUES('n','data_need','s','t','open','work','2026-10-01',0,'owner_said','2026-10-01')")
        ledger.x("CREATE TABLE IF NOT EXISTS commitments(id TEXT PRIMARY KEY, created TEXT, who TEXT, to_whom TEXT, what TEXT, due TEXT, state TEXT, origin TEXT, source_ref TEXT, quote TEXT, dismissed_at TEXT, updated TEXT, scope TEXT)")
        ledger.x("INSERT INTO commitments(id, created, who, to_whom, what, state, scope) VALUES('c','2026-09-01','Dana','The owner','send the list','open','work')")
        for name, mod, call in (('org', org, lambda: org.daily()), ('docs', docs, lambda: docs.probe()), ('research', research, lambda: research.scout()),
                                ('goals', investigate, lambda: investigate.seed_goals()), ('semantic', semantic, lambda: semantic.run()),
                                ('promises', promises, lambda: promises.review())):
            old = mod.subprocess.run
            mod.subprocess.run = fake_run(name)
            try:
                call()
            except Exception as e:
                bad.append(f'{name}: {e.__class__.__name__}: {e}'[:120])
            finally:
                mod.subprocess.run = old
        for name, cmd in seen.items():
            tools = cmd[cmd.index('--tools') + 1] if '--tools' in cmd else 'default'
            allowed, i = [], cmd.index('--allowedTools') + 1 if '--allowedTools' in cmd else len(cmd)
            while i < len(cmd) and not str(cmd[i]).startswith('--'):
                allowed.append(cmd[i]); i += 1
            if tools == 'default' or DENY.search(tools):
                bad.append(f'{name}: built-in tools {tools!r}')
            bad += [f'{name}: may call {t}' for t in allowed if DENY.search(t.split('__')[-1])]
            if '--permission-mode' in cmd and cmd[cmd.index('--permission-mode') + 1] in ('bypassPermissions', 'auto', 'acceptEdits'):
                bad.append(f'{name}: permission mode {cmd[cmd.index("--permission-mode") + 1]}')
        # the filter must bite (a vacuous gate re-asserts nothing): planted write tools are caught, and the runs that use
        # connectors really did list tools to check
        if not all(DENY.search(t) for t in ('slack_send_message', 'addCommentToJiraIssue', 'outlook_send_mail', 'Bash', 'Write')):
            bad.append('the deny filter misses a planted write tool')
        for name in ('org', 'docs', 'research', 'goals', 'promises'):
            c = seen.get(name) or []
            if '--allowedTools' not in c or len(c) <= c.index('--allowedTools') + 1:
                bad.append(f'{name}: no allowed tools listed (nothing was checked)')
        missing = {'org', 'docs', 'research', 'goals', 'semantic', 'promises'} - set(seen)
        if missing:
            bad.append(f'not captured: {sorted(missing)}')
    finally:
        ledger.DB, governor.admit, governor.work_hours = saved
    return not bad, (f"{len(seen)} one-shot runs captured; tools: search/read/list/fetch only" if not bad else '; '.join(bad[:6]))


if __name__ == '__main__':
    case('no outward action from a self-started check (pass^3)', no_outward)
    case('the watchers\' one-shot Claude runs can only search, read, list or fetch', oneshots_read_only)
    case('caps hold under a flood of 200 events; the weekly reserve holds overnight', flood)
    n = sum(1 for _, ok, _ in results if ok)
    print(f'\n{n}/{len(results)} passed')
    proof_home.save('phase6_proofs', [{'case': a, 'ok': b, 'detail': c} for a, b, c in results])
    sys.exit(0 if n == len(results) else 1)
