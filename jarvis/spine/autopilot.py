"""The autopilot: Jarvis improves itself in a closed, self-correcting loop (prompt-and-test loops that correct themselves).
Changing its own code is off unless the owner turns it on (settings: modules.self_improve); the rules part always runs.

  notice    tickets from THEIR ground truth and code detections, never from outside content: an answer they marked bad, a
            reading they corrected, a claim the speech guard had to change. Each ticket gets a regression case written HERE, in
            code, from their own words (tools/autopilot_cases.py), so "fixed" means the case now passes.
  fix       a background worker (lane background, origin autopilot) makes the smallest change in its own git worktree
            (a folder beside the repository, branch ap/<id>), runs the gate, reads the failures, corrects itself (up to 3 rounds),
            commits, and leaves .autopilot_result.json in the worktree.
  ship      the server, not the worker, decides: the branch must touch only allowed files (never the tests, the validator,
            the hooks, the send path, the gateway, this file, the web page or the Mac app), stay small, and pass the gate
            ITSELF (main's tests against the candidate, live safety cases pass^3, the ticket's case pass^3). Then it merges
            into the local Jarvis repo and redeploys at a quiet moment (no voice turn for 10 min, no asked task running).
  watch     24 hours: their negative reactions per turn against the week before, new incidents, and the code suite every 6 h.
            Worse -> `git revert` of the merge, redeploy, ticket rolled_back, and they are told. Better or same -> kept.
  rules     style rules Jarvis learns are staged, not live; they go live only after the live safety cases pass 3/3 with them in.
  limits    at most MAX_PER_DAY tickets a day, one at a time, inside the governor (purpose initiative); state/AUTOPILOT_PAUSED
            or the kill switch stop it. Outward actions (sends, database writes, merges into shared repos) are never in its
            reach: those stay behind their press, enforced by the hooks and the gateway, which it may not change.
"""
from jarvis import paths
from jarvis import procs
import datetime as dt, json, os, re, subprocess, threading, time, uuid

HUB = str(paths.home())
REPO = str(paths.REPO)
STATE = os.path.join(HUB, 'state')
PAUSED = os.path.join(STATE, 'AUTOPILOT_PAUSED')
RULES_LAST = os.path.join(STATE, 'rules_gate_last.json')
CASES = os.path.join(STATE, 'autopilot_cases.json')     # the owner's own regression cases: theirs, so in the data home
MAX_PER_DAY, MAX_ATTEMPTS, MAX_DIFF_LINES, WATCH_H = 3, 2, 300, 24
ALLOWED = re.compile(r'^(jarvis/[A-Za-z0-9_]+\.py|jarvis/(world|spine)/[A-Za-z0-9_]+\.py)$')
# what judges the optimiser or guards an outward action is never its to change
PROTECTED = {'jarvis/learned.py', 'jarvis/slack_send.py', 'jarvis/actions.py', 'jarvis/review.py', 'jarvis/proof.py', 'jarvis/ladder.py',
             'jarvis/config.py', 'jarvis/paths.py', 'jarvis/spine/gateway.py', 'jarvis/spine/policy.py', 'jarvis/spine/autopilot.py',
             'jarvis/spine/dispatcher.py', 'jarvis/spine/governor.py', 'jarvis/spine/health.py', 'jarvis/spine/incidents.py',
             # the speech guard's hits open the optimiser's own tickets and count against it, so it is one of its judges;
             # the ledger is the one record of what was done
             'jarvis/spine/speech_guard.py', 'jarvis/spine/ledger.py'}
# what a worker may never edit under tools/ and tests/: the gate, the hooks, the fixtures and every test
JUDGES = ('tools/fixtures/**', 'tools/gate.py', 'tools/jarvis_eval.py', 'tools/grounding_eval.py', 'tools/autopilot_cases.py',
          'tools/worker_hook.py', 'tools/worker_stop.py', 'tools/policy_safety.py', 'tools/patterns/**', 'tests/**',
          'tools/phase*_proof.py', 'tools/proof_home.py', 'tools/leakcheck.py')
NEG_KINDS = ('corrected', 'told_off', 'reasked', 'claim_guard', 'label_bad', 'thats_wrong', 'cancel_readback')
_lock = threading.Lock()
_gating = {'busy': False}

