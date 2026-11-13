"""Every note of the knowledge base, listed once (layout), and how the vault shows them (render).

The knowledge base keeps its ids (the knowledge base folder, brain/sessions, brain/sources); only the mirrored copies change:
  - readable names: a chat note is "2026-08-10 <its title>", a source note "<its title>"; the old id is kept as an alias;
  - one title: the file name is the title, so the note's own first heading is left out;
  - folders by kind and month under the knowledge base's folder in the vault (settings: vault.kb_folder): Chats/2026-08/,
    Documents/2026-09/, Reports/, Decks/, Screenshots/, Chat extras/, Jarvis records/, Archive/; Workstreams/ for the
    workstream notes; the notes about the whole of the work at the top;
  - properties: kind, date, workstreams (links), source, so Obsidian can filter;
  - workstream hubs: each workstream note ends with its chats and sources by month, newest first;
  - the home page, named like the folder: every workstream with its state and last update, the notes about the whole of
    the work, this week's chats;
  - every [[link]] in every copy is rewritten to the new names (relink), so backlinks keep working.
The layout is also the note index the properties (tools/kb_props.py) are built from, whether or not a vault is switched on.
No model use. Personal screening stays where it was (vault._pfilter on every text).
"""
from jarvis import config, entities, paths
import datetime as dt, glob, json, os, re

HUB = str(paths.home())
B = os.path.join(HUB, 'brain')
MAXLEN = 80


def kbf():
    """The knowledge base's folder in the vault (settings: vault.kb_folder); its home page has the same name."""
    return re.sub(r'[\\/:*?"<>|#^\[\]]+', ' ', str(config.load().get('vault.kb_folder') or '')).strip() or 'Knowledge'


def _read(p):
    try:
        return open(p, encoding='utf-8', errors='replace').read()
    except OSError:
        return ''


def _front(t):
    """(frontmatter dict of simple 'key: value' lines, body) - every leading frontmatter block is taken off (17 workstream
    notes had theirs 2-4 times until 23 Oct)."""
    meta = {}
    while t.startswith('---\n') and t.count('---\n') >= 2:
        head, t = t[4:].split('---\n', 1)
        for l in head.splitlines():
            if ':' in l:
                k, v = l.split(':', 1)
                meta.setdefault(k.strip(), v.strip())
        t = t.lstrip('\n')
    return meta, t


def _title_of(body):
    m = re.search(r'^# (.+)$', body, re.M)
    return m.group(1).strip() if m else ''


def _drop_title(body):
    """The body without its first top heading (the file name is the title)."""
    return re.sub(r'\A\s*# .+\n+', '', body, count=1)


def safe(name):
    """A file name Obsidian takes: no / \\ : * ? " < > | # ^ [ ], no leading dot, at most MAXLEN characters."""
    n = re.sub(r'[\\/:*?"<>|#^\[\]]+', ' ', name)
    n = re.sub(r'`', '', n)
    n = re.sub(r'\s+', ' ', n).strip(' .-')
    if len(n) > MAXLEN:
        n = n[:MAXLEN].rsplit(' ', 1)[0].rstrip(' ,;-')
    return n or 'untitled'


def _unit_header(sid):
    """The first line of the raw unit a source note was read from: '<kind> <path> | last modified <date>'."""
    for f in glob.glob(os.path.join(B, 'sources_extra', sid + '.*')):
        return _read(f).split('\n', 1)[0]
    return ''


def _source_kind(sid, header):
    low = (sid + ' ' + header).lower()
    if sid.startswith('c_'):
        return 'Screenshots', 'screenshots'
    if sid.startswith('a_'):
        return 'Chat extras', 'chat extra'
    if sid.startswith('j_'):
        return 'Jarvis records', 'jarvis record'
    if sid.startswith(('0_', 'b_')) or '/desktop/archive/' in low:
        return 'Archive', 'archive'
    if sid.endswith('-pptx') or 'deck-render' in low or '/slides' in low or 'slides-' in sid:
        return 'Decks', 'deck'
    if sid.endswith('-html'):
        return 'Reports', 'report'
    return 'Documents', 'document'


