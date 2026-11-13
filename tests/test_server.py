"""The server on an empty data home: it starts, creates what it needs, answers every read route, and guards the writes."""
import ast, json, os, pathlib, re, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close()
    return p


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.other = tempfile.TemporaryDirectory()        # a second, separate data home for looking at the code's own tables
        cls.home = pathlib.Path(cls.tmp.name) / 'home'
        cls.port = free_port()
        cls.env = dict(os.environ, JARVIS_HOME=str(cls.home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1', JARVIS_NO_BACKGROUND='1',
                       JARVIS_CLAUDE='/nonexistent/claude', PORT=str(cls.port))
        cls.err = open(pathlib.Path(cls.tmp.name) / 'server.err', 'w+')
        cls.proc = subprocess.Popen([sys.executable, '-m', 'jarvis.server'], env=cls.env, cwd=cls.tmp.name, stdout=subprocess.DEVNULL, stderr=cls.err)
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', cls.port), timeout=0.2).close(); break
            except OSError:
                if cls.proc.poll() is not None:
                    break
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.err.close(); cls.tmp.cleanup(); cls.other.cleanup()

    def stderr(self):
        self.err.flush(); self.err.seek(0)
        return self.err.read()[-1500:]

    def get(self, path, headers=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', headers=headers or {}), timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def post(self, path, body):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'Origin': f'http://localhost:{self.port}'})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def routes(self):
        p = subprocess.run([sys.executable, '-c', 'import json\nfrom jarvis import server\nprint(json.dumps(sorted(server.GET)))'],
                           capture_output=True, text=True, env=dict(self.env, JARVIS_HOME=self.other.name), timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_it_starts_and_makes_its_data_home(self):
        self.assertIsNone(self.proc.poll(), self.stderr())
        for part in ('state', 'work', 'roundups', 'brain'):
            self.assertTrue((self.home / part).is_dir(), part)
        self.assertEqual(sorted(p.name for p in pathlib.Path(self.tmp.name).iterdir() if p.name != 'server.err'), ['home'])   # nothing beside it

    def test_every_read_route_answers(self):
        routes = self.routes()
        self.assertGreaterEqual(len(routes), 18)
        for r in routes:
            code, body = self.get(r + ('?name=x' if r == '/api/recipient_note' else ''))
            self.assertEqual(code, 200, f'{r}: {body[:300]!r}\n{self.stderr()}')
            json.loads(body)
        for gone in ('/api/state', '/api/vpn', '/api/deploy_gate'):
            self.assertNotIn(gone, routes)

    def test_the_page_says_how_to_build_itself_when_it_is_not_built(self):
        if (REPO / 'web' / 'out').is_dir():
            self.skipTest('the screens are built in this copy')
        code, body = self.get('/')
        self.assertEqual(code, 503)
        self.assertIn(b'npm run build', body)

    def test_the_screens_learn_who_the_owner_is_from_the_server(self):
        code, body = self.get('/api/me')
        self.assertEqual(code, 200)
        me = json.loads(body)
        for key in ('set_up', 'name', 'first', 'ticket_base', 'slack_workspace', 'projects', 'kinds', 'apps', 'modules', 'media'):
            self.assertIn(key, me, key)
        self.assertEqual((me['modules'], me['apps']), ([], {'slack': False, 'tracker': False, 'mail': False}))   # nothing is on until they say so

    def test_outward_actions_need_the_app_window(self):
        for path, body in (('/api/slack_send', {'rid': 'r', 'aid': 'a', 'text': 'hi'}), ('/api/approvals_add', {'item': {}}),
                           ('/api/ladder_raise', {'cls': 'slack_team'}), ('/api/jira/comment', {'key': 'ABC-1', 'text': 'x', 'dry': False})):
            code, raw = self.post(path, body)
            self.assertEqual(code, 403, f'{path}: {code} {raw[:200]!r}')

    def test_every_write_route_has_a_tier_on_purpose(self):
        sys.path.insert(0, str(REPO))
        os.environ['JARVIS_HOME'] = self.other.name
        try:
            from jarvis.spine import gateway
        finally:
            os.environ.pop('JARVIS_HOME', None)
        src = (REPO / 'jarvis' / 'server.py').read_text()
        post = src[src.index('def post(path, b):'):src.index('class H(')]
        named = set(re.findall(r"'(/api/[a-z_/]+)'", post))
        dynamic = {'/api/approvals_set', '/api/skill_decide'}            # their tier depends on the body (gateway.tier)
        missing = sorted(p for p in named if p not in gateway.TIER and p not in dynamic and not p.startswith('/api/jira/') and p != '/api/jira/')
        self.assertEqual(missing, [], 'write routes with no tier of their own')
        stale = sorted(p for p in gateway.TIER if p not in named and not p.startswith('/api/jira/') and p not in ('/api/jarvis_task', '/api/jarvis_model', '/api/jarvis_drop'))
        self.assertEqual(stale, [], 'tiers for routes the server no longer has')


if __name__ == '__main__':
    unittest.main()
