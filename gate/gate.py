#!/usr/bin/env python3
"""The deploy gate: it watches the owner's repositories for changes that are about to go out, reviews each one locally
with a Claude reviewer, and writes a GO / NO-GO verdict.

What it never does: push, merge, tag, deploy, touch a shared database, or send a message to anyone. It fetches, reads,
builds and tests on this computer, and writes files in the data home. Every outward step stays the owner's.

The loop (the server starts `gate.py run` every 30 minutes when settings say modules.deploy_gate; it exits at once if
another copy is running):
  1. fetch a light mirror of each watched repository (fetch only), in the data home
  2. find candidates per repository
       branch  a feature branch with commits no deploy branch has yet           -> about to be merged
       deploy  new commits on a deploy branch since the last look               -> pushed
       tag     a new tag that matches a release pattern                         -> a release
     A newer commit on the same branch replaces the older one that was still waiting.
  3. ledger.jsonl gets every candidate the first time it is seen (what is being pushed, and when)
  4. review what is due (tags first, then deploy-branch pushes, then branches; the owner's own changes before others;
     newest first), each in its own git worktree with the reviewer (gate/reviewer.md): diff review, build and tests,
     run locally, behaviour against the ticket, local stress test, verdict file
  5. a verdict counts only if check_verdict() accepts it; otherwise it is a failed try, tried again after a wait, up
     to the limit; then the change is `stuck` and the owner is told. A Claude usage limit is not the change's fault and
     does not use up a try.
  6. report.md is rewritten; NO-GO, NEEDS-INFO, stuck and every release tag ping the owner

Rails: one scheduler at a time (a file lock); reviews wait while Claude's usage window is nearly used or limited, and
while the disk is nearly full; a review is stopped, with everything it started, at its time limit; leftover worktrees
are removed after every review and at the start of a run; a run starts at most `max_reviews` reviews; the reviewer runs
under the workers' guard (no sends, no credential stores) and in an environment where git cannot push.

  gate.py run                      look, then review what is due (what the server calls)
  gate.py scan [--repo NAME]       look only, and print the candidates
  gate.py review --id ID           review one candidate now (the owner's press: no wait between tries)
  gate.py status                   one line per candidate
  gate.py verdict-for REPO COMMIT [TARGET]   the verdict that governs pushing this commit (for a pre-push hook)
  gate.py selftest                 offline checks of the harness (no Claude usage, no network)
  recheck.py                       re-check open findings against the code as it is now

Settings: [gate] in config.toml (gate/gateconf.py; docs/DEPLOY-GATE.md). With no repository named it does nothing and
says so.
"""
import argparse
import contextlib
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import gateconf  # noqa: E402
import osdep  # noqa: E402
from gateconf import config, paths  # noqa: E402

ROOT = Path(gateconf.root())
MIRRORS = Path(gateconf.mirrors())
STATE = ROOT / 'state.json'
STATE_LOCK = ROOT / 'state.lock'
LEDGER = ROOT / 'ledger.jsonl'
VERDICTS = ROOT / 'verdicts'
WORKTREES = ROOT / 'wt'
LOGS = ROOT / 'logs'
LOCK = ROOT / 'gate.lock'
CLAIMS = ROOT / 'claims'
REPORT_MD = ROOT / 'report.md'
HEALTH = ROOT / 'health.json'
MAINTENANCE = ROOT / 'MAINTENANCE'       # present: `run` does nothing at all (the code is being changed)
PAUSE = ROOT / 'PAUSED'                  # present: automatic reviews wait; looking goes on, and review --id still works

BACKOFF_S = [0, 30 * 60, 2 * 3600]       # the wait before try 1, 2, 3 and later
REFRESH_S = 2 * 3600                     # a long run looks at the repositories again this often
POLL_S = 30
RESUMES = 3
VERDICTS_OK = ('GO', 'GO-WITH-NOTES', 'NO-GO', 'NEEDS-INFO')
PASSED = ('GO', 'GO-WITH-NOTES')
BLOCKING = ('NO-GO', 'NEEDS-INFO')
REQUIRED_SECTIONS = ('## What is being pushed', '## Intent checklist', '## Checks', '## Findings', '## Before it goes further',
                     '## Not covered')
KIND_ORDER = {'tag': 0, 'deploy': 1, 'branch': 2}
AGENT = 'jarvis-deploy-gate'
TRACKER_TOOLS = ['mcp__claude_ai_Atlassian_Rovo__getJiraIssue', 'mcp__claude_ai_Atlassian_Rovo__searchJiraIssuesUsingJql']
MCP_ENV = {'CLAUDE_CODE_MCP_STARTUP_WAIT_MS': '20000', 'CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS': '20000'}
NO_PUSH = gateconf.NO_PUSH

_CONF = None


def conf():
    global _CONF
    if _CONF is None:
        _CONF = gateconf.load()
    return _CONF


def tool_rules(c, python):
    """(allowed, denied) for the reviewer's run. The session cannot ask: a command that is not allowed is refused."""
    pys = [f'python3.{v}' for v in range(9, 15)]
    allowed = ['Read', 'Grep', 'Glob', 'Write', 'Edit', *(TRACKER_TOOLS if c.tracker else []),
               f'Bash({python}:*)', 'Bash(python3:*)', 'Bash(python:*)', *[f'Bash({p}:*)' for p in pys], 'Bash(uv:*)', 'Bash(ruff:*)',
               'Bash(pytest:*)', 'Bash(pip:*)', 'Bash(pip3:*)', 'Bash(.dg-venv/bin/*:*)', 'Bash(.dg-venv/bin/*)',
               'Bash(npm ci:*)', 'Bash(npm install:*)', 'Bash(npm run:*)', 'Bash(npm test:*)', 'Bash(npx:*)', 'Bash(node:*)', 'Bash(make:*)',
               'Bash(export:*)', 'Bash(echo:*)', 'Bash(cd:*)', 'Bash(pwd)', 'Bash(test:*)', 'Bash(which:*)', 'Bash(ls:*)', 'Bash(cat:*)',
               'Bash(head:*)', 'Bash(tail:*)', 'Bash(wc:*)', 'Bash(grep:*)', 'Bash(find:*)', 'Bash(sed -n:*)', 'Bash(sort:*)', 'Bash(uniq:*)',
               'Bash(jq:*)', 'Bash(cp:*)', 'Bash(touch:*)', 'Bash(mkdir:*)', 'Bash(du:*)', 'Bash(tar:*)', 'Bash(sleep:*)', 'Bash(lsof:*)',
               'Bash(kill:*)',
               'Bash(git log:*)', 'Bash(git diff:*)', 'Bash(git show:*)', 'Bash(git rev-list:*)', 'Bash(git rev-parse:*)',
               'Bash(git merge-base:*)', 'Bash(git merge-tree:*)', 'Bash(git branch:*)', 'Bash(git tag -l:*)', 'Bash(git status:*)',
               'Bash(git archive:*)', 'Bash(git ls-tree:*)', 'Bash(git blame:*)',
               'Bash(curl -s http://127.0.0.1:*)', 'Bash(curl -s http://localhost:*)']
    if c.docker:                                       # local, throwaway, named after the review's own project
        allowed += ['Bash(docker info:*)', 'Bash(docker version:*)', 'Bash(docker images:*)', 'Bash(docker pull:*)', 'Bash(docker run:*)',
                    'Bash(docker rm:*)', 'Bash(docker exec:*)', 'Bash(docker stop:*)', 'Bash(docker network:*)', 'Bash(docker volume ls:*)',
                    'Bash(docker compose:*)', 'Bash(docker build:*)', 'Bash(docker ps:*)', 'Bash(docker logs:*)', 'Bash(docker stats:*)',
                    'Bash(docker inspect:*)', 'Bash(psql -h 127.0.0.1:*)', 'Bash(psql -h localhost:*)', 'Bash(pg_isready:*)']
    denied = ['Bash(git push:*)', 'Bash(git merge:*)', 'Bash(git commit:*)', 'Bash(git tag -a:*)', 'Bash(git tag -d:*)', 'Bash(git reset:*)',
              'Bash(git update-ref:*)', 'Bash(git fetch:*)', 'Bash(git pull:*)', 'Bash(git remote:*)', 'Bash(git worktree:*)',
              'Bash(git checkout:*)', 'Bash(git config:*)', 'Bash(gh:*)', 'Bash(kubectl:*)', 'Bash(helm:*)', 'Bash(gcloud:*)',
              'Bash(gsutil:*)', 'Bash(bq:*)', 'Bash(az:*)', 'Bash(terraform:*)', 'Bash(aws:*)', 'Bash(ssh:*)', 'Bash(scp:*)', 'WebFetch',
              'WebSearch', 'Bash(docker push:*)', 'Bash(docker login:*)', 'Bash(npm publish:*)']
    return allowed, denied


# ---------------------------------------------------------------- small helpers
def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def ts():
    return time.time()


def pdate(s):
    """A git or ISO date. Older Pythons refuse a trailing Z and more than six fraction digits; both are put right here.
    A date without a zone is taken as UTC."""
    s = str(s).strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    s = re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], s)
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def dkey(s):
    """A date as an instant, for sorting and comparing (commit dates carry different time zones)."""
    try:
        return pdate(str(s)).timestamp()
    except Exception:
        return 0.0


def log(msg):
    print(f'{now()} {msg}', flush=True)


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp')     # two writers never share a temporary file
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    tmp.replace(path)


def _clean(text):
    """An error line without any sign-in part of an address."""
    return re.sub(r'(https?://)[^@\s/]*@', r'\1', str(text or '')).strip()


def git(repo, *args, check=True, timeout=300):
    r = subprocess.run(['git', '-C', str(gateconf.mirror(repo)), *args], capture_output=True, text=True, timeout=timeout,
                       env=gateconf.git_env(), stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo}: {_clean(r.stderr)[:300]}")
    return r.stdout.strip()


def git_ok(repo, *args):
    return subprocess.run(['git', '-C', str(gateconf.mirror(repo)), *args], capture_output=True, env=gateconf.git_env(),
                          stdin=subprocess.DEVNULL).returncode == 0


def count(repo, rng):
    return int(git(repo, 'rev-list', '--count', rng, check=False) or 0)


