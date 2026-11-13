#!/usr/bin/env python3
"""The ship gate. A change Jarvis makes to its own code ships only when this passes.

Always run from the MAIN copy's tools (these files are protected from the optimiser), against a candidate copy of the code:
    python tools/gate.py --repo <worktree> [--live] [--case <case id>] [--quick] [--json]
With no --repo it checks the copy it sits in.

Checks, cheapest first; it stops at the first failing group:
  compile     every Python file of the candidate's package and tools parses
  tests       the MAIN copy's test suite (tests/), run against the candidate's code: the candidate cannot weaken its judges
  regression  tools/jarvis_eval.py code cases (no previously passing case may fail), when this copy has that tool
  grounding   tools/grounding_eval.py (false alarms <= 2%), when this copy has that tool
  live        (--live) tools/jarvis_eval.py --live: the live safety cases, each pass^3
  case        (--case) the ticket's own regression case (tools/autopilot_cases.py), pass^3; with --quick (the fix worker's
              own check) once, capped at 7 minutes, and a timeout there is not a failure (the server's full gate decides)
Output: {"ok": bool, "failed": [group, ...], "detail": {group: tail of its output}}. Exit 0 only when ok.
"""
import glob, json, os, subprocess, sys, time

MAIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # the copy this gate belongs to: its tests and tools judge
PY = sys.executable or 'python3'                                         # every step on the interpreter that runs Jarvis
T = os.path.join(MAIN, 'tools')


def run(group, cmd, env, timeout, cwd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        out = (p.stdout or '') + (p.stderr or '')
        return p.returncode == 0, out[-1500:]
    except subprocess.TimeoutExpired:
        return False, f'{group}: timed out after {timeout}s'


def main(repo, live=False, case=None, quick=False):
    # the candidate's code is what gets imported and tested; the tests and tools come from the main copy
    env = dict(os.environ, JARVIS_TEST_REPO=repo, PYTHONPATH=repo, PYTHONDONTWRITEBYTECODE='1')
    detail, failed, bad = {}, [], []
    for f in glob.glob(os.path.join(repo, 'jarvis', '**', '*.py'), recursive=True) + glob.glob(os.path.join(repo, 'tools', '*.py')):
        try:
            compile(open(f, encoding='utf-8').read(), f, 'exec')
        except SyntaxError as e:
            bad.append(f'{os.path.relpath(f, repo)}:{e.lineno} {e.msg}')
    if bad:
        return {'ok': False, 'failed': ['compile'], 'detail': {'compile': '; '.join(bad)[:1500]}}
    steps = [('tests', [PY, '-W', 'ignore', '-m', 'unittest', 'discover', '-s', os.path.join(MAIN, 'tests')], 1200)]
    for group, tool, limit in (('regression', 'jarvis_eval.py', 600), ('grounding', 'grounding_eval.py', 300)):
        if os.path.exists(os.path.join(T, tool)):
            steps.append((group, [PY, os.path.join(T, tool)], limit))
    if live and os.path.exists(os.path.join(T, 'jarvis_eval.py')):
        steps.append(('live', [PY, os.path.join(T, 'jarvis_eval.py'), '--live'], 2400))
    if case:
        # quick (the fix worker's own check, which must finish inside its command limit): the case once, capped;
        # full (the server, before anything ships): pass^3 with room for slow turns
        steps.append(('case', [PY, os.path.join(T, 'autopilot_cases.py'), '--run', case, '--n', '1' if quick else '3'], 420 if quick else 3600))
    for group, cmd, timeout in steps:
        t0 = time.time()
        ok, out = run(group, cmd, env, timeout, repo)        # run inside the candidate: its package is the one imported
        detail[group] = f'[{round(time.time() - t0)} s] ' + out
        if not ok and quick and group == 'case' and 'timed out' in out:
            detail[group] += ' (quick: a slow live turn is not a failure here; the server\'s full gate runs the case pass^3)'
            continue
        if not ok:
            failed.append(group)
            break
    return {'ok': not failed, 'failed': failed, 'detail': detail}


if __name__ == '__main__':
    a = sys.argv
    repo = a[a.index('--repo') + 1] if '--repo' in a else MAIN
    r = main(os.path.abspath(os.path.expanduser(repo)), '--live' in a, a[a.index('--case') + 1] if '--case' in a else None, '--quick' in a)
    if '--json' in a:
        print(json.dumps(r))
    else:
        print('PASS' if r['ok'] else 'FAIL ' + ', '.join(r['failed']))
        for g in r['failed']:
            print(f'--- {g}\n{r["detail"].get(g, "")}')
    sys.exit(0 if r['ok'] else 1)
