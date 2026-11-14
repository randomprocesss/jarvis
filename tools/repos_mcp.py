#!/usr/bin/env python3
"""Read-only access to the owner's repositories on their code host, for the assistant.

A tiny tool server (stdio, JSON-RPC, standard library only) that the assistant's Claude runs start. It keeps a light mirror
of each repository in the data home, repos/<name>.git (git clone --mirror --filter=blob:none: every branch and commit, file
contents fetched only when asked) and answers from it. git signs in by itself, with whatever sign-in it already has on
this computer; this script never reads, asks for or prints a credential, and builds every address itself (no user or
password part).

Read only: clone, fetch, log, show, ls-tree, grep, diff. Nothing here can push, delete or change a branch. Open pull
requests are not visible here (tools/prwatch.py reads those); merged ones show in the history.

Which repositories: settings, [sources].
  code_url    the address they live under, e.g. "https://github.com/yourname/" (any host git can reach)
              (with only code_workspace set, https://bitbucket.org/<code_workspace>/ is used)
  pr_repos    their names (default: the git repositories found in the work folders)
With no address set, the server lists nothing and says so.

    python3 tools/repos_mcp.py --selftest     # quick check outside Claude
"""
import json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config, entities, paths  # noqa: E402

ROOT = str(paths.home() / 'repos')


def base():
    """The address the repositories live under, ending in "/" ('' = not set up)."""
    c = config.load()
    u = str(c.get('sources.code_url') or '').strip()
    if not u and str(c.get('sources.code_workspace') or '').strip():
        u = 'https://bitbucket.org/' + str(c.get('sources.code_workspace')).strip() + '/'
    u = re.sub(r'^(https?://)[^@/\s]*@', r'\1', u)              # never a user or password part
    return (u.rstrip('/') + '/') if re.match(r'^(https://|ssh://|git@)', u) else ''


def repos():
    if not base():
        return []
    return [str(r).strip() for r in config.load().get('sources.pr_repos') or [] if str(r).strip()] or entities.repos()


FETCH_EVERY = 600                      # seconds; older mirrors are refreshed before answering
MAX_OUT = 40_000
REF = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/\-]{0,200}$')
# Same spirit as Jarvis's Read deny list: secrets are never shown, whatever branch they are on.
SECRET_PATH = re.compile(r'(^|/)(\.env(\..*)?|.*credentials.*|.*secret.*|\.netrc|\.pgpass|pgpass.*|.*\.pem|.*\.key|id_rsa.*|\.git-credentials)$', re.I)
SECRET_LINE = re.compile(r'(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*\S+', re.I)
ENV = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/usr/bin/false', SSH_ASKPASS='/usr/bin/false')


