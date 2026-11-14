"""Jarvis's local server: http://localhost:<port>, this computer only.

Serves the built app (web/out) and a JSON API for it: the inbox (round-ups, Claude's work files, ticks, answers, groups),
approvals, ticket proposals, long jobs, the landing page's map, and the assistant (assistant.py). Writes to the ticket
tracker, Slack or a database are never done here directly: they become approved requests that a Claude worker carries out
(actions.py), or go through tracker.py with a dry run first.
"""
from jarvis import config, paths
import datetime as dt, errno, glob, http.server, json, os, re, subprocess, sys, threading, time, urllib.parse

paths.ensure()                                   # the data home and its folders exist before anything reads them
HUB = str(paths.home())
REPO = str(paths.REPO)
# Started by the Mac app, the server inherits a minimal PATH: every worker it starts needs git, python and node, so the
# usual places for installed tools come first when they exist.
os.environ['PATH'] = os.pathsep.join(dict.fromkeys(
    [d for d in ('/opt/homebrew/opt/node@20/bin', '/opt/homebrew/bin', '/opt/homebrew/sbin', '/usr/local/bin', os.path.expanduser('~/.local/bin'))
     if os.path.isdir(d)] + os.environ.get('PATH', os.defpath).split(os.pathsep)))
paths.use_tools()
from jarvis import actions, assistant, episodes, human, tracker, notify, slack_send  # noqa: E402
from jarvis.spine import dispatcher, gateway, health, heartbeat, ledger  # noqa: E402
from jarvis.world import (catalog, connectors, desk, docs, initiative, investigate, match, needs, org, promises, research,  # noqa: E402
                          semantic, sentcheck, skills)

PORT = int(os.environ.get('PORT') or config.load().get('server.port'))
OUT = str(paths.repo('web', 'out'))
ROUND = os.path.join(HUB, 'roundups')
STATE = os.path.join(ROUND, 'state.json')
TICK, TFILE = os.path.join(HUB, 'tickets'), os.path.join(HUB, 'tickets', 'proposals.json')
AFILE, ANSFILE = os.path.join(HUB, 'approvals.json'), os.path.join(HUB, 'answers.json')
SAFE = re.compile(r'^[A-Za-z0-9._-]{1,80}$')
DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
STATUSES = {'draft', 'ready', 'created', 'dismissed'}
from jarvis import presenter
ORG_WORDS = presenter.org_words()        # the organisation's names (settings), never shown on a shared screen
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.json': 'application/json', '.txt': 'text/plain; charset=utf-8',
         '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff', '.map': 'application/json', '.webp': 'image/webp',
         '.mp4': 'video/mp4', '.webm': 'video/webm', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.avif': 'image/avif'}


def parse_args(argv):
    """Optional: --port N (default $PORT, else the port in settings)."""
    global PORT
    if argv[:1] == ['--port'] and len(argv) == 2 and argv[1].isdigit():
        PORT = int(argv[1])
    elif argv:
        sys.exit(f'unknown arguments: {" ".join(argv)}  (use --port N)')


