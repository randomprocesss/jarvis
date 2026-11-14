"""The listening extras: where they keep things, and that a model file is kept only when its checksum matches."""
import contextlib, hashlib, importlib, io, json, os, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class VoiceSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'home'
        self.old = os.environ.get('JARVIS_HOME')
        os.environ['JARVIS_HOME'] = str(self.home)
        sys.path.insert(0, str(REPO / 'voice'))
        for m in ('place', 'setup'):
            sys.modules.pop(m, None)
        self.place = importlib.import_module('place')
        self.setup = importlib.import_module('setup')

    def tearDown(self):
        sys.path.remove(str(REPO / 'voice'))
        for m in ('place', 'setup'):
            sys.modules.pop(m, None)
        if self.old is None:
            os.environ.pop('JARVIS_HOME', None)
        else:
            os.environ['JARVIS_HOME'] = self.old
        self.tmp.cleanup()

    def test_everything_is_kept_in_the_data_home(self):
        home = os.path.realpath(str(self.home))
        for p in (self.place.DATA, self.place.MODELS, self.place.ENV, self.place.python()):
            self.assertTrue(os.path.realpath(p).startswith(home), p)
        self.assertFalse(os.path.realpath(self.place.DATA).startswith(str(REPO)))

    def test_nothing_is_ready_in_a_new_home_and_check_changes_nothing(self):
        s = self.setup.status()
        self.assertFalse(s['environment'])
        self.assertEqual(set(s['models'].values()), {False})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.setup.main(['--check']), 1)
        self.assertIs(json.loads(out.getvalue())['ready'], False)
        self.assertFalse(self.home.exists())

    def test_a_model_is_kept_only_when_its_checksum_matches(self):
        src = Path(self.tmp.name) / 'mirror'; src.mkdir()
        (src / 'small.onnx').write_bytes(b'not a real model, only bytes with a checksum')
        sha = hashlib.sha256((src / 'small.onnx').read_bytes()).hexdigest()
        self.assertEqual(self.setup.put_model('small.onnx', 44, sha, '', str(src)), '')
        self.assertTrue(self.setup.have('small.onnx', sha))
        why = self.setup.put_model('other.onnx', 44, sha, '', str(src))
        self.assertIn('not in', why)
        (src / 'other.onnx').write_bytes(b'a different file')
        why = self.setup.put_model('other.onnx', 16, sha, '', str(src))
        self.assertIn('checksum', why)
        self.assertEqual(sorted(os.listdir(self.place.MODELS)), ['small.onnx'])      # the bad one and its part file are gone

    def test_the_list_names_five_models_with_full_checksums(self):
        self.assertEqual(len(self.setup.MODELS), 5)
        for name, size, sha, url, licence in self.setup.MODELS:
            self.assertRegex(sha, r'^[0-9a-f]{64}$')
            self.assertTrue(url.startswith('https://') and url.endswith(name) and size > 0 and licence)


if __name__ == '__main__':
    unittest.main()
