"""The reports Jarvis keeps about itself (the scorecard, the shadow score, the replay exam, the weekly review, the context
finder) run before Jarvis has ever done anything: an empty answer, never an error, and never a number made up."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


class Reports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k != 'PYTHONPATH'}
        self.env.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(pathlib.Path(self.tmp.name) / 'claude'), JARVIS_CLAUDE='/nonexistent/claude',
                        PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, name, *args):
        p = subprocess.run([sys.executable, str(REPO / 'tools' / name), *args], capture_output=True, text=True, env=self.env, cwd=self.tmp.name, timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        return p.stdout

    def test_each_report_runs_on_an_empty_home(self):
        card = json.loads(self.tool('scorecard.py', '--json'))
        rows = card['metrics'] if isinstance(card, dict) else card
        self.assertGreaterEqual(len(rows), 10)
        vault = next(m for m in rows if m['key'] == 'vault')
        self.assertEqual((vault['value'], vault['show']), (None, 'the vault is switched off'))   # never the owner's own folder by a fixed path
        self.assertFalse(any('Traceback' in str(m.get('show')) for m in rows))
        self.assertIn('0 answered items with a Jarvis draft', self.tool('shadow_score.py'))
        self.assertIn('nothing judged yet', self.tool('replay_exam.py'))
        self.assertIn('Your feedback: none', self.tool('jarvis_review.py', '--no-judge'))
        self.assertIn('Memory notes: 0 hit(s)', self.tool('find_context.py', 'export', 'orders'))


if __name__ == '__main__':
    unittest.main()
