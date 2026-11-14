#!/usr/bin/env python3
"""The owner's build and test commands, run by the harness before the reviewer starts.

Running an install, a build and a test suite needs no judgement, and a reviewer that runs them itself spends its own
turns waiting. So the harness runs the commands the owner named in settings ([gate] build and test), on the head and on
the base, one after the other, records every command with its exit code, how long it took and its whole output next to
the verdict, and hands the reviewer the results. Nothing is invented: a repository with no command named gets no step.

The head is always run now. So is the base: a failure on the head that also fails on the base is seen by a run made in
the same review, never taken from an earlier one.

A private login (a package registry's token) comes from the environment file the owner named ([gate] env_file, a file of
NAME=value lines). Its values are given to these commands only, are never part of the reviewer's own environment or
prompt, and are blanked out of every log and line printed here. A reviewer that must run a step again does it through
`again`, which reads the file itself.

  prep.py run --repo R --head-wt DIR --head-sha SHA [--base-wt DIR --base-sha SHA] --out DIR --stem STEM
  prep.py again --repo R --step build|test --wt DIR      one step again, in a worktree of the gate
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

GATE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GATE not in sys.path:
    sys.path.insert(0, GATE)
import gateconf  # noqa: E402
import osdep  # noqa: E402

VERSION = 1
STEP_TIMEOUT_S = 20 * 60
STEPS = ('build', 'test')
MIN_SECRET = 6                                       # a shorter value is not blanked: it would blank ordinary text


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def env_values(path):
    """{NAME: value} from a file of NAME=value lines ('#' starts a comment, a leading "export " and quotes around the
    value are dropped). The values are never printed or logged by this file."""
    out = {}
    if not path:
        return out
    try:
        lines = Path(path).read_text(errors='replace').splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        k, _, v = line.partition('=')
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
            v = v[1:-1]
        if k.replace('_', 'a').isalnum() and not k[0].isdigit():
            out[k] = v
    return out


def blank(text, secrets):
    for s in secrets:
        if len(s) >= MIN_SECRET:
            text = text.replace(s, '***')
    return text


def command_env(conf, values):
    """What a build or test command runs with: this process's environment without Jarvis's own switches, the owner's
    values, and git unable to push."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_')}
    env.update(values)
    env.update(DG_LOCAL_ONLY='1', HUSKY='0', HUSKY_SKIP_INSTALL='1', GIT_TERMINAL_PROMPT='0')
    return gateconf.no_push(env, conf.base)


def run_cmd(cmd, cwd, log_path, env, secrets=(), timeout=STEP_TIMEOUT_S, echo=None):
    """(exit code, seconds). The command runs in the system's shell, in its own process group; its whole output goes to
    log_path with the secret values blanked. 124 = stopped at the time limit, with everything it started."""
    t0 = time.time()
    hit = []
    p = osdep.spawn(cmd, shell=True, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    env=env, text=True, errors='replace')

    def stop():
        hit.append(1)
        osdep.stop(p.pid, grace=3)                   # by its id: the reader below is the one that waits for it
    timer = threading.Timer(timeout, stop)
    timer.daemon = True
    timer.start()
    try:
        with open(log_path, 'w') as f:
            for line in p.stdout:
                line = blank(line, secrets)
                f.write(line)
                if echo:
                    echo(line)
        rc = p.wait()
    finally:
        timer.cancel()
    return (124 if hit else rc), round(time.time() - t0, 1)


def steps_for(conf, repo):
    """The steps the owner named for this repository, in order."""
    return [{'name': n, 'cmd': conf.command(n, repo)} for n in STEPS if conf.command(n, repo)]


def run(repo, head_wt, head_sha, out_dir, stem, base_wt=None, base_sha=None, log=print, runner=run_cmd, conf=None):
    """Run the owner's commands on the head (and on the base). Never raises for a failing step: every outcome is in the
    returned record, which is also written to <out_dir>/<stem>.prep.json."""
    t0 = time.time()
    conf = conf or gateconf.load()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    values = env_values(conf.env_file)
    secrets = [v for v in values.values() if v]
    env = command_env(conf, values)
    sides = {'head': {'wt': str(head_wt), 'sha': head_sha}}
    if base_wt and base_sha:
        sides['base'] = {'wt': str(base_wt), 'sha': base_sha}
    rec = {'version': VERSION, 'at': now(), 'repo': repo, 'stem': stem, 'notes': [], 'env_file': bool(values)}
    for side, s in sides.items():
        s['steps'] = []
        for st in steps_for(conf, repo):
            st = dict(st, cmd=blank(st['cmd'], secrets))
            if any(x['rc'] != 0 for x in s['steps']):
                rec['notes'].append(f"{side}: {st['name']} was not run, because the step before it failed")
                continue
            logp = out_dir / f"{stem}.{st['name']}.{side}.log"
            rc, secs = runner(conf.command(st['name'], repo), s['wt'], logp, env, secrets)
            st.update(rc=rc, seconds=secs, log=logp.name)
            s['steps'].append(st)
            log(f"[prep] {side} {st['name']}: exit {rc}, {secs} s")
    rec.update(sides)
    rec['seconds'] = round(time.time() - t0, 1)
    (out_dir / f'{stem}.prep.json').write_text(json.dumps(rec, indent=1))
    return rec


def summary(rec):
    """The lines the reviewer is given."""
    lines = []
    for side in ('head', 'base'):
        s = rec.get(side)
        if not s:
            continue
        lines.append(f"{side} @ {s['sha'][:10]}")
        for st in s['steps']:
            lines.append(f"  {st['name']:<6} `{st['cmd']}`  exit {st['rc']}{' (stopped at the time limit)' if st['rc'] == 124 else ''}  "
                         f"run now, {st['seconds']} s  log {st['log']}")
        if not s['steps']:
            lines.append('  (no step was run)')
    lines += [f'note: {n}' for n in rec.get('notes', [])]
    return '\n'.join(lines)


def again(repo, step, wt, conf=None, out=sys.stdout):
    """Run one step again in a worktree of the gate and print its output (secret values blanked). The exit code is the
    command's own. Only inside the gate's own worktrees: the owner's values go nowhere else."""
    conf = conf or gateconf.load()
    cmd = conf.command(step, repo) if step in STEPS else ''
    if not cmd:
        out.write(f'no {step} command is named for {repo} in settings\n')
        return 2
    root = os.path.realpath(str(gateconf.root() / 'wt'))
    if not os.path.realpath(str(wt)).startswith(root + os.sep) or not os.path.isdir(str(wt)):
        out.write('that folder is not a worktree of the deploy gate\n')
        return 2
    values = env_values(conf.env_file)
    logp = Path(wt) / f'.dg-again-{step}.log'
    rc, secs = run_cmd(cmd, wt, logp, command_env(conf, values), [v for v in values.values() if v], echo=out.write)
    out.write(f'[{step}] exit {rc} after {secs} s\n')
    return rc


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['run', 'again'])
    for o in ('--repo', '--head-wt', '--head-sha', '--base-wt', '--base-sha', '--out', '--stem', '--step', '--wt'):
        ap.add_argument(o)
    a = ap.parse_args()
    if osdep.why_not():
        print(osdep.why_not())
        sys.exit(1)
    if a.cmd == 'again':
        sys.exit(again(a.repo, a.step, a.wt))
    r = run(a.repo, a.head_wt, a.head_sha, a.out, a.stem, base_wt=a.base_wt, base_sha=a.base_sha)
    print(summary(r))
    print(f"prep took {r['seconds']} s")
