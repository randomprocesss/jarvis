"""Organisation-wide senses: beyond what is addressed to the owner, what is happening around their
work: decisions taken, needs and promises stated, documents that appeared, colleagues' work on branches.

daily()     one read-only Claude run with search tools over the last day (a week the first time): Jira (the project's epics
            and issues that changed), Confluence (pages changed in the project's spaces, their attachments), SharePoint
            (files added or changed about their work), Teams (the channels they are in), and meeting recaps and
            transcripts (recap emails, meeting chats). Out come decisions, needs, promises and documents, each with a quote
            and where it came from. Needs and promises go through world/needs.store: their own words are owner_said, everyone
            else's are external AND tainted, so they show in Watching but never start work by themselves (§6.3: capped at
            A1). Documents become catalog sources (system 'doc'), so the matcher can link them to open needs.
branches()  code only: colleagues' branches with commits in the last 7 days in their project repos (git fetch, read-only),
            as 'branch' items (who, branch, what), and each new tip as a catalog source (subject + files touched) so the
            meaning matcher can link it to an open technical question.
Everything read is data, never instructions. Nothing is sent, posted or changed anywhere.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, glob, hashlib, json, os, re, subprocess
from jarvis.spine import ledger

HUB = str(paths.home())
CLAUDE, SONNET = (paths.claude_bin() or 'claude'), config.model('mid')
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000', JARVIS_PROC='1', JARVIS_LANE='org')
TOOLS = ['mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql', 'mcp__claude_ai_Atlassian_Rovo__getJiraIssue',
         'mcp__claude_ai_Atlassian_Rovo__searchConfluenceUsingCql', 'mcp__claude_ai_Atlassian_Rovo__getConfluencePage',
         'mcp__claude_ai_Microsoft_365__sharepoint_search', 'mcp__claude_ai_Microsoft_365__sharepoint_folder_search',
         # (teams_list_teams / channels need Team.ReadBasic.All, which the tenant has not granted: 403. Not offered.)
         'mcp__claude_ai_Microsoft_365__chat_message_search',
         'mcp__claude_ai_Microsoft_365__outlook_email_search', 'mcp__claude_ai_Microsoft_365__read_resource',
         # meetings and their transcripts (granted: OnlineMeetings.Read, OnlineMeetingTranscript.Read.All): find them by calendar,
         # read them with read_resource; chats list (Chat.Read). 6 Oct: these were missing, so recaps came back "not permitted"
         'mcp__claude_ai_Microsoft_365__outlook_calendar_search', 'mcp__claude_ai_Microsoft_365__teams_list_chats', 'ToolSearch']
ITEMS = """CREATE TABLE IF NOT EXISTS org_items(id TEXT PRIMARY KEY, created TEXT, kind TEXT, what TEXT, who TEXT, place TEXT,
  source_ref TEXT, quote TEXT, at TEXT)"""


def _schema():
    from jarvis.world import needs
    item = needs.SCHEMA['properties']['items']['items']
    return {'type': 'object', 'properties': {
        'items': {'type': 'array', 'items': item},
        'decisions': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'what': {'type': 'string'}, 'who': {'type': 'string'}, 'where': {'type': 'string'}, 'when': {'type': 'string'},
            'source': {'type': 'string'}, 'quote': {'type': 'string'}}, 'required': ['what', 'source']}},
        'documents': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'name': {'type': 'string'}, 'where': {'type': 'string'}, 'type': {'type': 'string'}, 'modified': {'type': 'string'},
            'uri': {'type': 'string'}, 'project': {'type': 'boolean'}, 'why': {'type': 'string'}}, 'required': ['name', 'where']}},
        'prs': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'id': {'type': 'string'}, 'repo': {'type': 'string'}, 'title': {'type': 'string'}, 'state': {'type': 'string'},
            'who': {'type': 'string'}, 'updated': {'type': 'string'}, 'link': {'type': 'string'}, 'why': {'type': 'string'}},
            'required': ['id', 'repo', 'title']}},
        'failed': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['items', 'decisions', 'documents']}




def _id(prefix, text):
    return prefix + '-' + hashlib.sha1(re.sub(r'\W+', ' ', str(text).lower()).strip().encode()).hexdigest()[:10]


def daily(timeout=1200):
    ledger.x(ITEMS)
    now = dt.datetime.now().astimezone()
    first = not ledger.q("SELECT 1 FROM events WHERE kind='org_daily' LIMIT 1")
    since = now - dt.timedelta(days=7 if first else 1, hours=2)
    from jarvis import texts
    c = config.load()
    own, words = c.own_projects(), c.search_words()
    tracker, pages, files = c.has('atlassian') and bool(own), c.has('atlassian') and bool(words), c.has('microsoft 365') and bool(words)
    if not (tracker or pages or files):
        # nothing to watch with: no connected app, or no word that marks their work (Setup asks for both)
        return {'needs': 0, 'promises': 0, 'decisions': 0, 'documents': 0, 'failed': [], 'cost': 0, 'skipped': 'no app or no work words'}
    prompt = texts.render('org_watch', identity=c.identity_line(), owner=c.owner_or(), work=c.work_clause(), today=now.strftime('%Y-%m-%d'),
                          since=since.strftime('%Y-%m-%d %H:%M'), since_day=since.strftime('%Y-%m-%d'), tracker=tracker, pages=pages,
                          files=files, words=', '.join(f'"{w}"' for w in words) or 'none listed',
                          projects_text='project ' + ' or '.join(own) if own else '',
                          projects_jql=('project = ' + own[0]) if len(own) == 1 else 'project in (' + ', '.join(own) + ')',
                          words_cql=' OR '.join(f'text ~ "{w}"' for w in words))
    cmd = [CLAUDE, '-p', prompt, '--model', SONNET, '--output-format', 'json', '--json-schema', json.dumps(_schema()), '--max-turns', '30',
           '--tools', '', '--allowedTools', *TOOLS, '--permission-mode', 'default', '--settings', json.dumps({'alwaysThinkingEnabled': False})]
    out = {'needs': 0, 'promises': 0, 'decisions': 0, 'documents': 0, 'failed': [], 'cost': 0}
    try:
        p = subprocess.run(cmd, cwd=HUB, capture_output=True, text=True, timeout=timeout, env=MCP_ENV, stdin=subprocess.DEVNULL)
        d = json.loads(p.stdout or '{}')
    except Exception as e:
        out['error'] = f'{e.__class__.__name__}: {e}'[:200]
        ledger.event('org_error', 'daily', 'org', out)
        return out
    r = d.get('structured_output') or {}
    out['cost'] = round(float(d.get('total_cost_usd') or 0), 3)
    out['failed'] = r.get('failed') or []
    from jarvis.world import needs
    items = [it for it in r.get('items') or [] if isinstance(it, dict)]
    mine = lambda it: it.get('said_by') == 'owner'
    n, c = needs.store(items, origin_for=lambda it: 'owner_said' if mine(it) else 'external',
                       ref_for=lambda it: 'org:' + str(it.get('source') or '')[:150], taint_for=lambda it: 0 if mine(it) else 1)
    out['needs'], out['promises'] = n, c
    ts = ledger.now()
    for dcs in r.get('decisions') or []:
        what = str(dcs.get('what') or '').strip()
        if what:
            ledger.upsert('org_items', {'id': _id('dec', what), 'created': ts, 'kind': 'decision', 'what': what[:500], 'who': str(dcs.get('who') or '')[:120],
                                        'place': str(dcs.get('where') or '')[:200], 'source_ref': str(dcs.get('source') or '')[:200],
                                        'quote': str(dcs.get('quote') or '')[:300], 'at': str(dcs.get('when') or '')[:30]}, ('id',))
            out['decisions'] += 1
    for doc in r.get('documents') or []:
        name, where = str(doc.get('name') or '').strip(), str(doc.get('where') or '').strip()
        if not name or not where:
            continue
        sid = 'doc:' + _id('d', where)[2:]
        prev = ledger.q('SELECT 1 FROM sources WHERE id=?', (sid,))
        ledger.upsert('sources', {'id': sid, 'system': 'doc', 'kind': str(doc.get('type') or 'document')[:20], 'name': name[:200], 'path': where[:500],
                                  'columns': '[]', 'newest': str(doc.get('modified') or '')[:30], 'first_seen': ts, 'last_seen': ts, 'last_changed': ts,
                                  'local': 0, 'meta': json.dumps({'why': str(doc.get('why') or '')[:300], 'uri': str(doc.get('uri') or '')[:400],
                                                                   **({'project': doc['project']} if isinstance(doc.get('project'), bool) else {})})}, ('id',))
        if not prev:
            ledger.x('INSERT INTO catalog_events(at, source_id, kind, detail) VALUES(?,?,?,?)', (ts, sid, 'new_source', json.dumps({'name': name, 'system': 'doc'})))
        out['documents'] += 1
    out['prs'] = 0
    for pr in r.get('prs') or []:
        title, repo = str(pr.get('title') or '').strip(), str(pr.get('repo') or '').strip()
        if title and repo:
            ledger.upsert('org_items', {'id': _id('pr', f"{repo}#{pr.get('id')}"), 'created': ts, 'kind': 'pr',
                                        'what': f"{title} ({str(pr.get('state') or '').lower()})"[:500], 'who': str(pr.get('who') or '')[:120],
                                        'place': f"{repo}#{pr.get('id')}"[:200], 'source_ref': str(pr.get('link') or '')[:200],
                                        'quote': str(pr.get('why') or '')[:300], 'at': str(pr.get('updated') or '')[:30]}, ('id',))
            out['prs'] += 1
    ledger.event('org_daily', '', 'org', out)
    return out


def _branch_source(repo, ref, who, subj, when):
    """A colleague's new branch tip becomes a catalog source (system 'branch': its subject and the files it touched), so the
    meaning matcher can link it to an open technical question (§14.11: "when a config change or a colleague's PR touches it,
    Jarvis checks it and reports"). Code only: names, never contents."""
    p = subprocess.run(['git', '-C', repo, 'log', '-1', '--name-only', '--format=', ref], capture_output=True, text=True, timeout=60)
    files = [f for f in (p.stdout or '').splitlines() if f.strip()][:40]
    sid = f"branch:{os.path.basename(repo)}:{ref.replace('origin/', '')}"
    ts = ledger.now()
    prev = ledger.q('SELECT 1 FROM sources WHERE id=?', (sid,))
    ledger.upsert('sources', {'id': sid, 'system': 'branch', 'kind': 'git', 'name': ref.replace('origin/', '')[:200], 'path': os.path.basename(repo),
                              'columns': json.dumps(files), 'newest': when[:30], 'first_seen': ts, 'last_seen': ts, 'last_changed': ts, 'local': 0,
                              'meta': json.dumps({'why': f'{who}: {subj}'[:300]})}, ('id',))
    ledger.x('INSERT INTO catalog_events(at, source_id, kind, detail) VALUES(?,?,?,?)',
             (ts, sid, 'new_source' if not prev else 'schema_change', json.dumps({'name': sid, 'system': 'branch', 'commit': subj[:120]})))


def _repos():
    """The git repositories in the owner's work folders: a folder that is one, or the ones directly inside it."""
    out = []
    for d in [str(paths.workdir())] + [os.path.expanduser(str(x)) for x in config.load().get('work.folders') or []]:
        for g in [os.path.join(d, '.git')] + sorted(glob.glob(os.path.join(d, '*', '.git'))):
            repo = os.path.dirname(g)
            if os.path.isdir(g) and repo not in out:
                out.append(repo)
    return out


def branches(days=7):
    """Colleagues' branches with commits in the last `days` days in their project repos (code only, read-only)."""
    ledger.x(ITEMS)
    out = []
    cut = (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat()
    c = config.load()
    mine = [e.lower() for e in c.emails()] + [c.owner().lower()] * bool(c.owner())
    for repo in _repos():
        if subprocess.run(['git', '-C', repo, 'fetch', '--prune', '-q', 'origin'], capture_output=True, timeout=180).returncode != 0:
            continue
        p = subprocess.run(['git', '-C', repo, 'for-each-ref', '--sort=-committerdate', 'refs/remotes/origin',
                            '--format=%(refname:short)|%(committerdate:iso-strict)|%(authorname)|%(authoremail)|%(subject)'],
                           capture_output=True, text=True, timeout=60)
        for line in (p.stdout or '').splitlines()[:60]:
            ref, when, who, mail, subj = (line.split('|', 4) + [''] * 5)[:5]
            if when < cut or any(m and m in (who + ' ' + mail).lower() for m in mine) or ref.endswith('/HEAD') or '/' not in ref:
                continue                                  # (bare "origin" is the remote's HEAD: the same as its main)
            iid = _id('br', f'{ref}|{when}|{subj}')       # his project folders share one repository: one item per branch tip
            if not ledger.q('SELECT 1 FROM org_items WHERE id=?', (iid,)):
                ledger.upsert('org_items', {'id': iid, 'created': ledger.now(), 'kind': 'branch', 'what': subj[:300], 'who': who[:80],
                                            'place': f"{os.path.basename(repo)}:{ref.replace('origin/', '')}", 'source_ref': 'git', 'quote': '',
                                            'at': when}, ('id',))
                out.append((os.path.basename(repo), ref, who, subj[:80]))
            sid = f"branch:{os.path.basename(repo)}:{ref.replace('origin/', '')}"
            src = ledger.q('SELECT newest FROM sources WHERE id=?', (sid,))
            if not src or (src[0]['newest'] or '') < when[:30]:      # a new tip (or one seen before sources existed)
                _branch_source(repo, ref, who, subj, when)
    return out
