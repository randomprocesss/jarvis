#!/usr/bin/env python3
"""Re-check the deploy gate's open findings against the code as it is now.

A verdict is a judgement on one commit. Newer commits pile up on top of it, and until one of them is reviewed in full
the old NO-GO looks current, though later commits may have fixed what it found. This re-checks every finding of every
open verdict (not replaced; NO-GO, NEEDS-INFO, GO-WITH-NOTES) mechanically, with no model and no network (the local
mirrors only):

  anchors   the `path:line` or `path:a-b` places a finding cites, and the text of those lines at the reviewed commit
  targets   the code as it is now: the head of the candidate's own branch, and the head of every deploy branch of the
            repository, each only if it contains the reviewed commit (code that never got the change says nothing
            about whether it was fixed)
  per anchor and target:
    there      the cited line's text is still in that file (anywhere: line numbers move)
    changed    the text is gone; the commit that removed it is named, with its author
    file gone  the file no longer exists at the target
  finding status:  STILL THERE (an anchor is there at a target) | LIKELY FIXED (every anchor changed or gone at every
                   target) | NEEDS A RE-RUN (only running the code again can tell) | ABOUT THAT PUSH (about how the
                   push was made, not about code)
  who to tell:     for STILL THERE, the author of that line at the target (git blame), not whoever merged first

Writes recheck.json in the gate's folder (per verdict, per finding) and prints a summary.
    recheck.py            re-check all open verdicts
    recheck.py --json     print recheck.json
    recheck.py selftest   every rule on a scratch repository
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import gateconf  # noqa: E402
from gateconf import paths  # noqa: E402

G = Path(gateconf.root())
MIRRORS = Path(gateconf.mirrors())
OUT = G / 'recheck.json'
OPEN = ('NO-GO', 'NEEDS-INFO', 'GO-WITH-NOTES')
ANCHOR = re.compile(r'`?((?:[A-Za-z0-9_./\-]*/)?\.?[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9]{1,6})?):(\d+)(?:\s*[-–]\s*(\d+))?`?')


def _git(repo, *a, timeout=120):
    return subprocess.run(['git', '-C', str(MIRRORS / f'{repo}.git'), *a], capture_output=True, text=True, timeout=timeout,
                          env=gateconf.git_env(), stdin=subprocess.DEVNULL)


def git(repo, *a):
    return _git(repo, *a).stdout


def file_at(repo, sha, path):
    r = _git(repo, 'show', f'{sha}:{path}', timeout=60)
    return r.stdout if r.returncode == 0 else None


def sections(text):
    """The findings of a verdict file: [(n, title, body)], each kept whole for its anchors."""
    k = text.find('## Findings')
    if k < 0:
        return []
    body = text[k + 11:]
    end = re.search(r'\n## ', body)
    body = body[:end.start()] if end else body
    out, cur = [], None
    for line in body.splitlines():
        m = re.match(r'^(\d+)\.\s+(.*)', line)
        if m:
            cur = [int(m.group(1)), m.group(2), [m.group(2)]]
            out.append(cur)
        elif cur is not None:
            cur[2].append(line)
    return [(n, finding_title(first)[0], '\n'.join(lines)) for n, first, lines in out]


# Every finding starts with the reviewer's weight in bold ("**Blocking:** ..."). The title is the finding itself: the
# bold lead when it is a sentence, and the sentence after it when the bold is only a weight word. A remark that comes
# with the weight ("already there before this change") is kept apart as the label. The weight word must be followed by a
# separator, so "High memory use" stays a title.
_W = r'blocking|note|notes|low|info|minor|high|medium|critical|nit|cosmetic|question'
WEIGHT = re.compile(rf'^\(?\s*(?:{_W})\s*(?:\)|(?=[:(,.])|$)\s*(?:,\s*([^:()]{{0,80}}))?\s*(?:\(([^)]{{0,100}})\))?\s*[:.]?\s*(.*)$', re.I)


def _lead_sentence(rest):
    """The first full sentence of a finding's text; a second one is added while the first is very short."""
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z`(/"])', (rest or '').strip())
    title = parts[0]
    for nxt in parts[1:]:
        if len(title) >= 40:
            break
        title += ' ' + nxt
    return title


def finding_title(first):
    """(title, label) of a finding from its first line."""
    first = (first or '').strip()
    b = re.match(r'\*\*(.+?)\*\*[\s:]*(.*)', first)
    lead, rest = (b.group(1).strip(), b.group(2).strip()) if b else (first, '')
    label = ''
    w = WEIGHT.match(lead)
    if w:                                                   # drop the weight word; keep its remark as the label
        label = (w.group(2) or w.group(1) or '').strip(' ,;')
        lead = (w.group(3) or '').strip()
    if not lead:
        title = _lead_sentence(rest)
    elif b and len(lead) < 30 and rest:                     # a topic word in bold ("Process."): say what about it
        title = lead.rstrip('.: ') + ': ' + _lead_sentence(rest)
    else:
        title = lead if b else _lead_sentence(lead)
    return (title or first)[:220], label[:100]


def anchors(repo, sha, body):
    """[(path, line number, text)] cited by a finding, with the cited lines' text at the reviewed commit."""
    out, seen = [], set()
    for m in ANCHOR.finditer(body):
        path, a = m.group(1).lstrip('./'), int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        src = file_at(repo, sha, path)
        if src is None:                                      # a short path: find the file it names
            hits = [p for p in git(repo, 'ls-tree', '-r', '--name-only', sha).splitlines() if p.endswith('/' + path) or p == path]
            if len(hits) == 1:
                path = hits[0]
                src = file_at(repo, sha, path)
        if src is None:
            continue
        lines = src.splitlines()
        for i in range(a, min(b, a + 5) + 1):                # a range: at most six lines of it
            if 1 <= i <= len(lines):
                t = lines[i - 1].strip()
                if len(t) >= 8 and (path, t) not in seen:    # blank and trivial lines say nothing
                    seen.add((path, t))
                    out.append((path, i, t))
    return out


CODE = re.compile(r'`([^`\n]{12,160})`')


def snippet_anchors(repo, sha, body):
    """A finding that quotes code instead of a line number: a quoted piece that looks like code and occurs in exactly
    one file at the reviewed commit becomes an anchor (path, its line, the whole line's text)."""
    out = []
    for m in CODE.finditer(body):
        s = m.group(1).strip()
        if not re.search(r'[(=\[{.:_]', s) or s.count(' ') > 12 or '/' in s and ' ' not in s:
            continue                                         # prose or a bare path, not code
        hits = git(repo, 'grep', '-n', '-F', '--max-count', '2', '-e', s, sha).splitlines()
        files = {h.split(':', 2)[1] for h in hits if h.count(':') >= 3}
        if len(files) != 1:
            continue
        _, path, ln, line = hits[0].split(':', 3)
        if len(line.strip()) >= 8:
            out.append((path, int(ln), line.strip()))
    return out


SECRETY = re.compile(r'(pass|pw\b|_pw|secret|token|_auth|auth\b|api[_-]?key|private|credential|cookie)', re.I)


def shown(text):
    """The cited line as it may be stored and shown: a line that looks like it holds a secret keeps its shape only (the
    quoted values blanked), then the knowledge base's own secret patterns run over it. The raw line is only ever
    compared in memory."""
    t = text
    if SECRETY.search(t):
        t = re.sub(r'(["\'])(?:(?!\1).)*\1', r'\1***\1', t)
        t = re.sub(r'(=\s*)[^\s"\',;]{6,}', r'\1***', t)
    try:
        paths.use_tools()
        import kb_redact
        t = kb_redact.redact(t)
    except Exception:
        pass
    return t


def check_anchor(repo, reviewed, target, path, text):
    src = file_at(repo, target, path)
    if src is None:
        return {'state': 'file gone'}
    for i, line in enumerate(src.splitlines(), 1):
        if line.strip() == text:
            b = git(repo, 'blame', '--porcelain', '-L', f'{i},{i}', target, '--', path)
            au = re.search(r'^author (.+)$', b, re.M)
            return {'state': 'there', 'author': f"{au.group(1) if au else '?'} ({b.split(' ', 1)[0][:8] if b else ''})"}
    logged = git(repo, 'log', '--format=%h|%an|%ad', '--date=short', '--reverse', f'-S{text}', f'{reviewed}..{target}', '--', path)
    first = logged.splitlines()[0].split('|') if logged.strip() else None
    return {'state': 'changed', 'by': {'commit': first[0], 'author': first[1], 'date': first[2]} if first else None}


def is_ancestor(repo, a, b):
    return a == b or _git(repo, 'merge-base', '--is-ancestor', a, b, timeout=60).returncode == 0


def targets_for(c, deploy):
    """{branch: commit} of the code as it is now for this verdict: its own branch and the repository's deploy branches,
    each only if it contains the reviewed commit."""
    repo, t = c['repo'], {}
    names = ([c['ref']] if c['kind'] in ('branch', 'deploy') else []) + [b for b in deploy if b != c['ref']]
    for name in names:
        h = git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{name}').strip()
        if h and is_ancestor(repo, c['sha'], h):
            t[name] = h
    return t


KINDS = [('push', r'\b(ticket|signed off|sign-off|scope|unrelated|ride along|riding|branch (is )?named|merge-base|branch from|pull request description|commit message|subtask)\b'),
         ('behaviour', r'\b(returned|returns|return(ed)? [2-5]\d\d|500|404|click|screen|walk|load test|p95|latency|rendered|reload|browser|under burst|timeout|crash)\b'),
         ('absence', r'(\bnot in\b|\bmissing\b|\bnowhere\b|0 hits|\bnot set\b|\bnever (run|called|scheduled)|does not (run|call|exist|validate)|\bno (test|step|deploy step|migration)s?\b)'),
         ('dependency', r'(package(-lock)?\.json|lockfile|requirements\.txt|\.npmrc|\^\d+\.\d+|version|dependency)')]
SERIOUS = re.compile(r'(no-go|\bhigh\b|critical|password|secret|credential|\bkey\b|token|_auth|injection|leak|exposed|data loss|drops? .{0,20}(table|column))', re.I)
SECRET_TOPIC = re.compile(r'(password|secret|credential|\bkey\b|token|_auth|\bpw\b)', re.I)

# Each finding carries the weight its reviewer gave it, read from its first line: a weight word at the start ("High:",
# "(Low)", "Info:"), one in brackets ("(low)", "(blocking)"), or a review that names its only reason (the other findings
# are then notes). A finding with no mark keeps the review's weight, so nothing serious is ever made light by a missing
# word; "already there" alone is not "low" (a committed credential is a problem whoever wrote it).
SEV_NOTE = re.compile(r'^\W*(low|minor|info|note|nit|cosmetic)\b|\((?:[^)]*[,;]\s*)?(low|minor|info|cosmetic)\b[^)]*\)|,\s*low\b', re.I)
SEV_BLOCK = re.compile(r'^\W*(blocking|critical|high|medium)\b|\(blocking\)|\bblocking\b\W*$', re.I)
ONLY_REASON = re.compile(r'only reason (?:the|this) verdict|the only blocker', re.I)


def finding_severity(verdict, first_line, only_reason_n=None, n=None):
    """'blocking' or 'note' for one finding. only_reason_n: the finding a review names as its only reason."""
    if verdict not in ('NO-GO', 'NEEDS-INFO'):
        return 'note'                                  # a review that passed accepted its findings as notes
    t = re.sub(r'\*\*', '', first_line or '')
    if SEV_BLOCK.search(t):
        return 'blocking'
    if SEV_NOTE.search(t):
        return 'note'
    if only_reason_n is not None:
        return 'blocking' if n == only_reason_n else 'note'
    return 'blocking'


def finding_kind(title, body):
    t = (title + ' ' + body).lower()
    for k, rx in KINDS:
        if k == 'push' and SERIOUS.search(title + ' ' + body):
            continue                                     # a serious finding is never just "about the push"
        if re.search(rx, t):
            return k
    return 'other'


FILEISH = re.compile(r'`((?:[A-Za-z0-9_\-]+/)*\.?[A-Za-z0-9_\-]+\.(?:py|js|ts|tsx|jsx|json|ya?ml|sh|toml|cfg|ini|txt|sql|md|html|css|lock|npmrc|env|tpl|xml|gradle|java|go|rs|rb|php|cs|kt|swift))`|`(\.npmrc|Dockerfile|Makefile)`')


def cited_files(repo, sha, body):
    """The files a finding names (in backticks), as paths of the repository at the reviewed commit."""
    names = {m.group(1) or m.group(2) for m in FILEISH.finditer(body)}
    if not names:
        return []
    allp = git(repo, 'ls-tree', '-r', '--name-only', sha).splitlines()
    out = []
    for n in names:
        n = n.lstrip('./') if not n.startswith('.npmrc') else n
        out += [p for p in allp if p == n or p.endswith('/' + n)][:3]
    return sorted(set(out))


def same_code(repo, a, b, files=None):
    return _git(repo, 'diff', '--quiet', a, b, *(['--', *files] if files else [])).returncode == 0


def unanchored(repo, reviewed, targets, body, kind):
    """A finding with no code line. The same code as reviewed: still there, whatever its kind; the files it names
    unchanged: still there; a claim that something is missing: checked by looking for it; else the code must run again."""
    tg = list(targets.values())
    if tg and all(same_code(repo, reviewed, t) for t in tg):
        return 'STILL THERE', 'the code now is exactly the reviewed code'
    files = cited_files(repo, reviewed, body)
    if files and tg and all(same_code(repo, reviewed, t, files) for t in tg):
        return 'STILL THERE', 'the files it names have not changed since the review (' + ', '.join(f.split('/')[-1] for f in files[:3]) + ')'
    if kind == 'absence' and files:
        for t in [t for t in re.findall(r'`([A-Za-z_][A-Za-z0-9_\-]{3,60})`', body) if '.' not in t and '/' not in t]:
            if any(t in (file_at(repo, reviewed, f) or '') for f in files):
                continue                                   # not what was missing
            if any(any(t in (file_at(repo, x, f) or '') for f in files) for x in tg):
                return 'LIKELY FIXED', f'`{t}` is now in ' + ', '.join(f.split('/')[-1] for f in files[:2])
            return 'STILL THERE', f'`{t}` is still missing from ' + ', '.join(f.split('/')[-1] for f in files[:2])
    if kind == 'push':
        return 'ABOUT THAT PUSH', 'about how that push was made, not about code; it closes when the push is replaced'
    return 'NEEDS A RE-RUN', ('the files it names changed since the review' if files else 'it names no file') + '; only running the code again can tell'


def _inputs(st):
    """What a re-check depends on: the open verdicts (id, commit, verdict file time), the branch heads, and this code."""
    vs = sorted((cid, c['sha'], c.get('verdict'), Path(c['verdict_path']).stat().st_mtime if c.get('verdict_path') and Path(c['verdict_path']).exists() else 0)
                for cid, c in st['candidates'].items() if c.get('status') == 'done' and c.get('verdict') in OPEN)
    heads = {r: git(r, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads') for r in sorted({c['repo'] for c in st['candidates'].values()})}
    return hashlib.sha256(json.dumps([vs, heads]).encode() + Path(__file__).read_bytes()).hexdigest()


def recheck(force=False):
    try:
        st = json.loads((G / 'state.json').read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault('candidates', {})
    key = _inputs(st)
    if not force and OUT.exists():
        try:
            old = json.loads(OUT.read_text())
            if old.get('inputs') == key:
                return old                                 # nothing changed since the last re-check
        except ValueError:
            pass
    conf = gateconf.load()
    res, tally = {}, {}
    for cid, c in st['candidates'].items():
        if c.get('status') != 'done' or c.get('verdict') not in OPEN:
            continue
        vp = Path(c['verdict_path'])
        if not vp.exists():
            continue
        names = {l.split(' ', 1)[0][len('refs/heads/'):] for l in git(c['repo'], 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads').splitlines()}
        default = git(c['repo'], 'symbolic-ref', '--short', 'HEAD').strip()
        tg = targets_for(c, conf.deploy_branches(c['repo'], names, default))
        if not tg:                                         # no newer code contains it: the review speaks for itself
            tg = {'reviewed': c['sha']}
        fs = []
        secs = sections(vp.read_text(errors='replace'))
        only = next((n for n, _, body in secs if ONLY_REASON.search(body)), None)
        for n, title, body in secs:
            an = anchors(c['repo'], c['sha'], body) or snippet_anchors(c['repo'], c['sha'], body)
            per = [{'path': path, 'line': ln, 'text': shown(text)[:160],
                    'at': {k: check_anchor(c['repo'], c['sha'], v, path, text) for k, v in tg.items()}} for path, ln, text in an]
            kind = finding_kind(title, body)
            if kind == 'push' and not per:
                status, how = 'ABOUT THAT PUSH', 'about how that push was made, not about code; it closes when the push is replaced'
            elif per and any(x.get('state') == 'there' for a in per for x in a['at'].values()):
                status, how = 'STILL THERE', 'the cited code is still in the code as it is now'
            elif per and SECRET_TOPIC.search(title + ' ' + body):
                status, how = 'NEEDS A RE-RUN', 'the cited line changed, but a secret can move rather than go: confirm on the code as it is now'
            elif per:
                status, how = 'LIKELY FIXED', 'the cited code was changed by later commits'
            else:
                status, how = unanchored(c['repo'], c['sha'], tg, body, kind)
            tally[status] = tally.get(status, 0) + 1
            fixed_by = sorted({json.dumps(x['by']) for a in per for x in a['at'].values() if x.get('by')}) if status == 'LIKELY FIXED' else []
            tell = sorted({x['author'] for a in per for x in a['at'].values() if x.get('state') == 'there' and x.get('author')})
            first = body.splitlines()[0] if body else title
            fs.append({'n': n, 'title': title, 'label': finding_title(first)[1], 'status': status, 'kind': kind, 'how': how,
                       'anchors': per, 'severity': finding_severity(c['verdict'], first, only, n),
                       'fixed_by': [json.loads(x) for x in fixed_by], 'tell': tell})
        on_top = {k: int(git(c['repo'], 'rev-list', '--count', f"{c['sha']}..{v}").strip() or 0) for k, v in tg.items()}
        res[cid] = {'repo': c['repo'], 'kind': c['kind'], 'ref': c['ref'], 'sha': c['sha'], 'date': c.get('date'), 'verdict': c['verdict'],
                    'targets': tg, 'commits_on_top': on_top, 'findings': fs,
                    'still_there': sum(f['status'] == 'STILL THERE' for f in fs),
                    'likely_fixed': sum(f['status'] == 'LIKELY FIXED' for f in fs),
                    'needs_rerun': sum(f['status'] == 'NEEDS A RE-RUN' for f in fs),
                    'about_push': sum(f['status'] == 'ABOUT THAT PUSH' for f in fs)}
    out = {'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'inputs': key, 'tally': tally, 'verdicts': res}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, indent=1))
    tmp.replace(OUT)
    return out


def selftest():
    """Every rule on a scratch repository (no network, nothing outside a temporary folder). Returns the failures."""
    import tempfile
    global MIRRORS
    keep, fails = MIRRORS, []

    def t(name, cond):
        if not cond:
            fails.append(name)
    with tempfile.TemporaryDirectory() as d:
        MIRRORS = Path(d)
        r = Path(d) / 'fx.git'
        r.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME='Tester', GIT_AUTHOR_EMAIL='tester@example.com', GIT_COMMITTER_NAME='Tester',
                   GIT_COMMITTER_EMAIL='tester@example.com')

        def sh(*a):
            return subprocess.run(['git', '-C', str(r), *a], capture_output=True, text=True, check=True, env=env).stdout.strip()

        def commit(msg):
            sh('add', '-A')
            sh('commit', '-qm', msg)
            return sh('rev-parse', 'HEAD')
        try:
            sh('init', '-q', '-b', 'main')
            (r / 'app.py').write_text("import os\nPW = os.environ.get('X', 'not-a-real-one-1')\ndef go():\n    return run_job(step='totals')\n")
            (r / 'sched.sh').write_text('run a\nrun b\n')
            (r / 'b.py').write_text('x = 1\n')
            c1 = commit('c1')
            (r / 'b.py').write_text('x = 2\n')
            c2 = commit('c2')
            (r / 'app.py').write_text("import os\nPW = os.environ['X']\ndef go():\n    return run_job(step='totals')\n")
            c3 = commit('c3 read it from the environment')
            (r / 'sched.sh').write_text('run a\nrun b\nrun nightly_totals\n')
            c4 = commit('c4 schedule')
            an = anchors('fx', c1, 'Where: `app.py:4` calls it')
            t('an anchor is read at the reviewed commit', an and an[0][2] == "return run_job(step='totals')")
            t('a line still there', check_anchor('fx', c1, c4, 'app.py', an[0][2])['state'] == 'there')
            pw = anchors('fx', c1, '`app.py:2`')[0][2]
            ch = check_anchor('fx', c1, c3, 'app.py', pw)
            t('a changed line names the commit that changed it', ch['state'] == 'changed' and ch['by'] and c3.startswith(ch['by']['commit']))
            t('the stored text hides the secret', 'not-a-real-one-1' not in shown(pw))
            t('same code: still there', unanchored('fx', c1, {'main': c1}, 'the page breaks on reload', 'behaviour')[0] == 'STILL THERE')
            t('named file unchanged: still there', unanchored('fx', c1, {'main': c2}, 'slow call in `app.py`', 'behaviour')[0] == 'STILL THERE')
            t('named file changed: needs a re-run', unanchored('fx', c1, {'main': c3}, 'slow call in `app.py`', 'behaviour')[0] == 'NEEDS A RE-RUN')
            t('something missing is still missing', unanchored('fx', c1, {'main': c3}, '`nightly_totals` is not in `sched.sh`', 'absence')[0] == 'STILL THERE')
            t('something missing is now there', unanchored('fx', c1, {'main': c4}, '`nightly_totals` is not in `sched.sh`', 'absence')[0] == 'LIKELY FIXED')
            t('a finding about the push', finding_kind('Info: the branch is named for another ticket', '') == 'push')
            t('a serious finding is never about the push', finding_kind('NO-GO: password committed', 'ticket ABC-12 scope') != 'push')
            sh('checkout', '-qb', 'side', c1)
            (r / 'app.py').write_text('print(1)\n')
            side = commit('side')
            t('code that never got the change is not newer code', not is_ancestor('fx', side, c4) and is_ancestor('fx', c1, c4))
            tg = targets_for({'repo': 'fx', 'kind': 'branch', 'ref': 'side', 'sha': side}, ['main'])
            t('the targets are the branches that hold the reviewed commit', tg == {'side': side})
        finally:
            MIRRORS = keep
    sev = [('NO-GO', '**Committed credential (blocking).**', 'blocking'), ('NO-GO', 'Leftovers, low:', 'note'),
           ('NO-GO', '**Silent fallback on old data (P-6, low).** The bare', 'note'), ('NO-GO', 'Low notes:', 'note'),
           ('NO-GO', '**Question for the owner (low):** count', 'note'), ('NO-GO', 'Minor, already there: the converse', 'note'),
           ('NO-GO', '**High: the orders page returns an error**', 'blocking'), ('NO-GO', '**Info:** today is taken as UTC', 'note'),
           ('NEEDS-INFO', '**Medium (new with this change): the filter', 'blocking'), ('NO-GO', '**(Low) the role check fails closed.**', 'note'),
           ('NO-GO', '**(Already there, not in the diff) Committed credentials.** `.npmrc:5`', 'blocking'),
           ('NO-GO', '**A failed rollout reports success** (deploy.sh:41', 'blocking'), ('GO-WITH-NOTES', '**A failed rollout reports success**', 'note')]
    for v, line, want in sev:
        t(f'weight {want}: {v} {line[:40]}', finding_severity(v, line) == want)
    t("a review's only reason blocks, its other unmarked findings are notes",
      finding_severity('NEEDS-INFO', '**No ticket**', 2, 1) == 'note' and finding_severity('NEEDS-INFO', '**The address is unknown**', 2, 2) == 'blocking')
    ttl = [('**Blocking:** The export password is still committed, at export.py:35 and :36.', 'The export password is still committed, at export.py:35 and :36.', ''),
           ('**Note:** the change to deploy.sh in this push has no ticket. It changes what blocks a deploy.', 'the change to deploy.sh in this push has no ticket.', ''),
           ('**Note:** No ticket. The change is small.', 'No ticket. The change is small.', ''),
           ('**Blocking (already there before this change):** `.npmrc:5` holds a token.', '`.npmrc:5` holds a token.', 'already there before this change'),
           ('**Note (P-9):** the cookie is set without Secure.', 'the cookie is set without Secure.', 'P-9'),
           ('**A failed rollout reports success** (deploy.sh:41', 'A failed rollout reports success', ''),
           ('**High: the orders page returns an error**', 'the orders page returns an error', ''),
           ('**(Low) the role check fails closed for every screen.**', 'the role check fails closed for every screen.', ''),
           ('**Committed credential (blocking).**', 'Committed credential (blocking).', ''),
           ('**High memory use in the loader**', 'High memory use in the loader', ''),
           ('**Process.** The tag was cut before the tests ran.', 'Process: The tag was cut before the tests ran.', ''),
           ('Stale comment.', 'Stale comment.', ''),
           ('Low: the helper is unused. Remove it.', 'the helper is unused. Remove it.', '')]
    for line, want, lab in ttl:
        t(f'title: {line[:44]}', finding_title(line) == (want, lab))
    return fails


if __name__ == '__main__':
    if '--json' in sys.argv:
        print(OUT.read_text() if OUT.exists() else '{}')
        sys.exit(0)
    if 'selftest' in sys.argv:
        bad = selftest()
        for x in bad:
            print('FAIL', x)
        print('selftest:', 'PASS' if not bad else f'FAIL ({len(bad)})')
        sys.exit(1 if bad else 0)
    o = recheck(force='--force' in sys.argv)
    print(f"{len(o['verdicts'])} open verdicts re-checked; findings: {o['tally']}")
    for cid, v in sorted(o['verdicts'].items(), key=lambda x: str(x[1]['date'])):
        print(f"{str(v['date'])[:10]} {v['repo'][:22]:22} {v['kind']:7} {v['ref'][:28]:28} {v['sha'][:8]} {v['verdict']:13} "
              f"on top: {v['commits_on_top']} | still {v['still_there']}, fixed {v['likely_fixed']}, to run again {v['needs_rerun']}")
