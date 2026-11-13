"""The owner's repositories, read into the knowledge base: the git repositories in their work folders and the mirrors
Jarvis keeps (repos/*.git in the data home), into brain/sources_extra/l_repo_* units. No model use here; kb_extra reads
the units into notes like every other source.

  - per repository: its README on the main branch, and every commit by month (date, author, branches, subject and body;
    merge commits marked as merged pull requests). A repository shared with many other teams (settings:
    sources.shared_repos) keeps only the owner's own commits and the ones that name their work (work.search_words).
  - their code: for every repository that is not shared, each branch with their commits: the files it changes, with each
    Python module's docstring and its function and class signatures, so the code map can say what each piece does.

    kb_repos.py run
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config, paths
import datetime as dt, glob, os, re, subprocess, sys
from collections import defaultdict

HUB = str(paths.home())
REPOS = os.path.join(HUB, 'repos')
X = os.path.join(HUB, 'brain', 'sources_extra')


def repos():
    """{name: path}: the mirrors Jarvis keeps, then the git repositories in the work folders (a mirror wins a name)."""
    out = {os.path.basename(r)[:-4]: r for r in sorted(glob.glob(os.path.join(REPOS, '*.git')))}
    c = config.load()
    for d in [str(paths.workdir())] + [os.path.expanduser(str(x)) for x in c.get('work.folders') or []]:
        try:
            subs = sorted(os.listdir(d))
        except OSError:
            continue
        for repo in [d] + [os.path.join(d, s) for s in subs]:
            if os.path.isdir(os.path.join(repo, '.git')):
                out.setdefault(os.path.basename(repo), repo)
    return out


def _owner():
    """(their names for git: the name and the first part of each e-mail address, a pattern over those, a pattern over
    the words that name their work or None)."""
    c = config.load()
    names = [n for n in [c.owner()] + [e.split('@')[0] for e in c.emails()] if n and len(n) >= 3]
    me = re.compile('|'.join(re.escape(n) for n in names), re.I) if names else None
    words = [w for w in c.search_words() if len(str(w)) >= 3]
    work = re.compile(r'\b(?:' + '|'.join(re.escape(str(w)) for w in words) + r')\b', re.I) if words else None
    return names, me, work


def _shared():
    return {str(n).strip() for n in config.load().get('sources.shared_repos') or [] if str(n).strip()}


def git(repo, *a):
    return subprocess.run(['git', '-C', repo] + list(a), capture_output=True, text=True, errors='replace').stdout


def write(name, header, body):
    os.makedirs(X, exist_ok=True)
    open(os.path.join(X, name + '.txt'), 'w').write(header + '\n\n' + body.strip() + '\n')


def head_branch(repo):
    b = git(repo, 'symbolic-ref', '--short', 'HEAD').strip()
    return b or 'main'


def commits(repo, shared):
    """[(date, author, subject, body, is_merge, sha)] across every branch, oldest first."""
    sep, end = '\x1f', '\x1e'
    raw = git(repo, 'log', '--all', '--date=short', f'--pretty=format:%H{sep}%ad{sep}%an{sep}%P{sep}%s{sep}%b{sep}%ae{end}')
    _, me, work = _owner()
    out = []
    for rec in raw.split(end):
        f = rec.strip('\n').split(sep)
        if len(f) < 6:
            continue
        sha, day, who, parents, subj, body = f[:6]
        mail = f[6] if len(f) > 6 else ''
        if shared and not ((work and work.search(subj + ' ' + body)) or (me and me.search(who + ' ' + mail))):
            continue
        out.append((day, who, subj.strip(), body.strip(), len(parents.split()) > 1, sha))
    return sorted(out)


def code_map(repo, name):
    """For each branch with their commits: the Python modules it changed, with docstrings and signatures."""
    br = [b.strip() for b in git(repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads', 'refs/remotes').splitlines() if b.strip()]
    n, names = 0, _owner()[0]
    if not names:
        return 0                                         # nobody to look for: the owner has not said who they are yet
    for b in br:
        mine = git(repo, 'log', b, *[f'--author={x}' for x in names], '--pretty=format:%H', '-n', '400').split()
        if not mine:
            continue
        files = sorted({f for sha in mine for f in git(repo, 'show', '--name-only', '--pretty=format:', sha).split()
                        if f.endswith(('.py', '.ipynb', '.md', '.sql', '.yaml', '.yml', '.toml', '.sh'))})
        parts = [f'Branch {b}: {len(mine)} of their commits touch {len(files)} files.']
        for f in files[:300]:
            src = git(repo, 'show', f'{b}:{f}')
            if not src:
                continue
            if f.endswith('.py'):
                doc = re.match(r'\s*(?:#.*\n)*\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', src, re.S)
                sigs = re.findall(r'^(class \w+[^:]*:|def \w+\([^)]*\)[^:]*:)', src, re.M)
                parts.append(f'### {f} ({len(src.splitlines())} lines)\n' + (doc.group(1).strip()[:1500] + '\n' if doc else '')
                             + '\n'.join(sigs[:80]))
            elif f.endswith('.md'):
                parts.append(f'### {f}\n' + src[:3000])
            else:
                parts.append(f'### {f} ({len(src.splitlines())} lines)\n' + src[:800])
        slug = re.sub(r'[^a-z0-9]+', '-', b.lower()).strip('-')[:60]
        write(f'l_repo_{name}_code_{slug}', f'Code map: {name}, branch {b} (their commits and the files they touch) | read {dt.date.today()}',
              '\n\n'.join(parts))
        n += 1
    return n


def run():
    out, shared = {}, _shared()
    for name, repo in repos().items():
        hb = head_branch(repo)
        readme = next((git(repo, 'show', f'{hb}:{r}') for r in ('README.md', 'Readme.md', 'readme.md', 'README.rst', 'README')
                       if git(repo, 'show', f'{hb}:{r}')), '')
        if readme:
            write(f'l_repo_{name}_readme', f'Repository {name}: README on {hb} | read {dt.date.today()}', readme)
        cs = commits(repo, name in shared)
        by = defaultdict(list)
        for day, who, subj, body, merge, sha in cs:
            by[day[:7]].append(f"- {day} · {who} · {sha[:8]}{' · merged pull request' if merge else ''}: {subj}" + (f"\n  {body[:1500]}" if body else ''))
        for mon, lines in by.items():
            write(f'l_repo_{name}_log_{mon}', f'Repository {name}: {len(lines)} commits in {mon}' + (' (their own and the ones that name their work)' if name in shared else '')
                  + f' | read {dt.date.today()}', '\n'.join(lines))
        maps = code_map(repo, name) if name not in shared else 0
        out[name] = {'commits': len(cs), 'months': len(by), 'readme': bool(readme), 'code_maps': maps}
        print(name, out[name], flush=True)
    return out


if __name__ == '__main__':
    if sys.argv[1:2] == ['run']:
        run()
    else:
        print(__doc__)
