#!/usr/bin/env python3
"""Properties for every knowledge-base note, kept in ONE index (props.json in the knowledge base) instead of rewriting
every note file: no note body changes, no parser that reads a note's first line breaks, no versions pile up. The vault
(world/vault_layout.render) writes them into the mirrored copies' front matter; Jarvis's lookup tools and the entity pages
(kb_entities.py) read the index.

Per note: id, type, title, date, source, status, workstreams, the entities it names (tickets, pull requests, people, and
the owner's own kinds: jarvis/entities.py), and the data cut-offs it states. All in code; no model use.

    kb_props.py build        props.json and entity_index.json (in the knowledge base)
    kb_props.py show <id>    one note's properties
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import entities, paths
import collections, datetime as dt, glob, json, os, re, sys

HUB = str(paths.home())
B = os.path.join(HUB, 'brain')
KBD = str(paths.kb())
PROPS = os.path.join(KBD, 'props.json')
ENT = os.path.join(KBD, 'entity_index.json')
MEMORY = str(paths.claude_memory())

MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
CUT = re.compile(r'(?:data (?:to|through|until|up to)|cut[- ]?off(?: date)?(?: of| is| at|:)?|as of|freeze(?:d)? (?:on|at|of)?|'
                 r'snapshot (?:of|on)|history to)\s+(\d{1,2}\s+[A-Z][a-z]{2,8}(?:\s+20\d\d)?|20\d\d-\d\d-\d\d|[A-Z][a-z]{2,8}\s+\d{1,2}(?:,?\s+20\d\d)?)')


def _read(p):
    try:
        return open(p, errors='replace').read()
    except OSError:
        return ''


def _date(s, year):
    s = s.strip().replace(',', '')
    m = re.match(r'(20\d\d)-(\d\d)-(\d\d)$', s)
    if m:
        return s
    m = re.match(r'(\d{1,2})\s+([A-Za-z]{3})[a-z]*(?:\s+(20\d\d))?$', s) or None
    if m:
        d, mon, y = int(m.group(1)), MONTHS.get(m.group(2).lower()), int(m.group(3) or year)
    else:
        m = re.match(r'([A-Za-z]{3})[a-z]*\s+(\d{1,2})(?:\s+(20\d\d))?$', s)
        if not m:
            return None
        mon, d, y = MONTHS.get(m.group(1).lower()), int(m.group(2)), int(m.group(3) or year)
    try:
        return dt.date(y, mon, d).isoformat() if mon else None
    except (ValueError, TypeError):
        return None


def _type(k, v):
    kind = v['kind']
    if kind in ('project', 'workstream', 'person', 'owner'):
        return 'sub-note' if kind == 'workstream' and '--' in k else kind
    if kind == 'chat':
        return ('jarvis-conversations' if 'jarvis-conversations' in k else 'jarvis-work' if 'jarvis-work' in k else
                'sub-agent-runs' if k.endswith('-subs') else 'chat')
    for pre, t in (('l_jira', 'jira'), ('l_conf', 'confluence'), ('l_slack_dm', 'slack-dm'), ('l_slack', 'slack'), ('l_mail', 'mail'),
                   ('l_teams', 'teams'), ('l_meet', 'meeting'), ('l_sp_catalog', 'sharepoint-catalogue'), ('l_sp', 'sharepoint'),
                   ('l_repo', 'repository'), ('l_pr', 'pull-requests'), ('l_', 'live'), ('e_', 'notebook'), ('f_catalog', 'data-catalogue'),
                   ('f_', 'raw-output'), ('g_', 'command-output'), ('s_', 'spreadsheet'), ('j_', 'jarvis-record'), ('a_', 'chat-extra'),
                   ('c_', 'screenshot')):
        if k.startswith(pre):
            return t
    return kind.replace(' ', '-')


STATUS = {'project': 'summary', 'workstream': 'current', 'sub-note': 'current', 'person': 'profile', 'owner': 'profile',
          'chat': 'as-said', 'jarvis-conversations': 'as-said', 'jarvis-work': 'as-said', 'sub-agent-runs': 'as-said',
          'chat-extra': 'as-said', 'screenshot': 'as-said', 'jarvis-record': 'as-said'}


def _people():
    """[(regex, slug)] from the people notes: the full name and a first name that is not ambiguous."""
    out, firsts = [], collections.Counter()
    ppl = []
    for f in glob.glob(os.path.join(KBD, 'people', '*.md')):
        t = _read(f)
        m = re.search(r'^person:\s*(.+)$', t, re.M)
        name = (m.group(1) if m else os.path.basename(f)[:-3].replace('-', ' ').title()).strip()
        ppl.append((name, os.path.basename(f)[:-3])); firsts[name.split()[0]] += 1
    for name, slug in ppl:
        pats = [re.escape(name)]
        first = name.split()[0]
        if firsts[first] == 1 and len(first) >= 4:
            pats.append(r'\b' + re.escape(first) + r'\b')
        out.append((re.compile(r'(?:' + '|'.join(pats) + r')'), slug))
    return out


def extract(text, year, people, names=None, lists=None):
    """What a text names: its entities by kind and the data cut-offs it states. `names` (repository names) and `lists`
    (known values per kind) are passed by a caller that reads many texts."""
    e = entities.find(text, names, lists)
    e['people'] = sorted({slug for rx, slug in people if rx.search(text)})
    this = dt.date.today().year
    cuts = sorted({d for s in CUT.findall(text) for d in [_date(s, year)] if d and f'{this - 8}-01-01' <= d <= f'{this + 1}-12-31'})
    e['data_cutoffs'] = cuts[-12:]
    return e


def _shared():
    """What every extract of one build shares: the people patterns, the repository names, the known values per kind."""
    return _people(), entities.repos(), {k: entities.known(k) for k in entities.custom()}


def build():
    from jarvis.world import vault_layout as V
    L = V.layout()
    mem = V._members(L)
    ws_of = collections.defaultdict(list)
    for s, ids in mem.items():
        for i in ids:
            ws_of[i].append(s)
    people, names, lists = _shared()
    pairs = entities.pairs()
    this = dt.date.today().year
    props, ent = {}, collections.defaultdict(list)
    for k, v in L.items():
        text = _read(v['src'])
        year = int(v['date'][:4]) if re.match(r'20\d\d', v.get('date') or '') else this
        e = extract(text, year, people, names, lists)
        typ = _type(k, v)
        status = STATUS.get(typ, 'record' if typ in ('jira', 'confluence', 'slack', 'slack-dm', 'mail', 'teams', 'meeting', 'sharepoint',
                                                      'sharepoint-catalogue', 'repository', 'pull-requests', 'live') else 'document')
        if typ == 'sub-note' and k.endswith('--history'):
            status = 'history'
        props[k] = {'id': k, 'type': typ, 'title': v['title'], 'date': v.get('date') or '', 'source': v.get('source') or '',
                    'status': status,
                    'workstreams': sorted(set(ws_of.get(k, []))) or ([k.split('--')[0]] if typ in ('workstream', 'sub-note') else []), **e}
        for kind, key in pairs:
            for x in e.get(key) or []:
                ent[f'{kind}:{x}'].append(k)
    for f in glob.glob(os.path.join(MEMORY, '*.md')):                 # memory notes join the same index (entities only)
        if os.path.basename(f) == 'MEMORY.md':
            continue
        t = _read(f)
        e = extract(t, this, people, names, lists)
        k = 'memory:' + os.path.basename(f)[:-3]
        props[k] = {'id': k, 'type': 'memory', 'title': (re.search(r'^description:\s*(.+)$', t, re.M) or [None, k])[1][:200],
                    'date': dt.date.fromtimestamp(os.path.getmtime(f)).isoformat(), 'source': f.replace(os.path.expanduser('~'), '~'),
                    'status': 'superseded' if re.search(r'\bSUPERSEDED\b', t[:600]) else 'memory', 'workstreams': [], **e}
        for kind, key in pairs:
            for x in e.get(key) or []:
                ent[f'{kind}:{x}'].append(k)
    os.makedirs(KBD, exist_ok=True)
    tmp = PROPS + '.tmp'; json.dump(props, open(tmp, 'w'), indent=0); os.replace(tmp, PROPS)
    tmp = ENT + '.tmp'; json.dump(dict(sorted(ent.items())), open(tmp, 'w'), indent=0); os.replace(tmp, ENT)
    c = collections.Counter(p['type'] for p in props.values())
    kinds = collections.Counter(x.split(':', 1)[0] for x in ent)
    print(f'notes {len(props)} ({dict(c.most_common())}); entities {len(ent)} ({dict(kinds)}); '
          f'with a data cut-off {sum(1 for p in props.values() if p["data_cutoffs"])}; with workstreams {sum(1 for p in props.values() if p["workstreams"])}')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['build']:
        build()
    elif a[:1] == ['show'] and len(a) > 1:
        print(json.dumps(json.load(open(PROPS)).get(a[1]), indent=1))
    else:
        print(__doc__)
