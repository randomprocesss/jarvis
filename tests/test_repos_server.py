"""The repositories tool server: it reads only what settings name, from a real (local) git repository, and hides secrets."""
import importlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def git(d, *a):
    env = dict(os.environ, GIT_AUTHOR_NAME='Sam', GIT_AUTHOR_EMAIL='sam@example.com', GIT_COMMITTER_NAME='Sam', GIT_COMMITTER_EMAIL='sam@example.com')
    subprocess.run(['git', '-C', str(d), *a], check=True, capture_output=True, env=env)


class ReposServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = os.environ.get('JARVIS_HOME')
        os.environ['JARVIS_HOME'] = str(root / 'home')
        from jarvis import config, paths
        paths.ensure()
        self.config = config
        host = root / 'host'; work = host / 'shop'; work.mkdir(parents=True)
        git(work, 'init', '-q', '-b', 'main')
        (work / 'app.py').write_text('def total(rows):\n    return sum(rows)\n')
        (work / '.env').write_text('API_KEY=not-a-real-one\n')
        (work / 'settings.ini').write_text('name = shop\npassword = hunter2\n')
        git(work, 'add', '-A'); git(work, 'commit', '-q', '-m', 'ABC-12 add totals')
        git(work, 'clone', '-q', '--bare', str(work), str(host / 'shop.git'))
        self.host = host
        sys.path.insert(0, str(REPO / 'tools'))
        sys.modules.pop('repos_mcp', None)
        self.s = importlib.import_module('repos_mcp')
        self.s.base = lambda: 'file://' + str(host) + '/'          # a folder on this computer stands in for the code host

    def tearDown(self):
        sys.path.remove(str(REPO / 'tools'))
        sys.modules.pop('repos_mcp', None)
        if self.old is None:
            os.environ.pop('JARVIS_HOME', None)
        else:
            os.environ['JARVIS_HOME'] = self.old
        self.tmp.cleanup()

    def call(self, name, **args):
        out = self.s.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
        return out['result']['content'][0]['text'], out['result']['isError']

    def test_with_no_code_host_it_lists_nothing(self):
        importlib.reload(self.s)                                    # the real base(): settings name no address
        text, err = self.call('repos_list')
        self.assertIn('No code host is set', text)
        self.assertEqual(self.s.repos(), [])

    def test_address_from_settings_never_carries_a_user_or_password(self):
        importlib.reload(self.s)
        self.config.set('sources.code_url', 'https://someone:secret@example.com/team')
        self.assertEqual(self.s.base(), 'https://example.com/team/')
        self.config.set('sources.code_url', '')
        self.config.set('sources.code_workspace', 'acme')
        self.assertEqual(self.s.base(), 'https://bitbucket.org/' + 'acme/')

    def test_reads_a_named_repository_and_refuses_the_rest(self):
        self.config.set('sources.pr_repos', ['shop'])
        text, err = self.call('repo_log', repo='shop', n=5)
        self.assertFalse(err, text)
        self.assertIn('ABC-12 add totals', text)
        text, err = self.call('repo_read_file', repo='shop', path='app.py')
        self.assertIn('return sum(rows)', text)
        self.assertTrue(os.path.isdir(os.path.join(self.s.ROOT, 'shop.git')))           # the mirror is in the data home
        self.assertTrue(os.path.realpath(self.s.ROOT).startswith(os.path.realpath(os.environ['JARVIS_HOME'])))
        text, err = self.call('repo_log', repo='other')
        self.assertTrue(err)
        self.assertIn('unknown repo', text)

    def test_secrets_are_never_shown(self):
        self.config.set('sources.pr_repos', ['shop'])
        text, err = self.call('repo_read_file', repo='shop', path='.env')
        self.assertIn('not shown', text)
        self.assertNotIn('not-a-real-one', text)
        text, err = self.call('repo_read_file', repo='shop', path='settings.ini')
        self.assertNotIn('hunter2', text)
        text, err = self.call('repo_grep', repo='shop', pattern='password')
        self.assertNotIn('hunter2', text)

    def test_it_answers_the_protocol(self):
        out = self.s.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        names = [t['name'] for t in out['result']['tools']]
        self.assertEqual(len(names), 8)
        from jarvis import assistant
        self.assertEqual(sorted('mcp__repos__' + n for n in names), sorted(assistant.REPO_READ))
        self.assertIsNone(self.s.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))


if __name__ == '__main__':
    unittest.main()
