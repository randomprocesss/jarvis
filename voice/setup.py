#!/usr/bin/env python3
"""Set up the listening extras of the Mac app: the wake word ("Hey Jarvis", or your own one-word "Jarvis" once you teach
it your voice) and natural turn-taking. Without them the app still works: press to talk, and it speaks with the Mac's
own voice.

    python3 voice/setup.py              say what will be fetched, ask, then do it
    python3 voice/setup.py --yes        the same, without the question
    python3 voice/setup.py --check      say what is in place; change nothing
    python3 voice/setup.py --from DIR   take the model files from a folder you already have, not from the internet
    python3 voice/setup.py --models     only the model files (the Python environment is left alone)

Everything goes into your data folder, nothing into this repository:
  envs/voice      a Python environment with numpy and onnxruntime (from PyPI)
  voice/models    five small open models, about 15 MB, each checked against the checksum written below before it is kept

The models and their licences are listed in voice/CREDITS.md. One of them, the "Hey Jarvis" wake word, is licensed for
personal, non-commercial use (CC BY-NC-SA 4.0).
"""
import hashlib, json, os, shutil, subprocess, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import place  # noqa: E402

OWW = 'https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/'
MODELS = [   # file, bytes, sha256, where it comes from, licence
    ('melspectrogram.onnx', 1087958, 'ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f', OWW + 'melspectrogram.onnx', 'Apache-2.0'),
    ('embedding_model.onnx', 1326578, '70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f', OWW + 'embedding_model.onnx', 'Apache-2.0'),
    ('hey_jarvis_v0.1.onnx', 1271370, '94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb', OWW + 'hey_jarvis_v0.1.onnx',
     'CC BY-NC-SA 4.0 (personal, non-commercial use)'),
    ('silero_vad.onnx', 2327524, '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3',
     'https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad.onnx', 'MIT'),
    ('smart-turn-v3.2-cpu.onnx', 8679182, '2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f',
     'https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx', 'BSD-2-Clause'),
]
PACKAGES = ['numpy', 'onnxruntime']


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def have(name, sha):
    p = os.path.join(place.MODELS, name)
    return os.path.isfile(p) and digest(p) == sha


def env_ok():
    py = place.python()
    if not os.path.isfile(py):
        return False
    r = subprocess.run([py, '-c', 'import numpy, onnxruntime'], capture_output=True)
    return r.returncode == 0


def status():
    return {'data': place.DATA, 'environment': env_ok(), 'models': {m[0]: have(m[0], m[2]) for m in MODELS}}


def ready():
    s = status()
    return s['environment'] and all(s['models'].values())


def put_model(name, size, sha, url, source_dir=None):
    """Fetch (or copy) one model to a temporary name, check its checksum, and only then give it its real name."""
    os.makedirs(place.MODELS, exist_ok=True)
    dest = os.path.join(place.MODELS, name); tmp = dest + '.part'
    if source_dir:
        src = os.path.join(source_dir, name)
        if not os.path.isfile(src):
            return f'{name}: not in {source_dir}'
        shutil.copyfile(src, tmp)
    else:
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, 'wb') as f:
                shutil.copyfileobj(r, f)
        except Exception as e:  # noqa: BLE001  any network fault is the same to the person: say it and stop
            if os.path.exists(tmp):
                os.remove(tmp)
            return f'{name}: could not be fetched ({type(e).__name__}: {str(e)[:120]})'
    got = digest(tmp)
    if got != sha:
        os.remove(tmp)
        return f'{name}: the file does not match its published checksum, so it was not kept'
    os.replace(tmp, dest)
    return ''


def make_env():
    if not os.path.isfile(place.python()):
        os.makedirs(os.path.dirname(place.ENV), exist_ok=True)
        r = subprocess.run([sys.executable, '-m', 'venv', place.ENV])
        if r.returncode != 0:
            return 'the Python environment could not be made'
    r = subprocess.run([place.python(), '-m', 'pip', 'install', '--quiet', '--upgrade', 'pip'] )
    r = subprocess.run([place.python(), '-m', 'pip', 'install', '--quiet'] + PACKAGES)
    return '' if r.returncode == 0 and env_ok() else 'numpy and onnxruntime could not be installed into the environment'


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if '--check' in a:
        s = status(); s['ready'] = ready()
        print(json.dumps(s, indent=1)); return 0 if s['ready'] else 1
    source = a[a.index('--from') + 1] if '--from' in a and a.index('--from') + 1 < len(a) else None
    todo = [m for m in MODELS if not have(m[0], m[2])]
    want_env = '--models' not in a and not env_ok()
    if not todo and not want_env:
        print('The listening extras are already set up in', place.home()); return 0
    print('Jarvis: the listening extras (the wake word and turn-taking). To put in', place.home() + ':')
    if want_env:
        print('  - a Python environment, envs/voice, with', ' and '.join(PACKAGES), '(from PyPI)')
    for name, size, _, url, lic in todo:
        print(f'  - {name}  {size / 1e6:.1f} MB  {lic}\n      from {os.path.join(source, name) if source else url}')
    if '--yes' not in a:
        try:
            if input('Go ahead? [y/N] ').strip().lower() not in ('y', 'yes'):
                print('Nothing was changed.'); return 1
        except EOFError:
            print('Nothing was changed (run again with --yes to go ahead without the question).'); return 1
    problems = []
    for name, size, sha, url, _ in todo:
        err = put_model(name, size, sha, url, source)
        print('  ' + (err or f'{name}: in place, checksum matches'))
        if err:
            problems.append(err)
    if want_env:
        err = make_env()
        print('  ' + (err or 'the Python environment is ready'))
        if err:
            problems.append(err)
    if problems:
        print('Not finished:', len(problems), 'problem(s) above. What is in place stays; run this again to finish.'); return 1
    if not ready():
        print('The model files are in place. The Python environment is not: run this again without --models.'); return 1
    print('Done. Reopen Jarvis: it listens for "Hey Jarvis", and "Teach Jarvis your voice" in its menu makes the one-word wake word.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
