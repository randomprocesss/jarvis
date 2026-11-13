"""Semantic matching of new data to open needs: "a shape nobody predicted". Keyword matching
(world/match.py) only finds what a need's recognizer names; a new table, file or document can answer a need in words nobody
wrote down. When new sources appear (catalog new_source / schema_change, documents the org watch found, content_read once
world/docs.py has read one), one call of the mid model (no tools) reads them next to the open needs and says which need each
could answer, how sure, and why.

run()   new sources since the last run -> semantic_matches(need_id, source_id, confidence, why). match.py merges them into
        each need's candidates: 'high' counts as strong (an initiative may check it), 'medium' as weak (shown only).
"""
from jarvis import config
from jarvis import paths
import json, os, subprocess
from jarvis.spine import ledger

HUB = str(paths.home())
CLAUDE, SONNET = (paths.claude_bin() or 'claude'), config.model('mid')
TABLE = """CREATE TABLE IF NOT EXISTS semantic_matches(need_id TEXT, source_id TEXT, confidence TEXT, why TEXT, at TEXT,
  PRIMARY KEY(need_id, source_id))"""
SCHEMA = {'type': 'object', 'properties': {'matches': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'need_id': {'type': 'string'}, 'source_id': {'type': 'string'}, 'confidence': {'type': 'string', 'enum': ['high', 'medium']},
    'why': {'type': 'string'}}, 'required': ['need_id', 'source_id', 'confidence', 'why']}}}, 'required': ['matches']}


def _cursor(v=None):
    if v is None:
        return int(ledger.cursor('semantic_event') or 0)
    ledger.cursor('semantic_event', str(v))


def run(new_sources=None, needs=None, ask=None):
    """new_sources / needs / ask: for tests (the backtest passes history in). Returns the matches stored."""
    ledger.x(TABLE)
    if new_sources is None:
        cur = _cursor()
        ev = ledger.q("SELECT * FROM catalog_events WHERE id > ? AND kind IN ('new_source','schema_change','content_read') ORDER BY id LIMIT 60", (cur,))
        if not ev:
            return []
        ids = sorted({e['source_id'] for e in ev})
        new_sources = ledger.q('SELECT * FROM sources WHERE id IN (%s)' % ','.join('?' * len(ids)), tuple(ids))
        last = ev[-1]['id']
    else:
        last = None
    if needs is None:
        needs = ledger.q("SELECT id, kind, statement, target FROM needs WHERE state IN ('open','partial') AND dismissed_at IS NULL "
                         "AND coalesce(scope, 'work') = 'work' ORDER BY created DESC LIMIT 400")
    if not new_sources or not needs:
        if last:
            _cursor(last)
        return []
    changes = {}
    if new_sources and last:                         # what changed for each source (rows from -> to, new columns, ...)
        for e in ev:
            if e['kind'] != 'content_read':          # (its content is already in the note)
                changes.setdefault(e['source_id'], []).append(f"{e['kind']}: {e['detail']}"[:200])
    def src_line(s):
        cols = ', '.join(json.loads(s.get('columns') or '[]')[:40])
        m = json.loads(s.get('meta') or '{}') or {}
        note = ' '.join(x for x in (m.get('why', ''), m.get('summary', ''), '; '.join(m.get('mentions') or [])) if x)  # any source's note
        # (5 Oct: a db change's note was hidden); a document world/docs.py read adds what it says
        what = '; '.join(changes.get(s['id'], []))
        return ' | '.join(x for x in (s['id'], str(s.get('system')), str(s.get('name')), cols or s.get('path') or '', note, what) if x)[:1200]
    from jarvis import texts
    c = config.load()
    prompt = texts.render('semantic_match', owner=c.owner_or(), work=c.work_clause(), sources='\n'.join(src_line(s) for s in new_sources),
                          needs='\n'.join(f"{n['id']} | {n['kind']} | {str(n['statement'])[:220]} | {str(n.get('target') or '')[:140]}" for n in needs))
    if ask is None:
        def ask(p):
            r = subprocess.run([CLAUDE, '-p', p, '--model', SONNET, '--output-format', 'json', '--json-schema', json.dumps(SCHEMA),
                                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', ''],
                               capture_output=True, text=True, timeout=600, cwd=HUB, env=dict(os.environ, JARVIS_PROC='1', JARVIS_LANE='semantic'))
            return (json.loads(r.stdout or '{}').get('structured_output') or {}).get('matches') or []
    valid_n, valid_s = {n['id'] for n in needs}, {s['id'] for s in new_sources}
    out = []
    for m in ask(prompt) or []:
        if m.get('need_id') in valid_n and m.get('source_id') in valid_s and m.get('confidence') in ('high', 'medium'):
            ledger.x('INSERT OR REPLACE INTO semantic_matches(need_id, source_id, confidence, why, at) VALUES(?,?,?,?,?)',
                     (m['need_id'], m['source_id'], m['confidence'], str(m.get('why') or '')[:300], ledger.now()))
            out.append(m)
    if last:
        _cursor(last)
    ledger.event('semantic_matched', '', 'semantic', {'sources': len(new_sources), 'needs': len(needs), 'matches': len(out)})
    return out


def for_need(need_id):
    ledger.x(TABLE)
    return ledger.q('SELECT * FROM semantic_matches WHERE need_id=?', (need_id,))
