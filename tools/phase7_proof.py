#!/usr/bin/env python3
"""Phase 7 proofs (the desk and the fortnightly brief; the owner, 9 Oct 2026), code only. A scratch hub and a scratch ledger;
nothing touches their data or starts a worker.

  desk_routing    work Jarvis starts by itself lands in a '-desk' round-up that the Inbox, phone pings (needs_now), the
                  brain's inbox tools, Slack draft sync, the "working on it now" list, task alerts, the heartbeat's worker
                  trigger and the work sweep all skip; the same thing asked by them still reaches every one of them
  collect         each finished piece of desk work becomes one entry with its refs (a probe's finding, an investigation's
                  question, a research finding); collecting again adds nothing; noticed items from before the desk are
                  re-kinded, a file that also holds an asked item is left alone
  ship check      (13 Nov) the autopilot's real ship check, on a scratch git repo, refuses every change to what guards
                  outward actions (policy gate, gateway, dispatcher, governor, learned, send path) and to its judges
  untouched       nothing the cases made reached their real round-ups, work files or ledger
Run: python3 tools/phase7_proof.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import json, os, shutil, sys, tempfile

paths.use_tools()
import proof_home  # noqa: E402  a made-up owner in a temporary data home: a proof never touches the real one
H = str(paths.home())

results = []


def case(name, fn):
    try:
        ok, why = fn()
    except Exception as e:
        import traceback
        ok, why = False, f'{e.__class__.__name__}: {e} @ {traceback.format_exc().splitlines()[-3].strip()[:120]}'
    results.append((name, ok, why))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  -> {why}' if why else ''))


MADE = []          # every round-up id and task id the cases create in the scratch hub


def _untouched():
    """None of what the cases made exists in the owner's own data home or ledger (their Jarvis keeps writing there, so a size
    check would not do)."""
    import sqlite3
    real = proof_home.REAL
    leaked = [r for r in MADE if os.path.exists(os.path.join(real, 'roundups', r + '.json')) or os.path.exists(os.path.join(real, 'work', r + '.json'))]
    rows, drows, db = [], [], os.path.join(real, 'state', 'jarvis.db')
    if os.path.exists(db):
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10)
        ids = ('T-d', 'T-a', 'T-p', 'ini-p', 'inv-q', 'rf-1')
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE name IN ('tasks', 'desk')")}
        rows = c.execute('SELECT id FROM tasks WHERE id IN (%s)' % ','.join('?' * len(ids)), ids).fetchall() if 'tasks' in have else []
        drows = c.execute("""SELECT id FROM desk WHERE refs LIKE '%"inv-q"%' OR refs LIKE '%"rf-1"%'""").fetchall() if 'desk' in have else []
        c.close()                                        # the exact ids as JSON values, so a real finding's longer id never matches
    return not leaked and not rows and not drows and len(MADE) >= 4, {'checked': len(MADE), 'leaked': leaked, 'ledger_rows': rows + drows}


class Scratch:
    """A temp hub (roundups/, work/) and ledger, patched into every module the desk touches; restored on exit."""
    def __enter__(self):
        from jarvis import assistant, server, slack_send, mcp
        from jarvis.spine import ledger, dispatcher
        from jarvis.world import desk
        self.tmp = tempfile.mkdtemp()
        for d in ('roundups', 'work', 'state'):
            os.makedirs(os.path.join(self.tmp, d))
        json.dump({}, open(os.path.join(self.tmp, 'roundups', 'state.json'), 'w'))
        self.saved = [(ledger, 'DB'), (assistant, 'HUB'), (server, 'HUB'), (server, 'ROUND'), (server, 'STATE'), (slack_send, 'HUB'),
                      (mcp, 'HUB'), (desk, 'HUB'), (desk, 'ROUND'), (desk, 'BRAIN'), (desk, 'BRIEFS'), (dispatcher, 'HUB')]
        self.old = [getattr(m, a) for m, a in self.saved]
        ledger.DB = os.path.join(self.tmp, 'state', 'jarvis.db')
        for m in (assistant, server, slack_send, mcp, desk, dispatcher):
            m.HUB = self.tmp
        server.ROUND = desk.ROUND = os.path.join(self.tmp, 'roundups')
        desk.BRAIN = os.path.join(self.tmp, 'brain', 'topics')
        desk.BRIEFS = os.path.join(self.tmp, 'brain', 'briefs')
        server.STATE = os.path.join(self.tmp, 'roundups', 'state.json')
        desk._kind_cache.clear()
        return self

    def __exit__(self, *a):
        for (m, attr), v in zip(self.saved, self.old):
            setattr(m, attr, v)
        from jarvis.world import desk
        desk._kind_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def work(self, rid, aid, entry):
        p = os.path.join(self.tmp, 'work', rid + '.json')
        d = json.load(open(p)) if os.path.exists(p) else {}
        d[aid] = entry
        json.dump(d, open(p, 'w'))


def desk_routing():
    from jarvis import assistant, server, slack_send, mcp
    from jarvis.spine import ledger, heartbeat
    with Scratch() as s:
        noticed = {'need': 'minutes per order step', 'why': 'test', 'checked': ['RECEIPTS'], 'origin': 'a past Claude session'}
        drid, daid = assistant.new_item('probe SHIPLOG for step timestamps', 'Minutes per order step', noticed=noticed)
        arid, aaid = assistant.new_item('redo the call file', 'Redo the call file')
        irid, iaid = assistant.new_item('Jarvis server died. Find the cause.', 'Jarvis server died', desk=True)
        MADE.extend([drid, arid, irid])
        files = {r: json.load(open(os.path.join(s.tmp, 'roundups', r + '.json'))) for r in (drid, arid, irid)}
        routed = drid.endswith('-desk') and irid.endswith('-desk') and arid.endswith('-jarvis') and drid != irid and files[drid]['kind'] == 'desk' \
            and files[arid]['kind'] == 'jarvis' and files[drid]['actions'][0]['who'] == 'Jarvis (noticed)'
        needs = {'status': 'needs_you', 'summary': 'x', 'points': [{'key': 'a1.0', 'state': 'needs_you', 'note': 'answer this'}],
                 'reply': {'channel': 'slack', 'text': 'Hi Tomas, a nudge', 'channel_id': 'C1'}}
        for r in (drid, arid):
            s.work(r, 'a1', needs)
        ib = [r['id'] for r in server.inbox()['roundups']]
        pings = [k.split('|')[0] for k, _ in server.needs_now()]
        mcp = {rid for rid, *_ in mcp._items()}
        made = []
        o = (slack_send.needs_draft, slack_send._quiet, slack_send.make_draft)
        slack_send.needs_draft, slack_send._quiet = (lambda w, rid, aid: True), (lambda w: True)
        slack_send.make_draft = lambda rid, aid: made.append(rid) or {'ok': True}
        try:
            slack_send.sync_drafts()
        finally:
            slack_send.needs_draft, slack_send._quiet, slack_send.make_draft = o
        # tasks: one desk worker running, one asked worker running; both stuck (alerts)
        for tid, rid, origin in (('T-d', drid, 'initiative'), ('T-a', arid, 'owner')):
            ledger.upsert('tasks', {'id': tid, 'rid': rid, 'aid': 'a1', 'title': rid, 'kind': 'item', 'lane': 'background', 'origin': origin,
                                    'state': 'running', 'created': ledger.now(), 'started': ledger.now(), 'pid': os.getpid(), 'session': tid}, ('id',))
            ledger.event('task_stuck', tid, 'watchdog', {'idle_min': 30, 'title': rid})
        ledger.event('autopilot_shipped', 'ap-1', 'autopilot', {'title': 'a fix to itself'})
        tn = assistant.tasks_now()
        now = [t['rid'] for t in tn.get('running_all') or ([tn['running']] if tn.get('running') else [])]
        alerts = [a['rid'] or a['kind'] for a in server.task_alerts()]
        # heartbeat: a desk item never gets an inbox worker
        ledger.ingest_items()
        submitted = []
        from jarvis.spine import dispatcher
        o2 = dispatcher.submit
        dispatcher.submit = lambda *a, **k: submitted.append(a[2] if len(a) > 2 else k.get('rid')) or {'id': 'x'}
        try:
            hb = heartbeat.Heartbeat.__new__(heartbeat.Heartbeat)
            for r in (drid, irid):
                hb.new_item(r, 'a1')
        finally:
            dispatcher.submit = o2
        sweep_open = bool(ledger.q("SELECT 1 FROM items WHERE done=0 AND kind != 'desk' AND rid IN (?, ?)", (drid, irid)))
        ok = (routed and arid in ib and drid not in ib and irid not in ib and arid in pings and drid not in pings
              and arid in mcp and drid not in mcp and made == [arid] and arid in now and drid not in now
              and arid in alerts and drid not in alerts and 'improved' not in alerts and not submitted and not sweep_open)
        return ok, {'routed': routed, 'inbox': ib, 'pings': pings, 'brain': sorted(mcp), 'slack_drafts': made, 'working_now': now,
                    'alerts': alerts, 'heartbeat_submitted': submitted, 'sweep_sees_desk': sweep_open}


def collect():
    from jarvis import assistant
    from jarvis.spine import ledger
    from jarvis.world import desk, investigate, research
    with Scratch() as s:
        noticed = {'need': 'minutes per order step', 'why': 'test', 'checked': ['RECEIPTS'], 'origin': 'a past Claude session'}
        rid, aid = assistant.new_item('probe SHIPLOG', 'Minutes per order step', noticed=noticed)
        MADE.append(rid)
        s.work(rid, aid, {'status': 'done', 'summary': 'VERDICT: partly. SHIPLOG has pick and ship times for 61% of lines.',
                          'numbers': [{'what': 'lines with both dates', 'now': '61%'}],
                          'points': [{'key': 'a1.0', 'state': 'done', 'note': 'probed', 'answer': '61% of 2025 March lines',
                                      'sources': [{'type': 'data', 'ref': 'gs://.../SHIPLOG'}]}],
                          'done_work': [{'title': 'Result', 'path': '~/.jarvis/work/outputs/x/RESULT.md'}]})
        ledger.upsert('tasks', {'id': 'T-p', 'rid': rid, 'aid': aid, 'title': 't', 'kind': 'initiative', 'lane': 'background',
                                'origin': 'initiative', 'state': 'done', 'created': ledger.now()}, ('id',))
        ledger.upsert('initiatives', {'id': 'ini-p', 'created': ledger.now(), 'class': 'need_probe', 'title': 't', 'state': 'done',
                                      'task_id': 'T-p'}, ('id',))
        ledger.x(investigate.TABLE)
        ledger.upsert('investigations', {'id': 'inv-q', 'created': ledger.now(), 'goal': 'Batching gain vs single orders', 'why': 'w',
                                         'origin': 'goal', 'state': 'question', 'steps_done': 2, 'question': 'Which depots count as one batch?'}, ('id',))
        ledger.x(research.TABLE)
        ledger.upsert('research_findings', {'id': 'rf-1', 'created': ledger.now(), 'title': 'Accuracy vs service rank', 'url': 'https://x',
                                            'claim': 'c', 'applies': 'a', 'state': 'new'}, ('id',))
        # a noticed item from before the desk, and a mixed file (asked + noticed in the same minute)
        old = os.path.join(s.tmp, 'roundups', '2026-10-08-0909-jarvis.json')
        json.dump({'id': '2026-10-08-0909-jarvis', 'kind': 'jarvis', 'actions': [{'id': 'a1', 'src': 'noticed', 'noticed': noticed}]}, open(old, 'w'))
        mixed = os.path.join(s.tmp, 'roundups', '2026-10-08-0910-jarvis.json')
        json.dump({'id': '2026-10-08-0910-jarvis', 'kind': 'jarvis', 'actions': [{'id': 'a1', 'src': 'noticed', 'noticed': noticed},
                                                                                {'id': 'a2', 'src': 'jarvis'}]}, open(mixed, 'w'))
        import subprocess
        spawned, o = [], (subprocess.run, subprocess.Popen)
        subprocess.run = subprocess.Popen = lambda *a, **k: spawned.append(a[0] if a else k) or (_ for _ in ()).throw(RuntimeError('no process'))
        try:
            r1 = desk.collect()                         # no model call, no process: it only reads and files
        finally:
            subprocess.run, subprocess.Popen = o
        rows = {r['kind']: r for r in ledger.q('SELECT * FROM desk')}
        r2 = desk.collect()
        f = rows.get('finding') or {}
        refs = json.loads(f.get('refs') or '{}')
        ok = (not spawned and r1['collected'] == 3 and r2['collected'] == 0 and set(rows) == {'finding', 'question', 'research'}
              and refs.get('item') == f'{rid}|{aid}' and refs.get('verdict') == 'partly' and f['summary'].startswith('SHIPLOG has')
              and json.loads(rows['question']['refs'])['investigation'] == 'inv-q'
              and r1.get('migrated') == ['2026-10-08-0909-jarvis'] and json.load(open(mixed))['kind'] == 'jarvis'
              and desk.is_desk('2026-10-08-0909-jarvis') and not desk.is_desk('2026-10-08-0910-jarvis')
              and desk.counts()['open'] == 3)
        return ok, {'first': r1, 'second': r2, 'kinds': sorted(rows), 'refs': refs, 'summary': f.get('summary'),
                    'next_brief': desk.next_brief().isoformat()}


def tidy():
    import subprocess, types
    from jarvis.spine import ledger
    from jarvis.world import desk
    with Scratch() as s:
        desk.ensure()
        for i, (t, summ) in enumerate([('Minutes per order step', 'SHIPLOG times 4 of 5 steps; median 6.2 min per line'),
                                       ('Minutes per order step (again)', 'SHIPLOG times 4 of 5 steps; median 6.2 min per line'),
                                       ('Rush deliveries', 'Rush deliveries 2025: 37 ($96k)')]):
            desk._put(f'test:{i}', 'finding', t, summ)
        by_title = {r['title']: r['id'] for r in ledger.q('SELECT id, title FROM desk')}
        a, b, c = by_title['Minutes per order step'], by_title['Minutes per order step (again)'], by_title['Rush deliveries']
        stub = {'merge': [{'keep': a, 'drop': [b], 'why': 'same answer'}],
                'assign': [{'id': a, 'topic': 'ordering-cost'}, {'id': c, 'topic': 'late-cost'}],
                'rank': [{'id': a, 'score': 4, 'why': 'moves the ordering-cost goal'}, {'id': c, 'score': 5, 'why': 'prices an emergency'}],
                'topics': [{'slug': 'ordering-cost', 'title': 'Ordering cost', 'goal': 'g',
                            'position': [{'text': 'The system times 4 of 5 steps; median 6.2 min per line', 'src': [a]},
                                         {'text': 'That is $9.80 per line', 'src': [a]}],          # invented number: refused
                            'open_questions': [{'text': 'Invoice step not timed', 'src': [a]}], 'watch': []}]}
        r = desk.tidy(ask=lambda prompt: (stub, 0.0))
        body = open(os.path.join(desk.BRAIN, 'ordering-cost.md')).read()
        st = {x['id']: x for x in ledger.q('SELECT id, state, supersedes, rank, topic FROM desk')}
        again = desk.tidy(ask=lambda prompt: (stub, 0.0))
        # the real command: no tools, no connectors
        seen = {}
        o = subprocess.run
        subprocess.run = lambda cmd, **k: seen.update(cmd=cmd) or types.SimpleNamespace(stdout='{"structured_output": {}}')
        try:
            desk._ask_tidy('x')
        finally:
            subprocess.run = o
        cmd = seen.get('cmd') or []
        no_tools = cmd[cmd.index('--tools') + 1] == '' and '--strict-mcp-config' in cmd and '--allowedTools' not in cmd
        ok = (r['merged'] == 1 and r['lines_refused'] == 1 and st[b]['state'] == 'superseded' and st[b]['supersedes'] == a
              and st[a]['state'] == 'refined' and st[c]['rank'] == 5 and st[a]['topic'] == 'ordering-cost'
              and '6.2 min' in body and '9.80' not in body and f'(src: {a})' in body and again == 'nothing new' and no_tools)
        return ok, {'result': r, 'again': again, 'no_tools': no_tools, 'brief_head': body.split('## Open')[0][-160:]}


def fortnightly_brief():
    import datetime as dt
    from jarvis import assistant
    from jarvis.spine import ledger
    from jarvis.world import desk
    with Scratch() as s:
        desk.ensure(); desk._ensure_tidy_cols()
        noticed = {'need': 'x', 'why': 'w', 'checked': ['c'], 'origin': 'an investigation Jarvis ran by itself'}
        qrid, qaid = assistant.new_item('QUESTION: which sites pool?', 'Question: pooling', noticed=noticed)
        drid, daid = assistant.new_item('nudge Tomas', 'Nudge Tomas: lead times', noticed={**noticed, 'origin': 'a promise someone made you'})
        MADE.extend([qrid, drid])
        desk._put('t:f', 'finding', 'Rush deliveries priced', 'Rush deliveries 2025: 37 ($96k); one emergency about $1,163')
        desk._put('t:q', 'question', 'Batching vs single orders', 'Which depots count as one batch?', refs={'item': f'{qrid}|{qaid}'})
        desk._put('t:d', 'draft', 'Nudge Tomas: lead times', 'drafted', refs={'item': f'{drid}|{daid}'})
        fid = ledger.q("SELECT id FROM desk WHERE source_key='t:f'")[0]['id']
        ledger.x('UPDATE desk SET rank=5 WHERE id=?', (fid,))
        stub = {'headline': 'An emergency now has a price.',
                'matters': [{'title': 'Rush deliveries priced', 'finding': '37 runs in 2025 ($96k); one emergency about $1,163.', 'do': 'Use $1,163 in the policy.', 'src': [fid]},
                            {'title': 'Invented', 'finding': 'Saves $9,999 a year.', 'do': 'x', 'src': [fid]}],
                'picture': [], 'checked': []}
        ask = lambda prompt: (stub, 0.0)
        before = sorted(os.listdir(os.path.join(s.tmp, 'roundups')))
        dry = desk.brief(dry=True, today=dt.date(2026, 10, 23), ask=ask)
        dry_quiet = sorted(os.listdir(os.path.join(s.tmp, 'roundups'))) == before and not ledger.q("SELECT 1 FROM desk WHERE state='briefed'")
        md = open(dry['path']).read()
        pub = desk.brief(today=dt.date(2026, 10, 23), ask=ask)
        MADE.append(pub['item'].split('|')[0])
        from jarvis import server
        ib = [r['id'] for r in server.inbox()['roundups']]
        visible = [r for r in ib if not desk.is_desk(r)]
        st = {r['id']: r['state'] for r in ledger.q('SELECT id, state FROM desk')}
        not_due = desk.brief_due(dt.datetime(2026, 10, 9, 10, 0)) == 'not due' and desk.brief_due(dt.datetime(2026, 10, 23, 10, 0)) == 'not due'
        # his tick on the first finding marks it useful
        rid, aid = pub['item'].split('|')
        stp = os.path.join(s.tmp, 'roundups', 'state.json')
        json.dump({rid: {f'{aid}.0': True}}, open(stp, 'w'))
        liked = desk.reactions()
        react = ledger.q('SELECT reaction FROM desk WHERE id=?', (fid,))[0]['reaction']
        ok = (dry_quiet and '37 runs' in md and '9,999' not in md and len(dry['refused']) == 1 and pub['matters'] == 1
              and set(pub['released']) == {qrid, drid} and rid in visible and qrid in visible and drid in visible
              and st[fid] == 'briefed' and not_due and liked == 1 and react == 'useful'
              and ledger.q('SELECT state FROM desk WHERE id=?', (fid,))[0]['state'] == 'acknowledged')
        return ok, {'dry_quiet': dry_quiet, 'refused': dry['refused'], 'released': pub['released'], 'visible': visible,
                    'not_due_twice': not_due, 'useful': liked}


def depth_tickets():
    """The weekly review's depth misses become Level 5 tickets graded by a rubric, at most 2 a week, never without a rubric."""
    from jarvis.spine import autopilot as ap
    from jarvis import learned
    rub = 'It resolves which source is right and answers plainly.'
    sigs = [{'id': 10 + i, 'kind': 'review_bad', 'data': json.dumps({'q': q, 'answer': 'a read-back', 'rubric': r})}
            for i, (q, r) in enumerate([('is the shortage analysis running', rub), ('what did Tomas decide on lead times', rub),
                                        ('how much does pooling at the depot add', rub), ('which file has the asks', '')])]
    made, graders = [], []
    class L:
        def q(self, sql, *a): return [{'n': 0}] if 'count(*)' in sql else []
        def x(self, sql, args=()): made.append(args[2]) if sql.startswith('INSERT INTO autopilot') else None
    o = (learned.q, ap._db, ap._cursor, ap._add_case, ap._event)
    learned.q = lambda sql, *a: sigs if 'learn_signals' in sql else []
    ap._db, ap._cursor = (lambda: L()), (lambda v=None: 0 if v is None else v)
    ap._add_case, ap._event = (lambda tid, q, grader: graders.append([g['kind'] for g in grader]) or 'c'), (lambda *a, **k: None)
    try:
        ap.notice()
    finally:
        learned.q, ap._db, ap._cursor, ap._add_case, ap._event = o
    return made == ['shallow', 'shallow'] and all('rubric' in g for g in graders), {'tickets': made, 'graders': graders}


