#!/usr/bin/env python3
"""The reviewer's instructions, filled in for the owner and put where Claude Code finds its agents.

The instructions ship as a template (gate/reviewer.md, the same {{value}} and [[if x]] ... [[end]] marks as the skills):
who the owner is, whether a tracker is connected, whether containers may be used and which repositories have screens
all come from settings. install() writes them to Claude Code's agents folder as "jarvis-deploy-gate", and writes them
again when the settings or the template changed. A copy the owner edited by hand is left alone and said so, exactly as
jarvis/skillset.py does for the skills (this file uses its marker and its checks; the reviewer is an agent, not a
skill, and belongs to an optional part, so it is installed from here and only when the gate is used).

    python3 gate/reviewer.py            install or refresh, and say what happened
    python3 gate/reviewer.py --check    only say what would happen
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import gateconf  # noqa: E402
from gateconf import paths  # noqa: E402
from jarvis import skillset, texts  # noqa: E402

NAME = 'jarvis-deploy-gate'


def source():
    return paths.repo('gate', 'reviewer.md')


def target():
    return paths.claude_dir() / 'agents' / (NAME + '.md')


def values(conf=None):
    """What the template is filled with: the skills' own values, and what the gate's settings say."""
    conf = conf or gateconf.load()
    vals = skillset.values()
    vals.update(tracker=conf.tracker, no_tracker=not conf.tracker, docker=conf.docker, no_docker=not conf.docker,
                ui=bool(conf.ui_repos), ui_repos=', '.join(conf.ui_repos), stress=str(gateconf.tools() / 'stress.py'))
    return vals


def render(vals=None):
    """The reviewer's instructions for this owner."""
    return texts.render_text(source().read_text(encoding='utf-8'), NAME, **(vals or values()))


def install(check=False):
    """{name: 'installed' | 'updated' | 'same' | 'kept: you edited it'}. Nothing is written with check=True."""
    text = render()
    if not text.endswith('\n'):
        text += '\n'
    full = text + skillset.MARK.format(skillset._sha(text)) + '\n'
    dst = target()
    state, cur = skillset._state(dst)
    if state == 'edited':
        return {NAME: 'kept: you edited it'}
    if cur == full:
        return {NAME: 'same'}
    if not check:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix('.tmp')
        tmp.write_text(full, encoding='utf-8')
        os.replace(tmp, dst)
    return {NAME: 'installed' if state == 'missing' else 'updated'}


if __name__ == '__main__':
    print(json.dumps(install(check='--check' in sys.argv), indent=1))