def layout():
    """{note id: info} for every knowledge-base note the vault shows. info: path (vault-relative), name (file name without .md),
    title, kind, date, src (knowledge-base file), source (where it came from), stable (the old vault path, which keys its labels)."""
    out = {}
    for f in sorted(glob.glob(os.path.join(str(paths.kb()), '*.md'))):
        n = os.path.basename(f)[:-3]
        out[n] = {'path': f'{kbf()}/{n}.md', 'name': n, 'title': n, 'kind': 'project', 'date': '', 'src': f, 'source': '',
                  'stable': f'{kbf()}/{n}.md'}
    for f in sorted(glob.glob(os.path.join(str(paths.kb()), 'topics', '*.md'))):
        slug = os.path.basename(f)[:-3]
        meta, body = _front(_read(f))
        title = meta.get('title') or _title_of(body) or slug
        out[slug] = {'path': f'{kbf()}/Workstreams/{safe(title)}.md', 'name': safe(title), 'title': title, 'kind': 'workstream',
                     'date': meta.get('updated', ''), 'src': f, 'source': '', 'stable': f'{kbf()}/Topics/{slug}.md'}
    for f in sorted(glob.glob(os.path.join(str(paths.kb()), 'people', '*.md'))):   # Step 6b
        n = os.path.basename(f)[:-3]
        meta, body = _front(_read(f))
        title = meta.get('person') or _title_of(body) or n
        out['person:' + n] = {'path': f'{kbf()}/People/{safe(title)}.md', 'name': safe(title), 'title': title, 'kind': 'person',
                              'date': meta.get('updated', ''), 'src': f, 'source': '', 'stable': f'{kbf()}/People/{n}.md'}
    for f in sorted(glob.glob(os.path.join(str(paths.kb()), 'owner', '*.md'))):    # 24 Oct: Jarvis as the owner (R1-R3)
        n = os.path.basename(f)[:-3]
        meta, body = _front(_read(f))
        title = meta.get('title') or _title_of(body) or n
        out['owner:' + n] = {'path': f'{kbf()}/Owner/{safe(title)}.md', 'name': safe(title), 'title': title, 'kind': 'owner',
                             'date': meta.get('updated', ''), 'src': f, 'source': '', 'stable': f'{kbf()}/Owner/{n}.md'}
    for f in sorted(glob.glob(os.path.join(B, 'sessions', '*.md'))):
        sid = os.path.basename(f)[:-3]
        if sid.endswith('.cut'):
            continue
        t = _read(f)
        _, body = _front(t)
        title = _title_of(body) or sid
        date = sid[:10] if re.match(r'20\d\d-\d\d-\d\d', sid) else dt.date.fromtimestamp(os.path.getmtime(f)).isoformat()
        out[sid] = {'path': '', 'name': safe(f'{date} {title}'), 'title': title, 'kind': 'chat', 'date': date, 'src': f,
                    'source': 'chat ' + re.sub(r'-20\d{6}$', '', sid)[11:], 'stable': f'{kbf()}/Sessions/{sid}.md', 'folder': 'Chats'}
    for f in sorted(glob.glob(os.path.join(B, 'sources', '*.md'))):
        sid = os.path.basename(f)[:-3]
        if sid.endswith('.cut'):
            continue
        t = _read(f)
        _, body = _front(t)
        h = _unit_header(sid)
        folder, kind = _source_kind(sid, h)
        m = re.search(r'last modified (20\d\d-\d\d-\d\d)', h) or re.search(r'(20\d\d-\d\d-\d\d)', sid)
        date = m.group(1) if m else dt.date.fromtimestamp(os.path.getmtime(f)).isoformat()
        title = _title_of(body) or sid
        if folder == 'Documents' and title.lower().startswith('deck'):
            folder, kind = 'Decks', 'deck'                 # a deck saved as PDF
        src = re.sub(r'\s*\|\s*last modified.*$', '', h.split(' ', 1)[1]) if ' ' in h else ''
        name = safe(f'{date} {title}') if kind in ('screenshots', 'chat extra', 'jarvis record') else safe(title)
        out[sid] = {'path': '', 'name': name, 'title': title, 'kind': kind, 'date': date, 'src': f, 'source': src,
                    'stable': f'{kbf()}/Sources/{sid}.md', 'folder': folder}
    for f in sorted(glob.glob(os.path.join(str(paths.kb()), 'entities', '*', '*.md'))):   # 6 Nov (structure plan S2): entity pages
        meta, body = _front(_read(f))
        eid = meta.get('entity') or ''
        if not eid:
            continue
        title = json.loads(meta['title']) if str(meta.get('title', '')).startswith('"') else (meta.get('title') or eid)
        kind = eid.split(':', 1)[0]
        folder = entities.folder(kind)
        if kind == 'person' and (out.get(eid) or {}).get('kind') == 'person':
            eid = 'mentions:' + eid.split(':', 1)[1]      # 12 Nov: the mention page had the same key as the person's own note
            #                                               ("how they want things done", Step 6b) and replaced it: 16 notes never reached the vault
        out[eid] = {'path': f'{kbf()}/Entities/{folder}/{safe(title)}.md', 'name': safe(title), 'title': title, 'kind': 'entity',
                    'date': meta.get('updated', ''), 'src': f, 'source': '', 'stable': f'{kbf()}/Entities/{folder}/{safe(title)}.md'}
    for f in sorted(glob.glob(os.path.join(str(paths.kb()), 'ledger', '*.md'))):          # 6 Nov (S3): ledger pages
        meta, body = _front(_read(f))
        n = os.path.basename(f)[:-3]
        title = json.loads(meta['title']) if str(meta.get('title', '')).startswith('"') else (meta.get('title') or n)
        if n in ('contradictions', 'number_check'):
            title = {'contradictions': 'Ledger: contradictions to repair', 'number_check': 'Ledger: number check'}[n]
        out['ledger:' + n] = {'path': f'{kbf()}/Ledger/{safe(title)}.md', 'name': safe(title), 'title': title, 'kind': 'ledger',
                              'date': meta.get('updated', ''), 'src': f, 'source': '', 'stable': f'{kbf()}/Ledger/{safe(title)}.md'}
    names = {}                                            # one file name per note, so a link has one target
    for k, v in out.items():
        names.setdefault(v['name'].lower(), []).append(k)
    for same in names.values():
        if len(same) > 1:                                # told apart by the part of their ids that differs ("flows-1", "flows-2")
            toks = {k: re.split(r'[-_]', re.sub(r'^[a-z0-9]_', '', k)) for k in same}
            common = set.intersection(*(set(t) for t in toks.values()))
            for k in same:
                if out[k]['kind'] not in ('project', 'workstream'):
                    tail = ' '.join(t for t in toks[k] if t not in common)[:20] or k[-8:]
                    out[k]['name'] = safe(f"{out[k]['name'][:MAXLEN - len(tail) - 3]} ({tail})")
    for k, v in out.items():
        if not v['path']:
            v['path'] = f"{kbf()}/{v['folder']}/{v['date'][:7] or 'undated'}/{v['name']}.md"
    return out