def ship_check_refuses_guards():
    """13 Nov 2026 (the policy gate of 12 Nov was changeable by the autopilot): the optimiser may not touch what guards
    outward actions or what judges it. The REAL ship check (autopilot._diff_ok) runs on a scratch git repo, never their:
    a branch that changes one guard is refused, whether the guard is edited, added new (policy.py is not committed in the
    live repo yet), hidden among allowed files, deleted or renamed away; the judges under tools/ are refused too; a branch
    that changes only the brain still passes, so the check is not refusing everything. The workers' deny list names the
    guards under both roots (the live folder and the fix worktrees), which stops a worker before the ship check."""
    import subprocess
    from jarvis.spine import autopilot as ap, dispatcher as d
    guards = ['jarvis/spine/policy.py'] + sorted(ap.PROTECTED - {'jarvis/spine/policy.py'})   # every file the ship check guards
    judges = ['tools/gate.py', 'tools/jarvis_eval.py', 'tools/phase7_proof.py', 'tools/policy_safety.py', 'tools/proof_home.py',
              'tools/worker_hook.py', 'tests/test_spine.py']
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')       # no hook or setting of his applies

    def git(*a):
        p = subprocess.run(['git', '-C', tmp, '-c', 'user.name=proof', '-c', 'user.email=proof@localhost',
                            '-c', 'core.hooksPath=' + os.devnull, *a], capture_output=True, text=True, timeout=60, env=env)
        if p.returncode:
            raise RuntimeError(f"git {' '.join(a)[:60]}: {(p.stderr or p.stdout)[-160:]}")

    def put(f, text='x = 2\n'):
        os.makedirs(os.path.join(tmp, os.path.dirname(f)), exist_ok=True)
        with open(os.path.join(tmp, f), 'a') as fh:
            fh.write(text)

    def shipped(name, change):
        """The ship check's answer for a branch off main that makes this one change."""
        git('checkout', '-q', '-b', name, 'main'); change(); git('add', '-A'); git('commit', '-q', '-m', name)
        git('checkout', '-q', 'main')
        return ap._diff_ok(name)

    old = ap.REPO
    try:
        git('init', '-q'); git('symbolic-ref', 'HEAD', 'refs/heads/main')
        for f in guards[1:] + judges + ['jarvis/assistant.py']:  # policy.py is left out of main: its branch adds it new
            put(f, ''.join(f'line_{i} = {i}\n' for i in range(40)))
        git('add', '-A'); git('commit', '-q', '-m', 'base')
        ap.REPO = tmp                                            # autopilot._git reads REPO at call time
        wrong, n = [], 0
        cases = [(f'edit {f}', (lambda f=f: put(f)), f) for f in guards + judges]
        cases += [('guard hidden among allowed files', lambda: (put('jarvis/assistant.py'), put('jarvis/spine/policy.py')), 'jarvis/spine/policy.py'),
                  ('guard deleted', lambda: os.remove(os.path.join(tmp, 'jarvis/slack_send.py')), 'jarvis/slack_send.py'),
                  ('guard renamed away', lambda: git('mv', 'jarvis/spine/gateway.py', 'jarvis/spine/gateway_old.py'), 'jarvis/spine/gateway.py')]
        for i, (what, change, named) in enumerate(cases):
            ok, why = shipped(f'b{i}', change)
            n += 1
            if ok or 'may not change' not in why or named not in why:     # 'may not change' also means: no second attempt
                wrong.append(f'{what}: {why}')
        control = shipped('ok', lambda: put('jarvis/assistant.py'))
    finally:
        ap.REPO = old
        shutil.rmtree(tmp, ignore_errors=True)
    deny = d.worker_settings()['permissions']['deny']
    # every file the ship check protects, and every judge, is also on the workers' deny list, under the repository and under
    # the worktrees its own changes are made in
    listed = sorted(ap.PROTECTED) + list(ap.JUDGES)
    roots = (d._rule(paths.REPO), d._rule(paths.worktree('ap-*')))
    missing = [f'{root}/{f}' for root in roots for f in listed if f'Edit({root}/{f})' not in deny]
    still_open = [f for f in ('jarvis/spine/limits.py', 'jarvis/assistant.py') if not ap.ALLOWED.match(f) or f in ap.PROTECTED]
    ok = not wrong and control == (True, '1 files, 1 lines') and not missing and not still_open and n == len(guards) + len(judges) + 3
    return ok, {'refused': n - len(wrong), 'of': n, 'wrongly_passed': wrong, 'brain_only_change': control, 'deny_list_missing': missing,
                'wrongly_closed': still_open}


