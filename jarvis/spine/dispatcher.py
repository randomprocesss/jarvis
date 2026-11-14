"""Workers, in lanes (HARNESS.md §3b-c, Phase 2, 5 Oct 2026). Every run is a row in the ledger's `tasks`.

Lanes:
  asked       what they ask for: starts at once, up to ASKED_MAX in parallel. If memory is short, the newest background
              worker is paused (SIGINT; its session is kept) to make room, and resumed later with --resume.
  background  work nobody asked for this minute: new inbox items, then Jarvis's own checks (initiatives). Up to two at a
              time (initiatives at most one), only when no asked task waits and memory allows. Inbox work comes first: it
              pauses a running initiative when the lane is full (5 Oct: three new items queued behind a curiosity check).
  operator    approved actions (close_actions._spawn): its own reserved slot, recorded here, never blocked by the others.
Two workers never run on the same item. Each worker gets a known session id (--session-id), so a paused or dead worker can
be resumed; a worker that died mid-way is resumed at most RESUME_MAX times, then marked failed and they are told.
Every worker runs with state/worker-settings.json (no commit attribution, no memory writes, the kill switch and the
shared-record lint as hooks; see tools/worker_hook.py) and env JARVIS_PROC=1, JARVIS_TASK_ID=<id>. Workers run in the owner's
work folder (settings: work.folders), so they see that folder's own instructions.
"""
from jarvis import paths
from jarvis import procs
import datetime as dt, json, os, re, signal, subprocess, threading, time, uuid
from jarvis.spine import ledger

HUB = str(paths.home())
CLAUDE = None                    # a test's stand-in for the Claude Code command; None = the installed one
WORKDIR = None                   # a test's folder; None = the owner's work folder from settings
SETTINGS = os.path.join(HUB, 'state', 'worker-settings.json')
OPERATOR_SETTINGS = os.path.join(HUB, 'state', 'operator-settings.json')   # + what the owner allowed at Send (canvases, 5 Oct)
ASKED_MAX, BACKGROUND_MAX, INITIATIVE_MAX, MIN_FREE_PCT, RESUME_MAX = 3, 3, 1, 12, 2   # 13 Nov: background 2 -> 3 (two trial steps + one other)
TRIAL_MAX = 2                                            # steps of research trials running at once (13 Nov)
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000')
_lock = threading.RLock()
_procs = {}                      # task id -> Popen (the ones this server started)
model_for_work = lambda: None    # set by the assistant (the "work" tier model, or None = Claude's default)


def _claude():
    return CLAUDE or paths.claude_bin() or 'claude'


def _workdir():
    return WORKDIR or str(paths.workdir())


def _transcripts():
    """Where Claude Code writes the chats of the workers (it names the folder after the folder they ran in)."""
    return str(paths.claude_chats_for(_workdir()))


def _rule(p):
    """A path as a Claude Code permission rule writes it: ~/... inside the home folder, //... for any other full path."""
    p = os.path.abspath(os.path.expanduser(str(p))).replace(os.sep, '/')
    h = os.path.expanduser('~').replace(os.sep, '/')
    return '~' + p[len(h):] if p == h or p.startswith(h + '/') else '/' + p if p.startswith('/') else '//' + p


def _hook(name, *args):
    return ' '.join(f'"{x}"' for x in (paths.python(), str(paths.tool(name)))) + ''.join(' ' + a for a in args)


