#!/usr/bin/env python3
"""One page per entity (a ticket, a pull request, a person, or a kind the owner named in settings: jarvis/entities.py), built
in code from props.json and entity_index.json (tools/kb_props.py); no model use. Rebuilt whole each night.

Each page: what it is (a ticket or a pull request as last read from its live source, a person's note, or what the kind's
list of known values says), its facts (from the ledger), and every mention grouped by workstream, newest first, each
labelled with what kind of evidence it is (the settled view, a record as read, or what was said in a chat on that date).

    kb_entities.py build      -> entities/<kind>/<id>.md (in the knowledge base)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import entities, paths
import collections, datetime as dt, glob, json, os, re, shutil

HUB = str(paths.home())
B = os.path.join(HUB, 'brain')
KBD = str(paths.kb())
OUT = os.path.join(KBD, 'entities')
MAX_MENTIONS = 250
READS = {'current': 'settled view', 'history': 'history', 'summary': 'project summary', 'as-said': 'as said', 'record': 'record as read',
         'document': 'document', 'memory': 'memory (final results and corrections)', 'superseded': 'SUPERSEDED', 'profile': 'profile'}
RANK = {'current': 0, 'memory': 1, 'summary': 2, 'record': 3, 'document': 4, 'history': 5, 'as-said': 6, 'profile': 7, 'superseded': 8}
FIELD = re.compile(r'[a-z][a-z0-9_]{0,40}$')


def _read(p):
    try:
        return open(p, errors='replace').read()
    except OSError:
        return ''


def _src(k, p):
    if k.startswith('memory:'):
        return os.path.join(str(paths.claude_memory()), k[7:] + '.md')
    if k.startswith('person:'):
        return os.path.join(KBD, 'people', k[7:] + '.md')
    if k.startswith('owner:'):
        return os.path.join(KBD, 'owner', k[6:] + '.md')
    if p['type'] in ('workstream', 'sub-note'):
        return os.path.join(KBD, 'topics', k + '.md')
    if p['type'] == 'project':
        return os.path.join(KBD, k + '.md')
    if p['type'] in ('chat', 'jarvis-work', 'jarvis-conversations', 'sub-agent-runs'):
        return os.path.join(B, 'sessions', k + '.md')
    return os.path.join(B, 'sources', k + '.md')


def _sentence(text, needle):
    """The first line that names the entity, trimmed around it."""
    for line in text.split('\n'):
        i = line.find(needle)
        if i >= 0 and not line.startswith('---') and not re.match(r'[a-z_]+: ', line):   # not a properties line
            line = re.sub(r'\s+', ' ', line.strip(' -*#|'))
            j = line.find(needle)
            a, b = max(0, j - 140), j + len(needle) + 160
            return ('… ' if a else '') + line[a:b].strip() + (' …' if b < len(line) else '')
    return ''


def _tickets():
    """ticket -> (summary, meta line, read date) from the live tracker units, word for word."""
    out = {}
    for f in glob.glob(os.path.join(B, 'sources_extra', 'l_jira_*.txt')):
        t = _read(f)
        rd = (re.search(r'read (20\d\d-\d\d-\d\d)', t.split('\n', 1)[0]) or [None, ''])[1]
        for m in re.finditer(r'^## ([A-Z][A-Z0-9]{1,9}-\d+): (.+)\n(type .+)$', t, re.M):
            if m.group(1) not in out or rd >= out[m.group(1)][2]:   # the newest read wins
                out[m.group(1)] = (m.group(2).strip(), m.group(3).strip(), rd)
    return out


def _prs():
    out = {}
    for f in glob.glob(os.path.join(B, 'sources_extra', 'l_pr_*.txt')):
        t = _read(f)
        for m in re.finditer(r'^=== PR #(\d+) · (\w+) · (.+)\n(repo (\S+) .+)\n(reviewers: .+)\nlink: (\S+)', t, re.M):
            out[f'{m.group(5)}#{m.group(1)}'] = (m.group(3), m.group(2), m.group(4), m.group(6), m.group(7))
    return out


def _info(kind, x, lists):
    """(name, {field: value}) of a known value of one of the owner's own kinds; ('', {}) when its kind has no list."""
    v = (lists.get(kind) or {}).get(x)
    if isinstance(v, dict):
        return str(v.get('name') or ''), {k: v[k] for k in v if k != 'name' and FIELD.match(str(k)) and isinstance(v[k], (str, int, float, bool))}
    return (str(v) if v else ''), {}


