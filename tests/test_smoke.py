"""The smoke test against a real server: one is started on a free port in a temporary data home, with no Claude behind
it and nothing running in the background; every check that needs no model passes, a server that is not there is
reported check by check, and the server is stopped whatever happens."""
import os, pathlib, re, socket, subprocess, sys, tempfile, time, unittest, urllib.request

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close()
    return p


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)                              # cleanups run last first, and also when the start below fails
        t = pathlib.Path(cls.tmp.name)
        cls.home, cls.port = t / 'home', free_port()
        (t / 'scratch').mkdir()
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k not in ('PYTHONPATH', 'PORT')}
        cls.env.update(JARVIS_HOME=str(cls.home), CLAUDE_CONFIG_DIR=str(t / 'claude'), JARVIS_CLAUDE='/nonexistent/claude', JARVIS_NO_BACKGROUND='1',
                       PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(t / 'scratch'))   # the checks' scratch files go with the rest
        cls.err = open(t / 'server.err', 'w+')
        cls.addClassCleanup(cls.err.close)
        cls.proc = subprocess.Popen([sys.executable, '-m', 'jarvis.server', '--port', str(cls.port)], env=cls.env, cwd=cls.tmp.name,
                                    stdout=subprocess.DEVNULL, stderr=cls.err)
        cls.addClassCleanup(cls.stop)                                     # from here on the server is stopped, pass or fail
        for _ in range(150):
            try:
                with urllib.request.urlopen(f'http://localhost:{cls.port}/healthz', timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                if cls.proc.poll() is not None:
                    break
                time.sleep(0.1)
        cls.err.flush(); cls.err.seek(0)
        raise AssertionError('the server did not come up: ' + cls.err.read()[-1500:])

    @classmethod
    def stop(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(10)
            except subprocess.TimeoutExpired:
                cls.proc.kill(); cls.proc.wait(10)
        assert cls.proc.poll() is not None, 'the server is still running'

    def smoke(self, port):
        return subprocess.run([sys.executable, str(REPO / 'tools' / 'jarvis_smoke.py'), '--port', str(port), '--no-build'],
                              capture_output=True, text=True, env=self.env, cwd=self.tmp.name, timeout=600)

    def files(self):
        return {str(p.relative_to(self.home)) for p in self.home.rglob('*') if p.is_file() and not p.name.startswith('jarvis.db')}

    def test_every_check_that_needs_no_model_passes(self):
        before = self.files()
        p = self.smoke(self.port)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-3000:])
        lines = p.stdout.splitlines()
        self.assertEqual([l for l in lines if l.startswith('FAIL')], [])
        m = re.match(r'(\d+)/(\d+) passed', lines[-1])
        self.assertTrue(m and m.group(1) == m.group(2) and int(m.group(1)) >= 40, lines[-1])
        ran = {l[6:].split('  ')[0].strip() for l in lines if l.startswith('PASS')}
        for name in ('GET /api/inbox', 'GET /api/nudges', 'GET / (page)', 'GET /api/me (who the screens are for)', 'GET /healthz (watchdog probe)',
                     'parse_reply (mood, action filter)', 'memory: remember/forget', 'memory: recall finds the episode',
                     'review: numbers from the logs', 'spine: replay cases (code-only)'):
            self.assertIn(name, ran)
        self.assertNotIn('/api/vpn', out)                                 # a route only the system this came from had
        self.assertFalse(any(l.startswith(('PASS  build:', 'PASS  live brain', 'FAIL  live brain')) for l in lines))   # no build, no model
        self.assertIsNone(self.proc.poll())                               # the server it checked is still up
        self.assertLessEqual(self.files() - before, {os.path.join('state', 'eval_last.json')})   # their data: nothing new but the replay cases' result

    def test_a_server_that_is_not_there_fails_check_by_check(self):
        p = self.smoke(free_port())                                       # nothing listens there
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-3000:])
        lines = p.stdout.splitlines()
        self.assertIn('FAILED: GET /api/inbox, GET /api/changed, ', lines[-1])
        self.assertTrue(any(l.startswith('FAIL  GET /healthz (watchdog probe)') for l in lines))
        self.assertTrue(any(l.startswith('PASS  parse_reply (mood, action filter)') for l in lines))   # the checks that need no server still ran
        self.assertNotIn('spine: healthz shape', lines[-1])


if __name__ == '__main__':
    unittest.main()