def worker_settings():
  """The settings every worker runs with. Built when written, so the owner's own never-read list counts."""
  from jarvis import config
  from jarvis.spine import autopilot as _autopilot
  repo_roots = (_rule(paths.REPO), _rule(paths.worktree('ap-*')))
  return {
    # shared records never mention Claude, Jarvis or AI (the owner's rule): no Co-Authored-By trailer, no PR footer
    'includeCoAuthoredBy': False,
    'attribution': {'commit': '', 'pr': ''},
    'permissions': {'deny': [
        # workers read the memory notes (his rails) but never write them: a planted message cannot poison memory
        'Edit(~/.claude/projects/**/memory/**)', 'Edit(~/.claude/agent-memory/**)', 'Edit(~/.claude/skills/**)',
        'Edit(~/.claude/scheduled-tasks/**)', f"Edit({_rule(paths.state())}/**)",     # Edit rules cover every file-editing tool
        # credentials are never read
        f"Read({_rule(paths.state())}/page.key*)", f"Read({_rule(paths.state())}/secrets/**)", 'Read(**/pgpass*)', 'Read(**/.pgpass)',
        'Read(~/.ssh/**)', 'Read(**/.git-credentials)', 'Read(**/.netrc)',
        *[f'Read({_rule(p)})' for p in config.load().get('work.never_read') or []],   # and what the owner listed in settings
        # Phase 4 (HARNESS §6.4 denyRead): cloud credentials, keychains, browser profiles, .env files. The CLIs that need them
        # (gcloud, aws) still work: they read their own files; only the model's reads are refused.
        'Read(~/.aws/**)', 'Read(~/.config/gcloud/**)', 'Read(~/Library/Keychains/**)', 'Read(~/Library/Cookies/**)',
        'Read(~/Library/Application Support/Google/Chrome/**)', 'Read(~/Library/Application Support/Firefox/**)',
        'Read(~/Library/Safari/**)', 'Read(**/.env)', 'Read(**/.env.*)',
        # Level 5 (autopilot): the optimiser never edits what judges it or what guards outward actions. The ship check
        # refuses such diffs too (jarvis/spine/autopilot.py); these rules stop it earlier.
        # (built from the ship check's own lists, so the two can never drift apart)
        *[f'Edit({root}/{path})' for root in repo_roots for path in list(_autopilot.JUDGES) + sorted(_autopilot.PROTECTED)]]},
    'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'timeout': 10, 'command': _hook('worker_hook.py', 'pre')}]}],
              # Phase 4: acceptance checks before an item task may finish (no false "done"); item lanes only, at most 2 blocks
              'Stop': [{'hooks': [{'type': 'command', 'timeout': 30, 'command': _hook('worker_stop.py')}]}]},
  }


def write_settings():
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    base = worker_settings()
    op = json.loads(json.dumps(base))
    # The owner, 5 Oct: "Canvas at Send". Only the operator (the approvals worker, started by his press) gets it, and the hook
    # still refuses any canvas that is not an approved attachment of an approved request.
    op['permissions']['allow'] = ['mcp__claude_ai_Slack__slack_create_canvas', 'mcp__claude_ai_Slack__slack_update_canvas',
                                  # and "Files at Send": approved Excel/CSV go to the shared folder, linked in the message
                                  'mcp__claude_ai_Microsoft_365__sharepoint_upload_file', 'mcp__claude_ai_Microsoft_365__sharepoint_create_folder']
    for path, data in ((SETTINGS, base), (OPERATOR_SETTINGS, op)):
        json.dump(data, open(path + '.tmp', 'w'), indent=1)
        os.replace(path + '.tmp', path)


# Phase 4 router: which profile skills an item task gets, picked in code from the item's own words (no model call). A
# profile only ADDS rules (sourcing, tests, his voice); it never removes a tool or a step. At most two; none = inbox-work alone.
ROUTES = (
    ('coder', re.compile(r'\b(code|repo|branch|commit|pull request|\bPR\b|merge|bug|stack ?trace|traceback|exception|notebook cell|'
                         r'loader|pipeline|endpoint|api|deploy|refactor|unit test|pytest|function|script fails?)\b', re.I)),
    ('analyst', re.compile(r'\b(table|rows?|query|count|how many|excel|csv|spreadsheet|list of|forecast|scenario|numbers?|figures?|'
                           r'total|average|percent(age)?|trend|dataset|database|sql|parquet|compare|breakdown|impact)\b', re.I)),
    ('writer', re.compile(r'\b(review|guide|doc(ument)?|write-?up|confluence|release notes|page|slides?|deck|wording|explanations?|'
                          r'summary|email|reply to|feedback)\b', re.I)),
)


LABEL_PROFILE = {'analysis': 'analyst', 'scenario': 'analyst', 'code': 'coder', 'draft': 'writer', 'research': 'writer'}


