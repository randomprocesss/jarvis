"""The owner's repositories reach the knowledge base: README, commits by month, and a map of the code they wrote. A
repository shared with many teams gives only their own commits and the ones that name their work."""
import os, pathlib, shutil, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


@unittest.skipUnless(shutil.which('git'), 'git is not installed')
class ReposReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        self.home, self.work = t / 'home', t / 'Work'
        self.repo = self.work / 'order-desk'
        self.repo.mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('JARVIS_', 'GIT_')) and k != 'PYTHONPATH'}
        self.env.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(t / 'claude'), PYTHONDONTWRITEBYTECODE='1',
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        self.git('init', '-q')
        self.git('checkout', '-q', '-b', 'main')
        (self.repo / 'README.md').write_text('# Order desk\nHow the weekly export is made.\n')
        (self.repo / 'export.py').write_text('"""Builds the weekly export."""\n\n\ndef build(week, dry=False):\n    return week\n')
        self.commit('Sam Lee', 'sam@example.com', 'Add the weekly export')
        (self.repo / 'other.txt').write_text('x\n')
        self.commit('Pat Roe', 'pat@example.com', 'Tidy the build files')
        (self.repo / 'more.txt').write_text('y\n')
        self.commit('Pat Roe', 'pat@example.com', 'Fix the order desk totals')
        self.set(f'config.set("owner.name", "Sam Lee")\nconfig.set("owner.emails", ["sam@example.com"])\n'
                 f'config.set("work.folders", [{str(self.work)!r}])\nconfig.set("work.search_words", ["order desk"])')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a, **env):
        p = subprocess.run(['git', '-C', str(self.repo), *a], capture_output=True, text=True, env=dict(self.env, **env))
        self.assertEqual(p.returncode, 0, p.stderr[-400:])

    def commit(self, name, mail, subject):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', subject, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=mail,
                 GIT_AUTHOR_DATE='2026-03-04T10:00:00', GIT_COMMITTER_DATE='2026-03-04T10:00:00')

    def set(self, lines):
        p = subprocess.run([sys.executable, '-c', 'from jarvis import config, paths\npaths.ensure()\n' + lines], capture_output=True, text=True,
                           env=dict(self.env, PYTHONPATH=str(REPO)), cwd=self.tmp.name, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])

    def units(self):
        p = subprocess.run([sys.executable, str(REPO / 'tools' / 'kb_repos.py'), 'run'], capture_output=True, text=True, env=self.env, cwd=self.tmp.name, timeout=120)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        return {f.name: f.read_text() for f in (self.home / 'brain' / 'sources_extra').glob('l_repo_*.txt')}

    def test_readme_commits_and_their_code(self):
        u = self.units()
        self.assertEqual(sorted(u), ['l_repo_order-desk_code_main.txt', 'l_repo_order-desk_log_2026-03.txt', 'l_repo_order-desk_readme.txt'])
        self.assertIn('How the weekly export is made.', u['l_repo_order-desk_readme.txt'])
        log = u['l_repo_order-desk_log_2026-03.txt']
        self.assertIn('3 commits in 2026-03', log)
        self.assertIn('Sam Lee', log); self.assertIn('Tidy the build files', log)
        code = u['l_repo_order-desk_code_main.txt']
        self.assertIn('1 of their commits', code)                              # only Sam's commit counts as their code
        self.assertIn('Builds the weekly export.', code)
        self.assertIn('def build(week, dry=False):', code)
        self.assertNotIn('other.txt', code)

    def test_a_shared_repository_gives_only_their_part(self):
        self.set('config.set("sources.shared_repos", ["order-desk"])')
        u = self.units()
        self.assertEqual(sorted(u), ['l_repo_order-desk_log_2026-03.txt', 'l_repo_order-desk_readme.txt'])   # no code map
        log = u['l_repo_order-desk_log_2026-03.txt']
        self.assertIn('2 commits in 2026-03', log)
        self.assertIn('Add the weekly export', log)                            # their own
        self.assertIn('Fix the order desk totals', log)                        # names their work
        self.assertNotIn('Tidy the build files', log)


if __name__ == '__main__':
    unittest.main()
