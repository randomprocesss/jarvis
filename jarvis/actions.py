"""Approved actions and runs on request.

An action that reaches other people or systems (a ticket comment, a write) is recorded as an approved request in
approvals.json when the owner presses its button or says yes to Jarvis, and a Claude worker starts right away to carry
it out with the connected apps, following the jarvis-approved-actions skill. That skill picks up every approved request,
so a second press while one runs is simply done by that same worker or the next one. Nothing that is not approved is done.

run_now() starts a routine at once when the owner asks for it, instead of waiting for its next scheduled run.
"""
from jarvis import config
from jarvis import procs
from jarvis import paths
import datetime as dt, json, os, re, subprocess, threading, time, uuid

HUB = str(paths.home())
APPROVALS = os.path.join(HUB, 'approvals.json')
CLAUDE = (paths.claude_bin() or 'claude')
PIDFILE = os.path.join(HUB, 'work', 'outputs', 'approved-worker.pid')


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def _worker_alive():
    try:
        pid = int(open(PIDFILE).read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def queue_jira_comment(key, text, rid='', aid='', by=''):
    """Post to Jira (button or voice) without a Jira token: the owner's press or spoken OK is their approval for this one
    comment. Queued as an approved `jira` request and done by a Claude worker with the Atlassian connector."""
    d = json.load(open(APPROVALS)) if os.path.exists(APPROVALS) else {'items': []}
    items = d.get('items', [])
    for x in items:
        p = x.get('payload') or {}
        if x.get('kind') == 'jira' and p.get('action') == 'comment' and p.get('key') == key and p.get('text') == text \
                and x.get('status') in ('approved', 'running', 'done'):
            return {'ok': True, 'queued': True, 'already': x['status'], 'id': x['id'], 'worker': _spawn() if x['status'] != 'done' else 'idle'}
    now = _now()
    item = {'id': 'ap-jira-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S'), 'kind': 'jira',
            'title': f'Post your comment on {key}',
            'why': ('Released by the ladder after the 10-minute hold: this kind of comment is at "holds, then sends".' if by == 'ladder'
                    else 'You pressed Post (or said so to Jarvis).'),
            'plan': f'Add this exact text as a comment on {key} with the Atlassian connector, after checking it is not already there. '
                    'Then mark the item\'s reply sent and tick its points.',
            'payload': {'action': 'comment', 'key': key, 'text': text},
            'related': {'rid': rid, 'aid': aid} if rid and aid else {}, 'status': 'approved', 'created': now,
            'created_by': 'Jarvis (ladder)' if by == 'ladder' else 'Jarvis (Post to Jira)', 'approved_at': now}
    items.append(item)
    d['items'] = items
    tmp = APPROVALS + '.tmp'
    json.dump(d, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, APPROVALS)
    return {'ok': True, 'queued': True, 'id': item['id'], 'worker': _spawn()}


# Claude workers wait for the connectors (Jira, Slack, mail) to come up before their first step; without this a
# `claude -p` run can start before they connect and see none of their tools.
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000')
# Every worker runs with the worker settings (no commit attribution, no memory writes, kill switch + shared-record lint
# hooks; spine/dispatcher.py) and is a row in the ledger.
SETTINGS = os.path.join(HUB, 'state', 'worker-settings.json')
OPERATOR_SETTINGS = os.path.join(HUB, 'state', 'operator-settings.json')     # worker settings + canvases at Send
WORKER_ENV = dict(MCP_ENV, JARVIS_PROC='1')
SONNET = config.model('mid')


def _ledger(lane, kind, title, pid, log, origin, session=None):
    try:
        from jarvis.spine import dispatcher
        return dispatcher.record_external(lane, kind, title, pid, os.path.relpath(log, HUB), origin, session=session)
    except Exception:
        return None


def _work_model():
    """The model chosen for tasks in the model switcher (voice settings brain_work); none = Claude's default (Opus 5.5)."""
    try:
        from jarvis import assistant
        m = assistant.model_id('work')
        return ['--model', m] if m else []
    except Exception:
        return []


_again = {'want': False, 'watch': False}


def _rerun_when_free():
    """A press while the worker is busy: it may already have read approvals.json, so start another the moment it ends
    (never leave it for the hourly safety net: what the owner asks for runs now)."""
    while _worker_alive():
        time.sleep(5)
    _again['watch'] = False
    if _again['want']:
        _again['want'] = False
        _spawn()


