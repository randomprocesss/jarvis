"""Demo, a copy of everything, erase everything, updates, the data steps and the jarvis command. Everything happens in a
temporary home folder: the real Trash, the real data folder and the network are never touched."""
import contextlib, importlib, io, json, os, socket, stat, subprocess, sys, tempfile, unittest, urllib.error, urllib.request, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STUB = '''
import json, sys
a = sys.argv[1:]
if a[:1] == ['--version']:
    print('9.9.9 (Claude Code)')
elif a[:2] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max'}))
'''


def free_port():
    for _ in range(20):
        s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close()
        t = socket.socket()
        try:
            t.bind(('127.0.0.1', p + 1)); t.close()
            return p
        except OSError:
            t.close()
    raise RuntimeError('no two free ports in a row')


def git(d, *a):
    env = dict(os.environ, GIT_AUTHOR_NAME='Sam', GIT_AUTHOR_EMAIL='sam@example.com', GIT_COMMITTER_NAME='Sam', GIT_COMMITTER_EMAIL='sam@example.com')
    return subprocess.run(['git', '-C', str(d), *a], check=True, capture_output=True, text=True, env=env).stdout


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(os.path.realpath(self.tmp.name))
        self.user, self.home = self.t / 'user', self.t / 'user' / '.jarvis'
        self.user.mkdir()
        stub = self.t / 'claude-stub'
        stub.write_text('#!' + sys.executable + '\n' + STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.keep = {k: os.environ.get(k) for k in ('HOME', 'JARVIS_HOME', 'CLAUDE_CONFIG_DIR', 'JARVIS_CLAUDE', 'JARVIS_DEMO', 'JARVIS_PORT', 'JARVIS_MAIN_PORT')}
        os.environ.update(HOME=str(self.user), JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.t / 'claude'), JARVIS_CLAUDE=str(stub))
        for k in ('JARVIS_DEMO', 'JARVIS_PORT', 'JARVIS_MAIN_PORT'):
            os.environ.pop(k, None)
        from jarvis import config, paths, product, procs, setup
        self.config, self.paths, self.product, self.procs = config, paths, product, procs
        setup.forget()
        self.ended = []
        self._later = product._later
        product._later = lambda fn, secs=0: self.ended.append(fn)            # never end the test process
        paths.ensure()
        config.set('owner.name', 'Sam Lee')

    def tearDown(self):
        self.product._later = self._later
        for k, v in self.keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()


class CopyAndErase(Base):
    def fill(self):
        (self.home / 'brain' / 'main').mkdir(parents=True, exist_ok=True)
        (self.home / 'brain' / 'main' / 'note.md').write_text('On-time delivery was 82.6% on 164 orders.\n' * 20)
        (self.home / 'demo').mkdir(exist_ok=True); (self.home / 'demo' / 'x.txt').write_text('made up')
        skill = self.paths.claude_skills() / 'jarvis-roundup'
        skill.mkdir(parents=True); (skill / 'SKILL.md').write_text('a skill Jarvis installed')
        own = self.paths.claude_chats() / self.paths.own_runs_prefix()
        own.mkdir(parents=True); (own / 'run.jsonl').write_text('{}')
        mine = self.paths.claude_chats() / '-somewhere-else'
        mine.mkdir(parents=True); (mine / 'chat.jsonl').write_text('{}')
        return skill, own, mine

    def test_the_preview_names_what_would_go_and_what_stays(self):
        self.fill()
        p = self.product.erase_preview()
        names = {r['name']: r for r in p['rows']}
        self.assertGreater(names['brain']['bytes'], 800)
        self.assertIn('config.toml', names)
        self.assertEqual({r['name'] for r in p['also']}, {'skills', 'runs'})
        self.assertTrue(p['safe'])
        self.assertIn('Your own Claude Code chats', p['stays'])

    def test_a_copy_is_a_zip_in_the_home_folder_without_what_can_be_made_again(self):
        self.fill()
        body, code = self.product.export({})
        self.assertEqual(code, 200)
        self.assertEqual(Path(body['path']).parent, self.user)
        names = zipfile.ZipFile(body['path']).namelist()
        self.assertIn('jarvis-data/config.toml', names)
        self.assertIn('jarvis-data/brain/main/note.md', names)
        self.assertFalse(any(n.startswith('jarvis-data/demo/') for n in names))
        again, _ = self.product.export({})
        self.assertNotEqual(again['path'], body['path'])                       # a second copy never overwrites the first

    def test_erase_needs_the_word_and_then_moves_everything_where_it_can_be_taken_back(self):
        skill, own, mine = self.fill()
        self.assertEqual(self.product.erase({'confirm': 'erase'})[1], 400)
        self.assertTrue(self.home.exists())
        body, code = self.product.erase({'confirm': 'ERASE'})
        self.assertEqual(code, 200, body)
        self.assertFalse(self.home.exists() or skill.exists() or own.exists())
        self.assertTrue(mine.exists())                                          # the owner's own chats are not Jarvis's to remove
        kept = [p for p in self.user.rglob('note.md')]
        self.assertEqual(len(kept), 1)                                          # still on disk, in the trash place
        self.assertTrue(str(self.home) not in str(kept[0]))
        self.assertEqual(len(self.ended), 1)                                    # the server ends afterwards

    def test_erase_for_good_leaves_nothing(self):
        self.fill()
        body, code = self.product.erase({'confirm': 'ERASE', 'mode': 'forever'})
        self.assertEqual((code, body['where']), (200, 'erased for good'))
        self.assertEqual([p for p in self.user.rglob('note.md')], [])

    def test_a_folder_that_is_not_plainly_jarvis_is_never_erased(self):
        os.environ['JARVIS_HOME'] = str(self.user)                             # a setting pointing at the whole home folder
        (self.user / 'keep.txt').write_text('mine')
        body, code = self.product.erase({'confirm': 'ERASE', 'mode': 'forever'})
        self.assertEqual(code, 400)
        self.assertTrue((self.user / 'keep.txt').exists())
        self.assertFalse(self.product.erase_preview()['safe'])


