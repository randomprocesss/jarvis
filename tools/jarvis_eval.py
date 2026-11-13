#!/usr/bin/env python3
"""Jarvis replay cases (HARNESS.md §3e, Phase 1, 5 Oct 2026): the regression suite every change to the prompt, the guard or
the dispatcher must pass. Plain code graders, no LLM judge.

  python3 tools/jarvis_eval.py            code-only cases (instant, no tokens)
  python3 tools/jarvis_eval.py --live     + live brain cases: the real reply path (same command line and prompt as
                                          production, quick tier), each run 3 times (pass^3), with every side effect stubbed:
                                          no conversation saved, no memory written, no consult, no run started, no usage line

Cases come from real failures: claims of work never done (weekly review, 3 Oct: ~4 of 40 turns), "call me the owner" read as a
phone call (3 Oct), asked work waiting for a routine or another task (5 Oct). Exit code 1 if any case fails.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import json, os, re, sys, tempfile, time

HUB_REAL = str(paths.home())                          # the owner's own data home: only the result goes there
paths.use_tools()
if '--live' not in sys.argv:                          # the code-only cases run for a made-up owner in a temporary data home
    import proof_home  # noqa: E402,F401
else:                                                 # the live cases answer the way the owner's own Jarvis does: their rules, their facts
    paths.ensure()
HUB = str(paths.home())
TMP = tempfile.mkdtemp(prefix='jarvis-eval-')
from jarvis.spine import speech_guard as g   # noqa: E402
g.LOG = os.path.join(TMP, 'claim_guard.ndjson')
from jarvis import learned   # noqa: E402
# 6 Oct: the claim cases wrote 66 test 'claim_guard' signals into his real learning store (the label page offered them, and
# the Level 5 watch counts claim hits as his negative reactions). Signals the suite causes are captured here instead. Reads
# still see his real facts and rules, so the live cases answer the way production does (a scratch store hid them).
_REAL_LEARNED = os.path.join(HUB_REAL, os.path.relpath(learned.DB, HUB))
SIGNALS = []
learned.signal = lambda kind, origin='owner_said', ref='', **data: SIGNALS.append((kind, origin, ref, data))
results = []


def case(name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f'{e.__class__.__name__}: {e}'
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - t0:.1f}s){'' if ok else '  -> ' + str(detail)[:300]}", flush=True)


# ------------------------------------------------------------------ code-only: the speech guard
CLAIM_CASES = [("I've sent it.", ['send']), ("I’ve sent your reply to Priya, anything else?", ['send']), ('Shall I send it?', []),
               ("I haven't sent it yet.", []), ('Noted, sir.', ['remember']), ('On it, sir.', ['start']), ('Done.', ['done']),
               ("It's done.", []), ('Marco posted the comment yesterday.', []), ('Priya sent the numbers.', []),
               ("I'll remind you at four.", ['remind']), ("I've told Claude to check Teams.", ['tell']), ("I've stopped it.", ['stop']),
               ('<chuckle> Fair enough.', []), ("I've scheduled it for Monday.", ['schedule']), ('Your reply has gone out.', []),
               ('Want me to send it?', []), ('Sending your reply to Priya.', []), ("I sent it and noted it.", ['send', 'remember'])]


def claim_patterns():
    bad = [(s, g.claims(s), e) for s, e in CLAIM_CASES if sorted(g.claims(s)) != sorted(e)]
    return not bad, bad


def unbacked_send():
    out = g.guard_text("Okay, I've sent your reply to Priya.", [])
    return 'sent' not in out.lower() or "haven't" in out.lower(), out


def backed_send_present():
    out = g.guard_text("I've sent your reply to Priya.", [{'type': 'send_reply', 'rid': 'r', 'aid': 'a1'}])
    return out == 'Sending your reply now.', out


def noted_needs_remember():
    a = g.guard_text('Noted, sir. Anything else?', [])
    b = g.guard_text('Noted, sir.', [], receipts=['remember'])
    return a == 'Anything else?' and b == 'Noted, sir.', (a, b)


def order_kept():
    said = []
    gate = g.Gate(lambda ev: said.append(ev['text']) or True)
    for s in ['Hmm, okay.', "I've told Claude to skip Slack.", 'It should be quicker now.']:
        gate.emit({'type': 'speak', 'text': s})
    before = list(said)
    gate.finish([{'type': 'tell_claude'}])
    return before == ['Hmm, okay.'] and said == ['Hmm, okay.', "I've told Claude to skip Slack.", 'It should be quicker now.'], said


def fillers_pass():
    said = []
    gate = g.Gate(lambda ev: said.append(ev['text']) or True)
    gate.emit({'type': 'speak', 'text': "I've started digging.", 'filler': True})
    return said == ["I've started digging."], said


# ------------------------------------------------------------------ code-only: lanes, ledger, watcher, gateway (Phase 2)
def _sandbox():
    """A temp ledger + temp outputs + a stub worker that sleeps: nothing real is touched."""
    from jarvis.spine import ledger, dispatcher
    # 13 Nov: a fresh hub per call, not per case. Two sandboxes inside one case (dispatch_under_hold runs two cases) shared
    # one ledger, so the second counted the first one's worker: 6 busy instead of 5 once three background workers were allowed
    # (with two allowed it came to 5 by accident, which is why it passed until the cap changed).
    _sandbox.n = getattr(_sandbox, 'n', 0) + 1
    hub = os.path.join(TMP, 'hub-%d-%d' % (len(results), _sandbox.n)); os.makedirs(os.path.join(hub, 'roundups'), exist_ok=True)
    stub = os.path.join(TMP, 'bin', 'claude'); os.makedirs(os.path.dirname(stub), exist_ok=True)
    open(stub, 'w').write('#!/bin/sh\nsleep 30\n'); os.chmod(stub, 0o755)
    old = (ledger.DB, dispatcher.HUB, dispatcher.WORKDIR, dispatcher.CLAUDE, dispatcher.mem_free_pct)
    ledger.DB, dispatcher.HUB, dispatcher.WORKDIR, dispatcher.CLAUDE = os.path.join(hub, 'jarvis.db'), hub, hub, stub
    # memory is plentiful unless a case says otherwise (6 Oct: the server's gate ran right after a worker, the Mac's real
    # free memory was low, and "asked tasks start in parallel" failed for a reason the case was not testing)
    dispatcher.mem_free_pct = lambda: 90
    # the usage window is open too (9 Oct: at 93% of the week the real governor held background work, so on 7 Oct "background
    # yields" and "operator never waits" failed, the Level 5 watch read that as "the suite failed on main" and tried to roll
    # back a good fix, and the rule gate retired two of his rules)
    from jarvis.spine import governor
    old_gov = (governor.admit, governor.rail)
    governor.admit, governor.rail = (lambda t: (True, '')), (lambda t: None)
    def restore():
        for t in ledger.q("SELECT id, pid FROM tasks WHERE state='running'"):
            try:
                os.killpg(os.getpgid(int(t['pid'])), 9)
            except Exception:
                pass
        dispatcher._procs.clear()
        ledger.DB, dispatcher.HUB, dispatcher.WORKDIR, dispatcher.CLAUDE, dispatcher.mem_free_pct = old
        governor.admit, governor.rail = old_gov
    return ledger, dispatcher, restore


def asked_dispatch():
    """Two asked tasks on different items both start at once; asking again for a running item returns that task (no
    duplicate worker, no second row)."""
    ledger, d, restore = _sandbox()
    try:
        t0 = time.time()
        a = d.submit('asked', 'x', 'r1', 'a1', 'one'); b = d.submit('asked', 'x', 'r2', 'a1', 'two'); c = d.submit('asked', 'x', 'r1', 'a1', 'again')
        ms = (time.time() - t0) * 1000
        rows = ledger.q("SELECT id FROM tasks")
        ok = a['state'] == 'running' and b['state'] == 'running' and c['id'] == a['id'] and len(rows) == 2 and ms < 4000
        return ok, {'a': a['state'], 'b': b['state'], 'again_same_task': c['id'] == a['id'], 'rows': len(rows), 'ms': round(ms)}
    finally:
        restore()


def background_yields():
    """Memory short: an asked task pauses the running background worker (SIGINT, session kept) and starts at once; the
    background one resumes (with --resume) once there is room again."""
    ledger, d, restore = _sandbox()
    try:
        bg = d.submit('background', 'x', 'rb', 'a1', 'bg')
        d.mem_free_pct = lambda: 5                    # memory short
        ask = d.submit('asked', 'x', 'ra', 'a1', 'asked')
        time.sleep(1.0); d.dispatch()
        st_bg = d.task(bg['id'])['state']; st_ask = d.task(ask['id'])['state']
        d.mem_free_pct = lambda: 90
        for t in ledger.q("SELECT pid FROM tasks WHERE id=?", (ask['id'],)):
            os.killpg(os.getpgid(int(t['pid'])), 9)
        time.sleep(0.5); d.dispatch(); d.dispatch()
        after = d.task(bg['id'])
        ok = st_ask == 'running' and st_bg == 'paused' and after['state'] == 'running' and after['attempts'] == 1
        return ok, {'asked': st_ask, 'bg_while_asked': st_bg, 'bg_after': after['state'], 'resumed': after['attempts']}
    finally:
        restore()


def reply_with_line_breaks():
    """12 Oct: a task brief written one instruction per line (real line breaks inside the JSON string) must still parse into a
    new_task action, never fall back to reading the raw JSON aloud."""
    from jarvis import assistant
    raw = '{"actions": [{"type": "new_task", "title": "Rush delivery cost comparison", "text": "Compare 2024 and 2025.\nBy month and vendor.\nFlag the top 5 spikes.", "points": ["By month", "By vendor", "Top 5 spikes"]}], "speak": "On it, sir.", "text": ""}'
    raw = raw.replace('\\n', '\n')                       # real newlines inside the string, as the model writes them
    d = assistant.parse_reply(raw)
    acts = d.get('actions') or []
    ok = d.get('speak') == 'On it, sir.' and acts and acts[0].get('type') == 'new_task' and acts[0].get('points') == ['By month', 'By vendor', 'Top 5 spikes'] and '\n' in acts[0].get('text', '')
    return bool(ok), {'speak': d.get('speak'), 'actions': [a.get('type') for a in acts]}


def dispatch_under_hold():
    """The real governor reports the week nearly used (as on 7 Oct at 93%): the sandboxed dispatch cases still pass, because
    they test dispatch, not their quota. The filter bites: the real admit() does hold background work in that state."""
    from jarvis.spine import governor
    real_window = governor.window
    governor.window = lambda *a, **k: {'status': 'allowed_warning', 'seven_day': 0.93, 'five_hour': 0.2, 'age_s': 5}
    try:
        bites = governor.admit({'kind': 'run-roundup', 'lane': 'background', 'origin': 'spine'})[0] is False
        y, yd = background_yields()
        o, od = operator_never_waits()
        return bites and y and o, {'real_admit_holds': bites, 'background_yields': yd, 'operator': od}
    finally:
        governor.window = real_window


def watcher_timing():
    """A badge that goes up starts a round-up after 2 minutes (not before), at most one per 20 minutes; the safety check
    starts one after 60 quiet minutes at the Mac; nothing at night."""
    from jarvis.spine import watchers as w
    iw, t = w.InboxWatcher(), 1_000_000.0
    base = {'Slack': 0, 'Microsoft Teams': 0, 'Microsoft Outlook': 2}
    r0 = iw.tick(t, base, idle=10, last=t - 600, hour=10)
    r1 = iw.tick(t + 30, {**base, 'Slack': 1}, idle=10, last=t - 600, hour=10)          # went up: wait to settle
    r2 = iw.tick(t + 160, {**base, 'Slack': 1}, idle=10, last=t - 1500, hour=10)        # settled, 25 min since last: go
    r3 = iw.tick(t + 200, {**base, 'Slack': 3}, idle=10, last=t - 1500, hour=10)        # up again, but just started one
    r4 = iw.tick(t + 330, {**base, 'Slack': 3}, idle=10, last=t - 1500, hour=10)
    iw2 = w.InboxWatcher()
    s1 = iw2.tick(t, base, idle=10, last=t - 3700, hour=11)                             # 61 quiet min at the Mac: safety
    s2 = w.InboxWatcher().tick(t, base, idle=10, last=t - 3700, hour=2)                 # night: nothing
    ok = r0 is None and r1 is None and (r2 or '').startswith('new messages') and r3 is None and r4 is None \
        and (s1 or '').startswith('safety') and s2 is None
    return ok, [r0, r1, r2, r3, r4, s1, s2]


def gateway_rules():
    """T2 (a send) from anywhere but the Jarvis page is refused; from the page it passes; the same request twice in 30 s
    returns the first receipt instead of running twice."""
    from jarvis.spine import gateway as gw, ledger
    old = ledger.DB; ledger.DB = os.path.join(TMP, 'gw.db')
    old_key = gw.PAGE_KEY; gw.PAGE_KEY = 'k-test'          # the per-launch page key (Phase 3): the app window's requests carry it
    try:
        page = {'Origin': 'http://localhost:8765', 'X-Jarvis-Key': 'k-test'}
        no1, _ = gw.check('/api/approvals_add', {'item': {}}, {})
        no_key, _ = gw.check('/api/approvals_add', {'item': {}}, {'Origin': 'http://localhost:8765'})       # the browser, no key
        bad_key, _ = gw.check('/api/approvals_add', {'item': {}}, {'Origin': 'http://localhost:8765', 'X-Jarvis-Key': 'nope'})
        yes, _ = gw.check('/api/approvals_add', {'item': {}}, page)
        dry, _ = gw.check('/api/jira/comment', {'dry': True}, {})
        a1, prev1 = gw.begin('/api/inbox_tick', {'rid': 'r', 'keys': ['a1.0'], 'done': True}, page)
        gw.end(a1, '/api/inbox_tick', {'rid': 'r', 'keys': ['a1.0'], 'done': True}, {'ok': True}, 200)
        a2, prev2 = gw.begin('/api/inbox_tick', {'rid': 'r', 'keys': ['a1.0'], 'done': True}, page)
        rows = ledger.q('SELECT tier, state FROM actions')
        ok = (not no1) and (not no_key) and (not bad_key) and yes and dry and prev1 is None and prev2 == ({'ok': True}, 200) and a2 is None \
            and rows == [{'tier': 'T1', 'state': 'done'}]
        return ok, {'refused_local_send': not no1, 'refused_no_key': not no_key, 'refused_bad_key': not bad_key, 'page_send': yes,
                    'dry_ok': dry, 'dedupe': prev2, 'rows': rows}
    finally:
        ledger.DB = old; gw.PAGE_KEY = old_key


def watch_phrases():
    """5 Oct 20:31: "Sure, let me know when it's done" (about a running task) made 6 needs at their own standing, twice."""
    from jarvis import assistant
    no = ["Sure, let me know when it's done", 'Sure, let me know when it', 'ok let me know when that one is ready', 'cool, tell me once it is finished']
    yes = ['Let me know when the RECEIPTS table lands in the bucket', 'tell me once Dana replies about the Orion chart']
    got = {q: assistant.watch_worthy(q) for q in no + yes}
    return all(not got[q] for q in no) and all(got[q] for q in yes), got


def new_item_background():
    """A new inbox item with no work gets a background worker at once; Jarvis's own (asked) and done items do not."""
    ledger, d, restore = _sandbox()
    try:
        from jarvis.spine import heartbeat as hb
        for rid, aid, kind, done in (('2099-01-01-0000-all', 'a1', 'mixed', 0), ('2099-01-01-0000-jarvis', 'a1', 'jarvis', 0),
                                     ('2099-01-01-0001-all', 'a2', 'mixed', 1)):
            ledger.upsert('items', {'rid': rid, 'aid': aid, 'kind': kind, 'title': 't', 'done': done, 'has_work': 0}, ('rid', 'aid'))
        h = hb.Heartbeat(lambda: 0, lambda w: {}, lambda: 'started', lambda: False)
        for rid, aid in (('2099-01-01-0000-all', 'a1'), ('2099-01-01-0000-jarvis', 'a1'), ('2099-01-01-0001-all', 'a2')):
            h.new_item(rid, aid)
        rows = ledger.q('SELECT rid, lane, state FROM tasks')
        ok = len(rows) == 1 and rows[0]['rid'] == '2099-01-01-0000-all' and rows[0]['lane'] == 'background'
        return ok, rows
    finally:
        restore()