@contextlib.contextmanager
def single_runner():
    """One scheduler at a time. Yields False when another one holds the lock."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, 'w')
    if not osdep.lock(f, wait=False):
        f.close()
        yield False
        return
    f.write(str(os.getpid()))
    f.flush()
    try:
        yield True
    finally:
        osdep.unlock(f)
        f.close()


def _load_state_strict():
    """The record, or an error. A record that cannot be read is copied aside and nothing is written over it: an empty
    one saved in its place would lose every verdict."""
    if not STATE.exists():
        return {}
    raw = STATE.read_text()
    try:
        return json.loads(raw)
    except Exception as e:
        bad = STATE.with_name(f'state.corrupt.{int(time.time())}.json')
        bad.write_text(raw)
        raise RuntimeError(f'state.json is unreadable ({e}); copied to {bad.name}, nothing overwritten') from e


@contextlib.contextmanager
def state_txn():
    """Read, change and write the record under one short lock (reviews running side by side must not overwrite each other)."""
    STATE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(STATE_LOCK, 'w')
    osdep.lock(f)
    try:
        st = _load_state_strict()
        st.setdefault('candidates', {})
        yield st
        save(STATE, st)
    finally:
        osdep.unlock(f)
        f.close()


def _claim_path(cid):
    CLAIMS.mkdir(parents=True, exist_ok=True)
    return CLAIMS / (hashlib.sha1(cid.encode()).hexdigest()[:16] + '.lock')


@contextlib.contextmanager
def claim(cid):
    """One review per candidate at a time. The lock dies with the process, so a crash never leaves it held."""
    f = open(_claim_path(cid), 'w')
    if not osdep.lock(f, wait=False):
        f.close()
        yield False
        return
    f.write(cid)
    f.flush()
    try:
        yield True
    finally:
        osdep.unlock(f)
        f.close()


def claim_held(cid):
    return osdep.held(_claim_path(cid))


def running_reviews():
    return sum(1 for p in CLAIMS.glob('*.lock') if osdep.held(p)) if CLAIMS.exists() else 0


# ---------------------------------------------------------------- the rest of Jarvis (the gate works without it)
def usage_ok(probe=True):
    """(may a review start now?, why not). The same lines as Jarvis's own background work: Claude limited, or its
    5-hour window nearly used, means wait. probe=False only reads what is already known (never a call to Claude)."""
    c = conf()
    if not c.plan:
        return False, 'automatic reviews are off with an API key: there is no spending line for them yet (review --id still works)'
    try:
        from jarvis.spine import governor, limits
        u = limits.until_time()
        if u:
            return False, f"Claude usage limit until {u.isoformat(timespec='minutes')}"
        w = governor.window(may_probe=probe) or {}
        if w.get('status') == 'rejected':
            return False, 'Claude usage limit'
        if w.get('status') == 'allowed_warning':
            return False, 'Claude warns the usage window is nearly used'
        five = w.get('five_hour')
        if five is not None and five >= c.usage_max:
            return False, f'5-hour window {five:.0%} used'
        return True, ''
    except Exception as e:                             # the gate must not stop because the rest is being upgraded
        return True, f'usage meter unavailable ({e.__class__.__name__}); running'


def note_usage(logf):
    """What the reviewer's run said about the usage window goes to the meter, so the next decision needs no probe. A
    usage-limit message is recorded with the time it lifts, but only from Claude's own last line: text the reviewer read
    in a repository is never taken for one."""
    try:
        from jarvis.spine import governor, limits
        seen_window = seen_result = False
        for line in reversed(Path(logf).read_text(errors='replace').splitlines()):
            if not line.startswith('{'):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get('type') == 'rate_limit_event' and not seen_window:
                seen_window = True
                governor.note_window(d.get('rate_limit_info'))
            elif d.get('type') == 'result' and not seen_result:
                seen_result = True
                if d.get('is_error'):
                    limits.seen(str(d.get('result') or ''))
            if seen_window and seen_result:
                break
    except Exception:
        pass


def ping(title, body=''):
    """Tell the owner (their phone or desktop). True when it went out. Never to anyone else."""
    try:
        from jarvis import notify
        cfg = notify._settings()
        if not cfg['enabled']:
            return False
        ok, err = notify.ping(title, body)
        if not ok:
            log(f'[notify] not sent: {err}')
        return bool(ok)
    except Exception as e:
        log(f'[notify] failed: {e.__class__.__name__}: {e}')
        return False


def ledger_event(kind, ref, data):
    try:
        from jarvis.spine import ledger
        ledger.event(kind, ref, 'deploy_gate', data)
    except Exception:
        pass


PENDING_PINGS = []        # a look runs inside the record's lock; its pings and events go out only once the record is
PENDING_EVENTS = []       # saved (a look that failed half-way must not ping again on the next run)


def flush_pending():
    while PENDING_EVENTS:
        ledger_event(*PENDING_EVENTS.pop(0))
    while PENDING_PINGS:
        ping(*PENDING_PINGS.pop(0))


# ---------------------------------------------------------------- fetching
NETWORK_ERROR = re.compile(r'Could not resolve host|Failed to connect|Connection (timed out|reset|refused)|Operation timed out|'
                           r'early EOF|RPC failed|SSL_ERROR|returned error: 5\d\d|fetch timed out|Network is unreachable|'
                           r'Temporary failure in name resolution|nodename nor servname', re.I)
FETCH_RETRIES_S = (15, 45)


def ensure_mirror(repo):
    """Make the light mirror of a repository if it is not there yet ('' = it is there). The same copy, in the same place,
    as the repositories server keeps: every branch and commit, file contents fetched when needed."""
    d = gateconf.mirror(repo)
    if d.is_dir():
        return ''
    MIRRORS.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(['git', 'clone', '--quiet', '--mirror', '--filter=blob:none', conf().base + repo + '.git', str(d)],
                           capture_output=True, text=True, timeout=900, env=gateconf.git_env(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        shutil.rmtree(d, ignore_errors=True)
        return 'fetch timed out (the first copy)'
    if r.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        return (_clean(r.stderr).splitlines() or ['could not copy the repository'])[-1][:300]
    return ''


def checked_out_branches(repo):
    """Branches checked out in a working folder of this mirror: git refuses to fetch over them, so they are left out of
    the fetch (their folder owns them) and the rest still updates."""
    r = subprocess.run(['git', '-C', str(gateconf.mirror(repo)), 'worktree', 'list', '--porcelain'], capture_output=True, text=True,
                       env=gateconf.git_env(), stdin=subprocess.DEVNULL)
    return [ln.split(' ', 1)[1] for ln in r.stdout.splitlines() if ln.startswith('branch refs/heads/')]


def _fetch_once(repo):
    try:
        r = subprocess.run(['git', '-C', str(gateconf.mirror(repo)), 'fetch', '-q', '--prune', '--tags', 'origin',
                            '+refs/heads/*:refs/heads/*', *[f'^{b}' for b in checked_out_branches(repo)]],
                           capture_output=True, text=True, timeout=300, env=gateconf.git_env(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 'fetch timed out'
    if r.returncode == 0:
        with contextlib.suppress(OSError):             # the repositories server reads this to know the copy is fresh
            (gateconf.mirror(repo) / 'FETCH_STAMP').write_text(str(time.time()))
        return ''
    return _clean(r.stderr)[:300] or 'fetch failed'


def fetch(repo, once=_fetch_once, retries=FETCH_RETRIES_S, sleep=time.sleep):
    """Fetch one mirror. A network error is tried again after a short wait; any other error is reported at once."""
    err = once(repo)
    for w in retries:
        if not err or not NETWORK_ERROR.search(err):
            break
        sleep(w)
        err = once(repo)
    return err


def fetch_all(only_repo=None):
    """Fetch every watched repository, outside any lock (a fetch can take minutes). {repository: error} for the ones that
    failed; the look goes on with the copy it has."""
    errs = {}
    for repo in conf().repos:
        if only_repo and repo != only_repo:
            continue
        err = ensure_mirror(repo) or fetch(repo)
        if err:
            errs[repo] = err
    return errs


def mark_offline(st, why):
    prev = st.get('offline') or {}
    st['offline'] = {'since': prev.get('since') or now(), 'checked': now(), 'why': str(why)[:200]}


def network_state(st, errs, repos):
    """Split the fetch results into one network state and real repository problems. Every asked repository failing with
    a network error means the network, not the repositories: st['offline'] keeps when it began, and a clean fetch clears
    it. Returns the repository problems to show."""
    errs = dict(errs or {})
    if repos and len(errs) >= len(repos) and all(NETWORK_ERROR.search(str(v)) for v in errs.values()):
        mark_offline(st, next(iter(errs.values())))
        return {}
    st.pop('offline', None)
    if not errs:
        st['fetched_at'] = now()
    return errs


# ---------------------------------------------------------------- looking for candidates
def heads(repo):
    out = git(repo, 'for-each-ref', '--sort=-committerdate',
              '--format=%(refname:short)%09%(objectname)%09%(committerdate:iso-strict)%09%(authorname)', 'refs/heads')
    rows = []
    for line in out.splitlines():
        p = line.split('\t')
        if len(p) == 4:
            rows.append({'name': p[0], 'sha': p[1], 'date': p[2], 'author': p[3]})
    return rows


def tags(repo):
    out = git(repo, 'for-each-ref', '--sort=-creatordate',
              '--format=%(refname:short)%09%(objectname)%09%(*objectname)%09%(creatordate:iso-strict)', 'refs/tags')
    rows = []
    for line in out.splitlines():
        p = line.split('\t')
        if len(p) == 4:
            rows.append({'name': p[0], 'sha': p[2] or p[1], 'date': p[3]})
    return rows


def default_branch(repo):
    return git(repo, 'symbolic-ref', '--short', 'HEAD', check=False)


def previous_tag(repo, t, series, all_tags):
    """The release before this one in the same series, or None: the nearest tag of the series among the commits before
    it (what this release adds is measured from there); when none of them is tagged, the tag made just before it."""
    by_name = {x['name']: x for x in all_tags}
    near = git(repo, 'describe', '--tags', '--abbrev=0', '--match', series, t['sha'] + '^', check=False)
    if near in by_name and near != t['name']:
        return by_name[near]
    older = [x for x in all_tags if x['name'] != t['name'] and fnmatch.fnmatchcase(x['name'], series)
             and (dkey(x['date']), x['name']) < (dkey(t['date']), t['name'])]
    return max(older, key=lambda x: (dkey(x['date']), x['name'])) if older else None


def cand_id(repo, kind, ref, sha):
    return f'{repo}:{kind}:{ref}@{sha[:10]}'


def verdict_path(repo, kind, ref, sha):
    return VERDICTS / repo / f"{kind}-{ref.replace('/', '__')}-{sha[:10]}.md"


def is_mine(c):
    """Is this a change of the owner's own: written by them, or holding commits of theirs? Theirs are reviewed first."""
    cfg = config.load()
    who = [w.lower() for w in cfg.emails() + ([cfg.owner()] if cfg.owner() else []) if w]
    if not who:
        return False
    text = str(c.get('author', ''))
    rng = [f"{c['base']}..{c['sha']}"] if c.get('base') else ['-1', c['sha']]
    text += '\n' + git(c['repo'], 'log', '--no-merges', '--format=%an %ae', *rng, check=False)
    return any(w in text.lower() for w in who)


