"""Who Jarvis works for, and how. The only module that knows the owner.

Settings live in config.toml in the data home (paths.config_file()). Every setting has a default, so a missing file is a
valid start: Jarvis then knows it is not set up yet and the app opens on Setup. The Setup screen changes settings through
set(); nobody needs to edit the file by hand. A setting the code does not know is kept untouched, so a newer version's
settings survive an older one.

    cfg = config.load()
    cfg.get('owner.name')            'Sam'
    cfg.is_set_up()                  True once the owner has a name
    config.set('work.quiet', '22:30-07:00')
"""
import copy, json, os, re, tempfile
try:
    import tomllib
except ModuleNotFoundError:                          # Python older than 3.11: the small reader below is used instead
    tomllib = None
from . import paths

DEFAULTS = {
    'owner': {'name': '', 'address_as': '',                        # address_as: what Jarvis calls them ("Sam", "boss")
              'emails': []},                                        # their own addresses: how their sent mail and commits are told apart
    'org': {'names': [], 'domains': [],                           # their organisation's names and mail domains, masked on shared screens
            'never_name': []},                                     # first names never shown on the map (someone who has left)
    'work': {'projects': [], 'folders': [], 'days': 'mon-fri', 'hours': '09:00-19:00', 'quiet': '22:30-07:00',   # when they usually work
             'about': '',                                          # one line on what they do, in their words (for prompts)
             'search_words': [],                                   # words that mark their work when Jarvis searches the apps
             'branch_prefix': '',                                  # the start of the branches Jarvis makes ("sl/"); default: their initials
             'skip_words': [],                                     # a file whose path holds one of these words stays out of the
                                                                   # knowledge base (e.g. an admin tool's exports)
             'never_read': [],                                     # more places a worker must never read (keys, private folders)
             'analyst_words': []},                                 # words that mark a request as number work (their systems, tables)
    'engine': {'mode': 'claude-plan',                              # claude-plan | api-key
               'work_model': 'claude-opus-5-5', 'mid_model': 'claude-sonnet-5-5', 'quick_model': 'claude-haiku-5-5',
               'background_share': 0.25,                           # of a plan's usage window, for work nobody asked for
               'learn_window_max': 0.70,                           # learning waits while the 5-hour window is at or above this
               'learn_week_max': 0.50,                             # and stops for the week at this share of the weekly limit
               'learn_night_extra': 0.0,                           # the nightly update may go this far past that line (0: never)
               'daily_cap_usd': 5.0},                              # with an API key: background work stops here
    'sources': {'claude_connectors': True, 'chats': True, 'mcp_config': '', 'folders': [],
                'read_tools': [],                                  # read-only tools of their own MCP servers the assistant may call
                'slack_user_id': '', 'slack_workspace': '',        # theirs, found by Setup: "U…" and "https://<team>.slack.com"
                'expected': [],                                    # connectors their work needs: Jarvis says when one drops
                'shared_repos': [],                                # repositories shared with many teams: only their own commits
                                                                   # and the ones that name their work are read
                # what the knowledge base reads from the connected apps (tools/kb_live.py); nothing set, nothing read
                'since': '',                                       # the first day read, YYYY-MM-DD (default: twelve months back)
                'tracker_projects': [],                            # ticket projects read in full (default: knowledge.ticket_keys)
                'wiki_spaces': [],                                 # wiki spaces read in full
                'channels': [],                                    # chat channels: "ID name [since]" per line
                'mail_terms': [],                                  # words that mark work mail, Teams messages and meetings
                                                                   # (default: work.search_words)
                'client_domains': [],                              # a meeting with someone from these mail domains is work
                'shared_folder': '',                               # the shared-files folder to catalogue (its file:/// address)
                'shared_read': [],                                 # its top folders whose documents are read in full
                'code_workspace': '',                                              # the workspace on the code host, for pull requests
                'code_url': '',                                                    # where their repositories live, e.g. https://github.com/name/ (read-only mirror for the assistant)
                'pr_repos': []},                                   # repositories whose pull requests are read (default: the
                                                                   # git repositories in the work folders)
    'tracker': {'own_projects': [],                               # ticket projects that are theirs (e.g. "ABC"): the only ones a
                                                                   # comment may ever reach without their press
                'cloud_id': '',                                    # their Atlassian site's id, found by Setup (the connector needs it)
                'site': '', 'email': '', 'use_token': False},      # only for direct Jira calls with their own API token; off =
                                                                   # Jarvis does Jira through the Atlassian connector
    'knowledge': {'name': 'main',                                  # the knowledge base's folder inside brain/
                  'keep_secrets': False,                           # keep whole copies of files that held a secret, in a locked
                                                                   # folder? Off: the knowledge base's copies are redacted and no
                                                                   # second copy of a secret is made
                  'acronyms': [],                                  # words kept in capitals on the map ("API", their project codes)
                  'ticket_keys': [],                               # the ticket projects that are theirs ("ABC" for ABC-123); none: any
                  'entities': [],                                  # their own kinds of thing to keep a page for: "kind = pattern"
                  'pipelines': []},                                # code whose settings are read: "name = folder"; none: every
                                                                   # git repository in the work folders
    'vault': {'path': '',                                          # the folder the work is mirrored into (an Obsidian vault, or a
                                                                   # folder inside one), when modules.vault is on
              'kb_folder': 'Knowledge'},                           # the knowledge base's folder inside it
    'research': {'topics': []},                                    # standing topics for the weekly look outside (general terms)
    'sharing': {'ai_wording': 'never',                             # may text that goes to other people name Claude, Jarvis or AI?
                'folder': '',                                      # a synced folder (a cloud drive) spreadsheets are shared through
                'folder_link': ''},                                # the link to that folder, for the line that names a shared file
                                                                   # never | allowed (takes effect at the next start)
    'notify': {'enabled': True},                                   # pings to the owner's phone or desktop
    'health': {'watch_logins': []},                                # command-line logins to watch, e.g. "gcloud"
    # the optional parts (jarvis/modules), each switched on here
    'modules': {'deploy_gate': False, 'vault': False, 'prwatch': False,
                'self_improve': False},                             # Jarvis changing its own code (tested, watched, undone if worse)
    # the deploy gate (gate/, on when modules.deploy_gate is): which changes it reviews before they go further. A line may be
    # for every repository ("main") or for one ("api = develop staging"). Nothing named, nothing reviewed.
    'gate': {'repos': [],                                          # repositories it watches, by name under sources.code_url
                                                                   # (default: sources.pr_repos)
             'deploy_branches': [],                                # branches whose push deploys (default: each repository's
                                                                   # default branch)
             'release_tags': [],                                   # tag patterns that are releases ("v*"); none: no tag is reviewed
             'ignore_branches': [],                                # branch patterns never reviewed (a bot's branch, "wip/*")
             'build': [],                                          # the build command: "npm ci && npm run build", or "api = make build"
             'test': [],                                           # the test command, written the same way
             'ui_repos': [],                                       # repositories with screens: a verdict there counts only when
                                                                   # the change was run and used locally
             'env_file': '',                                       # a file of NAME=value lines for the build and test commands
                                                                   # only (a private registry's login); it must be named .env
                                                                   # or .env.<word>, which no worker may read
             'docker': False,                                      # may a review start throwaway local containers?
             'days': 7,                                            # a feature branch counts while its last commit is this new
             'max_reviews': 3,                                     # reviews started in one run
             'parallel': 1,                                        # reviews running at the same time
             'review_minutes': 50,                                 # one review is stopped after this long
             'max_attempts': 3,                                    # failed tries before a change is "stuck" and the owner is told
             'usage_max': 0.85,                                    # reviews wait while Claude's 5-hour window is at or above this
             'min_free_gb': 10},                                   # and while the disk has less free space than this
    'links': {'ticket_base': ''},                                  # e.g. their tracker's "browse" address, for ticket links
    'server': {'port': 8765},
}
MODES = ('claude-plan', 'api-key')
_WEEK = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
_SPAN = re.compile(r'^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$')
_DAYS = re.compile(r'^(mon|tue|wed|thu|fri|sat|sun)(-(mon|tue|wed|thu|fri|sat|sun))?$')


