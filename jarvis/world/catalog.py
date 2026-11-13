"""The source catalog: what data exists in the folders the owner chose, and what changed.

scan('local') walks the folders in settings (sources.folders, then work.folders). Every data file (csv, tsv, json, jsonl, xlsx,
xls, parquet) becomes a source in the ledger's `sources`: its name, where it is, its columns when they can be read without
extra packages (the header row of a csv or tsv, the keys of a jsonl's first record), its size, its rows (text files up to
ROWS_MAX bytes) and the day it last changed. Differences between two scans become `catalog_events`: new_source, schema_change,
new_range (a newer version of the file), volume_jump, gone. The first scan is the baseline: no events. A scan that suddenly
sees many files has found them, not received them: those are 'first_seen', which never counts as data arriving.
Other kinds of source (documents the org watch finds, branch tips) are written by their own modules.
Nothing is opened beyond its first lines, nothing is changed, and nothing leaves this computer.
"""
import csv, datetime as dt, io, json, os
from jarvis import config
from jarvis.spine import ledger

EXT = ('.csv', '.tsv', '.json', '.jsonl', '.xlsx', '.xls', '.parquet')
SKIP_DIRS = {'node_modules', '__pycache__', 'venv', 'env', 'site-packages', 'dist', 'build', 'out', 'target'}
MAX_FILES, MAX_DEPTH, ROWS_MAX, HEAD = 5000, 6, 64 * 1024 * 1024, 64 * 1024


def ours(source_id):
    """Is this source the owner's own work? Everything in the folders they chose is."""
    return True


def folders():
    """The folders to look in: the ones chosen for data, then the work folders. Only ones that exist."""
    c = config.load()
    out = []
    for f in (c.get('sources.folders') or []) + (c.get('work.folders') or []):
        d = os.path.abspath(os.path.expanduser(str(f)))
        if os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _fp(s):
    return ledger.sha({'c': s.get('columns') or [], 'n': s.get('newest'), 'f': s.get('files'), 'r': s.get('rows')})


def _columns(path, ext):
    """The column names, read from the first lines only. [] when the kind of file does not show them cheaply."""
    try:
        with open(path, 'rb') as f:
            head = f.read(HEAD).decode('utf-8', 'replace')
    except OSError:
        return []
    first = head.split('\n', 1)[0].lstrip('﻿').strip()
    if not first:
        return []
    try:
        if ext in ('.csv', '.tsv'):
            delim = '\t' if ext == '.tsv' or first.count('\t') > first.count(',') else ';' if first.count(';') > first.count(',') else ','
            return [c.strip()[:80] for c in next(csv.reader(io.StringIO(first), delimiter=delim))][:200]
        if ext == '.jsonl':
            d = json.loads(first)
            return [str(k)[:80] for k in d][:200] if isinstance(d, dict) else []
    except (ValueError, StopIteration, csv.Error):
        pass
    return []


def _rows(path, ext, size):
    """Rows of a text table (lines minus the header), or None when it is too big or not a line-per-row file."""
    if ext not in ('.csv', '.tsv', '.jsonl') or size > ROWS_MAX:
        return None
    n, last = 0, b'\n'
    try:
        with open(path, 'rb') as f:
            while True:
                b = f.read(1024 * 1024)
                if not b:
                    break
                n += b.count(b'\n'); last = b[-1:]
    except OSError:
        return None
    n += last != b'\n'
    return max(0, n - (ext != '.jsonl'))