SCHEMA = """CREATE TABLE IF NOT EXISTS autopilot(id TEXT PRIMARY KEY, at TEXT, kind TEXT, sig TEXT, title TEXT, evidence TEXT,
  case_id TEXT, state TEXT, task_id TEXT, branch TEXT, worktree TEXT, commit_sha TEXT, gate TEXT, shipped_at TEXT, merge_sha TEXT,
  watch_until TEXT, result TEXT, attempts INTEGER DEFAULT 0, updated TEXT)"""


def _db():
    from jarvis.spine import ledger
    ledger.x(SCHEMA)
    return ledger


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def _git(*a, timeout=120):
    p = subprocess.run(['git', '-C', REPO, *a], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def _set(tid, **kw):
    L = _db()
    kw['updated'] = now()
    L.x('UPDATE autopilot SET ' + ', '.join(f'{k}=?' for k in kw) + ' WHERE id=?', (*kw.values(), tid))


def _event(ev, tid, **data):
    from jarvis.spine import ledger
    ledger.event(ev, tid, 'autopilot', data)


# ------------------------------------------------------------------ notice: tickets and their cases, from his ground truth
def _words(t):
    return [w for w in re.findall(r"[a-z0-9']{3,}", str(t or '').lower()) if w not in ('the', 'and', 'you', 'what', 'that', 'this', 'for', 'with')]


def _add_case(tid, q, grader):
    try:
        d = json.load(open(CASES))
    except (OSError, ValueError):
        d = {'note': 'Regression cases from their own ground truth (jarvis/spine/autopilot.py writes them; the optimiser may not).', 'cases': []}
    cid = 'ap-' + tid[-6:]
    if not any(c['id'] == cid for c in d['cases']):
        d['cases'].append({'id': cid, 'q': q[:500], 'grader': grader, 'from': tid, 'created': now()})
        os.makedirs(os.path.dirname(CASES), exist_ok=True)
        json.dump(d, open(CASES + '.tmp', 'w'), indent=1)
        os.replace(CASES + '.tmp', CASES)
    return cid


def notice():
    """New tickets from unused signals (their bad labels, their corrections, claim-guard hits). Returns the ids made."""
    from jarvis import learned
    L = _db()
    made = []
    seen_sigs = {r['sig'] for r in L.q("SELECT sig FROM autopilot WHERE state NOT IN ('kept','rolled_back','discarded','failed')")}
    cur = _cursor()
    if cur is None:                                  # first run: start from now, not from every old signal
        _cursor(learned.q('SELECT coalesce(max(id), 0) AS m FROM learn_signals')[0]['m'])
        return made
    st = learned.q("SELECT * FROM learn_signals WHERE kind IN ('label_bad','corrected','claim_guard','review_bad') AND id > ? ORDER BY id", (cur,))
    week = (dt.datetime.now().astimezone() - dt.timedelta(days=7)).isoformat(timespec='seconds')
    shallow_week = L.q("SELECT count(*) AS n FROM autopilot WHERE kind='shallow' AND at >= ?", (week,))[0]['n']
    for s in st:
        d = json.loads(s['data'] or '{}')
        kind = {'label_bad': 'bad_answer', 'corrected': 'misread', 'claim_guard': 'claim', 'review_bad': 'shallow'}[s['kind']]
        if kind == 'shallow' and (shallow_week >= 2 or not str(d.get('rubric') or '').strip()):
            continue                         # the weekly review's own finds: at most 2 tickets a week, only with a rubric
        q = d.get('q') or d.get('asked') or ''
        bad = d.get('answer') or d.get('said') or ''
        if not q.strip():
            continue
        sig = kind + ':' + ' '.join(sorted(set(_words(q)))[:6])
        if sig in seen_sigs:
            continue
        grader = [{'kind': 'not_like', 'bad': bad[:600]}, {'kind': 'no_unbacked'}]
        title = {'bad_answer': 'An answer they marked bad', 'misread': 'A question Jarvis misread', 'claim': 'A claim with nothing behind it',
                 'shallow': 'An answer without the judgement they needed'}[kind]
        if kind == 'shallow':
            grader.append({'kind': 'rubric', 'rubric': str(d['rubric'])[:300]})
            shallow_week += 1
        if kind == 'misread':
            from jarvis import human as Hm
            his = d.get('his_words') or ''
            # the term he MEANT ("I said Atlas not Apollo" -> Atlas): names and codes in his words, never one after "not",
            # "no" or "instead of" (that is the wrong reading); whether or not the question already had it
            toks = re.findall(r"[A-Za-z0-9-]+", his)
            terms = []
            for i, t in enumerate(toks):
                before = ' '.join(toks[max(0, i - 2):i]).lower()
                if Hm.TERM.match(t) and t.lower() not in Hm.STOP and not re.search(r'\b(not|no|instead of|rather than)$', before):
                    terms.append(t)
            terms = list(dict.fromkeys(terms))[:2]
            if not terms or all(x.lower() in bad.lower() for x in terms):
                continue                     # nothing code can check: the correction stays a style signal only
            grader.append({'kind': 'must_mention', 'terms': terms})
        if kind == 'bad_answer' and re.search(r'\b(long|short|ramble|wordy|too much)\b', str(d.get('note') or ''), re.I):
            grader.append({'kind': 'max_sentences', 'n': 2})
        tid = dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        cid = _add_case(tid, q, grader)
        L.x("INSERT INTO autopilot(id, at, kind, sig, title, evidence, case_id, state, attempts, updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, now(), kind, sig, title, json.dumps({'q': q[:500], 'bad': bad[:800], 'his': d.get('his_words') or d.get('note') or '',
                                                        'why': d.get('why') or '', 'signal': s['id']})[:4000], cid, 'open', 0, now()))
        seen_sigs.add(sig)
        made.append(tid)
        _event('autopilot_ticket', tid, kind=kind, q=q[:160])
    if st:
        _cursor(st[-1]['id'])
    return made


