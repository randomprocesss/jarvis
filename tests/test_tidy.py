"""The data folder stays in order: an old backup copy moves to the one archive, nothing is deleted, every move can be put
back, and the live files stay where they are."""
import os, pathlib, subprocess, sys, tempfile, time, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


class Tidy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        (self.home / 'state' / 'secrets').mkdir(parents=True)
        (self.home / 'work').mkdir()
        old = time.time() - 9 * 86400
        self.files = {'state/notes.json': False, 'state/notes.json.bak_1101': True, 'state/new.json.bak_today': False,
                      'state/secrets/key.bak_1101': False, 'work/result.md.bak': False, 'plan.orig': True}
        for rel in self.files:
            p = self.home / rel
            p.write_text(rel)
            if 'today' not in rel:
                os.utime(p, (old, old))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k != 'PYTHONPATH'}
        self.env.update(JARVIS_HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, *a):
        p = subprocess.run([sys.executable, str(REPO / 'tools' / 'tidy_files.py'), *a], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        return p.stdout

    def test_old_backups_move_and_come_back(self):
        self.assertIn('2 entries would move', self.tool('plan'))
        self.assertTrue(all((self.home / rel).exists() for rel in self.files))          # a plan changes nothing
        self.assertIn('moved 2 entries', self.tool('run'))
        month = time.strftime('%Y-%m', time.localtime(time.time() - 9 * 86400))
        for rel, moves in self.files.items():
            self.assertEqual((self.home / rel).exists(), not moves, rel)
            if moves:
                self.assertEqual((self.home / 'archive' / 'backups' / month / rel).read_text(), rel)   # kept whole, under its old path
        rows = (self.home / 'archive' / 'MOVED.tsv').read_text().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn("Jarvis's data folder", (self.home / 'MAP.md').read_text())
        self.assertIn('put back 2 of 2', self.tool('undo', rows[0].split('\t')[0]))
        self.assertTrue(all((self.home / rel).exists() for rel in self.files))


if __name__ == '__main__':
    unittest.main()
