"""Jarvis's brain: POST /api/jarvis {q, screen, conv?, heard?, dropped?, voice?} -> streamed NDJSON from Claude.

Lines out:  {"type":"ack","conv"}  {"type":"progress","text":"Checking Slack"} (each tool Claude uses)
            {"type":"speak","text",<mood>,<filler>} (each spoken sentence as soon as it is complete)
            {"type":"final","speak","text","actions":[...validated...],"mood","conv","tier"}  or  {"type":"error","error"}
Tiers: a quick model (Haiku) answers or hands over to the deep model (Sonnet) in the same warm process; work requests go
straight to a background task (no model call). Conversations end after 10 minutes and are kept 30 days in voice/conversations/.

Tools: Read/Grep/Glob over the owner's work folders, Jarvis's data and their memory notes, plus the read/search tools of
the apps connected to their Claude account and of their own tool servers; everything that sends, creates or edits is denied. Actions come back as data; the page runs them through the same API as its buttons,
after a spoken read-back and a 3-second cancel. remember / forget / remind and ask_claude are done here at once.
Background work: tasks Jarvis starts (one worker at a time, /api/jarvis_task), steering notes for a running task, and
read-only consults of a full Claude (ask_claude).
"""
from jarvis import config
from jarvis import procs
from jarvis import paths
import datetime as dt, json, os, random, re, signal, subprocess, threading, time, uuid
from jarvis import human as human
from jarvis.spine import speech_guard

HUB = str(paths.home())
VOICE = os.path.join(HUB, 'voice')
CONV_DIR = os.path.join(VOICE, 'conversations')
CLAUDE = (paths.claude_bin() or 'claude')
MAX_Q, MAX_SCREEN, TIMEOUT, CONV_TTL = 2000, 14 * 1024, 90, 600   # screen: + the reading of the window they are looking at
# Wait for the owner's connected apps to come up before the first turn. Without it a headless run starts while they are
# still "pending" and has none of their tools, so Jarvis says "I can't reach that" about an app that is connected.
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000')


def memory_dir():
    """The owner's memory notes: Claude Code's memory for their work folder."""
    return str(paths.claude_chats_for(paths.workdir()) / 'memory')


def read_dirs():
    """What the assistant may read: the owner's work folders, Jarvis's own data and their memory notes."""
    dirs = [str(paths.workdir())]
    for d in config.load().get('work.folders') or []:
        d = os.path.expanduser(str(d))
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs + [HUB, memory_dir()]


def code_host():
    """True when settings name where the owner's repositories live (sources.code_url, or a workspace for pull requests)."""
    c = config.load()
    return bool(str(c.get('sources.code_url') or '').strip() or str(c.get('sources.code_workspace') or '').strip())


def mcp_configs():
    """The files that tell a Claude run which local tool servers to start: Jarvis's own lookup tools (written here, because it
    holds this computer's paths), then the owner's own file if they named one in settings (sources.mcp_config)."""
    own = str(paths.state('mcp.json'))
    servers = {'jarvis': {'command': paths.python(), 'args': ['-m', 'jarvis.mcp'],
                          'env': {'PYTHONPATH': str(paths.REPO), 'JARVIS_HOME': HUB}}}
    if code_host():              # the owner's repositories on their code host, read-only (tools/repos_mcp.py)
        servers['repos'] = {'command': paths.python(), 'args': [str(paths.tool('repos_mcp.py'))], 'env': {'JARVIS_HOME': HUB}}
    want = json.dumps({'mcpServers': servers}, indent=1)
    try:
        have = open(own).read()
    except OSError:
        have = ''
    if have != want:
        os.makedirs(os.path.dirname(own), exist_ok=True)
        open(own + '.tmp', 'w').write(want)
        os.replace(own + '.tmp', own)
    theirs = os.path.expanduser(str(config.load().get('sources.mcp_config') or ''))
    return [own] + ([theirs] if theirs and os.path.isfile(theirs) else [])


# What the assistant may call. Reading only: its own lookup tools, then the read tools of the apps connected to the owner's
# Claude account, then the read tools of their own tool servers that they listed in settings (sources.read_tools).
REPO_READ = ['mcp__repos__' + n for n in ('repos_list', 'repo_branches', 'repo_log', 'repo_commit', 'repo_files', 'repo_read_file',
                                          'repo_grep', 'repo_diff')]
LOOKUP = ['mcp__jarvis__' + n for n in ('inbox_find', 'inbox_item', 'tasks_now', 'needs_find', 'memory_recall', 'project_brief',
                                        'kb_search', 'kb_entity', 'kb_facts')]
APP_READ = ['mcp__claude_ai_Slack__slack_search_public', 'mcp__claude_ai_Slack__slack_search_public_and_private',
            'mcp__claude_ai_Slack__slack_read_channel', 'mcp__claude_ai_Slack__slack_read_thread',
            'mcp__claude_ai_Slack__slack_read_canvas', 'mcp__claude_ai_Slack__slack_read_user_profile',
            'mcp__claude_ai_Slack__slack_search_users', 'mcp__claude_ai_Slack__slack_search_channels',
            'mcp__claude_ai_Atlassian_Rovo__getJiraIssue', 'mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql',
            'mcp__claude_ai_Atlassian_Rovo__getConfluencePage', 'mcp__claude_ai_Atlassian_Rovo__searchConfluenceUsingCql',
            'mcp__claude_ai_Atlassian_Rovo__search', 'mcp__claude_ai_Atlassian_Rovo__fetch',
            'mcp__claude_ai_Microsoft_365__outlook_email_search', 'mcp__claude_ai_Microsoft_365__chat_message_search',
            'mcp__claude_ai_Microsoft_365__sharepoint_search', 'mcp__claude_ai_Microsoft_365__search_people',
            # the calendar, read-only
            'mcp__claude_ai_Microsoft_365__outlook_calendar_search', 'mcp__claude_ai_Microsoft_365__find_meeting_availability',
            'mcp__claude_ai_Microsoft_365__outlook_find_available_time', 'mcp__claude_ai_Microsoft_365__read_resource']
# Belt and braces: never let a write tool run, whatever a settings file allows.
APP_WRITE = ['mcp__claude_ai_Slack__slack_send_message', 'mcp__claude_ai_Slack__slack_send_message_draft',
             'mcp__claude_ai_Slack__slack_schedule_message', 'mcp__claude_ai_Slack__slack_create_canvas',
             'mcp__claude_ai_Slack__slack_update_canvas', 'mcp__claude_ai_Slack__slack_add_reaction',
             'mcp__claude_ai_Atlassian_Rovo__createJiraIssue', 'mcp__claude_ai_Atlassian_Rovo__editJiraIssue',
             'mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue', 'mcp__claude_ai_Atlassian_Rovo__transitionJiraIssue',
             'mcp__claude_ai_Atlassian_Rovo__addWorklogToJiraIssue', 'mcp__claude_ai_Atlassian_Rovo__createConfluencePage',
             'mcp__claude_ai_Atlassian_Rovo__updateConfluencePage', 'mcp__claude_ai_Atlassian_Rovo__createConfluenceFooterComment',
             'mcp__claude_ai_Atlassian_Rovo__createConfluenceInlineComment', 'mcp__claude_ai_Atlassian_Rovo__createIssueLink',
             'mcp__claude_ai_Microsoft_365__outlook_send_mail', 'mcp__claude_ai_Microsoft_365__outlook_send_draft',
             'mcp__claude_ai_Microsoft_365__outlook_forward_mail', 'mcp__claude_ai_Microsoft_365__outlook_create_draft',
             'mcp__claude_ai_Microsoft_365__outlook_create_reply_draft', 'mcp__claude_ai_Microsoft_365__outlook_create_reply_all_draft',
             'mcp__claude_ai_Microsoft_365__outlook_create_event', 'mcp__claude_ai_Microsoft_365__outlook_update_event',
             'mcp__claude_ai_Microsoft_365__outlook_delete_event', 'mcp__claude_ai_Microsoft_365__outlook_respond_to_event',
             'mcp__claude_ai_Microsoft_365__outlook_trash_thread', 'mcp__claude_ai_Microsoft_365__outlook_batch_delete_messages',
             'mcp__claude_ai_Microsoft_365__outlook_set_vacation', 'mcp__claude_ai_Microsoft_365__outlook_create_filter',
             'mcp__claude_ai_Microsoft_365__teams_send_chat_message', 'mcp__claude_ai_Microsoft_365__teams_send_channel_message',
             'mcp__claude_ai_Microsoft_365__teams_reply_channel_message', 'mcp__claude_ai_Microsoft_365__teams_create_chat',
             'mcp__claude_ai_Microsoft_365__sharepoint_upload_file', 'mcp__claude_ai_Microsoft_365__sharepoint_update_file',
             'mcp__claude_ai_Microsoft_365__sharepoint_delete_item', 'mcp__claude_ai_Microsoft_365__sharepoint_move_item']
# credentials are never read, whatever is on screen
SECRET_READS = ['Read(**/pgpass*)', 'Read(**/.pgpass)', 'Read(~/.ssh/**)', 'Read(~/.aws/**)', 'Read(~/.gnupg/**)',
                'Read(~/.config/gcloud/**)', 'Read(~/Library/Keychains/**)', 'Read(**/*credentials*)', 'Read(**/.git-credentials)',
                'Read(**/.netrc)', 'Read(**/*.pem)', 'Read(**/*.key)', 'Read(**/.env)', 'Read(**/.env.*)']
_TOOL = re.compile(r'^mcp__[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+$')


def read_tools():
    c = config.load()
    own = [t for t in (str(x).strip() for x in c.get('sources.read_tools') or []) if _TOOL.match(t) and t not in APP_WRITE]
    return ['Read', 'Grep', 'Glob'] + LOOKUP + (REPO_READ if code_host() else []) + (APP_READ if c.get('sources.claude_connectors') else []) + own


def deny_tools():
    c = config.load()
    return (['Bash', 'Edit', 'Write', 'NotebookEdit', 'WebFetch', 'Task', 'Agent'] + APP_WRITE + SECRET_READS
            + [f"Read({str(paths.state()).replace(os.path.expanduser('~'), '~', 1)}/page.key*)"]
            + [f'Read({p})' for p in c.get('work.never_read') or []])