class ConfigError(ValueError):
    """A setting that cannot be used. The message says which one and what it should look like."""


def _check(key, value):
    """Raise ConfigError unless `value` is usable for `key`. Unknown keys pass: they belong to another version."""
    table, _, name = key.partition('.')
    if table not in DEFAULTS or name not in DEFAULTS[table]:
        return
    want = DEFAULTS[table][name]
    if isinstance(want, bool):
        if not isinstance(value, bool):
            raise ConfigError(f'{key} must be true or false')
    elif isinstance(want, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f'{key} must be a number')
    elif isinstance(want, list):
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ConfigError(f'{key} must be a list of text items')
    elif not isinstance(value, str):
        raise ConfigError(f'{key} must be text')
    if key == 'engine.mode' and value not in MODES:
        raise ConfigError(f'engine.mode must be one of: {", ".join(MODES)}')
    if key == 'sharing.ai_wording' and value not in ('never', 'allowed'):
        raise ConfigError('sharing.ai_wording must be never or allowed')
    if key in ('work.hours', 'work.quiet') and not _SPAN.match(value):
        raise ConfigError(f'{key} must look like 09:00-19:00')
    if key == 'work.days' and not _DAYS.match(value):
        raise ConfigError('work.days must look like mon-fri')
    if key == 'server.port' and not (isinstance(value, int) and 1024 <= value <= 65535):
        raise ConfigError('server.port must be a whole number from 1024 to 65535')
    if key in ('engine.background_share', 'engine.learn_window_max', 'engine.learn_week_max', 'engine.learn_night_extra') and not 0 <= value <= 1:
        raise ConfigError(f'{key} must be between 0 and 1')
    if key == 'engine.daily_cap_usd' and value < 0:
        raise ConfigError('engine.daily_cap_usd cannot be negative')
    if key == 'sources.since' and value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ConfigError('sources.since must be a date like 2026-01-31, or empty')
    if key in ('owner.name', 'owner.address_as', 'work.about') and (len(value) > (200 if key == 'work.about' else 80) or '\n' in value):
        raise ConfigError(f'{key} must be one short line')
    if key in ('gate.days', 'gate.max_reviews', 'gate.parallel', 'gate.review_minutes', 'gate.max_attempts') and not (isinstance(value, int) and 1 <= value <= 1000):
        raise ConfigError(f'{key} must be a whole number from 1 to 1000')
    if key == 'gate.usage_max' and not 0 <= value <= 1:
        raise ConfigError('gate.usage_max must be between 0 and 1')
    if key == 'gate.min_free_gb' and value < 0:
        raise ConfigError('gate.min_free_gb cannot be negative')
    if key == 'gate.env_file' and value and not re.fullmatch(r'\.env(\.\w+)?', os.path.basename(value.rstrip('/\\'))):
        raise ConfigError('gate.env_file must be a file named .env or .env.<word> (the name no worker may read)')