def git(repo_dir, *args, timeout=60):
    p = subprocess.run(['git', '-C', repo_dir, *args], capture_output=True, text=True, timeout=timeout, env=ENV,
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        err = (p.stderr or '').strip().splitlines()
        raise RuntimeError(re.sub(r'https://[^@\s/]*@', 'https://', err[-1] if err else f'git {args[0]} failed'))
    return p.stdout


def scrub(text):
    return SECRET_LINE.sub(lambda m: m.group(1) + '=<hidden>', text)


def cap(text):
    return text if len(text) <= MAX_OUT else text[:MAX_OUT] + f'\n… (cut at {MAX_OUT} characters)'


def mirror(name):
    known = repos()
    if name not in known:
        raise ValueError(f'unknown repo {name!r}; known: {", ".join(known) or "none (settings: sources.code_url)"}')
    d = os.path.join(ROOT, name + '.git')
    stamp = os.path.join(d, 'FETCH_STAMP')
    if not os.path.isdir(d):
        os.makedirs(ROOT, exist_ok=True)
        p = subprocess.run(['git', 'clone', '--quiet', '--mirror', '--filter=blob:none', base() + name + '.git', d],
                           capture_output=True, text=True, timeout=180, env=ENV, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise RuntimeError(f'could not reach {name} on the code host (no access, or offline)')
        open(stamp, 'w').write(str(time.time()))
    elif time.time() - (float(open(stamp).read() or 0) if os.path.exists(stamp) else 0) > FETCH_EVERY:
        try:
            git(d, 'fetch', '--quiet', '--prune', 'origin', timeout=90)
            open(stamp, 'w').write(str(time.time()))
        except Exception:
            pass                       # offline: answer from the last copy and say how old it is
    return d


def fetched_at(d):
    try:
        return time.strftime('%d %b %H:%M', time.localtime(float(open(os.path.join(d, 'FETCH_STAMP')).read())))
    except Exception:
        return 'unknown'


def ref_ok(r):
    if not r or not REF.match(r) or '..' in r:
        raise ValueError(f'bad branch or commit name {r!r}')
    return r


def path_ok(p):
    p = (p or '').strip().lstrip('/')
    if '..' in p.split('/') or p.startswith('-'):
        raise ValueError(f'bad path {p!r}')
    return p


def default_branch(d):
    return git(d, 'symbolic-ref', '--short', 'HEAD').strip()


# ---------------------------------------------------------------- tools
def t_repos_list(a):
    rows = []
    if not base():
        return 'No code host is set. Settings: [sources] code_url (and pr_repos for the names).'
    for n in repos():
        d = os.path.join(ROOT, n + '.git')
        rows.append(f'{n}: ' + (f'mirrored, default branch {default_branch(d)}, fetched {fetched_at(d)}' if os.path.isdir(d)
                                else 'not mirrored yet (first question about it takes ~15 s)'))
    return base() + '\n' + '\n'.join(rows)


def t_repo_branches(a):
    d = mirror(a['repo'])
    base = default_branch(d)
    flt = (a.get('filter') or '').lower()
    n = min(int(a.get('limit') or 30), 200)
    out = git(d, 'for-each-ref', '--sort=-committerdate', f'--format=%(refname:short)|%(committerdate:short)|%(authorname)|%(ahead-behind:{base})|%(subject)', 'refs/heads')
    rows = [l for l in out.splitlines() if flt in l.lower()][:n]
    body = '\n'.join(f'- {b} | last commit {dt} by {au} | {ab.split()[0]} ahead / {ab.split()[1]} behind {base} | {s[:100]}'
                     for b, dt, au, ab, s in (r.split('|', 4) for r in rows))
    return f'{a["repo"]} (fetched {fetched_at(d)}; default branch {base}), newest first:\n{body or "(no matching branches)"}'


def t_repo_log(a):
    d = mirror(a['repo'])
    ref = ref_ok(a.get('ref') or default_branch(d))
    n = min(int(a.get('n') or 15), 100)
    args = ['log', f'-{n}', '--date=format:%d %b %Y %H:%M', '--format=%h | %ad | %an | %s', ref]
    if a.get('path'):
        args += ['--', path_ok(a['path'])]
    if a.get('merges_only'):
        args.insert(1, '--merges')
    return f'{a["repo"]} {ref} (fetched {fetched_at(d)}):\n' + git(d, *args)


def t_repo_commit(a):
    d = mirror(a['repo'])
    return cap(scrub(git(d, 'show', '--stat', '--format=%H%nAuthor: %an <%ae>%nDate: %ad%n%n%B', ref_ok(a['sha']))))


def t_repo_files(a):
    d = mirror(a['repo'])
    ref = ref_ok(a.get('ref') or default_branch(d))
    p = path_ok(a.get('path'))
    out = git(d, 'ls-tree', '--name-only', ref, *([p.rstrip('/') + '/'] if p else []))
    lines = out.splitlines()
    return f'{a["repo"]}:{ref}/{p}\n' + '\n'.join(lines[:500]) + (f'\n… {len(lines) - 500} more' if len(lines) > 500 else '')


def t_repo_read_file(a):
    d = mirror(a['repo'])
    ref = ref_ok(a.get('ref') or default_branch(d))
    p = path_ok(a['path'])
    if SECRET_PATH.search(p):
        return f'{p} looks like a secrets file, so it is not shown.'
    text = git(d, 'show', f'{ref}:{p}', timeout=90)
    lines = text.splitlines()
    s = max(int(a.get('start_line') or 1), 1)
    e = min(s + min(int(a.get('max_lines') or 300), 1000) - 1, len(lines))
    body = '\n'.join(f'{i}\t{lines[i - 1]}' for i in range(s, e + 1))
    return cap(scrub(f'{a["repo"]}:{ref}:{p} lines {s}-{e} of {len(lines)}\n{body}'))


def t_repo_grep(a):
    d = mirror(a['repo'])
    ref = ref_ok(a.get('ref') or default_branch(d))
    args = ['grep', '-n', '-I', '--max-count=20', '-i' if a.get('ignore_case', True) else '--no-ignore-case', '-e', a['pattern'], ref]
    if a.get('path'):
        args += ['--', path_ok(a['path'])]
    try:
        out = git(d, *args, timeout=120)
    except RuntimeError as e:
        if 'exit status 1' in str(e) or str(e).startswith('git grep'):
            return 'no matches'
        raise
    lines = [l for l in out.splitlines() if not SECRET_PATH.search(l.split(':', 2)[1] if l.count(':') >= 2 else '')]
    return cap(scrub('\n'.join(lines[:200]) + (f'\n… {len(lines) - 200} more' if len(lines) > 200 else ''))) or 'no matches'


def t_repo_diff(a):
    d = mirror(a['repo'])
    base = ref_ok(a.get('base') or default_branch(d))
    head = ref_ok(a['head'])
    args = ['diff', '--stat' if a.get('stat_only', True) else '--patch', f'{base}...{head}']
    if a.get('path'):
        args += ['--', path_ok(a['path'])]
    return cap(scrub(f'{a["repo"]}: changes on {head} since it left {base}\n' + git(d, *args, timeout=120)))


S = lambda **p: {'type': 'object', 'properties': p, 'required': [k for k, v in p.items() if v.pop('req', False)]}
R = lambda: {'type': 'string', 'description': 'repository name, as repos_list gives it', 'req': True}
TOOLS = {
    'repos_list': (t_repos_list, 'List the repositories Jarvis can read on the code host and how fresh each copy is.', S()),
    'repo_branches': (t_repo_branches, 'Branches of a repo, newest first: last commit date, author, ahead/behind the default branch, subject.',
                      S(repo=R(), filter={'type': 'string', 'description': 'substring, e.g. ABC-12 or export'}, limit={'type': 'integer'})),
    'repo_log': (t_repo_log, 'Recent commits on a branch (default: the default branch). merges_only shows merged pull requests.',
                 S(repo=R(), ref={'type': 'string'}, n={'type': 'integer'}, path={'type': 'string'}, merges_only={'type': 'boolean'})),
    'repo_commit': (t_repo_commit, 'One commit: message, author, date and the files it changed.', S(repo=R(), sha={'type': 'string', 'req': True})),
    'repo_files': (t_repo_files, 'List files/folders at a path on a branch.', S(repo=R(), ref={'type': 'string'}, path={'type': 'string'})),
    'repo_read_file': (t_repo_read_file, 'Read a file on any branch (numbered lines).',
                       S(repo=R(), path={'type': 'string', 'req': True}, ref={'type': 'string'}, start_line={'type': 'integer'}, max_lines={'type': 'integer'})),
    'repo_grep': (t_repo_grep, 'Search file contents on a branch (git grep, case-insensitive by default). Narrow with path for big repos.',
                  S(repo=R(), pattern={'type': 'string', 'req': True}, ref={'type': 'string'}, path={'type': 'string'}, ignore_case={'type': 'boolean'})),
    'repo_diff': (t_repo_diff, 'What a branch changed since it left base (default: the default branch). stat_only=false gives the patch.',
                  S(repo=R(), head={'type': 'string', 'req': True}, base={'type': 'string'}, path={'type': 'string'}, stat_only={'type': 'boolean'})),
}


def handle(msg):
    m, i = msg.get('method'), msg.get('id')
    if m == 'initialize':
        return {'jsonrpc': '2.0', 'id': i, 'result': {'protocolVersion': (msg.get('params') or {}).get('protocolVersion', '2025-06-18'),
                                                       'capabilities': {'tools': {}}, 'serverInfo': {'name': 'repos', 'version': '1.0'}}}
    if m == 'tools/list':
        return {'jsonrpc': '2.0', 'id': i, 'result': {'tools': [{'name': k, 'description': v[1], 'inputSchema': v[2]} for k, v in TOOLS.items()]}}
    if m == 'tools/call':
        p = msg.get('params') or {}
        fn = TOOLS.get(p.get('name'), (None,))[0]
        try:
            if not fn:
                raise ValueError('unknown tool')
            text, err = fn(p.get('arguments') or {}), False
        except subprocess.TimeoutExpired:
            text, err = 'The code host took too long to answer; try again or narrow it (path, branch).', True
        except Exception as e:
            text, err = f'{e.__class__.__name__}: {e}', True
        return {'jsonrpc': '2.0', 'id': i, 'result': {'content': [{'type': 'text', 'text': text}], 'isError': err}}
    if m == 'ping':
        return {'jsonrpc': '2.0', 'id': i, 'result': {}}
    if i is not None:
        return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32601, 'message': f'unknown method {m}'}}
    return None                        # notifications (initialized, cancelled) need no answer


def main():
    if '--selftest' in sys.argv:
        first = (repos() or [''])[0]
        for name, args in [('repos_list', {})] + ([('repo_branches', {'repo': first, 'limit': 5}),
                                                   ('repo_log', {'repo': first, 'n': 5, 'merges_only': True})] if first else []):
            print(f'## {name}\n' + TOOLS[name][0](args) + '\n')
        return
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except Exception:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