def _own_words():
    """The owner's own number-work words (settings: work.analyst_words), e.g. the names of their systems and tables. They
    count with the general ones above."""
    from jarvis import config
    words = [re.escape(w.strip()) for w in config.load().get('work.analyst_words') or [] if w.strip()]
    return re.compile(r'\b(' + '|'.join(words) + r')\b', re.I) if words else None


def route(rid, aid):
    """[(profile, matched words)] for an item, from its title, text and points."""
    try:
        a = next((x for x in json.load(open(os.path.join(HUB, 'roundups', rid + '.json'))).get('actions') or [] if x.get('id') == aid), {})
    except (OSError, ValueError):
        a = {}
    text = ' '.join([str(a.get('title') or ''), str(a.get('text') or '')[:2000]] +
                    [p if isinstance(p, str) else str((p or {}).get('text') or '') for p in a.get('points') or []])
    hits = []
    label = LABEL_PROFILE.get(str(a.get('route') or ''))          # the brain's own label for a voice task comes first
    if label:
        hits.append((label, ['label:' + a['route']]))
    found = []
    own = _own_words()
    for name, rx in ROUTES:
        words = {m.group(0).lower() for m in rx.finditer(text)}
        if name == 'analyst' and own:
            words |= {m.group(0).lower() for m in own.finditer(text)}
        words = sorted(words)
        if len(words) >= (1 if name == 'coder' else 2) and name != label:
            found.append((name, words))
    found.sort(key=lambda h: -len(h[1]))
    return (hits + found)[:2]


def item_prompt(rid, aid):
    base = f"Use the jarvis-inbox-work skill on round-up {rid} item {aid} only, and finish it now."
    hits = route(rid, aid)
    if not hits:
        return base
    names = [h[0] for h in hits]
    try:
        ledger.event('routed', f'{rid}|{aid}', 'router', {'profiles': names, 'words': {h[0]: h[1][:6] for h in hits}})
    except Exception:
        pass
    return base + ' Also follow ' + ' and '.join(f'the jarvis-profile-{n} skill' for n in names) + \
        f" (this item is {' and '.join(names)} work); they add rules on top of jarvis-inbox-work and never replace a step of it."


def mem_free_pct():
    try:
        return int(subprocess.run(['sysctl', '-n', 'kern.memorystatus_level'], capture_output=True, text=True, timeout=3).stdout.strip())
    except Exception:
        return 100


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def submit(lane, prompt, rid='', aid='', title='', origin='owner', kind='item', ask_quote='', initiative_id=None, confirmed=False):
    """Queue a worker run and try to start it now. Returns the task row. When they ask for an item that background work
    already has queued or running, that task is promoted to their (asked lane, starts at once) instead of a duplicate.
    An asked task whose estimate reaches their 10% rail (spine/governor.py) is 'held' until their yes (go())."""
    if lane == 'asked' and rid:
        same = ledger.q("SELECT * FROM tasks WHERE rid=? AND aid=? AND state IN ('queued','paused','running') ORDER BY created", (rid, aid))
        if same:
            t = same[0]
            ledger.x("UPDATE tasks SET lane='asked', origin='owner', ask_quote=COALESCE(NULLIF(?, ''), ask_quote) WHERE id=?", (ask_quote[:1000], t['id']))
            ledger.event('task_promoted', t['id'], 'owner', {'from': t['lane'], 'rid': rid, 'aid': aid})
            dispatch()
            return task(t['id'])
    tid = dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    ledger.upsert('tasks', {'id': tid, 'rid': rid, 'aid': aid, 'title': title[:200], 'kind': kind, 'lane': lane, 'origin': origin,
                            'state': 'queued', 'created': ledger.now(), 'session': str(uuid.uuid4()), 'ask_quote': ask_quote[:1000],
                            'initiative_id': initiative_id, 'prompt': prompt, 'attempts': 0}, ('id',))
    ledger.event('task_queued', tid, origin, {'lane': lane, 'rid': rid, 'aid': aid, 'title': title[:120]})
    if lane == 'asked' and not confirmed:
        try:
            from jarvis.spine import governor
            r = governor.rail(task(tid))
        except Exception as e:
            r = None; ledger.event('governor_error', tid, 'governor', {'error': str(e)[:200]})
        if r:
            ledger.x("UPDATE tasks SET state='held', note=? WHERE id=?", ('rail: ' + r['say'], tid))
            ledger.event('rail_held', tid, origin, r)
            return task(tid)
    dispatch()
    return task(tid)


