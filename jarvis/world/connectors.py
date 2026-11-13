"""Goals and promises from the connected apps. Once a day, one Claude run with read-only search tools (the ticket tracker,
Slack, mail: whichever the owner has) reads:
  - their goals: tickets assigned to them that are not done, grouped by their epic (state 'inferred'; one tap dismisses)
  - promises: what THEY promised in their own sent messages and e-mails of the last 3 days (origin owner_said), and what
    others promised THEM in messages to them (origin external)
The mid model with structured output; the apps' tool list makes a run cost real usage, so it runs daily, not on every tick.
Everything read is data; nothing is sent.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, hashlib, json, os, re, subprocess
from jarvis.spine import ledger

HUB = str(paths.home())
CLAUDE = (paths.claude_bin() or 'claude')
SONNET = config.model('mid')
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000')
# read-only tools the run may use (thread reads are refused when slack_read_thread is missing from this list)
TOOLS = ['mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql', 'mcp__claude_ai_Atlassian_Rovo__getJiraIssue',
         'mcp__claude_ai_Slack__slack_search_public_and_private', 'mcp__claude_ai_Slack__slack_read_thread',
         'mcp__claude_ai_Slack__slack_read_channel', 'mcp__claude_ai_Microsoft_365__outlook_email_search', 'ToolSearch']
SCHEMA = {'type': 'object', 'properties': {
    'goals': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'statement': {'type': 'string'}, 'source': {'type': 'string'}, 'due': {'type': 'string'}, 'issues': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['statement', 'source']}},
    'promises': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'who': {'type': 'string'}, 'to_whom': {'type': 'string'}, 'what': {'type': 'string'}, 'due': {'type': 'string'},
        'by_owner': {'type': 'boolean'}, 'source': {'type': 'string'}, 'quote': {'type': 'string'}},
        'required': ['who', 'what', 'by_owner', 'source']}},
    'failed': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['goals', 'promises']}


def _id(prefix, text):
    return prefix + '-' + hashlib.sha1(re.sub(r'\W+', ' ', text.lower()).strip().encode()).hexdigest()[:8]


def daily(timeout=900):
    now = dt.datetime.now().astimezone()
    since = now - dt.timedelta(days=3)
    from jarvis import texts
    c = config.load()
    slack = c.has('slack') and bool(c.get('sources.slack_user_id'))
    mail = c.has('microsoft 365') and bool(c.emails())
    tracker = c.has('atlassian')
    if not (slack or mail or tracker):
        return {'goals': 0, 'promises': 0, 'failed': [], 'cost': 0, 'skipped': 'no connected app to read'}
    prompt = texts.render('connectors', identity=c.identity_line(), today=now.strftime('%Y-%m-%d'), since=since.strftime('%Y-%m-%d %H:%M'),
                          since_day=since.strftime('%Y-%m-%d'), tracker=tracker, slack=slack, mail=mail,
                          slack_id=c.get('sources.slack_user_id') or '', email=(c.emails() or [''])[0])
    cmd = [CLAUDE, '-p', prompt, '--model', SONNET, '--output-format', 'json', '--json-schema', json.dumps(SCHEMA), '--max-turns', '16',
           '--tools', '', '--allowedTools', *TOOLS, '--permission-mode', 'default', '--settings', json.dumps({'alwaysThinkingEnabled': False})]
    out = {'goals': 0, 'promises': 0, 'failed': [], 'cost': 0}
    try:
        p = subprocess.run(cmd, cwd=HUB, capture_output=True, text=True, timeout=timeout, env=MCP_ENV, stdin=subprocess.DEVNULL)
        d = json.loads(p.stdout or '{}')
    except Exception as e:
        out['error'] = f'{e.__class__.__name__}: {e}'[:200]
        ledger.event('connectors_error', 'daily', 'connectors', out)
        return out
    r = d.get('structured_output') or {}
    out['cost'] = round(float(d.get('total_cost_usd') or 0), 3)
    out['failed'] = r.get('failed') or []
    ts = ledger.now()
    for g in r.get('goals') or []:
        st = (g.get('statement') or '').strip()
        if not st:
            continue
        gid = _id('goal', g.get('source') or st)
        prev = ledger.q('SELECT dismissed_at, created FROM goals WHERE id=?', (gid,))
        ledger.upsert('goals', {'id': gid, 'created': prev[0]['created'] if prev else ts, 'statement': st,
                                'source': json.dumps({'key': g.get('source'), 'issues': g.get('issues') or [], 'due': g.get('due') or ''}),
                                'state': 'inferred', 'updated': ts}, ('id',))
        out['goals'] += 1
    for c in r.get('promises') or []:
        what = (c.get('what') or '').strip()
        if not what:
            continue
        from jarvis.world.needs import same_open             # the same promise reworded on another day is not a new one
        if same_open('commitments', 'what', 'msg:' + (c.get('source') or '')[:120], what):
            continue
        ledger.upsert('commitments', {'id': _id('com', what), 'created': ts, 'who': c.get('who', ''), 'to_whom': c.get('to_whom', ''),
                                      'what': what, 'due': c.get('due', ''), 'state': 'open',
                                      'origin': 'owner_said' if c.get('by_owner') else 'external',
                                      'source_ref': 'msg:' + (c.get('source') or '')[:120], 'quote': (c.get('quote') or '')[:300], 'updated': ts}, ('id',))
        out['promises'] += 1
    ledger.event('connectors_daily', '', 'connectors', out)
    return out