def _spawn():
    """One detached worker at a time, in Auto mode like the routines; it survives this request."""
    if _worker_alive():
        _again['want'] = True
        if not _again['watch']:
            _again['watch'] = True
            threading.Thread(target=_rerun_when_free, daemon=True, name='approvals-rerun').start()
        return 'running'
    ts = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    out = os.path.join(HUB, 'work', 'outputs', f'approved-{ts}')
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, 'run.log'), 'ab')
    from jarvis.spine.dispatcher import exit_wrapped          # its exit code in run.exit, so a finished run is never filed as failed
    p = subprocess.Popen(exit_wrapped([CLAUDE, '-p', 'Use the jarvis-approved-actions skill now: carry out the approved requests in '
                          f'{APPROVALS} (the owner just pressed a button or confirmed by voice; a ticket comment is kind "jira"). '
                          'Do nothing that is not approved.', '--session-id', (sid := str(uuid.uuid4())),
                          '--permission-mode', 'auto', '--settings', OPERATOR_SETTINGS] + _work_model(), os.path.join(out, 'run.log')),
                         cwd=str(paths.workdir()), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         **procs.session_kw(), env=dict(WORKER_ENV, JARVIS_LANE='operator'))
    open(PIDFILE, 'w').write(str(p.pid))
    _ledger('operator', 'approved-actions', 'Carry out what the owner approved', p.pid, os.path.join(out, 'run.log'), 'owner', sid)
    return 'started'


# ---------------------------------------------------------------- on request, now: what the owner asks for does not wait
# for the next routine; routines are for regular work and things done automatically
ROUTINES = paths.repo('skills', 'routines')        # each routine's own prompt, shipped with the skills
PROPOSALS = os.path.join(HUB, 'tickets', 'proposals.json')
RUN_NOW = {
    # what: (routine whose prompt to run, or None, line that says it is the owner's request)
    'roundup': ('inbox-roundup', 'The owner asked for this check now (not the hourly run): fetch since the last check.'),
    'work': ('work-items', 'The owner asked for this work run now (not the half-past run): work every open item now.'),
    'tickets': (None, 'Use the jarvis-tickets skill, create step, for approved proposals only: the owner just pressed Create '
                      'in Jarvis, which is their approval. First claim the lock: '
                      f'`"{paths.python()}" "{paths.tool("worklock.py")}" claim tickets create --by "app: Create pressed"` '
                      '(exit 1 = another run is creating them: stop). Create every '
                      f'proposal in {PROPOSALS} with status "ready" AND approved_at AND no key, writing '
                      'status "created", key and created_at right after each one; then release the lock the same way '
                      '(`release tickets create`).'),
}


def _prompt(routine):
    """A routine's own prompt, filled in for the owner (skills/routines/<routine>.md without its front matter), so a run on
    request does exactly what the scheduled one does."""
    from jarvis import skillset
    return skillset.routine(routine)


def run_now(what, origin='owner'):
    """Start that run at once, detached (it survives the server). One at a time per kind: a second ask while one runs says so.
    origin 'owner' = they asked (asked lane); 'spine' = the heartbeat's inbox watcher (background). Round-ups run on Sonnet:
    fetching and summarising does not need the task model."""
    if what not in RUN_NOW:
        return {'ok': False, 'error': 'unknown run'}
    pidf = os.path.join(HUB, 'work', 'outputs', f'run-{what}.pid')
    try:
        os.kill(int(open(pidf).read().strip()), 0)
        return {'ok': True, 'state': 'running'}
    except (OSError, ValueError):
        pass
    routine, line = RUN_NOW[what]
    try:
        prompt = (_prompt(routine) + '\n\n' + line) if routine else line
    except OSError:
        return {'ok': False, 'error': f'the {routine} routine is not installed'}
    ts = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    out = os.path.join(HUB, 'work', 'outputs', f'run-{what}-{ts}')
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, 'run.log'), 'ab')
    model = ['--model', SONNET] if what == 'roundup' else _work_model()
    lane = 'asked' if origin == 'owner' else 'background'
    sid = str(uuid.uuid4())                 # a session id lets the watchdog see the run's transcript (see dispatcher._activity)
    from jarvis.spine.dispatcher import exit_wrapped          # its exit code in run.exit
    p = subprocess.Popen(exit_wrapped([CLAUDE, '-p', prompt, '--session-id', sid, '--permission-mode', 'auto', '--settings', SETTINGS] + model,
                                      os.path.join(out, 'run.log')),
                         cwd=str(paths.workdir()), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         **procs.session_kw(), env=dict(WORKER_ENV, JARVIS_LANE=lane, JARVIS_RUN_KIND=what,
                                                          JARVIS_RUN_START=dt.datetime.now().astimezone().isoformat(timespec='seconds')))
    open(pidf, 'w').write(str(p.pid))
    _ledger(lane, 'run-' + what, {'roundup': 'Check messages', 'work': 'Work on the inbox', 'tickets': 'Create approved tickets'}.get(what, what),
            p.pid, os.path.join(out, 'run.log'), origin, sid)
    return {'ok': True, 'state': 'started', 'log': os.path.relpath(os.path.join(out, 'run.log'), HUB)}
