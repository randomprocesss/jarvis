"""Matching needs to data (HARNESS.md §14.3, Phase 2, 5 Oct 2026). Plain code; no model.

For every open need, its recognizer (tables, columns, keywords, named sources) is looked up in the catalog:
  strong   a source whose table name IS one the need names (ORDERS, SHIPLOG, sales.order_lines...), or whose columns contain
           a column the need names
  weak     keyword hits on names or columns only
It runs when a need is registered (against everything that already exists: the data may have been there all along) and
whenever the catalog reports a change (new_source, schema_change, new_range). Strong candidates for needs that may act
(origin owner_said / owner_ack, untainted, data or answer needs) become initiative proposals (world/initiative.py), which
decide whether to probe. Weak candidates are only listed on the need, for the Watching view.
"""
import json, re
from jarvis.spine import ledger

ACTIVE = ('open', 'partial')
# columns nearly every table has: matching on them alone proves nothing
GENERIC = {'id', 'key', 'code', 'name', 'type', 'status', 'date', 'created', 'updated', 'created_at', 'updated_at', 'amount', 'quantity',
           'unit', 'user', 'user_id', 'owner', 'description', 'notes', 'value'}


def _norm(s):
    return re.sub(r'[^a-z0-9_.]', '', (s or '').lower())


def candidates(need, sources=None):
    r = (json.loads(need.get('recognizer') or '{}') or {}).get('look_for') or {}     # only what would ANSWER it, never context
    tables = {_norm(t) for t in r.get('tables') or [] if len(_norm(t)) >= 3}
    cols = {_norm(c) for c in r.get('columns') or [] if len(_norm(c)) >= 3} - GENERIC
    kws = {_norm(k) for k in r.get('keywords') or [] if len(_norm(k)) >= 4}
    sources = sources if sources is not None else ledger.q('SELECT id, system, name, columns, rows, newest, local FROM sources')
    out = []
    for s in sources:
        name = _norm(s['name'])
        short = name.split('.')[-1]
        scols = {_norm(c) for c in json.loads(s['columns'] or '[]')}
        strong = bool(tables & {name, short}) or (len(cols) >= 2 and len(cols & scols) >= 2)
        weak = sum(1 for k in kws if k in name or k in scols)
        if strong or weak >= 2:
            out.append({'source': s['id'], 'strength': 'strong' if strong else 'weak', 'rows': s.get('rows'),
                        'matched': sorted((tables & {name, short}) | (cols & scols))[:6] or sorted(k for k in kws if k in name or k in scols)[:6]})
    # Phase 6: what Claude judged could answer it (world/semantic.py): high = strong, medium = weak
    try:
        from jarvis.world import semantic
        have = {c['source'] for c in out}
        for m in semantic.for_need(need.get('id')):
            if m['source_id'] not in have:
                out.append({'source': m['source_id'], 'strength': 'strong' if m['confidence'] == 'high' else 'weak', 'rows': None,
                            'matched': ['meaning: ' + str(m['why'])[:120]]})
    except Exception:
        pass
    out.sort(key=lambda c: (c['strength'] != 'strong', -(c['rows'] or 0)))
    return out[:15]


def run(need_ids=None):
    """Match the given needs (or every active one). Returns {'needs': n, 'strong': [(need_id, source_id)], 'newly_strong': [...]}."""
    srcs = ledger.q('SELECT id, system, name, columns, rows, newest, local FROM sources')
    q = "SELECT * FROM needs WHERE state IN (%s) AND dismissed_at IS NULL AND coalesce(scope, 'work') = 'work'" % ','.join('?' * len(ACTIVE))   # project work only
    rows = ledger.q(q, ACTIVE)
    if need_ids is not None:
        rows = [n for n in rows if n['id'] in set(need_ids)]
    res = {'needs': len(rows), 'strong': [], 'newly_strong': []}
    for n in rows:
        cands = candidates(n, srcs)
        before = {c['source'] for c in json.loads(n.get('candidates') or '[]') if c.get('strength') == 'strong'}
        strong = [c['source'] for c in cands if c['strength'] == 'strong']
        ledger.x('UPDATE needs SET candidates=?, last_checked=? WHERE id=?', (json.dumps(cands), ledger.now(), n['id']))
        for s in strong:
            res['strong'].append((n['id'], s))
            if s not in before:
                res['newly_strong'].append((n['id'], s))
    ledger.event('needs_matched', '', 'match', {'needs': res['needs'], 'strong': len(res['strong']), 'new': len(res['newly_strong'])})
    return res