class Config:
    def __init__(self, data):
        self._d = data

    def get(self, key, default=None):
        table, _, name = key.partition('.')
        t = self._d.get(table)
        if not name:
            return copy.deepcopy(t) if t is not None else default
        return copy.deepcopy(t[name]) if isinstance(t, dict) and name in t else default

    def as_dict(self):
        return copy.deepcopy(self._d)

    def is_set_up(self):
        """Has the owner said who they are? Until then the app opens on Setup."""
        return bool(str(self.get('owner.name') or '').strip())

    def owner(self):
        return str(self.get('owner.name') or '').strip()

    def address_as(self):
        """What Jarvis calls the owner: their choice, else their first name, else nothing."""
        return str(self.get('owner.address_as') or '').strip() or (self.owner().split() or [''])[0]

    def owner_or(self, fallback='the owner'):
        """The owner's name for a prompt or a sentence; `fallback` until they have said it."""
        return self.owner() or fallback

    def work_line(self):
        """One clause on the owner's work for prompts: what they said in Setup, then their project names. '' if unknown."""
        about = ' '.join(str(self.get('work.about') or '').split())[:200]
        projects = [str(p).strip() for p in self.get('work.projects') or [] if str(p).strip()][:8]
        return '; '.join(([about] if about else []) + (['projects: ' + ', '.join(projects)] if projects else []))

    def emails(self):
        return [e for e in (str(x).strip() for x in self.get('owner.emails') or []) if '@' in e]

    def identity_line(self):
        """Who the owner is, for a prompt that searches the connected apps: their name, then only the ids that are known
        (Slack user, e-mail addresses, Atlassian site)."""
        ids = []
        if self.has('slack') and self.get('sources.slack_user_id'):
            ids.append(f"Slack user {self.get('sources.slack_user_id')}")
        if self.emails():
            ids.append('e-mail ' + ' / '.join(self.emails()))
        if self.has('atlassian') and self.get('tracker.cloud_id'):
            ids.append(f"Atlassian cloudId {self.get('tracker.cloud_id')}")
        return self.owner_or() + (f" ({'; '.join(ids)})" if ids else '')

    def who(self):
        """The owner and their work in one phrase for a prompt: "Sam Lee (product design; projects: Atlas)"."""
        return self.owner_or() + self.work_clause()

    def org_text(self):
        """Their organisation's names for a prompt (", who works at X"), or '' when none is listed."""
        names = [n for n in (' '.join(str(x).split()) for x in self.get('org.names') or []) if n]
        return (' at ' + ' / '.join(names[:3])) if names else ''

    def work_clause(self):
        """' (what they do; projects: A, B)' for a prompt, or '' when nothing is known."""
        w = self.work_line()
        return f' ({w})' if w else ''

    def search_words(self):
        """The words that mark the owner's work in a search: their own list, then their project names."""
        out = []
        for w in (self.get('work.search_words') or []) + (self.get('work.projects') or []):
            w = ' '.join(str(w).split())
            if w and w.lower() not in (x.lower() for x in out) and not any(ch in w for ch in '"\\'):
                out.append(w)
        return out[:12]

    def own_projects(self):
        """Their ticket projects (tracker.own_projects), as clean keys."""
        return [p for p in (str(x).strip().upper() for x in self.get('tracker.own_projects') or []) if re.fullmatch(r'[A-Z][A-Z0-9]{1,9}', p)]

    def has(self, app):
        """Is this app connected for the owner's work? app: 'slack' | 'atlassian' | 'microsoft 365' (or any word of a
        connector's name). True when a connector with that word is in sources.expected, which Setup fills from what it finds."""
        if not self.get('sources.claude_connectors'):
            return False
        return any(app.lower() in str(n).lower() for n in self.get('sources.expected') or [])

    def in_work_hours(self, when=None):
        """Is `when` (local time; default now) inside the owner's usual working days and hours? A span may run past
        midnight (22:00-06:00) and the days may wrap the week (sun-thu)."""
        import datetime as dt
        when = when or dt.datetime.now()
        first, _, last = str(self.get('work.days') or 'mon-fri').partition('-')
        i, j = _WEEK.index(first), _WEEK.index(last or first)
        days = {(i + k) % 7 for k in range((j - i) % 7 + 1)}
        start, _, end = str(self.get('work.hours') or '09:00-19:00').partition('-')
        lo, hi = (int(x[:2]) * 60 + int(x[3:5]) for x in (start, end))
        now = when.hour * 60 + when.minute
        inside = lo <= now < hi if lo < hi else (now >= lo or now < hi)
        return when.weekday() in days and inside