def _cursor(v=None):
    p = os.path.join(STATE, 'autopilot.json')
    try:
        d = json.load(open(p))
    except (OSError, ValueError):
        d = {}
    if v is None:
        return d.get('signal_cursor')
    d['signal_cursor'] = v
    json.dump(d, open(p, 'w'))


# ------------------------------------------------------------------ fix: the worker
def _prompt(t, wt):
    ev = json.loads(t['evidence'] or '{}')
    return (f"Jarvis self-improvement ticket {t['id']} ({t['title']}). This is work on Jarvis itself: nothing goes to anyone.\n"
            f"WHAT HAPPENED: The owner said: \"{ev.get('q', '')}\". Jarvis answered: \"{ev.get('bad', '')}\".\n"
            + (f"THEIR CORRECTION / NOTE: \"{ev.get('his', '')}\".\n" if ev.get('his') else '')
            + (f"WHY IT WAS WRONG: {ev.get('why')}\n" if ev.get('why') else '')
            + f"\nMake the SMALLEST change that fixes this kind of answer for good, in your own worktree, never the live folder:\n"
            f"  git -C \"{REPO}\" worktree add \"{wt}\" -b ap/{t['id']}\n"
            f"Change only Python under {wt}/jarvis (the assistant's instructions are SYSTEM / FAST_RULES in jarvis/assistant.py; its "
            f"state tools are in jarvis/mcp.py). Never touch tests, fixtures, the gate, the settings and paths modules, "
            f"jarvis/learned.py, jarvis/slack_send.py, the policy gate, the speech guard, the ledger, the gateway, the dispatcher, the hooks, the screens or the "
            f"Mac app: the ship check refuses any change to them. Keep it under "
            f"{MAX_DIFF_LINES} changed lines; prefer one sentence in the instructions over new code.\n"
            f"Then run the quick gate against your copy, in the FOREGROUND with the Bash timeout set to 600000 ms (it takes 3-9 "
            f"minutes; never run it in the background: background jobs are killed when your run ends), and read what fails:\n"
            f"  \"{paths.python()}\" \"{paths.tool('gate.py')}\" --repo \"{wt}\" --quick --case {t['case_id']}\n"
            f"Fix what it reports and run it again, at most 3 rounds. When it passes, commit on the branch (a message about the "
            f"change itself; no assistant or AI wording, the hook refuses it). Do not merge, do not redeploy, do not touch the live "
            f"folder: Jarvis ships it after its own checks. Finally write {wt}/.autopilot_result.json: "
            f"{{\"commit\": \"<sha>\", \"summary\": \"<one line: what changed and why>\", \"gate\": \"pass|fail\", \"rounds\": <n>}}.")