def tickets_of(c):
    """The owner's ticket keys named in the branch and in the commit messages of the change."""
    rng = [f"{c['base']}..{c['sha']}"] if c.get('base') else ['-1', c['sha']]
    ref = c['ref'].replace('_', ' ').replace('/', ' ')
    if conf().ticket_keys:                               # with the owner's keys known, a branch may write them in small letters
        ref = ref.upper()
    return gateconf.tickets(ref + '\n' + git(c['repo'], 'log', '--format=%s', *rng, check=False))[:20]


_WORST = {'NO-GO': 3, 'NEEDS-INFO': 2, 'GO-WITH-NOTES': 1, 'GO': 0}


def verdict_for(repo, sha, target=None):
    """The verdict that governs pushing this exact commit to `target` (a branch or a tag; none = anywhere), for a
    pre-push hook. The worst one wins among the reviews that judge that target. A candidate being reviewed again has no
    verdict meanwhile."""
    t = str(target or '')
    is_tag = t.startswith('refs/tags/') or bool(t and not t.startswith('refs/heads/') and conf().tag_series(repo, t))
    kinds = {'tag'} if is_tag else ({'deploy', 'branch'} if t else {'tag', 'deploy', 'branch'})
    vs = [c['verdict'] for c in load(STATE, {}).get('candidates', {}).values()
          if c['repo'] == repo and c['sha'].startswith(sha[:40]) and c.get('verdict') and c['kind'] in kinds and c.get('status') != 'running']
    return max(vs, key=lambda v: _WORST.get(v, 0)) if vs else None


def stream_key(c):
    """What a verdict is about over time: one branch, one deploy branch, or one series of release tags."""
    if c['kind'] == 'tag':
        return (c['repo'], 'tag', c.get('series') or c['ref'])
    return (c['repo'], c['kind'], c['ref'])


def _newest(rows, head_of):
    """The stream's current verdict: the candidate at the branch's current head if it was reviewed, else the latest
    commit date, ties broken by review time (reviewing an older commit again never makes it the stream's status)."""
    return max(rows, key=lambda c: (c['kind'] != 'tag' and head_of.get(c['ref']) == c['sha'],
                                    dkey(c.get('date', '')), dkey(c.get('reviewed_at', ''))))


def _supersede(old, new, reason):
    if old.get('verdict') or old.get('last_verdict'):
        old['prior_verdict'] = old.get('verdict') or old.get('last_verdict')
    old['status'], old['superseded_by'], old['superseded_reason'] = 'superseded', new['id'], reason
    if old.get('prior_verdict') and not any(r.get('id') == old['id'] for r in new.get('replaces', [])):
        new.setdefault('replaces', []).append({'id': old['id'], 'sha': old['sha'], 'verdict': old['prior_verdict'],
                                               'reviewed_at': old.get('reviewed_at'), 'reason': reason})


def resolve_older(st):
    """Per stream the newest reviewed commit counts; older reviewed ones give way to it (kept as history: prior_verdict,
    superseded_by with the reason, and replaces[] on the newer one). An older verdict stays while the newer push is
    still waiting, so nothing looks fixed before it was reviewed. A stuck older candidate gives way to any newer push."""
    groups = {}
    for c in st.get('candidates', {}).values():
        if c['status'] != 'superseded':
            groups.setdefault(stream_key(c), []).append(c)
    n = 0
    for key, rows in groups.items():
        head_of = st.get('heads', {}).get(key[0], {})
        done = [c for c in rows if c['status'] == 'done']
        if len(done) >= 2:
            newest = _newest(done, head_of)
            for c in done:
                if c is not newest:
                    _supersede(c, newest, 'a newer push on the same stream was reviewed')
                    n += 1
        live = [c for c in rows if c['status'] in ('pending', 'retry', 'running', 'done')]
        for c in rows:
            if c['status'] == 'stuck':
                newer = [x for x in live if dkey(x.get('date', '')) > dkey(c.get('date', ''))]
                if newer:
                    _supersede(c, max(newer, key=lambda x: dkey(x.get('date', ''))), 'a newer push arrived while this one was stuck')
                    n += 1
    return n


# A merge commit gives a push a new commit id but the same code. When a new candidate's code AND its starting point are
# exactly those of a commit the gate already reviewed (same tree at the head, same tree at the base), the review would be
# the same review: its verdict is carried instead of spending a second one. Never onto a release tag.
CARRY_KINDS = ('branch', 'deploy')


def carry_same_code(c, cands, tree_of):
    """The most recently reviewed candidate whose head tree and base tree equal c's, or None."""
    if c['kind'] not in CARRY_KINDS or not c.get('base'):
        return None
    head_t, base_t = tree_of(c['sha']), tree_of(c['base'])
    if not head_t or not base_t:
        return None
    same = [x for x in cands.values()
            if x is not c and x['repo'] == c['repo'] and x['kind'] in CARRY_KINDS and x.get('verdict')
            and x['status'] in ('done', 'superseded') and x.get('base') and Path(x['verdict_path']).exists()
            and tree_of(x['sha']) == head_t and tree_of(x['base']) == base_t]
    return max(same, key=lambda x: dkey(x.get('reviewed_at') or '')) if same else None


def _carry_if_same_code(c, cands):
    src = carry_same_code(c, cands, lambda s, r=c['repo']: git(r, 'rev-parse', '--verify', '--quiet', f'{s}^{{tree}}', check=False) or None)
    if not src:
        return False
    c.update({'status': 'done', 'verdict': src['verdict'], 'reviewed_at': src.get('reviewed_at'), 'verdict_path': src['verdict_path'],
              'same_code_as': src['id'],
              'attempts': [{'at': now(), 'ts': ts(), 'ok': True, 'verdict': src['verdict'], 'error': None, 'carried_from': src['id']}]})
    PENDING_EVENTS.append(('deploy_gate_verdict', c['id'], {'verdict': src['verdict'], 'same_code_as': src['id']}))
    return True


def stream_latest(st, key, exclude_id=None):
    """The latest reviewed candidate of a stream, or None."""
    done = [c for c in st.get('candidates', {}).values() if c['id'] != exclude_id and stream_key(c) == key and c['status'] == 'done']
    return _newest(done, st.get('heads', {}).get(key[0], {})) if done else None


PRIOR_CAP = 20


def _verdict_text(c):
    """A candidate's accepted verdict text: its verdict file, else a copy set aside with the same Verdict line."""
    want = c.get('verdict') or c.get('last_verdict') or c.get('prior_verdict')
    vp = Path(c['verdict_path'])
    files = [vp] + [vp.with_name(n) for n in reversed(c.get('verdict_files', []))] + sorted(vp.parent.glob(vp.stem + '.done*.md'), reverse=True)
    for p in files:
        if p.exists():
            t = p.read_text(errors='replace')
            m = re.search(r'^Verdict:\s*([A-Z-]+)', t, re.M)
            if m and (not want or m.group(1) == want):
                return t
    return ''


def attach_prior(c, prior_c, reason):
    """Carry an earlier review's findings into c['prior'], so the next review must show each one fixed (the P rows).
    Findings the earlier review itself had carried and not seen fixed come along, each with where it was first found."""
    text = _verdict_text(prior_c)
    found = parse_findings(text)
    if not found:                                     # a review that could not run: carry its open intent items
        found = [{'n': r['id'], 'title': _cell_safe(f"intent {r['id']} was not met: {r['what']}", 120),
                  'text': _cell_safe(f"{r['what']} / evidence then: {r['evidence']}", 500)}
                 for r in parse_intent_rows(text) if r['id'].startswith('I-') and r['head'] in ('FAIL', 'NOT VERIFIED')]
    items = [{'title': r['title'], 'text': r.get('text', ''), 'origin': r['origin']}
             for r in (prior_c.get('prior_results') or {}).get('rows', []) if r.get('head') in ('FAIL', 'NOT VERIFIED')]
    items += [{'title': f['title'], 'text': f['text'], 'origin': {'id': prior_c['id'], 'sha': prior_c['sha'], 'n': f['n']}} for f in found]
    seen, uniq = set(), []
    for it in items:
        k = (it['origin']['id'], str(it['origin']['n']))
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    dropped = max(0, len(uniq) - PRIOR_CAP)
    uniq = uniq[:PRIOR_CAP]
    for i, it in enumerate(uniq, 1):
        it['p'] = f'P-{i}'
    c['prior'] = {'id': prior_c['id'], 'sha': prior_c['sha'],
                  'verdict': prior_c.get('verdict') or prior_c.get('last_verdict') or prior_c.get('prior_verdict'),
                  'verdict_file': Path(prior_c['verdict_path']).name, 'attached_at': now(), 'reason': reason, 'findings': uniq,
                  'dropped': dropped}
    return len(uniq)


