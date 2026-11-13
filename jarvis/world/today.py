"""The vault's working surface: a Today page and database views, so the vault is somewhere to work from and not only a
mirror to read.

pages(keep) returns the generated files the sync adds to the vault, built in code from Jarvis's own records (nothing here
calls a model, and every line comes from a work entry, the ledger or a state file that was already screened):

  Today.md                      what waits on them, what Jarvis did since yesterday, what is wrong right now, the scorecard
  Views/Jarvis work.base        every inbox item as a row: needs you, by status (kanban), newest first, replies
  Views/Tickets.base            the tickets the work touches, by status (kanban) and by assignee
  Views/Pull requests.base      the pull requests, by repository
  Views/Workstreams.base        the workstream notes by last change
  Views/Chats.base              the Claude Code chats by month
  Views/<Kind>s.base            one view for each kind of thing the owner named in settings (knowledge.entities)
  Views/Maps/<workstream>.canvas   a map of each workstream; Views/Maps/All work.canvas for the whole of the work

A .base file is Obsidian's database view (YAML: filters, properties, views). `![[Jarvis work.base#Needs you]]` embeds a
view in a note. Read-only: ticks and edits made in the vault do not flow back.
"""
from jarvis import config, entities, paths
from jarvis.world.vault_layout import kbf
import datetime as dt, json, os, re, sqlite3

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'jarvis.db')
WORK = re.compile(r'^Work/(\d{4}-\d\d-\d\d) .* \((\S+) (\S+)\)\.md$')


_READY = []