def model(tier):
    """The Claude model for a tier of work: 'work' (the hard thinking), 'mid' (reviews, summaries), 'quick' (small checks)."""
    if tier not in ('work', 'mid', 'quick'):
        raise ConfigError(f'no such model tier: {tier}')
    return load().get(f'engine.{tier}_model') or DEFAULTS['engine'][f'{tier}_model']


def _value(text, where):
    """One value as the settings file writes it: "text", 'text', a number, true / false, or a list of those."""
    text = text.strip()
    if text.startswith('['):
        if not text.endswith(']'):
            raise ValueError(f'{where}: the list is not closed')
        items, cur, quote, esc = [], '', '', False
        for ch in text[1:-1]:
            if quote:
                cur += ch
                if esc:
                    esc = False
                elif ch == '\\' and quote == '"':
                    esc = True
                elif ch == quote:
                    quote = ''
            elif ch in '"\'':
                quote = ch; cur += ch
            elif ch == ',':
                items.append(cur); cur = ''
            else:
                cur += ch
        if quote:
            raise ValueError(f'{where}: a quote is not closed')
        return [_value(x, where) for x in items + [cur] if x.strip()]
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            v = json.loads(text)
        except ValueError:
            raise ValueError(f'{where}: the text in quotes cannot be read') from None
        if isinstance(v, str):
            return v
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    if text in ('true', 'false'):
        return text == 'true'
    if re.fullmatch(r'[+-]?\d+', text):
        return int(text)
    if re.fullmatch(r'[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?|[+-]?\d+[eE][+-]?\d+', text):
        return float(text)
    raise ValueError(f'{where}: {text[:40]!r} is not a value this file can hold')