def _files():
    """Every data file under the chosen folders, as source rows."""
    out, home = [], os.path.expanduser('~')
    for root in folders():
        base = root.rstrip(os.sep).count(os.sep)
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith('.') and x not in SKIP_DIRS) if d.count(os.sep) - base < MAX_DEPTH else []
            for name in sorted(files):
                ext = os.path.splitext(name)[1].lower()
                if ext not in EXT or name.startswith(('.', '~$')):
                    continue
                p = os.path.join(d, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                shown = '~' + p[len(home):] if p.startswith(home + os.sep) else p
                out.append({'id': 'local:' + shown, 'system': 'local', 'kind': ext[1:], 'name': os.path.splitext(name)[0], 'path': shown,
                            'columns': _columns(p, ext), 'rows': _rows(p, ext, st.st_size), 'bytes': st.st_size, 'files': 1,
                            'newest': dt.date.fromtimestamp(st.st_mtime).isoformat(), 'local': 1})
                if len(out) >= MAX_FILES:
                    return out, True
    return out, False


def scan(kind='local'):
    """Returns {'sources': n, 'events': [(source_id, kind)], 'baseline', 'notes'}. Only 'local' exists here."""
    res = {'kind': kind, 'sources': 0, 'events': [], 'notes': []}
    if kind != 'local':
        res['error'] = f'no such kind of scan: {kind}'
        return res
    if not folders():
        res['notes'].append('no folder chosen yet')
        return res
    rows, cut = _files()
    if cut:
        res['notes'].append(f'stopped at {MAX_FILES} files')
    baseline = not ledger.q("SELECT 1 FROM sources WHERE system='local' LIMIT 1")
    old = {r['id']: r for r in ledger.q("SELECT * FROM sources WHERE system='local'")}
    now = ledger.now()
    for s in rows:
        fp, prev, ev = _fp(s), old.get(s['id']), None
        if prev is None:
            ev = ('new_source', {'name': s['name'], 'system': s['system'], 'rows': s.get('rows'), 'columns': len(s.get('columns') or [])})
        elif prev.get('fingerprint') != fp:
            pc, nc = set(json.loads(prev['columns'] or '[]')), set(s.get('columns') or [])
            pr, nr = prev.get('rows') or 0, s.get('rows') or 0
            if nc and pc and nc != pc:
                ev = ('schema_change', {'name': s['name'], 'added': sorted(nc - pc)[:30], 'removed': sorted(pc - nc)[:30]})
            elif pr and nr and abs(nr - pr) / max(pr, 1) > 0.5:
                ev = ('volume_jump', {'name': s['name'], 'from': pr, 'to': nr})
            elif (s.get('newest') or '') > (prev.get('newest') or ''):
                ev = ('new_range', {'name': s['name'], 'newest': s.get('newest'), 'was': prev.get('newest')})
        ledger.upsert('sources', {'id': s['id'], 'system': s['system'], 'kind': s.get('kind'), 'name': s['name'], 'path': s.get('path'),
                                  'columns': json.dumps(s.get('columns') or []), 'rows': s.get('rows'), 'bytes': s.get('bytes'),
                                  'files': s.get('files'), 'newest': s.get('newest'), 'first_seen': prev['first_seen'] if prev else now,
                                  'last_seen': now, 'last_changed': now if (ev or not prev) else prev.get('last_changed'),
                                  'fingerprint': fp, 'local': 1, 'meta': '{}'}, ('id',))
        if ev and not baseline:
            ledger.x('INSERT INTO catalog_events(at, source_id, kind, detail) VALUES(?,?,?,?)', (now, s['id'], ev[0], json.dumps(ev[1], default=str)))
            res['events'].append((s['id'], ev[0]))
    news = [e for e in res['events'] if e[1] == 'new_source']
    if len(news) > 10:                               # found, not received (a folder just added to the list)
        ledger.x("UPDATE catalog_events SET kind='first_seen' WHERE kind='new_source' AND at=?", (now,))
        res['events'] = [(sid, 'first_seen' if k == 'new_source' else k) for sid, k in res['events']]
    if not cut and not baseline:
        seen = {s['id'] for s in rows}
        for sid in set(old) - seen:
            ledger.x('INSERT INTO catalog_events(at, source_id, kind, detail) VALUES(?,?,?,?)', (now, sid, 'gone', '{}'))
            ledger.x('DELETE FROM sources WHERE id=?', (sid,))
            res['events'].append((sid, 'gone'))
    res['sources'], res['baseline'] = len(rows), baseline
    ledger.event('catalog_scan', kind, 'catalog', {'sources': res['sources'], 'events': len(res['events'])})
    return res


def search(words, limit=12):
    """Sources whose name, path or columns contain any of the words (case-insensitive), best first."""
    words = [w.lower() for w in words if w and len(w) >= 3]
    if not words:
        return []
    out = []
    for s in ledger.q('SELECT id, system, name, path, columns, rows, newest, local FROM sources'):
        hay_name, cols = (s['name'] or '').lower(), (s['columns'] or '').lower()
        score = sum(3 for w in words if w in hay_name) + sum(1 for w in words if w in cols)
        if score:
            out.append((score, s))
    out.sort(key=lambda x: (-x[0], x[1]['id']))
    return [dict(s, score=sc) for sc, s in out[:limit]]