def go(tid):
    """Their yes to a task held at the rail: it queues and starts like any asked task."""
    t = task(tid)
    if not t or t['state'] != 'held':
        return t
    ledger.x("UPDATE tasks SET state='queued', note=NULL WHERE id=?", (tid,))
    ledger.event('rail_yes', tid, 'owner', {})
    dispatch()
    return task(tid)


def task(tid):
    r = ledger.q('SELECT * FROM tasks WHERE id=?', (tid,))
    return r[0] if r else None


def _launch(t, resume=False):
    d = os.path.join(HUB, 'work', 'outputs', 'task-' + t['id'])
    os.makedirs(d, exist_ok=True)
    log_path = os.path.join(d, 'run.log')
    log = open(log_path, 'a')
    log.write(f"{ledger.now()} {'resume' if resume else 'start'} lane={t['lane']} {t.get('rid') or ''} {t.get('aid') or ''}\n"); log.flush()
    wm = model_for_work()
    base = [_claude(), '-p']
    if resume:
        cmd = base + ['Continue exactly where you stopped (you were paused or interrupted) and finish the task.', '--resume', t['session']]
    else:
        cmd = base + [t['prompt'], '--session-id', t['session']]
    cmd += ['--permission-mode', 'auto', '--settings', SETTINGS] + (['--model', wm] if wm else [])   # effort: the model's default, never lowered
    env = dict(MCP_ENV, JARVIS_PROC='1', JARVIS_TASK_ID=t['id'], JARVIS_LANE=t['lane'])
    p = subprocess.Popen(exit_wrapped(cmd, log_path), cwd=_workdir(), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         **procs.session_kw(), env=env)
    _procs[t['id']] = p
    ledger.x("UPDATE tasks SET state='running', pid=?, started=COALESCE(started, ?), heartbeat_at=?, log=?, attempts=attempts+? WHERE id=?",
             (p.pid, ledger.now(), ledger.now(), os.path.relpath(log_path, HUB), 1 if resume else 0, t['id']))
    ledger.event('task_started' if not resume else 'task_resumed', t['id'], t['origin'], {'lane': t['lane'], 'pid': p.pid})
    return p


STUCK_MIN, LONG_MIN, MAX_MIN = 25, 45, 120     # watchdog: no activity for 25 min = stuck; 45 min = told; 2 h = stopped


def _activity(t):
    """Seconds since the worker last did anything: its transcript (and subagents') or its log was written. Every launch
    passes --session-id (9 Oct: round-ups had none, so only their log counted, which `claude -p` writes at the end; runs of
    14-32 min past 25 min were interrupted as stuck and died with "Execution error")."""
    files = [os.path.join(HUB, t.get('log') or 'x')]
    if t.get('session'):
        chats = _transcripts()
        files += [os.path.join(chats, t['session'] + '.jsonl')]
        sub = os.path.join(chats, t['session'], 'subagents')
        if os.path.isdir(sub):
            files += [os.path.join(sub, f) for f in os.listdir(sub)]
    m = max([os.path.getmtime(x) for x in files if os.path.exists(x)] or [0])
    return time.time() - m if m else None


def _watchdog(t):
    """A running worker that is stuck is interrupted (it resumes where it stopped, bounded by RESUME_MAX); one that runs
    long is reported at 45 min and stopped at 2 h. Operator and external rows are left alone (not ours to resume)."""
    if t['lane'] not in ('asked', 'background') or not t.get('pid'):
        return
    try:
        age_min = (dt.datetime.now() - dt.datetime.fromisoformat(str(t.get('started'))[:19])).total_seconds() / 60
    except ValueError:
        return
    idle = _activity(t)
    def sig(s):
        try:
            procs.signal_group(int(t['pid']), s)
        except (OSError, TypeError, ValueError):
            pass
    if age_min >= MAX_MIN:
        ledger.x("UPDATE tasks SET note='overtime' WHERE id=?", (t['id'],))
        ledger.event('task_overtime_stopped', t['id'], 'watchdog', {'minutes': round(age_min), 'title': t['title']})
        sig(signal.SIGTERM)
    elif idle is not None and idle >= STUCK_MIN * 60 and t.get('note') != 'stuck':
        ledger.x("UPDATE tasks SET note='stuck' WHERE id=?", (t['id'],))
        ledger.event('task_stuck', t['id'], 'watchdog', {'idle_min': round(idle / 60), 'title': t['title']})
        sig(signal.SIGINT)
    elif age_min >= LONG_MIN and not ledger.q("SELECT 1 FROM events WHERE kind='task_long' AND ref=?", (t['id'],)):
        ledger.event('task_long', t['id'], 'watchdog', {'minutes': round(age_min), 'title': t['title'], 'idle_s': round(idle or 0)})