def _members(L):
    """slug -> note ids in that workstream: the notes it cites (topic_map sources) and the filing of newer notes."""
    try:
        topics = json.load(open(os.path.join(str(paths.kb()), 'topic_map.json')))
    except (OSError, ValueError):
        topics = []
    try:
        filing = json.load(open(os.path.join(HUB, 'state', 'topics_filing.json')))
    except (OSError, ValueError):
        filing = {}
    by_src = {}
    for k, v in L.items():
        if v.get('source'):
            by_src.setdefault(v['source'].replace(os.path.expanduser('~'), '~'), k)
    mem = {}
    for t in topics:
        ids = []
        for s in t.get('sources') or []:
            p, _, x = s.partition(':')
            if p in ('S', 'X') and x in L:
                ids.append(x)
            elif p == 'D':                               # a document by its path under the work folder
                k = by_src.get(str(paths.workdir()).replace(os.path.expanduser('~'), '~') + '/' + x)
                if k:
                    ids.append(k)
        mem[t['slug']] = ids
    for i, slugs in filing.items():
        for s in slugs:
            if i in L:
                mem.setdefault(s, []).append(i)
    return {s: list(dict.fromkeys(v)) for s, v in mem.items()}


_PROPS = {}


def _props():
    """props.json (in the knowledge base) (tools/kb_props.py), read once per render; {} when it does not exist yet."""
    p = os.path.join(str(paths.kb()), 'props.json')
    try:
        m = os.path.getmtime(p)
        if _PROPS.get('m') != m:
            _PROPS.update(m=m, d=json.load(open(p)))
        return _PROPS.get('d') or {}
    except (OSError, ValueError):
        return {}


