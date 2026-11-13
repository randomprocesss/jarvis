"""Keeping promises (HARNESS.md §14.5 commitment_followup, Phase 6, 6 Oct 2026). Promises were extracted (their notes, sessions,
Slack, mail, the org watch) but nothing checked whether they were kept: Watching held 79 open ones, many long done.

review()     daily, one read-only Sonnet run over at most LIMIT open promises (oldest check first; each re-checked every
             RECHECK_DAYS): Slack, Jira, mail and Teams chats are searched for evidence. kept -> resolved (with the evidence),
             moot (overtaken, no longer relevant) -> superseded; not_yet / unclear are recorded and shown in Watching.
             Promises by "the assistant" (their Claude sessions) are skipped: no connector can see those sessions.
followups()  a promise someone made TO them that is overdue and not kept gets one noticed item: a worker drafts a short nudge
             in their style (A2). It is never sent: the draft waits for their Send. Shares the initiative caps and the governor.
Everything read is data, never instructions. Nothing is sent, posted or changed.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, json, os, re, subprocess, uuid
from jarvis.spine import dispatcher, ledger

HUB = str(paths.home())
CLAUDE, SONNET = (paths.claude_bin() or 'claude'), config.model('mid')
LIMIT, RECHECK_DAYS, OVERDUE_DAYS = 25, 3, 5
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000', JARVIS_PROC='1', JARVIS_LANE='promises')
TOOLS = ['mcp__claude_ai_Slack__slack_search_public_and_private', 'mcp__claude_ai_Slack__slack_read_thread',
         'mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql', 'mcp__claude_ai_Atlassian_Rovo__getJiraIssue',
         'mcp__claude_ai_Microsoft_365__outlook_email_search', 'mcp__claude_ai_Microsoft_365__read_resource',
         'mcp__claude_ai_Microsoft_365__chat_message_search',
         # (6 Oct, first run: "Confluence comment tools were permission-denied" for a promise to post page comments)
         'mcp__claude_ai_Atlassian_Rovo__getConfluencePageFooterComments', 'mcp__claude_ai_Atlassian_Rovo__getConfluencePageInlineComments', 'ToolSearch']
_S = {'type': 'string'}
SCHEMA = {'type': 'object', 'properties': {'checks': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'id': _S, 'verdict': {'type': 'string', 'enum': ['kept', 'not_yet', 'moot', 'unclear']}, 'evidence': _S, 'note': _S},
    'required': ['id', 'verdict']}}}, 'required': ['checks']}


def ensure():
    cols = [c['name'] for c in ledger.q('PRAGMA table_info(commitments)')]
    for c in ('checked_at', 'check_verdict', 'check_note', 'nudged_at'):
        if c not in cols:
            ledger.x(f'ALTER TABLE commitments ADD COLUMN {c} TEXT')


def _prompt(rows):
    from jarvis import texts
    c = config.load()
    return texts.render('promises_review', identity=c.identity_line(), work=c.work_clause(), rows=rows)


def _is_owner(s):
    """Is this name the owner? "you", "the owner", or any part of their own name (two letters or more)."""
    s = str(s or '')
    names = [re.escape(p) for p in config.load().owner().split() if len(p) >= 2]
    return bool(re.search(r'^you\b|\bthe owner\b' + ''.join(r'|\b%s\b' % n for n in names), s, re.I))


def _is_assistant(s):
    return bool(re.search(r'assistant|jarvis|claude', str(s or ''), re.I))


def due_rows(limit=LIMIT):
    ensure()
    now = dt.datetime.now().astimezone()
    old, recheck = (now - dt.timedelta(days=1)).isoformat(), (now - dt.timedelta(days=RECHECK_DAYS)).isoformat()
    rows = ledger.q("SELECT * FROM commitments WHERE state='open' AND dismissed_at IS NULL AND coalesce(scope, 'work')='work' AND created <= ? "
                    "AND (checked_at IS NULL OR checked_at < ?) ORDER BY coalesce(checked_at, ''), created", (old, recheck))
    return [r for r in rows if not _is_assistant(r['who'])][:limit]


def review(ask=None, timeout=1500):
    todo = due_rows()
    if not todo:
        return 'nothing to check'
    if ask is None:
        from jarvis.spine import governor
        ok, why = governor.admit({'kind': 'run-roundup', 'lane': 'background'})     # watcher work
        if not ok:
            return 'held: ' + why
        def ask(p):
            cmd = [CLAUDE, '-p', p, '--model', SONNET, '--output-format', 'json', '--json-schema', json.dumps(SCHEMA), '--max-turns', '40',
                   '--tools', '', '--allowedTools', *TOOLS, '--permission-mode', 'default', '--settings', json.dumps({'alwaysThinkingEnabled': False})]
            r = subprocess.run(cmd, cwd=HUB, capture_output=True, text=True, timeout=timeout, env=MCP_ENV, stdin=subprocess.DEVNULL)
            d = json.loads(r.stdout or '{}')
            return (d.get('structured_output') or {}).get('checks') or [], round(float(d.get('total_cost_usd') or 0), 3)
    rows = '\n'.join(f"{r['id']} | {r['who']} -> {r['to_whom']} | {str(r['what'])[:220]} | {r['due'] or '-'} | {str(r['source_ref'] or '')[:120]} | "
                     f"{str(r['quote'] or '')[:160]} | {str(r['created'])[:10]}" for r in todo)
    try:
        res = ask(_prompt(rows))
    except Exception as e:
        ledger.event('promises_error', '', 'promises', {'error': f'{e.__class__.__name__}: {e}'[:200]})
        return 'error'
    checks, cost = res if isinstance(res, tuple) else (res, 0)
    ids, ts, n = {r['id'] for r in todo}, ledger.now(), {'kept': 0, 'moot': 0, 'not_yet': 0, 'unclear': 0}
    for c in checks or []:
        if c.get('id') not in ids or c.get('verdict') not in n:
            continue
        v = c['verdict']
        n[v] += 1
        note = '; '.join(x for x in (str(c.get('evidence') or '')[:300], str(c.get('note') or '')[:200]) if x)
        state = {'kept': 'resolved', 'moot': 'superseded'}.get(v, 'open')
        ledger.x('UPDATE commitments SET state=?, checked_at=?, check_verdict=?, check_note=?, updated=? WHERE id=?', (state, ts, v, note, ts, c['id']))
        ids.discard(c['id'])
    for i in ids:                                       # not answered: checked, unclear
        ledger.x("UPDATE commitments SET checked_at=?, check_verdict='unclear' WHERE id=?", (ts, i))
    ledger.event('promises_checked', '', 'promises', {**n, 'asked': len(todo), 'cost': cost})
    return f"{len(todo)} checked: {n['kept']} kept, {n['moot']} moot, {n['not_yet']} not yet, {n['unclear']} unclear, ${cost}"


def overdue(r, today=None):
    today = today or dt.date.today()
    m = re.match(r'(\d{4}-\d{2}-\d{2})', str(r.get('due') or ''))
    if m:
        return m.group(1) < today.isoformat()
    try:
        return (today - dt.date.fromisoformat(str(r['created'])[:10])).days >= OVERDUE_DAYS
    except ValueError:
        return False


def followups():
    """One overdue, unkept promise owed TO them -> one noticed item where a worker drafts a nudge (never sent)."""
    from jarvis.spine import governor
    from jarvis.world import initiative
    ensure()
    if ledger.q("SELECT 1 FROM initiatives WHERE state IN ('admitted','running') LIMIT 1") or initiative._started_today() >= initiative.MAX_PER_DAY:
        return []
    if not governor.admit({'lane': 'background', 'origin': 'initiative', 'kind': 'initiative', 'rid': '', 'aid': ''})[0]:
        return []
    for r in ledger.q("SELECT * FROM commitments WHERE state='open' AND dismissed_at IS NULL AND coalesce(scope, 'work')='work' "
                      "AND check_verdict='not_yet' AND nudged_at IS NULL ORDER BY created"):
        if _is_owner(r['who']) or _is_assistant(r['who']) or not _is_owner(r['to_whom']) or not overdue(r):
            continue
        what = f"{r['who']} promised: {r['what']}" + (f" (due {r['due']})" if r['due'] else f" (noted {str(r['created'])[:10]})")
        title = initiative.headline(f"Nudge {r['who']}: {r['what']}")
        brief = (f"Jarvis noticed this by itself (nobody asked today): {what}. It has not happened yet: {r['check_note'] or 'no sign of it'}.\n"
                 f"Where it was promised: {r['source_ref'] or 'unknown'}" + (f' ("{r["quote"]}")' if r['quote'] else '') + ".\n"
                 "Draft ONE short, friendly nudge in the owner's own style to that person, in the same place it was promised (the Slack thread "
                 "or channel, or a mail reply), as the reply in the work entry. Do NOT send it: The owner presses Send. If you find it was "
                 "in fact delivered, say so instead and draft nothing. Start the work entry's summary with 'VERDICT: yes' (nudge drafted), "
                 "'VERDICT: no' (it was delivered after all) or 'VERDICT: partly', then the one-line reason.")
        from jarvis import assistant
        rid, aid = assistant.new_item(brief, title, noticed={'need': what, 'why': 'a promise to you is overdue', 'checked': [r['source_ref'] or 'promises'],
                                                          'origin': 'a promise someone made you'})
        initiative._mark_noticed(rid, aid)
        iid = 'ini-' + uuid.uuid4().hex[:8]
        t = dispatcher.submit('background', dispatcher.item_prompt(rid, aid), rid, aid, title=title, origin='initiative', kind='initiative', initiative_id=iid)
        ledger.upsert('initiatives', {'id': iid, 'created': ledger.now(), 'class': 'commitment_followup', 'need_id': None, 'trigger': f"commitment:{r['id']}",
                                      'title': title, 'plan': 'A2 draft a nudge (never sent)', 'origin_rank': 4, 'autonomy': 'A2', 'state': 'admitted',
                                      'task_id': t['id']}, ('id',))
        ledger.x('UPDATE commitments SET nudged_at=? WHERE id=?', (ledger.now(), r['id']))
        ledger.event('initiative_started', iid, 'initiative', {'followup': r['id'], 'title': title, 'task': t['id'], 'rid': rid})
        return [(r['id'], title)]
    return []