class Demo(Base):
    def test_the_demo_is_a_second_jarvis_on_made_up_data_that_sends_nothing(self):
        port = free_port()
        os.environ['JARVIS_PORT'] = str(port)
        body, code = self.product.demo_start({})
        self.assertEqual(code, 200, body)
        demo_port = port + 1
        try:
            self.assertEqual(body['url'], f'http://localhost:{demo_port}/#/today')
            get = lambda path: json.loads(urllib.request.urlopen(f'http://127.0.0.1:{demo_port}{path}', timeout=10).read())
            me = get('/api/me')
            self.assertTrue(me['demo'] and me['set_up'])
            self.assertEqual(me['name'], 'Sam Lee')
            self.assertTrue((self.home / 'demo' / 'DEMO.md').exists())           # its own data home, inside the real one
            for path, payload in (('/api/slack_send', {'rid': 'x', 'aid': 'y'}), ('/api/watch', {'what': 'x'}), ('/api/setup_save', {'set': {'owner.name': 'Other'}})):
                req = urllib.request.Request(f'http://127.0.0.1:{demo_port}{path}', data=json.dumps(payload).encode(), method='POST',
                                             headers={'Content-Type': 'application/json', 'Host': f'localhost:{demo_port}', 'Origin': f'http://localhost:{demo_port}'})
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req, timeout=10)
                self.assertEqual(e.exception.code, 403)
                self.assertIn('This is the demo', json.loads(e.exception.read())['error'])
            self.assertEqual(self.config.load().owner(), 'Sam Lee')
            again, code = self.product.demo_start({})                              # asking twice opens the same one
            self.assertEqual((code, again['url']), (200, body['url']))
        finally:
            self.product.demo_stop()
        self.assertIsNone(self.procs.health(demo_port, 1.0))

    def test_leaving_the_demo_points_back_at_the_owners_own_setup(self):
        os.environ.update(JARVIS_DEMO='1', JARVIS_MAIN_PORT='8123')
        body, code = self.product.demo_leave({})
        self.assertEqual((code, body['url']), (200, 'http://localhost:8123/#/setup'))
        self.assertEqual(self.product.erase({'confirm': 'ERASE'})[1], 400)       # the demo can never erase the real data
        self.assertEqual(self.product.update({})[1], 400)