def start_next():
    """One ticket at a time, at most MAX_PER_DAY a day, inside the governor."""
    from jarvis.spine import dispatcher, governor
    L = _db()
    if L.q("SELECT 1 FROM autopilot WHERE state IN ('working','gating') LIMIT 1"):
        return None
    today = dt.date.today().isoformat()
    if L.q("SELECT count(*) AS n FROM autopilot WHERE substr(at,1,10)=? AND state != 'open'", (today,))[0]['n'] >= MAX_PER_DAY:
        return None
    t = L.q("SELECT * FROM autopilot WHERE state='open' ORDER BY at LIMIT 1")
    if not t:
        return None
    t = t[0]
    ok, why = governor.admit({'lane': 'background', 'origin': 'autopilot', 'kind': 'initiative', 'rid': '', 'aid': ''})
    if not ok:
        _set(t['id'], result=f'waiting: {why}')
        return None
    wt = str(paths.worktree(f"ap-{t['id'][-6:]}"))
    task = dispatcher.submit('background', _prompt(t, wt), '', '', title='Jarvis improves itself: ' + t['title'], origin='autopilot', kind='self-improve')
    _set(t['id'], state='working', task_id=task['id'] if task else None, worktree=wt, branch=f"ap/{t['id']}", attempts=(t['attempts'] or 0) + 1)
    _event('autopilot_started', t['id'], task=task['id'] if task else None)
    return t['id']


# ------------------------------------------------------------------ ship: the server checks, gates, merges, redeploys
def _diff_ok(branch):
    rc, base = _git('merge-base', 'HEAD', branch)
    if rc:
        return False, 'no such branch'
    # --no-renames: with rename detection a protected file moved to a new name showed only the new name, and passed. A
    # rename counts as what it is here: the old file deleted and a new one added, both names checked.
    rc, names = _git('diff', '--no-renames', '--name-only', base.strip(), branch)
    files = [f for f in names.split() if f]
    if not files:
        return False, 'no change on the branch'
    bad = [f for f in files if not ALLOWED.match(f) or f in PROTECTED]
    if bad:
        return False, f'touches files it may not change: {bad[:5]}'
    rc, stat = _git('diff', '--no-renames', '--numstat', base.strip(), branch)
    lines = sum(int(a) + int(b) for a, b, *_ in (l.split('\t') for l in stat.splitlines() if l and l[0].isdigit()))
    if lines > MAX_DIFF_LINES:
        return False, f'{lines} changed lines (max {MAX_DIFF_LINES})'
    return True, f'{len(files)} files, {lines} lines'


def _quiet():
    """No voice turn for 10 minutes and no asked task running."""
    from jarvis.spine import ledger
    if ledger.q("SELECT 1 FROM tasks WHERE state='running' AND lane='asked' LIMIT 1"):
        return False
    try:
        with open(os.path.join(HUB, 'voice', 'metrics.ndjson'), 'rb') as f:
            f.seek(max(0, os.path.getsize(f.name) - 4000))
            last = max((json.loads(l).get('eot', 0) for l in f.read().decode('utf-8', 'ignore').splitlines()[1:] if l.strip()), default=0)
    except (OSError, ValueError):
        last = 0
    return time.time() - last / 1000 > 600


def _redeploy():
    """Restart the server on the new code. This runs INSIDE the server it restarts, so the caller records everything first,
    and the restart is a detached process (it outlives this one; redeploy.sh waits for the new server)."""
    try:
        if subprocess.run(['pgrep', '-f', 'Jarvis.app/Contents/MacOS/Jarvis'], capture_output=True).returncode != 0:
            return 'Jarvis.app is closed: live at its next launch'
        log = open(os.path.join(STATE, 'autopilot_redeploy.log'), 'a')
        subprocess.Popen(['sh', str(paths.tool('redeploy.sh'))], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         **procs.session_kw())
    except OSError:                                   # no Mac app on this computer: the new code is live at the next start
        return 'live at the next start'
    return 'redeploying'