def load(path, default):
    """A JSON file, or `default` when it is missing or unreadable."""
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def save(path, obj):
    """Write JSON atomically (temp file + rename), so a reader never sees half a file."""
    tmp = path + '.tmp'
    json.dump(obj, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def run(cmd, cwd=HUB, timeout=300):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return {'ok': p.returncode == 0, 'out': (p.stdout + p.stderr)[-6000:]}


# ------------------------------------------------------------------ inbox
def inbox():
    """The 8 newest round-ups (roundups/*.json, dropped there by any Claude session), Claude's work on them, their ticks and
    answers, the grouped asks, and what Jarvis's own worker runs now."""
    os.makedirs(ROUND, exist_ok=True)
    out = []
    files = sorted((f for f in os.listdir(ROUND) if f.endswith('.json') and f != 'state.json'), reverse=True)
    week = (dt.datetime.now() - dt.timedelta(days=7)).strftime('%Y-%m-%d')
    # the last 7 days (the page's own window), at least the 8 newest (9 Oct: 8 one-item Jarvis files pushed his 7 Oct
    # round-up out of the Inbox)
    for f in [f for i, f in enumerate(files) if i < 8 or f[:10] >= week]:
        d = load(os.path.join(ROUND, f), None)
        if isinstance(d, dict) and d.get('kind') in ('desk', 'fixture'):
            continue                                   # Jarvis's own desk (it reaches him in the fortnightly brief, world/desk.py);
                                                       # 'fixture' = the reviewer proof's planted items (tools/phase4_proof.py)
        if isinstance(d, dict) and isinstance(d.get('actions'), list):
            # 1 Nov: the 30 Oct 08:35 and 13:15 round-ups wrote actions with no id, so Start now sent none and got "no such
            # item" (5 presses). The worker already reads such actions in order as a1, a2: the page gets the same ids.
            d['actions'] = [{**a, 'id': a.get('id') or f'a{i + 1}'} if isinstance(a, dict) else a for i, a in enumerate(d['actions'])]
        out.append({**d, 'id': f[:-5]} if isinstance(d, dict) else {'id': f[:-5], 'title': f, 'error': 'unreadable'})
    work = {os.path.basename(f)[:-5]: w for f in glob.glob(os.path.join(HUB, 'work', '*.json')) if (w := load(f, None)) is not None}
    style = os.path.join(HUB, 'style.md')
    return {'roundups': out, 'state': load(STATE, {}), 'work': work,
            'schedule': load(os.path.join(HUB, 'roundup_schedule.json'), None), 'last': load(os.path.join(HUB, 'roundup_last.json'), None),
            'work_last': load(os.path.join(HUB, 'work_last.json'), None), 'answers': load(ANSFILE, {}),
            'groups': load(os.path.join(HUB, 'groups.json'), {}).get('groups', []),          # the same ask from different places
            'slack_leftovers': slack_send.leftovers(),                                         # old Slack drafts for him to delete
            'task_alerts': task_alerts(),                                                     # failed / stuck / long / held tasks (Phase 4)
            'style_at': os.path.getmtime(style) if os.path.exists(style) else None,
            'jarvis_tasks': assistant.tasks_now()}                                              # "Claude is working on this now"


def changed_v():
    """A number that moves whenever something the inbox shows is written (round-ups, work files, approvals, answers, ticks,
    groups, tickets, VPN waits). The page asks every 2 s and re-reads only when it moved."""
    v = 0.0
    for pat in ('roundups/*.json', 'work/*.json', 'approvals.json', 'answers.json', 'groups.json', 'tickets/*.json', 'vpn_wait'):
        for f in glob.glob(os.path.join(HUB, pat)):
            try:
                v = max(v, os.path.getmtime(f))
            except OSError:
                pass
    # a worker starting or ending is news too (12 Oct: an asked task ran for minutes while its card still said "Not started
    # yet", because only files moved this number and the worker had not written one yet)
    try:
        r = ledger.q("SELECT max(max(coalesce(created, ''), coalesce(started, ''), coalesce(ended, ''))) AS m FROM tasks")
        if r and r[0]['m']:
            v = max(v, dt.datetime.fromisoformat(r[0]['m']).timestamp())
    except Exception:
        pass
    return v


def unsent_reply(rid, key):
    """True if the item behind point `key` (a2.0 -> a2) has a drafted reply that has not gone out."""
    w = load(os.path.join(HUB, 'work', rid + '.json'), {}).get(key.split('.', 1)[0]) if SAFE.match(rid) else None
    r = (w or {}).get('reply') or {} if isinstance(w, dict) else {}
    if not str(r.get('text') or '').strip() or r.get('sent_at') or (w or {}).get('merged_into'):
        return False
    return not any(x.get('kind') == 'slack_send' and x.get('status') == 'done' and (x.get('related') or {}).get('rid') == rid
                   and (x.get('related') or {}).get('aid') == key.split('.', 1)[0] for x in load(AFILE, {}).get('items', []))


def tick(rid, keys, done):
    """Tick or untick points; a point that is the same ask as others (groups.json) moves with them."""
    st = load(STATE, {})
    asked = {f'{rid}|{k}' for k in keys}
    refs = set(asked)
    for g in load(os.path.join(HUB, 'groups.json'), {}).get('groups', []):
        members = {g['lead'], *g['members']}
        if refs & members:
            refs |= members
    if done:
        # Never close a linked point whose item still has an unsent reply (a message merged into a thread reply, once
        # marked done, takes the unsent thread reply out of To do with it). Only the points the owner ticked close regardless.
        refs = {ref for ref in refs if ref in asked or not unsent_reply(*ref.split('|', 1))}
    for ref in refs:
        r, k = ref.split('|', 1)
        if done:
            st.setdefault(r, {})[k] = True
        else:
            st.setdefault(r, {}).pop(k, None)
    save(STATE, st)
    return {'ok': True, 'state': st}


def answer_save(rid, aid, text, parts=None):
    """Their answer to an item's questions. `parts` = {question id: answer} from the per-question cards; `text` holds them all,
    labelled, which is what the work run reads."""
    if not SAFE.match(rid) or not SAFE.match(aid):
        raise ValueError('bad id')
    d, key = load(ANSFILE, {}), f'{rid}|{aid}'
    if str(text).strip():
        d[key] = {'text': str(text)[:4000], 'at': now(), 'used': False}
        if isinstance(parts, dict):
            d[key]['parts'] = {str(k)[:40]: str(v)[:1000] for k, v in list(parts.items())[:20] if str(v).strip()}
    else:
        d.pop(key, None)
    save(ANSFILE, d)
    return {'ok': True, 'answers': d}


def answer(b):
    """Save their answer; if Claude is working on that item right now it reads it at its next step, otherwise a worker starts on
    it at once (not at the next :30 run)."""
    rid, aid, text = str(b.get('rid', '')), str(b.get('aid', '')), str(b.get('text', ''))
    r = answer_save(rid, aid, text, b.get('parts'))
    if not text.strip():
        return r
    if any(x.get('rid') == rid and x.get('aid') == aid for x in assistant.tasks_now().get('running_all') or []):
        assistant.steer(rid, aid, text)
        return {**r, 'steered': True}
    if b.get('start', True):
        t = assistant.task({'type': 'start_task', 'rid': rid, 'aid': aid})
        r = {**r, 'worker': ('started' if t.get('started') else 'queued') if t.get('ok') else t.get('error', 'not started'), 'queued_behind': t.get('queued_behind', 0)}
    return r


def needs_now():
    """[(key, title)]: one key per open decision/question (item#point:note), so a new question pings their phone even on an
    old item (notify.py)."""
    ib, out = inbox(), []
    st = ib['state']
    for r in ib['roundups']:
        for a in r.get('actions') or []:
            w = (ib['work'].get(r['id']) or {}).get(a.get('id')) or {}
            pts = [p for p in w.get('points') or [] if p.get('state') in ('needs_you', 'decide') and not (st.get(r['id']) or {}).get(p.get('key'))]
            if pts:
                title = re.sub(r'\s+', ' ', str(a.get('title') or a.get('text') or ''))[:100]
                out.append((f"{r['id']}|{a.get('id')}#" + ';'.join(f"{p.get('key')}:{str(p.get('note') or '')[:60]}" for p in pts), title))
    return out


# ------------------------------------------------------------------ jobs + brain map
def jobs_list():
    """Long jobs (tools/run_job.py) for the Office view: running ones, and ones that ended in the last 3 hours. A multi-variant
    scenario run (run_cycle.py a b c ...) reports how many variants are done."""
    outs, t_now = os.path.join(HUB, 'work', 'outputs'), dt.datetime.now().astimezone()
    work = {os.path.basename(f)[:-5]: load(f, {}) for f in glob.glob(os.path.join(HUB, 'work', '20*.json'))}
    rows = []
    for jf in glob.glob(os.path.join(outs, '**', 'job.json'), recursive=True):
        j = load(jf, None)
        if not j:
            continue
        try:
            ended = dt.datetime.fromisoformat(j['ended'])
        except (KeyError, TypeError, ValueError):
            ended = None
        st = j.get('state')
        if st not in ('running', 'starting') and not (ended and (t_now - ended).total_seconds() < 3 * 3600):
            continue
        d = os.path.dirname(jf); rel = os.path.relpath(d, outs)
        m = re.match(r'^(\d{4}-\d{2}-\d{2}-\d{4}-[a-z]+)-(a\d+)$', rel.split(os.sep)[0])
        w = (work.get(m.group(1)) or {}).get(m.group(2)) if m else None
        cmd = [str(x) for x in (j.get('cmd') or [])]
        done = total = None
        cyc = [i for i, c in enumerate(cmd) if c.endswith('run_cycle.py')]
        if cyc:
            vs = cmd[cyc[0] + 1:]
            total, done = len(vs), 0
            try:
                lines = open(os.path.join(d, 'run.log'), errors='replace').read().splitlines()
                start = max([i for i, l in enumerate(lines) if vs and l.startswith(f'=== {vs[0]}: copy')] or [0])   # this run's part
                done = len({l.split(':')[0][4:] for l in lines[start:] if re.match(r'^=== \S+: rc=', l)} & set(vs))
            except OSError:
                pass
        rows.append({'dir': rel, 'label': str((w or {}).get('title') or (w or {}).get('summary') or rel)[:120], 'state': st,
                     'started': j.get('started'), 'ended': j.get('ended'), 'check': j.get('check'), 'done': done, 'total': total})
    rows.sort(key=lambda r: (r['state'] not in ('running', 'starting'), r.get('started') or ''))
    return {'jobs': rows, 'at': t_now.isoformat(timespec='seconds')}


ALERT_KINDS = {'task_failed': 'failed', 'task_stuck': 'stuck', 'task_long': 'long', 'task_overtime_stopped': 'stopped',
               'task_refusals_stopped': 'refused',       # a rail hold is said by the page that asked, at once
               'health_problem': 'health', 'incident': 'storm',   # a login to renew, a stale watcher; restart storms only
               'autopilot_shipped': 'improved', 'autopilot_rolled_back': 'rolled_back'}   # Level 5: what changed in Jarvis itself


def task_alerts(hours=6):
    """Task outcomes they must hear about that no result card shows: failed, stuck (interrupted and resumed), running long,
    stopped by the watchdog or by repeated refusals, held at their 10% rail. The page says each once."""
    from jarvis.spine import ledger
    cut = (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec='seconds')
    out = []
    for e in ledger.q("SELECT * FROM events WHERE at >= ? AND kind IN (%s) ORDER BY at" % ','.join('?' * len(ALERT_KINDS)), (cut, *ALERT_KINDS)):
        try:
            d = json.loads(e['data'] or '{}')
        except ValueError:
            d = {}
        if e['kind'] == 'incident' and d.get('kind') != 'restart_storm':
            continue                                   # other incidents become a fix item in his inbox; only storms are said
        t = ledger.q('SELECT title, rid, aid, lane, origin, state FROM tasks WHERE id=?', (e['ref'],))
        t = t[0] if t else {}
        if e['kind'] == 'task_failed' and t.get('state') == 'done':
            continue                                   # refiled as done: it finished (1 Nov, dispatcher._finished_ok)
        if t.get('origin') in ('initiative', 'incident', 'autopilot') or e['kind'].startswith('autopilot_') or (t.get('rid') and desk.is_desk(t['rid'])):
            continue                                   # Jarvis's own work (desk, Level 5): the fortnightly brief tells him (9 Oct)
        out.append({'id': e['id'], 'at': e['at'], 'kind': ALERT_KINDS[e['kind']], 'title': ORG_WORDS.sub('our', str(t.get('title') or d.get('title') or ''))[:90],
                    'rid': t.get('rid') or '', 'aid': t.get('aid') or '', 'lane': t.get('lane') or '', 'why': str(d.get('why') or d.get('say') or '')[:160],
                    'minutes': d.get('minutes') or d.get('idle_min')})
    return out[-20:]


_KB_MAP = {'key': None, 'data': None}


def _kb_acronyms():
    """Words the map writes in capitals: the owner's own list (settings: knowledge.acronyms)."""
    return {str(w).lower(): str(w) for w in config.load().get('knowledge.acronyms') or [] if str(w).strip()} | {'jarvis': 'Jarvis'}


def _never_named():
    """First names never shown on the map (settings: org.never_name), e.g. someone who has left."""
    return {str(w).strip().lower() for w in config.load().get('org.never_name') or [] if str(w).strip()}


def _kb_map():
    """The knowledge base on the map. One pass over the ledger gives, per workstream, COUNTS ONLY (current facts, methods,
    decisions, open points, replaced records) and the tables, tickets and first names its records carry. Never a record's
    text. Cached until the ledger file changes."""
    p = str(paths.kb('ledger', 'records.jsonl'))
    try:
        key = os.path.getmtime(p)
    except OSError:
        return None
    if _KB_MAP['key'] == key:
        return _KB_MAP['data']
    ws = {}
    from jarvis import entities
    own_kinds = set(entities.custom())
    def bump(d, k):
        d[k] = d.get(k, 0) + 1
    try:
        for line in open(p, errors='replace'):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            w = str(r.get('workstream') or '')
            if not w or w.startswith('('):               # "(memory)" is on the map already, as the memory topics
                continue
            d = ws.setdefault(w, {'kinds': {}, 'tables': {}, 'tickets': {}, 'who': {}, 'old': 0})
            if r.get('status') != 'current':
                d['old'] += 1
                continue
            bump(d['kinds'], str(r.get('kind') or 'fact'))
            for e in r.get('entities') or []:
                t, _, v = str(e).partition(':')
                if t in own_kinds and v:                 # one of the owner's own kinds of thing (settings: knowledge.entities)
                    bump(d['tables'], v); d.setdefault('thing_kind', {})[v] = t
                elif t == 'ticket' and re.fullmatch(r'[A-Za-z]{2,6}-\d{1,6}', v):
                    bump(d['tickets'], v.upper())
            who = re.match(r'[A-Za-z]+', str(r.get('who') or ''))
            if who:
                bump(d['who'], who.group(0).lower())
    except OSError:
        return None
    _KB_MAP.update(key=key, data={'ws': ws, 'at': dt.datetime.fromtimestamp(key).astimezone().isoformat(timespec='seconds')})
    return _KB_MAP['data']


def _kb_people():
    """{first name: full name} from the knowledge base's people notes (their names only)."""
    out, pd, left = {}, str(paths.kb('people')), _never_named()
    try:
        for f in sorted(os.listdir(pd)):
            m = re.search(r'^person:\s*(.+)$', open(os.path.join(pd, f), errors='replace').read(400), re.M)
            if m:
                full = m.group(1).strip()
                first = full.split()[0].lower()
                if first not in left:
                    out[first] = full
    except OSError:
        pass
    return out


def brain():
    """The landing page's map: what Jarvis knows, as a graph. Nodes: people, inbox items, tickets, repositories, memory
    notes (grouped by their MEMORY.md section) and sources; links between them. Titles only, never note bodies. Plus the
    knowledge base: one hub per workstream with its counts, and the tables, tickets and people its records name (_kb_map)."""
    left = _never_named()
    hide = re.compile(r'\b(?:%s)\b(?:\s+[A-Z][a-z]+)?' % '|'.join(re.escape(n.capitalize()) for n in sorted(left))) if left else None
    nodes, links, seen = [], [], set()
    def node(i, kind, label, **kw):
        if i not in seen:
            lab = ORG_WORDS.sub('our', str(label))
            if hide:                                  # this map can be on a shared screen: a listed name never shows
                lab = hide.sub('a former colleague', lab)
            seen.add(i); nodes.append({'id': i, 'kind': kind, 'label': (lab[:1].upper() + lab[1:])[:90], **kw})
        return i
    def link(a, b):
        if a in seen and b in seen and a != b:
            links.append([a, b])
    ib = inbox()
    st, work = ib['state'], ib['work']
    names = {'slack': 'Slack', 'email': 'Email', 'jira': 'Jira', 'meeting': 'Meetings', 'teams': 'Teams', 'confluence': 'Confluence'}
    for r in ib['roundups']:
        for a in r.get('actions') or []:
            rid, aid = r['id'], a.get('id')
            w = (work.get(rid) or {}).get(aid) or {}
            pts = a.get('points') or []
            done = bool(pts) and all((st.get(rid) or {}).get(f'{aid}.{i}') for i in range(len(pts)))
            src = a.get('src') or r.get('kind') or 'other'
            it = node(f'i:{rid}|{aid}', 'item', a.get('title') or (pts[0].get('text') if pts and isinstance(pts[0], dict) else '') or 'Item',
                      who=a.get('who') or '', src=src, state=w.get('status') or '', done=done, key=f'{rid}|{aid}', when=a.get('when') or '')
            if a.get('who'):
                link(node('p:' + a['who'], 'person', a['who']), it)
            link(node('s:' + src, 'source', names.get(src, src.title())), it)
            if w.get('jira_key') or w.get('ticket_key'):
                link(it, node('t:' + (w.get('jira_key') or w.get('ticket_key')), 'ticket', w.get('jira_key') or w.get('ticket_key')))
    by_id = {}
    for t in tickets_load()['items']:
        if t.get('key'):
            node('t:' + t['key'], 'ticket', t['key'])
            if t.get('summary'):
                by_id['t:' + t['key']] = ORG_WORDS.sub('our', t['summary'])[:140]
    for n in nodes:
        if n['id'] in by_id:
            n['detail'] = by_id[n['id']]
    for g in ib['groups']:
        ids = ['i:' + x.split('.')[0] for x in [g['lead']] + list(g.get('members') or [])]
        for x in ids[1:]:
            link(ids[0], x)
    from jarvis.world import org as _org
    repos = _org._repos()
    if repos:
        hub = node('r:git', 'source', 'Repositories')
        for r in repos:
            link(hub, node('r:' + os.path.basename(r), 'repo', os.path.basename(r)))
    try:
        sec = None
        for line in open(os.path.join(assistant.memory_dir(), 'MEMORY.md'), errors='replace'):
            if line.startswith('## '):
                sec = node('m:' + line[3:].strip(), 'topic', line[3:].strip())
            for m in re.finditer(r'\[([^\]]+)\]\(([^)]+\.md)\)', line):
                n = node('n:' + m.group(2), 'note', m.group(1))
                if sec:
                    link(sec, n)
    except OSError:
        pass
    kbsum = None
    kb = _kb_map()
    if kb:
        ws, ppl = kb['ws'], _kb_people()
        tot_tables = {}
        for d in ws.values():
            for t, c in d['tables'].items():
                tot_tables[t] = tot_tables.get(t, 0) + c
        top_tables = {t for t, _ in sorted(tot_tables.items(), key=lambda x: -x[1])[:16]}
        acr = _kb_acronyms()
        def pretty(w):
            words = [acr.get(x, x) for x in w.split('-')]
            return (' '.join(words))[:1].upper() + (' '.join(words))[1:]
        tot = {'records': 0, 'open': 0, 'decisions': 0, 'old': 0}
        for w, d in sorted(ws.items(), key=lambda x: -sum(x[1]['kinds'].values())):
            k = d['kinds']
            cur = sum(k.values())
            if cur < 50:
                continue
            tot['records'] += cur; tot['open'] += k.get('open', 0); tot['decisions'] += k.get('decision', 0); tot['old'] += d['old']
            sid = node('w:' + w, 'stream', pretty(w), n=cur, facts=k.get('fact', 0), methods=k.get('method', 0),
                       decisions=k.get('decision', 0), open=k.get('open', 0), old=d['old'], key=w)
            for t, c in sorted(d['tables'].items(), key=lambda x: -x[1])[:6]:
                if t in top_tables and c >= 15:
                    link(sid, node('b:' + t, 'table', t, n=tot_tables[t], detail=str((d.get('thing_kind') or {}).get(t, '')).replace('_', ' ').capitalize()))
            for t, c in sorted(d['tickets'].items(), key=lambda x: -x[1])[:4]:
                if c >= 10:
                    link(sid, node('t:' + t, 'ticket', t))
            for name, c in sorted(d['who'].items(), key=lambda x: -x[1]):
                if name in ppl and c >= 3:
                    link(sid, node('p:' + ppl[name], 'person', ppl[name]))
        kbsum = {**tot, 'streams': sum(1 for n in nodes if n['kind'] == 'stream'), 'at': kb['at']}
    counts = {}
    for n in nodes:
        counts[n['kind']] = counts.get(n['kind'], 0) + 1
    return {'nodes': nodes, 'links': links, 'counts': counts, 'kb': kbsum}


# ------------------------------------------------------------------ Jira ticket proposals
def tickets_load():
    d = load(TFILE, {})
    d.setdefault('items', [])
    d['mtime'] = os.path.getmtime(TFILE) if os.path.exists(TFILE) else 0
    return d


def tickets_save(items):
    """Save their ticket proposals. Claude writes keys back into the same file after creating tickets, so a created ticket's
    key/status on disk always wins, and a created ticket is never dropped."""
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError('items must be a list')
    disk = {i.get('id'): i for i in tickets_load()['items']}
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict) or not SAFE.match(str(it.get('id', ''))) or it['id'] in seen:
            raise ValueError('bad or duplicate item id')
        d = disk.get(it['id'])
        if d and d.get('status') == 'created' and d.get('key'):
            it = {**it, 'status': 'created', 'key': d['key'], 'created_at': d.get('created_at')}
        if it.get('status') not in STATUSES:
            it['status'] = 'draft'
        out.append(it)
        seen.add(it['id'])
    out += [d for i, d in disk.items() if i not in seen and d.get('status') == 'created']
    os.makedirs(TICK, exist_ok=True)
    save(TFILE, {'items': out})
    return tickets_load()


