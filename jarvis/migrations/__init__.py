"""Carrying an owner's data from one version of Jarvis to the next.

A step is a module in this folder named m<4 digits>_<what>.py with a function run(home) that changes the data home from
the layout of the version before to its own. Steps run in order, each once; which ones have run is kept in the data home
(state/migrations.json). A step must be safe to run on data that is already in the new layout, and it never deletes: what
it replaces goes to the trash folder. The server runs the pending steps when it starts, before anything reads the data.

There is no step yet: this is the first layout.
"""
import importlib
import json
import os
import pkgutil
import re

from .. import paths


def steps():
    """[(number, module name)] of the steps in this folder, in order."""
    out = []
    for m in pkgutil.iter_modules(__path__):
        g = re.fullmatch(r'm(\d{4})_[a-z0-9_]+', m.name)
        if g:
            out.append((int(g.group(1)), m.name))
    return sorted(out)


def _state():
    return str(paths.state('migrations.json'))


def done():
    try:
        d = json.load(open(_state(), encoding='utf-8'))
        return sorted(int(x) for x in d.get('done') or [])
    except (OSError, ValueError, TypeError):
        return []


def run(all_steps=None, load=None):
    """Run the steps that have not run in this data home. Returns the numbers that ran. A step that fails stops the run
    and is not recorded, so it is tried again at the next start; the error is raised for the caller to show."""
    had, ran = set(done()), []
    for n, name in (all_steps if all_steps is not None else steps()):
        if n in had:
            continue
        mod = (load or (lambda nm: importlib.import_module(f'{__name__}.{nm}')))(name)
        mod.run(str(paths.home()))
        had.add(n); ran.append(n)
        os.makedirs(os.path.dirname(_state()), exist_ok=True)
        with open(_state() + '.tmp', 'w', encoding='utf-8') as f:
            json.dump({'done': sorted(had)}, f)
        os.replace(_state() + '.tmp', _state())
    return ran