def write_tools():
    """The connected apps' tools that change something: off for every run that only looks things up."""
    return list(APP_WRITE)

MODELS = {'haiku': ('claude-haiku-5-5', 'Haiku 5.5'), 'sonnet': ('claude-sonnet-5-5', 'Sonnet 5.5'),
          'opus': ('claude-opus-5-5', 'Opus 5.5'), 'fable': ('claude-fable-5-2', 'Fable 5.2')}


def _reactions():
    """The reaction library's lines (voice/reactions/mia/index.json, Orpheus recordings): the brain may open a reply with one."""
    try:
        items = json.load(open(os.path.join(VOICE, 'reactions', 'mia', 'index.json')))['items']
    except Exception:
        return ''
    return ('\nREACTIONS: the voice has these as real, expressive recordings. When the moment has feeling, START speak with ONE of '
            'them, copied exactly as its own sentence (tags included), then the rest in your words. Pick by meaning: good news '
            '"Oh nice!" or "Yes! Finally.", a repeat failure "Ugh, again?", something funny "<chuckle> Fair enough.", a surprise '
            '"Wait, really?", thinking "Hmm, let me think.", a plain yes "Got it.". Not for plain facts, never two in a row: '
            + ' | '.join(i['text'] for i in items))


_prompts = {}


def system_prompt(fast=False):
    """The assistant's instructions for this owner: the template (jarvis/texts/system.md) with their name, their work in
    their words, their folders, their time zone and the apps they have; then the reaction lines; then the speed rules for
    the quick tier. Built again when the settings change."""
    from jarvis import texts
    try:
        stamp = os.path.getmtime(paths.config_file())
    except OSError:
        stamp = 0
    if _prompts.get('stamp') != stamp:
        _prompts.clear(); _prompts['stamp'] = stamp
    if fast not in _prompts:
        c = config.load()
        owner, work = c.owner_or(), c.work_line()
        called = str(c.get('owner.address_as') or '').strip()
        slack, atl, ms = c.has('slack'), c.has('atlassian'), c.has('microsoft 365')
        apps = [n for n, on in (('Slack', slack), ('Jira and Confluence', atl), ('Outlook, Teams and SharePoint', ms)) if on]
        secs = -(time.altzone if time.localtime().tm_isdst > 0 else time.timezone)
        tz = f"{time.strftime('%Z')}, UTC{'+' if secs >= 0 else '-'}{abs(secs) // 3600}:{abs(secs) % 3600 // 60:02d}"
        names = list(MODELS)
        text = texts.render(
            'system', owner=owner, owner_caps=owner.upper(), work=f' ({work})' if work else '',
            folders=', '.join(read_dirs()[:-2]) or 'none chosen yet', home=HUB, memory=memory_dir(),
            apps=': ' + ', '.join(apps) if apps else ' (none is connected yet: say so when they ask for something that needs one)',
            cloud_id=str(c.get('tracker.cloud_id') or '').strip() if atl else '',
            address_line=(f'Call them "{called}" now and then, not every time.' if called else
                          'Call them by their first name now and then, not every time.' if c.owner() else ''),
            tracker=atl, calendar=ms, slack=slack, drive=slack and bool(str(c.get('sharing.folder') or '').strip()), tz=tz,
            model_names=' | '.join(names), model_labels=', '.join(MODELS[n][1] for n in names), model_example=MODELS[names[-1]][1])
        _prompts[fast] = text + _reactions() + (texts.render('fast_rules') if fast else '')
    return _prompts[fast]


def origin_ok(headers):
    from jarvis.spine import gateway
    return gateway.from_page(headers)   # one check, on the port this server really listens on


# ---------------------------------------------------------------- conversations
_convs, _lock = {}, threading.Lock()


def _conv_path(cid):
    return os.path.join(CONV_DIR, re.sub(r'[^A-Za-z0-9_-]', '', cid) + '.json')


def _save_conv(cid, c):
    os.makedirs(CONV_DIR, exist_ok=True)
    json.dump({'id': cid, 'started': c['started'], 'turns': c['turns']}, open(_conv_path(cid), 'w'), indent=1)


def purge_old():
    if not os.path.isdir(CONV_DIR):
        return
    cut = time.time() - 30 * 86400          # 30 days: Jarvis recalls earlier conversations (jarvis_human)
    for f in os.listdir(CONV_DIR):
        p = os.path.join(CONV_DIR, f)
        if f.endswith('.json') and os.path.getmtime(p) < cut:
            os.remove(p)


def conversations():
    purge_old()
    out = []
    if os.path.isdir(CONV_DIR):
        for f in sorted(os.listdir(CONV_DIR), reverse=True)[:12]:
            try:
                out.append(json.load(open(os.path.join(CONV_DIR, f))))
            except Exception:
                pass
    out.sort(key=lambda c: c.get('started', ''), reverse=True)
    return {'conversations': out}


WATCH_FILLER = {'sure', 'okay', 'yeah', 'yes', 'cool', 'thanks', 'thank', 'please', 'just', 'it', "it's", 'its', 'that', "that's", 'this',
                'one', 'done', 'ready', 'finished', 'finishes', 'back', 'comes', 'there', 'then', 'when', 'once', 'and', 'the', 'owner', 'jarvis'}
def watch_worthy(q):
    """A "let me know when ..." that names something of its own (a table, a person's reply), not "it" or "that one"."""
    from jarvis.world import needs as _n
    if not _n.WATCH.search(q or ''):
        return False
    own = _n.WATCH.sub(' ', q.lower())
    return len([w for w in re.findall(r"[a-z0-9']{3,}", own) if w not in WATCH_FILLER]) >= 2


IDLE_CONTINUE = 15 * 60          # a saved conversation whose last turn is this recent is picked up again after a restart


def _load_conv(cid):
    """A conversation the server no longer holds in memory (it restarted, 5 Oct: the codeword test), from its saved file,
    if its last turn is recent enough to still be the same conversation. Call with _lock held."""
    if not cid or not ID.match(str(cid)) or cid in _convs:
        return _convs.get(cid)
    try:
        d = json.load(open(_conv_path(cid)))
        last = dt.datetime.fromisoformat(str((d.get('turns') or [{}])[-1].get('at') or d.get('started'))).timestamp()
    except (OSError, ValueError, TypeError):
        return None
    if time.time() - last > IDLE_CONTINUE:
        return None
    c = {'t0': time.time(), 'session': None, 'started': d.get('started') or '', 'turns': d.get('turns') or []}
    _convs[cid] = c
    return c


def _get_conv(cid):
    now = time.time()
    with _lock:
        c = (_convs.get(cid) or _load_conv(cid)) if cid else None
        if c and now - c['t0'] > CONV_TTL:          # ten quiet minutes end a conversation (the page does the same)
            c = None
        if not c:
            cid = dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
            c = {'t0': now, 'session': None, 'started': dt.datetime.now().isoformat(timespec='seconds'), 'turns': []}
            _convs[cid] = c
        c['t0'] = now                                # t0 = his last turn (5 Oct: it was the start, so every conversation
        return cid, c                                # was silently replaced 10 minutes in, even mid-talk)


# ---------------------------------------------------------------- voice metrics (timings and flags only; Jarvis.app writes them)
METRICS = os.path.join(VOICE, 'metrics.ndjson')
USAGE = os.path.join(VOICE, 'brain_usage.ndjson')     # one line per brain turn: tokens, cache, cost, speed (tools/jarvis_review.py reads it)


def usage_log(tag, d):
    """Records what one brain turn cost and how fast it was, from the CLI's result event. Never raises."""
    try:
        u = d.get('usage') or {}
        mu = d.get('modelUsage') or {}
        rec = {'at': dt.datetime.now().isoformat(timespec='seconds'), **tag, 'models': sorted(mu),
               'input': u.get('input_tokens', 0), 'cache_read': u.get('cache_read_input_tokens', 0),
               'cache_write': u.get('cache_creation_input_tokens', 0), 'output': u.get('output_tokens', 0),
               'thinking': (u.get('output_tokens_details') or {}).get('thinking_tokens', 0),
               'cost': round(float(d.get('total_cost_usd') or 0), 5), 'ttft_ms': d.get('ttft_ms'), 'ms': d.get('duration_ms'),
               'steps': d.get('num_turns'), 'error': bool(d.get('is_error'))}
        with open(USAGE, 'a') as f:
            f.write(json.dumps(rec) + '\n')
    except Exception:
        pass


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return round(xs[k])


def metrics_summary(day=None):
    """Today's spoken turns: median / p90 of their last word -> first audio and -> answer audio, plus barge-in counts."""
    day = day or dt.date.today().isoformat()
    rows = []
    for p in (METRICS + '.1', METRICS):
        if os.path.exists(p):
            for line in open(p):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('day') == day:
                    rows.append(r)
    def pick(rs):
        def lat(k):
            xs = [r['lat'][k] for r in rs if isinstance(r.get('lat'), dict) and isinstance(r['lat'].get(k), (int, float)) and 0 <= r['lat'][k] < 120000]
            return {'median': _pct(xs, 0.5), 'p90': _pct(xs, 0.9), 'n': len(xs)}
        bs = [r['barge_stop_ms'] for r in rs if isinstance(r.get('barge_stop_ms'), (int, float))]
        return {'turns': len(rs), 'first_audio': lat('first_audio'), 'answer_audio': lat('answer_audio'), 'eot': lat('eot'),
                'barge': sum(1 for r in rs if r.get('barge')), 'false_barge': sum(1 for r in rs if r.get('false_barge')),
                'cutoff': sum(1 for r in rs if r.get('cutoff')), 'fast_path': sum(1 for r in rs if r.get('fast_path')),
                'barge_stop_ms': {'median': _pct(bs, 0.5), 'p90': _pct(bs, 0.9), 'n': len(bs)}}
    live = [r for r in rows if r.get('src', 'live') == 'live']
    test = [r for r in rows if r.get('src') == 'test']
    return {'day': day, 'live': pick(live), 'test': pick(test)}


# ---------------------------------------------------------------- actions
ROUTES = {'today', 'inbox', 'tickets', 'home'}
ID = re.compile(r'^[A-Za-z0-9._|:-]{1,120}$')


def _s(v, n=4000):
    return isinstance(v, str) and 0 < len(v) <= n


