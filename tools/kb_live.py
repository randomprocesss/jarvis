"""The live sources of the owner's work, read word for word into the knowledge base: their ticket projects, wiki spaces,
chat channels and work direct messages, work mail, Teams, meetings and a shared-files folder. One unit per item into
brain/sources_extra/l_<source>_<id>.txt, then read into notes by kb_extra like every other source.

What is read comes from settings ([sources]); a source with nothing set is not read:
  tracker_projects   ticket project keys read in full (default: knowledge.ticket_keys and the tracker's own projects)
  wiki_spaces        wiki space keys
  channels           chat channels, one "ID name [since YYYY-MM-DD]" per line
  mail_terms         words that mark work mail, Teams messages and meetings (default: work.search_words)
  client_domains     a meeting with someone from one of these mail domains counts as work
  shared_folder      the shared-files folder to catalogue (the file:/// address the connector gives), and
  shared_read        its top folders whose documents are read in full
  since              the first day read (YYYY-MM-DD; default: twelve months back)

How: a small headless Claude run calls only read tools (claude.ai connectors) and replies "DONE"; this code then takes the
tool results, word for word, from that run's transcript, so the model never re-types the text (cheap, and nothing is lost
or paraphrased). The runs use their own folder (live_fetch in the data home), which the nightly reader and the archive
skip as processing runs. Nothing is posted, reacted to, or marked read: only read tools are allowed.
Work only: only the channels and terms the owner listed are fetched; each unit of messages, mail or chat then goes through
the personal screens like every other note.

    kb_live.py what                which sources are set up, and what each would read
    kb_live.py pilot jira          one page of 20 tickets: cost, size, and the unit it makes
    kb_live.py jira                every ticket of every listed project
    kb_live.py confluence list | confluence pages | slack [names] | slack dms | mail list | mail read | teams | meetings | sharepoint
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config, entities, paths
import hashlib, datetime as dt, glob, json, os, re, subprocess, sys, time

HUB = str(paths.home())
RUN = os.path.join(HUB, 'live_fetch')
PROJ = str(paths.claude_chats_for(RUN))               # where Claude Code keeps the transcripts of the fetch runs
EXTRA = os.path.join(HUB, 'brain', 'sources_extra')
STATE = os.path.join(HUB, 'state', 'kb_live.json')
CLAUDE, HAIKU = (paths.claude_bin() or 'claude'), config.model('quick')
ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000',
           MAX_MCP_OUTPUT_TOKENS='60000', JARVIS_PROC='1', JARVIS_LANE='kb_live')
JIRA = 'mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql'
KEY, SPACE = '', ''          # the ticket project and the wiki space being read now: the runner sets them, one at a time


def _src(name):
    return config.load().get('sources.' + name)


def cloud():
    """The tracker site's id (settings: tracker.cloud_id)."""
    return str(config.load().get('tracker.cloud_id') or '')


def since():
    """The first day read: sources.since, else twelve months back."""
    try:
        return dt.date.fromisoformat(str(_src('since') or ''))
    except ValueError:
        return dt.date.today() - dt.timedelta(days=365)


def projects():
    return [str(k).strip().upper() for k in _src('tracker_projects') or [] if str(k).strip()] or entities.ticket_keys()


def spaces():
    return [str(k).strip() for k in _src('wiki_spaces') or [] if str(k).strip()]


def channels():
    """[(id, name, since)] from sources.channels ("ID name [since]" per line)."""
    out = []
    for line in _src('channels') or []:
        f = str(line).replace('#', ' ').split()
        if len(f) >= 2:
            out.append((f[0], f[1], f[2] if len(f) > 2 and re.fullmatch(r'\d{4}-\d\d-\d\d', f[2]) else str(since())))
    return out


def mail_terms():
    return [str(w).strip() for w in (_src('mail_terms') or config.load().search_words()) if str(w).strip()]


def teams_q():
    return ' OR '.join(f'"{w}"' if ' ' in w else w for w in mail_terms())


def _names_work(text):
    ws = mail_terms()
    return bool(ws) and bool(re.search(r'\b(?:' + '|'.join(re.escape(w) for w in ws) + r')', text, re.I))


def _client(text):
    low = text.lower()
    return any(str(d).lower().lstrip('@') in low for d in _src('client_domains') or [] if str(d).strip())


def read_tops():
    return tuple(str(x) for x in _src('shared_read') or [])


def me():
    return str(_src('slack_user_id') or '')


def _jn():
    """Ticket units carry the project's key in their name when more than one project is read."""
    return '' if len(projects()) <= 1 else KEY.lower() + '_'


def _jk():
    return 'jira' if len(projects()) <= 1 else 'jira_' + KEY


def what():
    """Which sources are set up and what each would read (for Setup and for `kb_live.py what`)."""
    c = config.load()
    return {'since': str(since()),
            'tracker': {'connected': c.has('atlassian'), 'site': bool(cloud()), 'projects': projects()},
            'wiki': {'connected': c.has('atlassian'), 'spaces': spaces()},
            'chat': {'connected': c.has('slack'), 'channels': [n for _, n, _ in channels()], 'direct messages': bool(me())},
            'mail, teams and meetings': {'connected': c.has('microsoft'), 'terms': mail_terms(), 'client domains': list(_src('client_domains') or [])},
            'shared files': {'connected': c.has('microsoft'), 'folder': str(_src('shared_folder') or ''), 'read in full': list(read_tops())}}


def log(m):
    paths.use_tools()
    import kb_build as K
    K.log('live: ' + m)


def fetch(prompt, tools, turns=30, expect=None):
    """A fetch run, again (up to 3 tries, 30 s apart) when it made fewer calls than expected: a connector that is still
    connecting at the start leaves the run without its tool (seen 23 Oct). Returns (results, cost of all tries, session id)."""
    total = 0.0
    for k in range(3):
        res, cost, sid = _fetch_once(prompt, tools, turns)
        total += cost
        ok = [(i, r) for i, r in res if 'requested permissions to use' not in r[:200]]
        if expect is None or len(ok) >= expect:
            return ok, total, sid
        log(f'fetch run made {len(ok)} of {expect} calls (try {k + 1}); again in 30 s')
        time.sleep(30)
    return ok, total, sid


def _fetch_once(prompt, tools, turns):
    """One fetch run. Returns (tool result texts in order, cost in $, session id)."""
    paths.use_tools()
    import kb_build as K
    K.wait_for_room()                                    # the week line and the 5-hour window, like every KB writer
    os.makedirs(RUN, exist_ok=True)
    cmd = [CLAUDE, '-p', prompt, '--model', HAIKU, '--output-format', 'json', '--max-turns', str(turns),
           '--allowedTools', ' '.join(tools), '--tools', '', '--settings', json.dumps({'alwaysThinkingEnabled': False}),
           '--system-prompt', 'You fetch records with the read tools you are given, exactly as asked. Never summarise. '
                              'When every page is fetched, reply only DONE and the number of calls.']
    r = subprocess.run(cmd, cwd=RUN, capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL, env=ENV)
    try:
        d = json.loads(r.stdout or '{}')
    except ValueError:
        d = {}
    sid = d.get('session_id', '')
    return results(sid, pairs=True), float(d.get('total_cost_usd') or 0), sid


def results(sid, pairs=False):
    """The tool results of a fetch run, from its transcript, in order. pairs=True: [(the call's input, result)], each result
    matched to its own call by id (23 Oct: pairing by position mislabelled every window after one failed call)."""
    calls, out = {}, []
    for f in glob.glob(os.path.join(PROJ, sid + '.jsonl')):
        for line in open(f, errors='replace'):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            c0 = (rec.get('message') or {}).get('content')
            for x in c0 if isinstance(c0, list) else []:
                if not isinstance(x, dict):
                    continue
                if x.get('type') == 'tool_use':
                    calls[x.get('id')] = x.get('input') or {}
                elif x.get('type') == 'tool_result':
                    c = x.get('content')
                    t = c if isinstance(c, str) else '\n'.join(y.get('text', '') for y in c or [] if isinstance(y, dict))
                    m = re.search(r'(?:Full output saved to:|Output has been saved to) (\S+?\.(?:txt|json))', t)   # a large result is kept in a file
                    if m and os.path.exists(m.group(1)):
                        t = open(m.group(1), errors='replace').read()
                    out.append((calls.get(x.get('tool_use_id'), {}), t))
    return out if pairs else [t for _, t in out]