def ticket_write_back(tid, fields):
    """jira.create_tickets calls this right after each ticket is created: record the key in proposals.json."""
    items = tickets_load()['items']
    for it in items:
        if it.get('id') == tid:
            it.update(fields)
    save(TFILE, {'items': items})


# ------------------------------------------------------------------ approvals


def approvals_load():
    d = load(AFILE, {})
    d.setdefault('items', [])
    d['mtime'] = os.path.getmtime(AFILE) if os.path.exists(AFILE) else 0
    d['executor'] = (paths.claude_skills() / 'jarvis-approved-actions' / 'SKILL.md').exists()   # is the skill that carries them out installed?
    return d


def approvals_set(aid, status, choice=None):
    """The app may only approve/decline a pending request (picking one of its options if it has any), or take back an
    approval Claude has not started."""
    d = approvals_load()
    for it in d['items']:
        if it.get('id') != aid:
            continue
        cur = it.get('status')
        if not ((cur == 'pending' and status in ('approved', 'declined')) or (cur == 'approved' and status == 'pending')):
            raise ValueError(f'cannot change a request that is {cur}')
        opts = [o.get('id') for o in it.get('options') or []]
        if status == 'approved' and opts:
            if choice not in opts:
                raise ValueError('pick one of the options')
            it['choice'] = choice
        elif status != 'approved':
            it.pop('choice', None)
        it['status'] = status
        it['approved_at'] = now() if status == 'approved' else None
        save(AFILE, {'items': d['items']})
        return approvals_load()
    raise ValueError('no such request')


def share_folder():
    """The synced folder files are shared through (settings: sharing.folder: a folder a cloud-drive app keeps in step, which
    the people written to can open), or None when none is set or it is not there."""
    d = os.path.expanduser(str(config.load().get('sharing.folder') or '').strip())
    return d if d and os.path.isdir(d) else None


def _share_line(dest, root, day):
    """The line a message ends with for a shared file: its name, where it is in the shared folder, and the folder's link."""
    link = str(config.load().get('sharing.folder_link') or '').strip()
    return f"File: {os.path.basename(dest)} (in {os.path.basename(root.rstrip(os.sep))}/{day}" + (f": {link})" if link else ")")


# Files that go out as a line pointing into the shared folder (write-ups, .md, go as a canvas at Send).
SHARE_EXT = ('.xlsx', '.csv')