def valid_action(a):
    """Only the listed actions with well-formed fields survive; everything else is dropped."""
    if not isinstance(a, dict):
        return None
    t = a.get('type')
    ok = lambda *ks: all(_s(a.get(k), 120) and ID.match(a[k]) for k in ks)
    if t in ('send_reply', 'post_jira', 'start_task', 'stop_task') and ok('rid', 'aid'):
        return {'type': t, 'rid': a['rid'], 'aid': a['aid']}
    if t == 'approve' and ok('id'):
        r = {'type': t, 'id': a['id']}
        if _s(a.get('choice'), 120):
            r['choice'] = a['choice']
        return r
    if t == 'decline' and ok('id'):
        return {'type': t, 'id': a['id']}
    if t == 'tick' and ok('rid') and isinstance(a.get('keys'), list) and 0 < len(a['keys']) <= 30 and all(_s(k, 60) and ID.match(k) for k in a['keys']):
        return {'type': t, 'rid': a['rid'], 'keys': a['keys']}
    if t == 'run_now' and a.get('what') in ('roundup', 'work'):
        return {'type': t, 'what': a['what']}
    if t == 'ask_claude' and _s(a.get('question'), 600):
        return {'type': t, 'question': a['question'].strip()}
    if t == 'tell_claude' and ok('rid', 'aid') and _s(a.get('text'), 4000):
        return {'type': t, 'rid': a['rid'], 'aid': a['aid'], 'text': a['text']}
    if t == 'open':
        if a.get('route') in ROUTES:
            return {'type': t, 'route': a['route']}
        if ok('rid', 'aid'):
            return {'type': t, 'rid': a['rid'], 'aid': a['aid']}
    if t == 'set_model' and a.get('model') == 'default':
        return {'type': t, 'tier': 'all', 'model': 'default'}
    if t == 'set_model' and a.get('model') in MODELS and a.get('tier', 'thinking') in ('fast', 'deep', 'work', 'thinking', 'all'):
        return {'type': t, 'tier': a.get('tier', 'thinking'), 'model': a['model']}
    if t in ('remember', 'forget') and _s(a.get('text'), 400):
        return {'type': t, 'text': a['text'].strip()}
    if t == 'remind' and _s(a.get('text'), 200) and _s(a.get('at'), 40):
        return {'type': t, 'text': a['text'].strip(), 'at': a['at'].strip()}
    if t == 'new_task' and _s(a.get('text'), 2000):
        title = a.get('title') if isinstance(a.get('title'), str) and 0 < len(a['title'].strip()) <= 80 else ''
        kind = str(a.get('kind') or '').strip().lower()
        pts = [re.sub(r'\s+', ' ', p).strip()[:200] for p in (a.get('points') or []) if isinstance(p, str) and p.strip()][:8] if isinstance(a.get('points'), list) else []
        return {'type': t, 'text': a['text'], **({'title': title.strip()} if title else {}), **({'kind': kind} if kind in ROUTE_LABELS else {}),
                **({'points': pts} if len(pts) >= 2 else {})}
    if t == 'pronounce' and _s(a.get('word'), 40) and _s(a.get('say'), 80) and re.match(r"^[\w .'-]+$", a['word']) and re.match(r"^[\w .'-]+$", a['say']):
        return {'type': t, 'word': a['word'].strip(), 'say': a['say'].strip()}
    if t == 'reveal' and _s(a.get('path'), 500):
        p = os.path.realpath(os.path.expanduser(a['path']))
        home = os.path.expanduser('~')
        if p.startswith(home + '/') and '/.ssh' not in p and 'pgpass' not in p:
            return {'type': t, 'path': p}
    return None


MOODS = ('neutral', 'warm', 'amused', 'serious', 'calm', 'excited', 'sympathetic')


def _sig(t):
    return re.sub(r'[^a-z0-9]', '', str(t or '').lower())


