"""Setup: the steps the Setup screen shows, what is in place for each, and suggestions measured on this computer.

One place for every check. The same checks feed the screen later as "what is connected", so a broken connection shows up
there and not as silence. Nothing here acts by itself: a check runs when the owner presses it, a setting changes when they
save it, and a suggestion only ever offers.

    state()          GET  /api/setup           the steps, with what is in place, and the suggestions
    save(b)          POST /api/setup_save      {"set": {"owner.name": "Sam Lee", ...}}; every value is checked first
    check(b)         POST /api/setup_check     {"what": "claude"}: one short Claude run; also finds the connected apps
    dismiss(b)       POST /api/setup_dismiss   {"id": "..."}: hide a suggestion for good
    learn(b)         POST /api/setup_learn     start the first learning pass (tools/learn_first.py)
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

from . import config, paths
from jarvis import procs

CONNECTORS_PAGE = 'https://claude.ai/settings/connectors'
CLAUDE_PAGE = 'https://claude.com/claude-code'
# The apps Jarvis knows how to read through the owner's Claude account, by a word of the connector's name.
APPS = [('slack', 'Slack', 'chat'), ('atlassian', 'Jira and Confluence', 'tickets and pages'),
        ('microsoft 365', 'Outlook, Teams and SharePoint', 'mail, calendar and files')]
# What Setup may change. Everything else in the settings file belongs to a later version or to the owner's own hand.
EDITABLE = ('owner.', 'org.', 'work.', 'engine.', 'sources.', 'tracker.', 'knowledge.', 'vault.', 'research.', 'sharing.',
            'notify.', 'health.', 'modules.', 'links.')
MCP_ENV = {'CLAUDE_CODE_MCP_STARTUP_WAIT_MS': '20000', 'CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS': '20000'}
_cache = {}


def _cached(name, secs, fn):
    hit = _cache.get(name)
    if hit and time.time() - hit[0] < secs:
        return hit[1]
    val = fn()
    _cache[name] = (time.time(), val)
    return val


def forget():
    """Drop what was measured, so the next look measures again (after a save or a check)."""
    _cache.clear()


def _state_file():
    return str(paths.state('setup.json'))


def _load():
    try:
        d = json.load(open(_state_file(), encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _store(d):
    p = _state_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(p + '.tmp', p)


def _short(p):
    """A folder as the owner would write it: ~/Work/Atlas."""
    home = os.path.expanduser('~')
    p = str(p)
    return '~' + p[len(home):] if p == home or p.startswith(home + os.sep) else p


# ---------------------------------------------------------------- Claude
def claude():
    """Is Claude Code here and signed in? No model call: the command's own version and sign-in status only. Never the
    account's address or organisation."""
    def look():
        exe = paths.claude_real()
        if not exe:
            return {'found': False, 'signed_in': False,
                    'fix': f'Claude Code is not installed on this computer. Install it from {CLAUDE_PAGE}, sign in, then press Check again.'}
        out = {'found': True, 'signed_in': False, 'version': '', 'method': '', 'plan': ''}
        try:
            v = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
            out['version'] = (v.stdout or '').strip().split()[0] if (v.stdout or '').strip() else ''
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            a = subprocess.run([exe, 'auth', 'status'], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
            d = json.loads(a.stdout or '{}')
            out['signed_in'] = bool(d.get('loggedIn'))
            out['method'] = str(d.get('authMethod') or '')[:40]
            out['plan'] = str(d.get('subscriptionType') or '')[:40]
        except (OSError, ValueError, subprocess.SubprocessError):
            out['unknown'] = True                     # an older Claude Code: the test below is the proof
        if config.load().get('engine.mode') == 'api-key':          # the key stands where the sign-in would
            from . import keystore
            out.update(signed_in=keystore.has(), unknown=False, key=True, key_where=keystore.where(), key_store=bool(keystore.backend()))
            if not out['signed_in']:
                out['fix'] = 'No API key yet. Paste it below: it is kept in ' + keystore.where().split(':')[0] + ', never in a file.' if keystore.backend() else keystore.where()
            return out
        if not out['signed_in'] and not out.get('unknown'):
            out['fix'] = 'Claude Code is installed but not signed in. Run "claude" in a terminal once and sign in, then press Check again.'
        return out
    c = dict(_cached('claude', 60, look))
    c['test'] = _load().get('claude_test') or None
    return c


def test_claude():
    """One short Claude run: proves it answers, and reads from its first line which apps are connected to the owner's
    Claude account (names and state only). The owner's press; costs a few tokens."""
    exe = paths.claude_bin()
    if not exe:
        return {'ok': False, 'error': claude().get('fix', 'Claude Code was not found.')}
    paths.ensure()
    t0, said, servers, err = time.time(), '', None, ''
    try:
        p = subprocess.run([exe, '-p', 'Reply with the one word: ready', '--model', config.model('quick'), '--output-format', 'stream-json',
                            '--verbose', '--tools', ''], capture_output=True, text=True, timeout=180, cwd=str(paths.home()),
                           env=dict(os.environ, **MCP_ENV), stdin=subprocess.DEVNULL)
        for line in (p.stdout or '').splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get('type') == 'system' and d.get('subtype') == 'init' and isinstance(d.get('mcp_servers'), list):
                servers = [{'name': str(x.get('name') or '')[:80], 'status': str(x.get('status') or '')[:30]} for x in d['mcp_servers'] if isinstance(x, dict)]
            elif d.get('type') == 'rate_limit_event':
                try:
                    from .spine import governor
                    governor.note_window(d.get('rate_limit_info'))
                except Exception:  # noqa: BLE001  the meter is a courtesy here
                    pass
            elif d.get('type') == 'result':
                said = str(d.get('result') or '')[:200]
                if d.get('is_error'):
                    err = said or 'Claude answered with an error.'
        if not said and not err:
            err = ((p.stderr or '').strip().splitlines() or ['Claude gave no answer.'])[-1][:200]
    except subprocess.TimeoutExpired:
        err = 'Claude did not answer within three minutes.'
    except OSError as e:
        err = f'Claude could not be started ({e.__class__.__name__}).'
    ok = not err and 'ready' in said.lower()
    if not ok and not err:
        err = f'Claude answered, but not as asked: "{said[:80]}".'
    res = {'ok': ok, 'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'secs': round(time.time() - t0, 1), 'error': err}
    st = _load()
    st['claude_test'] = res
    _store(st)
    filled = []
    if servers is not None:
        p_ = str(paths.state('connectors.json'))
        with open(p_ + '.tmp', 'w', encoding='utf-8') as f:
            json.dump({'at': time.time(), 'servers': servers}, f)
        os.replace(p_ + '.tmp', p_)
        c = config.load()
        if ok and c.get('sources.claude_connectors') and not c.get('sources.expected'):
            filled = [s['name'] for s in servers if s['status'] == 'connected' and any(w in s['name'].lower() for w, _, _ in APPS)]
            if filled:                               # first time: the apps found connected are the ones their work is read from
                config.set('sources.expected', filled)
    forget()
    return dict(res, connectors=servers or [], expected_set=filled)


def connectors():
    """The connectors Claude last reported (names and state), and when that was. Empty until a check has run."""
    try:
        d = json.load(open(str(paths.state('connectors.json')), encoding='utf-8'))
    except (OSError, ValueError):
        return {'at': None, 'servers': []}
    at = d.get('at')
    return {'at': dt.datetime.fromtimestamp(at).astimezone().isoformat(timespec='seconds') if isinstance(at, (int, float)) else None,
            'servers': [s for s in d.get('servers') or [] if isinstance(s, dict)]}


def ready():
    """What background work may start yet: {'claude': Claude answered Setup's check, 'apps': an app is connected too}.
    Before that, a round-up or a scan can only fail, or spend the owner's allowance reading nothing (found in the new-user
    trial, 14 Nov: the first round-up started a minute after install, with Claude signed out)."""
    ok = bool((_load().get('claude_test') or {}).get('ok'))
    return {'claude': ok, 'apps': ok and any(s.get('status') == 'connected' for s in connectors()['servers'])}


def apps():
    """One row per app Jarvis can read through the owner's Claude account: connected or not, by Claude's last report."""
    c, seen = config.load(), connectors()
    on = bool(c.get('sources.claude_connectors'))
    if c.get('engine.mode') == 'api-key':
        return {'on': False, 'checked': seen['at'], 'api_key': True, 'other': [],
                'rows': [{'id': w.replace(' ', '_'), 'label': label, 'what': what, 'state': 'needs its own tool server', 'used': False,
                          'fix': 'With an API key, the apps connected to a Claude account are out of reach. Add a tool server for it to the file named in sources.mcp_config, and its read tools to sources.read_tools.'}
                         for w, label, what in APPS]}
    rows = []
    for word, label, what in APPS:
        hit = next((s for s in seen['servers'] if word in str(s.get('name', '')).lower()), None)
        state = 'unknown' if seen['at'] is None else (str(hit.get('status')) if hit else 'not connected')
        rows.append({'id': word.replace(' ', '_'), 'label': label, 'what': what, 'state': state if on else 'off',
                     'used': on and c.has(word), 'fix': '' if state == 'connected' else
                     ('Press "Check Claude and my apps" to find out.' if state == 'unknown' else
                      f'Connect it in Claude ({CONNECTORS_PAGE}), then press Check again.')})
    other = [str(s.get('name')) for s in seen['servers'] if s.get('status') == 'connected'
             and not any(w in str(s.get('name', '')).lower() for w, _, _ in APPS) and str(s.get('name', '')).lower() not in ('jarvis', 'repos')]
    return {'on': on, 'checked': seen['at'], 'rows': rows, 'other': sorted(other)[:20]}


def reads():
    """What learning reads from the apps, as settings name it (tools/kb_live.py reads these)."""
    c = config.load()
    return {'site': str(c.get('tracker.cloud_id') or ''), 'values': {
        'sources.tracker_projects': c.get('sources.tracker_projects') or [], 'sources.wiki_spaces': c.get('sources.wiki_spaces') or [],
        'sources.channels': c.get('sources.channels') or [], 'sources.mail_terms': c.get('sources.mail_terms') or [],
        'sources.since': str(c.get('sources.since') or '')}}


# ---------------------------------------------------------------- chats and folders
def _chat_dirs():
    """[(folder name, number of chats, newest change)] of Claude Code's chat folders, without Jarvis's own runs."""
    def look():
        own, out = paths.own_runs_prefix(), []
        try:
            dirs = sorted(d for d in paths.claude_chats().iterdir() if d.is_dir())
        except OSError:
            return out
        for d in dirs:
            if d.name == own or d.name.startswith(own + '-'):
                continue
            try:
                files = [f for f in d.iterdir() if f.name.endswith('.jsonl')]
            except OSError:
                continue
            if files:
                out.append((d.name, len(files), max(f.stat().st_mtime for f in files)))
        return out
    return _cached('chat_dirs', 30, look)


def chats():
    rows = _chat_dirs()
    in_scope = {d.name for d in paths.work_chat_dirs()}
    return {'on': bool(config.load().get('sources.chats')), 'found': sum(n for _, n, _ in rows), 'projects': len(rows),
            'in_scope': sum(n for name, n, _ in rows if name in in_scope)}


def _enc(name):
    return re.sub(r'[^A-Za-z0-9]', '-', name)


def decode_chat_folder(name, limit=20000):
    """The real folder behind one of Claude Code's chat folder names (every character that is not a letter or a digit became
    a dash, so the name alone is ambiguous): found by walking the disk. '' when it no longer exists."""
    want, seen = name, [0]

    def walk(path, rest):
        if not rest:
            return path
        if seen[0] > limit or not rest.startswith('-'):
            return ''
        try:
            entries = sorted(os.listdir(path or os.sep))
        except OSError:
            return ''
        for e in entries:
            seen[0] += 1
            enc = _enc(e)
            if rest[1:].startswith(enc) and (len(rest) == len(enc) + 1 or rest[len(enc) + 1] == '-'):
                full = os.path.join(path or os.sep, e)
                if os.path.isdir(full):
                    got = walk(full, rest[len(enc) + 1:])
                    if got:
                        return got
        return ''
    return walk('', want) if want.startswith('-') else ''


def folders():
    c = config.load()
    rows = []
    for f in c.get('work.folders') or []:
        d = os.path.expanduser(str(f))
        rows.append({'path': str(f), 'ok': os.path.isdir(d) and os.access(d, os.R_OK), 'why': '' if os.path.isdir(d) else 'this folder does not exist'})
    return {'rows': rows, 'workdir': _short(paths.workdir())}


# ---------------------------------------------------------------- learning
def learning():
    """Where the knowledge base stands: is a first pass running, what it has made so far, when the night's update last ran."""
    def count(d, ext='.md'):
        try:
            return sum(1 for f in os.listdir(str(d)) if f.endswith(ext))
        except OSError:
            return 0
    try:
        st = json.load(open(str(paths.state('learn_first.json')), encoding='utf-8'))
    except (OSError, ValueError):
        st = {}
    running = False
    if st.get('pid') and not st.get('ended'):
        try:
            os.kill(int(st['pid']), 0)
            running = True
        except (OSError, ValueError):
            running = False
    try:
        night = json.load(open(os.path.join(str(paths.home()), 'brain', 'kb_refresh.json'), encoding='utf-8')).get('last_run')
    except (OSError, ValueError, AttributeError):
        night = None
    notes, kb = count(paths.brain('sessions')), count(paths.kb())
    return {'running': running, 'step': st.get('step') if running else '', 'started': st.get('started'), 'ended': st.get('ended'),
            'ok': st.get('ok'), 'error': st.get('error') or ('' if running or st.get('ended') or not st.get('pid') else 'The learning pass stopped before it finished. Press Start learning to carry on: nothing is read twice.'),
            'chat_notes': notes, 'kb_notes': kb, 'built': kb > 0, 'night': night}


def learn(b=None):
    """Start the first learning pass in its own process. One at a time; a pass that was cut short carries on."""
    if learning()['running']:
        return {'ok': False, 'error': 'A learning pass is already running.'}, 409
    c = claude()
    if not c.get('found') or (not c.get('signed_in') and not c.get('unknown')):
        return {'ok': False, 'error': c.get('fix') or 'Claude Code is not ready.'}, 400
    if not chats()['in_scope']:
        return {'ok': False, 'error': 'There are no Claude Code chats in your work folders yet. Add a folder you have worked in with Claude Code, or come back after your first chats.'}, 400
    paths.ensure()
    log = open(str(paths.logs('learn_first.log')), 'a')
    p = subprocess.Popen([paths.python(), str(paths.tool('learn_first.py'))], cwd=str(paths.home()), stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, **procs.session_kw())
    forget()
    return {'ok': True, 'pid': p.pid}, 200


# ---------------------------------------------------------------- the optional parts and trust
def optional():
    c = config.load()
    on = c.get('modules') or {}
    voice_models = os.path.isfile(os.path.join(str(paths.voice()), 'models', 'hey_jarvis_v0.1.onnx'))
    voice_env = os.path.isfile(os.path.join(str(paths.home()), 'envs', 'voice', 'bin', 'python'))
    vault = os.path.expanduser(str(c.get('vault.path') or ''))
    return [
        {'id': 'voice', 'label': 'Wake word and turn-taking (Mac app)', 'on': voice_models and voice_env, 'mac_only': True,
         'status': 'set up' if voice_models and voice_env else 'not set up: the app listens when you press, and speaks with the Mac\'s own voice',
         'how': 'In the Jarvis folder run: python3 voice/setup.py'},
        {'id': 'vault', 'label': 'Mirror the work into a notes folder (an Obsidian vault)', 'on': bool(on.get('vault')) and os.path.isdir(vault),
         'status': ('on, into ' + _short(vault)) if on.get('vault') and os.path.isdir(vault) else ('switched on, but the folder is missing' if on.get('vault') else 'off'),
         'keys': ['modules.vault', 'vault.path'], 'value': str(c.get('vault.path') or '')},
        {'id': 'prwatch', 'label': 'Pull-request watch', 'on': bool(on.get('prwatch')) and bool(str(c.get('sources.code_workspace') or '').strip()),
         'status': 'on' if on.get('prwatch') and c.get('sources.code_workspace') else ('switched on, but no workspace is named' if on.get('prwatch') else 'off'),
         'keys': ['modules.prwatch', 'sources.code_workspace'], 'value': str(c.get('sources.code_workspace') or '')},
        {'id': 'deploy_gate', 'label': 'Deploy gate: review what is about to be pushed', 'on': bool(on.get('deploy_gate')),
         'status': _gate_status(bool(on.get('deploy_gate'))), 'keys': ['modules.deploy_gate'],
         'how': 'What it watches is named in the settings file ([gate]); docs/DEPLOY-GATE.md says how'},
    ]


def _gate_status(on):
    """The deploy gate in one line: off, on with what it watches, or switched on with what is still missing."""
    if not on:
        return 'off'
    from .modules import deploy_gate
    s = deploy_gate._settings()
    if not s.get('ready'):
        return 'switched on, but ' + str(s.get('why') or 'not set up')
    n = len(s.get('repos') or [])
    return f"on, watching {n} repositor{'y' if n == 1 else 'ies'}"


def trust():
    """How far Jarvis may act alone, per kind of action. A new install starts with every kind at "your press"."""
    from . import ladder
    try:
        from .spine import policy
        acting = policy.acting_on(db=str(paths.state('jarvis.db')))
    except Exception:  # noqa: BLE001  no record yet: nothing has been raised
        acting = {}
    labels = {c[0]: c[1] for c in ladder.CLASSES}
    raised = [labels.get(c, c) for c in sorted(acting)]
    return {'kinds': len(ladder.CLASSES), 'raised': raised,
            'line': 'Every kind of action waits for your press.' if not raised else f'{len(raised)} kind(s) of action may go out after a hold: ' + ', '.join(raised[:4])}


# ---------------------------------------------------------------- suggestions
def _obsidian_vaults():
    out = []
    for p in (os.path.expanduser('~/Library/Application Support/obsidian/obsidian.json'), os.path.expanduser('~/.config/obsidian/obsidian.json'),
              os.path.join(os.environ.get('APPDATA') or '', 'obsidian', 'obsidian.json')):
        try:
            d = json.load(open(p, encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for v in (d.get('vaults') or {}).values():
            if isinstance(v, dict) and os.path.isdir(str(v.get('path') or '')):
                out.append(str(v['path']))
    return out


def _remote_base():
    """The address most of the owner's repositories share, without any user or password part ('' when there is none)."""
    bases = {}
    roots = [str(paths.workdir())] + [os.path.expanduser(str(x)) for x in config.load().get('work.folders') or []]
    for root in roots:
        try:
            subs = [root] + [os.path.join(root, s) for s in sorted(os.listdir(root))]
        except OSError:
            continue
        for repo in subs:
            if not os.path.isdir(os.path.join(repo, '.git')):
                continue
            try:
                u = subprocess.run(['git', '-C', repo, 'remote', 'get-url', 'origin'], capture_output=True, text=True, timeout=10,
                                   stdin=subprocess.DEVNULL).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
            u = re.sub(r'^(https?://)[^@/\s]*@', r'\1', u)
            m = re.match(r'^(https://[^/\s]+/[^/\s]+/)[^/\s]+?(?:\.git)?/?$', u)
            if m:
                bases.setdefault(m.group(1), []).append(os.path.basename(repo))
    if not bases:
        return '', []
    base = max(bases, key=lambda b: len(bases[b]))
    return base, sorted(set(bases[base]))[:12]


def suggestions():
    """What is worth doing next, each from something measured on this computer, each with one line of why. Never acted on
    without the owner's press; "Not now" hides one for good."""
    c, st, out = config.load(), _load(), []
    hidden = set(st.get('dismissed') or [])

    def add(sid, title, why, **action):
        if sid not in hidden:
            out.append(dict(id=sid, title=title, why=why, **action))
    ch, lr, cl = chats(), learning(), claude()
    have = [os.path.realpath(os.path.expanduser(str(f))) for f in c.get('work.folders') or []]
    if ch['found']:
        top, skip = [], {os.path.realpath(os.path.expanduser('~')), os.path.realpath(str(paths.home())), os.sep}
        for name, n, _ in sorted(_chat_dirs(), key=lambda r: -r[1])[:8]:
            real = decode_chat_folder(name)
            if real and os.path.realpath(real) not in skip and not any(os.path.realpath(real) == h or os.path.realpath(real).startswith(h + os.sep) for h in have):
                top.append((real, n))
        for real, n in top[:3]:
            add('folder:' + real, f'Add {_short(real)} as a work folder', f'{n} of your Claude Code chats were held there.',
                label='Add it', save={'work.folders': list(c.get('work.folders') or []) + [_short(real)]})
    if ch['in_scope'] and not lr['built'] and not lr['running'] and cl.get('found'):
        add('learn', f"Learn from your {ch['in_scope']} Claude Code chats", 'They are in your work folders and nothing has been learned from them yet.',
            label='Start learning', run='learn')
    seen = connectors()
    if seen['at'] is None and cl.get('found'):
        add('check', 'Check Claude and your apps', 'One short Claude run shows that it answers and which of your apps are connected.', label='Check now', run='check')
    else:
        rows = {r['id']: r for r in apps()['rows']}
        connected = [r['label'] for r in rows.values() if r['state'] == 'connected']
        for r in rows.values():
            if r['state'] != 'connected' and connected:
                add('connect:' + r['id'], f"Connect {r['label']}", f"Only {', '.join(connected)} {'is' if len(connected) == 1 else 'are'} connected, so {r['what']} are not read.",
                    label='Open Claude', href=CONNECTORS_PAGE)
    if not c.get('modules.vault'):
        for v in _obsidian_vaults()[:1]:
            add('vault:' + v, f'Mirror your work into your notes vault at {_short(v)}', 'An Obsidian vault is on this computer. Jarvis writes only into its own folder there.',
                label='Switch it on', save={'modules.vault': True, 'vault.path': _short(v)})
    if not str(c.get('sources.code_url') or '').strip() and not str(c.get('sources.code_workspace') or '').strip():
        base, names = _remote_base()
        if base:
            add('code:' + base, 'Let the assistant read your repositories on ' + base.split('/')[2], f"{len(names)} of your repositories live under {base} ({', '.join(names[:4])}). Read-only.",
                label='Use it', save={'sources.code_url': base})
    if sys.platform == 'darwin' and not optional()[0]['on']:
        add('voice', 'Set up the wake word', 'On a Mac, Jarvis can wake when you say its name. Five small open models, about 15 MB.', label='How', note=optional()[0]['how'])
    return out


def dismiss(b):
    sid = str((b or {}).get('id') or '')[:300]
    if not sid:
        return {'ok': False, 'error': 'which suggestion?'}, 400
    st = _load()
    st['dismissed'] = sorted(set(st.get('dismissed') or []) | {sid})
    _store(st)
    forget()
    return {'ok': True}, 200


# ---------------------------------------------------------------- the steps
def steps():
    c, cl, ch, ap, fo, lr = config.load(), claude(), chats(), apps(), folders(), learning()
    test = cl.get('test') or {}
    any_app = any(r['state'] == 'connected' for r in ap['rows'])
    return [
        {'id': 'you', 'title': 'You', 'done': c.is_set_up(),
         'status': f"{c.owner()}, called {c.address_as()}" if c.is_set_up() else 'Tell Jarvis your name to begin.',
         'values': {'owner.name': c.owner(), 'owner.address_as': str(c.get('owner.address_as') or ''), 'work.about': str(c.get('work.about') or ''),
                    'work.days': c.get('work.days'), 'work.hours': c.get('work.hours'), 'work.quiet': c.get('work.quiet')}},
        {'id': 'claude', 'title': 'Claude', 'done': bool(cl.get('found') and (cl.get('signed_in') or cl.get('unknown')) and test.get('ok')),
         'status': cl.get('fix') or ((f"Answered in {test.get('secs')} s." if test.get('ok') else (test.get('error') or 'Signed in. Press Check to see it answer.'))),
         'claude': cl, 'mode': c.get('engine.mode')},
        {'id': 'sources', 'title': 'Sources', 'done': bool((ch['on'] and ch['found']) or any_app),
         'status': f"{ch['found']} Claude Code chats in {ch['projects']} projects on this computer; " + (
             (', '.join(r['label'] for r in ap['rows'] if r['state'] == 'connected') + ' connected.') if any_app else
             ('apps connected to a Claude account are out of reach with an API key.' if ap.get('api_key') else
              'no app checked yet.' if ap['checked'] is None else 'no app connected.')),
         'chats': ch, 'apps': ap, 'reads': reads()},
        {'id': 'folders', 'title': 'Folders', 'done': any(r['ok'] for r in fo['rows']),
         'status': (f"{sum(1 for r in fo['rows'] if r['ok'])} work folder(s); {ch['in_scope']} chats were held in them." if fo['rows'] else
                    'Name the folders that hold your work: Jarvis reads the chats held there and writes its work beside them.'),
         'folders': fo},
        {'id': 'budget', 'title': 'Budget', 'done': True,
         'status': (f"Background work may use up to {round(100 * float(c.get('engine.background_share') or 0))}% of your Claude plan's window." if c.get('engine.mode') == 'claude-plan'
                    else f"Background work stops at ${float(c.get('engine.daily_cap_usd') or 0):.2f} a day."),
         'values': {'engine.mode': c.get('engine.mode'), 'engine.background_share': c.get('engine.background_share'), 'engine.daily_cap_usd': c.get('engine.daily_cap_usd')}},
        {'id': 'learn', 'title': 'Learn', 'done': lr['built'],
         'status': (f"Learning now: {lr['step']} ({lr['chat_notes']} chats read so far)." if lr['running'] else
                    (f"The knowledge base holds {lr['kb_notes']} notes, from {lr['chat_notes']} chats." if lr['built'] else
                     (lr['error'] or 'Nothing learned yet.'))),
         'learning': lr},
        {'id': 'optional', 'title': 'Optional parts', 'done': True, 'status': ', '.join(o['label'] for o in optional() if o['on']) or 'None switched on.',
         'parts': optional()},
        {'id': 'trust', 'title': 'Trust', 'done': True, 'status': trust()['line'], 'trust': trust()},
    ]


def state(q=None):
    c = config.load()
    s = steps()
    need = [x['id'] for x in s if not x['done']]
    return {'set_up': c.is_set_up(), 'steps': s, 'next': need[0] if need else '', 'left': len(need), 'suggestions': _cached('suggestions', 30, suggestions),
            'home': _short(paths.home()), 'version': _version()}


def _version():
    try:
        return open(os.path.join(str(paths.REPO), 'VERSION'), encoding='utf-8').read().strip()[:20]
    except OSError:
        return ''


def save(b):
    """Change settings from the Setup screen. Every value is checked before anything is written; one bad value and none
    is saved."""
    vals = (b or {}).get('set')
    if not isinstance(vals, dict) or not vals:
        return {'ok': False, 'error': 'nothing to save'}, 400
    for k in vals:
        if not isinstance(k, str) or not k.startswith(EDITABLE):
            return {'ok': False, 'error': f'{k} is not a setting that Setup changes'}, 400
    was = config.load()
    first_name = not was.is_set_up()
    try:
        for k, v in vals.items():
            config._check(k, v)                      # all of them first
        for k, v in vals.items():
            config.set(k, v)
    except config.ConfigError as e:
        return {'ok': False, 'error': str(e)}, 400
    forget()
    if first_name and config.load().is_set_up():     # the workers' skills carry the owner's name: write them now
        try:
            from . import skillset
            skillset.install()
        except Exception:  # noqa: BLE001  the server writes them at its next start as well
            pass
    return {'ok': True, 'state': state()}, 200


def key(b):
    """Keep (or remove) the owner's API key in the system's password store. The key is never returned, logged or stored
    in a file of Jarvis's."""
    from . import keystore
    b = b or {}
    if b.get('remove'):
        keystore.remove()
        forget()
        return {'ok': True, 'state': state()}, 200
    try:
        keystore.put(b.get('key'))
    except keystore.KeyError_ as e:
        return {'ok': False, 'error': str(e)}, 400
    forget()
    return {'ok': True, 'where': keystore.where(), 'state': state()}, 200


def find_site(b=None):
    """Find the owner's Atlassian site through the app connected to their Claude account (one small run, read-only), and
    keep its id in settings (tracker.cloud_id): the ticket and page readers need it."""
    exe = paths.claude_bin()
    if not exe:
        return {'ok': False, 'error': claude().get('fix', 'Claude Code was not found.')}, 400
    if config.load().get('engine.mode') == 'api-key':
        return {'ok': False, 'error': 'With an API key, the apps connected to a Claude account are out of reach.'}, 400
    paths.ensure()
    tool = 'mcp__claude_ai_Atlassian_Rovo__getAccessibleAtlassianResources'
    schema = {'type': 'object', 'properties': {'sites': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'name': {'type': 'string'}, 'url': {'type': 'string'}}, 'required': ['id']}}}, 'required': ['sites']}
    prompt = (f'Call the tool {tool} once, with no arguments. Return every site it lists, copying each id, name and url exactly '
              'as the tool gave them. If the tool is not available or fails, return an empty list.')
    try:
        p = subprocess.run([exe, '-p', prompt, '--model', config.model('quick'), '--output-format', 'json', '--json-schema', json.dumps(schema),
                            '--allowedTools', tool, '--max-turns', '4'], capture_output=True, text=True, timeout=240, cwd=str(paths.home()),
                           env=dict(os.environ, **MCP_ENV), stdin=subprocess.DEVNULL)
        d = json.loads(p.stdout or '{}')
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {'ok': False, 'error': f'The search did not finish ({e.__class__.__name__}).'}, 500
    sites = [s for s in ((d.get('structured_output') or {}).get('sites') or []) if isinstance(s, dict)
             and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', str(s.get('id', '')))]
    if not sites:
        return {'ok': False, 'error': 'No Atlassian site was found. Connect Atlassian in Claude, then try again.'}, 404
    pick = str((b or {}).get('id') or '')
    chosen = next((s for s in sites if s['id'] == pick), sites[0] if len(sites) == 1 else None)
    if chosen:
        config.set('tracker.cloud_id', chosen['id'])
        if chosen.get('url') and not config.load().get('links.ticket_base'):
            config.set('links.ticket_base', str(chosen['url']).rstrip('/') + '/browse/')
        forget()
    return {'ok': True, 'sites': [{'id': s['id'], 'name': str(s.get('name') or '')[:80], 'url': str(s.get('url') or '')[:120]} for s in sites],
            'chosen': chosen['id'] if chosen else '', 'state': state()}, 200


def check(b):
    what = str((b or {}).get('what') or 'claude')
    if what != 'claude':
        return {'ok': False, 'error': 'unknown check'}, 400
    forget()
    return {'ok': True, 'result': test_claude(), 'state': state()}, 200