def share_files(att, text):
    """At their Send press: each .xlsx / .csv attachment is copied into <shared folder>/<today>/ (the cloud-drive app
    uploads it), and one line per file is added to the message. Code, not a model.
    Returns (attachments, text). Raises when the folder is missing: nothing goes out without its file."""
    import shutil
    files = [a for a in att if a['path'].lower().endswith(SHARE_EXT) and not a.get('shared')]   # shared at draft time already: skip
    if not files:
        return att, text
    root = share_folder()
    if not root:
        raise ValueError('No shared folder is set, or it is missing (Settings: the folder files are shared through), so nothing was sent.')
    today = dt.date.today().isoformat()
    day = os.path.join(root, today)
    os.makedirs(day, exist_ok=True)
    lines = []
    for a in files:
        base, ext = os.path.splitext(os.path.basename(a['path']))
        if f'File: {base}' in text:            # the draft already carries its link (shared at draft time): never share twice
            continue
        dest, n = os.path.join(day, base + ext), 1
        data = open(a['path'], 'rb').read()
        while os.path.exists(dest) and open(dest, 'rb').read() != data:     # the same file is reused, never copied as "(2)"
            n += 1; dest = os.path.join(day, f'{base} ({n}){ext}')
        if not os.path.exists(dest):
            shutil.copy2(a['path'], dest)
        a['shared'] = os.path.relpath(dest, root)
        lines.append(_share_line(dest, root, today))
    if not lines:
        return att, text
    return att, (text.rstrip() + '\n\n' + '\n'.join(lines)).strip()


def share_file(b):
    """POST /api/share_file {path}: a worker drafting a reply puts its Excel / CSV into the shared folder
    at once and gets the line to end the draft with, so the draft itself carries the link (a draft that only says
    "file attached" has no file). Only files Jarvis made (work/outputs);
    the same file twice gives the same link line."""
    import shutil
    outs = os.path.realpath(os.path.join(HUB, 'work', 'outputs'))
    f = os.path.realpath(os.path.expanduser(str(b.get('path', ''))))
    if not f.startswith(outs + os.sep) or not f.lower().endswith(SHARE_EXT) or not os.path.isfile(f) or os.path.getsize(f) > 5_000_000:
        raise ValueError('only an .xlsx / .csv under 5 MB in the outputs folder can be shared')
    root = share_folder()
    if not root:
        raise ValueError('no shared folder is set, or it is missing (settings: sharing.folder)')
    day = dt.date.today().isoformat()
    os.makedirs(os.path.join(root, day), exist_ok=True)
    dest = os.path.join(root, day, os.path.basename(f))
    if os.path.exists(dest) and open(dest, 'rb').read() != open(f, 'rb').read():
        base, ext = os.path.splitext(os.path.basename(f)); n = 2
        while os.path.exists(os.path.join(root, day, f'{base} ({n}){ext}')):
            n += 1
        dest = os.path.join(root, day, f'{base} ({n}){ext}')
    if not os.path.exists(dest):
        shutil.copy2(f, dest)
    return {'ok': True, 'line': _share_line(dest, root, day), 'shared': os.path.relpath(dest, root)}


def drafts_loop():
    """New Slack replies get their Slack draft (app/slack_send.py) within about a minute; no model call when nothing is new."""
    while True:
        time.sleep(60)
        if os.path.exists(os.path.join(HUB, 'state', 'KILL')):
            continue
        try:
            r = slack_send.sync_drafts()
            if r.get('made'):
                ledger.event('slack_drafts', '', 'server', r)
        except Exception as e:
            ledger.event('slack_drafts_error', '', 'server', {'error': str(e)[:200]})


def approvals_add(item):
    """The app adds requests they have just confirmed in a dialog (Send to Slack), already approved."""
    if not isinstance(item, dict) or item.get('kind') != 'slack_send':
        raise ValueError('the app can only add Slack sends')
    # 5 Oct 2026: Slack replies go out through /api/slack_send (app/slack_send.py), not through an approvals worker.
    raise ValueError('Slack replies are sent with Send to Slack now (reload the page).')
    p = item.get('payload') or {}
    if not re.fullmatch(r'[CDG][A-Z0-9]{6,}', str(p.get('channel_id', ''))) or not str(p.get('text', '')).strip():
        raise ValueError('need a Slack channel id and some text')
    if p.get('thread_ts') and not re.fullmatch(r'\d{6,}\.\d+', str(p['thread_ts'])):
        raise ValueError('bad thread timestamp')
    # attachments go out as Slack canvases; only Markdown write-ups from the outputs folder are allowed
    outs, att = os.path.realpath(os.path.join(HUB, 'work', 'outputs')), []
    for a in (p.get('attachments') or [])[:5]:
        f = os.path.realpath(os.path.expanduser(str((a or {}).get('path', ''))))
        ext = os.path.splitext(f)[1].lower()
        cap = 40_000 if ext == '.md' else 5_000_000
        if not f.startswith(outs + os.sep) or ext not in ('.md', '.csv', '.xlsx') or not os.path.isfile(f) or os.path.getsize(f) > cap:
            raise ValueError('attachments must be write-ups (.md, under 40 KB) or tables (.csv / .xlsx, under 5 MB) in the outputs folder')
        att.append({'title': str(a.get('title') or os.path.basename(f))[:120], 'path': f})
    att, send_text = share_files(att, str(p['text']))          # Excel / CSV: into the shared folder, link line added
    p = {**p, 'text': send_text}
    d, t = approvals_load(), now()
    d['items'].append({'id': 'ap-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(len(d['items']) + 1), 'kind': 'slack_send',
                       'title': str(item.get('title', 'Send a Slack reply'))[:200], 'why': str(item.get('why', ''))[:500],
                       'plan': 'Send this exact message: ' + str(p['text'])[:4000] + ''.join(f'\nAttach as a Slack canvas: {a["title"]}' for a in att
                                                                                      if not a.get('shared')),
                       'payload': {**{k: p.get(k) for k in ('channel_id', 'thread_ts', 'text', 'to', 'where')}, 'attachments': att},
                       'related': item.get('related') or {}, 'status': 'approved', 'created': t, 'created_by': 'Jarvis', 'approved_at': t})
    save(AFILE, {'items': d['items']})
    return approvals_load()


# ------------------------------------------------------------------ the ticket tracker
def token_works():
    """Does Jira accept the owner's own API token? Only asked when direct calls are switched on in settings."""
    try:
        tracker.call('GET', '/rest/api/3/myself')
        return True
    except Exception:
        return False


def jira_route(name, b):
    """Direct Jira actions. Writes take dry=true first (the page shows the plan and asks), then dry=false."""
    dry = b.get('dry', True) is not False
    try:
        if name == 'create_tickets':
            return tracker.create_tickets(tickets_load()['items'], ids=[str(i) for i in b.get('ids') or []], dry=dry, write_back=ticket_write_back)
        if name == 'comment':
            key, text = str(b.get('key', '')).strip().upper(), str(b.get('text', '')).strip()
            if not re.fullmatch(r'[A-Z][A-Z0-9]+-\d+', key) or not text:
                return {'ok': False, 'error': 'need a ticket key and some text'}
            if dry:
                return {'ok': True, 'dry': True, 'plan': [{'key': key, 'text': text}]}
            try:
                r = tracker.call('POST', f'/rest/api/3/issue/{key}/comment', {'body': tracker.adf(text)})
            except (tracker.NoToken, tracker.JiraError) as e:
                if isinstance(e, tracker.JiraError) and token_works():
                    raise                                    # a real error with a working login: show it
                # no working Jira token (the usual case: direct calls are off): a Claude worker posts it with the
                # Atlassian connector, exactly this text (the worker hook checks it against the approval)
                return actions.queue_jira_comment(key, text, str(b.get('rid', '')), str(b.get('aid', '')))
            return {'ok': True, 'dry': False, 'result': {'key': key, 'comment_id': r.get('id')}}
        return {'ok': False, 'error': 'unknown Jira action'}
    except tracker.NoToken as e:
        return {'ok': False, 'error': str(e), 'setup': tracker.setup_line(), 'needs_token': True}
    except tracker.JiraError as e:
        return {'ok': False, 'error': str(e)}


def open_output(path):
    """Write-ups (outputs/), what the context sweep found (context/) and the folders of investigations and research trials
    (tasks/; 13 Nov: "Open the trial folder" was refused) can be opened; nothing else."""
    roots = [os.path.realpath(os.path.join(HUB, 'work', d)) for d in ('outputs', 'context', 'tasks')]
    target = os.path.realpath(os.path.expanduser(str(path)))
    if not any(target == r or target.startswith(r + os.sep) for r in roots) or not os.path.exists(target):
        return {'ok': False, 'out': "That is not one of Jarvis's work folders, or it no longer exists."}, 400
    return run(['open', target]), 200


def nudges(q):
    """Things the page says at the next quiet moment: reminders that are due and what Claude found in the background."""
    return {'due': human.due(), 'answers': assistant.consults_ready()}


def metrics(q):
    d = q.get('day') or ''
    return assistant.metrics_summary(d if DATE.fullmatch(d) else None)


def changed(q):
    ui = os.path.join(OUT, 'index.html')
    return {'v': changed_v(), 'ui': os.path.getmtime(ui) if os.path.exists(ui) else 0}


def _recipient_notes():
    """The notes on how they write to each person, with a flag on a note whose only basis is an edit signal later found to
    be false (a reply recorded as cut down that they had in fact sent whole). Only they can drop
    a note; the flag puts the reason next to the button."""
    from jarvis import learned
    rows = learned.q('SELECT * FROM recipient_notes ORDER BY recipient')
    sig = lambda ref: [int(x) for x in re.findall(r'\d+', str(ref or '').split('signals:')[-1])] if 'signals:' in str(ref or '') else []
    def void(ids):
        if not ids:
            return None
        kinds = [k['kind'] for k in learned.q('SELECT kind FROM learn_signals WHERE id IN (%s)' % ','.join('?' * len(ids)), tuple(ids))]
        return bool(kinds) and all(k == 'draft_edited_void' for k in kinds)
    for r in rows:
        last = void(sig(r.get('source_ref')))
        if not last:
            continue
        earlier = [void(sig(e['source_ref'])) for e in learned.q("SELECT source_ref FROM learn_edits WHERE tbl='recipient_notes' AND row_id=? AND accepted=1 "
                                                               "AND source_ref != ?", (f"{r['recipient']}|{r['channel']}", r.get('source_ref') or ''))]
        r['basis_void'] = 'last' if any(v is False for v in earlier) else 'all'      # 'last': only its latest change rests on a false signal
    return rows


