"""Long-term memory: what the owner said is told apart from what Jarvis said, by labels the code writes and reads itself."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


def run(code, home):
    e = dict(os.environ, JARVIS_HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, cwd=str(home), timeout=120)
    if p.returncode != 0:
        raise AssertionError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_owners_turns_are_found_in_the_material_the_code_itself_writes(self):
        got = run('import json\nfrom jarvis import paths\npaths.ensure()\nfrom jarvis import episodes as E\n'
                  'text = "[abcd.0 2026-03-04T10:00] OWNER: the review is on Friday\\n  JARVIS: Noted.\\n'
                  '[abcd.1 2026-03-04T10:01] OWNER: no, I meant Thursday\\n  JARVIS: Thursday, got it."\n'
                  'o = E._origins([("conv", "c1", 0, text)])\n'
                  'print(json.dumps({"labels": sorted(o), "first": o["abcd.0"]["he"], "corrected": o["abcd.0"]["corrected"],'
                  ' "said": E._cite(o, ["abcd.1:owner"])[0], "ack": E._cite(o, ["abcd.1:jarvis"])[0], "none": E._cite(o, ["zzzz.9:owner"])[0],'
                  ' "old_label": E._cite(o, ["abcd.1:he"])[0], "after_correction": E._cite(o, ["abcd.0:jarvis"])[0]}))', self.home)
        self.assertEqual(got['labels'], ['abcd.0', 'abcd.1'])
        self.assertEqual(got['first'], 'the review is on Friday')
        self.assertTrue(got['corrected'])                    # the next turn corrected Jarvis
        self.assertEqual(got['said'], 'owner_said')          # their own words
        self.assertEqual(got['old_label'], 'owner_said')     # material saved before the rename still reads
        self.assertEqual(got['ack'], 'owner_ack')            # only Jarvis said it, and they did not correct it
        self.assertIsNone(got['after_correction'])           # what they corrected is not evidence
        self.assertIsNone(got['none'])                       # no such turn: no evidence, no fact

    def test_the_prompt_asks_for_the_label_the_code_reads(self):
        got = run('import json\nfrom jarvis import config, paths\npaths.ensure()\nconfig.set("owner.name", "Sam Lee")\n'
                  'from jarvis import episodes as E\nprint(json.dumps(E._prompt_text()))', self.home)
        self.assertIn('":owner"', got)
        self.assertIn("Sam Lee's assistant", got)
        for hole in ('{owner}', '{work}', '{tz}'):
            self.assertNotIn(hole, got)
        self.assertNotIn(' he ', got.lower().replace('"', ' '))


if __name__ == '__main__':
    unittest.main()