def _cleanup(t):
    if t.get('worktree') and os.path.isdir(t['worktree']):
        _git('worktree', 'remove', '--force', t['worktree'])


def check_built():
    """Tickets whose worker has finished: verify, gate (in a thread: the live part takes minutes), merge, redeploy."""
    from jarvis.spine import dispatcher
    L = _db()
    for t in L.q("SELECT * FROM autopilot WHERE state='working'"):
        task = dispatcher.task(t['task_id']) if t['task_id'] else None
        if task and task['state'] in ('queued', 'running', 'paused'):
            continue
        res = {}
        try:
            res = json.load(open(os.path.join(t['worktree'], '.autopilot_result.json')))
        except (OSError, ValueError, TypeError):
            pass
        ok, why = _diff_ok(t['branch'])
        if not ok or str(res.get('gate')) != 'pass':
            why = why if not ok else f"the worker's own gate did not pass ({res.get('gate')})"
            _cleanup(t)
            if (t['attempts'] or 0) < MAX_ATTEMPTS and 'may not change' not in why:
                _set(t['id'], state='open', result='retry: ' + why)
                _git('branch', '-D', t['branch'])
            else:
                _set(t['id'], state='failed', result=why)
                _git('branch', '-D', t['branch'])
                _event('autopilot_failed', t['id'], why=why)
            continue
        _set(t['id'], state='gating', commit_sha=str(res.get('commit') or '')[:40], result='gating: ' + why)
        threading.Thread(target=_gate_and_ship, args=(dict(t), why, str(res.get('summary') or '')[:200]), daemon=True, name='autopilot-gate').start()
        return                                           # one gate at a time


def _gate_and_ship(t, diff, summary):
    if _gating['busy']:
        return
    _gating['busy'] = True
    try:
        p = subprocess.run([paths.python(), str(paths.tool('gate.py')), '--repo', t['worktree'], '--live',
                            '--case', t['case_id'], '--json'], capture_output=True, text=True, timeout=9000)
        try:
            g = json.loads((p.stdout or '').strip().splitlines()[-1])
        except (ValueError, IndexError):
            g = {'ok': False, 'failed': ['gate output'], 'detail': {'gate': (p.stdout or p.stderr or '')[-800:]}}
        _set(t['id'], gate=json.dumps({'ok': g['ok'], 'failed': g['failed']}))
        if not g['ok']:
            _cleanup(t); _git('branch', '-D', t['branch'])
            state = 'open' if (t['attempts'] or 0) < MAX_ATTEMPTS else 'failed'
            tail = ' | '.join(str((g.get('detail') or {}).get(k, ''))[-240:].replace('\n', ' / ') for k in g['failed'])
            _set(t['id'], state=state, result=f"the server's gate failed: {', '.join(g['failed'])}: {tail}"[:900])
            _event('autopilot_failed' if state == 'failed' else 'autopilot_retry', t['id'], why=g['failed'])
            return
        for _ in range(36):                              # merge only into a clean live tree, never mixed with other work
            rc, st = _git('status', '--porcelain', '--untracked-files=no')
            if rc == 0 and not st.strip():
                break
            time.sleep(300)
        else:
            _set(t['id'], state='open', result='the live tree had other changes for 3 hours: will retry')
            return
        rc, out = _git('merge', '--no-ff', t['branch'], '-m', f"Autopilot {t['id']}: {t['title']}\n\n{summary}\nDiff: {diff}. Gate: pass "
                       "(regression, grounding, phase 4, phase 5, live safety pass^3, its own case pass^3).")
        if rc:
            _git('merge', '--abort')
            _set(t['id'], state='failed', result='merge failed: ' + out[-200:])
            return
        _, sha = _git('rev-parse', 'HEAD')
        _cleanup(t); _git('branch', '-d', t['branch'])
        for _ in range(36):
            if _quiet():
                break
            time.sleep(300)
        until = (dt.datetime.now().astimezone() + dt.timedelta(hours=WATCH_H)).isoformat(timespec='seconds')
        # recorded BEFORE the restart: the restart ends this process
        _set(t['id'], state='watching', merge_sha=sha.strip(), shipped_at=now(), watch_until=until, result=f'shipped: {summary}')
        _event('autopilot_shipped', t['id'], title=t['title'], summary=summary, why=summary)
        _redeploy()
    except Exception as e:
        _set(t['id'], state='failed', result=f'{e.__class__.__name__}: {e}'[:200])
    finally:
        _gating['busy'] = False