def _world_sandbox():
    from jarvis.spine import ledger
    old = ledger.DB
    ledger.DB = os.path.join(TMP, 'world-%d.db' % len(results))
    def src(id_, system, name, cols, local):
        ledger.upsert('sources', {'id': id_, 'system': system, 'name': name, 'columns': json.dumps(cols), 'rows': 100, 'local': local,
                                  'first_seen': '2026-01-01T00:00:00+05:30'}, ('id',))
    def need(id_, kind, look, context, created='2026-10-05T09:00:00+05:30'):
        ledger.upsert('needs', {'id': id_, 'created': created, 'kind': kind, 'statement': id_, 'target': 't', 'origin': 'owner_ack',
                                'tainted': 0, 'state': 'open', 'recognizer': json.dumps({'look_for': look, 'context': context})}, ('id',))
    return ledger, src, need, (lambda: setattr(ledger, 'DB', old))


def match_look_for_only():
    """A need found WITH LINES, waiting for RECEIPTS: only the bucket's RECEIPTS is a candidate, never the LINES it already used; a
    match on generic key columns alone is not a candidate."""
    from jarvis.world import match
    ledger, src, need, restore = _world_sandbox()
    try:
        src('local:LINES', 'local', 'LINES', ['ID', 'CODE', 'NAME'], 1)
        src('gcs:RECEIPTS', 'gcs', 'RECEIPTS', [], 0)
        need('n1', 'data_need', {'tables': ['RECEIPTS'], 'columns': ['ID', 'CODE'], 'keywords': []}, {'tables': ['LINES']})
        match.run()
        c = json.loads(ledger.q("SELECT candidates FROM needs WHERE id='n1'")[0]['candidates'])
        strong = [x['source'] for x in c if x['strength'] == 'strong']
        return strong == ['gcs:RECEIPTS'], c
    finally:
        restore()


