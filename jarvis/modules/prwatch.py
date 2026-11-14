"""The pull-request watch as a part the owner switches on (settings: modules.prwatch, and sources.code_workspace for the
workspace on the code host): every 30 minutes, in its own process, what changed since the last look becomes a round-up."""
import os
import subprocess

from jarvis import paths
from jarvis import procs


def _run():
    log = open(str(paths.logs('prwatch.log')), 'a')
    return subprocess.Popen([paths.python(), str(paths.tool('prwatch.py'))], cwd=str(paths.home()),
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **procs.session_kw())


def status(q=None):
    """GET /api/prwatch: is a workspace set, when the last look was, how many pull requests it saw, the last error."""
    import json
    from jarvis import config
    try:
        st = json.load(open(os.path.join(str(paths.home()), 'prwatch', 'state.json')))
    except (OSError, ValueError):
        st = {}
    return {'workspace': str(config.load().get('sources.code_workspace') or ''), 'at': st.get('at'), 'counts': st.get('counts') or {},
            'error': st.get('error') or ''}


GET = {'/api/prwatch': status}


def jobs():
    return [('prwatch', 1800, _run)]