def _reap():
    """Running rows whose process has gone: finished (exit 0 / with a result) or died (resumed later, bounded)."""
    for t in ledger.q("SELECT * FROM tasks WHERE state='running'"):
        p = _procs.get(t['id'])
        if p is not None:
            rc = p.poll()
            if rc is None:
                ledger.x('UPDATE tasks SET heartbeat_at=? WHERE id=?', (ledger.now(), t['id']))
                _watchdog(t)
                continue
        elif _alive(t['pid']):                # started by an earlier server process: still going
            ledger.x('UPDATE tasks SET heartbeat_at=? WHERE id=?', (ledger.now(), t['id']))
            _watchdog(t)
            continue
        else:
            rc = _exit_file(t)                # not started by this server process: the sh wrapper's run.exit, else None
        _procs.pop(t['id'], None)
        if rc not in (0, None) and _hit_limit(t):
            ledger.x("UPDATE tasks SET state='paused', exit=?, note='Claude usage limit: resumes when it lifts' WHERE id=?", (rc, t['id']))
            continue
        den = _denials(t)
        if den:                                       # the hook stopped it: refused 3 times in a row or 20 in all
            ledger.x("UPDATE tasks SET state='failed', ended=?, exit=?, note=? WHERE id=?",
                     (ledger.now(), rc, f"stopped after {den['total']} refused tool calls", t['id']))
            ledger.event('task_refusals_stopped', t['id'], 'watchdog', {'title': t['title'], 'why': f"{den['consec']} refusals in a row, {den['total']} in all"})
            continue
        if t.get('note') == 'overtime':                  # 23 Oct: before "done". A worker stopped at 2 h can exit 0 (or leave a
            _overtime(t, rc)                             # clean log), and three such runs that day were filed as done with
            continue                                     # nothing written to their items, which then looked idle
        if rc == 0 or (rc is None and _finished_ok(t)):
            ledger.x("UPDATE tasks SET state='done', ended=?, exit=? WHERE id=?", (ledger.now(), rc, t['id']))
            _acceptance(t)
            try:                                       # Phase 4 maker-checker: an independent review of results with numbers
                from jarvis import review
                review.after_task(t)
            except Exception as e:
                ledger.event('review_error', t['id'], 'review', {'error': str(e)[:200]})
            ledger.event('task_done', t['id'], t['origin'], {'lane': t['lane'], 'rid': t['rid'], 'aid': t['aid']})
        elif rc in (-signal.SIGINT, 130) and t.get('note') == 'preempted':
            ledger.x("UPDATE tasks SET state='paused', exit=? WHERE id=?", (rc, t['id']))
        elif (t.get('attempts') or 0) < RESUME_MAX and rc not in (-signal.SIGTERM, -getattr(signal, 'SIGKILL', 9), 143):
            ledger.x("UPDATE tasks SET state='paused', exit=?, note='died; will resume' WHERE id=?", (rc, t['id']))
            ledger.event('task_died', t['id'], t['origin'], {'exit': rc, 'attempts': t.get('attempts')})
        else:
            ledger.x("UPDATE tasks SET state='failed', ended=?, exit=? WHERE id=?", (ledger.now(), rc, t['id']))
            ledger.event('task_failed', t['id'], t['origin'], {'exit': rc, 'lane': t['lane'], 'rid': t['rid'], 'aid': t['aid'],
                                                              'title': t['title']})