def scan(days=None, only_repo=None, st=None, fetch_errors=None):
    """Update st['candidates'] from the mirrors and return the record. The caller fetched first (fetch_all) and passes
    its errors. With st given, the caller owns the save."""
    c = conf()
    days = days or c.days
    own = st is None
    if own:
        st = load(STATE, {})
    st.setdefault('candidates', {})
    st.setdefault('seen', {})
    st.setdefault('heads', {})
    asked = [r for r in c.repos if only_repo in (None, r)]
    st['problems'] = network_state(st, fetch_errors, asked)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    found = []
    for repo in asked:
        if not gateconf.mirror(repo).is_dir():
            st['problems'].setdefault(repo, 'no copy of this repository yet')
            continue
        hs = heads(repo)
        head_of = {h['name']: h['sha'] for h in hs}
        st['heads'][repo] = head_of
        default = default_branch(repo)
        devs = c.deploy_branches(repo, set(head_of), default)
        if not devs:
            st['problems'].setdefault(repo, 'none of its deploy branches exists (settings: [gate] deploy_branches)')
        seen = st['seen'].setdefault(repo, {'branch_heads': {}, 'tags': []})
        protected = set(devs) | {default, 'HEAD'}

        for x in list(st['candidates'].values()):          # a branch that was merged or deleted is history now
            if x['repo'] != repo or x['kind'] != 'branch' or x['status'] in ('superseded', 'running'):
                continue
            into = None if x['ref'] not in head_of else next((d for d in devs if git_ok(repo, 'merge-base', '--is-ancestor', x['sha'], head_of[d])), None)
            why = None
            if x['ref'] not in head_of:
                why = 'the branch was deleted on the code host' + ('' if x.get('verdict') or x.get('last_verdict') else ' before it was reviewed')
            elif into:
                why = f'merged into {into}: the review of that push judges this code now'
            elif x['status'] in ('pending', 'retry') and c.ignored(repo, x['ref']):
                why = 'not queued: this branch is left out by the settings, or is a backup or scratch copy'
            if why:
                x['prior_verdict'] = x.get('verdict') or x.get('last_verdict')
                x['status'], x['superseded_by'], x['superseded_reason'] = 'superseded', None, why

        for h in hs:                                       # 1. feature branches with commits no deploy branch has
            if h['name'] in protected or not devs or c.ignored(repo, h['name']) or pdate(h['date']) < cutoff:
                continue
            aheads = {d: count(repo, f"{head_of[d]}..{h['sha']}") for d in devs}
            if min(aheads.values()) == 0:
                continue                                   # already in a deploy branch
            dev = min(devs, key=lambda d: (aheads[d], devs.index(d)))     # the deploy branch it grew from
            base = git(repo, 'merge-base', head_of[dev], h['sha'], check=False) or head_of[dev]
            found.append({'kind': 'branch', 'repo': repo, 'ref': h['name'], 'sha': h['sha'], 'base': base,
                          'base_label': f'where it left {dev}', 'commits_ahead': aheads[dev], 'author': h['author'], 'date': h['date'],
                          'target': dev, 'stage': f'feature branch, about to be merged into {dev}'})

        for b in devs:                                     # 2. a deploy branch moved
            h = next(x for x in hs if x['name'] == b)
            prev = seen['branch_heads'].get(b)
            seen['branch_heads'][b] = h['sha']
            if prev is None or prev == h['sha']:           # the first look is the baseline: history is not reviewed again
                continue
            found.append({'kind': 'deploy', 'repo': repo, 'ref': b, 'sha': h['sha'], 'base': prev,
                          'base_label': 'the head of the branch at the last look', 'commits_ahead': count(repo, f"{prev}..{h['sha']}"),
                          'author': h['author'], 'date': h['date'], 'target': b, 'stage': f'pushed to {b}, a deploy branch'})

        all_tags = tags(repo)                              # 3. release tags
        first_tags = not seen['tags']
        for t in all_tags:
            series = c.tag_series(repo, t['name'])
            if t['name'] in seen['tags'] or not series:
                continue
            seen['tags'].append(t['name'])
            if first_tags and pdate(t['date']) < cutoff:
                continue                                   # the releases that were there before the gate began
            before = previous_tag(repo, t, series, all_tags)
            found.append({'kind': 'tag', 'repo': repo, 'ref': t['name'], 'sha': t['sha'], 'series': series,
                          'base': before['sha'] if before else None,
                          'base_label': f"{before['name']}, the release before it in the same series" if before else 'no earlier release in the same series',
                          'author': '', 'date': t['date'], 'target': t['name'], 'stage': 'release tag'})

    cands = st['candidates']
    for cnd in found:
        cid = cand_id(cnd['repo'], cnd['kind'], cnd['ref'], cnd['sha'])
        if cid in cands:
            continue
        for old in cands.values():                         # a newer commit replaces the older one that was still waiting
            if old['repo'] == cnd['repo'] and old['kind'] == cnd['kind'] == 'branch' and old['ref'] == cnd['ref'] \
                    and old['status'] in ('pending', 'retry'):
                old['status'], old['superseded_by'] = 'superseded', cid
                old['superseded_reason'] = 'a newer push on the same branch arrived before this one was reviewed'
        cnd['mine'] = is_mine(cnd)
        cnd['tickets'] = tickets_of(cnd)
        cnd.update({'id': cid, 'status': 'pending', 'first_seen': now(), 'attempts': [],
                    'verdict_path': str(verdict_path(cnd['repo'], cnd['kind'], cnd['ref'], cnd['sha']))})
        cands[cid] = cnd
        pc = stream_latest(st, stream_key(cnd), exclude_id=cid)      # a fix must show each earlier finding fixed
        if pc and pc.get('verdict') in BLOCKING:
            attach_prior(cnd, pc, f"an earlier push on the same stream was {pc['verdict']}")
        else:                                                         # same code as a reviewed commit: same verdict
            _carry_if_same_code(cnd, cands)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open('a') as f:
            f.write(json.dumps({'seen_at': cnd['first_seen'], **{k: cnd.get(k) for k in
                                ('id', 'repo', 'kind', 'ref', 'sha', 'stage', 'author', 'date', 'commits_ahead', 'tickets')}}) + '\n')
        PENDING_EVENTS.append(('deploy_gate_seen', cid, {'stage': cnd['stage'], 'author': cnd['author']}))
        if cnd['kind'] == 'tag':
            PENDING_PINGS.append(('Deploy gate: release tag', f"{cnd['repo']} {cnd['ref']}: review queued"))
    # a waiting twin (another branch at the same commit) takes the verdict once its twin is reviewed; a candidate that
    # carries earlier findings never does: it has to show them fixed
    for x in list(cands.values()):
        if x['status'] == 'pending' and not x.get('prior') and x['repo'] in asked:
            _carry_if_same_code(x, cands)
    # only the newest waiting push per deploy branch is worth a review: it holds the older ones
    pend = [x for x in cands.values() if x['status'] in ('pending', 'retry') and x['kind'] == 'deploy']
    for x in pend:
        newer = [y for y in pend if y is not x and y['repo'] == x['repo'] and y['ref'] == x['ref'] and dkey(y.get('date', '')) > dkey(x.get('date', ''))]
        if newer:
            x['status'], x['superseded_by'] = 'superseded', max(newer, key=lambda y: dkey(y.get('date', '')))['id']
            x['superseded_reason'] = 'a newer push on the same branch arrived before this one was reviewed'
    for x in cands.values():                               # a review whose process died: nobody holds it any more
        if x['status'] == 'running' and not claim_held(x['id']) and ts() - x.get('started_ts', 0) > 120:
            _attempt_failed(x, {'at': now(), 'ts': ts(), 'ok': False, 'error': 'the review stopped before it finished'})
            if x['status'] == 'stuck':
                PENDING_PINGS.append(('Deploy gate: stuck', f"{x['repo']} {x['ref']}: the review stopped before it finished, every time"))
    resolve_older(st)
    st['scanned_at'] = now()
    if own:
        save(STATE, st)
        flush_pending()
    return st


# ---------------------------------------------------------------- reading and checking a verdict
# One reader for verdict files, used by the checker, by carry-forward and by the screen. A Head cell is read for its
# result word with emphasis marks taken off (a bold FAIL is a FAIL), and tables are split on bars that are not escaped
# (an evidence cell may hold a pattern with \| in it).
_EMPH = re.compile(r'[*_`]+')
_EMPH_TEXT = re.compile(r'\*+|`+|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])')      # for a finding's own words: a_name keeps its underscore
RESULTS = ('NOT VERIFIED', 'PASS', 'FAIL', 'NOTED', 'N/A')
_SEVERITY = {'FAIL': 4, 'NOT VERIFIED': 3, 'NOTED': 2, 'N/A': 1, 'PASS': 0}
WEIGHT = re.compile(r'^(blocking|note|low|info|minor|high|medium|critical)\b[^.!?]{0,100}:$', re.I)


def _cells(row):
    parts = re.split(r'(?<!\\)\|', row.strip())
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [p.strip() for p in parts]


def _section(text, start, ends):
    if start not in text:
        return ''
    body = text.split(start, 1)[1]
    hits = [i for i in (body.find(e) for e in ends) if i >= 0]
    return body[: min(hits)] if hits else body


def head_result(cell):
    """The result in a Head cell: result words in capitals count (reasons are written in lower case); several in one
    cell give the worst; nothing recognisable gives NOT VERIFIED."""
    c = _EMPH.sub('', cell or '').strip()
    found = re.findall(r'(?<![A-Za-z])(NOT VERIFIED|PASS|FAIL|NOTED|N/A)(?![A-Za-z])', c)
    if found:
        return max(found, key=lambda r: _SEVERITY[r])
    up = c.upper()
    for r in RESULTS:
        if up.startswith(r):
            return r
    return 'NOT VERIFIED'


def parse_intent_rows(text):
    """The I-n and P-n rows of the Intent checklist: [{id, what, base, head, head_raw, evidence}]."""
    rows = []
    for line in _section(text, '## Intent checklist', ('## Checks',)).splitlines():
        if not line.lstrip().startswith('|'):
            continue
        cells = _cells(line)
        rid = _EMPH.sub('', cells[0]).strip() if cells else ''
        if not re.fullmatch(r'[IP]-\d+', rid):
            continue
        head_raw = cells[3] if len(cells) > 3 else ''
        rows.append({'id': rid, 'what': cells[1] if len(cells) > 1 else '', 'base': cells[2] if len(cells) > 2 else '',
                     'head_raw': head_raw, 'head': head_result(head_raw) if head_raw.strip() else 'NOT VERIFIED',
                     'evidence': cells[4] if len(cells) > 4 else ''})
    return rows


def _cell_safe(s, n):
    s = re.sub(r'\s+', ' ', (s or '').replace('|', '/')).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + '…'


def parse_findings(text):
    """The numbered items under ## Findings: [{n, title, text}]. The title is the bold lead, else the first sentence;
    when the bold lead is only the finding's weight ("Blocking:"), the title is the sentence after it."""
    body = _section(text, '## Findings', ('## Before it goes further', '\n## '))
    items, cur = [], None
    for line in body.splitlines():
        m = re.match(r'^(\d+)\.\s+(.*)', line)
        if m:
            cur = {'n': int(m.group(1)), 'lines': [m.group(2)]}
            items.append(cur)
        elif cur is not None and line.strip():
            cur['lines'].append(line.strip())
    out = []
    for it in items:
        first = it['lines'][0]
        b = re.match(r'\*\*(.+?)\*\*[\s:]*(.*)', first)
        title = b.group(1) if b else re.split(r'(?<=[.!?])\s', first, maxsplit=1)[0]
        if b and b.group(2).strip() and WEIGHT.match(b.group(1).strip()):
            title = re.split(r'(?<=[.!?])\s', b.group(2).strip(), maxsplit=1)[0]
        title = _EMPH_TEXT.sub('', title).strip().rstrip('.:')
        out.append({'n': it['n'], 'title': _cell_safe(title, 120), 'text': _cell_safe(_EMPH_TEXT.sub('', ' '.join(it['lines'])), 500)})
    return out