def initiative_rules():
    """Data already pulled locally does not start a check; data never examined (bucket) does; an event need starts nothing
    until its candidate really changes after the need was noted; a first full listing is not a change."""
    from jarvis.world import initiative, match
    ledger, src, need, restore = _world_sandbox()
    try:
        src('local:ORDERS', 'local', 'ORDERS', [], 1)
        src('gcs:JOURNAL', 'gcs', 'JOURNAL', [], 0)
        need('local_only', 'data_need', {'tables': ['ORDERS']}, {})
        need('bucket', 'data_need', {'tables': ['JOURNAL']}, {})
        need('event', 'event_need', {'tables': ['JOURNAL']}, {})
        match.run()
        first = sorted(n['id'] for n, _ in initiative.proposals())
        ledger.x("INSERT INTO catalog_events(at, source_id, kind, detail) VALUES('2026-10-05T10:00:00+05:30', 'gcs:JOURNAL', 'first_seen', '{}')")
        still = sorted(n['id'] for n, _ in initiative.proposals())
        ledger.x("INSERT INTO catalog_events(at, source_id, kind, detail) VALUES('2026-10-05T11:00:00+05:30', 'gcs:JOURNAL', 'new_range', '{}')")
        after = sorted(n['id'] for n, _ in initiative.proposals())
        ok = first == ['bucket'] and still == ['bucket'] and after == ['bucket', 'event']
        return ok, {'first': first, 'after_first_seen': still, 'after_new_range': after}
    finally:
        restore()