def _what(kind, x, lists, tickets, prs, names):
    L = []
    if kind == 'ticket':
        t = tickets.get(x)
        if t:
            L += [f'**{x}: {t[0]}**', '', f'{t[1]}', '', f'*As read from the tracker on {t[2]}; the status may have changed since.*']
        else:
            L += [f'**{x}**: not among the tickets read from the tracker (created later, or another project); the mentions below say what it is.']
    elif kind == 'pr':
        p = prs.get(x)
        if p:
            L += [f'**Pull request {x}: {p[0]}** ({p[1]})', '', p[2], p[3], f'link: {p[4]}', '', '*As last read from the repository host.*']
        else:
            L += [f'**Pull request {x}** (not among the pull requests read).']
    elif kind == 'person':
        L += [f'**{names.get(x, x)}**: how they work is in [[person:{x}]].']
    else:
        name, fields = _info(kind, x, lists)
        L += [f'**{entities.title(kind, x)}**' + (f': {name}' if name else '')]
        L += [''] * bool(fields) + [f"- {k.replace('_', ' ')}: {v}" for k, v in sorted(fields.items())]
    return L


def _typed(kind, x, lists, tickets, prs):
    """Front-matter lines for database views in the vault: a ticket's status and owner, a pull request's state, and the
    fields the list of known values gives one of the owner's own kinds, so a view can group and show them."""
    q = lambda v: json.dumps(str(v), ensure_ascii=False)
    out = []
    if kind == 'ticket' and tickets.get(x):
        summ, meta, rd = tickets[x]
        out.append(f'summary: {q(summ[:160])}')
        f = dict(re.findall(r'(type|status|priority|assignee|updated) (.+?)(?= · |$)', meta))
        for k, name in (('status', 'ticket_status'), ('type', 'ticket_type'), ('priority', 'priority'), ('assignee', 'assignee')):
            if f.get(k):
                out.append(f'{name}: {q(f[k].strip())}')
        if re.fullmatch(r'20\d\d-\d\d-\d\d', (f.get('updated') or '').strip()):
            out.append(f"ticket_updated: {f['updated'].strip()}")
        if rd:
            out.append(f'ticket_read: {rd}')
    elif kind == 'pr' and prs.get(x):
        out += [f'pr_title: {q(prs[x][0][:160])}', f'pr_state: {q(prs[x][1])}', f"repo: {q(x.split('#')[0])}"]
    elif kind not in entities.BUILT_IN:
        name, fields = _info(kind, x, lists)
        if name:
            out.append(f'description: {q(name[:200])}')
        reserved = ('entity', 'kind', 'title', 'mentions', 'updated', 'description', 'aliases')
        out += [f'{k}: {v:g}' if isinstance(v, (int, float)) and not isinstance(v, bool) else f'{k}: {q(v)}'
                for k, v in sorted(fields.items()) if k not in reserved]
    return out