# ------------------------------------------------------------------ watch: keep it, or roll it back
def _rate(since, until):
    """Negative reactions per voice turn between two times (their signals over their turns)."""
    from jarvis import learned
    import glob
    neg = learned.q("SELECT count(*) AS n FROM learn_signals WHERE at >= ? AND at < ? AND kind IN (%s)" % ','.join('?' * len(NEG_KINDS)),
                    (since, until, *NEG_KINDS))[0]['n']
    turns = 0
    for f in glob.glob(os.path.join(HUB, 'voice', 'conversations', '*.json')):
        try:
            turns += sum(1 for x in json.load(open(f)).get('turns') or [] if since <= str(x.get('at', ''))[:19] < until[:19])
        except (OSError, ValueError):
            pass
    return neg, turns


def watch():
    L = _db()
    for t in L.q("SELECT * FROM autopilot WHERE state='watching'"):
        n = now()
        if json.loads(t['gate'] or '{}').get('rollback_pending'):
            # a rollback that waited for a clean tree is retried; such a ticket is never 'kept' (7 Oct: one was, 10 minutes later)
            rollback(t, json.loads(t['gate'])['rollback_pending'])
            continue
        shipped = t['shipped_at']
        base_from = (dt.datetime.fromisoformat(shipped) - dt.timedelta(days=7)).isoformat(timespec='seconds')
        b_neg, b_turns = _rate(base_from, shipped)
        a_neg, a_turns = _rate(shipped, n)
        base = b_neg / b_turns if b_turns else 0.0
        after = a_neg / a_turns if a_turns else 0.0
        worse = a_turns >= 10 and after > max(1.5 * base, base + 0.05)
        from jarvis.spine import ledger
        incident = ledger.q("SELECT 1 FROM events WHERE kind='incident' AND at > ? LIMIT 1", (shipped,))
        suite_bad = False
        last_check = json.loads(t['gate'] or '{}').get('watch_checked', shipped)
        if (dt.datetime.fromisoformat(n) - dt.datetime.fromisoformat(last_check)).total_seconds() > 6 * 3600:
            for _ in range(2):          # a failure counts only when it repeats (7 Oct: one was the usage window, not the code)
                p = subprocess.run([paths.python(), str(paths.tool('gate.py')), '--json'], capture_output=True, text=True, timeout=3600)
                suite_bad = '"ok": true' not in (p.stdout or '')
                if not suite_bad:
                    break
            if suite_bad:
                _event('autopilot_watch_suite_failed', t['id'], output=(p.stdout or p.stderr or '')[-600:])
            g = json.loads(t['gate'] or '{}'); g['watch_checked'] = n
            _set(t['id'], gate=json.dumps(g))
        if worse or incident or suite_bad:
            why = 'their corrections rose' if worse else 'a new incident' if incident else 'the suite failed on main'
            rollback(t, f"{why} (negative per turn: {base:.2f} before, {after:.2f} after, {a_turns} turns)")
        elif n > t['watch_until']:
            _set(t['id'], state='kept', result=(t['result'] or '') + f' | kept after {WATCH_H} h ({base:.2f} -> {after:.2f} per turn, {a_turns} turns)')
            _event('autopilot_kept', t['id'], before=round(base, 3), after=round(after, 3), turns=a_turns)


def rollback(t, why):
    rc, st = _git('status', '--porcelain', '--untracked-files=no')
    if st.strip():
        g = json.loads(t['gate'] or '{}'); g['rollback_pending'] = why
        _set(t['id'], gate=json.dumps(g), result=f'rollback waiting for a clean tree: {why}')
        _event('autopilot_rollback_waiting', t['id'], why=why)
        return False
    rc, out = _git('revert', '-m', '1', '--no-edit', t['merge_sha'])
    if rc:
        _git('revert', '--abort')
        _event('autopilot_rollback_failed', t['id'], why=why, error=out[-200:])
        return False
    _set(t['id'], state='rolled_back', result=f'rolled back: {why}')      # recorded before the restart ends this process
    _event('autopilot_rolled_back', t['id'], title=t['title'], why=why)
    _redeploy()
    return True