IDS = [(re.compile(r'\(?\b(?:round-up|roundup)\s+\d{4}-\d\d-\d\d-\d{4}-[\w-]+(?:,?\s*item\s+a\d+(?:\.\d+)?)?\)?', re.I), ''),
       (re.compile(r'\b\d{4}-\d\d-\d\d-\d{4}-[\w-]+\|a\d+(?:\.\d+)?\b'), ''),
       (re.compile(r'\bthread ts \d{9,}\.\d+\b', re.I), 'the thread'),
       (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'), '')]


def scrub_ids(t):
    """Screen text without plumbing ids (5 Oct audit: 'round-up 2026-10-05-0001-jarvis, item a1', thread ts, UUIDs)."""
    for rx, rep in IDS:
        t = rx.sub(rep, t)
    t = re.sub(r'[ \t]{2,}', ' ', re.sub(r'\(\s*\)', '', t))
    return re.sub(r'[ \t]+([.,;:])', r'\1', t).strip()


def shape_points(text, max_n=8, max_len=160):
    """A research answer as at most `max_n` short bullets, enforced in code (the prompt's rule was broken 5 of 5 times)."""
    t = scrub_ids(str(text or '')).strip()
    lines = [re.sub(r'^\s*(?:[-•*]|\(?\d{1,2}[.)])\s+', '', ln).strip() for ln in t.split('\n') if ln.strip()]
    if len(lines) <= 1:                                   # one block of prose: one bullet per sentence
        lines = [x.strip() for x in re.split(r'(?<=[.!?])\s+(?=[A-Z])', t) if x.strip()]
    def cutw(x):
        return x if len(x) <= max_len else x[:max_len].rsplit(' ', 1)[0].rstrip(',;:') + '…'
    pts = [cutw(x) for x in lines if not re.fullmatch(r'\**[^*]{0,40}:\**', x)][:max_n]
    return '\n'.join('- ' + x for x in pts)


TURN_TOOLS = {}                     # conv id -> tool names called this turn (grounding v2 reads and clears it)


def parse_reply(txt):
    s = (txt or '').strip()
    m = re.search(r'\{.*\}', s, re.S)
    try:
        # strict=False: line breaks inside a string are fine (12 Oct: a task brief written one instruction per line made the
        # whole reply unparseable, and Jarvis read the raw JSON aloud while no task started)
        d = json.loads(m.group(0) if m else s, strict=False)
    except Exception:
        from jarvis.spine import limits
        u = limits.seen(s)
        if u:
            return {'speak': f"I've hit Claude's usage limit until {limits.say_until(u)}, so I can't think this through right now. "
                             "Anything you ask meanwhile waits and runs then.", 'text': '', 'actions': [], 'mood': 'calm'}
        return {'speak': s[:300] if s else "Sorry, I didn't get an answer.", 'text': s, 'actions': []}
    acts = [x for x in (valid_action(a) for a in (d.get('actions') or [])[:6]) if x]
    out = {'speak': str(d.get('speak') or '')[:600], 'text': scrub_ids(str(d.get('text') or ''))[:12000], 'actions': acts}
    if _sig(out['text']) and _sig(out['text']) == _sig(out['speak']):
        out['text'] = ''                                # the screen never repeats the spoken answer
    if d.get('escalate') is True:
        out['escalate'] = True
    if d.get('mood') in MOODS:
        out['mood'] = d['mood']
    return out


class SpeakStream:
    """Pulls the "speak" string out of the streaming JSON text and yields each sentence as soon as it is complete."""

    def __init__(self):
        self.buf, self.sent, self.done = '', 0, False

    def _value(self):
        m = re.search(r'"speak"\s*:\s*"', self.buf)
        if not m:
            return None, False
        val, esc = [], False
        for ch in self.buf[m.end():]:
            if esc:
                val.append({'n': ' ', 't': ' '}.get(ch, ch)); esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                return ''.join(val), True
            else:
                val.append(ch)
        return ''.join(val), False

    def feed(self, chunk):
        if self.done:
            return []
        self.buf += chunk
        val, closed = self._value()
        if val is None:
            return []
        out = []
        while True:
            rest = val[self.sent:]
            m = re.search(r'[.!?…](?=\s)', rest)     # punctuation followed by a space: that sentence is over
            if not m:
                break
            out.append(rest[:m.end()].strip())
            self.sent += m.end()
        if closed:
            out.append(val[self.sent:].strip())
            self.sent, self.done = len(val), True
        return [x for x in out if x]


def progress_text(name, inp):
    """What Claude is doing right now, in words the owner can hear while they wait (never paths, ids or patterns)."""
    n = str(name).lower()
    path = str(inp.get('file_path') or inp.get('path') or inp.get('pattern') or '')
    def where(p):
        p = p.lower()
        if '/memory' in p: return 'your notes'
        if '/roundups' in p: return 'the latest messages'
        if '/work/' in p or p.endswith('/work'): return 'your inbox items'
        if 'tickets' in p: return 'the tickets'
        if 'approvals' in p: return 'the approvals'
        if any(p.startswith(d.lower()) for d in read_dirs()[:-2]): return 'your work files'
        return 'the files'
    if 'slack' in n: return 'Checking Slack'
    if n.startswith('mcp__jarvis__'): return 'Checking my records'
    if 'jira' in n or 'atlassian' in n and 'confluence' not in n: return 'Checking Jira'
    if 'confluence' in n: return 'Checking Confluence'
    if 'outlook' in n or 'email' in n: return 'Checking your email'
    if 'teams' in n or 'chat_message' in n: return 'Checking Teams'
    if 'sharepoint' in n: return 'Checking SharePoint'
    if n == 'read': return 'Reading ' + where(path)
    if n == 'grep': return 'Searching ' + where(path)
    if n == 'glob': return 'Looking through ' + where(path)
    return 'Working on it'


# ---------------------------------------------------------------- the brain: warm Claude processes (settings: warm_brain, fast_model)
# Model switcher ("switch to Opus" and it does): voice/settings.json holds the model per
# tier: brain_fast (quick spoken answers), brain_deep (the deep look) and brain_work (tasks Jarvis starts and the approvals
# worker; empty = Claude's own default). Changed by voice ({"type":"set_model"}), the landing picker or /api/jarvis_model.
TIER_DEFAULT = {'fast': 'haiku', 'deep': 'sonnet', 'work': ''}
TIER_NAME = {'fast': 'quick answers', 'deep': 'deep questions', 'work': 'tasks'}


def model_name(tier, cfg=None):
    n = str((cfg if cfg is not None else _settings()).get('brain_' + tier) or TIER_DEFAULT[tier])
    return n if n in MODELS else TIER_DEFAULT[tier]


def models_state():
    cfg = _settings()
    # tasks with no model chosen run on Claude's own default: Opus 5.5 on this account (checked 2 Oct: claude-opus-5-5[1m])
    return {'fast': model_name('fast', cfg), 'deep': model_name('deep', cfg), 'work': model_name('work', cfg) or 'default', 'work_default': 'Opus 5.5',
            'names': {k: v[1] for k, v in MODELS.items()}, 'defaults': TIER_DEFAULT}


def switch_model(tier, model):
    """Sets the model for a tier (fast | deep | work | thinking = deep + work | all), or 'default' for all three, after one
    quick check that the model answers on this plan. Writes voice/settings.json (merged, atomic). Returns the new state."""
    if model == 'default':
        patch = {'brain_fast': None, 'brain_deep': None, 'brain_work': None}
    else:
        if model not in MODELS:
            return {'ok': False, 'error': 'I only know Haiku, Sonnet, Opus and Fable.'}
        tiers = {'thinking': ['deep', 'work'], 'all': ['fast', 'deep', 'work']}.get(tier, [tier])
        if any(t not in TIER_DEFAULT for t in tiers):
            return {'ok': False, 'error': 'unknown part of the brain'}
        try:
            p = subprocess.run([CLAUDE, '-p', 'Reply with the single word OK.', '--model', MODELS[model][0], '--max-turns', '1'],
                               cwd=HUB, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            if p.returncode != 0 or 'ok' not in (p.stdout or '').lower():
                return {'ok': False, 'error': f"{MODELS[model][1]} didn't answer on your plan, so nothing changed."}
        except Exception as e:
            return {'ok': False, 'error': f"Couldn't check {MODELS[model][1]} ({e.__class__.__name__}), so nothing changed."}
        patch = {'brain_' + t: model for t in tiers}
    path = os.path.join(VOICE, 'settings.json')
    try:
        cur = json.load(open(path))
    except Exception:
        cur = {}
    for k, v in patch.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    tmp = path + '.models.tmp'
    json.dump(cur, open(tmp, 'w'), indent=2, sort_keys=True)
    os.replace(tmp, path)
    return {'ok': True, **models_state()}


def model_id(tier, cfg=None):
    n = model_name(tier, cfg)
    return MODELS[n][0] if n else None


# said while the deep look starts (varied; the quick tier usually writes its own line, often with a related "by the way")
DEEP_LINES = ["Let me look into that properly.", "Give me a moment to dig into it.", "Let me check that properly.", "Okay, let me go through it."]
# "do / run / prepare ... the analysis" is work to hand off (new_task), not a question for the deep tier: let the quick tier see it
WORK_HINT = re.compile(r"^\s*(?:(?:please|ok|okay|so|jarvis|hey jarvis|can you|could you|would you|will you|i want you to|i need you to|go ahead and)[,\s]+)*(?:do(?!\s+(?:you|i|we|they|it|this|that|so|not|n't)\b)|run|prepare|create|build|write up|draft|put together|analy[sz]e|work out|redo|re-?run)\b\s+\S+", re.I)
_LEAD = re.compile(r"^\s*(?:(?:please|ok|okay|so|jarvis|hey jarvis|can you|could you|would you|will you|i want you to|i need you to|go ahead and)[,\s]+)*", re.I)


def _handoff(q, screen, c):
    """The owner's words are work to do: a background task at once, no brain call (the quick model kept researching or
    asking "which metric?" instead). The read-back is their own words ("I'll run the scenario for March in the background."); the worker
    gets their words plus what was on screen and the last thing said."""
    phrase = _LEAD.sub('', q).strip().rstrip('?.! ')
    phrase = re.sub(r"\bmy\b", "your", re.sub(r"\bme\b", "you", phrase, flags=re.I), flags=re.I)
    phrase = phrase[0].lower() + phrase[1:] if phrase else 'that'
    ctx = []
    sel = (screen or {}).get('selected') or {}
    on = str(sel.get('title') or '').strip()
    if on:
        who = str(sel.get('who') or '')
        src = 'their own ask to Jarvis' if re.match(r'you\b', who, re.I) else f'from {who}' if who else 'in their inbox'
        ctx.append(f"On their screen: the item \"{on}\" ({src}; round-up {sel.get('rid')}, item {sel.get('aid')}).")
    if c.get('turns'):
        t = c['turns'][-1]
        ctx.append(f"Just before, they asked \"{(t.get('q') or '')[:200]}\" and Jarvis said \"{(t.get('speak') or '')[:200]}\".")
    text = q.strip() + ('\n\n' + ' '.join(ctx) if ctx else '')
    # "run it for March" must not become the title, nor the worker's brief the card. When the words lean on the item on
    # screen (it, this, that, the same), the task is named after that item, and the card shows his ask as one clean line.
    vague = bool(re.search(r'\b(it|this|that|these|those|the same)\b', phrase, re.I)) or len(phrase.split()) <= 3
    title = (f'{on}: {phrase}' if on and vague else phrase)[:80]
    ask = phrase[0].upper() + phrase[1:]
    point = (f'{ask} (on "{on}")' if on and vague else ask)[:200]
    return {'speak': 'On it.', 'text': f'Working on it in the background: {title}.',
            'actions': [{'type': 'new_task', 'title': title, 'text': text[:2000], 'points': [point]}]}


DEEP_HINT = re.compile(r"\b(compar\w*|analy[sz]\w*|investigat\w*|dig into|look into|deep dive|summari[sz]e (all|every|the whole)|go through (all|every)|across (all|every)|root cause|why (did|does|is|are|was|were))\b", re.I)
NO_THINKING = json.dumps({'alwaysThinkingEnabled': False})
# The warm-up is cut off as soon as the model starts its reply (the prompt is cached by then), so no warm-up answer stays in the
# conversation for the quick model to copy: "reply {}" made it answer "thanks" with {}, "reply Ready." made it say "Ready." (2 Oct)
IDLE_CLOSE, WARMUP = 600, '(System warm-up, not from the owner. No reply needed.)'
RECYCLE_TURNS, RECYCLE_CTX = 40, 140_000   # a process this deep in a conversation is replaced between turns (with a recap)
KEEPALIVE_S = 270                      # 4.5 min: inside the ~5 min prompt-cache lifetime


def _brain_cmd(model, fast):
    cmd = [CLAUDE, '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
           '--system-prompt', system_prompt(fast), '--permission-mode', 'default', '--tools', 'Read,Grep,Glob',
           '--allowedTools', *read_tools(), '--disallowedTools', *deny_tools(),
           '--add-dir', *read_dirs(), '--mcp-config', *mcp_configs()]
    if fast:
        cmd += ['--settings', NO_THINKING]       # the quick voice layer answers without extended thinking (~0.5 s sooner)
    return cmd + (['--model', model] if model else [])


class Brain:
    """One warm Claude Code process (stream-json in and out). One turn at a time; an abandoned turn is interrupted before the next."""

    def __init__(self, model, fast):
        import queue
        self.model, self.fast, self.base_model = model, fast, model
        self.q, self.ctl = queue.Queue(), queue.Queue()
        self.lock = threading.Lock()
        self.pending, self.session, self.turns = 0, None, 0
        self.born = self.last = self.last_real = time.time()
        self.last_ping = 0.0
        self.ready = threading.Event()        # the warm-up turn has come back (process + prompt cache are hot)
        self.dead = False
        self.cancel_req = False               # a newer request for this conversation wants the current turn stopped
        self.static = ''                      # the profile/style block this process has been given (jarvis_human.before)
        self.seen_conv, self.ctx = None, 0     # the conversation whose turns this process holds; its last context size
        self.p = subprocess.Popen(_brain_cmd(model, fast), cwd=HUB, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, env=MCP_ENV)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.p.stdout:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get('type') == 'control_response':
                    self.ctl.put(d)
                elif d.get('type') == 'rate_limit_event':       # Claude's own meter: the governor's window (spine/governor.py)
                    try:
                        from jarvis.spine import governor
                        governor.note_window(d.get('rate_limit_info'))
                    except Exception:
                        pass
                else:
                    if d.get('type') == 'system' and d.get('session_id'):
                        self.session = d['session_id']
                    if d.get('type') == 'system' and d.get('subtype') == 'init' and isinstance(d.get('mcp_servers'), list):
                        try:                           # connector state for deep /healthz (names and status only)
                            p_ = os.path.join(HUB, 'state', 'connectors.json')
                            json.dump({'at': time.time(), 'servers': [{'name': x.get('name'), 'status': x.get('status')} for x in d['mcp_servers']]},
                                      open(p_ + '.tmp', 'w'))
                            os.replace(p_ + '.tmp', p_)
                        except Exception:
                            pass
                    self.q.put(d)
        finally:
            self.dead = True
            self.q.put({'type': '_eof'})

    def alive(self):
        return not self.dead and self.p.poll() is None

    def _write(self, o):
        try:
            self.p.stdin.write(json.dumps(o) + '\n'); self.p.stdin.flush(); return True
        except Exception:
            self.dead = True; return False

    def control(self, subtype, timeout=5, **kw):
        rid = uuid.uuid4().hex[:8]
        if not self._write({'type': 'control_request', 'request_id': rid, 'request': dict(subtype=subtype, **kw)}):
            return False
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                d = self.ctl.get(timeout=max(0.05, timeout - (time.time() - t0)))
            except Exception:
                break
            r = d.get('response') or {}
            if r.get('request_id') == rid:
                return r.get('subtype') == 'success'
        return False

    def set_model(self, m):
        if m == self.model:
            return True
        ok = self.control('set_model', model=m)
        if ok:
            self.model = m
        return ok

    def _drain(self, timeout):
        """Waits for the results of turns nobody is reading any more."""
        t0 = time.time()
        while self.pending > 0 and time.time() - t0 < timeout:
            try:
                d = self.q.get(timeout=0.1)
            except Exception:
                continue
            if d.get('type') == 'result':
                self.pending -= 1
            elif d.get('type') == '_eof':
                return False
        return self.pending <= 0

    def turn(self, text, on_event, timeout=TIMEOUT):
        """Sends one user message and feeds every event to on_event until its result. on_event returning False (the page went
        away) interrupts the turn. Returns the result event, or None."""
        if self.lock.locked():
            self.cancel_req = True               # the turn in progress is stale: stop it so this one can go
        with self.lock:
            self.cancel_req = False
            if self.pending > 0 and not self._drain(2.5):
                self.control('interrupt', timeout=2)
                if not self._drain(3):
                    self.close(); return None
            # clear stale events
            while not self.q.empty():
                try:
                    if self.q.get_nowait().get('type') == '_eof':
                        self.dead = True
                except Exception:
                    break
            if not self.alive() or not self._write({'type': 'user', 'message': {'role': 'user', 'content': text}}):
                return None
            self.pending += 1
            self.turns += 1
            self.last = time.time()
            if text != WARMUP:
                self.last_real = self.last            # his turns only: keep-alive pings never count as activity
            t0 = time.time()
            stop = superseded = False
            while True:
                left = timeout - (time.time() - t0)
                if left <= 0:
                    self.control('interrupt', timeout=2)
                    return None if not self._drain(3) else {'type': 'result', 'result': None, 'timed_out': True}
                if self.cancel_req and not stop:
                    stop = superseded = True
                    self.control('interrupt', timeout=2)
                try:
                    d = self.q.get(timeout=min(left, 0.2))
                except Exception:
                    continue
                k = d.get('type')
                if k == '_eof':
                    self.pending = 0; return None
                if k == 'result':
                    self.pending -= 1; self.last = time.time()
                    if superseded:                   # a newer request took over: nothing of this turn is kept or sent
                        return dict(d, superseded=True)
                    on_event(d)
                    return d
                if not stop and on_event(d) is False:
                    stop = True
                    self.control('interrupt', timeout=2)   # nobody is listening: stop spending on it

    def warm(self):
        """A tiny first turn so the process and the prompt cache are hot before they speak."""
        started = lambda d: d.get('type') == 'stream_event' and (d.get('event') or {}).get('type') == 'message_start'
        r = self.turn(WARMUP, lambda d: not started(d), timeout=60)   # False at the reply's start = interrupt it there
        if r is not None:
            self.ready.set()
        return r is not None

    def close(self):
        self.dead = True
        try:
            self.p.stdin.close()
        except Exception:
            pass
        p = self.p
        threading.Timer(3, lambda: p.poll() is None and p.kill()).start()


class Pool:
    """ONE warm spare (taken by the next conversation, replaced at once in the background) + the current conversation's process.
    At most 2 processes: a new conversation closes the previous conversation's process first. The deep tier is the conversation's
    own process switched to the deep model for that turn (so it keeps the whole conversation), then switched back."""

    def __init__(self):
        self.lock = threading.Lock()
        self.spare = None
        self.conv = {}            # cid -> Brain
        self.cfg_key = None
        threading.Thread(target=self._reaper, daemon=True).start()

    def _key(self, cfg):
        fast = bool(cfg.get('fast_model'))
        return (model_id('fast', cfg) if fast else model_id('deep', cfg), fast)

    def _spawn(self, key):
        b = Brain(*key)
        threading.Thread(target=b.warm, daemon=True).start()
        return b

    def ensure_spare(self, cfg):
        key = self._key(cfg)
        with self.lock:
            if self.cfg_key != key:               # settings changed: the old spare has the wrong model or prompt
                if self.spare:
                    self.spare.close()
                self.spare, self.cfg_key = None, key
            if self.spare and not self.spare.alive():
                self.spare = None
            if not self.spare and len([b for b in self.conv.values() if b.alive()]) < 2:
                self.spare = self._spawn(key)
            return self.spare

    def get(self, cid, cfg):
        """The conversation's warm process (taking the spare for a new conversation)."""
        key = self._key(cfg)
        with self.lock:
            b = self.conv.get(cid)
            if b and b.alive() and (b.base_model, b.fast) == key and (b.turns >= RECYCLE_TURNS or b.ctx >= RECYCLE_CTX) \
                    and not b.lock.locked():
                # Phase 3 recycling: a long conversation moves to a fresh process between turns, with a recap (_recap),
                # so Claude Code never compacts in the middle of his turn
                print(f'brain recycle: {cid} after {b.turns} turns, context {b.ctx}', flush=True)
                b.close(); self.conv.pop(cid, None); b = None
            if b and b.alive() and (b.base_model, b.fast) == key:
                return b, False
            for k, old in list(self.conv.items()):     # one conversation at a time: the old one's process goes
                old.close(); del self.conv[k]
            sp = self.spare if (self.spare and self.spare.alive() and self.cfg_key == key) else None
            self.spare = None
            b = sp or Brain(*key)
            self.conv[cid] = b
        # a new spare starts warming right away, off this request's path (starting a CLI process costs ~0.5 s)
        threading.Thread(target=self.ensure_spare, args=(cfg,), daemon=True).start()
        return b, sp is not None

    def end(self, cid):
        with self.lock:
            b = self.conv.pop(cid, None)
        if b:
            b.close()

    def prewarm(self, cid, cfg):
        """They have just woken Jarvis: make sure the process that will answer has a hot prompt cache."""
        if not cfg.get('warm_brain'):
            return {'ok': True, 'skipped': 'warm_brain off'}
        with self.lock:
            b = self.conv.get(cid) if cid else None
        b = b if (b and b.alive()) else self.ensure_spare(cfg)
        if b and b.pending == 0 and not b.lock.locked() and time.time() - b.last > 240:
            threading.Thread(target=b.warm, daemon=True).start()
            return {'ok': True, 'warming': True}
        return {'ok': True, 'warming': False}

    def _reaper(self):
        while True:
            time.sleep(20)
            now = time.time()
            with self.lock:
                for k, b in list(self.conv.items()):
                    if not b.alive() or (now - b.last_real > IDLE_CLOSE and not b.lock.locked()):
                        b.close(); del self.conv[k]
                    elif KEEPALIVE_S <= now - b.last_real < IDLE_CLOSE - 30 and now - max(b.last_ping, b.last_real) >= KEEPALIVE_S \
                            and not b.lock.locked() and b.ready.is_set():
                        # Phase 3 keep-alive: the prompt cache lives ~5 min; a cold first turn after a pause took ~3.3 s to the
                        # first word against ~1.4 s warm (91 turns, 5 Oct). One silent warm-up keeps it hot while he is around.
                        b.last_ping = now
                        threading.Thread(target=b.warm, daemon=True).start()
                if self.spare and not self.spare.alive():
                    self.spare = None

    def status(self):
        with self.lock:
            return {'spare': bool(self.spare and self.spare.alive()), 'spare_ready': bool(self.spare and self.spare.ready.is_set()),
                    'conversations': {k: {'model': b.model, 'turns': b.turns, 'idle_s': round(time.time() - b.last)} for k, b in self.conv.items()}}


POOL = Pool()


# Each piece of the 2 Oct voice upgrade is a switch in voice/settings.json; a missing key means its default here.
DEFAULTS = {'warm_brain': True, 'fast_model': True}


def _settings():
    try:
        d = json.load(open(os.path.join(VOICE, 'settings.json')))
    except Exception:
        d = {}
    return {**DEFAULTS, **(d if isinstance(d, dict) else {})}


def warm_at_start():
    """Called once by server.py: start the warm spare if warm_brain is on."""
    cfg = _settings()
    if cfg.get('warm_brain'):
        POOL.ensure_spare(cfg)


class TurnReader:
    """Turns the CLI's stream-json events into the page's NDJSON: progress per tool, speak per finished sentence.
    With `budget` (the quick tier), a turn that turns into research (a 3rd tool call, or 6 s of tools with nothing said) is
    stopped and flagged for the deep tier."""

    def __init__(self, emit, budget=False, tag=None):
        self.emit, self.ss, self.last_text, self.final, self.session, self.alive, self.spoke = emit, SpeakStream(), '', None, None, True, 0
        self.tag = tag                         # {conv, stage}: the turn's usage goes to brain_usage.ndjson
        self.budget, self.tools, self.t0, self.escalate_now = budget, 0, time.time(), False
        self.fillers = 0
        self.ctx = 0

    def __call__(self, d):
        ok = self._take(d)
        if self.budget and not self.escalate_now and self.spoke == 0 and (self.tools >= 3 or (self.tools >= 1 and time.time() - self.t0 > 6)):
            self.escalate_now = True
        return ok and not self.escalate_now

    def _take(self, d):
        k = d.get('type')
        if k == 'system' and d.get('session_id'):
            self.session = d['session_id']
        elif k == 'stream_event':
            ev = d.get('event') or {}
            if ev.get('type') == 'message_start':
                self.ss = SpeakStream()      # a new assistant message (after tool use): the JSON reply starts over
            delta = ev.get('delta') or {}
            if delta.get('type') == 'text_delta':
                for s in self.ss.feed(delta.get('text', '')):
                    if re.match(r'\s*\{\s*"actions"\s*:\s*\[\s*\{\s*"type"\s*:\s*"(new_task|start_task)"', self.ss.buf):
                        continue                           # a task reply: the page reads the task back; nothing said twice
                    if re.match(r'\s*\{\s*"escalate"\s*:\s*true', self.ss.buf):
                        self.fillers += 1                  # the quick tier's hand-over line: keeps the wait, not the answer
                        self.alive = self.emit({'type': 'speak', 'text': s, 'filler': True}) and self.alive
                        continue
                    self.spoke += 1
                    mm = re.search(r'"mood"\s*:\s*"(\w+)"', self.ss.buf)
                    mood = mm.group(1) if mm and mm.group(1) in MOODS else None
                    self.alive = self.emit({'type': 'speak', 'text': s, **({'mood': mood} if mood else {})}) and self.alive
        elif k == 'assistant':
            for blk in (d.get('message') or {}).get('content') or []:
                if blk.get('type') == 'text':
                    self.last_text = blk.get('text', '')
                elif blk.get('type') == 'tool_use':
                    self.tools += 1
                    TURN_TOOLS.setdefault((self.tag or {}).get('conv', ''), []).append(blk.get('name', ''))   # for grounding v2
                    what = progress_text(blk.get('name', ''), blk.get('input') or {})
                    if what:
                        self.alive = self.emit({'type': 'progress', 'text': what}) and self.alive
        elif k == 'result':
            self.session = d.get('session_id') or self.session
            self.final = d.get('result') or self.last_text
            if self.tag is not None:
                usage_log(self.tag, d)
                u_ = d.get('usage') or {}               # the context one call carried, on average (recycling, Phase 3)
                self.ctx = (u_.get('input_tokens', 0) + u_.get('cache_read_input_tokens', 0) + u_.get('cache_creation_input_tokens', 0)) // max(1, d.get('num_turns') or 1)
        return self.alive


def handle(body, headers, wfile, cfg):
    """Streams NDJSON lines to wfile. The caller has sent the headers already."""
    def emit(o):
        try:
            wfile.write((json.dumps(o) + '\n').encode()); wfile.flush(); return True
        except Exception:
            return False
    cfg = {**DEFAULTS, **(cfg or {})}
    q = (body.get('q') or '').strip()
    screen = body.get('screen') or {}
    cid, c = _get_conv(body.get('conv') or '')
    emit({'type': 'ack', 'conv': cid})
    raw_emit = emit
    gate = speech_guard.Gate(raw_emit, cid)      # no claim without a receipt: claim sentences wait for the actions (spine/speech_guard.py)
    emit = gate.emit
    try:
        static, dyn = human.before(q, body, c, cid)   # (what you know about him: profile, style, corrections; this turn: time, memory, earlier moments, mood)
    except Exception as e:
        print('jarvis_human:', e, flush=True); static, dyn = '', ''
    pre = _tasks_line() + dyn                         # TASKS NOW: what runs now
    try:                                              # "let me know when X" becomes a tracked need now (world/needs.py)
        from jarvis.world import needs as _needs
        # 5 Oct (20:31): "Sure, let me know when it's done" about a task already running made 6 Watching needs, twice, at
        # his own (A3) standing: a second worker could have started on the same question. A watch phrase that names
        # nothing new ("it", "that one") refers to work Jarvis already reports on: no need. Once per conversation per 90 s.
        recent = time.time() - c.get('watch_mined', 0) < 90
        if watch_worthy(q) and not recent and body.get('dropped') is not True:
            c['watch_mined'] = time.time()
            ctx = (c['turns'][-1].get('q', '') + ' / ' + c['turns'][-1].get('speak', ''))[:400] if c.get('turns') else ''
            threading.Thread(target=_needs.mine_said, args=(q, 'voice:' + cid, ctx), daemon=True, name='need-said').start()
            pre += '(Jarvis now tracks this in Watching and will tell them when it turns up; you may say so.)\n'
    except Exception as e:
        print('need_said:', e, flush=True)
    if WORK_HINT.search(q) and body.get('dropped') is not True:
        r = _handoff(q, screen, c)
        with _lock:
            c['turns'].append({'at': dt.datetime.now().isoformat(timespec='seconds'), 'q': q, 'speak': r['speak'], 'text': r['text'], 'actions': r['actions'], 'tier': 'handoff'})
            _save_conv(cid, c)
        return emit({'type': 'final', 'conv': cid, 'secs': 0, 'tier': 'handoff', **r})
    note = ''
    heard = body.get('heard')
    if isinstance(heard, str) and heard.strip():
        # he cut Jarvis off: the last answer only counts up to what he actually heard
        note = '(They interrupted your last answer; they only heard: "%s")\n\n' % heard.strip()[:600]
        with _lock:
            if c['turns']:
                c['turns'][-1]['interrupted'] = True
                c['turns'][-1]['heard'] = heard.strip()[:600]
    if body.get('dropped') is True:
        # the app ended his turn too early and he went on: the previous message was only the start of this one
        note = '(They had not finished; ignore your answer to their previous message. Their whole message is:)\n\n' + note
        with _lock:
            if c['turns'] and q.lower().startswith((c['turns'][-1].get('q') or '').lower()[:40]):
                c['turns'].pop()
    carry = ''
    prev = body.get('prev_conv')
    if isinstance(prev, str) and ID.match(prev) and prev != cid and not c.get('turns'):
        # 5 Oct (The owner: "it has dementia"): his last question moved to the background, so this question opened a new
        # conversation; without this it did not know what had just been said, or that it promised to report back
        with _lock:
            pc = _convs.get(prev) or _load_conv(prev) or {}
            last = [t for t in (pc.get('turns') or [])[-4:] if t.get('q')]
        pend = str(body.get('pending_q') or '').strip()[:300]
        if last or pend:
            carry = ('EARLIER IN THIS CONVERSATION (same conversation, continued here because the last question is still being '
                     'worked on in the background):\n' + ''.join(f'- They said: "{t.get("q", "")[:200]}"; you answered: "{(t.get("speak") or "")[:240]}"\n' for t in last)
                     + (f'- STILL RUNNING in the background: "{pend}". You told them you would let them know when it is done; '
                        'the answer reaches them by itself when ready. If they say "let me know" or asks about it, they mean that one.\n' if pend else '')
                     + '\n')
            c['carried_from'] = prev
    prompt = carry + pre + note + q + '\n\nSCREEN ' + json.dumps(screen, ensure_ascii=False)[:MAX_SCREEN]
    t0 = time.time()
    tier, final, session = 'cold', None, None
    if cfg.get('warm_brain'):
        tier, final, session = _ask_warm(cid, c, q, prompt, screen, cfg, emit, static)
    if final is None and tier != 'gone':
        tier = 'cold' if tier == 'cold' else tier + '+cold'
        final, session = _ask_cold(c, static + prompt, cfg, emit, model=(model_id('fast', cfg) if cfg.get('fast_model') and not cfg.get('warm_brain') else None))
        if final is not None and cfg.get('fast_model') and not cfg.get('warm_brain') and parse_reply(final).get('escalate'):
            emit({'type': 'escalate'}); emit({'type': 'progress', 'text': 'Digging into it'})
            c['session'] = session or c['session']
            final, session = _ask_cold(c, '[DEEP] ' + prompt, cfg, emit, model=model_id('deep', cfg))
            tier = 'cold-deep'
    if tier == 'gone':
        return
    if final is None:
        timed_out = time.time() - t0 >= TIMEOUT - 1
        return emit({'type': 'error', 'error': 'That took too long, so I stopped.' if timed_out else 'Claude did not answer.'})
    r = parse_reply(final)
    r.pop('escalate', None)
    receipts = _do_here(r, screen, c, raw_emit, q)
    if tasks_now().get('running'):
        receipts.append('running')
    spoken = gate.finish(r['actions'], receipts, q)    # held claims, checked against what really runs; '' when nothing streamed
    try:                                               # grounding v2, log only: state claims vs what the turn looked at
        speech_guard.ground_log(spoken or r.get('speak', ''), TURN_TOOLS.pop(cid, []), cid, q)
    except Exception:
        pass
    if spoken:
        r['speak'] = spoken
    if not r['speak'].strip():
        print('jarvis: empty speak, raw reply %r' % (final or '')[:300], flush=True)
        # never a silent answer: say the first sentence of the screen text, or admit it
        first = re.split(r'(?<=[.!?])\s', re.sub(r'[*#`|_>-]+', ' ', r['text']).strip())[0][:200].strip() if r['text'].strip() else ''
        r['speak'] = first or ("Done." if r['actions'] else "Sorry, I lost that one. Say it again?")
    if not spoken:                                     # spoken in one piece by the page (finalize): the same check on the whole answer
        r['speak'] = speech_guard.guard_text(r['speak'], r['actions'], receipts) or r['speak']
    # model switch: quick answers change only when he says so, and nothing is claimed done before the page has run it
    for a in r['actions']:
        if a.get('type') == 'set_model':
            if a.get('model') != 'default' and a.get('tier') in ('all', 'fast') and not re.search(r'\b(everything|all of (it|them)|every part|quick|fast|whole brain|all models)\b', q, re.I):
                a['tier'] = 'thinking'
            r['speak'] = 'Okay.'
    with _lock:
        c['session'] = session or c['session']
        c['turns'].append({'at': dt.datetime.now().isoformat(timespec='seconds'), 'q': q, 'speak': r['speak'], 'text': r['text'], 'actions': r['actions'], 'tier': tier,
                           **({'voice': body['voice']} if isinstance(body.get('voice'), dict) else {})})   # how he sounded (laugh, sigh, pitch...)
        _save_conv(cid, c)
    emit({'type': 'final', 'conv': cid, 'secs': round(time.time() - t0, 1), 'tier': tier, **r})


def _tasks_line():
    """TASKS NOW: what Jarvis's worker runs and has queued (the brain must not guess it from the inbox)."""
    tn = tasks_now()
    if not (tn['running'] or tn['queue']):
        return ''
    r0 = tn['running']
    line = 'TASKS NOW: ' + (f"running \"{r0['title']}\" (round-up {r0['rid']}, item {r0['aid']}, started {r0['started'][11:16]})" if r0 else 'nothing running')
    return line + ''.join(f"; queued \"{x['title']}\" ({x['rid']} {x['aid']})" for x in tn['queue'][:3]) + '\n'


def _do_here(r, screen, c, emit, q=None):
    """The actions done here, not by the page: ask_claude (a background consult), remember / forget / remind. Each leaves a
    line in "text"; a reminder that did not take is also spoken. Returns the receipts: the types that really took effect."""
    keep, receipts = [], []
    for a in r['actions']:
        t = a['type']
        if t == 'ask_claude':
            sel = (screen or {}).get('selected') or {}
            ctx = (f"their screen shows {sel.get('who', '')}'s item \"{sel.get('title', '')}\" ({sel.get('rid')} {sel.get('aid')}). " if sel.get('title') else '') + \
                  ' '.join(f"Earlier they asked \"{x.get('q', '')[:160]}\", Jarvis said \"{x.get('speak', '')[:160]}\"." for x in c['turns'][-2:])
            consult(a['question'], ctx)
            said = ''                                  # plumbing, not a message: the chat shows the answer when it is back (5 Oct)
            receipts.append(t)
        elif t == 'run_now':
            from jarvis import actions
            r0 = actions.run_now(a['what'])
            said = {'roundup': 'Checking your messages now', 'work': 'Working on your inbox now'}[a['what']] + (' (already running)' if r0.get('state') == 'running' else '') + '.'
            if r0.get('ok'):
                receipts.append(t)
            else:
                said = "Couldn't start the %s run: %s." % (a['what'], r0.get('error', 'unknown error'))
        elif t in ('remember', 'forget', 'remind'):
            said = human.apply(a, q)
            if t == 'remind' and said and not said.startswith("I'll remind"):
                emit({'type': 'speak', 'text': said})
            if (t == 'remember' and said in ('Noted.', 'I already have that.')) or (t == 'forget' and said == 'Forgotten.') or \
               (t == 'remind' and said.startswith("I'll remind")):
                receipts.append(t)
        else:
            keep.append(a); continue
        if said:
            r['text'] = (r['text'] + '\n' if r['text'] else '') + said
    r['actions'] = keep
    return receipts


def _recap(c, n=6):
    """The conversation so far, for a process that has not seen it (5 Oct: "it has dementia"). Empty for a new one."""
    turns = [t for t in (c.get('turns') or [])[-n:] if t.get('q')]
    if not turns:
        return ''
    return ('THIS CONVERSATION SO FAR (you are continuing it; it moved to a fresh process):\n'
            + ''.join(f'- They said: "{t.get("q", "")[:220]}"; you answered: "{(t.get("speak") or "")[:260]}"\n' for t in turns) + '\n')


def _ask_warm(cid, c, q, prompt, screen, cfg, emit, static=''):
    """Returns (tier, final_text or None, session). tier 'gone' = the page went away (nothing more to send). `static` (profile,
    style, corrections) goes in front of the first message this process sees, and again only when it has changed."""
    try:
        b, _ = POOL.get(cid, cfg)
    except Exception:
        return 'warm-failed', None, None
    lead = static if static and static != b.static else ''
    b.static = static or b.static
    if b.seen_conv != cid:                       # a process new to this conversation (recycled, reaped, restarted, crashed):
        lead += _recap(c)                        # it gets the conversation so far, so it never forgets mid-conversation
        b.seen_conv = cid
    fast = bool(cfg.get('fast_model'))
    rd = TurnReader(emit, budget=fast, tag={'conv': cid, 'stage': 'fast' if fast else 'warm'})
    tier = 'fast' if fast else 'warm'
    if fast and DEEP_HINT.search(q) and not WORK_HINT.search(q):
        # obviously deep: say so at once and go straight to the deep model (a line that keeps the wait, not the answer)
        emit({'type': 'escalate'}); emit({'type': 'speak', 'text': random.choice(DEEP_LINES), 'filler': True})
        tier = 'deep'
    else:
        if fast and not b.set_model(model_id('fast', cfg)):
            return 'warm-failed', None, None
        r = b.turn(lead + prompt, rd)
        b.ctx = rd.ctx or b.ctx
        lead = ''                                    # the deep look below is the same process: it has seen it
        if r is None:
            POOL.end(cid)
            return ('gone' if not rd.alive else 'warm-failed'), None, None
        if not rd.alive or r.get('superseded'):        # the page left, or his next words replaced this turn (dropped/corrected)
            return 'gone', None, None
        if rd.escalate_now:
            # it started researching: say so, and let the deep model carry on from what it found
            emit({'type': 'escalate'})
            if not rd.fillers: emit({'type': 'speak', 'text': random.choice(DEEP_LINES), 'filler': True})
            tier = 'fast>>deep'
        elif not (fast and parse_reply(rd.final or '').get('escalate')):
            return tier, rd.final, rd.session or b.session
        else:
            emit({'type': 'escalate'})
            tier = 'fast>deep'
    emit({'type': 'progress', 'text': 'Digging into it'})
    if not b.set_model(model_id('deep', cfg)):
        return 'warm-failed', None, None
    if fast:
        b.control('set_max_thinking_tokens', max_thinking_tokens=8000)   # the deep look may think
    rd2 = TurnReader(emit, tag={'conv': cid, 'stage': 'deep'})
    r = b.turn('[DEEP] ' + lead + prompt, rd2)
    b.ctx = rd2.ctx or b.ctx
    def back():                                  # back to the quick model, without thinking, for the next turn
        if fast:
            b.control('set_max_thinking_tokens', max_thinking_tokens=None)
        b.set_model(model_id('fast', cfg))
    threading.Thread(target=back, daemon=True).start()
    if r is None:
        POOL.end(cid)
        return ('gone' if not rd2.alive else 'warm-failed'), None, None
    if not rd2.alive or r.get('superseded'):
        return 'gone', None, None
    return tier, rd2.final, rd2.session or b.session


def drop(body):
    """POST /api/jarvis_drop {conv, q}: the page took back a question it had sent (the app dropped or corrected that turn, and
    the new words may be answered on the page itself). A turn still running for it is stopped and never recorded; one already
    recorded with exactly that question is removed."""
    cid, q = body.get('conv'), body.get('q')
    if not isinstance(cid, str) or not ID.match(cid) or not isinstance(q, str) or not q.strip():
        return {'ok': False, 'error': 'bad request'}
    with POOL.lock:
        b = POOL.conv.get(cid)
    if b and b.lock.locked():
        b.cancel_req = True
    removed = False
    with _lock:
        c = _convs.get(cid)
        if c and c.get('turns') and (c['turns'][-1].get('q') or '') == q.strip():
            c['turns'].pop(); _save_conv(cid, c); removed = True
    return {'ok': True, 'stopped': bool(b and b.cancel_req), 'removed': removed}


def _ask_cold(c, prompt, cfg, emit, model=None):
    """The original path: one headless Claude run per turn (--resume keeps the conversation)."""
    cmd = [CLAUDE, '-p', prompt, '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
           '--system-prompt', system_prompt(bool(cfg.get('fast_model'))), '--permission-mode', 'default', '--tools', 'Read,Grep,Glob',
           '--allowedTools', *read_tools(), '--disallowedTools', *deny_tools(),
           '--add-dir', *read_dirs(), '--mcp-config', *mcp_configs()]
    if model == model_id('fast', cfg):
        cmd += ['--settings', NO_THINKING]
    cmd += ['--model', model or model_id('deep', cfg)]
    if c['session']:
        cmd += ['--resume', c['session']]
    try:
        p = subprocess.Popen(cmd, cwd=HUB, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, env=MCP_ENV)
    except Exception as e:
        emit({'type': 'error', 'error': 'Could not start Claude: ' + str(e)})
        return None, None
    killer = threading.Timer(TIMEOUT, p.kill); killer.start()
    rd = TurnReader(emit, tag={'conv': c.get('id', ''), 'stage': 'cold'})
    try:
        for line in p.stdout:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if rd(d) is False:
                p.kill(); break
    finally:
        killer.cancel()
        p.wait()
    return rd.final, rd.session


# ---------------------------------------------------------------- background work: tasks, notes, consults
# Workers run in lanes (spine/dispatcher.py, HARNESS.md Phase 2): what he asks for is the 'asked' lane and starts at once, up to
# 3 in parallel (background work is paused to make room); every run is a row in the ledger (state/jarvis.db).
from jarvis.spine import dispatcher  # noqa: E402
dispatcher.model_for_work = lambda: model_id('work')


NOTES = os.path.join(HUB, 'work', 'notes')
CONSULTS = os.path.join(VOICE, 'consults')


def steer(rid, aid, text):
    """Their words for the worker running on that item (Jarvis "tell Claude ..."): appended to work/notes/<rid>-<aid>.jsonl, which
    the worker re-reads between steps (jarvis-inbox-work skill, "Notes from the owner while you work")."""
    os.makedirs(NOTES, exist_ok=True)
    with open(os.path.join(NOTES, f'{rid}-{aid}.jsonl'), 'a') as f:
        f.write(json.dumps({'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'text': text[:2000]}, ensure_ascii=False) + '\n')


def consult(question, context=''):
    """Jarvis asks a full Claude (the tasks model, Opus by default) to find something out, in the background and read-only.
    The answer lands in voice/consults/<id>.json; the page says it when it is back (/api/vpn "answers")."""
    os.makedirs(CONSULTS, exist_ok=True)
    cid = dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    path = os.path.join(CONSULTS, cid + '.json')
    rec = {'id': cid, 'q': question[:600], 'state': 'running', 'started': dt.datetime.now().astimezone().isoformat(timespec='seconds')}
    json.dump(rec, open(path, 'w'), indent=1)
    prompt = (f'{config.load().owner_or()} asked Jarvis, their voice assistant, and Jarvis needs you to find out: "{question}".\n' + (f'Context: {context}\n' if context else '')
              + 'Find the real answer: read files, run read-only commands and queries, search the connected apps (messages, tickets, '
              'pages, mail). CHANGE NOTHING: no file writes outside the temporary folder, no database writes, no messages, no ticket or '
              'page changes. If a system does not answer, say which one and that it could not be reached. Then reply with ONLY a JSON object: {"speak": "<the answer for the ear: lead with the answer itself in one short '
              'sentence (under 25 words), at most one more; no ids or paths>", "text": "<the details for the screen as 3-8 short '
              'Markdown bullets (under 700 characters in all): the exact numbers, names and dates, and where each came from>"}')
    def run():
        wm = model_id('work')
        try:
            # research only (5 Oct review: this run had Slack's send tools and no hook): every write tool off, worker hook on
            writes = write_tools()
            p = subprocess.run([CLAUDE, '-p', prompt, '--permission-mode', 'auto', '--output-format', 'text',
                                '--disallowedTools', 'Edit', 'Write', 'NotebookEdit', *writes,
                                '--settings', os.path.join(HUB, 'state', 'worker-settings.json')] + (['--model', wm] if wm else []),
                               cwd=str(paths.workdir()), capture_output=True, text=True, timeout=900, env=dict(MCP_ENV, JARVIS_PROC='1', JARVIS_LANE='consult'))
            r = parse_reply(p.stdout or '')
            full = r['text'] or (p.stdout or '')[-4000:]
            sp = ' '.join(re.split(r'(?<=[.!?])\s+', r['speak'].strip())[:2])      # spoken: two sentences at most
            rec.update(state='done' if (p.stdout or '').strip() else 'failed', speak=sp, text=shape_points(full), text_full=full)
        except Exception as e:
            rec.update(state='failed', speak='', text=str(e)[:300])
        rec['ended'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        json.dump(rec, open(path + '.tmp', 'w'), indent=1); os.replace(path + '.tmp', path)
    threading.Thread(target=run, daemon=True, name='consult').start()
    return cid


def consults_ready():
    out = []
    if os.path.isdir(CONSULTS):
        for f in sorted(os.listdir(CONSULTS))[-50:]:
            if f.endswith('.json'):
                try:
                    r = json.load(open(os.path.join(CONSULTS, f)))
                except Exception:
                    continue
                if r.get('state') in ('done', 'failed') and not r.get('delivered'):
                    out.append({k: r.get(k) for k in ('id', 'q', 'state', 'speak', 'text')})
    return out


def consult_delivered(cid):
    p = os.path.join(CONSULTS, re.sub(r'[^\w-]', '', cid) + '.json')
    try:
        r = json.load(open(p)); r['delivered'] = True; json.dump(r, open(p, 'w'), indent=1)
    except Exception:
        pass


def tasks_now():
    """What Jarvis's workers run now and what waits: {running: newest {rid, aid, title, started, lane}, running_all, queue}.
    From the ledger; workers started before a server restart are still found (by pid in the ledger, or by command line)."""
    def title(rid, aid):
        try:
            d = json.load(open(os.path.join(HUB, 'roundups', rid + '.json')))
            a = next((x for x in d.get('actions', []) if x.get('id') == aid), {})
            return (a.get('title') or a.get('text') or '')[:90]
        except Exception:
            return ''
    fmt = lambda t: {'rid': t['rid'], 'aid': t['aid'], 'title': title(t['rid'], t['aid']) or (t.get('title') or '')[:90],
                     'started': t.get('started') or '', 'lane': t.get('lane') or 'asked'}
    try:
        from jarvis.world.desk import is_desk                  # Jarvis's own desk work stays with it until the brief
        cur = [fmt(t) for t in dispatcher.running() if t.get('rid') and not is_desk(t['rid'])]
        q = [fmt(t) for t in dispatcher.queued() if t.get('rid') and not is_desk(t['rid'])]
    except Exception as e:
        print('tasks_now ledger:', e, flush=True); cur, q = [], []
    known = {(x['rid'], x['aid']) for x in cur}
    from jarvis.world.desk import is_desk
    try:   # workers from before the ledger (or another process) by their command line
        ps = subprocess.run(['ps', '-axo', 'lstart=,command='], capture_output=True, text=True, timeout=5).stdout
        for line in ps.splitlines():
            cmd = ' '.join(line.split()[5:])
            m = re.match(r'\S*/claude -p Use the jarvis-inbox-work skill on round-up ([\w-]+) item ([\w-]+) only', cmd)
            if m and (m.group(1), m.group(2)) not in known and not is_desk(m.group(1)):     # desk work stays on the desk
                st = dt.datetime.strptime(' '.join(line.split()[:5]), '%a %b %d %H:%M:%S %Y').astimezone()
                cur.append({'rid': m.group(1), 'aid': m.group(2), 'title': title(m.group(1), m.group(2)),
                            'started': st.isoformat(timespec='seconds'), 'lane': 'asked'})
                known.add((m.group(1), m.group(2)))
    except Exception:
        pass
    cur.sort(key=lambda it: it.get('started', ''), reverse=True)
    try:                                                # long runs a worker started: they go on with no worker (19 Oct)
        from jarvis.mcp import _jobs
        jobs = [dict(j, title=title(*j['item'].split('|', 1)) if '|' in j['item'] else '') for j in _jobs()]
    except Exception as e:
        print('tasks_now jobs:', e, flush=True); jobs = []
    return {'running': cur[0] if cur else None, 'running_all': cur, 'queue': q, 'jobs': jobs}


def stop_task(rid, aid):
    """Stops the work on that item (the whole process group), or drops it from the queue; releases its claim."""
    hit = dispatcher.stop(rid, aid)
    if not hit:                       # a worker from before the ledger: find it by its command line
        try:
            ps = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=5).stdout
            for line in ps.splitlines():
                pid, _, cmd = line.strip().partition(' ')
                if re.match(r'\S*/claude -p Use the jarvis-inbox-work skill on round-up %s item %s only' % (re.escape(rid), re.escape(aid)), cmd.strip()):
                    procs.signal_group(int(pid), signal.SIGTERM); hit = True
        except Exception:
            pass
    if hit:
        subprocess.run(['python3', os.path.join(str(paths.REPO), 'tools', 'worklock.py'), 'release', rid, aid], capture_output=True, timeout=20)
        return {'ok': True, 'stopped': True}
    return {'ok': False, 'error': 'nothing is running on that item'}


ROUTE_LABELS = ('analysis', 'scenario', 'code', 'draft', 'research')


def new_item(text, title='', noticed=None, route='', desk=None, points=None):
    """writes roundups/<YYYY-MM-DD-HHMM>-jarvis.json with one action and returns (rid, aid). `noticed`: the structured
    fields for an item Jarvis noticed itself; the point text stays the worker's brief (5 Oct: the brief was shown to the owner as
    one paragraph). Work Jarvis starts by itself (`noticed`, or desk=True for incidents) goes to its desk instead:
    roundups/<ts>-desk.json, kind 'desk', which the Inbox and everything that tells them skip until the fortnightly brief
    (world/desk.py; the owner, 9 Oct)."""
    desk = bool(noticed) if desk is None else desk
    now = dt.datetime.now().astimezone()
    rid = now.strftime('%Y-%m-%d-%H%M') + '-jarvis'
    if desk:                                    # one file per desk item, so the brief can release one without its neighbours
        base, i = now.strftime('%Y-%m-%d-%H%M%S'), 0
        rid = base + '-desk'
        while os.path.exists(os.path.join(HUB, 'roundups', rid + '.json')):
            i += 1; rid = f'{base}{i}-desk'
    p = os.path.join(HUB, 'roundups', rid + '.json')
    d = json.load(open(p)) if os.path.exists(p) else {
        'id': rid, 'title': "From Jarvis's desk" if desk else 'From Jarvis', 'kind': 'desk' if desk else 'jarvis',
        'period': now.strftime('%-d %b %Y, %H:%M'), 'source': 'Jarvis noticed this by itself.' if desk else 'Asked by voice in Jarvis.',
        'by': 'Jarvis', 'created': now.strftime('%Y-%m-%d'), 'window_start': now.isoformat(timespec='seconds'),
        'window_end': now.isoformat(timespec='seconds'), 'actions': [], 'answers': [], 'fyi': []}
    aid = 'a%d' % (len(d['actions']) + 1)
    who = {'src': 'noticed', 'who': 'Jarvis (noticed)'} if desk else {'src': 'jarvis', 'who': 'You'}   # 19 Oct: not "You (Jarvis)"
    d['actions'].append({'id': aid, **who, 'where': 'Jarvis', 'when': now.strftime('%-d %b %H:%M'),
                         'title': (title or text)[:90], 'text': text,
                         # his separate asks, one line each (12 Oct: the whole brief showed as one paragraph); the full brief is `text`
                         'points': [{'text': p} for p in points] if points else [{'text': text}], **({'noticed': noticed} if noticed else {}),
                         **({'route': route} if route else {})})
    json.dump(d, open(p + '.tmp', 'w'), indent=1); os.replace(p + '.tmp', p)
    return rid, aid


def task(body):
    kind = body.get('type')
    dry = bool(body.get('dry'))
    if kind == 'new_task':
        text = (body.get('text') or '').strip()
        if not text or len(text) > 2000:
            return {'ok': False, 'error': 'say what the work is'}
        if dry:
            return {'ok': True, 'dry': True, 'would': 'write roundups/<now>-jarvis.json, then start one worker on it'}
        kind_ = str(body.get('kind') or '').strip().lower()           # the brain's label (Phase 4 router, stage 2)
        pts = [str(p).strip()[:200] for p in (body.get('points') or []) if str(p).strip()][:8] if isinstance(body.get('points'), list) else []
        rid, aid = new_item(text, (body.get('title') or '').strip()[:80], route=kind_ if kind_ in ROUTE_LABELS else '', points=pts or None)
    elif kind == 'stop_task':
        rid, aid = body.get('rid') or '', body.get('aid') or ''
        if not (ID.match(rid) and ID.match(aid)):
            return {'ok': False, 'error': 'no such item'}
        if not dry:
            try:
                from jarvis import learned
                learned.signal('task_stopped', 'owner_said', f'{rid}|{aid}')
            except Exception:
                pass
        return {'ok': True, 'dry': True} if dry else stop_task(rid, aid)
    elif kind == 'start_task':
        rid, aid = body.get('rid') or '', body.get('aid') or ''
        if not (ID.match(rid) and ID.match(aid)) or not os.path.exists(os.path.join(HUB, 'roundups', rid + '.json')):
            return {'ok': False, 'error': 'no such item'}
    elif kind == 'go':                                  # his yes to a task held at the 10% rail
        tid = str(body.get('id') or '')
        t = dispatcher.task(tid) if re.fullmatch(r'[0-9]{8}-[0-9]{6}-[0-9a-f]{4}', tid) else None
        if not t:
            return {'ok': False, 'error': 'no such task'}
        if dry:
            return {'ok': True, 'dry': True}
        t = dispatcher.go(tid)
        return {'ok': True, 'item': {'rid': t['rid'], 'aid': t['aid'], 'ts': t['id']}, 'task_id': t['id'], 'started': t['state'] == 'running'}
    else:
        return {'ok': False, 'error': 'unknown task type'}
    if dry:
        return {'ok': True, 'dry': True, 'item': {'rid': rid, 'aid': aid}, 'would': f"claude -p '{dispatcher.item_prompt(rid, aid)}' --permission-mode auto"}
    t = dispatcher.submit('asked', dispatcher.item_prompt(rid, aid), rid, aid, title=(body.get('title') or '')[:80], origin='owner',
                          ask_quote=str(body.get('text') or '')[:1000])
    if t['state'] == 'held':
        return {'ok': True, 'held': True, 'rail': str(t.get('note') or '')[6:], 'item': {'rid': rid, 'aid': aid, 'ts': t['id']}, 'task_id': t['id']}
    started = t['state'] == 'running'
    ahead = 0 if started else sum(1 for x in dispatcher.queued('asked') if x['created'] < t['created']) + 1
    return {'ok': True, 'item': {'rid': rid, 'aid': aid, 'ts': t['id']}, 'task_id': t['id'], 'started': started, 'queued_behind': ahead,
            'log': t.get('log') or ''}