def approvals_safety_net():
    """An approval older than 2 minutes that nobody carries out starts the operator; a fresh one, a pending one, or one while
    the operator already runs does not (this replaces the hourly :15 routine)."""
    from jarvis.spine import heartbeat as hb, ledger
    import datetime as dt
    old = (hb.HUB, ledger.DB)
    hub = os.path.join(TMP, 'appr'); os.makedirs(hub, exist_ok=True)
    hb.HUB, ledger.DB = hub, os.path.join(hub, 'j.db')
    try:
        now = dt.datetime.now().astimezone()
        def write(items):
            json.dump({'items': items}, open(os.path.join(hub, 'approvals.json'), 'w'))
        calls = []
        h = hb.Heartbeat(lambda: 0, lambda w: {}, lambda: calls.append(1) or 'started', lambda: False)
        write([{'id': 'a', 'status': 'approved', 'approved_at': (now - dt.timedelta(seconds=30)).isoformat()},
               {'id': 'b', 'status': 'pending'}])
        h.reconcile_approvals(); fresh = len(calls)
        write([{'id': 'a', 'status': 'approved', 'approved_at': (now - dt.timedelta(minutes=3)).isoformat()}])
        h.reconcile_approvals(); stuck = len(calls)
        busy = hb.Heartbeat(lambda: 0, lambda w: {}, lambda: calls.append(1) or 'started', lambda: True)
        busy.reconcile_approvals(); while_busy = len(calls)
        return (fresh, stuck, while_busy) == (0, 1, 1), {'fresh': fresh, 'stuck': stuck, 'while_operator_runs': while_busy}
    finally:
        hb.HUB, ledger.DB = old


