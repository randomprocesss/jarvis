"""Jarvis's own state, as typed tools for the assistant. Asked to redo "the draft for Priya" or "the reply in that thread", it
must find that item in its own records, see its draft / file / sent state, and act on it, never go searching the apps.

A tiny MCP server (stdio, JSON-RPC, stdlib only), read-only:
  inbox_find(query)        items matching a person or topic, with rid/aid and their state (draft, file attached, sent, running)
  inbox_item(rid, aid)     one item: its points and their states, the drafted reply, attachments, files made, sent or not
  tasks_now()              what Jarvis's workers run now and what waits
  needs_find(query)        what the owner's work is waiting for (Watching), matching a topic
  project_brief, kb_entity, kb_facts, kb_search, memory_recall: the knowledge base and the long-term memory
    python -m jarvis.mcp --selftest
"""
from jarvis import paths
import datetime as dt, glob, json, os, re, sqlite3, sys

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'jarvis.db')
from jarvis import presenter  # noqa: E402
ORG = presenter.org_words()          # the organisation's names, from settings (never shown on a shared screen)


def _load(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d


def _clean(s, n=200):
    s = ORG.sub('our', re.sub(r'\s+', ' ', str(s or ''))).strip()
    return s if len(s) <= n else s[:n - 1] + '…'


def _items(days=14):
    st = _load(os.path.join(HUB, 'roundups', 'state.json'), {}) or {}
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    for f in sorted(glob.glob(os.path.join(HUB, 'roundups', '20*.json')), reverse=True):
        rid = os.path.basename(f)[:-5]
        if rid[:10] < cut:
            continue
        d = _load(f, {}) or {}
        if d.get('kind') in ('desk', 'fixture'):
            continue                    # Jarvis's own desk is not his inbox; it reaches him in the fortnightly brief (9 Oct)
        work = _load(os.path.join(HUB, 'work', rid + '.json'), {}) or {}
        for a in d.get('actions') or []:
            aid = str(a.get('id') or '')
            w = work.get(aid) or {}
            yield rid, aid, a, w, st.get(rid) or {}, d


def _when(iso):
    """A time as they would say it, in their time zone: "today 1:30 pm", "yesterday 4:05 pm", "2 Oct 3:56 pm". 5 Oct: a raw
    13:30 was spoken as "half one this morning"."""
    try:
        t = dt.datetime.fromisoformat(str(iso)).astimezone()
    except (TypeError, ValueError):
        return None
    d = (dt.date.today() - t.date()).days
    day = 'today' if d == 0 else 'yesterday' if d == 1 else t.strftime('%-d %b')
    return f"{day} {t.strftime('%-I:%M %p').lower()}"


def _how_file(x, rep, sent):
    """How a file reaches the person, read from the reply itself (5 Oct): since drafts are made with the link line
    already in them, a Slack draft carries the file as a link (the shared folder for Excel/CSV, a canvas for a write-up), so
    sending from Slack sends the file too."""
    text = str(rep.get('text') or '') + '\n' + str(rep.get('sent_text') or '')   # sent_text is a 200-char read-back only
    m = re.search(r'https://\S+', str(x.get('link_line') or ''))
    link = (m.group(0).rstrip(')') if m else '') or str(x.get('canvas_url') or '')
    if x.get('inline') in (True, 'True', 'true'):
        how, ok = 'inline in the reply text', True
    elif link and link in text:
        how, ok = ('as a link into the shared folder' if x.get('shared') else 'as a Slack canvas link') + ' inside the reply text', True
    elif sent:
        how, ok = 'the sent text has no link to it (it may have gone some other way; check the thread)', False
    else:
        how, ok = 'not linked yet: Jarvis adds the link line when it makes the Slack draft (or at Send)', False
    where = re.search(r'\(in ([^:()]+/[^:()]+)[:)]', str(x.get('link_line') or ''))   # "(in <shared folder>/<day>: <link>)"
    return {'file': os.path.basename(x.get('path') or ''), 'how': how,
            'link_to': (f"shared folder {where.group(1)}" if where else ('Slack canvas' if x.get('canvas_url') else None)),
            'went_out': bool(sent and ok), 'in_the_slack_draft': bool(ok and rep.get('slack_draft'))}


def _state(rid, aid, a, w, ticks, running):
    pts = a.get('points') or []
    done = bool(pts) and all(ticks.get(f'{aid}.{i}') for i in range(len(pts)))
    rep = w.get('reply') or {}
    sent = bool(rep.get('sent_at'))
    att = [_how_file(x, rep, sent) for x in rep.get('attachments') or []]
    # sent = the draft itself went out (Jarvis's Send, or he posted it from Slack: sentcheck sets sent_at). replied_by_owner alone
    # can be a quick acknowledgement ("Sure, will do") while the deliverable is still to send
    needs = [p for p in w.get('points') or [] if isinstance(p, dict) and p.get('state') in ('needs_you', 'decide')]
    if (rid, aid) in running:
        state = 'Claude is working on it now'
    elif done:
        state = 'done'
    elif sent:
        state = 'reply sent' + (f" ({rep.get('sent_by')})" if rep.get('sent_by') else '')
    elif needs:
        state = 'needs the owner'
    elif rep.get('text'):
        state = 'reply drafted, not sent'
    elif w:
        state = 'worked on, ' + str(w.get('status') or 'open')
    else:
        state = 'not started'
    return {'rid': rid, 'aid': aid, 'who': _clean(a.get('who'), 60), 'where': _clean(a.get('where') or rep.get('where'), 60),
            'title': _clean(a.get('title') or a.get('text'), 140), 'state': state, 'draft': bool(rep.get('text')),
            # a finished item's old "not created: waiting on your decisions" is history, not a state
            'slack_draft': (None if state == 'done' and str(rep.get('slack_draft') or '').startswith('not created') else rep.get('slack_draft') or None),
            'sent_when': _when(rep.get('sent_at')) if sent else None,
            'merged_into': w.get('merged_into') or None, 'attachments': att, 'sent': sent, 'needs_owner': len(needs),
            'updated': w.get('updated') or (d_at(rid))}


def d_at(rid):
    return rid[:10]


def _running():
    try:
        c = sqlite3.connect(DB)
        return {(r[0], r[1]) for r in c.execute("SELECT rid, aid FROM tasks WHERE state='running' AND rid != ''")}
    except Exception:
        return set()


def inbox_find(args):
    q = str(args.get('query') or '').lower()
    words = [w for w in re.findall(r'[a-z0-9§.]+', q) if len(w) >= 3 and w not in ('the', 'and', 'for', 'about', 'from', 'with', 'thread', 'message', 'draft', 'reply')]
    include_done = bool(args.get('include_done'))
    run = _running()
    hits = []
    for rid, aid, a, w, ticks, d in _items():
        s = _state(rid, aid, a, w, ticks, run)
        if s['state'] == 'done' and not include_done:
            continue
        hay = ' '.join([s['who'], s['where'], s['title'], _clean(a.get('text'), 600), str((w.get('reply') or {}).get('to') or '')]).lower()
        score = sum(2 if w_ in (s['who'] or '').lower() else 1 for w_ in words if w_ in hay) if words else 1
        if score:
            hits.append((score, s))
    hits.sort(key=lambda x: (-x[0], x[1]['rid']), reverse=False)
    out = [h for _, h in hits[:int(args.get('limit') or 8)]]
    if not out and not include_done:
        # "no open items" for a person lets an old memory say a reply still waits when it went hours ago. Say what finished.
        try:                                          # the search answers in words when nothing matches at all
            done = json.loads(inbox_find({**args, 'include_done': True}) or '[]') if words else []
        except ValueError:
            done = []
        if isinstance(done, dict):
            done = done.get('items') or []
        if isinstance(done, list) and done:
            return json.dumps({'open': [], 'finished': done[:5], 'note': 'Nothing open matches; these matching items are finished '
                               '(their state says whether the reply was sent).'}, ensure_ascii=False)
    if not out:
        return 'No matching open items. (include_done=true searches finished ones too.)'
    note = ('NOTE: a file goes as a link line inside the reply text (the shared folder for Excel/CSV, a Slack canvas for a write-up). '
            'went_out=true means the sent message carried that link; the draft in Slack already holds it.')
    return json.dumps({'items': out, 'note': note} if any(h['attachments'] for h in out) else out, ensure_ascii=False)


def inbox_item(args):
    rid, aid = str(args.get('rid') or ''), str(args.get('aid') or '')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', rid) or not re.fullmatch(r'[A-Za-z0-9._-]{1,40}', aid):
        raise ValueError('bad rid/aid')
    d = _load(os.path.join(HUB, 'roundups', rid + '.json'), {}) or {}
    a = next((x for x in d.get('actions') or [] if x.get('id') == aid), None)
    if not a:
        return 'No such item.'
    w = (_load(os.path.join(HUB, 'work', rid + '.json'), {}) or {}).get(aid) or {}
    s = _state(rid, aid, a, w, (_load(os.path.join(HUB, 'roundups', 'state.json'), {}) or {}).get(rid) or {}, _running())
    rep = w.get('reply') or {}
    outd = os.path.join(HUB, 'work', 'outputs', f'{rid}-{aid}')
    files = sorted(os.listdir(outd))[:20] if os.path.isdir(outd) else []
    s['long_run'] = next((x for x in _jobs() if x['item'] == f'{rid}|{aid}'), None)   # 19 Oct: alive or not, from its process
    s.update({'ask': _clean(a.get('text'), 700), 'summary': _clean(w.get('summary'), 600),
              'points': [{'text': _clean(p.get('text') if isinstance(p, dict) else p, 160), 'state': (p.get('state') if isinstance(p, dict) else None),
                          'note': _clean((p.get('note') or p.get('answer')) if isinstance(p, dict) else '', 240)} for p in (w.get('points') or [])][:10],
              'reply_text': _clean(rep.get('text'), 900), 'reply_to': rep.get('to'), 'files_made': files,
              'sent_at': rep.get('sent_at'), 'sent_when': _when(rep.get('sent_at')), 'sent_by': rep.get('sent_by')})
    return json.dumps(s, ensure_ascii=False)


def _jobs():
    """Long runs a worker started with run_job.py (a notebook or a scenario run). They go on after the worker ends, so
    "nothing is running" is only true when no such run is alive either."""
    import datetime as _dt
    out = []
    base = os.path.join(HUB, 'work', 'outputs')
    for p in glob.glob(os.path.join(base, '*', 'job.json')) + glob.glob(os.path.join(base, '*', '*', 'job.json')):
        j = _load(p, {}) or {}
        if j.get('state') not in ('running', 'waiting_vpn'):
            continue
        try:
            os.kill(int(j.get('pid')), 0); alive = True
        except (OSError, ValueError, TypeError):
            alive = False
        d = os.path.dirname(p)
        folder = os.path.relpath(d, base).split(os.sep)[0]
        m = re.match(r'(.+)-(a\d+)$', folder)
        last = 0.0
        for root, dirs, fs in os.walk(d):
            if root.count(os.sep) - d.count(os.sep) >= 2:
                dirs[:] = []
            for f in fs:
                try:
                    last = max(last, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
        out.append({'item': f'{m.group(1)}|{m.group(2)}' if m else '', 'what': 'long run started by a worker', 'job': os.path.relpath(d, HUB),
                    'state': ('waiting for the VPN' if j['state'] == 'waiting_vpn' else 'running') if alive else 'died: its process is gone',
                    'started': j.get('started'), 'last_activity': _dt.datetime.fromtimestamp(last).astimezone().isoformat(timespec='seconds') if last else ''})
    return out


def tasks_now(args):
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("SELECT lane, origin, state, title, rid, aid, started, note FROM tasks WHERE state IN ('running','queued','paused','held') ORDER BY created").fetchall()
    except Exception as e:
        return f'ledger not readable: {e}'
    lane = {'asked': 'for the owner', 'background': 'inbox work', 'operator': 'carrying out approvals'}
    def title(rid, aid):
        d = _load(os.path.join(HUB, 'roundups', rid + '.json'), {}) or {}
        a = next((x for x in d.get('actions') or [] if x.get('id') == aid), {})
        return a.get('title') or a.get('text') or ''
    out = [{'state': r[2], 'what': _clean(r[3] or (title(r[4], r[5]) if r[4] else ''), 120), 'kind': 'noticed by Jarvis' if r[1] == 'initiative' else lane.get(r[0], r[0]),
            'item': f'{r[4]}|{r[5]}' if r[4] else '', 'started': r[6], **({'why_waiting': _clean(r[7], 160)} if r[7] and r[2] != 'running' else {})} for r in rows]
    out += _jobs()                                       # a long run is running work too, with no worker on it
    return json.dumps(out, ensure_ascii=False) if out else 'Nothing is running or waiting.'


def project_brief(args):
    """The settled picture of one topic of the owner's work. Searches the knowledge base first (project-wide notes and
    workstream notes built from every Claude Code chat, Jarvis task, document and decision, refreshed nightly), then the
    desk's topic briefs (brain/topics). With no workstream match, the best-matching chat notes (brain/sessions). An empty
    topic lists the topics."""
    kb = sorted(glob.glob(str(paths.kb('topics', '*.md')))) + sorted(glob.glob(str(paths.kb('*.md'))))
    files = kb + sorted(glob.glob(os.path.join(HUB, 'brain', 'topics', '*.md')))
    if not files:
        return 'No topic briefs yet.'
    head = lambda t: (re.search(r'^title: (.*)$', t, re.M) or re.search(r'^# (.*)$', t, re.M))
    key = lambda f: ('kb:' if f in kb else 'desk:') + os.path.basename(f)[:-3]
    docs = {key(f): _load_text(f) for f in files}
    words = [w for w in re.findall(r'[a-z0-9$%.]+', str(args.get('topic') or '').lower()) if len(w) >= 3]
    if not words:
        return json.dumps([{'topic': k, 'title': (head(v).group(1) if head(v) else k)} for k, v in docs.items()], ensure_ascii=False)
    score = {k: sum(v.lower().count(w) for w in words) + 5 * sum(w in k for w in words) + (3 if k.startswith('kb:') else 0) * bool(sum(v.lower().count(w) for w in words))
             for k, v in docs.items()}
    best = sorted((k for k in score if score[k]), key=lambda k: -score[k])[:2]
    def label(k):                                         # what each brief is, so a chat line never reads as settled
        if k.startswith('kb:'):
            v = docs[k]; up = (re.search(r'^updated: (.*)$', v, re.M) or [None, '?'])[1]
            return f'{k} | workstream note: the settled view, updated {up}; check a number against its cited source before stating it'
        return f'{k} | desk topic brief (Jarvis): a lead, check against the knowledge base'
    out = [f'[{label(k)}]\n' + docs[k][:4500] for k in best]
    ents = _entity_files(args.get('topic'))              # a question that names a ticket, a table, a code or a person
    if ents:                                             # gets that entity page first (what it is, its facts, its evidence)
        out = [f'[entity page {os.path.basename(f)[:-3]} | built in code from the notes and the sources as read; each mention says what kind of evidence it is]\n'
               + _load_text(f)[:3000] for f in ents[:2]] + out
    if not any(k.startswith('kb:') for k in best):        # no workstream note yet: the chat notes that match best
        notes = {f: _load_text(f) for f in glob.glob(os.path.join(HUB, 'brain', 'sessions', '*.md'))}
        ns = {f: sum(t.lower().count(w) for w in words) for f, t in notes.items()}
        for f in sorted((f for f in ns if ns[f]), key=lambda f: -ns[f])[:3]:
            out.append(f'[chat note {os.path.basename(f)[:-3]} | what was said on {os.path.basename(f)[:10]}; it may have been corrected later]\n' + notes[f][:1800])
    more = _search(args.get('topic') or '', ('source note', 'chat note', 'memory'), 4) + \
        _search(args.get('topic') or '', ('raw document', 'raw chat'), 3)        # 23 Oct (KB plan Step 7b): the 1,100+ source
    if more:                                                                       # notes and the raw text were never searched
        out.append('ALSO IN THE KNOWLEDGE BASE (passages; «» marks the matching words; quote with the source):\n' + more)
    facts = _facts(args.get('topic'))                    # 12 Nov (one answer path): the fact ledger first. The exam's misses were
    if facts:                                            # facts that sit in the ledger but not in the first screens of a note
        out.insert(0, 'CHECKED FACTS FROM THE LEDGER (each is one statement parsed from the notes, with its data cut-off and the note '
                      'it came from; "current" unless marked SUPERSEDED, and a superseded value is never given as the answer; check a '
                      'number against its source before telling other people):\n' + facts)
    return '\n\n'.join(out) if out else 'No topic brief matches; topics: ' + ', '.join(docs)


def _facts(query, k=8):
    try:
        paths.use_tools()
        import kb_facts
        return kb_facts.lines(str(query or ''), k)
    except Exception:
        return ''


def kb_facts_tool(args):
    """The ledger records that match a question."""
    return _facts(args.get('query'), int(args.get('limit') or 10)) or 'No ledger record matches.'


def _search(query, kinds, limit):
    """The best passages from the full-text index (tools/kb_search.py), one line of source each."""
    try:
        paths.use_tools()
        import kb_search
        rows = kb_search.search(str(query), limit=limit, kinds=list(kinds))
    except Exception:
        return ''
    return '\n'.join(f"- [{r['kind']}: {r['title']}{', ' + r['date'] if r['date'] else ''}; {r['source']} {r['where']}; "
                     f"reads as: {r.get('reads_as', '?')}{'; data cut-off ' + ', '.join(r['data_cutoffs']) if r.get('data_cutoffs') else ''}] {r['passage']}"
                     for r in rows)


_ENT = {'at': 0.0, 'pages': {}}
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,40}")


def _entity_index():
    """{name in lower case: page} for every entity page of the knowledge base (entities/<kind>/<name>.md): tickets, tables,
    codes, places, people. Read again when the folder changes."""
    base = str(paths.kb('entities'))
    try:
        stamp = max([os.path.getmtime(base)] + [os.path.getmtime(d) for d in glob.glob(os.path.join(base, '*')) if os.path.isdir(d)])
    except OSError:
        return {}
    if stamp != _ENT['at']:
        _ENT['at'], _ENT['pages'] = stamp, {os.path.basename(f)[:-3].lower(): f for f in sorted(glob.glob(os.path.join(base, '*', '*.md')))}
    return _ENT['pages']


def _entity_files(text):
    """Entity pages named in a question: a word of it that is a page's name, or the last part of one (a page 'north-PL04'
    answers to 'PL04')."""
    pages = _entity_index()
    if not pages:
        return []
    tails = {}
    for name, f in pages.items():
        tail = name.rsplit('-', 1)[-1]
        if tail != name and len(tail) >= 4 and re.search(r'\d', tail):
            tails.setdefault(tail, f)
    out = []
    for m in _TOKEN.finditer(str(text or '')):
        t = m.group(0).strip('.-_').lower()
        f = pages.get(t) or tails.get(t)
        if f and f not in out:
            out.append(f)
    return out


def kb_entity(args):
    """One entity page in full: what it is, its ledger facts and every mention with what kind of evidence it is."""
    q = str(args.get('id') or args.get('query') or '')
    fs = _entity_files(q)
    if not fs:
        return 'No entity page for that. Pages exist for the tickets, tables, codes, pull requests and people the knowledge base has met; try kb_search.'
    return '\n\n'.join(_load_text(f)[:int(args.get('max_chars') or 7000)] for f in fs[:3])


def kb_search_tool(args):
    """Word-for-word search of everything the knowledge base was built from."""
    where = str(args.get('where') or 'all')
    kinds = {'notes': ('workstream', 'project', 'person', 'owner', 'source note', 'chat note', 'memory', 'entity', 'ledger'), 'raw': ('raw document', 'raw chat'), 'people': ('person',), 'owner': ('owner',)}.get(where)
    try:
        paths.use_tools()
        import kb_search
        rows = kb_search.search(str(args.get('query') or ''), limit=min(int(args.get('limit') or 8), 20), kinds=list(kinds) if kinds else None)
    except Exception as e:
        return f'search failed: {str(e)[:120]}'
    return json.dumps(rows, ensure_ascii=False) if rows else 'Nothing matches those words.'


def _load_text(p):
    try:
        return open(p, encoding='utf-8').read()
    except OSError:
        return ''


def needs_find(args):
    q = [w for w in re.findall(r'[a-z0-9]+', str(args.get('query') or '').lower()) if len(w) >= 3]
    try:
        c = sqlite3.connect(DB)
        # Watching shows project work only (scope 'work', 5 Oct); this tool answers about the same list
        rows = c.execute("SELECT id, kind, state, statement, target, candidates, created FROM needs WHERE state IN ('open','partial','answered') "
                         "AND dismissed_at IS NULL AND coalesce(scope, 'work') = 'work'").fetchall()
    except Exception as e:
        return f'ledger not readable: {e}'
    hits = []
    for r in rows:
        hay = (r[3] + ' ' + (r[4] or '')).lower()
        cands = [x['source'] for x in json.loads(r[5] or '[]') if x.get('strength') == 'strong'][:3]
        # no topic ("what's pending in Watching?", 5 Oct: the empty search said nothing was there): the ones with data
        # Jarvis could check first, then the newest
        sc = sum(1 for w in q if w in hay) if q else (2 if cands else 1)
        if sc:
            hits.append((sc, r[6] or '', {'kind': r[1], 'state': r[2], 'need': _clean(r[3], 220), 'must_produce': _clean(r[4], 140),
                                          'data_to_check': cands, 'noted': (r[6] or '')[:10]}))
    hits.sort(key=lambda x: (-x[0], x[1]), reverse=False)
    if not q:
        hits.sort(key=lambda x: (-x[0], [-ord(ch) for ch in x[1]]))
        return json.dumps({'open_in_watching': len(rows), 'top': [h for _, _, h in hits[:8]]}, ensure_ascii=False) if hits else 'Watching is empty.'
    return json.dumps([h for _, _, h in hits[:8]], ensure_ascii=False) if hits else 'Nothing in Watching matches that.'


def memory_recall(args):
    """What Jarvis knows about a topic, with dates: facts (still true, or from when to when), past conversations, episodes
    and what they asked to remember (app/learned.py, FTS5)."""
    from jarvis import learned
    rows = learned.recall(str(args.get('query') or ''), limit=int(args.get('limit') or 8), since=args.get('since'), until=args.get('until'))
    if not rows:
        return 'Nothing in memory matches that.'
    return json.dumps([{'what': _clean(r['text'], 400), 'when': (r['at'] or '')[:16], 'kind': r['src']} for r in rows], ensure_ascii=False)


S = lambda **p: {'type': 'object', 'properties': {k: {kk: vv for kk, vv in v.items() if kk != 'req'} for k, v in p.items()},
                 'required': [k for k, v in p.items() if v.get('req')]}
TOOLS = {
    'inbox_find': (inbox_find, "Find inbox items (their To do) by person or topic, e.g. 'Priya', 'Sam budget excel', 'user guide'. Returns rid/aid and each item's state: drafted or not, files attached and how they go out, sent or not (also when they sent it themselves from Slack), running now. Use this before acting on an item or answering about drafts, files or what Jarvis did.",
                   S(query={'type': 'string', 'req': True}, include_done={'type': 'boolean'}, limit={'type': 'integer'})),
    'inbox_item': (inbox_item, 'One inbox item in full: the ask, its points with their states, the drafted reply text, attachments, files made, sent or not.',
                   S(rid={'type': 'string', 'req': True}, aid={'type': 'string', 'req': True})),
    'tasks_now': (tasks_now, "What Jarvis's workers are doing now and what waits (for the owner, inbox work, noticed by Jarvis, approvals), and every long run still going (a notebook or scenario a worker started: it runs on after the worker ends; alive or died, started, last activity).", S()),
    'memory_recall': (memory_recall, "What Jarvis knows or was told about a topic, with dates: facts (still true, or true from when to when), past conversations, episodes, what they asked to remember. Use for 'what do you know about X', 'what did we decide on Y', 'what was it before'. since/until: YYYY-MM-DD.",
                      S(query={'type': 'string', 'req': True}, since={'type': 'string'}, until={'type': 'string'}, limit={'type': 'integer'})),
    'project_brief': (project_brief, "The best current summary of a topic of their work (a lead with its sources, not proof: check a number against its source before stating it): its current position with sources, open questions and what to watch, kept current by Jarvis's desk. Call it FIRST for any question about where their work stands; an empty topic lists the topics.",
                      S(topic={'type': 'string'})),
    'kb_entity': (kb_entity, "One page about a ticket, a table, a code, a place, a pull request or a person: what it is, its facts, and every note that mentions it, each labelled settled view / record / as said on a date. Use it whenever a question names one of these.",
                  {'type': 'object', 'properties': {'id': {'type': 'string', 'description': "the name as written, e.g. a ticket key 'ABC-753' or a table name"}}, 'required': ['id']}),
    'kb_facts': (kb_facts_tool, "The fact ledger of their work: checked single statements (a number with its unit, scope and data cut-off, a decision, an open point), each with the note it came from and whether it is current or SUPERSEDED. Use it for 'how many', 'what share', 'what was decided', 'what is the value of' questions; project_brief already includes the best matches. A superseded record is shown so the old value is recognised, never to be given as current.",
                 S(query={'type': 'string', 'req': True}, limit={'type': 'integer'})),
    'kb_search': (kb_search_tool, "Word-for-word search of everything the knowledge base was built from: workstream, project, source and chat notes, memory notes, the raw text of every document read, and every archived work chat (their words, Claude's replies, commands and their output). For a detail a brief does not hold: an exact number, a name, a table, a command, who said what and when. Returns passages with their source and date. where: notes | raw | people | owner (how the owner knows, decides and works) | all (default).",
                  S(query={'type': 'string', 'req': True}, where={'type': 'string'}, limit={'type': 'integer'})),
    'needs_find': (needs_find, "What their work is waiting for (Watching): open needs, questions and decisions matching a topic, with the data Jarvis could check. An empty query lists the top open ones and how many there are.",
                   S(query={'type': 'string'})),
}


def handle(msg):
    m, i = msg.get('method'), msg.get('id')
    if m == 'initialize':
        return {'jsonrpc': '2.0', 'id': i, 'result': {'protocolVersion': (msg.get('params') or {}).get('protocolVersion', '2025-06-18'),
                                                       'capabilities': {'tools': {}}, 'serverInfo': {'name': 'jarvis', 'version': '1.0'}}}
    if m == 'tools/list':
        return {'jsonrpc': '2.0', 'id': i, 'result': {'tools': [{'name': k, 'description': v[1], 'inputSchema': v[2]} for k, v in TOOLS.items()]}}
    if m == 'tools/call':
        p = msg.get('params') or {}
        fn = TOOLS.get(p.get('name'), (None,))[0]
        try:
            if not fn:
                raise ValueError('unknown tool')
            text, err = fn(p.get('arguments') or {}), False
        except Exception as e:
            text, err = f'{e.__class__.__name__}: {e}', True
        return {'jsonrpc': '2.0', 'id': i, 'result': {'content': [{'type': 'text', 'text': text}], 'isError': err}}
    if m == 'ping':
        return {'jsonrpc': '2.0', 'id': i, 'result': {}}
    if i is not None:
        return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32601, 'message': f'unknown method {m}'}}
    return None


def main():
    if '--selftest' in sys.argv:
        print(inbox_find({'query': 'budget excel'})[:900]); print(tasks_now({})[:500]); print(needs_find({'query': 'cost per order'})[:500])
        return
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except Exception:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + '\n'); sys.stdout.flush()


if __name__ == '__main__':
    main()
