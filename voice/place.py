"""Where the listening helpers keep things.

The code is this folder, part of the repository. Everything that belongs to one person (the model files, their own wake-word
model, timings) is in their data folder: <data home>/voice. The data home is JARVIS_HOME, or ~/.jarvis when that is not
set, the same rule as the rest of Jarvis (jarvis/paths.py); the Mac app passes it to every helper it starts.

Nothing here needs more than Python itself, so the helpers' own environment (numpy and onnxruntime only) can import it.
"""
import os

CODE = os.path.dirname(os.path.abspath(__file__))


def home() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get('JARVIS_HOME') or os.path.join('~', '.jarvis')))


DATA = os.path.join(home(), 'voice')
MODELS = os.path.join(DATA, 'models')
ENV = os.path.join(home(), 'envs', 'voice')


def python() -> str:
    """The Python of the helpers' environment (made by setup.py)."""
    return os.path.join(ENV, 'Scripts', 'python.exe') if os.name == 'nt' else os.path.join(ENV, 'bin', 'python')