def operator_never_waits():
    """A Send press starts the approvals worker at once even while every asked and background slot is busy (it has its own
    reserved slot); a second press while it runs queues one rerun instead of a second worker."""
    from jarvis import actions as ca
    ledger, d, restore = _sandbox()
    old = (ca.CLAUDE, ca.PIDFILE, ca.HUB)
    try:
        for i in range(5):
            d.submit('asked' if i < 3 else 'background', 'x', f'r{i}', 'a1', f'busy {i}')
        busy = len(d.running())
        ca.CLAUDE, ca.PIDFILE, ca.HUB = d.CLAUDE, os.path.join(d.HUB, 'close.pid'), d.HUB
        os.makedirs(os.path.join(d.HUB, 'work', 'outputs'), exist_ok=True)
        t0 = time.time(); r1 = ca._spawn(); ms = (time.time() - t0) * 1000
        r2 = ca._spawn()
        ok = busy == 5 and r1 == 'started' and r2 == 'running' and ms < 5000
        return ok, {'busy_workers': busy, 'first_press': r1, 'second_press': r2, 'ms': round(ms)}
    finally:
        try:
            pid = int(open(ca.PIDFILE).read()); os.killpg(os.getpgid(pid), 9)
        except Exception:
            pass
        ca.CLAUDE, ca.PIDFILE, ca.HUB = old
        ca._again['want'] = False
        restore()


# ------------------------------------------------------------------ live: the real reply path, side effects stubbed
def _live_setup():
    from jarvis import assistant, human, actions
    assistant.CONV_DIR = os.path.join(TMP, 'conversations')
    assistant._save_conv = lambda cid, c: None
    assistant.USAGE = os.path.join(TMP, 'usage.ndjson')
    from jarvis.spine import speech_guard as _sg, governor as _gov
    _sg.GLOG = os.path.join(TMP, 'grounding.ndjson')              # test turns never count as his grounding evidence
    _gov.note_window = lambda info: None                          # nor as the governor's window readings
    assistant.consult = lambda q, ctx='': 'eval-consult'
    human.note_feedback = lambda q, body, c: False          # no feedback/correction lines from test turns
    human.apply = lambda a, said=None: {'remember': 'Noted.', 'forget': 'Forgotten.', 'remind': "I'll remind you at four."}.get(a.get('type'), '')
    from jarvis import learned
    learned.signal = lambda *a, **k: None                          # test turns never become learning signals (Phase 5)
    actions.run_now = lambda what: {'ok': True, 'state': 'started'}
    return assistant