def generic_writes():
    import subprocess
    hook = str(paths.tool('worker_hook.py'))
    def rc(tool):
        # each call as its own task: three refusals in a row would (rightly) stop the worker instead of refusing again
        tid = 'proof7-' + tool.split('__')[-1] + '-' + str(os.getpid())
        r = subprocess.run([paths.python(), hook, 'pre'], input=json.dumps({'tool_name': tool, 'tool_input': {}}), text=True,
                           capture_output=True, env=dict(os.environ, JARVIS_PROC='1', JARVIS_LANE='background', JARVIS_TASK_ID=tid)).returncode
        try:
            os.remove(os.path.join(H, 'state', 'denials', tid + '.json'))
        except OSError:
            pass
        return r
    refused = [rc(t) for t in ('mcp__claude_ai_Atlassian__executeWrite', 'mcp__claude_ai_Atlassian__executeDestructive', 'mcp__any__delete_page')]
    allowed = [rc(t) for t in ('mcp__claude_ai_Atlassian__executeRead', 'mcp__claude_ai_Atlassian_Rovo__getJiraIssue')]
    return all(r == 2 for r in refused) and all(a == 0 for a in allowed), {'refused': refused, 'allowed': allowed}


def schedule():
    import datetime as dt
    from jarvis.world import desk
    got = [desk.next_brief(dt.date(2026, 10, d)).isoformat() for d in (9, 23, 24)] + [desk.next_brief(dt.date(2026, 11, 6)).isoformat(),
                                                                                      desk.next_brief(dt.date(2026, 11, 7)).isoformat()]
    want = ['2026-10-23', '2026-10-23', '2026-11-06', '2026-11-06', '2026-11-20']
    return got == want, got