class Updates(Base):
    def make(self):
        origin, clone = self.t / 'origin', self.t / 'copy'
        origin.mkdir()
        git(origin, 'init', '-q', '-b', 'main')
        (origin / 'VERSION').write_text('0.0.1\n'); (origin / 'app.txt').write_text('one\n')
        git(origin, 'add', '-A'); git(origin, 'commit', '-q', '-m', 'first')
        git(self.t, 'clone', '-q', str(origin), str(clone))
        return origin, clone

    def release(self, origin, v):
        (origin / 'VERSION').write_text(v + '\n'); (origin / 'app.txt').write_text('more\n')
        git(origin, 'add', '-A'); git(origin, 'commit', '-q', '-m', 'release ' + v); git(origin, 'tag', 'v' + v)

    def test_no_release_means_up_to_date_and_a_plain_folder_says_it_cannot_update_itself(self):
        origin, clone = self.make()
        st = self.product.update_state(repo=clone, fresh=True)
        self.assertEqual((st['current'], st['latest'], st['available']), ('0.0.1', '', False))
        plain = self.t / 'plain'; plain.mkdir(); (plain / 'VERSION').write_text('0.0.1\n')
        self.assertIn('git clone', self.product.update_state(repo=plain)['why_not'])
        self.assertEqual(self.product.update({}, repo=clone, restart=False)[1], 400)

    def test_a_newer_release_is_offered_and_the_update_keeps_the_settings(self):
        origin, clone = self.make()
        self.release(origin, '0.0.2'); self.release(origin, '0.1.0')
        st = self.product.update_state(repo=clone, fresh=True)
        self.assertEqual((st['latest'], st['available'], st['can_update']), ('v0.1.0', True, True))
        body, code = self.product.update({}, repo=clone, restart=False)
        self.assertEqual((code, body['from'], body['to']), (200, '0.0.1', '0.1.0'))
        self.assertEqual((clone / 'app.txt').read_text(), 'more\n')
        self.assertTrue((self.home / 'state' / 'backups' / 'config-before-v0.1.0.toml').exists())
        self.assertFalse(self.product.update_state(repo=clone, fresh=True)['available'])

    def test_a_copy_changed_by_hand_is_left_alone_and_says_why(self):
        origin, clone = self.make()
        self.release(origin, '0.0.2')
        (clone / 'app.txt').write_text('my own change\n')
        st = self.product.update_state(repo=clone, fresh=True)
        self.assertTrue(st['available'] and not st['can_update'])
        self.assertIn('changed by hand', st['why_not'])
        self.assertEqual(self.product.update({}, repo=clone, restart=False)[1], 400)
        self.assertEqual((clone / 'app.txt').read_text(), 'my own change\n')


class DataSteps(Base):
    def test_each_step_runs_once_in_order_and_a_failed_one_is_tried_again(self):
        from jarvis import migrations
        seen = []

        class Step:
            def __init__(self, n, fail=False): self.n, self.fail = n, fail
            def run(self, home):
                if self.fail: raise RuntimeError('not yet')
                seen.append(self.n)
        mods = {'m0001_a': Step(1), 'm0002_b': Step(2, fail=True), 'm0003_c': Step(3)}
        steps = [(1, 'm0001_a'), (2, 'm0002_b'), (3, 'm0003_c')]
        with self.assertRaises(RuntimeError):
            migrations.run(steps, mods.get)
        self.assertEqual((seen, migrations.done()), ([1], [1]))
        mods['m0002_b'].fail = False
        self.assertEqual(migrations.run(steps, mods.get), [2, 3])
        self.assertEqual(migrations.run(steps, mods.get), [])
        self.assertEqual(migrations.steps(), [])                                 # the first layout: no step ships yet


class Command(Base):
    def run_cli(self, *args):
        from jarvis import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_start_status_and_stop(self):
        port = str(free_port())
        code, out = self.run_cli('start', '--port', port, '--no-open')
        try:
            self.assertEqual(code, 0, out)
            self.assertIn(f'running at http://localhost:{port}/', out)
            code, out = self.run_cli('status', '--port', port)
            self.assertIn('running at', out)
            self.assertIn('Setup: still to do', out)
            code, out = self.run_cli('start', '--port', port, '--no-open')
            self.assertIn('already running', out)
        finally:
            code, out = self.run_cli('stop', '--port', port)
        self.assertIn('has stopped', out)
        self.assertIsNone(self.procs.health(int(port), 1.0))
        self.assertIn('not running', self.run_cli('stop', '--port', port)[1])

    def test_doctor_says_what_is_there_and_what_to_do(self):
        code, out = self.run_cli('doctor', '--port', str(free_port()))
        self.assertEqual(code, 0, out)
        self.assertIn('ok    Claude Code 9.9.9', out)
        self.assertIn('Everything Jarvis needs is here.', out)
        from jarvis import setup
        found = self.paths.claude_bin
        self.paths.claude_bin = lambda: ''                                       # a computer without Claude Code
        setup.forget()
        try:
            code, out = self.run_cli('doctor', '--port', str(free_port()))
        finally:
            self.paths.claude_bin = found
            setup.forget()
        self.assertEqual(code, 1)
        self.assertIn('FIX   Claude Code', out)
        self.assertIn('Install it from', out)

    def test_help_and_an_unknown_command(self):
        self.assertIn('jarvis start', self.run_cli()[1])
        self.assertEqual(self.run_cli('fly')[0], 1)


if __name__ == '__main__':
    unittest.main()
