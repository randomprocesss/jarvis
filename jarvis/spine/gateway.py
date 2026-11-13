"""The action gateway (HARNESS.md §6.2, Phase 2, 5 Oct 2026): every state-changing request passes here.

Tiers (their rules):
  T0 read          GETs; never recorded
  T1 internal      ticks, answers, starting/stopping their tasks, run-now, saving drafts: run at once, recorded
  T2 outward       a send, a Jira comment, ticket creation, approving a request: only from the Jarvis page itself (their
                   press or their voice confirmation after the read-back); a worker or any other local process is refused
  T3 consequential database writes, merges, Weekly close writes: only from the page, and only as their approval of a request
Every recorded action gets a payload hash, an idempotency key (the same request twice within 30 s returns the first
receipt instead of running twice: a double press, or a voice "yes" heard twice) and a receipt (what really happened).
Phase 3 adds a per-launch token for the page; until then the page is recognised by Origin/Referer, and the worker hook
(tools/worker_hook.py) refuses any worker command that calls these endpoints.
"""
from jarvis import paths
import hmac, json, os, threading, time
from jarvis.spine import ledger

ORIGIN = 'http://localhost:8765'
# The page key (Phase 3, 5 Oct 2026): Jarvis.app keeps it in the Keychain and writes it to this file just before it starts the
# server; the server reads and deletes it on boot. A send/approve/write (T2/T3) must carry it as X-Jarvis-Key, so a worker
# that forges Origin is still refused. No key (server not started by the app) = T2/T3 refused.
KEY_FILE = str(paths.home() / 'state/page.key.boot')
PAGE_KEY = None


def load_page_key():
    global PAGE_KEY
    try:
        k = open(KEY_FILE).read().strip()
        os.remove(KEY_FILE)
        if len(k) >= 32:
            PAGE_KEY = k
    except OSError:
        pass
    return bool(PAGE_KEY)
TIER = {'/api/inbox_tick': 'T1', '/api/answer': 'T1', '/api/jarvis_task': 'T1', '/api/run_now': 'T1', '/api/tickets_save': 'T1',
        '/api/consult_done': 'T1', '/api/reminder_done': 'T1', '/api/jarvis_drop': 'T1', '/api/jarvis_model': 'T1',
        '/api/open_output': 'T1',
        '/api/watch': 'T1', '/api/share_file': 'T1', '/api/slack_leftover_done': 'T1', '/api/learn_signal': 'T1', '/api/learned_undo': 'T1',
        '/api/learned_label': 'T1', '/api/learned_retire': 'T1', '/api/autopilot': 'T1', '/api/jarvis_prewarm': 'T1', '/api/research_mark': 'T1',     # copies a Jarvis-made table into the shared folder (no message)
        '/api/approvals_add': 'T2', '/api/slack_send': 'T2', '/api/key_check': 'T2', '/api/jira/comment': 'T2', '/api/jira/create_tickets': 'T2', '/api/jira/done': 'T2',
        '/api/forge_promote': 'T3',                    # a tool Jarvis built becomes trusted code: his press
        # 12 Nov (the ladder): raising a level is his press in the app window; lowering one and stopping a held reply are the
        # safe direction and work from any local page
        '/api/ladder_raise': 'T2', '/api/ladder_lower': 'T1', '/api/held_stop': 'T1'}
QUIET = {'/api/jarvis_drop', '/api/consult_done', '/api/reminder_done', '/api/jarvis_prewarm'}   # too chatty to record
_recent, _lock = {}, threading.Lock()


def tier(path, body):
    t = TIER.get(path, 'T1')
    if path == '/api/approvals_set':
        t = 'T2' if str(body.get('status')) == 'approved' else 'T1'
    if path == '/api/skill_decide':                # adding a skill Jarvis drafted changes what every session can do: his press
        t = 'T2' if body.get('add') is not False else 'T1'
    if path.startswith('/api/jira/') and body.get('dry', True) is not False:
        t = 'T0'                                   # a dry run only shows the plan
    return t


def from_page(headers):
    o = headers.get('Origin') or ''
    r = headers.get('Referer') or ''
    return o == ORIGIN or (not o and r.startswith(ORIGIN + '/'))


def check(path, body, headers):
    """(ok, error). T2/T3 only from the Jarvis page."""
    t = tier(path, body)
    if t in ('T2', 'T3'):
        if not from_page(headers):
            ledger.event('gateway_refused', path, 'unknown', {'tier': t, 'why': 'not from the Jarvis page'})
            return False, 'This action needs the owner: only the Jarvis page can send, approve or write.'
        if not PAGE_KEY:
            ledger.event('gateway_refused', path, 'unknown', {'tier': t, 'why': 'no page key (server not started by Jarvis.app)'})
            return False, 'Open Jarvis from the Jarvis app to send, approve or write (this server was not started by the app).'
        if not hmac.compare_digest(str(headers.get('X-Jarvis-Key') or ''), PAGE_KEY):
            ledger.event('gateway_refused', path, 'unknown', {'tier': t, 'why': 'wrong or missing page key'})
            return False, 'Only the Jarvis app window can send, approve or write.'
    return True, ''


def begin(path, body, headers):
    """Records the action; returns (action_id, previous_response or None). previous_response = same request <30 s ago."""
    if path in QUIET:
        return None, None
    t = tier(path, body)
    payload = {k: v for k, v in (body or {}).items() if k not in ('screen',)}
    h = ledger.sha({'p': path, 'b': payload})
    now = time.time()
    with _lock:
        for k in [k for k, (ts, _) in _recent.items() if now - ts > 30]:
            _recent.pop(k, None)
        prev = _recent.get(h)
        if prev and prev[1] is not None and t != 'T0':
            return None, prev[1]
        _recent[h] = (now, None)
    aid = ledger.x('INSERT INTO actions(at, type, tier, origin, payload, hash, idem, state) VALUES(?,?,?,?,?,?,?,?)',
                   (ledger.now(), path, t, 'page' if from_page(headers) else 'local', json.dumps(payload, ensure_ascii=False)[:4000],
                    h, h, 'running'))
    return aid, None


def end(aid, path, body, response, code):
    """Stores the receipt (a short summary of what really happened)."""
    if aid is None:
        return
    ok = code < 400 and not (isinstance(response, dict) and (response.get('ok') is False or response.get('error')))
    receipt = response if not isinstance(response, dict) else {k: response[k] for k in list(response)[:12]
                                                               if k in ('ok', 'error', 'state', 'worker', 'started', 'id', 'task_id',
                                                                        'queued_behind', 'steered', 'dry', 'result', 'already')}
    ledger.x('UPDATE actions SET state=?, receipt=?, ended=? WHERE id=?',
             ('done' if ok else 'failed', json.dumps(receipt, ensure_ascii=False, default=str)[:2000], ledger.now(), aid))
    h = ledger.sha({'p': path, 'b': {k: v for k, v in (body or {}).items() if k not in ('screen',)}})
    with _lock:
        if h in _recent:
            _recent[h] = (_recent[h][0], (response, code))