def learned_view(q):
    """The Learned view (Phase 5): what changed this week (accepted and refused, with why and undo), the active rules, the
    facts that hold, the notes per person, what they asked to remember, and the signals behind them."""
    from jarvis import learned
    cut = (dt.datetime.now() - dt.timedelta(days=7)).isoformat(timespec='seconds')
    clean = lambda t: ORG_WORDS.sub('our', str(t or ''))
    edits = learned.q("SELECT * FROM learn_edits WHERE at >= ? AND run != 'migrate' ORDER BY at DESC LIMIT 200", (cut,))
    return {'edits': [{**e, 'text': clean(e['text']), 'undoable': bool(e['accepted'] and e['inverse'] and not e['undone_at'])} for e in edits],
            'rules': [{k: (clean(v) if k == 'text' else v) for k, v in r.items()} for r in learned.q("SELECT * FROM rules WHERE state='active' ORDER BY scope, created")],
            'facts': [{k: (clean(v) if k == 'text' else v) for k, v in f.items()} for f in learned.current_facts()],
            'recipients': _recipient_notes(),
            'memory': [{k: (clean(v) if k == 'text' else v) for k, v in m.items()} for m in learned.memory_items()],
            'signals': learned.q("SELECT kind, count(*) AS n, max(at) AS last FROM learn_signals WHERE at >= ? GROUP BY kind ORDER BY n DESC", (cut,)),
            'max_rules': learned.MAX_RULES, 'staged': learned.q("SELECT id, text, created FROM rules WHERE state='staged'"),
            'skills': _offered_skills(),
            'tools': _forge_tools(),
            'autopilot': _autopilot_status()}


def _forge():
    import importlib.util
    spec = importlib.util.spec_from_file_location('forge', os.path.join(str(paths.REPO), 'tools', 'forge.py'))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _forge_tools():
    try:
        return _forge().staged()[:12]
    except Exception:
        return []


def _offered_skills():
    try:
        from jarvis.world import skills
        return skills.offered()
    except Exception:
        return []


def _autopilot_status():
    try:
        from jarvis.spine import autopilot
        st = autopilot.status()
        st['tickets'] = [{**t, 'result': ORG_WORDS.sub('our', str(t.get('result') or ''))} for t in st['tickets']]
        return st
    except Exception as e:
        return {'error': str(e)[:120]}


def labels_view(q):
    from jarvis import learning
    return {'items': [{**t, 'q': ORG_WORDS.sub('our', str(t.get('q') or '')), 'answer': ORG_WORDS.sub('our', str(t.get('answer') or ''))}
                      for t in learning.label_candidates()]}


def recipient_note_get(q):
    """How they write to a person (for the drafting worker: curl /api/recipient_note?name=Priya)."""
    from jarvis import learned
    return {'notes': [{'recipient': r['recipient'], 'channel': r['channel'], 'note': r['note']} for r in learned.recipient_note(q.get('name') or '')]}


from jarvis.world import cockpit  # noqa: E402
GET = {'/api/inbox': lambda q: inbox(), '/api/nudges': nudges,
       '/api/changed': changed, '/api/tickets': lambda q: tickets_load(), '/api/approvals': lambda q: approvals_load(),
       '/api/jobs': lambda q: jobs_list(), '/api/brain': lambda q: brain(), '/api/jira/status': lambda q: tracker.status(),
       '/api/jarvis_models': lambda q: assistant.models_state(), '/api/jarvis_conversations': lambda q: assistant.conversations(),
       '/api/jarvis_brain': lambda q: assistant.POOL.status(), '/api/jarvis_metrics': metrics,
       '/api/learned': learned_view, '/api/labels': labels_view, '/api/recipient_note': recipient_note_get,
       # read-only data for the screens (world/cockpit.py)
       '/api/autonomy': cockpit.autonomy, '/api/kb': cockpit.kb, '/api/activity': cockpit.activity, '/api/research': cockpit.research}
POST_EXTRA = {}                                  # write routes of the optional parts that are switched on (load_modules)
MODULES = {}


def load_modules(log=print):
    """The optional parts the owner switched on in settings (modules.<name>): each brings its own routes and timed jobs.
    One that is switched on but cannot be loaded is said in the log and left out; the rest of Jarvis runs."""
    import importlib
    for name in ('deploy_gate', 'vault'):
        if not config.load().get(f'modules.{name}'):
            continue
        try:
            m = importlib.import_module(f'jarvis.modules.{name}')
        except ImportError as e:
            log(f'module {name} is switched on but could not be loaded: {e}')
            continue
        MODULES[name] = m
        GET.update(getattr(m, 'GET', {}))
        POST_EXTRA.update(getattr(m, 'POST', {}))
    return sorted(MODULES)

def _tell_held(e):
    """A reply went into the 10-minute hold: they are told on the phone and sees it on the page (Today and Trust)."""
    try:
        notify.ping(f"Sending to {e.get('to') or e.get('where') or 'someone'} in 10 minutes", f"{e.get('first_line', '')}\nStop it in Jarvis (Today) if it should not go.")
    except Exception:
        pass
    ledger.event('held', e.get('id', ''), 'ladder', {k: e.get(k) for k in ('rid', 'aid', 'cls', 'to', 'send_at')})


def spine_status(q):
    """What the spine is doing: lanes (running + waiting), the scheduler's view, recent events (the Running view)."""
    fmt = lambda t: {k: t.get(k) for k in ('id', 'lane', 'kind', 'title', 'rid', 'aid', 'state', 'started', 'created', 'origin', 'note')}
    hb = HEART.status if HEART else {}
    ev = ledger.q("SELECT at, kind, ref, data FROM events WHERE kind NOT IN ('task_queued') ORDER BY id DESC LIMIT 25")
    done = ledger.q("SELECT * FROM tasks WHERE state IN ('done','failed','stopped') ORDER BY COALESCE(ended, created) DESC LIMIT 10")
    return {'running': [fmt(t) for t in dispatcher.running()], 'waiting': [fmt(t) for t in dispatcher.queued()],
            'recent': [fmt(t) for t in done], 'heartbeat': hb, 'events': ev, 'kill': os.path.exists(heartbeat.KILL)}


def watching(q):
    """The Watching view: what the project waits for (needs), promises (commitments), what Jarvis is checking by itself
    (initiatives), logins that blind it, and how much it can see (catalog)."""
    nf = ('id', 'kind', 'statement', 'target', 'why', 'assumed', 'origin', 'state', 'topic', 'source_ref', 'created', 'candidates', 'dismissed_at')
    # only work is watched (never personal, machine, admin or Jarvis-build items); the rest is counted
    act = ledger.q("SELECT * FROM needs WHERE dismissed_at IS NULL AND state IN ('open','partial') AND coalesce(scope, 'work') = 'work' ORDER BY created DESC")
    other = ledger.q("SELECT scope, count(*) AS n FROM needs WHERE dismissed_at IS NULL AND state IN ('open','partial') AND coalesce(scope, 'work') != 'work' GROUP BY scope")
    out_needs = []
    for n in act:
        c = [x for x in json.loads(n.get('candidates') or '[]') if x.get('strength') == 'strong']
        out_needs.append({**{k: n.get(k) for k in nf if k != 'candidates'}, 'candidates': [x['source'] for x in c][:4]})
    return {'needs': out_needs,
            'satisfied': ledger.q("SELECT id, statement, state, updated, note FROM needs WHERE state IN ('satisfied','resolved','answered') ORDER BY updated DESC LIMIT 20"),
            'dismissed': ledger.q("SELECT id, statement, dismissed_at FROM needs WHERE dismissed_at IS NOT NULL ORDER BY dismissed_at DESC LIMIT 30"),
            'commitments': _commitments(),
            'not_work': {r['scope']: r['n'] for r in other},
            'initiatives': ledger.q("SELECT i.id, i.created, i.title, i.state, i.need_id, t.rid, t.aid FROM initiatives i LEFT JOIN tasks t ON t.id = i.task_id ORDER BY i.created DESC LIMIT 15"),
            'logins': [n for n in (config.load().get('health.watch_logins') or []) if ledger.cursor('login:' + str(n))],
            'goals': ledger.q("SELECT id, statement, source, state FROM goals WHERE dismissed_at IS NULL ORDER BY created"),
            'recognizers': json.loads(ledger.cursor('recognizer_selftest') or '{}'),
            'catalog': ledger.q("SELECT system, count(*) n, max(last_seen) seen FROM sources GROUP BY system"),
            'changes': ledger.q("SELECT at, source_id, kind, detail FROM catalog_events WHERE kind != 'first_seen' ORDER BY id DESC LIMIT 15"),
            # Phase 6: data that stopped or shifted, what happens around the project, and newer data than his notebooks used
            'anomalies': [e for e in ledger.q("SELECT at, source_id, kind, detail FROM catalog_events WHERE kind IN ('feed_stopped','quality_shift',"
                                              "'volume_jump') AND at >= ? ORDER BY id DESC LIMIT 200", ((dt.datetime.now() - dt.timedelta(days=30)).isoformat(),))
                          if catalog.ours(e['source_id'])][:12],
            'org': _org_items(),
            'stale': [],                                 # (kept for the screen; nothing fills it in this version)
            'desk': _desk(),                             # Jarvis's own desk: counts and the next brief only (world/desk.py)
            'investigations': _investigations(),         # multi-step goals Jarvis runs by itself (world/investigate.py)
            'research': _research(),                     # outside papers and techniques (world/research.py): his verdict
            'docs_read': _docs_read()}                   # documents Jarvis opened and read (world/docs.py)


