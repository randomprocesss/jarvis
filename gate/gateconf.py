"""What the deploy gate is told in settings: which repositories it watches, which branches and tags mean "this is going
out", how each repository is built and tested, and its limits. Nothing about any owner is written in gate/ itself.

Settings (config.toml in the data home; `jarvis/config.py` lists every key):
  [sources] code_url     where the repositories live; with only code_workspace set, the same address the repositories
                         server uses
  [gate]    repos        their names (default: sources.pr_repos)
            deploy_branches / release_tags / ignore_branches / build / test
                         one line for every repository ("main"), or one for a single repository, written
                         "name = value" with a space on each side of the equals sign ("api = develop staging")
With no address or no repository named, ready() says so and the gate does nothing.

Everything the gate writes lives in the data home: deploy_gate/ (its record, verdicts, logs) and repos/ (the same light
mirrors the repositories server keeps).
"""
import fnmatch
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)                         # the repository: a script run by itself finds the package
from jarvis import config, paths  # noqa: E402

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}')
ONE = re.compile(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s+=\s+(\S.*)')          # "name = value"
SCRATCH = re.compile(r'(^|[-_/.])(backup|bak|prerebase|pre-rebase|tmp|temp|scratch)([-_/.]|$)', re.I)   # a copy of a branch


def root():
    """Where the gate keeps its record, verdicts and logs."""
    return paths.at('deploy_gate')


def mirrors():
    return paths.at('repos')


def mirror(repo):
    return mirrors() / (str(repo) + '.git')


def tools():
    return paths.repo('gate', 'tools')


def base():
    """The address the repositories live under, ending in "/" ('' = not set). The repositories server's own rule, so the
    two parts can never disagree about where a repository is."""
    paths.use_tools()
    import repos_mcp
    return repos_mcp.base()


def git_env(extra=None):
    """git never asks for a password here: it signs in with whatever sign-in it already has, or fails."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/usr/bin/false', SSH_ASKPASS='/usr/bin/false')
    env.update(extra or {})
    return env


NO_PUSH = 'no-push://deploy-gate/'       # where every push from a review is sent: nowhere


def no_push(env, base=''):
    """`env` with git unable to push anywhere, whatever is typed: the mirror's remote has no push address, any push to
    the code host is sent nowhere, and no hook of the repository runs. For everything that runs code of a change."""
    env = dict(env)
    n = int(env['GIT_CONFIG_COUNT']) if str(env.get('GIT_CONFIG_COUNT') or '').isdigit() else 0
    pairs = [('remote.origin.pushurl', NO_PUSH), ('remote.pushDefault', 'origin'), ('core.hooksPath', os.devnull)]
    if base:
        pairs.append((f'url.{NO_PUSH}.pushInsteadOf', base))
    for i, (k, v) in enumerate(pairs, n):
        env[f'GIT_CONFIG_KEY_{i}'], env[f'GIT_CONFIG_VALUE_{i}'] = k, v
    env['GIT_CONFIG_COUNT'] = str(n + len(pairs))
    return env


def _lines(c, key):
    return [str(x).strip() for x in c.get('gate.' + key) or [] if str(x).strip()]


def _split(lines, repos, key, problems):
    """({repository: [its values]}, [the values for every repository]) from a list of settings lines."""
    own, every = {}, []
    for line in lines:
        m = ONE.fullmatch(line)
        if m and m.group(1) in repos:
            own.setdefault(m.group(1), []).append(m.group(2).strip())
        elif m:
            problems.append(f'gate.{key}: "{m.group(1)} = ..." names a repository the gate does not watch; the line is ignored')
        else:
            every.append(line)
    return own, every


class Conf:
    """The gate's settings, read once. `problems` lists the lines that could not be used (they are ignored, never guessed)."""

    def __init__(self, c=None):
        c = c or config.load()
        self.problems = []
        self.base = base()
        named = _lines(c, 'repos') or [str(r).strip() for r in c.get('sources.pr_repos') or [] if str(r).strip()]
        self.repos = []
        for r in named:
            if not NAME.fullmatch(r):
                self.problems.append(f'gate.repos: "{r[:40]}" is not a repository name; it is ignored')
            elif r not in self.repos:
                self.repos.append(r)
        self._lists = {}
        for key in ('deploy_branches', 'release_tags', 'ignore_branches'):
            own, every = _split(_lines(c, key), self.repos, key, self.problems)
            self._lists[key] = ({r: [p for v in vals for p in v.split()] for r, vals in own.items()},
                                [p for v in every for p in v.split()])
        self._cmds = {key: _split(_lines(c, key), self.repos, key, self.problems) for key in ('build', 'test')}
        self.ui_repos = [r for r in _lines(c, 'ui_repos') if r in self.repos]
        self.docker = bool(c.get('gate.docker'))
        self.env_file = ''
        f = os.path.expanduser(str(c.get('gate.env_file') or '').strip())
        if f:
            if not re.fullmatch(r'\.env(\.\w+)?', os.path.basename(f.rstrip(os.sep))):
                self.problems.append('gate.env_file: the file must be named .env or .env.<word>; it is ignored')
            elif not os.path.isfile(f):
                self.problems.append('gate.env_file: that file does not exist; the commands run without it')
            else:
                self.env_file = f

        def num(key, lo, hi, whole=True):
            d = config.DEFAULTS['gate'][key]
            v = c.get('gate.' + key)
            v = d if isinstance(v, bool) or not isinstance(v, (int, float)) else v
            v = min(max(v, lo), hi)
            return int(v) if whole else float(v)
        self.days = num('days', 1, 1000)
        self.max_reviews = num('max_reviews', 1, 1000)
        self.parallel = num('parallel', 1, 16)
        self.review_s = num('review_minutes', 1, 1000) * 60
        self.max_attempts = num('max_attempts', 1, 1000)
        self.usage_max = num('usage_max', 0, 1, whole=False)
        self.min_free_gb = num('min_free_gb', 0, 100000, whole=False)
        self.plan = str(c.get('engine.mode') or 'claude-plan') == 'claude-plan'
        self.tracker = c.has('atlassian')
        self.ticket_keys = _ticket_keys()

    def _list(self, key, repo):
        own, every = self._lists[key]
        return list(own.get(repo) or every)

    def deploy_patterns(self, repo):
        """The patterns of this repository's deploy branches ([] = its default branch)."""
        return self._list('deploy_branches', repo)

    def deploy_branches(self, repo, names, default=''):
        """Which of these branch names are deploy branches, in the order the settings give them."""
        pats = self.deploy_patterns(repo) or ([default] if default else [])
        out = []
        for p in pats:
            for n in sorted(names):
                if fnmatch.fnmatchcase(n, p) and n not in out:
                    out.append(n)
        return out

    def tag_patterns(self, repo):
        return self._list('release_tags', repo)

    def tag_series(self, repo, tag):
        """The pattern a tag is a release of (every tag of one pattern is one series), or None when it is not a release."""
        return next((p for p in self.tag_patterns(repo) if fnmatch.fnmatchcase(tag, p)), None)

    def ignored(self, repo, branch):
        """Is this branch never reviewed as a change on its way? The owner's patterns, and a backup or scratch copy."""
        return any(fnmatch.fnmatchcase(branch, p) for p in self._list('ignore_branches', repo)) or bool(SCRATCH.search(branch))

    def command(self, kind, repo):
        """The build or test command for this repository ('' = none named)."""
        own, every = self._cmds[kind]
        return (own.get(repo) or every or [''])[0]

    def ui(self, repo):
        return repo in self.ui_repos

    def ready(self):
        """'' when there is something to watch, else what is missing, in one sentence."""
        if not self.base:
            return 'not set up: no code host in settings ([sources] code_url)'
        if not self.repos:
            return 'not set up: no repository to watch in settings ([gate] repos, or [sources] pr_repos)'
        return ''

    def as_dict(self):
        """What the screen and `status` show of the settings (no command text, never the environment file)."""
        return {'ready': not self.ready(), 'why': self.ready(), 'repos': list(self.repos),
                'deploy_branches': {r: self.deploy_patterns(r) for r in self.repos},
                'release_tags': {r: self.tag_patterns(r) for r in self.repos},
                'build': [r for r in self.repos if self.command('build', r)], 'test': [r for r in self.repos if self.command('test', r)],
                'ui_repos': list(self.ui_repos), 'docker': self.docker, 'env_file': bool(self.env_file),
                'limits': {'days': self.days, 'max_reviews': self.max_reviews, 'parallel': self.parallel,
                           'review_minutes': self.review_s // 60, 'max_attempts': self.max_attempts,
                           'usage_max': self.usage_max, 'min_free_gb': self.min_free_gb},
                'problems': list(self.problems)}


def _ticket_keys():
    from jarvis import entities
    return entities.ticket_keys()


def tickets(text):
    """The owner's ticket keys in a text (knowledge.ticket_keys; with none listed, any KEY-123 that is not a look-alike)."""
    from jarvis import entities
    return entities.tickets(str(text or ''))


def load():
    return Conf()
