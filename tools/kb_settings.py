#!/usr/bin/env python3
"""The settings of the owner's own pipelines and analyses, read from their code (a setting often lives only there, in no
note, so nobody can answer "what tolerance did that run use?"). No model use here; kb_extra reads each unit into a note,
and the ledger takes the values from that note.

Which code: the owner's own list (settings: knowledge.pipelines, one "name = folder" per line, for a pipeline that is part
of a repository or lives outside the work folders); without a list, every git repository in the work folders.

Per pipeline, every setting in code: module-level UPPERCASE constants and config dicts (.py), top-level UPPERCASE
assignments in notebook code cells, argparse defaults, docker-compose / .toml / .yaml / .env-style values, each with file,
line and the comment on that line. Names that look like credentials are recorded as "is set (value withheld)"; every unit then
goes through the secrets vault and redaction like every other source.

    kb_settings.py run      -> brain/sources_extra/e_settings_<pipeline>.txt
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config, paths
import ast, datetime as dt, glob, json, os, re, subprocess, sys

HOME = os.path.expanduser('~')
HUB = str(paths.home())
X = os.path.join(HUB, 'brain', 'sources_extra')


def _slug(s):
    return re.sub(r'[^a-z0-9]+', '-', str(s).lower()).strip('-')[:60] or 'pipeline'


def pipelines():
    """{name: folder} to read: the owner's own list, else every git repository in the work folders."""
    out, c = {}, config.load()
    for line in c.get('knowledge.pipelines') or []:
        name, sep, path = str(line).partition('=')
        if sep and name.strip() and path.strip():
            out[_slug(name)] = os.path.expanduser(path.strip())
    if out:
        return out
    for d in [str(paths.workdir())] + [os.path.expanduser(str(x)) for x in c.get('work.folders') or []]:
        try:
            subs = sorted(os.listdir(d))
        except OSError:
            continue
        for repo in [d] + [os.path.join(d, s) for s in subs]:
            if os.path.isdir(os.path.join(repo, '.git')):
                out.setdefault(_slug(os.path.basename(repo)), repo)
    return out
SECRET_NAME = re.compile(r'pass(word)?|secret|token|api[_-]?key|auth|credential|private[_-]?key|dsn|conn(ection)?[_-]?str', re.I)
WITHHELD = 'is set (value withheld: looks like a credential)'   # worded so the secret screens do not read it as a secret
SKIP = ('/.git/', '/node_modules/', '/.venv', '/venv', '/__pycache__/', '/site-packages/', '.egg-info', '/evidence/', '/results/', '/data/')


def _short(v, n=160):
    v = re.sub(r'\s+', ' ', v.strip())
    return v if len(v) <= n else v[:n] + ' …'


def _py(src, path, rel, out, notebook_cell=None):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    lines = src.split('\n')
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in targets:
            if not (name.isupper() or name in ('CONFIG', 'config', 'PARAMS', 'params', 'SETTINGS', 'settings')) or len(name) < 2:
                continue
            seg = ast.get_source_segment(src, node.value) if getattr(node, 'value', None) is not None else ''
            line = lines[node.lineno - 1]
            comment = line.split('#', 1)[1].strip() if '#' in line else ''
            lead = f'- {name} {WITHHELD}' if SECRET_NAME.search(name) else f"- {name} = {_short(seg or '')}"
            where = f'{rel}:{node.lineno}' + (f' (cell {notebook_cell})' if notebook_cell is not None else '')
            out.append(lead + (f'   # {_short(comment, 200)}' if comment else '') + f'   [{where}]')
    for node in ast.walk(tree):                          # argparse defaults
        if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'add_argument':
            flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            kw = {k.arg: k.value for k in node.keywords}
            if flags and 'default' in kw:
                d = ast.get_source_segment(src, kw['default']) or ''
                val = '(value withheld)' if any(SECRET_NAME.search(f) for f in flags) else _short(d)
                out.append(f"- {' / '.join(flags)} default {val}   [{rel}:{node.lineno}]")


def _kv(path, rel, out):
    for i, line in enumerate(open(path, errors='replace').read().split('\n'), 1):
        m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*(.+?)\s*$', line)
        if not m or line.strip().startswith('#'):
            continue
        k, v = m.group(1), m.group(2)
        if v in ('', '|', '>') or len(k) > 60:
            continue
        secret = SECRET_NAME.search(k) or SECRET_NAME.search(v[:40])
        out.append((f'- {k} {WITHHELD}' if secret else f'- {k}: {_short(v)}') + f'   [{rel}:{i}]')


def pipeline(name, root):
    out = []
    if not os.path.isdir(root):
        return None
    commit = subprocess.run(['git', '-C', root, 'log', '-1', '--format=%h %ad %s', '--date=short'], capture_output=True, text=True).stdout.strip()
    for dp, dn, fn in os.walk(root):
        if any(s in dp + '/' for s in SKIP):
            dn[:] = []
            continue
        for f in sorted(fn):
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root)
            n0 = len(out)
            try:
                if f.endswith('.py'):
                    _py(open(p, errors='replace').read(), p, rel, out)
                elif f.endswith('.ipynb'):
                    nb = json.load(open(p))
                    for ci, c in enumerate(nb.get('cells') or []):
                        if c.get('cell_type') == 'code':
                            src = ''.join(c.get('source') or [])
                            src = '\n'.join(l for l in src.split('\n') if not l.lstrip().startswith(('%', '!')))
                            _py(src, p, rel, out, ci)
                elif f in ('docker-compose.yml', 'docker-compose.yaml', 'pyproject.toml') or f.endswith(('.toml', '.yaml', '.yml', '.cfg', '.ini')) \
                        or f.startswith('.env') or f.endswith('.env'):
                    _kv(p, rel, out)
            except Exception as e:
                out.append(f'- (could not read {rel}: {e.__class__.__name__})')
            if len(out) > n0:
                out.insert(n0, f'\n### {rel} (modified {dt.datetime.fromtimestamp(os.path.getmtime(p)):%Y-%m-%d})')
    if not out:
        return None
    head = (f'Settings of the {name} pipeline, read from its code on {dt.date.today()} (in code, no model): every '
            f'module constant, notebook setting, command-line default and config value, with file and line | {root.replace(HOME, "~")}'
            + (f' | git {commit}' if commit else ''))
    return head, '\n'.join(out)


def run():
    paths.use_tools()
    import kb_redact, kb_secrets_vault as SV
    n = 0
    os.makedirs(X, exist_ok=True)
    for name, root in pipelines().items():
        r = pipeline(name, root)
        if not r:
            print(f'{name}: nothing found at {root}'); continue
        head, body = r
        p = os.path.join(X, f'e_settings_{name}.txt')
        text = head + '\n\n' + body + '\n'
        open(p, 'w').write(text)
        if SV.has_secret(text):
            SV.keep(p, text); kb_redact.redact_file(p)
        print(f'{name}: {body.count(chr(10) + "- ") + (1 if body.startswith("- ") else 0)} settings, {len(text) // 1000} KB')
        n += 1
    print(f'{n} units written')


if __name__ == '__main__':
    if sys.argv[1:2] == ['run']:
        run()
    else:
        print(__doc__)