def _overtime(t, rc):
    """A worker the watchdog stopped at MAX_MIN: failed (never done), and its item asks them whether to restart it, with
    its outputs so far kept and picked up by the restart."""
    ledger.x("UPDATE tasks SET state='failed', ended=?, exit=?, note=? WHERE id=?",
             (ledger.now(), rc, f'stopped by the watchdog after {MAX_MIN // 60} hours: needs a restart', t['id']))
    ledger.event('task_failed', t['id'], t['origin'], {'exit': rc, 'lane': t['lane'], 'rid': t['rid'], 'aid': t['aid'],
                                                      'title': t['title'], 'why': f'ran over {MAX_MIN // 60} hours; stopped'})
    if not (t.get('rid') and t.get('aid')):
        return
    f = os.path.join(HUB, 'work', t['rid'] + '.json')
    try:
        with open(f + '.lock', 'w') as lk:
            procs.flock(lk)
            d = json.load(open(f)) if os.path.exists(f) else {}
            w = d.setdefault(t['aid'], {})
            out = f"work/outputs/{t['rid']}-{t['aid']}/"
            ask = {'id': 'restart-' + t['id'][-4:], 'q': f'The worker stopped at its {MAX_MIN // 60}-hour limit. Restart it?',
                   'detail': f"It ran from {str(t.get('started'))[11:16]} until the watchdog stopped it. What it made so far is kept in {out}; "
                             f"a restart continues from there instead of starting over.",
                   'options': [{'id': 'continue', 'label': 'Restart and continue', 'recommended': True,
                                'plan': f'a new worker reads {out} and its log, then carries on from where it stopped'},
                               {'id': 'leave', 'label': 'Leave it stopped', 'plan': 'nothing runs; the item stays as it is'}]}
            if not any(a.get('id') == ask['id'] for a in w.get('asks') or []):
                w.setdefault('asks', []).append(ask)
            w['status'] = 'needs_you'
            w['needs_you'] = w.get('needs_you') or f'Stopped at the {MAX_MIN // 60}-hour limit; restart it? (its work so far is kept)'
            w['updated'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
            tmp = f + '.tmp'; json.dump(d, open(tmp, 'w'), indent=1, ensure_ascii=False); os.replace(tmp, f)
            procs.funlock(lk)
    except (OSError, ValueError) as e:
        ledger.event('overtime_note_failed', t['id'], 'watchdog', {'error': str(e)[:200]})


def _denials(t):
    """The worker hook's refusal count for this task, when it reached the stop (tools/worker_hook.py), else None."""
    try:
        d = json.load(open(os.path.join(HUB, 'state', 'denials', t['id'] + '.json')))
    except (OSError, ValueError):
        return None
    return d if d.get('consec', 0) >= 3 or d.get('total', 0) >= 20 else None


def _acceptance(t):
    """What the Stop-hook checks found (tools/worker_stop.py), into the ledger: a finish with open hard problems is shown."""
    try:
        r = json.load(open(os.path.join(HUB, 'state', 'stopchecks', t['id'] + '.json')))
    except (OSError, ValueError):
        return
    ledger.event('acceptance', t['id'], t['origin'], {'rid': t.get('rid'), 'aid': t.get('aid'), 'hard': r.get('hard') or [],
                                                     'soft': r.get('soft') or [], 'blocks': r.get('blocks', 0)})


def _hit_limit(t):
    """Did this worker stop on Claude's usage limit? (the last lines of its log)"""
    from jarvis.spine import limits
    try:
        f = os.path.join(HUB, t.get('log') or '')
        with open(f, 'rb') as fh:
            fh.seek(max(0, os.path.getsize(f) - 4000))
            return limits.seen(fh.read().decode('utf-8', 'ignore')) is not None
    except (OSError, TypeError):
        return False


def exit_wrapped(cmd, log_path):
    """cmd run through a small wrapper that writes its exit code to run.exit next to the log. 1 Nov: runs started detached (the
    hourly Check messages, the operator) or before a server restart left no exit code, and were judged by their log."""
    xf = os.path.splitext(log_path)[0] + '.exit'
    try:
        os.remove(xf)                         # a resumed task reuses its log: never read the last attempt's code
    except OSError:
        pass
    return procs.exit_file_wrapper(xf, cmd)


def _exit_file(t):
    """The exit code the sh wrapper wrote (run.exit), or None (older runs, or the wrapper was stopped with the worker)."""
    try:
        return int(open(os.path.join(HUB, os.path.splitext(t['log'])[0] + '.exit')).read().strip())
    except (OSError, TypeError, ValueError):
        return None


# what claude -p itself prints when a run fails; 1 Nov: matching "error" anywhere filed finished round-ups as failed because
# their summaries said a source "errored" and then read fine on retry (14 finished runs since 6 Oct, refiled as done)
FAIL_LINE = re.compile(r'^\s*(API Error|Error:|Execution error|Traceback|Failed to authenticate|Invalid API key|'
                       r'Credit balance is too low|Claude AI usage limit|'
                       r"You've hit your (usage )?limit)", re.I | re.M)
LAUNCH_LINE = re.compile(r'^\d{4}-\d\d-\d\dT\S+ (start|resume) lane=.*$', re.M)


def _finished_ok(t):
    """No exit code: done if the log ends with an answer (claude -p prints only its final answer, so a run stopped midway
    leaves none) and no failure line. _launch's own start/resume lines are not an answer."""
    try:
        tail = LAUNCH_LINE.sub('', open(os.path.join(HUB, t['log']), errors='replace').read())[-600:]
        return bool(tail.strip()) and not FAIL_LINE.search(tail)
    except Exception:
        return False


def _busy_items():
    return {(t['rid'], t['aid']) for t in ledger.q("SELECT rid, aid FROM tasks WHERE state='running' AND rid != ''")}


def _preempt_one(only_initiative=False):
    """Pause the newest background worker (or only an initiative) to make room. True if one was paused."""
    rows = ledger.q("SELECT * FROM tasks WHERE state='running' AND lane='background'" + (" AND origin='initiative'" if only_initiative else "")
                    + " ORDER BY started DESC LIMIT 1")
    if not rows:
        return False
    t = rows[0]
    ledger.x("UPDATE tasks SET note='preempted' WHERE id=?", (t['id'],))
    try:
        procs.signal_group(int(t['pid']), signal.SIGINT)
    except (OSError, TypeError, ValueError):
        pass
    ledger.event('task_preempted', t['id'], 'spine', {'for': 'asked work'})
    return True


def dispatch():
    """Start whatever may start now. Called on every submit and by the heartbeat."""
    with _lock:
        _reap()
        if os.path.exists(os.path.join(HUB, 'state', 'KILL')):      # kill switch: nothing new starts
            return
        from jarvis.spine import limits
        if limits.until_time():                                      # degraded mode: the queue waits for the limit to lift
            return
        running = ledger.q("SELECT lane, rid, aid FROM tasks WHERE state='running'")
        n = {'asked': 0, 'background': 0}
        for r in running:
            if r['lane'] in n:
                n[r['lane']] += 1
        busy = _busy_items()
        free = mem_free_pct()
        # 1) asked: queued first, then paused ones (died mid-way) in creation order
        for t in ledger.q("SELECT * FROM tasks WHERE lane='asked' AND state IN ('queued','paused') ORDER BY created"):
            if (t['rid'], t['aid']) in busy and t['rid']:
                continue
            if n['asked'] >= ASKED_MAX:
                break
            if free < MIN_FREE_PCT and (n['asked'] + n['background']) > 0:
                if not _preempt_one():
                    break                       # memory is short and nothing to pause: it waits (and says so)
                time.sleep(1.5)
            _launch(t, resume=t['state'] == 'paused')
            n['asked'] += 1
            busy.add((t['rid'], t['aid']))
            free = mem_free_pct()
        # 2) background: only when no asked task waits, memory allows, and below its cap
        waiting_asked = ledger.q("SELECT 1 FROM tasks WHERE lane='asked' AND state='queued' LIMIT 1")
        if waiting_asked:
            return
        # 13 Nov: research trials he asked for may run TRIAL_MAX steps at once; Jarvis's other own work stays at INITIATIVE_MAX
        try:
            trial_ids = {r['task_id'] for r in ledger.q("SELECT task_id FROM investigations WHERE origin='research' AND task_id IS NOT NULL")}
        except Exception:
            trial_ids = set()
        run_ini = [r['id'] for r in ledger.q("SELECT id FROM tasks WHERE state='running' AND lane='background' AND origin='initiative'")]
        n_trial = sum(1 for i in run_ini if i in trial_ids)
        n_ini = len(run_ini) - n_trial
        for t in ledger.q("SELECT * FROM tasks WHERE lane='background' AND state IN ('queued','paused') "
                          "ORDER BY (origin='initiative'), created"):                        # inbox work first
            if free < MIN_FREE_PCT + 8:
                break
            if (t['rid'], t['aid']) in busy and t['rid']:
                continue
            trial = t['origin'] == 'initiative' and t['id'] in trial_ids
            if t['origin'] == 'initiative' and ((trial and n_trial >= TRIAL_MAX) or (not trial and n_ini >= INITIATIVE_MAX)):
                continue
            try:                                         # Phase 4 governor: background spending waits for room
                from jarvis.spine import governor
                ok, why = governor.admit(t)
            except Exception as e:
                ok, why = True, ''; ledger.event('governor_error', t['id'], 'governor', {'error': str(e)[:200]})
            if not ok:
                if t.get('note') != 'governor: ' + why:
                    ledger.x("UPDATE tasks SET note=? WHERE id=?", ('governor: ' + why, t['id']))
                    ledger.event('governor_hold', t['id'], 'governor', {'why': why, 'origin': t['origin']})
                continue
            if n['background'] >= BACKGROUND_MAX:
                if t['origin'] != 'initiative' and (n_ini + n_trial) and _preempt_one(only_initiative=True):   # inbox work first: a trial step waits too
                    n['background'] -= 1
                    if n_ini:
                        n_ini -= 1
                    else:
                        n_trial -= 1
                    time.sleep(1.5)
                else:
                    break
            ledger.x("UPDATE tasks SET note=NULL WHERE id=?", (t['id'],))
            _launch(t, resume=t['state'] == 'paused')
            n['background'] += 1
            n_ini += 1 if t['origin'] == 'initiative' and not trial else 0
            n_trial += 1 if trial else 0
            busy.add((t['rid'], t['aid']))


def stop(rid, aid):
    """Stop the run on that item (whole process group) and drop queued ones for it."""
    with _lock:
        hit = False
        for t in ledger.q("SELECT * FROM tasks WHERE rid=? AND aid=? AND state IN ('queued','paused','running')", (rid, aid)):
            hit = True
            if t['state'] == 'running':
                try:
                    procs.signal_group(int(t['pid']), signal.SIGTERM)
                except (OSError, TypeError, ValueError):
                    pass
            ledger.x("UPDATE tasks SET state='stopped', ended=? WHERE id=?", (ledger.now(), t['id']))
            ledger.event('task_stopped', t['id'], 'owner', {'rid': rid, 'aid': aid})
        return hit


def running(lane=None):
    rows = ledger.q("SELECT * FROM tasks WHERE state='running'" + (' AND lane=?' if lane else '') + ' ORDER BY started DESC',
                    (lane,) if lane else ())
    return [r for r in rows if (r['id'] in _procs and _procs[r['id']].poll() is None) or _alive(r['pid'])]


def queued(lane=None):
    return ledger.q("SELECT * FROM tasks WHERE state IN ('queued','paused')" + (' AND lane=?' if lane else '') + ' ORDER BY created',
                    (lane,) if lane else ())


def record_external(lane, kind, title, pid, log='', origin='owner', session=None):
    """A worker started elsewhere (close_actions: the operator, run-now runs) gets a ledger row too. `session`: the
    --session-id it was started with, so the watchdog can see its transcript."""
    tid = dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    ledger.upsert('tasks', {'id': tid, 'rid': '', 'aid': '', 'title': title[:200], 'kind': kind, 'lane': lane, 'origin': origin,
                            'state': 'running', 'created': ledger.now(), 'started': ledger.now(), 'pid': pid, 'log': log, 'session': session,
                            'heartbeat_at': ledger.now(), 'attempts': RESUME_MAX}, ('id',))   # not resumable by us
    ledger.event('task_started', tid, origin, {'lane': lane, 'kind': kind, 'pid': pid})
    return tid