def render(L, text_of):
    """[(vault path, text, stable path)] for every note, with properties, hubs, the home page and a list of the unfiled.
    text_of(src file) -> the screened text, or None to hold the note back (vault._pfilter)."""
    mem = _members(L)
    ws_of = {}
    for s, ids in mem.items():
        for i in ids:
            ws_of.setdefault(i, []).append(s)
    out = []
    for k, v in L.items():
        t = text_of(v['src'])
        if t is None:
            continue
        meta, body = _front(t)
        props = [f'aliases: [{json.dumps(k)}]', f"kind: {v['kind']}"]
        if v['date']:
            props.append(f"date: {v['date']}")
        if v['kind'] == 'workstream' and meta.get('sources'):
            props.append(f"sources: {meta['sources']}")
        ws = [L[s]['name'] for s in ws_of.get(k, []) if s in L]
        if ws:
            props.append('workstreams: [' + ', '.join(json.dumps(f'[[{w}]]') for w in ws) + ']')
        if v['source']:
            props.append(f"source: {json.dumps(v['source'])}")
        pp = _props().get(k)                              # 6 Nov (structure plan S1): the full property set from props.json (in the knowledge base)
        if pp:
            props.append(f"type: {pp['type']}")
            props.append(f"status: {pp['status']}")
            for key, ek in [(key, kind) for kind, key in entities.pairs() if kind != 'person'] + [('data_cutoffs', '')]:
                if pp.get(key):                           # entities link to their pages (S2); cut-offs stay plain dates
                    vals = [f'[[{L[ek + ":" + x]["name"]}]]' if ek and (ek + ':' + x) in L else x for x in pp[key][:30]]
                    props.append(f'{key}: ' + json.dumps(vals))
            if pp.get('people'):
                pn = {kk[7:]: vv['name'] for kk, vv in L.items() if kk.startswith('person:')}
                props.append('people: [' + ', '.join(json.dumps(f'[[{pn[x]}]]' if x in pn else x) for x in pp['people'][:20]) + ']')
        if v['kind'] == 'entity':                         # 12 Nov: the typed properties the entity page carries (kb_entities._typed)
            props += [f'{key}: {meta[key]}' for key in meta if key not in ENTITY_SKIP and meta.get(key) not in (None, '')]
        body = _drop_title(body) if v['kind'] not in ('project',) else body
        if v['kind'] == 'workstream':
            body = body.rstrip() + '\n\n' + _hub(L, mem.get(k, [])) + '\n'
        out.append((v['path'], '---\n' + '\n'.join(props) + '\n---\n' + body.lstrip('\n'), v['stable']))
    home = f'{kbf()}/{kbf()}.md'
    out.append((home, _home(L, mem), home))
    filed = {i for ids in mem.values() for i in ids}
    rest = [k for k, v in L.items() if k not in filed and v['kind'] not in ('project', 'workstream', 'person', 'owner', 'entity', 'ledger')]
    unfiled = f'{kbf()}/Not yet in a workstream.md'
    out.append((unfiled, _listing('Not yet in a workstream',
                'Notes not yet filed under a workstream; the next fold files them.', L, rest), unfiled))
    return out


def _listing(title, intro, L, ids):
    by = {}
    for i in ids:
        by.setdefault(L[i]['date'][:7] or 'undated', []).append(i)
    lines = [intro, '']
    for mon in sorted(by, reverse=True):
        lines.append(f'### {mon}')
        for i in sorted(by[mon], key=lambda i: L[i]['date'], reverse=True):
            lines.append(f"- [[{L[i]['name']}]] · {L[i]['kind']}")
        lines.append('')
    return '\n'.join(lines)


def _hub(L, ids):
    if not ids:
        return '## Notes in this workstream\n\nNone filed yet.'
    return '## Notes in this workstream\n\n' + _listing('', f'{len(ids)} notes, newest first.', L, [i for i in ids if i in L])


def _gist(src):
    """The first sentence of a workstream note's opening paragraph."""
    _, body = _front(_read(src))
    m = re.search(r'## In one paragraph\s*\n+(.+?)(?:\n\n|$)', body, re.S) or re.search(r'^(?!#)(\S.+?)(?:\n\n|$)', _drop_title(body), re.S | re.M)
    s = re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''
    return (re.split(r'(?<=[.!?])\s', s, 1)[0])[:220]


ENTITY_SKIP = ('entity', 'kind', 'title', 'updated', 'aliases', 'date')   # an entity page's other properties go to the copy as they are
PARTS = {'current-position': 'where it stands', 'decisions-methods': 'decisions and methods',
         'data-deliverables-people': 'data, deliverables, people', 'data-code': 'data and code', 'history': 'history'}