def _strip_comment(line):
    """The line without a trailing # comment (a # inside quotes is text)."""
    quote, esc = '', False
    for i, ch in enumerate(line):
        if quote:
            if esc:
                esc = False
            elif ch == '\\' and quote == '"':
                esc = True
            elif ch == quote:
                quote = ''
        elif ch in '"\'':
            quote = ch
        elif ch == '#':
            return line[:i]
    return line


def _parse(text):
    """The settings file's own small subset of TOML (tables of text, numbers, true / false and lists), read without any
    package: Python before 3.11 has no TOML reader, and Jarvis must run on the Python a computer already has."""
    data, table, pending, start = {}, None, '', 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).strip()
        if pending:                                  # a list written over several lines
            pending += ' ' + line
            if line.endswith(']'):
                key, _, val = pending.partition('=')
                (data if table is None else data[table])[key.strip()] = _value(val, f'line {start}')
                pending = ''
            continue
        if not line:
            continue
        m = re.fullmatch(r'\[([A-Za-z0-9_-]+)\]', line)
        if m:
            table = m.group(1); data.setdefault(table, {})
            continue
        key, eq, val = line.partition('=')
        if not eq or not re.fullmatch(r'[A-Za-z0-9_-]+', key.strip()):
            raise ValueError(f'line {n}: expected "name = value" or "[section]"')
        if val.strip().startswith('[') and not val.strip().endswith(']'):
            pending, start = line, n
            continue
        (data if table is None else data[table])[key.strip()] = _value(val, f'line {n}')
    if pending:
        raise ValueError(f'line {start}: the list is not closed')
    return data


def _read(path):
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding='utf-8')
        return tomllib.loads(text) if tomllib is not None else _parse(text)
    except (ValueError, UnicodeDecodeError) as e:          # tomllib's own error is a ValueError too
        raise ConfigError(f'{path} cannot be read ({e}). Fix that line, or move the file away to start again.') from None


def load():
    """The settings: the file's values over the defaults. Raises ConfigError for a value that cannot be used."""
    data = copy.deepcopy(DEFAULTS)
    for table, vals in _read(paths.config_file()).items():
        if isinstance(vals, dict):
            data.setdefault(table, {})
            for name, v in vals.items():
                _check(f'{table}.{name}', v)
                data[table][name] = v
        else:
            data[table] = vals                                    # a top-level value from another version: kept
    return Config(data)


def _toml_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return '[' + ', '.join(_toml_value(x) for x in v) + ']'
    return json.dumps(str(v), ensure_ascii=False)                 # a TOML basic string takes the same escapes


def _dump(data):
    out = ['# Jarvis settings. The Setup screen in the app writes this file; you rarely need to edit it by hand.']
    top = {k: v for k, v in data.items() if not isinstance(v, dict)}
    out += [f'{k} = {_toml_value(v)}' for k, v in top.items()]
    for table, vals in data.items():
        if isinstance(vals, dict):
            out += ['', f'[{table}]'] + [f'{k} = {_toml_value(v)}' for k, v in vals.items() if not isinstance(v, dict)]
    return '\n'.join(out) + '\n'


def save(cfg):
    """Write the settings, whole or not at all (a temporary file, then one move), readable by the owner only."""
    paths.ensure()
    target = paths.config_file()
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix='.config-', suffix='.toml')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(_dump(cfg.as_dict()))
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def set(key, value):
    """Change one setting (the Setup screen's door). Checked first; nothing is written if the value cannot be used."""
    if '.' not in key:
        raise ConfigError('a setting is named like owner.name')
    _check(key, value)
    cfg = load()
    table, _, name = key.partition('.')
    cfg._d.setdefault(table, {})[name] = value
    save(cfg)
    return cfg