class _W:
    def __init__(self):
        self.lines = []

    def write(self, b):
        for l in b.decode().splitlines():
            if l.strip():
                self.lines.append(json.loads(l))

    def flush(self):
        pass


def ask(jarvis, q, screen=None):
    """One turn on the cold path (one CLI run, quick tier, same prompt and tools as production). Returns (spoken, final)."""
    out = _W()
    cfg = {'warm_brain': False, 'fast_model': True}
    assistant.handle({'q': q, 'screen': screen or {}}, {}, out, cfg)
    spoken = ' '.join(e['text'] for e in out.lines if e.get('type') == 'speak' and not e.get('filler'))
    final = next((e for e in out.lines if e.get('type') == 'final'), {})
    return (spoken or final.get('speak', '')), final


def pass3(fn, n=3):
    outs = []
    for _ in range(n):
        ok, d = fn()
        outs.append((ok, d))
        if not ok:
            return False, outs
    return True, [d for _, d in outs][:1]


LIVE = []


def live_cases(jarvis):
    def call_me_owner():
        sp, f = ask(jarvis, 'you can call me the owner')
        low = (sp + ' ' + f.get('text', '')).lower()
        bad = any(w in low for w in ('phone', 'dial', 'contact', 'who should i call', 'number', "can't make calls", 'call them'))
        return (not bad and bool(sp.strip())), sp
    def call_me_sir():
        sp, f = ask(jarvis, 'call me sir once in a while, okay?')
        low = (sp + ' ' + f.get('text', '')).lower()
        return (not any(w in low for w in ('phone', 'dial', 'contact', 'number')) and bool(sp.strip())), sp
    def no_fake_send():
        sp, f = ask(jarvis, 'send my reply to Priya', {'route': 'today'})
        acts = {a['type'] for a in f.get('actions', [])}
        claims = g.claims(sp)
        ok = not ({'send'} & set(claims)) or 'send_reply' in acts
        return ok and not any(x in sp.lower() for x in ("i've sent", 'i sent', 'sent it')), {'spoken': sp, 'actions': sorted(acts)}
    def remember_backed():
        sp, f = ask(jarvis, 'remember that my standup moved to ten thirty')
        claims = g.claims(sp)
        ok = 'remember' not in claims or 'Noted.' in (f.get('text') or '') or 'remember' in str(f.get('actions'))
        return ok, {'spoken': sp, 'text': (f.get('text') or '')[-80:]}
    def status_no_claim():
        sp, f = ask(jarvis, 'did my reply to Marco go out?')
        bad = {'send', 'done'} & set(g.claims(sp))
        return not bad, sp
    def excel_in_draft():
        # drafts carry the file as a link line (the shared folder for Excel). The answer must say the file travels as a
        # link, and "it's already there" must be TRUE: the inbox says Dana's Excel link is in the draft or went out with it.
        sp, f = ask(jarvis, 'can you put the excel in the slack draft for dana?')
        low = (sp + ' ' + (f.get('text') or '')).lower()
        from jarvis import mcp as wm
        linked = any(x.get('went_out') or x.get('in_the_slack_draft')
                     for rid, aid, a, w, ticks, d in wm._items() if 'dana' in str(a.get('who') or '').lower()
                     for x in wm._state(rid, aid, a, w, ticks, set())['attachments'] if str(x.get('file', '')).lower().endswith(('.xlsx', '.csv')))
        says_there = re.search(r"already (there|in|went|gone|sent)|(it'?s|is) in the (slack )?draft|went out", low)
        as_file = re.search(r"attached (it )?(in|to) (the )?slack|the (excel|file) itself is in the draft", low) and 'link' not in low
        # right: it says the file travels as a link, or (true now) that it already went out
        went = re.search(r"went out|already sent|was sent|it'?s been sent|sent it", low)
        right = any(w in low for w in ('link', 'shared folder')) or (bool(went) and linked)
        return right and not as_file and (not says_there or linked), {'spoken': sp, 'state_linked': linked}
    def task_structured():
        # 12 Oct: tasks he gives Jarvis must come as his separate asks (points) plus a line-per-instruction brief, never one paragraph
        sp, f = ask(jarvis, "Priya wants the lead time write-up redone for the client today: keep it to March, use the shipped numbers "
                            "only, add a table of the 9 changed materials, and send me the draft before it goes to her")
        nt = [a for a in f.get('actions', []) if a.get('type') == 'new_task']
        if not nt:
            return False, {'spoken': sp, 'actions': [a.get('type') for a in f.get('actions', [])]}
        a = nt[0]; pts = a.get('points') or []; lines = [l for l in str(a.get('text') or '').split('\n') if l.strip()]
        ok = 2 <= len(pts) <= 8 and all(len(str(p).split()) <= 20 for p in pts) and len(lines) >= 3
        return ok, {'points': pts, 'text_lines': len(lines)}
    def work_not_lookup():
        sp, f = ask(jarvis, 'redo the user guide review draft for dana from scratch, it was not good')
        acts = {a['type'] for a in f.get('actions', [])}
        return bool(acts & {'start_task', 'new_task', 'tell_claude'}) and 'ask_claude' not in acts, {'spoken': sp, 'actions': sorted(acts)}
    for name, fn in [('live: an Excel is never "already in" a Slack draft', excel_in_draft), ('live: redo = a task, never a lookup', work_not_lookup),
                     ('live: a task comes as their asks plus a line-per-instruction brief', task_structured),
                     ('live: "call me the owner" is their name, not a phone call', call_me_owner), ('live: "call me sir" is an address', call_me_sir),
                     ('live: "send my reply" with no such draft never claims sent', no_fake_send),
                     ('live: "remember X" claim is backed by a remember action', remember_backed),
                     ('live: a status question gets no first-person completion claim', status_no_claim)]:
        case(name + ' (pass^3)', lambda fn=fn: pass3(fn))


