#!/usr/bin/env python3
"""The first learning pass, started from the Setup screen ("Start learning").

Three steps, each the same tool the tests run: the owner's Claude Code chats are read into units (extract_sessions.py),
each unit becomes a short note (kb_build.py sessions), and the knowledge base is built from the notes (kb_build.py kb).
Every step carries on from where an earlier pass stopped, so nothing is read or paid for twice. The tools keep their own
guards (they wait when the usage window is nearly used, and stop at the weekly line).

Where it stands is written to state/learn_first.json for the screen: the step, when it started and ended, and whether it
finished. Its output goes to logs/learn_first.log.

    learn_first.py            run the pass
"""
import datetime as dt, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths  # noqa: E402

STATE = str(paths.state('learn_first.json'))
STEPS = [('reading your chats', ['extract_sessions.py', '--rich']),
         ('writing a note for each chat', ['kb_build.py', 'sessions']),
         ('building the knowledge base', ['kb_build.py', 'kb'])]


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def put(**kw):
    try:
        st = json.load(open(STATE, encoding='utf-8'))
    except (OSError, ValueError):
        st = {}
    st.update(kw)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1)
    os.replace(STATE + '.tmp', STATE)


def main():
    paths.ensure()
    put(pid=os.getpid(), started=now(), ended=None, ok=None, error='', step=STEPS[0][0])
    for label, cmd in STEPS:
        put(step=label)
        print(f'{now()} {label}: {" ".join(cmd)}', flush=True)
        r = subprocess.run([paths.python(), str(paths.tool(cmd[0]))] + cmd[1:], cwd=str(paths.home()), stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            put(ended=now(), ok=False, error=f'"{label}" stopped (exit {r.returncode}). The log says why: logs/learn_first.log. Press Start learning to carry on.')
            return 1
    put(ended=now(), ok=True, step='')
    print(f'{now()} the first learning pass is finished', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