if __name__ == '__main__':
    case('desk: noticed work never reaches them; asked work still does (8 routes)', desk_routing)
    case('desk: finished desk work becomes one entry each with refs; collecting twice adds nothing; old items re-kinded', collect)
    case('desk: tidy merges duplicates, ranks, writes topic briefs and refuses a number its source lacks; no tools', tidy)
    case('desk: the brief is every second Friday from 23 Oct', schedule)
    case('brief: dry run changes nothing; publishing makes one item, releases questions and drafts, refuses an untraced number; once; ticks = useful', fortnightly_brief)
    case('autopilot: depth misses become rubric-graded tickets, at most 2 a week, never without a rubric', depth_tickets)
    case('autopilot: the ship check refuses the policy gate, the gateway, the dispatcher, the governor, learned and the send path, and its judges; workers are denied them', ship_check_refuses_guards)
    case('hook: a connector\'s generic write/delete tools are refused for every worker; reads pass', generic_writes)
    case('isolation: nothing the cases made reached their real round-ups or ledger', _untouched)
    n = sum(1 for _, ok, _ in results if ok)
    print(f'\n{n}/{len(results)} passed')
    proof_home.save('phase7_proofs', [{'case': a, 'ok': b, 'detail': c} for a, b, c in results])
    sys.exit(0 if n == len(results) else 1)