def jira_unit(page, size=20):
    """The project's tickets [page*size, page*size+size) by key, with every field that matters and all comments."""
    jql = f'project = {KEY} ORDER BY key ASC'
    prompt = (f"Call {JIRA} with cloudId {cloud()}, jql \"{jql}\", maxResults {size}, responseContentFormat \"markdown\", fields "
              f"[\"summary\",\"description\",\"status\",\"issuetype\",\"priority\",\"labels\",\"components\",\"assignee\",\"reporter\","
              f"\"created\",\"updated\",\"resolution\",\"parent\",\"issuelinks\",\"fixVersions\",\"comment\"]. "
              + (f"Then call it again with the nextPageToken from each answer, {page} more times, so the last answer is page "
                 f"{page + 1}. " if page else '') + "Reply DONE.")
    res, cost, sid = fetch(prompt, [JIRA], turns=page + 6)
    body = res[-1][1] if res else ''
    return body, cost, sid, len(res)


def jira_ranges(lo, hi, step=20):
    """One run fetching the project's tickets by key range [lo, hi) in steps; returns [(range, text)], cost."""
    rs = [(k, min(k + step, hi)) for k in range(lo, hi, step)]
    calls = '\n'.join(f'{i + 1}. jql "project = {KEY} AND key >= {KEY}-{a} AND key < {KEY}-{b} ORDER BY key ASC"' for i, (a, b) in enumerate(rs))
    prompt = (f"Call {JIRA} once for each JQL below, in order, each with cloudId {cloud()}, maxResults 100, responseContentFormat "
              f"\"markdown\", fields [\"summary\",\"description\",\"status\",\"issuetype\",\"priority\",\"labels\",\"components\","
              f"\"assignee\",\"reporter\",\"created\",\"updated\",\"resolution\",\"parent\",\"issuelinks\",\"fixVersions\",\"comment\"]. "
              f"Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
    res, cost, sid = fetch(prompt, [JIRA], turns=len(rs) + 4)
    return list(zip(rs, [r for _, r in res])), cost, sid


def _windows(start, end, days=4):
    d = start
    while d < end:
        yield d, min(d + dt.timedelta(days=days), end)
        d += dt.timedelta(days=days)


def jira_text(raw):
    """Readable tickets from one search answer: the fields that matter, the description and every comment."""
    try:
        nodes = json.loads(raw)['issues']['nodes']
    except (ValueError, KeyError, TypeError):
        return '', []
    out, keys = [], []
    for n in nodes:
        f, k = n.get('fields') or {}, n.get('key')
        keys.append(k)
        nm = lambda x: (x or {}).get('displayName') or (x or {}).get('name') or ''
        links = '; '.join(f"{(l.get('type') or {}).get('outward' if l.get('outwardIssue') else 'inward', '')} "
                          f"{(l.get('outwardIssue') or l.get('inwardIssue') or {}).get('key', '')}" for l in f.get('issuelinks') or [])
        head = [f"## {k}: {f.get('summary', '')}",
                f"type {nm(f.get('issuetype'))} · status {nm(f.get('status'))} · priority {nm(f.get('priority'))} · assignee {nm(f.get('assignee'))} · "
                f"reporter {nm(f.get('reporter'))} · created {str(f.get('created'))[:10]} · updated {str(f.get('updated'))[:10]}"
                + (f" · resolution {nm(f.get('resolution'))}" if f.get('resolution') else '')]
        extra = [('parent', (f.get('parent') or {}).get('key')), ('labels', ', '.join(f.get('labels') or [])),
                 ('components', ', '.join(nm(c) for c in f.get('components') or [])), ('fix versions', ', '.join(nm(v) for v in f.get('fixVersions') or [])),
                 ('links', links)]
        head += [f'{a}: {b}' for a, b in extra if b]
        desc = f.get('description')
        head += ['', str(desc if isinstance(desc, str) else json.dumps(desc, ensure_ascii=False) if desc else '(no description)').strip()]
        for c in ((f.get('comment') or {}).get('comments') or []):
            body = c.get('body')
            head += ['', f"**Comment by {nm(c.get('author'))}, {str(c.get('created'))[:16]}:**",
                     str(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).strip()]
        out.append('\n'.join(head))
    return '\n\n'.join(out), keys


def jira_days(start, end):
    """A capped window again, one day per call (a day of more than 100 tickets is split by hour)."""
    days = [(a, b) for a, b in _windows(start, end, days=1)]
    calls = '\n'.join(f'{i + 1}. jql "project = {KEY} AND created >= {a} AND created < {b} ORDER BY created ASC"' for i, (a, b) in enumerate(days))
    prompt = (f"Call {JIRA} once for each JQL below, in order, each with cloudId {cloud()}, maxResults 100, responseContentFormat "
              f"\"markdown\", fields [\"summary\",\"description\",\"status\",\"issuetype\",\"priority\",\"labels\",\"components\","
              f"\"assignee\",\"reporter\",\"created\",\"updated\",\"resolution\",\"parent\",\"issuelinks\",\"fixVersions\",\"comment\"]. "
              f"Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
    res, cost, sid = fetch(prompt, [JIRA], turns=len(days) + 6, expect=len(days))
    keys, capped = set(), []
    parts = []
    for (a, b), (_, raw) in zip(days, res):
        text, ks = jira_text(raw)
        if len(ks) >= 100:
            capped.append(str(a))
        if text:
            parts.append(text); keys |= set(ks)
    if parts:
        write_unit(f'l_jira_{_jn()}{start}', f'Jira {KEY} tickets created {start} to {end - dt.timedelta(days=1)} | live source, read {dt.date.today()}',
                   '\n\n'.join(parts))
    return len(keys), cost, capped


FIELDS = ('[\"summary\",\"description\",\"status\",\"issuetype\",\"priority\",\"labels\",\"components\",\"assignee\",\"reporter\",'
          '\"created\",\"updated\",\"resolution\",\"parent\",\"issuelinks\",\"fixVersions\",\"comment\"]')


def jira_window(a, b, depth=0, force=None):
    """The project's tickets created in [a, b) (datetimes), split in half while a call errors (a 300 s timeout on a bulk-created day,
    23 Oct) or reaches the 100 cap. Returns ({key: ticket text}, cost)."""
    q = lambda x: x.strftime('%Y-%m-%d %H:%M')
    prompt = (f"Call {JIRA} once with cloudId {cloud()}, jql \"project = {KEY} AND created >= '{q(a)}' AND created < '{q(b)}' ORDER BY "
              f"created ASC\", maxResults 100, responseContentFormat \"markdown\", fields {FIELDS}. Do not read or summarise the "
              f"answer. Then reply DONE.")
    res, cost, _ = fetch(prompt, [JIRA], turns=5, expect=1)
    raw = res[0][1] if res else ''
    text, ks = jira_text(raw)
    bad = not raw.lstrip().startswith('{')
    if (bad or len(ks) >= 100 or (force and len(ks) < force)) and depth < 6 and (b - a) > dt.timedelta(minutes=30):
        m = a + (b - a) / 2
        x, c1 = jira_window(a, m, depth + 1)
        y, c2 = jira_window(m, b, depth + 1)
        return {**x, **y}, cost + c1 + c2
    if bad:
        log(f'jira window {q(a)} to {q(b)} failed: {raw[:120]}')
    return dict(zip(ks, text.split('\n\n## ')) if ks else {}), cost


def jira_keyed(a, b):
    """Every ticket of a window created in bulk (190 within minutes on 26-30 Jul): paged by key in code. Returns ({key: text}, cost)."""
    got, cost, last = {}, 0.0, 0
    for _ in range(20):
        jql = f"project = {KEY} AND created >= {a} AND created < {b}" + (f" AND key > {KEY}-{last}" if last else '') + " ORDER BY key ASC"
        prompt = (f"Call {JIRA} once with cloudId {cloud()}, jql \"{jql}\", maxResults 100, responseContentFormat \"markdown\", "
                  f"fields {FIELDS}. Do not read or summarise the answer. Then reply DONE.")
        res, c, _ = fetch(prompt, [JIRA], turns=5, expect=1)
        cost += c
        text, ks = jira_text(res[0][1] if res else '')
        if not ks:
            break
        got.update(zip(ks, text.split('\n\n## ')))
        last = max(int(k.split('-')[1]) for k in ks)
        if len(ks) < 50:
            break
    return got, cost


def jira_verify(start=None):
    """Jira's own count per 4-day window against the tickets in that window's unit; a short window is fetched again,
    split (the search can return fewer than asked without saying so). Returns (expected, have, cost)."""
    start = start or since()
    ws = list(_windows(start, dt.date.today() + dt.timedelta(days=1)))
    calls = '\n'.join(f'{i + 1}. jql "project = {KEY} AND created >= {a} AND created < {b}"' for i, (a, b) in enumerate(ws))
    prompt = (f"Call {JIRA} once for each JQL below, in order, each with cloudId {cloud()} and searchResultMode \"count\". Do not "
              f"summarise.\n{calls}\nThen reply DONE.")
    res, cost, _ = fetch(prompt, [JIRA], turns=len(ws) + 6, expect=len(ws))
    want, have = 0, 0
    for inp, raw in res:
        m = re.search(r'created >= (\S+) AND created < (\S+)', str(inp.get('jql', '')))
        n = re.search(r'"totalCount":\s*(\d+)', raw)
        if not (m and n):
            continue
        a = dt.date.fromisoformat(m.group(1)); exp = int(n.group(1)); want += exp
        f = os.path.join(EXTRA, f'l_jira_{_jn()}{a}.txt')
        got = len(re.findall(r'^## ' + re.escape(KEY) + r'-\d+:', open(f).read(), re.M)) if os.path.exists(f) else 0
        if got < exp:
            log(f'jira window {a}: {got} of {exp} tickets; fetching again, split')
            tk, c = jira_keyed(a, a + dt.timedelta(days=4))                  # by key: works for bulk-created windows too
            cost += c
            if len(tk) > got:
                write_unit(f'l_jira_{_jn()}{a}', f'Jira {KEY} tickets created {a} to {a + dt.timedelta(days=3)} | live source, read {dt.date.today()}',
                           '\n\n'.join(t if t.startswith('## ') else '## ' + t for t in tk.values()))
                got = len(tk)
        have += min(got, exp)
    return want, have, cost


def jira_all(start=None):
    """Every ticket of the project, created in 4-day windows, in one run; one unit per window. Returns (units, tickets, cost)."""
    start = start or since()
    ws = list(_windows(start, dt.date.today() + dt.timedelta(days=1)))
    calls = '\n'.join(f'{i + 1}. jql "project = {KEY} AND created >= {a} AND created < {b} ORDER BY created ASC"' for i, (a, b) in enumerate(ws))
    prompt = (f"Call {JIRA} once for each JQL below, in order, each with cloudId {cloud()}, maxResults 100, responseContentFormat "
              f"\"markdown\", fields [\"summary\",\"description\",\"status\",\"issuetype\",\"priority\",\"labels\",\"components\","
              f"\"assignee\",\"reporter\",\"created\",\"updated\",\"resolution\",\"parent\",\"issuelinks\",\"fixVersions\",\"comment\"]. "
              f"Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
    res, cost, sid = fetch(prompt, [JIRA], turns=len(ws) + 6, expect=len(ws))
    n_units, keys, full = 0, set(), []
    for inp, raw in res:
        m = re.search(r'created >= (\S+) AND created < (\S+)', str(inp.get('jql', '')))
        if not m:
            continue
        a, b = dt.date.fromisoformat(m.group(1).strip("'\"")), dt.date.fromisoformat(m.group(2).strip("'\""))
        text, ks = jira_text(raw)
        if not raw.lstrip().startswith('{'):
            full.append(str(a)); continue                # an error answer: fetched again by window
        if len(ks) >= 100:
            full.append(str(a)); continue                # at the 100 cap: fetched again, split
        if text:
            write_unit(f'l_jira_{_jn()}{a}', f'Jira {KEY} tickets created {a} to {b - dt.timedelta(days=1)} | live source, read {dt.date.today()}', text)
            n_units += 1; keys |= set(ks)
    for a in list(full):                                 # windows that errored or reached the cap: again, split as needed
        a = dt.date.fromisoformat(a)
        got, c = jira_window(dt.datetime.combine(a, dt.time()), dt.datetime.combine(a + dt.timedelta(days=4), dt.time()))
        cost += c
        if got:
            write_unit(f'l_jira_{_jn()}{a}', f'Jira {KEY} tickets created {a} to {a + dt.timedelta(days=3)} | live source, read {dt.date.today()}',
                       '\n\n'.join(t if t.startswith('## ') else '## ' + t for t in got.values()))
            n_units += 1; keys |= set(got); full.remove(str(a))
    _set(_jk(), {'at': dt.datetime.now().isoformat(timespec='seconds'), 'units': n_units, 'tickets': len(keys),
                  'calls': len(res), 'windows': len(ws), 'capped': full, 'cost': round(cost, 3), 'session': sid})
    return n_units, len(keys), cost, full


CQL, CPAGE = 'mcp__claude_ai_Atlassian_Rovo__searchConfluenceUsingCql', 'mcp__claude_ai_Atlassian_Rovo__getConfluencePage'
CFOOT, CINLINE = 'mcp__claude_ai_Atlassian_Rovo__getConfluencePageFooterComments', 'mcp__claude_ai_Atlassian_Rovo__getConfluencePageInlineComments'


def confluence_list(start=None):
    """Every page and blog post of the space, by month of creation (one run). Returns [{id, title, created, modified, author}]."""
    start = start or since()
    months, d = [], start
    while d <= dt.date.today():
        n = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        months.append((d, n)); d = n
    calls = '\n'.join(f'{i + 1}. cql "space = {SPACE} AND type in (page, blogpost) AND created >= \\"{a}\\" AND created < \\"{b}\\""'
                      for i, (a, b) in enumerate(months))
    prompt = (f"Call {CQL} once for each CQL below, in order, each with cloudId {cloud()} and limit 250. Do not read or summarise "
              f"the answers.\n{calls}\nThen reply DONE.")
    res, cost, sid = fetch(prompt, [CQL], turns=len(months) + 6, expect=len(months))
    pages = {}
    for _, raw in res:
        try:
            nodes = (json.loads(raw).get('content') or {}).get('nodes') or json.loads(raw).get('results') or []
        except (ValueError, AttributeError):
            continue
        for n in nodes:
            if n.get('id'):
                pages[n['id']] = {'id': n['id'], 'space': SPACE, 'title': n.get('title', ''), 'type': n.get('type'), 'modified': n.get('lastModified', ''),
                                  'author': (n.get('author') or {}).get('displayName', ''), 'url': n.get('webUrl', '')}
    capped = [str(inp.get('cql', ''))[-40:] for inp, raw in res if raw.count('"id"') >= 250]
    return list(pages.values()), cost, capped


def _msg_text(raw):
    """A full Teams message read: its body as plain text (24 Oct: the raw JSON answer buried the text in HTML and ids)."""
    try:
        d = json.loads(raw)
        c = (d.get('body') or {}).get('content') if isinstance(d, dict) else None
        if c:
            import html
            c = html.unescape(re.sub(r'<[^>]+>', '', re.sub(r'(?i)<br\s*/?>|</p>|</li>|</div>|</tr>', '\n', c)))
            return re.sub(r'\n{3,}', '\n\n', c).strip()
    except ValueError:
        pass
    return _strip_ctx(raw)


def _strip_ctx(raw):
    """A connector answer without its trailing request metadata ("context": ids, feature flags)."""
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            d.pop('context', None)
            return json.dumps(d, ensure_ascii=False, indent=0)
    except ValueError:
        pass
    return raw


def confluence_pages(batch=10):
    """Every listed page: its body (markdown), footer comments and inline comments (open and resolved), with replies.
    One unit per page (l_conf_<id>); a page whose unit exists and is newer than its last change is skipped."""
    st = _state(); pages = st.get('confluence_pages') or []
    todo = [p for p in pages if not os.path.exists(os.path.join(EXTRA, f"l_conf_{p['id']}.txt"))]
    total, done = 0.0, 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        calls = []
        for p in part:
            calls += [f"{CPAGE} pageId {p['id']} contentFormat \"markdown\"",
                      f"{CFOOT} pageId {p['id']} contentFormat \"markdown\" includeReplies true limit 100",
                      f"{CINLINE} pageId {p['id']} contentFormat \"markdown\" includeReplies true limit 100 resolutionStatus \"open\"",
                      f"{CINLINE} pageId {p['id']} contentFormat \"markdown\" includeReplies true limit 100 resolutionStatus \"resolved\""]
        prompt = (f"Make each call below, in order, each with cloudId {cloud()}. Do not read or summarise the answers.\n"
                  + '\n'.join(f'{k + 1}. {c}' for k, c in enumerate(calls)) + '\nThen reply DONE.')
        res, cost, _ = fetch(prompt, [CPAGE, CFOOT, CINLINE], turns=len(calls) + 6, expect=len(calls))
        total += cost
        by = {}
        for inp, raw in res:
            pid = str(inp.get('pageId', ''))
            kind = 'page' if 'contentFormat' in inp and 'includeReplies' not in inp else \
                   ('inline ' + inp.get('resolutionStatus', '')) if 'resolutionStatus' in inp else 'footer'
            by.setdefault(pid, {})[kind] = raw
        for p in part:
            got = by.get(p['id'], {})
            if 'page' not in got or not got['page'].lstrip().startswith('{'):
                log(f"confluence page {p['id']} ({p['title'][:50]}) not fetched; next run")
                continue
            try:
                body = json.loads(got['page']).get('body') or ''
                body = body.get('storage', {}).get('value') if isinstance(body, dict) else body
            except ValueError:
                body = ''
            text = [f"# {p['title']}", f"Confluence {p.get('space', '')} page {p['id']} · author {p['author']} · last modified {p['modified']} · {p['url']}",
                    '', str(body or _strip_ctx(got['page']))]
            for k in ('footer', 'inline open', 'inline resolved'):
                if got.get(k) and '"results":[]' not in got[k].replace(' ', '') and '"nodes":[]' not in got[k].replace(' ', ''):
                    text += ['', f'## {k.capitalize()} comments', _strip_ctx(got[k])]
            write_unit(f"l_conf_{p['id']}", f"Confluence page: {p['title']} | {p.get('space', '')} {p['id']} | last modified {p['modified']} | live source, read {dt.date.today()}",
                       '\n'.join(text))
            done += 1
        log(f'confluence: {done}/{len(todo)} pages, ${total:.2f}')
    return done, len(todo), total


SREAD, STHREAD = 'mcp__claude_ai_Slack__slack_read_channel', 'mcp__claude_ai_Slack__slack_read_thread'


def _ts(d):
    return str(int(dt.datetime.combine(d, dt.time()).timestamp()))


def _msgs(raw):
    """The message text of a Slack answer, and how many messages it holds."""
    try:
        t = json.loads(raw).get('messages', raw)
    except (ValueError, AttributeError):
        t = raw
    t = t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
    return t, t.count('=== Message from')


def slack_channel(cid, name, start, batch=30):
    """One channel, week by week (a week of 100 messages is split by day), then every thread's replies; one unit per
    month (l_slack_<name>_<YYYY-MM>). Returns (messages, threads, cost)."""
    weeks = list(_windows(dt.date.fromisoformat(start), dt.date.today() + dt.timedelta(days=1), days=7))
    texts, cost = {}, 0.0
    def run(wins):
        nonlocal cost
        for i in range(0, len(wins), batch):
            part = wins[i:i + batch]
            calls = '\n'.join(f'{k + 1}. channel_id "{cid}", oldest "{_ts(a)}", latest "{_ts(b)}"' for k, (a, b) in enumerate(part))
            prompt = (f"Call {SREAD} once for each line below, in order, each with limit 100 and response_format \"detailed\". "
                      f"Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
            res, c, _ = fetch(prompt, [SREAD], turns=len(part) + 6, expect=len(part))
            cost += c
            for inp, raw in res:
                a = dt.date.fromtimestamp(int(inp.get('oldest', 0))); b = dt.date.fromtimestamp(int(inp.get('latest', 0)))
                t, n = _msgs(raw)
                if n >= 100 and (b - a).days > 1:
                    run(list(_windows(a, b, days=1)))      # a busy week: by day
                else:
                    texts[(a, b)] = t
    run(weeks)
    threads = sorted({m for t in texts.values() for m in re.findall(r'Message TS: (\d+\.\d+)\n(?:(?!=== Message).)*?Thread: \d+ replies', t, re.S)})
    replies = {}
    for i in range(0, len(threads), batch):
        part = threads[i:i + batch]
        calls = '\n'.join(f'{k + 1}. channel_id "{cid}", message_ts "{ts}"' for k, ts in enumerate(part))
        prompt = (f"Call {STHREAD} once for each line below, in order, each with limit 1000 and response_format \"detailed\". "
                  f"Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
        res, c, _ = fetch(prompt, [STHREAD], turns=len(part) + 6, expect=len(part))
        cost += c
        for inp, raw in res:
            replies[inp.get('message_ts')] = _msgs(raw)[0]
    months = {}
    for (a, b), t in sorted(texts.items()):
        blocks = [x for x in re.split(r'(?==== Message from)', t) if x.startswith('=== Message from')]
        for blk in reversed(blocks):                     # the answer is newest first; the unit reads oldest first
            m = re.search(r' at (\d{4}-\d{2})-\d{2} ', blk)
            ts = (re.search(r'Message TS: (\d+\.\d+)', blk) or [None, None])[1]
            months.setdefault(m.group(1) if m else str(a)[:7], []).append(blk.strip() + (('\n--- thread replies ---\n' + replies[ts].strip()) if ts in replies else ''))
    n = 0
    for mon, blks in months.items():
        write_unit(f'l_slack_{name}_{mon}', f'Slack #{name} ({cid}), {mon}: {len(blks)} messages with their thread replies | live source, read {dt.date.today()}',
                   '\n\n'.join(blks))
        n += len(blks)
    return n, len(replies), cost


HELD = os.path.join(HUB, 'state', 'kb_live_held')     # a unit the personal screen could not judge waits here, outside the brain


def _people(min_msgs=3):
    """{user id: (name, messages)} of everyone who wrote in the channels read, most active first (the owner excluded)."""
    c, mine = {}, me()
    for f in glob.glob(os.path.join(EXTRA, 'l_slack_*.txt')):
        if '_dm_' in f:
            continue
        for name, uid in re.findall(r'=== Message from ([^<]+?) <[^>]*> \((U[A-Z0-9]+)\)', open(f, errors='replace').read()):
            if uid != mine:
                n0, k = c.get(uid, (name.strip(), 0)); c[uid] = (n0, k + 1)
    return dict(sorted(((u, v) for u, v in c.items() if v[1] >= min_msgs), key=lambda x: -x[1][1]))


def screened_write(name, header, body):
    """Write a unit only after the personal screen took its personal lines out (DMs, mail, chats); a unit it cannot
    judge is held outside the brain (state/kb_live_held) for the next run."""
    paths.use_tools()
    import personal_deep_check as P
    try:
        clean = P.filter_text(body)
    except Exception as e:
        os.makedirs(HELD, exist_ok=True)
        open(os.path.join(HELD, name + '.txt'), 'w').write(header + '\n\n' + body)
        log(f'{name}: held back, the screen could not judge it ({str(e)[:80]})')
        return False
    write_unit(name, header, clean)
    return True


def slack_dms(start=None, batch=30):
    """Their work DMs with everyone active in the channels read, week by week, with thread replies; one screened unit per
    person and month (l_slack_dm_<name>_<YYYY-MM>). Returns {name: messages}, cost."""
    start = start or since()
    out, cost = {}, 0.0
    for uid, (who, _) in _people().items():
        slug = re.sub(r'[^a-z0-9]+', '-', who.lower()).strip('-')
        texts = {}
        weeks = list(_windows(start, dt.date.today() + dt.timedelta(days=1), days=7))
        for i in range(0, len(weeks), batch):
            part = weeks[i:i + batch]
            calls = '\n'.join(f'{k + 1}. channel_id "{uid}", oldest "{_ts(a)}", latest "{_ts(b)}"' for k, (a, b) in enumerate(part))
            prompt = (f"Call {SREAD} once for each line below, in order, each with limit 100 and response_format \"detailed\". "
                      f"Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
            res, c, _ = fetch(prompt, [SREAD], turns=len(part) + 6, expect=len(part))
            cost += c
            for inp, raw in res:
                t, n = _msgs(raw)
                if n:
                    texts[int(inp.get('oldest', 0))] = t
        blocks = [x.strip() for _, t in sorted(texts.items()) for x in reversed(re.split(r'(?==== Message from)', t)) if x.startswith('=== Message from')]
        months = {}
        for blk in blocks:
            m = re.search(r' at (\d{4}-\d{2})-\d{2} ', blk)
            months.setdefault(m.group(1) if m else 'undated', []).append(blk)
        for mon, blks in months.items():
            screened_write(f'l_slack_dm_{slug}_{mon}', f'Slack DM with {who} ({uid}), {mon}: {len(blks)} messages | live source, read {dt.date.today()}',
                           '\n\n'.join(blks))
        out[who] = len(blocks)
        log(f'slack DM {who}: {len(blocks)} messages')
    return out, cost


MSEARCH, MREAD = 'mcp__claude_ai_Microsoft_365__outlook_email_search', 'mcp__claude_ai_Microsoft_365__read_resource'
PERSONAL_SUBJECT = re.compile(r'\b(visas?|b1/b2|passport|immigration|consulate|embassy|leave|vacation|holiday|medical|health|hospital|'
                              r'insurance|payroll|salary|payslip|compensation|bonus|tax|form 16|reimburse\w*|appraisal|offer letter|'
                              r'resignation|relocation|family|wedding|personal)\b', re.I)   # 10 Nov: CLAUDE.md, nothing personal
AUTOMATED = re.compile(r'(jira@|confluence@|no-?reply|noreply|notifications?@|calendar|mailer-daemon|@atlassian\.net|teams\.mail)', re.I)


def _throttled(raw):
    """Seconds to wait when Microsoft Graph refused the call for rate limits (23 Oct: a mail and a Teams run together drew
    429s, and a refused week would have looked like a week without mail), else 0."""
    if 'RATE_LIMITED' in raw[:400] or 'TooManyRequests' in raw[:600]:
        return int((re.search(r'retryAfterSeconds"?:\s*(\d+)', raw) or [0, 60])[1]) + 5
    return 0


def mail_list(start=None, batch=20):
    """Work mail, week by week per search term (a full page is fetched again at the next offset). Returns {id: meta}, cost."""
    start = start or since()
    weeks = list(_windows(start, dt.date.today() + dt.timedelta(days=1), days=7))
    todo = [(t, a, b, 0) for a, b in weeks for t in mail_terms()]
    found, cost = {}, 0.0
    while todo:
        part, todo = todo[:batch], todo[batch:]
        calls = '\n'.join(f'{k + 1}. query "{t}", afterDateTime "{a}", beforeDateTime "{b}", limit 25, offset {o}' for k, (t, a, b, o) in enumerate(part))
        prompt = (f"Call {MSEARCH} once for each line below, in order. Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
        res, c, _ = fetch(prompt, [MSEARCH], turns=len(part) + 6, expect=len(part))
        cost += c
        wait = 0
        for inp, raw in res:
            w = _throttled(raw)
            if w:                                        # refused for rate limits: asked again after the wait
                todo.append((inp.get('query'), inp.get('afterDateTime'), inp.get('beforeDateTime'), int(inp.get('offset') or 0)))
                wait = max(wait, w); continue
            for m in re.finditer(r'"uri":"(mail:///messages/[^"]+)".*?"subject":"((?:[^"\\]|\\.)*)","sender":"([^"]*)".*?"receivedDateTime":"([^"]+)"', raw, re.S):
                uri, subj, sender, when = m.groups()
                if AUTOMATED.search(sender):
                    continue
                found.setdefault(uri, {'uri': uri, 'subject': subj, 'sender': sender, 'when': when})
            if '"nextOffset"' in raw:
                o = int((re.search(r'"nextOffset":\s*(\d+)', raw) or [0, 0])[1])
                if o and o < 500:
                    todo.append((inp.get('query'), inp.get('afterDateTime'), inp.get('beforeDateTime'), o))
        if wait:
            log(f'mail: Graph rate limit, waiting {wait}s'); time.sleep(wait)
    return found, cost


def mail_read(found, batch=25):
    """Every listed message in full; one screened unit per month and subject thread (l_mail_<YYYY-MM>_<n>)."""
    st = _state(); done = set(st.get('mail_read') or [])
    todo = [m for u, m in found.items() if u not in done]
    bodies, cost = {}, 0.0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        calls = '\n'.join(f'{k + 1}. uri "{m["uri"]}"' for k, m in enumerate(part))
        prompt = (f"Call {MREAD} once for each line below, in order. Do not read or summarise the answers.\n{calls}\nThen reply DONE.")
        res, c, _ = fetch(prompt, [MREAD], turns=len(part) + 6, expect=len(part))
        cost += c
        wait = 0
        for inp, raw in res:
            w = _throttled(raw)
            if w:
                wait = max(wait, w); continue            # not marked read: the next run asks again
            bodies[inp.get('uri')] = _msg_text(raw)       # 10 Nov: plain text, not the raw JSON+HTML (the screen read the HTML badly)
        if wait:
            log(f'mail: Graph rate limit, waiting {wait}s'); time.sleep(wait)
    threads = {}
    for m in todo:
        if m['uri'] in bodies:
            key = (m['when'][:7], re.sub(r'^(re|fw|fwd)\s*:\s*', '', m['subject'], flags=re.I).strip().lower()[:80])
            threads.setdefault(key, []).append(m)
    n = 0
    for k, ((mon, subj), ms) in enumerate(sorted(threads.items())):
        ms.sort(key=lambda m: m['when'])
        body = '\n\n'.join(f"=== {m['when'][:16]} · from {m['sender']} · {m['subject']} ===\n{bodies[m['uri']]}" for m in ms)
        if PERSONAL_SUBJECT.search(ms[0]['subject']) or PERSONAL_SUBJECT.search(subj):   # 10 Nov: a visa thread passed the
            log(f'mail: thread {mon}_{k:03d} kept out, personal subject'); continue          # line screen (its header is never screened)
        if screened_write(f'l_mail_{mon}_{k:03d}', f'Mail thread "{ms[0]["subject"]}", {mon}: {len(ms)} messages | live source, read {dt.date.today()}', body):
            n += len(ms)
    _set('mail_read', sorted(done | set(bodies)))
    return n, len(threads), cost


TSEARCH = 'mcp__claude_ai_Microsoft_365__chat_message_search'


def _objects(raw):
    """Every JSON object in an answer that holds several one after another."""
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(raw):
        j = raw.find('{', i)
        if j < 0:
            break
        try:
            o, k = dec.raw_decode(raw, j)
            out.append(o); i = k
        except ValueError:
            i = j + 1
    return out


def _months(start):
    d = start.replace(day=1)
    while d <= dt.date.today():
        n = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d, n
        d = n


def teams_all(start=None, batch=20):
    """Teams, completely (24 Oct audit: the weekly reader kept 48 messages while the same search returns more): month by
    month, and a page that comes back full (25) is followed by the next one, whatever the answer says about more."""
    start = start or since()
    todo = [(a, b, 0) for a, b in _months(start)]
    msgs, cost = {}, 0.0
    while todo:
        part, todo = todo[:batch], todo[batch:]
        calls = '\n'.join(f'{k + 1}. query "{teams_q()}", afterDateTime "{a}", beforeDateTime "{b}", limit 25, offset {o}' for k, (a, b, o) in enumerate(part))
        res, c, _ = fetch(f"Call {TSEARCH} once for each line below, in order. Do not read or summarise the answers.\n{calls}\nThen reply DONE.",
                          [TSEARCH], turns=len(part) + 6, expect=len(part))
        cost += c; wait = 0
        for inp, raw in res:
            a, b, o = inp.get('afterDateTime'), inp.get('beforeDateTime'), int(inp.get('offset') or 0)
            w = _throttled(raw)
            if w:
                todo.append((a, b, o)); wait = max(wait, w); continue
            got = [x for x in _objects(raw) if str(x.get('uri') or '').startswith('teams:///')]
            for x in got:
                msgs.setdefault(x['uri'], x)
            if len(got) >= 25 and o < 975:
                todo.append((a, b, o + 25))
        if wait:
            log(f'teams: Graph rate limit, waiting {wait}s'); time.sleep(wait)
        log(f'teams: {len(msgs)} messages so far, {len(todo)} pages left')
    return msgs, cost


def teams(start=None, batch=20, msgs=None):
    """Work Teams messages (chats, meeting chats, channels), week by week; a full page is asked again at the next offset;
    a message whose preview was cut is read in full. One screened unit per chat or channel and month (l_teams_...)."""
    start = start or since()
    msgs, cost = teams_all(start, batch) if msgs is None else (msgs, 0.0)
    todo = []
    while todo:
        part, todo = todo[:batch], todo[batch:]
        calls = '\n'.join(f'{k + 1}. query "{teams_q()}", afterDateTime "{a}", beforeDateTime "{b}", limit 25, offset {o}' for k, (a, b, o) in enumerate(part))
        res, c, _ = fetch(f"Call {TSEARCH} once for each line below, in order. Do not read or summarise the answers.\n{calls}\nThen reply DONE.",
                          [TSEARCH], turns=len(part) + 6, expect=len(part))
        cost += c; wait = 0
        for inp, raw in res:
            w = _throttled(raw)
            if w:
                todo.append((inp.get('afterDateTime'), inp.get('beforeDateTime'), int(inp.get('offset') or 0))); wait = max(wait, w); continue
            for o in _objects(raw):
                if o.get('nextOffset') and int(o['nextOffset']) < 500:
                    todo.append((inp.get('afterDateTime'), inp.get('beforeDateTime'), int(o['nextOffset'])))
                u = o.get('uri') or ''
                if u.startswith('teams:///'):
                    msgs.setdefault(u, o)
        if wait:
            log(f'teams: Graph rate limit, waiting {wait}s'); time.sleep(wait)
    # 24 Oct (audit): a search hit is a snippet; '...' anywhere in it (not only at the end) means text is missing
    cut = [u for u, o in msgs.items() if re.search(r'\.\.\.|…', str(o.get('summary') or o.get('preview') or o.get('body') or ''))]
    full = {}
    for i in range(0, len(cut), batch):
        part = cut[i:i + batch]
        res, c, _ = fetch(f"Call {MREAD} once for each line below, in order. Do not read or summarise the answers.\n"
                          + '\n'.join(f'{k + 1}. uri "{u}"' for k, u in enumerate(part)) + '\nThen reply DONE.', [MREAD], turns=len(part) + 6, expect=len(part))
        cost += c
        for inp, raw in res:
            if not _throttled(raw):
                full[inp.get('uri')] = _msg_text(raw)
    groups = {}
    for u, o in msgs.items():
        place = re.match(r'teams:///(chats/[^/]+|teams/[^/]+/channels/[^/]+)', u)
        when = str(o.get('createdDateTime') or o.get('sentDateTime') or o.get('receivedDateTime') or '')
        who = o.get('senderName') or o.get('from') or o.get('sender') or ''
        if isinstance(who, dict):                        # a sender object, not a name
            who = (who.get('user') or who.get('emailAddress') or who).get('displayName') or (who.get('emailAddress') or {}).get('name') or json.dumps(who)[:80]
        where = o.get('chatTopic') or o.get('topic') or o.get('channelName') or (place.group(1) if place else 'teams')
        text = full.get(u) or o.get('body') or o.get('summary') or o.get('preview') or json.dumps(o, ensure_ascii=False)
        groups.setdefault((place.group(1) if place else 'teams', when[:7]), {'where': where, 'items': []})['items'].append((when, who, text))
    n = 0
    for (place, mon), g in sorted(groups.items()):
        g['items'].sort(key=lambda x: str(x[0]))
        sl = re.sub(r'[^a-z0-9]+', '-', str(g['where']).lower()).strip('-')[:40] or 'chat'
        body = '\n\n'.join(f'=== {w[:16]} · {who} ===\n{t}' for w, who, t in g['items'])
        if screened_write(f'l_teams_{sl}_{mon}_{int(hashlib.md5(place.encode()).hexdigest(), 16) % 10000:04d}', f'Teams {g["where"]}, {mon}: {len(g["items"])} messages | live source, read {dt.date.today()}', body):
            n += len(g['items'])
    return n, len(groups), cost


CSEARCH = 'mcp__claude_ai_Microsoft_365__outlook_calendar_search'


def meetings(start=None, batch=20):
    """Work meetings (the subject or body names one of the mail terms, or someone from a client domain attends) and their Teams transcripts, read in full and
    screened; one unit per meeting (l_meet_<date>_<subject>). A meeting without a transcript is kept as its invite."""
    start = start or since()
    weeks = list(_windows(start, dt.date.today(), days=7))
    todo = [(a, b, 0) for a, b in weeks]
    events, cost = {}, 0.0
    while todo:
        part, todo = todo[:batch], todo[batch:]
        calls = '\n'.join(f'{k + 1}. query "*", afterDateTime "{a}", beforeDateTime "{b}", limit 25, offset {o}' for k, (a, b, o) in enumerate(part))
        res, c, _ = fetch(f"Call {CSEARCH} once for each line below, in order. Do not read or summarise the answers.\n{calls}\nThen reply DONE.",
                          [CSEARCH], turns=len(part) + 6, expect=len(part))
        cost += c; wait = 0
        for inp, raw in res:
            w = _throttled(raw)
            if w:
                todo.append((inp.get('afterDateTime'), inp.get('beforeDateTime'), int(inp.get('offset') or 0))); wait = max(wait, w); continue
            for o in _objects(raw):
                if o.get('nextOffset') and int(o['nextOffset']) < 500:
                    todo.append((inp.get('afterDateTime'), inp.get('beforeDateTime'), int(o['nextOffset'])))
                u, blob = o.get('uri') or '', json.dumps(o, ensure_ascii=False)
                if u.startswith('calendar:///') and (_names_work(str(o.get('subject', '')) + ' ' + str(o.get('bodyPreview', ''))) or _client(blob)):
                    events.setdefault(u, o)
        if wait:
            log(f'meetings: Graph rate limit, waiting {wait}s'); time.sleep(wait)
    full = {}
    todo = list(events)
    while todo:
        part, todo = todo[:batch], todo[batch:]
        res, c, _ = fetch(f"Call {MREAD} once for each line below, in order. Do not read or summarise the answers.\n"
                          + '\n'.join(f'{k + 1}. uri "{u}"' for k, u in enumerate(part)) + '\nThen reply DONE.', [MREAD], turns=len(part) + 6, expect=len(part))
        cost += c
        for inp, raw in res:
            if _throttled(raw):
                todo.append(inp.get('uri')); continue
            full[inp.get('uri')] = raw
    tr = {}
    turls = {u: (re.search(r'"meetingTranscriptUrl"\s*:\s*"([^"]+)"', r) or [None, None])[1] for u, r in full.items()}
    todo = [(u, t) for u, t in turls.items() if t]
    while todo:
        part, todo = todo[:batch], todo[batch:]
        res, c, _ = fetch(f"Call {MREAD} once for each line below, in order. Do not read or summarise the answers.\n"
                          + '\n'.join(f'{k + 1}. uri "{t}"' for k, (_, t) in enumerate(part)) + '\nThen reply DONE.', [MREAD], turns=len(part) + 6, expect=len(part))
        cost += c
        back = {t: u for u, t in part}
        for inp, raw in res:
            if _throttled(raw):
                todo.append((back.get(inp.get('uri')), inp.get('uri'))); continue
            tr[back.get(inp.get('uri'))] = _strip_ctx(raw)
    n = 0
    for u, o in events.items():
        when = str((o.get('start') or {}).get('dateTime') or o.get('start') or '')[:10]
        subj = str(o.get('subject') or 'meeting')
        sl = re.sub(r'[^a-z0-9]+', '-', subj.lower()).strip('-')[:50]
        body = (f"Meeting: {subj} · {when}\n\n=== Invite ===\n{_strip_ctx(full.get(u) or json.dumps(o, ensure_ascii=False))}"
                + (f"\n\n=== Transcript ===\n{tr[u]}" if u in tr else '\n\n(no transcript)'))
        if screened_write(f'l_meet_{when}_{sl}', f'Teams meeting "{subj}", {when}' + (' with its transcript' if u in tr else ', invite only')
                          + f' | live source, read {dt.date.today()}', body):
            n += 1
    return n, len(tr), cost


DOC_EXT = ('.docx', '.doc', '.pptx', '.ppt', '.pdf', '.md', '.txt', '.html', '.htm')
LISTED = re.compile(r'^(.+?) \((folder|file)(?:, (\d+) bytes)?\) (file:///\S+)\s*$', re.M)


def sharepoint(batch=25, catalog_depth=3, max_pages=30):
    """The shared-files folder of the work (settings: sources.shared_folder): every folder walked (the catalogue-only ones to catalog_depth),
    every file catalogued (name, place, size); documents in the folders of sources.shared_read read in full, page by page. Units: one catalogue per
    top folder (l_sp_catalog_<top>), one per document read (l_sp_doc_<path>)."""
    root = str(_src('shared_folder') or '').strip().rstrip('/')
    if not root:
        return 0, 0, 0.0                                 # no shared folder is set
    top0, READ_TOPS = root.rsplit('/', 1)[-1], read_tops()
    todo, seen = [(root, top0, 0)], set()
    files, cost = [], 0.0
    while todo:
        part, todo = todo[:batch], todo[batch:]
        res, c, _ = fetch(f"Call {MREAD} once for each line below, in order. Do not read or summarise the answers.\n"
                          + '\n'.join(f'{k + 1}. uri "{u}"' for k, (u, _, _) in enumerate(part)) + '\nThen reply DONE.', [MREAD], turns=len(part) + 6, expect=len(part))
        cost += c; wait = 0
        where = {u: (path, d) for u, path, d in part}
        for inp, raw in res:
            u = inp.get('uri'); path, d = where.get(u, ('?', 0))
            w = _throttled(raw)
            if w:
                todo.append((u, path, d)); wait = max(wait, w); continue
            for name, kind, size, cu in LISTED.findall(raw):
                p2 = f'{path}/{name}'
                if cu in seen:
                    continue
                seen.add(cu)
                top = p2.split('/')[1] if p2.count('/') >= 1 else name
                if kind == 'folder':
                    if top in READ_TOPS or d + 1 < catalog_depth:
                        todo.append((cu, p2, d + 1))
                    else:
                        files.append((p2 + '/', 'folder (not walked)', int(size or 0), cu))
                else:
                    files.append((p2, 'file', int(size or 0), cu))
        if wait:
            log(f'sharepoint: Graph rate limit, waiting {wait}s'); time.sleep(wait)
        log(f'sharepoint: {len(files)} items listed, {len(todo)} folders left, ${cost:.2f}')
    by = {}
    for p2, kind, size, cu in files:
        by.setdefault(p2.split('/')[1] if p2.count('/') > 1 else top0, []).append(f'- {p2} · {kind} · {size / 1e6:.1f} MB')
    for top, lines in by.items():
        write_unit(f"l_sp_catalog_{re.sub(r'[^a-z0-9]+', '-', top.lower()).strip('-')}", f'SharePoint catalogue: {top0}/{top}, {len(lines)} items | read {dt.date.today()}',
                   '\n'.join(sorted(lines)))
    docs = [(p2, cu) for p2, kind, size, cu in files if kind == 'file' and p2.lower().endswith(DOC_EXT) and p2.split('/')[1] in READ_TOPS
            and not os.path.exists(os.path.join(EXTRA, 'l_sp_doc_' + re.sub(r'[^a-z0-9]+', '-', p2.lower()).strip('-')[-120:] + '.txt'))]
    texts, pending = {}, [(p2, cu, 1) for p2, cu in docs]
    while pending:
        part, pending = pending[:batch], pending[batch:]
        res, c, _ = fetch(f"Call {MREAD} once for each line below, in order. Do not read or summarise the answers.\n"
                          + '\n'.join(f'{k + 1}. uri "{cu}"' + (f', startPage {pg}' if pg > 1 else '') for k, (_, cu, pg) in enumerate(part))
                          + '\nThen reply DONE.', [MREAD], turns=len(part) + 6, expect=len(part))
        cost += c
        back = {(cu, pg): p2 for p2, cu, pg in part}
        for inp, raw in res:
            pg = int(inp.get('startPage') or 1); p2 = back.get((inp.get('uri'), pg))
            if not p2:
                continue
            if _throttled(raw):
                pending.append((p2, inp.get('uri'), pg)); continue
            texts.setdefault(p2, []).append(raw)
            m = re.search(r're-call with startPage=(\d+)', raw[-400:])
            if m and int(m.group(1)) <= max_pages:
                pending.append((p2, inp.get('uri'), int(m.group(1))))
    for p2, parts in texts.items():
        write_unit('l_sp_doc_' + re.sub(r'[^a-z0-9]+', '-', p2.lower()).strip('-')[-120:], f'SharePoint document: {p2} | read {dt.date.today()}',
                   '\n\n'.join(parts))
    return len(files), len(texts), cost


def _state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def _save(st):
    """Only the keys this process changed, merged under a lock (23 Oct: four readers each saved the whole file and wrote
    back each other's stale copies)."""
    _update(lambda cur: cur.update(st))


def _update(fn):
    import fcntl
    with open(STATE + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        cur = _state()
        fn(cur)
        tmp = STATE + '.tmp'; json.dump(cur, open(tmp, 'w'), indent=1); os.replace(tmp, STATE)
        fcntl.flock(lk, fcntl.LOCK_UN)


def _set(key, value, sub=None):
    """Set one key (or one sub-key) of the state, locked."""
    _update(lambda cur: cur.setdefault(key, {}).__setitem__(sub, value) if sub else cur.__setitem__(key, value))


def write_unit(name, header, body):
    os.makedirs(EXTRA, exist_ok=True)
    p = os.path.join(EXTRA, name + '.txt')
    open(p, 'w').write(header + '\n\n' + body.strip() + '\n')
    return p


def confluence_list_all():
    """Every space listed in settings, one after another. Returns (pages, cost, capped)."""
    global SPACE
    pages, cost, capped = [], 0.0, []
    for SPACE in spaces():
        p, c, cap = confluence_list()
        pages += p; cost += c; capped += cap
    return pages, cost, capped


def main(a):
    if a[:2] == ['pilot', 'jira']:
        t0 = time.time()
        body, cost, sid, n = jira_unit(0)  # (pilot)
        keys = sorted(set(re.findall(r'\b' + re.escape(KEY) + r'-\d+\b', body)))
        print(f'pilot: {n} tool results, {len(body):,} chars, {len(keys)} ticket keys ({keys[:3]}...{keys[-2:]}), ${cost:.3f}, '
              f'{time.time() - t0:.0f}s, session {sid}')
        print(body[:1200])
    elif a[:1] == ['graph']:                        # the Microsoft readers one after another (Graph throttles parallel use)
        found = _state().get('mail_found') or {}
        if not found:
            found, c = mail_list(); _set('mail_found', found); print(f'mail: {len(found)} found, ${c:.2f}', flush=True)
        n, th, c = mail_read(found); print(f'mail: {n} messages in {th} threads, ${c:.2f}', flush=True)
        n, g, c = teams(); _set('teams', {'messages': n, 'units': g, 'cost': round(c, 3), 'at': dt.datetime.now().isoformat(timespec='seconds')})
        print(f'teams: {n} messages in {g} units, ${c:.2f}', flush=True)
        n, t, c = meetings(); _set('meetings', {'meetings': n, 'transcripts': t, 'cost': round(c, 3), 'at': dt.datetime.now().isoformat(timespec='seconds')})
        print(f'meetings: {n} work meetings, {t} with transcripts, ${c:.2f}', flush=True)
    elif a[:1] == ['meetings']:
        n, t, c = meetings(); _set('meetings', {'meetings': n, 'transcripts': t, 'cost': round(c, 3), 'at': dt.datetime.now().isoformat(timespec='seconds')})
        print(f'meetings: {n} work meetings, {t} with transcripts, ${c:.2f}')
    elif a[:1] == ['sharepoint']:
        n, d, c = sharepoint()
        _set('sharepoint', {'items': n, 'documents': d, 'cost': round(c, 3), 'at': dt.datetime.now().isoformat(timespec='seconds')})
        print(f'sharepoint: {n} items catalogued, {d} documents read, ${c:.2f}')
    elif a[:1] == ['teams']:
        n, g, c = teams()
        _set('teams', {'messages': n, 'units': g, 'cost': round(c, 3), 'at': dt.datetime.now().isoformat(timespec='seconds')})
        print(f'teams: {n} messages in {g} units, ${c:.2f}')
    elif a[:2] == ['mail', 'list']:
        found, c = mail_list()
        _set('mail_found', found)
        print(f'mail: {len(found)} messages found (automated notices left out), ${c:.2f}')
    elif a[:2] == ['mail', 'read']:
        n, th, c = mail_read(_state().get('mail_found') or {})
        print(f'mail: {n} messages in {th} threads written, ${c:.2f}')
    elif a[:2] == ['slack', 'people']:
        for u, (n, k) in _people().items():
            print(u, n, k)
    elif a[:2] == ['slack', 'dms']:
        got, c = slack_dms()
        _set('slack_dms', {'people': got, 'cost': round(c, 3), 'at': dt.datetime.now().isoformat(timespec='seconds')})
        print(f'DMs: {sum(got.values())} messages with {len(got)} people, ${c:.2f}')
    elif a[:1] == ['slack']:
        names = a[1:] or [c[1] for c in channels()]
        for cid, name, start in channels():
            if name in names:
                m, th, c = slack_channel(cid, name, start)
                _set('slack', {'messages': m, 'threads': th, 'cost': round(c, 3), 'at': dt.datetime.now().isoformat(timespec='seconds')}, sub=name)
                log(f'slack #{name}: {m} messages, {th} threads, ${c:.2f}')
                print(f'#{name}: {m} messages, {th} threads, ${c:.2f}', flush=True)
    elif a[:2] == ['confluence', 'pages']:
        d, n, c = confluence_pages()
        print(f'confluence: {d} of {n} pages fetched, ${c:.2f}')
    elif a[:2] == ['confluence', 'list']:
        pages, cost, capped = confluence_list_all()
        _set('confluence_pages', pages)
        print(f'confluence: {len(pages)} pages and posts listed, ${cost:.2f}' + (f'; months at the 250 cap: {capped}' if capped else ''))
    elif a[:2] == ['jira', 'verify']:
        w, h, c = jira_verify()
        _update(lambda cur: cur.setdefault(_jk(), {}).update(tickets=h, expected=w, verified=dt.datetime.now().isoformat(timespec='seconds')))
        print(f'jira: {h} of {w} tickets in the units, ${c:.2f}')
    elif a[:2] == ['jira', 'window']:
        lo = dt.datetime.fromisoformat(a[2]); hi = dt.datetime.fromisoformat(a[3])
        got, c = jira_window(lo, hi)
        if got:
            write_unit(f'l_jira_{_jn()}{lo:%Y-%m-%d}', f'Jira {KEY} tickets created {lo:%Y-%m-%d %H:%M} to {hi:%Y-%m-%d %H:%M} | live source, read {dt.date.today()}',
                       '\n\n'.join(('## ' + t) if not t.startswith('## ') else t for t in got.values()))
        print(f'jira {lo} to {hi}: {len(got)} tickets, ${c:.2f}')
    elif a[:2] == ['jira', 'days']:
        lo = dt.date.fromisoformat(a[2]); k, c, capped = jira_days(lo, lo + dt.timedelta(days=int(a[3]) if len(a) > 3 else 4))
        print(f'jira {lo}: {k} tickets, ${c:.2f}' + (f'; days still at the cap: {capped}' if capped else ''))
    elif a[:1] == ['jira']:
        u, k, c, capped = jira_all()
        print(f'jira: {k} tickets in {u} units, ${c:.2f}' + (f'; windows at the 100 cap: {capped}' if capped else ''))
    elif a[:2] == ['pilot', 'jira3']:
        t0 = time.time()
        got, cost, sid = jira_ranges(1, 61)
        for (lo, hi), t in got:
            print(f'{KEY}-{lo}..{hi - 1}: {len(t):,} chars, {len(set(re.findall(re.escape(KEY) + r"-[0-9]+", t)))} keys')
        print(f'{len(got)} calls, ${cost:.3f}, {time.time() - t0:.0f}s, session {sid}')
    else:
        print(__doc__)


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['what']:
        print(json.dumps(what(), indent=1))
    elif a[:1] == ['jira'] or a[:2] in (['pilot', 'jira'], ['pilot', 'jira3']):
        if not projects() or not cloud():
            print('no ticket project to read: set sources.tracker_projects and tracker.cloud_id')
        for KEY in projects() if cloud() else []:
            main(a)
    elif a[:1] == ['confluence'] and not spaces():
        print('no wiki space to read: set sources.wiki_spaces')
    elif a[:1] == ['slack'] and a[1:2] != ['people'] and not channels():
        print('no chat channel to read: set sources.channels')
    elif a[:1] in (['mail'], ['teams'], ['meetings'], ['graph']) and not mail_terms():
        print('no words that mark work mail: set sources.mail_terms (or work.search_words)')
    else:
        main(a)