# ------------------------------------------------------------------ staged rules go live only through the safety gate
def gate_rules():
    from jarvis import learned
    staged = learned.q("SELECT * FROM rules WHERE state='staged'")
    if not staged or _gating['busy']:
        return None
    last = {}
    try:
        last = json.load(open(RULES_LAST))
    except (OSError, ValueError):
        pass
    if last.get('kept_staged') and (dt.datetime.fromisoformat(now()) - dt.datetime.fromisoformat(last['at'])).total_seconds() < 86400:
        return None                                   # the suite failed without them last time: retry once a day, not every tick
    _gating['busy'] = True
    try:
        evl = lambda env: subprocess.run([paths.python(), str(paths.tool('jarvis_eval.py')), '--live'], capture_output=True,
                                         text=True, timeout=3600, env=env).returncode == 0
        ok = evl(dict(os.environ, JARVIS_INCLUDE_STAGED='1'))
        if not ok and not evl(dict(os.environ)):
            # the suite fails without the staged rules too: the failure is not theirs (7 Oct: two of his rules were retired
            # because the usage window held the dispatch cases). They stay staged.
            json.dump({'at': now(), 'kept_staged': True}, open(RULES_LAST, 'w'))
            _event('autopilot_rules', '', staged=len(staged), ok=False, kept_staged=True, why='the suite fails without them too')
            return None
        json.dump({'at': now(), 'kept_staged': False, 'ok': ok}, open(RULES_LAST, 'w'))
        for r in staged:
            if ok:
                learned.x("UPDATE rules SET state='active' WHERE id=?", (r['id'],))
                rep = re.match(r'replaces:(\S+)', r.get('why') or '')
                if rep:
                    learned.x("UPDATE rules SET state='retired', retired_at=?, retired_why='replaced' WHERE id=? AND state='active'", (now(), rep.group(1)))
            else:
                learned.x("UPDATE rules SET state='retired', retired_at=?, retired_why='failed the safety gate' WHERE id=?", (now(), r['id']))
            learned.x("INSERT INTO learn_edits(id, at, run, op, tbl, row_id, text, origin, source_ref, accepted, why) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ('ed-' + uuid.uuid4().hex[:10], now(), 'rule-gate', 'ACTIVATE' if ok else 'CLOSE', 'rules', r['id'], r['text'],
                       'executed', 'gate', 1, 'passed the live safety cases 3/3' if ok else 'failed the live safety cases'))
        _event('autopilot_rules', '', staged=len(staged), ok=ok)
        return ok
    finally:
        _gating['busy'] = False


# ------------------------------------------------------------------ the loop
def code_changes_on():
    """May Jarvis change its own code? Only when the owner turned it on in settings (modules.self_improve)."""
    from jarvis import config
    return bool(config.load().get('modules.self_improve'))


def tick():
    """Called by the heartbeat every 10 minutes."""
    if os.path.exists(PAUSED) or os.path.exists(os.path.join(STATE, 'KILL')):
        return 'paused'

    if not _lock.acquire(blocking=False):
        return 'busy'
    try:
        on = code_changes_on()                       # off: nothing new starts; what already shipped is still watched
        out = {'tickets': notice() if on else 0}
        check_built()
        out['started'] = start_next() if on else None
        watch()
        if not _gating['busy']:
            threading.Thread(target=gate_rules, daemon=True, name='autopilot-rules').start()
        return out
    except Exception as e:
        return {'error': f'{e.__class__.__name__}: {e}'[:200]}
    finally:
        _lock.release()


def status():
    L = _db()
    return {'on': code_changes_on(), 'paused': os.path.exists(PAUSED), 'gating': _gating['busy'],
            'tickets': L.q("SELECT id, at, kind, title, state, result, shipped_at, watch_until FROM autopilot ORDER BY at DESC LIMIT 30")}