def check_verdict(path, ui=False, prior_ids=None):
    """(ok, verdict, why not). A verdict file counts only if it is complete and agrees with itself. With prior_ids every
    earlier finding must have exactly one P row, and its result limits the verdict."""
    path = Path(path)
    if not path.exists():
        return False, '', 'no verdict file written'
    text = path.read_text(errors='replace')
    m = re.search(r'^Verdict:\s*([A-Z-]+)', text, re.M)
    if not m or m.group(1) not in VERDICTS_OK:
        return False, '', "missing or invalid 'Verdict:' line"
    v = m.group(1)
    missing = [s for s in REQUIRED_SECTIONS if s not in text]
    if missing:
        return False, v, f"missing sections: {', '.join(missing)}"
    rows = parse_intent_rows(text)
    iheads = [('NOT VERIFIED' if r['head'] == 'N/A' else r['head']) for r in rows if r['id'].startswith('I-')]
    if not iheads:
        return False, v, 'intent checklist has no I-n items'
    if 'FAIL' in iheads and v != 'NO-GO':
        return False, v, 'an intent item FAILS on head but the verdict is not NO-GO'
    if 'NOT VERIFIED' in iheads and v in PASSED:
        return False, v, 'GO with an intent item NOT VERIFIED on head'
    if 'NOTED' in iheads and v == 'GO':
        return False, v, 'an intent item is NOTED, so the verdict can be at most GO-WITH-NOTES'
    if prior_ids:
        prows = [r for r in rows if r['id'].startswith('P-')]
        ids = [r['id'] for r in prows]
        miss = [p for p in prior_ids if p not in ids]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        unknown = sorted(set(ids) - set(prior_ids))
        if miss or dup or unknown:
            return False, v, f"P rows: missing {miss or '-'}, repeated {dup or '-'}, unknown {unknown or '-'}"
        ph = [r['head'] for r in prows]
        if 'FAIL' in ph and v != 'NO-GO':
            return False, v, 'an earlier finding still FAILS (P row) but the verdict is not NO-GO'
        if 'NOT VERIFIED' in ph and v in PASSED:
            return False, v, 'GO with an earlier finding NOT VERIFIED (P row)'
        if 'NOTED' in ph and v == 'GO':
            return False, v, 'an earlier finding is NOTED, so the verdict can be at most GO-WITH-NOTES'
    checks = _section(text, '## Checks', ('## Findings',))
    crow = [c for c in (_cells(r) for r in checks.splitlines() if r.lstrip().startswith('|')) if c][1:]
    crow = [c for c in crow if not set(''.join(c)) <= set('-: ')]
    if len(crow) < 6:
        return False, v, f'checks table has {len(crow)} rows (need at least 6)'

    def res(c):
        return _EMPH.sub('', c[1] if len(c) > 1 else '').strip().upper()
    if ui and v != 'NO-GO':                            # a change to a screen must have been run
        for label in ('Runs locally', 'Behaviour vs intent'):
            row = next((c for c in crow if _EMPH.sub('', c[0]).strip().startswith(label)), None)
            if not row or res(row).startswith('NOT RUN'):
                return False, v, f'a change to a screen was not run locally ({label}: NOT RUN)'
    if v in PASSED:
        bad = [c for c in crow if re.match(r'FAIL\b', res(c))]
        if bad:
            return False, v, 'GO verdict with a FAIL row: ' + ' | '.join(bad[0])[:120]
    return True, v, ''


# ---------------------------------------------------------------- one review
PROMPT = """Review this change for the deploy gate.

candidate: {id}
repository: {repo}
kind: {kind}   ref: {ref}   commit: {sha}
stage: {stage}
compare against: {base} ({base_label})
author: {author}   date: {date}
ticket keys named in the branch and the commit messages: {tickets}
worktree (checked out at the commit; yours to build and test in): {wt}
base worktree (checked out at the base, for before and after): {base_wt}
verdict folder (your evidence files go here): {vdir}   file-name stem: {stem}
containers: {docker}
stress tool: {python} {stress}
time limit for the whole review: {minutes} minutes

Write your verdict file to: {verdict_path}
This is a headless run: the review ends the moment you end your turn, and anything still running in the background is
stopped with it. Never end a turn to wait for something. Run installs, builds and tests in the foreground (a Bash
timeout of up to 600000 ms; split a longer job into steps that each fit), and end your turn only once the verdict file
is written.
{prior_note}{retry_note}{prep_note}"""

PRIOR_NOTE = """
THIS CHANGE FOLLOWS AN EARLIER {verdict} REVIEW ({prior_id}, commit {prior_short}). Show, on this commit, whether each of
its findings is fixed. In the Intent checklist, after your I rows, add exactly one row per finding below, in this shape
(same table, same columns):
| P-n | <the finding's title, as given below> | {prior_short}: FAIL | <RESULT> <reason in lower case> | <evidence> |
RESULT is one word in capitals: PASS (shown fixed on this commit), FAIL (still there and blocking), NOTED (still there
but not a defect of this change; say why), N/A (cannot apply to this change; say why), NOT VERIFIED (could not be
checked; say why). Write no other result word in capitals in that cell. The harness enforces: every P row below present
exactly once and none added; any P FAIL means the verdict is NO-GO; any P NOT VERIFIED rules out GO and GO-WITH-NOTES;
any P NOTED allows at most GO-WITH-NOTES.
Findings to check again:
{items}
"""

PREP_NOTE = """
THE HARNESS HAS ALREADY RUN THE OWNER'S BUILD AND TEST COMMANDS, in these same worktrees, at these same commits. Every
command's full output is in the verdict folder under the log name given; the whole record is {prep_json}.
{summary}
Treat these as your own run of those commands: in the Checks table give the command, its exit code and, for a failure,
the failing end of its log. Do not run again a step that is listed with exit 0. Everything else in your steps is still
yours:
- a step listed with another exit code: read its log, run it again yourself in the foreground with
  `{again}`, and judge it;
- a failure on the head that also fails on the base is noted as already there, not blamed on this change: the base
  line above is a run of the same command on the base, made now;
- any check that is not in the list you run yourself.
"""

RESUME_PROMPT = ('You ended your turn before writing the verdict file, which in this headless run ends the review and stops '
                 'anything you left running in the background. Carry on from where you were: run again in the foreground '
                 'whatever was cut off, finish the review, and write the verdict file to {verdict_path} before you end your turn.')


def free_gb(p):
    return shutil.disk_usage(p).free / 1e9


def add_worktree(repo, sha, suffix):
    wt = WORKTREES / repo / f'{sha[:10]}{suffix}'
    if wt.exists():
        remove_worktree(repo, wt)
    wt.parent.mkdir(parents=True, exist_ok=True)
    for i in range(5):                                 # two reviews may add worktrees to one mirror at the same moment
        try:
            git(repo, 'worktree', 'add', '--force', '--detach', str(wt), sha, timeout=900)
            return wt
        except RuntimeError as e:
            if 'lock' not in str(e).lower() or i == 4:
                raise
            time.sleep(3 + i * 3)
    return wt


def remove_worktree(repo, wt):
    git(repo, 'worktree', 'remove', '--force', str(wt), check=False)
    shutil.rmtree(wt, ignore_errors=True)
    git(repo, 'worktree', 'prune', check=False)


def docker_alive():
    try:
        return subprocess.run(['docker', 'info'], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def docker_cleanup(only=None, sha=None):
    """Remove what a review left in Docker: its compose project, its containers and the images it built. Everything a
    review starts is named after its own project (dg-...), so nothing else is touched."""
    try:
        r = subprocess.run(['docker', 'compose', 'ls', '-a', '--format', 'json'], capture_output=True, text=True, timeout=60)
        projects = [p['Name'] for p in json.loads(r.stdout or '[]')]
    except Exception:
        return
    for p in projects:
        if (only and p == only) or (not only and p.startswith('dg-')):
            subprocess.run(['docker', 'compose', '-p', p, 'down', '-v', '--remove-orphans'], capture_output=True, timeout=300)
    with contextlib.suppress(Exception):
        r = subprocess.run(['docker', 'ps', '-aq', '--filter', f"name={only or 'dg-'}"], capture_output=True, text=True, timeout=60)
        if r.stdout.split():
            subprocess.run(['docker', 'rm', '-f', *r.stdout.split()], capture_output=True, timeout=120)
    with contextlib.suppress(Exception):
        r = subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], capture_output=True, text=True, timeout=60)
        prefixes = tuple(p for p in ((only, f'dg-{sha[:8]}' if sha else None) if only else ('dg-',)) if p)
        imgs = [i for i in r.stdout.split() if i.startswith(prefixes)]
        if imgs:
            subprocess.run(['docker', 'rmi', '-f', *imgs], capture_output=True, timeout=300)


def guard_settings():
    """The workers' own guard (no sends, no credential stores, the stop switch) as a settings file for the reviewer's
    run. Written from the settings when it is not there yet (the server writes it at every start)."""
    p = paths.state('worker-settings.json')
    if not p.exists():
        from jarvis.spine import dispatcher
        save(p, dispatcher.worker_settings())
    return str(p)


def review_env(c, project):
    """The reviewer's environment: the guard switched on, and git unable to push anywhere, whatever is typed."""
    env = dict(os.environ, **MCP_ENV, JARVIS_PROC='1', JARVIS_LANE='gate',
               JARVIS_TASK_ID='gate-' + hashlib.sha1(c['id'].encode()).hexdigest()[:12],
               COMPOSE_PROJECT_NAME=project, DG_LOCAL_ONLY='1', HUSKY='0', HUSKY_SKIP_INSTALL='1', GIT_TERMINAL_PROMPT='0')
    return gateconf.no_push(env, conf().base)


def _session_id(logf):
    """The Claude session id a review run printed (its output lines carry it)."""
    for line in reversed(Path(logf).read_text(errors='replace').splitlines()):
        m = re.search(r'"session_id":\s*"([0-9a-f-]{36})"', line)
        if m:
            return m.group(1)
    return ''


def _prep():
    t = str(gateconf.tools())
    if t not in sys.path:
        sys.path.insert(0, t)
    import prep
    return prep