def build(facts_by_entity=None):
    P = json.load(open(os.path.join(KBD, 'props.json')))
    E = json.load(open(os.path.join(KBD, 'entity_index.json')))
    lists = {k: entities.known(k) for k in entities.custom()}
    if facts_by_entity is None:
        try:
            facts_by_entity = json.load(open(os.path.join(KBD, 'ledger', 'by_entity.json')))
        except (OSError, ValueError):
            facts_by_entity = {}
    tickets, prs = _tickets(), _prs()
    names = {k[7:]: re.sub(r'^(?:Person\s+)+', '', v['title']) for k, v in P.items() if k.startswith('person:')}   # 12 Nov: the entity page's own
    #                                    title came back in as the name, so every build added one more "Person " (and the mention search looked for "Person")
    tmp = OUT + '.new'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)                                      # also when nothing names an entity yet: an empty folder, not an error
    n = collections.Counter()
    texts = {}
    for eid, notes in E.items():
        kind, x = eid.split(':', 1)
        rows = []
        for k in notes:
            p = P.get(k)
            if not p:
                continue
            if k not in texts:
                texts[k] = _read(_src(k, p))
            needle = x.split('#')[0] if kind == 'pr' else x
            if kind == 'person':
                needle = names.get(x, x).split()[0]
            rows.append((p, _sentence(texts[k], needle)))
        if not rows:
            continue
        title = entities.title(kind, x, names)
        L = ['---', f'entity: {eid}', f'kind: {kind}', f'title: {json.dumps(title)}', f'mentions: {len(rows)}',
             f'updated: {dt.date.today()}'] + _typed(kind, x, lists, tickets, prs) + ['---', f'# {title}', '']
        L += _what(kind, x, lists, tickets, prs, names) + ['']
        fx = facts_by_entity.get(eid) or []
        L += ['## Facts (ledger)', '']
        if fx:
            cur = [f for f in fx if f.get('status') == 'current']
            old = [f for f in fx if f.get('status') != 'current']
            L += [f"- {f['statement']} [{f['id']}] ({f.get('data_cutoff') or 'no cut-off stated'}; [[{(f.get('sources') or ['?'])[0]}]])" for f in cur[:60]]
            if old:
                L += ['', '**Superseded:**'] + [f"- ~~{f['statement']}~~ [{f['id']}] → {', '.join(f.get('superseded_by') or [])}" for f in old[:40]]
        else:
            L += ['*No ledger records yet.*']
        L += ['', f'## Mentions ({len(rows)} notes)', '',
              '*Each line says what kind of evidence it is: the settled view (a workstream note), memory (final results and '
              'corrections), a record as read, a document, or what was said in a chat on that date (which may have been corrected later).*', '']
        by = collections.defaultdict(list)
        for p, s in rows:
            for w in (p['workstreams'] or ['(no workstream)'])[:1]:
                by[w].append((p, s))
        shown = 0
        for w in sorted(by, key=lambda w: (-len(by[w]), w)):
            L.append(f'### {w} ({len(by[w])})' if w.startswith('(') else f'### [[{w}]] ({len(by[w])})')
            ordered = sorted(sorted(by[w], key=lambda r: r[0]['date'] or '', reverse=True), key=lambda r: RANK.get(r[0]['status'], 9))
            for p, s in ordered[:max(5, MAX_MENTIONS // max(len(by), 1))]:
                L.append(f"- {p['date'] or 'undated'} · {READS.get(p['status'], p['status'])} · [[{p['id']}]]" + (f': {s}' if s else ''))
                shown += 1
            L.append('')
        if shown < len(rows):
            L.append(f'*{len(rows) - shown} more mentions: search for "{x}" (kb_search).*')
        d = os.path.join(tmp, kind)
        os.makedirs(d, exist_ok=True)
        fn = re.sub(r'[^A-Za-z0-9._-]+', '-', x)
        open(os.path.join(d, fn + '.md'), 'w').write('\n'.join(L) + '\n')
        n[kind] += 1
    old = OUT + '.old'
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(OUT):
        os.replace(OUT, old)
    os.replace(tmp, OUT)
    shutil.rmtree(old, ignore_errors=True)
    print(f'entity pages: {sum(n.values())} ({dict(n)})')


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['build']:
        build()
    else:
        print(__doc__)
