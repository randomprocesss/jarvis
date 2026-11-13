"""The work vault as a part the owner switches on (settings: modules.vault, and vault.path for the folder): the sync every
15 minutes, in its own process so a long pass never holds up the rest, and where it stands, for the screens."""
import collections
import os
import subprocess

from jarvis import paths
from jarvis.world import vault


def _sync():
    """Start one pass in its own process; its result line goes to the log."""
    log = open(str(paths.logs('vault_sync.log')), 'a')
    return subprocess.Popen([paths.python(), os.path.join(str(paths.REPO), 'jarvis', 'world', 'vault_run.py')], cwd=str(paths.home()),
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def status(q=None):
    """GET /api/vault: is it on, where it writes, when the last pass ended, how many notes it wrote and how many it held
    back, by reason (never a note's text)."""
    st = vault._load()
    held = collections.Counter(str(h.get('why') or '').split(' "')[0] for h in st.get('held') or [])
    return {'on': bool(vault.root()), 'folder': vault.root(), 'at': st.get('at'), 'written': len(st.get('written') or {}),
            'held': dict(held), 'error': st.get('error')}


GET = {'/api/vault': status}


def jobs():
    return [('vault', 900, _sync)]