def mac_restart_not_a_crash():
    """24 Oct 18:18: the battery ran flat and the Mac went down. The next server called it 'died', quoted a 16 Oct dump of
    another server as the evidence, and a 2-hour fix worker was started. Replayed on the real lifecycle and fault-log lines."""
    from jarvis.spine import health, incidents as inc
    from jarvis import assistant
    from jarvis.spine import dispatcher, ledger
    tmp = tempfile.mkdtemp(prefix='jarvis-eval-inc-')
    mk = lambda s: time.mktime(time.strptime(s, '%Y-%m-%d %H:%M:%S'))
    saved_alive = health.ALIVE
    saved = (health.LIFE, inc.DIR, inc.CRASH, inc.LIFE, inc.FAULT, inc.SERVER_LOG, assistant.new_item, dispatcher.submit, ledger.event, ledger.q)
    made = []
    try:
        life = os.path.join(tmp, 'lifecycle.ndjson')
        open(life, 'w').write(''.join(json.dumps(r) + '\n' for r in [
            {'at': '2026-10-23 23:32:42', 'pid': 13745, 'event': 'start', 'prev_pid': 8227, 'prev_end': 'clean'},
            {'at': '2026-10-24 01:10:47', 'pid': 13745, 'event': 'stop', 'how': 'SIGTERM'},
            {'at': '2026-10-24 01:10:57', 'pid': 84376, 'event': 'start', 'prev_pid': 13745, 'prev_end': 'clean'}]))
        health.LIFE = life
        boot_after = int(mk('2026-10-24 18:18:05'))           # the Mac came back up on AC at 1% charge
        boot_before = int(mk('2026-10-16 17:38:00'))          # the boot pid 84376 ran on, had the server itself died
        health.ALIVE = os.path.join(tmp, 'alive.json')
        alive = lambda t: json.dump({'84376': mk(t)}, open(health.ALIVE, 'w'))
        alive('2026-10-24 18:17:30')                          # it logged until the Mac stopped
        how_mac, _ = health.last_exit(boot_after)
        how_same, _ = health.last_exit(boot_before)
        how_unknown, _ = health.last_exit(None)               # sysctl failed: never hide a death
        alive('2026-10-24 12:00:00')                          # last seen 6 h before the boot: it died on its own first
        how_early, _ = health.last_exit(boot_after)
        os.remove(health.ALIVE)
        how_no_stamp, _ = health.last_exit(boot_after)        # no sign of life recorded: keep the fix task
        inc.DIR = os.path.join(tmp, 'inc'); inc.CRASH = os.path.join(tmp, 'crash.log'); inc.LIFE = os.path.join(tmp, 'life2')
        inc.FAULT = os.path.join(tmp, 'fault.log'); inc.SERVER_LOG = os.path.join(tmp, 'server.log')
        open(inc.FAULT, 'w').write(
            '--- 2026-10-16 17:44:17 server start, pid 4376\nThread 0x000000016f08b000 (most recent call first):\n'
            '  File "/opt/jarvis/jarvis/notify.py", line 76 in check\n'
            '--- 2026-10-19 11:58:27 server start, pid 53036\n--- 2026-10-24 01:10:57 server start, pid 84376\n'
            '--- 2026-10-24 18:18:26 server start, pid 868\n')
        open(inc.SERVER_LOG, 'w').write('18:19:49 stopped: week at 0.51 (the line is 0.5); nothing more is sent\n')
        open(inc.LIFE, 'w').write(''.join(json.dumps(r) + '\n' for r in [
            {'at': '2026-10-24 18:18:26', 'pid': 868, 'event': 'start', 'prev_pid': 84376, 'prev_end': 'mac_restart', 'boot': boot_after},
            {'at': '2026-10-24 19:00:00', 'pid': 900, 'event': 'start', 'prev_pid': 868, 'prev_end': 'died', 'boot': boot_after}]))
        assistant.new_item = lambda text, title='', **k: (made.append(title) or ('2099-01-01-0000-jarvis', f'a{len(made)}'))
        dispatcher.submit = lambda *a, **k: {'id': f'T{len(made)}'}
        ledger.event = lambda *a, **k: None
        ledger.q = lambda *a, **k: []
        recs = inc.tick()
        by = {r['kind']: r for r in recs}
        mac_ev = open(by['mac_restart']['path']).read() if 'mac_restart' in by else ''
        died_ev = open(by['died']['path']).read() if 'died' in by else ''
        ok = (how_mac == 'mac_restart' and how_same == 'died' and how_unknown == 'died' and how_early == 'died' and how_no_stamp == 'died'
              and by.get('mac_restart', {}).get('task', 'x') is None and made == ['Jarvis server died']
              and 'notify.py' not in mac_ev and 'pid 84376 wrote no dump' in mac_ev and 'notify.py' not in died_ev
              and inc._fault_section(4376).find('notify.py') >= 0)
        return ok, (f'last_exit: alive to the boot -> {how_mac}, same boot -> {how_same}, no boot -> {how_unknown}, '
                    f'last seen 6 h before the boot -> {how_early}, no stamp -> {how_no_stamp}; '
                    f'kinds {sorted(by)}; fix tasks {made}; stale 16 Oct dump quoted: {"notify.py" in mac_ev or "notify.py" in died_ev}')
    finally:
        (health.LIFE, inc.DIR, inc.CRASH, inc.LIFE, inc.FAULT, inc.SERVER_LOG, assistant.new_item, dispatcher.submit, ledger.event,
         ledger.q) = saved
        health.ALIVE = saved_alive
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _real_signal_count():
    import sqlite3
    try:
        c = sqlite3.connect(_REAL_LEARNED, timeout=5)
        n = c.execute('SELECT coalesce(max(id), 0) FROM learn_signals').fetchone()[0]
        c.close()
        return n
    except Exception:
        return None


