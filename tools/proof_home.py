"""A made-up owner in a temporary data home, for the proofs: they never read or write the owner's own data.

A proof imports this before any other part of Jarvis. It points JARVIS_HOME at a new temporary folder, fills in a made-up
owner whose apps are all connected (so every watcher has something to start), and keeps the place the results go: the
state folder of the real data home.

    import proof_home                      # first; then the modules under test
    proof_home.save('phase6_proofs', rows) # at the end
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths  # noqa: E402

REAL = str(paths.home())                                 # the owner's own data home: only the results are written there
HOME = tempfile.mkdtemp(prefix='jarvis_proof_')
os.environ['JARVIS_HOME'] = HOME
from jarvis import config  # noqa: E402

paths.ensure()
WORK = os.path.join(HOME, 'Work')
os.makedirs(WORK, exist_ok=True)
for key, value in (('owner.name', 'Sam Lee'), ('owner.emails', ['sam@example.com']), ('work.about', 'runs the order desk'),
                   ('work.folders', [WORK]), ('work.search_words', ['order desk']), ('tracker.own_projects', ['ABC']),
                   ('sources.expected', ['Slack', 'Atlassian Rovo', 'Microsoft 365'])):
    config.set(key, value)


def save(name, rows):
    """The proof's results, for the owner's own Jarvis to show: <real data home>/state/<name>.json."""
    d = os.path.join(REAL, 'state')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + '.json'), 'w') as f:
        json.dump(rows, f, indent=1)