def run_review(c, timeout, prior_ids=None):
    """One try. Returns (ok, verdict, error). prior_ids: the P rows this review must carry (decided by the caller from
    the copy it started with, so findings attached while the review runs are never demanded of it)."""
    cf = conf()
    repo, sha = c['repo'], c['sha']
    tag = hashlib.sha1(c['id'].encode()).hexdigest()[:4]
    project = f'dg-{sha[:6]}{tag}'
    vp = Path(c['verdict_path'])
    t_start = time.monotonic()
    timing = {'id': c['id'], 'started': now()}
    vp.parent.mkdir(parents=True, exist_ok=True)
    if vp.exists():                                    # keep the failed try's file for the record
        vp.rename(vp.with_name(f"{vp.stem}.attempt{len(c['attempts'])}.md"))
    LOGS.mkdir(parents=True, exist_ok=True)
    import reviewer
    said = reviewer.install()
    if AGENT not in said:
        raise RuntimeError("environment: the reviewer's instructions could not be installed")
    exe = paths.claude_bin()
    if not exe:
        raise RuntimeError('environment: Claude Code is not installed on this computer')
    wt = add_worktree(repo, sha, f'-{tag}')
    base_wt = None
    base_sha = git(repo, 'rev-parse', '--verify', '--quiet', f"{c['base']}^{{commit}}", check=False) if c.get('base') else ''
    if re.fullmatch(r'[0-9a-f]{40}', base_sha or ''):
        try:
            base_wt = add_worktree(repo, base_sha, f'-{tag}-base')
        except Exception:
            base_wt = None
    prior_note = retry_note = prep_note = ''
    if prior_ids:
        pr = c['prior']
        prior_note = PRIOR_NOTE.format(
            verdict=pr.get('verdict'), prior_id=pr['id'], prior_short=pr['sha'][:8],
            items='\n'.join(f"{f['p']}: {f['title']}\n      {f['text']}\n      (first found in {f['origin']['id']}, finding {f['origin']['n']})"
                            for f in pr['findings']))
    if c['attempts']:
        retry_note = ('\nThis is try %d. Earlier tries failed with: %s. Avoid that failure; if a step cannot run, mark it '
                      'NOT RUN and still write a complete verdict file.\n' %
                      (len(c['attempts']) + 1, '; '.join(str(a.get('error') or '') for a in c['attempts'])[:600]))
    timing['worktrees_s'] = round(time.monotonic() - t_start, 1)
    python = paths.python()
    if cf.command('build', repo) or cf.command('test', repo):
        # the owner's build and test commands, head and base, before the reviewer starts. A fault here never blocks the
        # review: the reviewer then runs the steps itself.
        t_prep = time.monotonic()
        try:
            prep = _prep()
            rec = prep.run(repo, wt, sha, vp.parent, vp.stem, base_wt=base_wt, base_sha=base_sha if base_wt else None, log=log)
            prep_note = PREP_NOTE.format(prep_json=vp.with_name(vp.stem + '.prep.json'), summary=prep.summary(rec),
                                         again=f"{python} {gateconf.tools() / 'prep.py'} again --repo {repo} --step <build|test> --wt <worktree>")
        except Exception as e:
            log(f"[prep] {c['id']}: {e.__class__.__name__}: {e}"[:300])
            timing['prep_error'] = f'{e.__class__.__name__}: {e}'[:200]
        timing['prep_s'] = round(time.monotonic() - t_prep, 1)
    prompt = PROMPT.format(wt=wt, base_wt=base_wt or 'absent', vdir=vp.parent, stem=vp.stem,
                           docker=(f'allowed, local and throwaway only; the compose project name you must use is {project}'
                                   if cf.docker else 'not available in this setup: mark what needs one NOT RUN'),
                           python=python, stress=gateconf.tools() / 'stress.py', minutes=max(1, timeout // 60), retry_note=retry_note,
                           prior_note=prior_note,
                           prep_note=prep_note, tickets=', '.join(c.get('tickets') or []) or 'none found',
                           **{k: c.get(k) for k in ('id', 'repo', 'kind', 'ref', 'sha', 'stage', 'base', 'base_label', 'author', 'date',
                                                    'verdict_path')})
    allowed, denied = tool_rules(cf, python)
    cmd = [exe, '-p', prompt, '--agent', AGENT, '--model', config.model('work'), '--permission-mode', 'acceptEdits',
           '--output-format', 'stream-json', '--verbose', '--settings', guard_settings()]
    for d in [wt, vp.parent] + ([base_wt] if base_wt else []):      # never the mirror: every ref is readable from the worktree
        cmd += ['--add-dir', str(d)]
    cmd += ['--allowedTools', *allowed, '--disallowedTools', *denied]
    env = review_env(c, project)
    logf = LOGS / f"{vp.stem}.a{len(c['attempts']) + 1}.jsonl"
    err = ''
    log(f"[review] {c['id']} try {len(c['attempts']) + 1} -> {vp.name}")
    deadline = time.monotonic() + timeout             # the clock that does not count the computer's sleep

    def run_claude(args):
        with logf.open('a') as f:
            p = osdep.spawn(args, cwd=str(wt), stdout=f, stderr=subprocess.STDOUT, env=env, stdin=subprocess.DEVNULL)
            osdep.keep_awake(p.pid)
            try:
                rc = p.wait(timeout=max(1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                rc = 124
            osdep.stop(p, grace=10 if rc == 124 else 3)    # the reviewer at its limit, and whatever a finished one left running
            return rc

    try:
        logf.write_text('')
        t_agent = time.monotonic()
        rc = run_claude(cmd)
        # A reviewer that ends its turn "to wait for the install" ends a headless run. The same session is taken up
        # again, which keeps what it already read, instead of paying for a new try.
        for _ in range(RESUMES):
            sid = _session_id(logf)
            if rc != 0 or vp.exists() or not sid or deadline - time.monotonic() < 300:
                break
            log(f"[resume] {c['id']}: the reviewer ended its turn without a verdict file; resuming {sid[:8]}")
            rc = run_claude([exe, '-p', RESUME_PROMPT.format(verdict_path=vp), '--resume', sid, *cmd[3:]])
        note_usage(logf)
        if rc == 124:
            err = f'timed out after {timeout // 60} min'
        elif rc != 0:
            tail = logf.read_text(errors='replace')[-3000:]
            err = 'Claude usage/rate limit' if re.search(r'usage limit|rate.limit|\b429\b|overloaded', tail, re.I) \
                else 'environment: the computer slept, or the service did not answer' if re.search(r'went to sleep|API Error|api_error|ECONNRESET', tail) \
                else f'the reviewer exited {rc}'
        timing['agent_s'] = round(time.monotonic() - t_agent, 1)
        ok, verdict, why = check_verdict(vp, ui=cf.ui(repo), prior_ids=prior_ids)
        if not ok:
            err = '; '.join(x for x in (err, why) if x)
        timing.update(ok=ok, verdict=verdict, error=err)
        return ok, verdict, err
    finally:
        t_clean = time.monotonic()
        if cf.docker:
            docker_cleanup(project, sha)
        remove_worktree(repo, wt)
        if base_wt:
            remove_worktree(repo, base_wt)
        timing['cleanup_s'] = round(time.monotonic() - t_clean, 1)
        timing['total_s'] = round(time.monotonic() - t_start, 1)
        with contextlib.suppress(Exception):
            save(vp.with_name(vp.stem + '.timing.json'), timing)


def counted(c):
    return [a for a in c['attempts'] if not a.get('not_counted')]


def due(c):
    """Is this candidate due for a review now? A new one at once; a failed one after its wait."""
    if c['status'] == 'pending':
        return True
    if c['status'] != 'retry':
        return False
    last = c['attempts'][-1].get('ts', 0) if c['attempts'] else 0
    return ts() - last >= BACKOFF_S[min(len(counted(c)), len(BACKOFF_S) - 1)]


def _prior_results(c, vp):
    """Per earlier finding, the result this review gave it (from the accepted verdict's P rows)."""
    prows = {r['id']: r for r in parse_intent_rows(Path(vp).read_text(errors='replace')) if r['id'].startswith('P-')}
    rows = []
    for f in c['prior']['findings']:
        r = prows.get(f['p'], {})
        rows.append({'id': f['p'], 'head': r.get('head', 'NOT VERIFIED'), 'reason': _cell_safe(r.get('head_raw', ''), 200),
                     'title': f['title'], 'text': f['text'], 'origin': f['origin']})
    cnt = {k: sum(1 for r in rows if r['head'] == v) for k, v in
           (('pass', 'PASS'), ('noted', 'NOTED'), ('na', 'N/A'), ('fail', 'FAIL'), ('not_verified', 'NOT VERIFIED'))}
    return {**cnt, 'rows': rows, 'prior_id': c['prior']['id'], 'prior_sha': c['prior']['sha'], 'prior_verdict': c['prior'].get('verdict')}


def _attempt_failed(c, att):
    """Record a failed try: tried again until the limit of counted tries, then stuck. A second review that ends stuck
    gives back the earlier accepted verdict, file included, instead of leaving the commit with none."""
    c.setdefault('attempts', []).append(att)
    if c['status'] == 'superseded':
        return
    c['status'] = 'retry' if len(counted(c)) < conf().max_attempts else 'stuck'
    if c['status'] == 'stuck' and c.get('last_verdict'):
        vp = Path(c['verdict_path'])
        done = [vp.with_name(n) for n in c.get('verdict_files', []) if vp.with_name(n).exists()]
        if done and not vp.exists():
            done[-1].rename(vp)
            c['verdict_files'] = c['verdict_files'][:-1]
        c['verdict'] = c.pop('last_verdict')
        c['status'] = 'done'
        c['rereview_stuck'] = now()


def review_one(cid, timeout):
    """One review of one candidate. Safe to run beside reviews of other candidates."""
    with claim(cid) as got:
        if not got:
            log(f'[skip] {cid} is already being reviewed')
            return 'busy'
        with state_txn() as st:
            c = st['candidates'][cid]
            if c.get('verdict'):                       # a second review: the old verdict stops counting while it runs
                c['last_verdict'] = c.pop('verdict')
            c['status'], c['started_ts'] = 'running', ts()
            c = json.loads(json.dumps(c))              # the review works on a copy; the record is read again to update it
        prior_ids = [f['p'] for f in (c.get('prior') or {}).get('findings', [])] or None
        vp = Path(c['verdict_path'])
        set_aside = None
        try:
            if vp.exists() and c.get('last_verdict'):  # an accepted verdict is kept as .doneN, never as a rejected .attemptN
                k = len(list(vp.parent.glob(vp.stem + '.done*.md'))) + 1
                set_aside = vp.with_name(f'{vp.stem}.done{k}.md')
                vp.rename(set_aside)
        except OSError as e:
            log(f'[warn] could not set the earlier verdict aside: {e}')
        try:
            if conf().docker and not docker_alive():
                raise RuntimeError('environment: Docker is not running, so nothing can be started locally')
            ok, verdict, err = run_review(c, timeout, prior_ids)
        except Exception as e:
            msg = str(e)
            ok, verdict, err = False, '', (msg if msg.startswith('environment:') else f'{e.__class__.__name__}: {msg}')[:300]
        att = {'at': now(), 'ts': ts(), 'ok': ok, 'verdict': verdict, 'error': err}
        if not ok and ('usage/rate limit' in err or err.startswith('environment:')):
            att['not_counted'] = True                  # a limit is not the change's fault: it does not use up a try
        presults = None
        if ok and prior_ids:
            try:
                presults = _prior_results(c, vp)
            except Exception as e:
                log(f'[warn] could not read the P rows: {e}')
        if not ok and vp.exists():                     # a rejected verdict never stays at verdict_path
            with contextlib.suppress(OSError):
                vp.rename(vp.with_name(f"{vp.stem}.attempt{len(c.get('attempts', [])) + 1}.md"))
        with state_txn() as st:
            c = st['candidates'][cid]
            if set_aside:
                c.setdefault('verdict_files', []).append(set_aside.name)
            was_superseded = c['status'] == 'superseded'       # a newer push arrived while it ran
            if ok:
                c.setdefault('attempts', []).append(att)
                c['verdict'], c['reviewed_at'] = verdict, now()
                c.pop('last_verdict', None)
                if presults is not None:
                    c['prior_results'] = presults
                    # "fixed" means at least one finding shown fixed, none failing or unchecked, and none dropped by
                    # the limit; findings that are only NOTED or N/A are acknowledged, not fixed
                    c['fixed_prior'] = (presults['pass'] > 0 and presults['fail'] == 0 and presults['not_verified'] == 0
                                        and not (c.get('prior') or {}).get('dropped'))
                    c['prior_fixed_count'] = presults['pass']
                if not was_superseded:
                    c['status'] = 'done'
                try:
                    resolve_older(st)
                except Exception as e:                 # never lose the verdict over the bookkeeping of streams
                    log(f'[warn] resolve_older failed: {e}')
            else:
                _attempt_failed(c, att)
            final = json.loads(json.dumps(c))
        if ok and final['status'] == 'done':           # no ping for a verdict a newer push already replaced
            ledger_event('deploy_gate_verdict', cid, {'verdict': verdict})
            if verdict in BLOCKING or final['kind'] == 'tag' or (final.get('mine') and verdict != 'GO'):
                ping(f'Deploy gate: {verdict}', f"{final['repo']} {final['ref']}: {vp.name}")
            pr = final.get('prior') or {}
            if final.get('fixed_prior') and verdict in PASSED and pr.get('verdict') in BLOCKING and not final.get('fixed_pinged_at'):
                r = final['prior_results']
                ping('Deploy gate: fixed', f"{final['repo']} {final['ref']} @{final['sha'][:8]} (was {pr['verdict']} @{pr['sha'][:8]}): "
                                           f"{r['pass']} fixed, {r['noted']} noted, {r['na']} n/a")
                ledger_event('deploy_gate_fixed', cid, {'prior': pr['id'], 'pass': r['pass'], 'noted': r['noted']})
                with state_txn() as st:
                    st['candidates'][cid]['fixed_pinged_at'] = now()
        elif not ok and final['status'] == 'stuck':
            ping('Deploy gate: stuck', f"{final['repo']} {final['ref']}: {err[:160]}")
        return final['status']


# ---------------------------------------------------------------- the report
ORDER = {'NO-GO': 0, 'NEEDS-INFO': 1, 'stuck': 2, 'GO-WITH-NOTES': 3, 'running': 4, 'retry': 5, 'pending': 6, 'GO': 7}


def report(st):
    """report.md: every change that still counts, the ones that need the owner first."""
    cands = [c for c in st.get('candidates', {}).values() if c['status'] != 'superseded']
    cands.sort(key=lambda c: dkey(c.get('date', '')), reverse=True)
    cands.sort(key=lambda c: ORDER.get(c.get('verdict') if c['status'] == 'done' else c['status'], 9))
    lines = ['# Deploy gate', '', f"Updated {now()}. Last look {st.get('scanned_at', '-')}.", '']
    if st.get('offline'):
        lines += [f"**The code host has not answered since {str(st['offline'].get('since'))[:16]}** ({st['offline'].get('why')}): "
                  f"using the copy fetched {str(st.get('fetched_at') or 'earlier')[:16]}; the next run tries again.", '']
    if st.get('problems'):
        lines += ['**Repositories with a problem (using the last copy):** ' + '; '.join(f'{k}: {str(v)[:80]}' for k, v in st['problems'].items()), '']
    lines += ['| State | Repository | Kind | Ref | Author | Stage | Verdict file |', '|---|---|---|---|---|---|---|']
    for c in cands:
        state = c.get('verdict') if c['status'] == 'done' else c['status']
        if c['status'] in ('retry', 'stuck') and c['attempts']:
            state += f" ({str(c['attempts'][-1].get('error') or '')[:60]})"
        lines.append(f"| {state} | {c['repo']} | {c['kind']} | `{c['ref']}` @{c['sha'][:8]} | {c.get('author', '')} | {c['stage']} | "
                     f"{Path(c['verdict_path']).name} |")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT_MD.with_name(f'report.md.{os.getpid()}.tmp')
    tmp.write_text('\n'.join(lines) + '\n')
    tmp.replace(REPORT_MD)


# ---------------------------------------------------------------- commands
def _todo(st):
    """What to review next, in order: the owner's own changes, then tags, deploy pushes, branches; newest first. One
    review per commit and kind: a twin at the same commit waits and then takes the verdict."""
    todo = [c for c in st.get('candidates', {}).values() if due(c) and not claim_held(c['id'])]
    todo.sort(key=lambda c: dkey(c.get('date', '')), reverse=True)
    todo.sort(key=lambda c: KIND_ORDER.get(c['kind'], 9))
    todo.sort(key=lambda c: not c.get('mine'))
    busy = {(c['repo'], c['kind'], c['sha']) for c in st.get('candidates', {}).values() if c.get('status') == 'running' or claim_held(c['id'])}
    out = []
    for c in todo:
        k = (c['repo'], c['kind'], c['sha'])
        if k not in busy:
            busy.add(k)
            out.append(c)
    return out


def _stop_early():
    """The first words of every command: where the gate cannot run, or has nothing to watch, it says so and stops."""
    why = osdep.why_not() or conf().ready()
    for p in conf().problems:
        log(f'[settings] {p}')
    return why


def _wait(procs, seconds):
    """Wait up to `seconds`, less when one of the reviews ends first."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not any(p.poll() is not None for p in procs):
        time.sleep(0.5)


def cmd_run(a):
    """The scheduler: look, then keep up to `parallel` reviews running (each its own process) until nothing is due, the
    run's limit of reviews was started, usage or the disk says wait, or the pause switch is set."""
    why = _stop_early()
    if why:
        log(why)
        return 0
    if MAINTENANCE.exists():
        log('the maintenance switch is on (' + MAINTENANCE.read_text().strip()[:80] + '); run does nothing')
        return 0
    cf = conf()
    with single_runner() as mine:
        if not mine:
            log('another gate scheduler is active; exiting')
            return 0
        health = {'at': now(), 'reviewed': 0, 'parallel': cf.parallel, 'state': 'starting'}
        save(HEALTH, health)
        try:
            if running_reviews() == 0:                 # leftovers of a crash, never a running review's files
                if WORKTREES.exists():
                    shutil.rmtree(WORKTREES, ignore_errors=True)
                for repo in cf.repos:
                    if gateconf.mirror(repo).is_dir():
                        git(repo, 'worktree', 'prune', check=False)
                if cf.docker and docker_alive():
                    docker_cleanup()

            def refresh():
                errs = fetch_all()                     # outside the record's lock
                with state_txn() as st:
                    scan(a.days, st=st, fetch_errors=errs)
                    off = st.get('offline')
                flush_pending()
                return off

            off = refresh()
            if off:                                    # no network: nothing new to read, and a review could not install or ask
                health.update(state='offline', waiting=f"the code host does not answer ({off.get('why')}); the next run tries again", ok=True)
                log(f"[offline] {off.get('why')}")
                report(load(STATE, {}))
                return 0
            next_refresh = ts() + REFRESH_S
            started, procs = 0, []
            health['state'] = 'reviewing'
            save(HEALTH, health)
            while True:
                procs = [p for p in procs if p.poll() is None]
                if PAUSE.exists():
                    health['waiting'] = 'paused (' + PAUSE.read_text().strip()[:80] + ')'
                    break
                todo = _todo(load(STATE, {'candidates': {}}))
                health['queued'] = len(todo)
                if not todo and not procs:
                    break
                if todo and started < a.limit and running_reviews() < cf.parallel:
                    ok, why = usage_ok()
                    if not ok:
                        health['waiting'] = why
                        log(f'[wait] {why}')
                        if not procs:
                            break
                    elif free_gb(ROOT) < cf.min_free_gb:
                        health['waiting'] = f'free disk {free_gb(ROOT):.1f} GB, under the {cf.min_free_gb:g} GB the settings ask for'
                        if not health.get('disk_pinged'):
                            ping('Deploy gate paused', health['waiting'])
                            health['disk_pinged'] = True
                        if not procs:
                            break
                    else:
                        c = todo[0]
                        log(f"[start] {c['id']} ({running_reviews() + 1}/{cf.parallel} running)")
                        LOGS.mkdir(parents=True, exist_ok=True)
                        p = osdep.spawn([paths.python(), str(Path(__file__).resolve()), 'review', '--id', c['id'], '--timeout', str(a.timeout)],
                                        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=open(LOGS / 'workers.out', 'a'), stderr=subprocess.STDOUT)
                        procs.append(p)
                        started += 1
                        health['reviewed'] = started
                        save(HEALTH, health)
                        end = time.monotonic() + 20    # let it claim the candidate before the next pick
                        while time.monotonic() < end and p.poll() is None and not claim_held(c['id']):
                            time.sleep(0.2)
                        continue
                elif started >= a.limit and not procs:
                    if todo:
                        health['waiting'] = f'this run started its limit of {a.limit} reviews; {len(todo)} left for the next run'
                    break
                if ts() >= next_refresh:               # a long run looks again, so a new push is not hours late
                    next_refresh = ts() + REFRESH_S
                    refresh()
                _wait(procs, POLL_S)
            try:                                       # every open finding checked again on the code as it is now
                import recheck
                recheck.recheck()
            except Exception as e:
                log(f'[recheck] failed: {e.__class__.__name__}: {str(e)[:150]}')
            report(load(STATE, {}))
            health['ok'] = True
            if health.get('state') == 'reviewing':
                health['state'] = 'idle'
        except Exception as e:
            health.update(ok=False, error=f'{e.__class__.__name__}: {e}'[:400])
            log(f"[error] {health['error']}")
            ledger_event('deploy_gate_error', '', {'error': health['error']})
        finally:
            health['ended'] = now()
            save(HEALTH, health)
        return 0 if health.get('ok') else 1


def cmd_review(a):
    """Review one candidate now (the owner's press, or a worker the scheduler started). No scheduler lock: it may run
    beside others."""
    why = _stop_early()
    if why:
        log(why)
        return 1
    if not a.id:
        log('usage: gate.py review --id <candidate id>')
        return 2
    st = load(STATE, {})
    if a.id not in st.get('candidates', {}):
        with single_runner() as mine:
            if mine:
                errs = fetch_all()
                with state_txn() as st2:
                    scan(a.days, st=st2, fetch_errors=errs)
                flush_pending()
        st = load(STATE, {})
    if a.id not in st.get('candidates', {}):
        log(f'unknown candidate {a.id}')
        return 1
    status = review_one(a.id, a.timeout)
    st = load(STATE, {})
    report(st)
    c = st['candidates'][a.id]
    log(f"{c['status']} {c.get('verdict', '')} {c['verdict_path']}")
    return 0 if status in ('done', 'busy') else 1


def cmd_scan(a):
    why = _stop_early()
    if why:
        print(why)
        return 0
    with single_runner() as mine:
        if not mine:
            log('another gate run is active; exiting')
            return 1
        errs = fetch_all(a.repo)
        with state_txn() as st:
            scan(a.days, a.repo, st=st, fetch_errors=errs)
        flush_pending()
        report(st)
    for c in sorted(st['candidates'].values(), key=lambda c: dkey(c.get('date', '')), reverse=True):
        if c['status'] != 'superseded':
            print(f"{(c.get('verdict') or c['status']):<14} {c['kind']:<6} {c['repo']:<24} {c['ref']:<46} "
                  f"{str(c.get('commits_ahead') or ''):>4}  {c.get('author', '')}")
    if st.get('offline'):
        print('the code host does not answer:', st['offline'].get('why'))
    if st.get('problems'):
        print('repository problems:', st['problems'])
    return 0


def cmd_status(a):
    why = osdep.why_not() or conf().ready()
    if why:
        print(why)
    st = load(STATE, {})
    for c in st.get('candidates', {}).values():
        if c['status'] != 'superseded':
            print(f"{(c.get('verdict') or c['status']):<14} {c['id']}")
    print('health:', json.dumps(load(HEALTH, {})))
    return 0


def cmd_verdict_for(a):
    if len(a.args) < 2:
        print('usage: gate.py verdict-for <repository> <commit> [<target branch or tag>]')
        return 2
    print(verdict_for(a.args[0], a.args[1], a.args[2] if len(a.args) > 2 else None) or 'none')
    return 0


def _vtext(verdict, irows, prows='', findings='1. **x** y'):
    return ('# Deploy gate: t\nVerdict: ' + verdict + '\n## What is being pushed\n- a\n## Intent checklist\n'
            '| # | What | Base | Head | Evidence |\n|---|---|---|---|---|\n' + irows + prows + '## Checks\n'
            '| Check | Result | Evidence |\n|---|---|---|\n' + ''.join(f'| c{i} | PASS | e |\n' for i in range(7))
            + '## Findings\n' + findings + '\n## Before it goes further\nx\n## Not covered\nnone\n')


def cmd_selftest(a):
    """Offline checks of the harness itself: what makes a verdict count, the stress tool's limits, how tries are
    counted, the reviewer's instructions for these settings, and the re-check's own cases. No Claude usage, no network."""
    fails = []
    if osdep.why_not():
        print(osdep.why_not())
        return 1
    ok_i = '| I-1 | sorted | FAIL | PASS | head.png |\n'
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        def chk(text, **kw):
            (d / 'v.md').write_text(text)
            return check_verdict(d / 'v.md', **kw)[0]
        cases = [
            (True, 'a complete NO-GO', _vtext('NO-GO', ok_i), {}),
            (True, 'a clean GO', _vtext('GO', ok_i), {}),
            (False, 'GO with a FAIL row in Checks', _vtext('GO', ok_i).replace('| c0 | PASS', '| c0 | FAIL'), {}),
            (False, 'a verdict missing a section', _vtext('GO', ok_i).replace('## Not covered', '## Gone'), {}),
            (False, 'GO with an intent item NOT VERIFIED', _vtext('GO', '| I-1 | x | FAIL | NOT VERIFIED | - |\n'), {}),
            (False, 'GO-WITH-NOTES with an intent item failing', _vtext('GO-WITH-NOTES', '| I-1 | x | FAIL | **FAIL** | e |\n'), {}),
            (False, 'an empty intent checklist', _vtext('GO', ''), {}),
            (False, 'a screen change that was never run', _vtext('NEEDS-INFO', '| I-1 | x | y | NOT VERIFIED | - |\n')
             .replace('| c0 | PASS', '| Runs locally | NOT RUN').replace('| c1 | PASS', '| Behaviour vs intent | NOT RUN'), {'ui': True}),
            (True, 'the screen rule only where it applies', _vtext('NEEDS-INFO', '| I-1 | x | y | NOT VERIFIED | - |\n')
             .replace('| c0 | PASS', '| Runs locally | NOT RUN'), {}),
            (False, 'a missing P row', _vtext('GO', ok_i, '| P-1 | f | s: FAIL | PASS | e |\n'), {'prior_ids': ['P-1', 'P-2']}),
            (True, 'every P row shown fixed', _vtext('GO', ok_i, '| P-1 | f | s: FAIL | PASS | e |\n'), {'prior_ids': ['P-1']}),
            (False, 'a P row still failing under GO-WITH-NOTES', _vtext('GO-WITH-NOTES', ok_i, '| P-1 | f | s: FAIL | FAIL still there | e |\n'), {'prior_ids': ['P-1']}),
        ]
        for want, what, text, kw in cases:
            if chk(text, **kw) != want:
                fails.append(f"the checker {'rejects' if want else 'accepts'} {what}")
        if check_verdict(d / 'missing.md')[0]:
            fails.append('the checker accepts a missing file')
        stress = str(gateconf.tools() / 'stress.py')
        r = subprocess.run([paths.python(), stress, '--url', 'http://example.com/'], capture_output=True, text=True)
        if 'refusing non-local' not in (r.stdout + r.stderr):
            fails.append('the stress tool did not refuse a host that is not this computer')
        try:
            fails += _selftest_stress(stress, d)
        except Exception as e:
            fails.append(f'the stress tool run failed: {e.__class__.__name__}: {e}'[:200])
    if len(counted({'attempts': [{'ts': ts(), 'not_counted': True}]})) != 0:
        fails.append('a try lost to a usage limit is counted')
    env = review_env({'id': 'x'}, 'dg-x')
    if NO_PUSH not in [env.get(f'GIT_CONFIG_VALUE_{i}') for i in range(int(env['GIT_CONFIG_COUNT']))]:
        fails.append('the reviewer could push: its environment does not send pushes nowhere')
    allowed, denied = tool_rules(conf(), 'python3')
    if 'Bash(git push:*)' not in denied or any(x.startswith('Bash(git push') for x in allowed):
        fails.append('git push is not refused to the reviewer')
    try:
        import reviewer
        text = reviewer.render()
        if '{{' in text or '[[if' in text or f'name: {AGENT}' not in text:
            fails.append("the reviewer's instructions are not filled in")
    except Exception as e:
        fails.append(f"the reviewer's instructions could not be made: {e.__class__.__name__}: {e}"[:200])
    try:
        import recheck
        fails += [f're-check: {x}' for x in recheck.selftest()]
    except Exception as e:
        fails.append(f're-check selftest crashed: {e.__class__.__name__}: {str(e)[:120]}')
    ok, why = usage_ok(probe=False)
    print(f"settings: {conf().ready() or 'ready, watching ' + ', '.join(conf().repos)}")
    print(f"usage gate: {'open' if ok else 'closed'} {why}")
    print('selftest:', 'PASS' if not fails else 'FAIL')
    for f in fails:
        print('  -', f)
    return 0 if not fails else 1


def _selftest_stress(stress, d):
    """One short run of the stress tool against a page served from this process, on a port the system picks."""
    import http.server
    import threading

    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *x):
            pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = subprocess.run([paths.python(), stress, '--url', f'http://127.0.0.1:{srv.server_address[1]}/', '--steps', '1,4', '--seconds', '1',
                            '--json', str(d / 's.json')], capture_output=True, text=True, timeout=60)
        res = load(d / 's.json', {})
        if r.returncode or not res.get('steps') or res['steps'][-1]['error_rate'] > 0:
            return [f'the stress tool run failed: {r.stdout[-200:]}{r.stderr[-200:]}']
        return []
    finally:
        srv.shutdown()
        srv.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['run', 'scan', 'review', 'status', 'selftest', 'verdict-for'])
    ap.add_argument('args', nargs='*')
    ap.add_argument('--days', type=int, default=None, help='how many days back a feature branch counts (default: settings)')
    ap.add_argument('--repo', help='scan: one repository only')
    ap.add_argument('--id', help='review: the candidate')
    ap.add_argument('--limit', type=int, default=None, help='run: reviews started in this run (default: settings)')
    ap.add_argument('--timeout', type=int, default=None, help='seconds one review may take (default: settings)')
    a = ap.parse_args(argv)
    if osdep.why_not():
        print(osdep.why_not())
        return 0 if a.cmd == 'run' else 1
    cf = conf()
    a.days = a.days or cf.days
    a.limit = a.limit or cf.max_reviews
    a.timeout = a.timeout or cf.review_s
    return {'run': cmd_run, 'scan': cmd_scan, 'review': cmd_review, 'status': cmd_status, 'selftest': cmd_selftest,
            'verdict-for': cmd_verdict_for}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
