"""The settings of the owner's own code reach the knowledge base: read from their repositories in code, with file and line,
and a value that looks like a credential is never copied."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


class SettingsReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        self.home, self.work = t / 'home', t / 'Work'
        repo = self.work / 'order-desk'
        (repo / '.git').mkdir(parents=True)
        (repo / 'run.py').write_text('import argparse\nTOLERANCE = 0.5   # fill tolerance\nAPI_TOKEN = "zq9-not-for-notes"\nlower = 3\n'
                                     'p = argparse.ArgumentParser()\np.add_argument("--runs", default=32)\n')
        (repo / 'part').mkdir()
        (repo / 'part' / 'settings.toml').write_text('window_days = 90\npassword = "zq9-also-not"\n')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k != 'PYTHONPATH'}
        self.env.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(t / 'claude'), PYTHONDONTWRITEBYTECODE='1')
        self.set(f'config.set("work.folders", [{str(self.work)!r}])')

    def tearDown(self):
        self.tmp.cleanup()

    def set(self, line):
        p = subprocess.run([sys.executable, '-c', 'from jarvis import config, paths\npaths.ensure()\n' + line], capture_output=True, text=True,
                           env=dict(self.env, PYTHONPATH=str(REPO)), cwd=self.tmp.name, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])

    def run_tool(self):
        p = subprocess.run([sys.executable, str(REPO / 'tools' / 'kb_settings.py'), 'run'], capture_output=True, text=True, env=self.env, cwd=self.tmp.name, timeout=120)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        return {f.name: f.read_text() for f in (self.home / 'brain' / 'sources_extra').glob('e_settings_*.txt')}

    def test_every_repository_in_the_work_folders_is_read(self):
        units = self.run_tool()
        self.assertEqual(list(units), ['e_settings_order-desk.txt'])
        text = units['e_settings_order-desk.txt']
        self.assertIn('TOLERANCE = 0.5   # fill tolerance   [run.py:2]', text)
        self.assertIn('--runs default 32', text)
        self.assertIn('window_days: 90', text)
        self.assertNotIn('lower = 3', text)                                  # an ordinary variable is not a setting
        self.assertNotIn('zq9', text)                                        # neither credential was copied
        self.assertEqual(text.count('value withheld'), 2)

    def test_the_owners_own_list_wins(self):
        self.set(f'config.set("knowledge.pipelines", ["Desk settings = {self.work}/order-desk/part"])')
        units = self.run_tool()
        self.assertEqual(list(units), ['e_settings_desk-settings.txt'])
        self.assertIn('window_days: 90', units['e_settings_desk-settings.txt'])
        self.assertNotIn('TOLERANCE', units['e_settings_desk-settings.txt'])


if __name__ == '__main__':
    unittest.main()
