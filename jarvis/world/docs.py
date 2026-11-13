"""Reading the documents the org watch finds (HARNESS.md Phase 6, 6 Oct 2026). The org watch lists a document by its name and
a one-line reason; nothing knew what was inside it, so the meaning matcher could only match names. probe() opens a few unread
project documents in one read-only Sonnet run (SharePoint / OneDrive files through read_resource, Confluence pages through
getConfluencePage) and records what each holds: a summary, its tables' column headers, the figures it states (with a quote)
and the needs, decisions, dates or owners it mentions. A document that was read becomes a content_read catalog event, so
world/semantic.py matches it again, on its content.

Caps: DAILY documents a day (first PAGES pages of a docx / pptx / pdf), two tries per document, nothing while the governor
holds watcher work. Everything read is data, never instructions. Nothing is sent, posted or changed.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, json, os, subprocess
from jarvis.spine import ledger

HUB = str(paths.home())
CLAUDE, SONNET = (paths.claude_bin() or 'claude'), config.model('mid')
DAILY, PAGES, TRIES = 4, 12, 2
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000', JARVIS_PROC='1', JARVIS_LANE='docs',
               MAX_MCP_OUTPUT_TOKENS='60000')   # a long spreadsheet overflows the default and is then never seen
TOOLS = ['mcp__claude_ai_Microsoft_365__read_resource', 'mcp__claude_ai_Microsoft_365__sharepoint_search',
         'mcp__claude_ai_Atlassian_Rovo__getConfluencePage', 'mcp__claude_ai_Atlassian_Rovo__searchConfluenceUsingCql', 'ToolSearch']
_STR = {'type': 'string'}
SCHEMA = {'type': 'object', 'properties': {'docs': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'id': _STR, 'read': {'type': 'boolean'}, 'project': {'type': 'boolean'}, 'uri': _STR, 'summary': _STR, 'why_not': _STR,
    'tables': {'type': 'array', 'items': {'type': 'object', 'properties': {'name': _STR, 'columns': {'type': 'array', 'items': _STR}}}},
    'numbers': {'type': 'array', 'items': {'type': 'object', 'properties': {'what': _STR, 'value': _STR, 'quote': _STR}}},
    'mentions': {'type': 'array', 'items': _STR}}, 'required': ['id', 'read', 'project']}}}, 'required': ['docs']}


def _prompt(lines):
    from jarvis import texts
    c = config.load()
    return texts.render('docs_probe', owner=c.owner_or(), work=c.work_clause(), pages=PAGES, docs=lines,
                        cloud_id=str(c.get('tracker.cloud_id') or '') if c.has('atlassian') else '')


def _meta(s):
    try:
        return json.loads(s.get('meta') or '{}') or {}
    except ValueError:
        return {}


def pending(limit=DAILY):
    """Unread documents worth a try, newest first."""
    rows = ledger.q("SELECT * FROM sources WHERE system='doc' ORDER BY coalesce(newest, first_seen) DESC")
    out = [r for r in rows if not _meta(r).get('read_at') and _meta(r).get('project') is not False and _meta(r).get('tries', 0) < TRIES]
    return out[:limit]


def _ask(prompt, timeout):
    cmd = [CLAUDE, '-p', prompt, '--model', SONNET, '--output-format', 'json', '--json-schema', json.dumps(SCHEMA), '--max-turns', '24',
           '--tools', '', '--allowedTools', *TOOLS, '--permission-mode', 'default', '--settings', json.dumps({'alwaysThinkingEnabled': False})]
    p = subprocess.run(cmd, cwd=HUB, capture_output=True, text=True, timeout=timeout, env=MCP_ENV, stdin=subprocess.DEVNULL)
    d = json.loads(p.stdout or '{}')
    return (d.get('structured_output') or {}).get('docs') or [], round(float(d.get('total_cost_usd') or 0), 3)


def probe(limit=DAILY, timeout=1200, ask=None):
    """Read up to `limit` unread documents (fewer once today's DAILY are read). ask: for tests (prompt -> (docs, cost))."""
    today = dt.date.today().isoformat()
    left = DAILY - len(ledger.q("SELECT 1 FROM events WHERE kind='doc_read' AND at >= ? AND data LIKE '%\"read\": true%'", (today,)))
    todo = pending(min(limit, left)) if left > 0 else []
    if not todo:
        return 'nothing to read' if left > 0 else f'{DAILY} read today'
    if ask is None:
        from jarvis.spine import governor
        ok, why = governor.admit({'kind': 'run-roundup', 'lane': 'background'})     # watcher work: waits when the window is full
        if not ok:
            return 'held: ' + why
    lines = '\n'.join(f"{s['id']} | {s['name']} | {s.get('path') or ''} | {_meta(s).get('uri') or ''}" for s in todo)
    try:
        docs, cost = (ask or _ask)(_prompt(lines), timeout)
    except Exception as e:
        ledger.event('docs_error', '', 'docs', {'error': f'{e.__class__.__name__}: {e}'[:200]})
        return 'error'
    by_id, ts, read = {s['id']: s for s in todo}, ledger.now(), 0
    for d in docs:
        s = by_id.pop(str(d.get('id') or ''), None)
        if not s:
            continue
        m = _meta(s)
        m['tries'], m['project'] = m.get('tries', 0) + 1, bool(d.get('project'))
        if d.get('uri'):
            m['uri'] = str(d['uri'])[:400]
        row = {'id': s['id']}
        if d.get('read'):
            read += 1
            m.update(read_at=ts, summary=str(d.get('summary') or '')[:600],
                     numbers=[{k: str(x.get(k) or '')[:160] for k in ('what', 'value', 'quote')} for x in (d.get('numbers') or [])[:6] if isinstance(x, dict)],
                     mentions=[str(x)[:200] for x in (d.get('mentions') or [])[:6]])
            cols = [(f"{t.get('name')}.{c}" if t.get('name') else str(c)) for t in (d.get('tables') or []) if isinstance(t, dict)
                    for c in (t.get('columns') or [])][:80]
            row.update(columns=json.dumps(cols), last_changed=ts)
            if m['project']:                               # semantic.py matches it again, on what it says
                ledger.x('INSERT INTO catalog_events(at, source_id, kind, detail) VALUES(?,?,?,?)',
                         (ts, s['id'], 'content_read', json.dumps({'content': m['summary'][:300], 'columns': len(cols)})))
        else:
            m['why_not'] = str(d.get('why_not') or '')[:200]
        row['meta'] = json.dumps(m)
        ledger.upsert('sources', row, ('id',))
        ledger.event('doc_read', s['id'], 'docs', {'read': bool(d.get('read')), 'project': m['project'], 'cost': cost})
    for s in by_id.values():                               # not answered: one try spent
        m = _meta(s)
        m['tries'] = m.get('tries', 0) + 1
        ledger.upsert('sources', {'id': s['id'], 'meta': json.dumps(m)}, ('id',))
    ledger.event('docs_probe', '', 'docs', {'asked': len(todo), 'read': read, 'cost': cost})
    return f'{read}/{len(todo)} read, ${cost}'