def _q(sql, args=()):
    if not _READY:                                       # the ledger's tables exist, also before Jarvis has done anything
        from jarvis.spine import ledger
        ledger.connect().close(); _READY.append(1)
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _j(p, d=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def _names(keep):
    """{(rid, aid): the Work note's name} from the notes this sync writes."""
    out = {}
    for rel in keep:
        m = WORK.match(rel)
        if m:
            out[(f'{m.group(1)}-{m.group(2)}', m.group(3))] = os.path.basename(rel)[:-3]
    return out


def _link(names, rid, aid, title):
    n = names.get((rid, aid))
    t = re.sub(r'[\[\]|]', ' ', str(title or '')).strip()[:110]
    return f'[[{n}|{t or n}]]' if n else t


def _entry(rid, aid):
    d = _j(os.path.join(HUB, 'work', rid + '.json'), {}) or {}
    return d.get(aid) if isinstance(d.get(aid), dict) else {}


def _one(s, n=170):
    s = re.sub(r'\s+', ' ', str(s or '')).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def today(keep):
    now = dt.datetime.now().astimezone()
    names = _names(keep)
    L = ['---', 'kind: today', f'date: {now:%Y-%m-%d}', '---',
         f'*Rebuilt by Jarvis at {now:%H:%M} on {now:%a %d %b}, from its own records. This page is rewritten at every sync, so write nothing here.*', '']

    # 1. what waits on him
    wait = _q("SELECT rid, aid, title, who, created FROM items WHERE needs_you=1 AND done=0 ORDER BY created DESC")
    L += [f'## Waiting on you ({len(wait)})', '']
    if not wait:
        L += ['Nothing.', '']
    for r in wait:
        w = _entry(r['rid'], r['aid'])
        age = (now.date() - dt.date.fromisoformat(str(r['created'])[:10])).days
        ask = (w.get('needs_you') or [''])[0] if isinstance(w.get('needs_you'), list) else w.get('needs_you')
        L.append(f"- {_link(names, r['rid'], r['aid'], r['title'])} · {r['who'] or ''} · {age} day{'s' if age != 1 else ''}")
        if ask:
            L.append(f'    - {_one(ask)}')
    L.append('')

    # 1b. promises still owed and the open questions that matter most (the 12 Nov triage against the knowledge base)
    def _jl(name):
        try:
            return [json.loads(l) for l in open(os.path.join(HUB, 'state', name))]
        except (OSError, ValueError):
            return []
    name = str(config.load().owner() or '').strip().lower()
    mine_names = {'owner', 'the owner', 'me', 'i'} | ({name, name.split()[0]} if name else set())
    me = lambda x: str(x or '').strip().lower() in mine_names
    rank = {'high': 0, 'medium': 1, 'low': 2}
    owed = sorted((r for r in _jl('promises_triage.jsonl') if r.get('verdict') == 'owed'), key=lambda r: (rank.get(r.get('priority'), 1), r['created']))
    mine = [r for r in owed if me(r.get('who'))]
    theirs = [r for r in owed if not me(r.get('who')) and (me(r.get('to_whom')) or not r.get('to_whom'))]
    if owed:
        L += [f'## You owe ({len(mine)})', ''] + [f"- **{r.get('priority')}** · to {r.get('to_whom') or 'not said'}: {_one(r['what'], 200)} *(noted {r['created']})*" for r in mine[:10]]
        L += ([f'- and {len(mine) - 10} more in [[Promises still owed]]'] if len(mine) > 10 else []) + ['']
        L += [f'## Owed to you ({len(theirs)})', ''] + [f"- **{r.get('priority')}** · {r.get('who') or 'not said'}: {_one(r['what'], 200)} *(noted {r['created']})*" for r in theirs[:8]] + ['']
    qs = sorted((r for r in _jl('needs_triage.jsonl') if r.get('verdict') == 'open' and r.get('priority') == 'high'), key=lambda r: r['created'], reverse=True)
    if qs:
        L += [f'## Open questions that matter most ({len(qs)} high priority)', ''] + [f"- {_one(r['statement'], 220)} *({r.get('workstream') or 'no workstream'}; noted {r['created']})*" for r in qs[:12]]
        L += ['', 'All of them, by workstream: [[Open questions, triaged]]', '']

    # 2. what Jarvis did since yesterday
    since = (now - dt.timedelta(hours=24)).isoformat(timespec='seconds')
    done = _q("SELECT rid, aid, title, lane, origin, kind, ended FROM tasks WHERE state='done' AND ended >= ? AND rid != '' "
              "AND kind IN ('item','initiative','investigation','review-fix') ORDER BY ended DESC", (since,))
    seen, asked, own = set(), [], []
    for t in done:
        k = (t['rid'], t['aid'])
        if k in seen:
            continue
        seen.add(k)
        w = _entry(*k)
        line = f"- {_link(names, t['rid'], t['aid'], t['title'])}: {_one(w.get('summary'), 150)}" if w.get('summary') else f"- {_link(names, t['rid'], t['aid'], t['title'])}"
        (asked if t['lane'] == 'asked' or t['origin'] == 'owner' else own).append(line)
    L += [f'## Done in the last 24 hours ({len(seen)})', '']
    if asked:
        L += ['**You asked for**', ''] + asked + ['']
    if own:
        L += ['**On its own** (inbox items, checks it started)', ''] + own[:25] + ([f'- and {len(own) - 25} more, in [[Jarvis work.base]]'] if len(own) > 25 else []) + ['']
    if not seen:
        L += ['Nothing finished.', '']
    failed = _q("SELECT COUNT(*) n FROM tasks WHERE state='failed' AND ended >= ?", (since,))[0]['n']
    running = _q("SELECT title FROM tasks WHERE state='running' ORDER BY started")
    L += [f"Running now: {', '.join(_one(r['title'], 60) for r in running) or 'nothing'}. Failed in the last 24 hours: {failed}.", '']

    # 3. what is wrong right now
    wrong = []
    try:
        from jarvis.spine import health
        wrong += [p['say'] for p in (health.deep().get('problems') or [])]
    except Exception:
        pass
    held = _q("SELECT ref, data FROM events WHERE kind='proof_held' AND at >= ? ORDER BY at DESC", (since,))
    for h in held[:6]:
        rid, _, aid = h['ref'].partition('|')
        d = json.loads(h['data'] or '{}')
        wrong.append(f"A reply is held back until its numbers are tied to an output: {_link(names, rid, aid, _entry(rid, aid).get('summary') or h['ref'])} ({', '.join(d.get('untied') or [])[:80]})")
    L += [f'## Wrong right now ({len(wrong)})', ''] + ([f'- {_one(x, 260)}' for x in wrong] or ['Nothing known.']) + ['']

    # 4. the scorecard
    sc = _j(os.path.join(HUB, 'state', 'scorecard.json'), {}) or {}
    if sc.get('metrics'):
        L += [f"## Scorecard (counted {str(sc.get('at'))[11:16]}; rates over 14 days)", '', '| | Now | Target |', '|---|---|---|']
        for m in sc['metrics']:
            mark = {True: '✓', False: '✗', None: '·'}[m.get('ok')]
            L.append(f"| {mark} {m['label']} | {str(m['show']).replace('|', '/')} | {m['target']} |")
        L.append('')

    # 4b. the ladder
    lad = _j(os.path.join(HUB, 'state', 'ladder.json'), {}) or {}
    if lad.get('classes'):
        L += ['## Autonomy ladder (advisory: nothing acts on it; levels move by your switch)', '', '| Level | Kind of action | Evidence so far | Limit |', '|---|---|---|---|']
        L += [f"| {c['level']} | {c['label']} | {c['evidence']}{' · **eligible for L3**' if c.get('eligible') else ''} | {c.get('limit') or ''} |" for c in lad['classes']]
        L.append('')

    kb = kbf()
    own = ' · '.join(f'[[{entities.folder(k)}.base]]' for k in sorted(entities.custom()))
    L += ['## Views', '', '![[Jarvis work.base#Needs you]]', '',
          'More: [[Jarvis work.base]] · [[Goals.base]] · [[Promises.base]] · [[Tickets.base]] · [[Pull requests.base]] · [[Workstreams.base]] · [[Chats.base]]'
          + (' · ' + own if own else '') + f' · maps: [[All work.canvas]] (one per workstream in Views/Maps) · the knowledge base: [[{kb}]]', '']
    return '\n'.join(L)


def _base(filters, props, views):
    """A .base file as YAML text. filters: list of filter statements (all must hold)."""
    def y(v):
        return json.dumps(v, ensure_ascii=False)
    L = ['filters:', '  and:'] + [f'    - {y(f)}' for f in filters]
    if props:
        L.append('properties:')
        for k, name in props:
            L += [f'  {k}:', f'    displayName: {y(name)}']
    L.append('views:')
    for v in views:
        L += [f"  - type: {v.get('type', 'table')}", f"    name: {y(v['name'])}"]
        if v.get('limit'):
            L.append(f"    limit: {v['limit']}")
        if v.get('filters'):
            L += ['    filters:', '      and:'] + [f'        - {y(f)}' for f in v['filters']]
        if v.get('group'):
            L += ['    groupBy:', f"      property: {v['group'][0]}", f"      direction: {v['group'][1]}"]
        L += ['    order:'] + [f'      - {c}' for c in v['order']]
    return '\n'.join(L) + '\n'


def views():
    work_cols = ['file.name', 'status', 'asked_by', 'source', 'reply', 'review', 'ticket', 'date']
    work_props = [('asked_by', 'Asked by'), ('needs_you', 'Needs you'), ('file.name', 'Item'), ('date', 'Arrived')]
    kb = kbf()
    out = {
        'Views/Jarvis work.base': _base(
            ['file.inFolder("Work")', 'kind == "work"'], work_props,
            [{'name': 'Needs you', 'filters': ['needs_you == true'], 'order': ['file.name', 'asked_by', 'status', 'date']},
             {'name': 'By status', 'type': 'kanban', 'group': ('status', 'ASC'), 'order': ['file.name', 'asked_by', 'date']},
             {'name': 'Newest first', 'group': ('date', 'DESC'), 'limit': 150, 'order': work_cols},
             {'name': 'Replies', 'filters': ['reply != "none"'], 'group': ('reply', 'ASC'), 'order': ['file.name', 'asked_by', 'reply', 'review', 'date']},
             {'name': 'Everything', 'order': work_cols}]),
        'Views/Tickets.base': _base(
            [f'file.inFolder("{kb}/Entities/Tickets")'],
            [('file.name', 'Ticket'), ('summary', 'Summary'), ('ticket_status', 'Status'), ('ticket_type', 'Type'), ('assignee', 'Assignee'),
             ('ticket_updated', 'Updated in the tracker'), ('ticket_read', 'Read on'), ('mentions', 'Notes that mention it')],
            [{'name': 'By status', 'type': 'kanban', 'group': ('ticket_status', 'ASC'), 'order': ['file.name', 'summary', 'assignee']},
             {'name': 'By assignee', 'group': ('assignee', 'ASC'), 'order': ['file.name', 'summary', 'ticket_status', 'priority', 'ticket_updated']},
             {'name': 'All tickets', 'order': ['file.name', 'summary', 'ticket_status', 'ticket_type', 'assignee', 'priority', 'ticket_updated', 'mentions', 'ticket_read']}]),
        'Views/Pull requests.base': _base(
            [f'file.inFolder("{kb}/Entities/Pull requests")'],
            [('file.name', 'Pull request'), ('pr_title', 'Title'), ('pr_state', 'State'), ('repo', 'Repository'), ('mentions', 'Notes that mention it')],
            [{'name': 'By repository', 'group': ('repo', 'ASC'), 'order': ['file.name', 'pr_title', 'pr_state', 'mentions']}]),
        'Views/Workstreams.base': _base(
            [f'file.inFolder("{kb}/Workstreams")', 'kind == "workstream"'], [('file.name', 'Workstream'), ('date', 'Last change'), ('data_cutoffs', 'Data cut-offs')],
            [{'name': 'By last change', 'filters': ['type != "sub-note"'], 'order': ['file.name', 'date', 'status', 'data_cutoffs', 'people']},
             {'name': 'With their parts', 'group': ('type', 'ASC'), 'order': ['file.name', 'date', 'status']}]),
        'Views/Goals.base': _base(                              # 13 Nov (spec 1 step 4): one note per goal and per promise still owed
            ['file.inFolder("Jarvis/Goals")', 'kind == "goal"'], [('file.name', 'Goal'), ('state', 'State'), ('date', 'Since'), ('tickets', 'Tickets'), ('review_at', 'Review')],
            [{'name': 'All goals', 'order': ['file.name', 'state', 'tickets', 'date', 'review_at']},
             {'name': 'By state', 'group': ('state', 'ASC'), 'order': ['file.name', 'tickets', 'date']}]),
        'Views/Promises.base': _base(
            ['file.inFolder("Jarvis/Promises")', 'kind == "promise"'],
            [('file.name', 'Promise'), ('who', 'Who owes'), ('to_whom', 'To whom'), ('due', 'Due'), ('date', 'Made on'), ('mine', 'Mine')],
            [{'name': 'I owe', 'filters': ['mine == true'], 'order': ['file.name', 'to_whom', 'due', 'date']},
             {'name': 'Owed to me', 'filters': ['mine == false'], 'group': ('who', 'ASC'), 'order': ['file.name', 'who', 'due', 'date']},
             {'name': 'All, newest first', 'group': ('date', 'DESC'), 'order': ['file.name', 'who', 'to_whom', 'due']}]),
        'Views/Chats.base': _base(
            [f'file.inFolder("{kb}/Chats")', 'kind == "chat"'], [('file.name', 'Chat'), ('date', 'Date'), ('workstreams', 'Workstreams')],
            [{'name': 'Newest first', 'group': ('date', 'DESC'), 'limit': 200, 'order': ['file.name', 'workstreams', 'date']}]),
    }
    for kind in sorted(entities.custom()):                # one view for each kind of thing the owner named in settings
        folder = entities.folder(kind)
        out[f'Views/{folder}.base'] = _base(
            [f'file.inFolder("{kb}/Entities/{folder}")'],
            [('file.name', kind.replace('_', ' ').capitalize()), ('description', 'What it is'), ('mentions', 'Notes that mention it')],
            [{'name': f'All {folder.lower()}', 'order': ['file.name', 'description', 'mentions']}])
    return out


def _canvas(nodes, edges):
    return json.dumps({'nodes': nodes, 'edges': edges}, ensure_ascii=False, indent=1) + '\n'


def maps(keep):
    """One map per workstream (JSON Canvas, Obsidian's open format) and one of the whole of the work. A map shows the workstream note
    in the middle with its parts below, its ledger and the project's decisions and open questions above, the people on the
    left, the tickets and the owner's own kinds of thing on the right. Only notes this sync writes are placed, so no card is empty."""
    from jarvis.world import vault_layout as VL
    L, P = VL.layout(), VL._props()
    have = lambda k: k in L and L[k]['path'] in keep
    ws = {k: v for k, v in L.items() if v['kind'] == 'workstream' and have(k)}
    parents = sorted((k for k in ws if not ('--' in k and k.split('--', 1)[0] in ws)), key=lambda k: ws[k]['date'], reverse=True)
    out = {}

    def fnode(i, key, x, y, w=300, h=64, color=None):
        d = {'id': i, 'type': 'file', 'file': L[key]['path'], 'x': x, 'y': y, 'width': w, 'height': h}
        if color:
            d['color'] = color
        return d

    def column(tag, label, keys, x, y, w=300, h=64, color=None):
        """A labelled group with its cards stacked; returns (nodes, group id or None)."""
        keys = [k for k in keys if have(k)]
        if not keys:
            return [], None
        ns = [{'id': f'g_{tag}', 'type': 'group', 'label': f'{label} ({len(keys)})', 'x': x - 20, 'y': y - 20,
               'width': w + 40, 'height': len(keys) * (h + 12) + 28}]
        ns += [fnode(f'{tag}{j}', k, x, y + j * (h + 12), w, h, color) for j, k in enumerate(keys)]
        return ns, f'g_{tag}'

    for k in parents:
        pp = P.get(k) or {}
        nodes = [fnode('c', k, 0, 0, 560, 440, '5')]
        edges = []
        parts = sorted(s for s in ws if s.startswith(k + '--'))
        for tag, label, keys, x, y, w, side, color in (
                ('part', 'Parts of this note', parts, 0, 500, 560, ('bottom', 'top'), '5'),
                ('ppl', 'People', ['person:' + x for x in (pp.get('people') or [])[:8]], -420, 0, 300, ('left', 'right'), '6'),
                ('tic', 'Tickets', ['ticket:' + x for x in (pp.get('tickets') or [])[:12]], 660, 0, 300, ('right', 'left'), '2'),
                *[(f'own{i}', entities.folder(kind), [f'{kind}:{x}' for x in (pp.get(entities.key(kind)) or [])[:10]], 1020 + 320 * i, 0, 260,
                   ('right', 'left'), '4') for i, kind in enumerate(sorted(entities.custom())[:3])]):
            ns, gid = column(tag, label, keys, x, y, w, 64, color)
            nodes += ns
            if gid and not tag.startswith('own'):
                edges.append({'id': f'e_{tag}', 'fromNode': 'c', 'fromSide': side[0], 'toNode': gid, 'toSide': side[1]})
        top = [x for x in ('ledger:' + k, 'Decisions', 'Open questions', 'Timeline') if have(x)]
        for j, x in enumerate(top):
            nodes.append(fnode(f'top{j}', x, j * 300 - (len(top) - 1) * 150 + 130, -160, 280, 90, '3'))
            edges.append({'id': f'e_top{j}', 'fromNode': f'top{j}', 'fromSide': 'bottom', 'toNode': 'c', 'toSide': 'top'})
        out[f"Views/Maps/{ws[k]['name']}.canvas"] = _canvas(nodes, edges)

    nodes, edges = [], []                                 # the project: the 8 project notes, then the workstreams, newest change first
    proj = [x for x, v in L.items() if v['kind'] == 'project' and have(x)]
    for j, x in enumerate(proj):
        nodes.append(fnode(f'p{j}', x, j * 250, -180, 230, 70, '3'))
    cols = 5
    for j, k in enumerate(parents):
        nodes.append(fnode(f'w{j}', k, (j % cols) * 400, (j // cols) * 300, 360, 260, '5'))
    if nodes:
        out['Views/Maps/All work.canvas'] = _canvas(nodes, edges)
    return out


def pages(keep):
    """{vault path: text} of everything generated here. A fault in one page never loses the others."""
    out = {}
    try:
        out['Today.md'] = today(keep)
    except Exception as e:
        out['Today.md'] = f'# Today\n\nCould not be built this time ({e.__class__.__name__}: {str(e)[:200]}).\n'
    try:
        out.update(views())
    except Exception:
        pass
    try:
        out.update(maps(keep))
    except Exception:
        pass
    return out