def _home(L, mem):
    """12 Nov (autonomy plan, part I): one row per workstream, its split sub-notes as links in the row. The table used to
    list every sub-note as its own row: 78 rows, 58 of them "Part of ...", so the 20 workstreams were hard to find."""
    ws = {k: v for k, v in L.items() if v['kind'] == 'workstream'}
    sub = {}
    for k, v in ws.items():
        if '--' in k and k.split('--', 1)[0] in ws:
            sub.setdefault(k.split('--', 1)[0], []).append((k.split('--', 1)[1], k, v))
    order = list(PARTS)
    rows = []
    for k, v in ws.items():
        if '--' in k and k.split('--', 1)[0] in ws:
            continue                                     # shown in its parent's row
        parts = sorted(sub.get(k, []), key=lambda t: (order.index(t[0]) if t[0] in order else 99, t[0]))
        date = max([v['date']] + [x['date'] for _, _, x in parts])
        n = len(mem.get(k, [])) + sum(len(mem.get(sk, [])) for _, sk, _ in parts)
        links = ' · '.join(f"[[{x['name']}\\|{PARTS.get(p, p.replace('-', ' '))}]]" for p, _, x in parts)
        rows.append((date, v['name'], f"| [[{v['name']}]] | {date} | {n} | {_gist(v['src']).replace('|', '/')} | {links or '-'} |"))
    lines = ['---', 'kind: home', '---', 'The knowledge base: every Claude Code chat, Jarvis task, document and decision,',
             'read, screened and filed. Rebuilt from the knowledge base on every sync; edit the notes in the knowledge base, not here.', '',
             '## Workstreams', '', f'{len(rows)} workstreams, most recently changed first.', '',
             '| Workstream | Updated | Notes | What it is | Its parts |', '|---|---|---|---|---|']
    lines += [r for _, _, r in sorted(rows, key=lambda t: (t[0], t[1]), reverse=True)]
    lines += ['', '## Project notes', '', ' · '.join(f"[[{v['name']}]]" for v in L.values() if v['kind'] == 'project'), '']
    ppl = [v for v in L.values() if v['kind'] == 'person']
    if ppl:
        lines += ['## People: how each one wants things done', '', ' · '.join(f"[[{v['name']}]]" for v in sorted(ppl, key=lambda v: v['name'])), '']
    me = [v for v in L.values() if v['kind'] == 'owner']
    if me:
        lines += ['## The owner at work: what they know, how they decide, how they work', '', ' · '.join(f"[[{v['name']}]]" for v in sorted(me, key=lambda v: v['name'])), '']
    week = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    recent = sorted((k for k, v in L.items() if v['kind'] == 'chat' and v['date'] >= week), key=lambda k: L[k]['date'], reverse=True)
    lines += ['## This week', ''] + [f"- [[{L[k]['name']}]]" for k in recent] + ['', '## Everything by kind', '',
              ' · '.join(sorted({f"{v['folder']}" for v in L.values() if v.get('folder')})) + ' (folders, by month)',
              '', 'Not filed yet: [[Not yet in a workstream]]']
    return '\n'.join(lines) + '\n'


LINK = re.compile(r'\[\[([^\]|#\n]+)(#[^\]|\n]*)?(\|[^\]\n]*)?\]\]')


def _target(raw):
    """(note name, the backslash to keep) of a link's first part. In a table the label's bar is escaped, [[Note\\|label]],
    so the name arrives with a trailing backslash (12 Nov: the home page's part links)."""
    t = raw.strip()
    return (t[:-1].strip(), '\\') if t.endswith('\\') else (t, '')


def relink(text, names):
    """Every [[old id]] (with an optional #heading and |label) pointed at the note's new name."""
    def sub(m):
        tgt, esc = _target(m.group(1))
        new = names.get(tgt) or names.get(tgt.split('/')[-1]) or names.get(tgt[:-3] if tgt.endswith('.md') else '')
        return f"[[{new}{m.group(2) or ''}{esc}{m.group(3) or ''}]]" if new else m.group(0)
    return LINK.sub(sub, text)


def unlink_missing(text, exists):
    """A copy's links to notes the vault does not hold (held back by a screen, or never there) become plain text, and an
    index line that only points at one ("- [[x]] · kind") is left out, so the mirror has no links that lead nowhere."""
    out = []
    for line in text.split('\n'):
        m = re.match(r'^- \[\[([^\]|#\n]+)\]\] · [a-z ]+$', line)
        if m and m.group(1).strip().lower() not in exists:
            continue
        out.append(LINK.sub(lambda m: m.group(0) if (_target(m.group(1))[0].split('/')[-1].lower().removesuffix('.md') in exists)
                            else (m.group(3) or '|' + m.group(1))[1:], line))
    return '\n'.join(out)