def _docs_read():
    out = []
    for r in ledger.q("SELECT id, name, path, meta FROM sources WHERE system='doc' AND meta LIKE '%read_at%' ORDER BY last_changed DESC LIMIT 12"):
        try:
            m = json.loads(r['meta'] or '{}')
        except ValueError:
            continue
        if m.get('project') and m.get('summary'):
            out.append({'id': r['id'], 'name': ORG_WORDS.sub('our', str(r['name'] or '')), 'where': ORG_WORDS.sub('our', str(r['path'] or '')), 'read_at': m.get('read_at'),
                        'summary': ORG_WORDS.sub('our', m['summary']), 'mentions': [ORG_WORDS.sub('our', x) for x in (m.get('mentions') or [])[:4]]})
    return out[:8]


def _commitments():
    try:
        from jarvis.world import promises
        promises.ensure()                              # (kept / moot / not yet: world/promises.py checks them daily)
    except Exception:
        pass
    return ledger.q("SELECT id, who, to_whom, what, due, state, origin, source_ref, check_verdict, check_note, checked_at, nudged_at FROM commitments "
                    "WHERE dismissed_at IS NULL AND state='open' AND coalesce(scope, 'work') = 'work' ORDER BY due")


def _investigations():
    try:
        from jarvis.world import investigate
        ledger.x(investigate.TABLE)
        return [{**r, 'goal': ORG_WORDS.sub('our', str(r['goal'] or '')), 'question': ORG_WORDS.sub('our', str(r['question'] or ''))}
                for r in ledger.q("SELECT id, created, goal, origin, state, steps_done, passed, budget, question, rid, aid, updated FROM investigations "
                                  "WHERE state IN ('active','running','question') OR updated >= ? ORDER BY created DESC LIMIT 10",
                                  ((dt.datetime.now() - dt.timedelta(days=7)).isoformat(),))]
    except Exception:
        return []


def _desk():
    try:
        return desk.counts()
    except Exception:
        return None


def _research():
    try:
        from jarvis.world import research
        return research.recent()
    except Exception:
        return []


def _org_items():
    try:
        return [{**r, 'what': ORG_WORDS.sub('our', str(r['what'] or '')), 'place': ORG_WORDS.sub('our', str(r['place'] or ''))}
                for r in ledger.q("SELECT * FROM org_items WHERE created >= ? ORDER BY created DESC LIMIT 20",
                                  ((dt.datetime.now() - dt.timedelta(days=14)).isoformat(),))]
    except Exception:
        return []


def watch_post(b):
    """Dismiss or bring back a need, commitment or goal (one tap in Watching). A dismissed need never acts again."""
    kind, rid, op = str(b.get('kind', '')), str(b.get('id', '')), str(b.get('op', ''))
    table = {'need': 'needs', 'commitment': 'commitments', 'goal': 'goals'}.get(kind)
    if not table or not SAFE.match(rid) or op not in ('dismiss', 'restore'):
        raise ValueError('bad watch request')
    ledger.x(f'UPDATE {table} SET dismissed_at=? WHERE id=?', (ledger.now() if op == 'dismiss' else None, rid))
    ledger.event('watch_' + op, rid, 'owner', {'kind': kind})
    try:                                             # Phase 5: what he dismisses teaches what is not worth watching
        from jarvis import learned
        row = ledger.q(f'SELECT * FROM {table} WHERE id=?', (rid,))
        learned.signal(f'{kind}_{op}ed', 'owner_said', rid, text=str((row[0] if row else {}).get('statement') or (row[0] if row else {}).get('what') or '')[:300])
    except Exception:
        pass
    return {'ok': True}


def work_sweep():
    """Every 3 hours, 07:00-22:00: the work routine's own prompt over every open item (it notices what they already replied
    themselves, retries failed steps, writes up finished jobs, and stops at once when there is nothing to do). New items do not
    wait for it: they get a worker the moment they arrive (heartbeat). Replaces the hourly :30 run."""
    if not (7 <= dt.datetime.now().hour < 22):
        return 'outside hours'
    if not ledger.q("SELECT 1 FROM items WHERE done=0 AND kind != 'desk' LIMIT 1"):
        return 'no open items'
    if ledger.q("SELECT 1 FROM tasks WHERE state IN ('running','queued') AND lane='background' AND origin='inbox' LIMIT 1"):
        return 'inbox workers busy'
    return actions.run_now('work', origin='spine')


def share_check(q):
    """Is the shared folder there and writable from the server (the first write is when the system may ask the owner to
    allow Jarvis to use that folder)? Creates today's sub-folder only."""
    root = share_folder()
    if not root:
        return {'ok': False, 'error': 'no shared folder is set, or it is missing (settings: sharing.folder)'}
    try:
        os.makedirs(os.path.join(root, dt.date.today().isoformat()), exist_ok=True)
        return {'ok': True, 'link_set': bool(load(os.path.join(HUB, 'voice', 'settings.json'), {}).get('analysis_link'))}
    except OSError as e:
        return {'ok': False, 'error': f'{e.__class__.__name__}: {e}'[:200]}


HEART = None
GET['/api/spine'] = spine_status
GET['/api/share_check'] = share_check


def me(q=None):
    """What the screens need to know about the owner: the name to greet them by, the names their own records carry, where a
    ticket or a chat opens, their ticket projects, their own kinds of thing, the connected apps and the parts switched on."""
    from jarvis import entities
    c = config.load()
    name = c.owner()
    return {'set_up': c.is_set_up(), 'name': name, 'first': (name.split() or [''])[0], 'called': c.address_as(),
            'ticket_base': str(c.get('links.ticket_base') or ''), 'slack_workspace': str(c.get('sources.slack_workspace') or ''),
            'projects': c.own_projects(), 'kinds': {k: entities.folder(k) for k in sorted(entities.custom())},
            'apps': {'slack': c.has('slack'), 'tracker': c.has('atlassian'), 'mail': c.has('microsoft 365')},
            'share': bool(share_folder()), 'modules': sorted(MODULES),
            'media': os.path.isdir(os.path.join(OUT, 'media'))}      # the pictures and loops are an optional pack: the screens ask
    #                                                             for them only when they are there


GET['/api/me'] = me


def _setup():
    from jarvis import setup
    return setup


GET['/api/setup'] = lambda q=None: _setup().state(q)          # the Setup screen: steps, checks and suggestions (jarvis/setup.py)
GET['/api/watching'] = watching


JARVIS_PAGE_ONLY = ('/api/jarvis', '/api/jarvis_task', '/api/jarvis_prewarm', '/api/jarvis_drop', '/api/jarvis_model', '/api/run_now')