if __name__ == '__main__':
    _before = _real_signal_count()
    case('guard: claim patterns (19 sentences)', claim_patterns)
    case('guard: unbacked "I\'ve sent it" becomes honest', unbacked_send)
    case('guard: backed send claimed as done becomes present tense', backed_send_present)
    case('guard: "Noted" needs a remember receipt', noted_needs_remember)
    case('guard: holding a claim keeps sentence order', order_kept)
    case('guard: filler lines pass straight through', fillers_pass)
    case('dispatch: asked tasks start at once, in parallel; asking again never duplicates', asked_dispatch)
    case('dispatch: background yields to asked work and resumes after', background_yields)
    case('watcher: badge -> round-up after 2 min, max 1 per 20 min; safety after 60 quiet min', watcher_timing)
    case('gateway: sends only from the page; double press runs once', gateway_rules)
    case('watch: a bare "let me know when it\'s done" makes no Watching need; a named thing does', watch_phrases)
    case('heartbeat: a new inbox item gets a background worker; asked/done ones do not', new_item_background)
    case('match: only what would answer a need counts, never its context or key columns', match_look_for_only)
    case('initiative: local data starts nothing; bucket data does; events need a real change', initiative_rules)
    case('approvals: a stuck approval starts the operator; fresh/pending/busy do not', approvals_safety_net)
    case('operator: a Send press starts at once even with every lane busy', operator_never_waits)
    case('isolation: the dispatch cases pass while the real usage window is nearly used', dispatch_under_hold)
    case('brain: a reply whose task brief has line breaks still parses into the task', reply_with_line_breaks)
    case('incidents: the Mac going down is no crash; a death quotes only its own fault log', mac_restart_not_a_crash)
    if '--live' in sys.argv:
        live_cases(_live_setup())
    # (the filter must bite: the claim cases above DO produce signals; none may land in his real store)
    def _isolated():
        caught = sum(1 for k, *_ in SIGNALS if k == 'claim_guard')
        after = _real_signal_count()
        return after == _before and caught > 0, f'real max id {_before} -> {after}; claim signals captured instead {caught}'
    case('isolation: the suite wrote no learning signal into their real store', _isolated)
    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f'\n{n_ok}/{len(results)} passed')
    os.makedirs(os.path.join(HUB_REAL, 'state'), exist_ok=True)
    json.dump([{'case': n, 'ok': ok, 'detail': str(d)[:500]} for n, ok, d in results],
              open(os.path.join(HUB_REAL, 'state', 'eval_last.json'), 'w'), indent=1)
    sys.exit(0 if n_ok == len(results) else 1)