def post(path, b):
    """(body, status) for every POST except /api/jarvis (which streams)."""
    if path in ('/api/setup_save', '/api/setup_check', '/api/setup_dismiss', '/api/setup_learn'):       # the Setup screen (jarvis/setup.py)
        s = _setup()
        return {'/api/setup_save': s.save, '/api/setup_check': s.check, '/api/setup_dismiss': s.dismiss, '/api/setup_learn': s.learn}[path](b)
    if path == '/api/jarvis_task':
        return assistant.task(b), 200
    if path == '/api/jarvis_model':                       # the model switcher (voice set_model or the landing picker)
        return assistant.switch_model(str(b.get('tier', 'thinking')), str(b.get('model', ''))), 200
    if path == '/api/jarvis_prewarm':
        conv = b.get('conv') if isinstance(b.get('conv'), str) and len(b['conv']) < 80 else ''
        return assistant.POOL.prewarm(conv, assistant._settings()), 200
    if path == '/api/jarvis_drop':                        # the page took back a question (he went on, or the words were corrected)
        return assistant.drop(b), 200
    if path == '/api/consult_done':
        assistant.consult_delivered(str(b.get('id', ''))[:40])
        return {'ok': True}, 200
    if path == '/api/reminder_done':
        human.done(str(b.get('id', ''))[:20])
        return {'ok': True}, 200
    if path == '/api/inbox_tick':
        rid, keys = str(b.get('rid', '')), [str(k) for k in b.get('keys', [])]
        if not SAFE.match(rid) or not keys or not all(SAFE.match(k) for k in keys):
            return {'error': 'bad id'}, 400
        return tick(rid, keys, bool(b.get('done'))), 200
    if path == '/api/answer':
        return answer(b), 200
    if path == '/api/watch':
        return watch_post(b), 200
    if path in POST_EXTRA:                                # a route of an optional part that is switched on
        return POST_EXTRA[path](b), 200
    if path == '/api/share_file':
        return share_file(b), 200
    if path == '/api/run_now':                       # he asked for it: a round-up, a work run or ticket creation starts now
        return actions.run_now(str(b.get('what', ''))), 200
    if path == '/api/approvals_set':
        # his press is his approval: the approvals worker starts at once (one at a time), not at the hourly :15 run
        r = approvals_set(str(b.get('id', '')), str(b.get('status', '')), b.get('choice'))
        return ({**r, 'worker': actions._spawn()} if str(b.get('status', '')) == 'approved' else r), 200
    if path == '/api/key_check':                     # the page proves it holds the page key (passes the T2 gate)
        return {'ok': True}, 200
    if path == '/api/slack_leftover_done':           # he deleted that old draft in Slack: stop listing it
        return slack_send.leftover_done(str(b.get('rid', '')), str(b.get('aid', ''))), 200
    if path == '/api/forge_promote':                 # Learned: a tool Jarvis built becomes trusted (T3: the gateway)
        return _forge().promote(str(b.get('name', ''))[:41]), 200
    if path == '/api/skill_decide':                  # Learned: add a skill Jarvis drafted (T2: the gateway) or not now
        from jarvis.world import skills
        return skills.decide(str(b.get('name', ''))[:41], b.get('add') is not False), 200
    if path == '/api/research_mark':                 # Watching: a research finding is worth trying (an A2 investigation) or not
        from jarvis.world import research
        return research.mark(str(b.get('id', ''))[:20], str(b.get('verdict', ''))), 200
    if path == '/api/learned_undo':                  # Phase 5: Undo in the Learned view (the inverse goes through the validator)
        from jarvis import learned
        return learned.undo(str(b.get('id', ''))[:40]), 200
    if path == '/api/autopilot':                     # pause / resume Jarvis improving itself (the Learned view)
        from jarvis.spine import autopilot
        if b.get('op') == 'pause':
            open(autopilot.PAUSED, 'w').write(dt.datetime.now().astimezone().isoformat(timespec='seconds'))
        elif b.get('op') == 'resume' and os.path.exists(autopilot.PAUSED):
            os.remove(autopilot.PAUSED)
        return {'ok': True, 'paused': os.path.exists(autopilot.PAUSED)}, 200
    if path == '/api/learned_retire':                # one tap in Learned: a rule, note, memory or fact is retired (undoable)
        from jarvis import learned
        t = str(b.get('table', ''))
        if t not in ('rules', 'facts', 'memory', 'recipient_notes'):
            return {'ok': False, 'error': 'unknown table'}, 400
        res = learned.apply_edits([{'op': 'CLOSE', 'table': t, 'id': str(b.get('id', ''))[:40], 'recipient': b.get('recipient'),
                                    'channel': b.get('channel'), 'why': 'retired in Learned', 'origin': 'owner_said', 'source_ref': 'learned-view'}],
                                  run='learned-view')
        return {'ok': bool(res and res[0][1]), 'error': (res[0][2] if res and not res[0][1] else '')}, 200
    if path == '/api/learned_label':                 # the weekly label page: good / bad / skip on a past answer
        from jarvis import learning
        return learning.label(str(b.get('trace', ''))[:120], str(b.get('label', '')), b.get('note', ''), b.get('q', ''), b.get('answer', '')), 200
    if path == '/api/learn_signal':                  # Phase 5: the page reports his reactions (cancel during read-back, labels)
        k = str(b.get('kind', ''))
        if k not in ('cancel_readback', 'label_good', 'label_bad'):
            return {'ok': False, 'error': 'unknown signal'}, 400
        from jarvis import learned
        learned.signal(k, 'owner_said', str(b.get('ref', ''))[:120], label=str(b.get('label', ''))[:200], note=str(b.get('note', ''))[:300])
        return {'ok': True}, 200
    if path == '/api/slack_send':                    # his Send press / voice yes: app/slack_send.py posts it once, in code
        r = slack_send.start(str(b.get('rid', '')), str(b.get('aid', '')), str(b.get('text', '')), voice=bool(b.get('voice')))
        return r, 200
    if path in ('/api/ladder_raise', '/api/ladder_lower'):   # 12 Nov: his switch on the Trust screen. Up: one level, only if the
        from jarvis.spine import policy                              # class earned it and stays at or under his ceiling (policy.set_level)
        cls = str(b.get('cls', ''))[:40]
        cur = policy.level_of(cls)
        i = policy.ORDER.index(cur)
        if path == '/api/ladder_raise':
            r = policy.set_level(cls, policy.ORDER[min(i + 1, len(policy.ORDER) - 1)], 'page', 'raised by the owner in the Jarvis app')
        else:
            r = policy.set_level(cls, policy.ORDER[max(i - 1, 1)], 'owner', 'lowered by the owner') if i > 1 else {'ok': False, 'why': 'already at the lowest level'}
        ledger.event('ladder_' + ('set' if r.get('ok') else 'refused'), cls, 'owner', {'route': path, **{k: r.get(k) for k in ('level', 'was', 'why')}})
        return r, 200
    if path == '/api/held_stop':                              # his stop on a reply that is waiting out its 10 minutes: always wins
        from jarvis.spine import policy
        r = policy.stop(str(b.get('id', ''))[:40], 'owner')
        ledger.event('held_stopped' if r.get('ok') else 'held_stop_failed', str(b.get('id', ''))[:40], 'owner', {'why': r.get('why')})
        return r, 200
    if path == '/api/approvals_add':
        return {**approvals_add(b.get('item')), 'worker': actions._spawn()}, 200
    if path == '/api/tickets_save':
        return tickets_save(b.get('items')), 200
    if path == '/api/open_output':
        return open_output(b.get('path', ''))
    if path.startswith('/api/jira/'):
        return jira_route(path[len('/api/jira/'):], b), 200
    return {'error': 'not found'}, 404


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        # only page loads (GET /) go to server.log; assets and API calls stay quiet
        if self.command == 'GET' and self.path.split('?')[0] in ('/', '/index.html'):
            ua = 'Jarvis.app' if 'Jarvis' in (self.headers.get('User-Agent') or '') else 'browser'
            sys.stderr.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} GET / ({ua})\n')
            sys.stderr.flush()

    def send(self, obj, code=200, ctype='application/json'):
        body = obj if isinstance(obj, bytes) else (obj.encode() if isinstance(obj, str) else json.dumps(obj).encode())
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'no-store')
        # Page hardening (HARNESS.md §6.7): no other site may frame, script or read this page; scripts and data from here only
        # (Next's static build needs inline scripts and styles). nosniff and no referrer for every response.
        if ctype.startswith('text/html'):
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data: blob:; media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
            self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(body)

    def send_page(self, path):
        """The built app from web/out (/, /_next/..., /favicon.ico)."""
        base = OUT
        rel = 'index.html' if path in ('', '/') else os.path.normpath(urllib.parse.unquote(path).lstrip('/'))
        if rel.startswith('..') or os.path.isabs(rel):
            return self.send({'error': 'not found'}, 404)
        p = os.path.join(base, rel)
        if os.path.isdir(p):
            p = os.path.join(p, 'index.html')
        elif not os.path.isfile(p) and os.path.isfile(p + '.html'):
            p += '.html'
        if not os.path.isfile(p):
            if not os.path.isdir(base):
                return self.send(f'{os.path.relpath(base, REPO)} is missing: build it with "npm run build" in the web folder.', 503, 'text/plain; charset=utf-8')
            return self.send({'error': 'not found'}, 404)
        ext = os.path.splitext(p)[1].lower()
        if ext in ('.mp4', '.webm'):                     # WebKit plays video only with byte ranges
            return self.send_media(p, TYPES[ext])
        return self.send(open(p, 'rb').read(), ctype=TYPES.get(ext, 'application/octet-stream'))

    def send_media(self, p, ctype):
        size = os.path.getsize(p)
        m = re.match(r'bytes=(\d*)-(\d*)$', (self.headers.get('Range') or '').strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                a = int(m.group(1)); b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                a = max(0, size - int(m.group(2))); b = size - 1
            if a > b or a >= size:
                self.send_response(416); self.send_header('Content-Range', f'bytes */{size}'); self.end_headers(); return
            with open(p, 'rb') as f:
                f.seek(a); body = f.read(b - a + 1)
            self.send_response(206); self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
        else:
            body = open(p, 'rb').read(); self.send_response(200)
        self.send_header('Content-Type', ctype); self.send_header('Content-Length', len(body))
        self.send_header('Accept-Ranges', 'bytes'); self.send_header('Cache-Control', 'no-cache')  # 13 Nov: a replaced film must show at once
        self.send_header('X-Content-Type-Options', 'nosniff'); self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers(); self.wfile.write(body)

    def host_ok(self):
        """Only requests addressed to this Mac's server (blocks DNS-rebinding pages); a POST from a page on another origin is
        refused too. curl from the routines sends no Origin and keeps working."""
        if (self.headers.get('Host') or '') not in (f'localhost:{PORT}', f'127.0.0.1:{PORT}'):
            return False
        o = self.headers.get('Origin')
        return not (self.command == 'POST' and o and o not in (f'http://localhost:{PORT}', f'http://127.0.0.1:{PORT}'))

    def do_GET(self):
        if not self.host_ok():
            return self.send({'error': 'forbidden'}, 403)
        u = urllib.parse.urlparse(self.path)
        if u.path == '/presenter.js':                     # the presenter's patterns for the page, before its own code (Phase 3)
            body = presenter.js().encode()
            self.send_response(200); self.send_header('Content-Type', 'application/javascript'); self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if u.path == '/healthz':                          # the supervisors' liveness probe (spine/health.py): cheap, never touches the brain
            if 'deep=1' in (u.query or ''):               # Phase 4: per-subsystem state (slower: logins, VPN; cached)
                return self.send(health.deep())
            return self.send(health.healthz())
        if not u.path.startswith('/api/'):
            return self.send_page(u.path)
        fn = GET.get(u.path)
        if not fn:
            return self.send({'error': 'not found'}, 404)
        try:
            out = fn(dict(urllib.parse.parse_qsl(u.query)))
        except Exception as e:                            # a screen gets an answer it can show, never a dropped connection
            sys.stderr.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} GET {u.path} failed: {e.__class__.__name__}: {e}\n'); sys.stderr.flush()
            return self.send({'error': f'{e.__class__.__name__}: {e}'[:300]}, 500)
        return self.send(out)

    def do_POST(self):
        if not self.host_ok():
            return self.send({'error': 'forbidden'}, 403)
        n = int(self.headers.get('Content-Length') or 0)
        if self.path in JARVIS_PAGE_ONLY:
            # Jarvis: only from the app's own page, with small requests (q <= 2,000 chars, screen <= 14 KB)
            if not assistant.origin_ok(self.headers):
                return self.send({'error': 'forbidden'}, 403)
            if n > 16 * 1024:
                return self.send({'error': 'request too large'}, 413)
        b = json.loads(self.rfile.read(n) or b'{}')
        if self.path == '/api/jarvis':
            q, scr = b.get('q'), json.dumps(b.get('screen') or {})
            if not isinstance(q, str) or not q.strip() or len(q) > assistant.MAX_Q or len(scr.encode()) > assistant.MAX_SCREEN:
                return self.send({'error': 'q must be 1-2000 characters and screen at most 14 KB'}, 400)
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return assistant.handle(b, self.headers, self.wfile, load(os.path.join(assistant.VOICE, 'settings.json'), {}))
        ok, err = gateway.check(self.path, b, self.headers)       # T2/T3 only from the Jarvis page (spine/gateway.py)
        if not ok:
            return self.send({'ok': False, 'error': err}, 403)
        act, prev = gateway.begin(self.path, b, self.headers)    # recorded, with an idempotency key
        if prev is not None:
            return self.send(*prev)
        try:
            body, code = post(self.path, b)
        except (ValueError, TypeError) as e:
            body, code = {'error': str(e)}, 400
        gateway.end(act, self.path, b, body, code)
        self.send(body, code)


def bind():
    """The listening server. If the port is taken, another copy already serves: exit at once. A copy that stands by and
    takes the port over later would run the code it loaded hours earlier; whoever started this one starts a fresh server
    when none is listening, so nothing is lost by exiting."""
    try:
        return http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        sys.stderr.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} port {PORT} is taken by another server (pid {os.getpid()} exits)\n'); sys.stderr.flush()
        os._exit(0)


def _tool_job(name, *args, out=None):
    """A timed job that runs a command-line tool in its own process (never holding the scheduler). None when that tool is
    not part of this copy."""
    tool = paths.tool(name)
    if not tool.exists():
        return None
    def start():
        log = open(out, 'a') if out else subprocess.DEVNULL
        return subprocess.Popen([paths.python(), str(tool), *args], cwd=HUB, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT if out else subprocess.DEVNULL, start_new_session=True)
    return start


def world_jobs(since):
    """(name, every N seconds, what to run): everything Jarvis does on a timer besides the inbox. Each job decides inside
    whether it has anything to do (no connected app, nothing new, the usage governor holding it back)."""
    jobs = [('catalog_local', 1800, lambda: catalog.scan('local')), ('mine_memory', 1800, needs.mine_memory), ('mine_notes', 1800, needs.mine_notes),
            ('mine_sessions', 900, lambda: needs.mine_transcripts([f for f in needs.finished_sessions() if os.path.getmtime(f) > since])),
            ('match', 900, match.run), ('initiatives', 600, lambda: (initiative.reap(), initiative.settle_outcomes(), initiative.admit(), initiative.anomalies())),
            ('work_sweep', 3 * 3600, work_sweep), ('mine_reports', 600, needs.mine_reports),
            ('recognizers', 86400, needs.recognizer_selftest), ('connectors', 86400, connectors.daily),
            ('merge', 6 * 3600, needs.merge_duplicates), ('age', 86400, needs.age_pass),
            ('sent_check', 1800, lambda: sentcheck.check() if 7 <= dt.datetime.now().hour < 23 else 'night'),
            ('scope', 900, needs.classify_scope),          # new needs/promises: work or not (only work is watched)
            # around the owner's work (decisions, needs, promises, documents) and colleagues' branches
            ('org_watch', 86400, org.daily), ('org_branches', 6 * 3600, org.branches),
            ('org_docs', 3 * 3600, docs.probe),            # read what the watch found (a few a day)
            # investigations (multi-step, one read-only step per session; caps + governor inside)
            ('investigate', 120, lambda: (investigate.seed(), investigate.take_answers(), investigate.step())),
            ('goals', 7 * 86400, investigate.seed_goals),
            ('research', 7 * 86400, research.scout),       # outside papers and techniques, weekly
            ('research_trials', 600, research.tick),       # every finding is tried by Jarvis itself and settled into a verdict
            ('promises', 86400, promises.review),          # kept? (daily, read-only)
            ('desk', 600, desk.collect),                   # Jarvis's own work -> desk entries, kept until the fortnightly brief
            ('desk_tidy', 86400, desk.tidy),               # merge duplicates, rank, keep the topic briefs current
            ('desk_brief', 3600, desk.brief_due),          # every second Friday: the brief, once
            ('promise_followups', 3600, promises.followups),   # draft a nudge
            ('skills', 86400, lambda: (skills.retire(), skills.mine())),
            ('semantic', 1800, semantic.run)]              # new data vs needs, by meaning
    # tools that run in their own process: the scorecard, and the nightly refresh of the knowledge base
    for name, every, job in (('scorecard', 600, _tool_job('scorecard.py', '--json')),
                             ('kb_refresh', 3600, _tool_job('kb_refresh.py', '--if-due', out=os.path.join(HUB, 'brain', 'kb_refresh.out')))):
        if job:
            jobs.append((name, every, job))
    for m in MODULES.values():                           # the optional parts that are switched on bring their own
        jobs += list(getattr(m, 'jobs', lambda: [])())
    return jobs


def main(argv=None):
    global HEART
    parse_args(sys.argv[1:] if argv is None else argv)
    # Started as a background job of a shell ("nohup ... &"), this process begins with SIGINT and SIGQUIT IGNORED, which
    # every worker would inherit. Preemption pauses a worker with SIGINT. A handled signal resets to the default in every
    # child at exec; an ignored one stays ignored. (Not every system has SIGQUIT.)
    import signal as _signal
    for _s in (getattr(_signal, 'SIGINT', None), getattr(_signal, 'SIGQUIT', None)):
        if _s is not None:
            _signal.signal(_s, lambda *_: None)
    srv = bind()
    # what every worker inherits and cannot change: may shared text name the assistant? (tools/worker_hook.py reads it)
    os.environ['JARVIS_AI_WORDING'] = str(config.load().get('sharing.ai_wording') or 'never')
    os.environ['JARVIS_PORT'], os.environ['JARVIS_PYTHON'] = str(PORT), paths.python()      # for the restart script
    ledger.connect().close()                         # the ledger exists, with its tables, before anything reads it
    health.install(PORT)     # crash logs, thread deaths, pidfile (spine/health.py): never die silently
    if tracker.use_token():
        health.PROVIDERS['tracker'] = token_works
    log = lambda m: (sys.stderr.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}\n'), sys.stderr.flush())
    on = load_modules(log)
    if config.load().is_set_up():                       # the workers' skills, filled in from the settings as they are now
        try:
            from jarvis import skillset
            skillset.install()
        except Exception as e:
            print(f'skills not refreshed: {e.__class__.__name__}: {e}', flush=True)
    print(f'Jarvis on http://localhost:{PORT} (pid {os.getpid()})' + (f'; optional parts: {", ".join(on)}' if on else ''), flush=True)
    if os.environ.get('JARVIS_NO_BACKGROUND') != '1':    # a test copy on another port: no assistant process, pings or memory runs
        assistant.warm_at_start()   # the warm assistant's spare process (settings.json warm_brain)
        notify.start(needs_now, log=log)   # pings: approvals waiting, long jobs ended, decisions needing the owner
        episodes.start()   # Jarvis's longer memory: consolidates quiet conversations every 10 min, tidies facts daily
        # the one scheduler (spine/heartbeat.py): lanes, inbox watcher, new items -> background workers, approvals reconciler
        gateway.load_page_key()                    # before anything can ask for a T2/T3 action
        dispatcher.write_settings()
        if not ledger.cursor('sessions_since'):       # chats are mined from the first start on; older ones need the owner's OK
            ledger.cursor('sessions_since', time.time())
        since = float(ledger.cursor('sessions_since'))
        HEART = heartbeat.Heartbeat(changed_v, lambda what: actions.run_now(what, origin='spine'), actions._spawn,
                                    actions._worker_alive, extra=world_jobs(since))
        HEART.on_leave_slack, HEART.front = sentcheck.check, sentcheck.front_app      # they left Slack: did they send a draft themselves?
        HEART.start()

        def _policy_loop():
            # The ladder's timer on its own thread: world jobs run one at a time, and a held reply must be sent, stopped or
            # read back on time whatever else is busy. With no kind of action at "holds, then sends" and nothing held,
            # tick() returns 'off' at once.
            from jarvis.spine import policy
            while True:
                try:
                    policy.tick(tell=_tell_held)
                except Exception as e:
                    try:
                        ledger.event('policy_error', 'tick', 'spine', {'error': f'{e.__class__.__name__}: {e}'[:300]})
                    except Exception:
                        pass
                time.sleep(30)
        threading.Thread(target=_policy_loop, daemon=True, name='policy').start()
        slack_send.HOOKS.update(tick=tick, share_files=share_files)
        threading.Thread(target=drafts_loop, daemon=True, name='slack-drafts').start()
        from jarvis.spine import governor
        threading.Thread(target=governor.loop, daemon=True, name='governor').start()   # the usage scan, every 10 min
    srv.serve_forever()


if __name__ == '__main__':
    main()
