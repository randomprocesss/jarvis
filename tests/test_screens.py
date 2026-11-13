"""Nothing personal and no secret enters the knowledge base: the shared screens, redaction, the secret scan, what is kept
of a file that held a secret, and the chat reader that feeds it all. The made-up secrets are assembled while the test
runs, so this file holds none."""
import json, os, pathlib, stat, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
PW = 'pass' + 'word'                                          # so no secret-shaped text sits in this file
KEY = 'AK' + 'IA' + 'Z' * 16
CONN = 'postgres' + 'ql://' + 'reader' + ':' + 'hunter2hunter2' + '@' + 'db.example.org:5432/sales'


def run(code, home, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k != 'CLAUDE_CONFIG_DIR'}
    e.update(JARVIS_HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1', **env)
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, cwd=str(home), timeout=120)
    if p.returncode != 0:
        raise AssertionError(p.stderr[-2500:])
    return json.loads(p.stdout.strip().splitlines()[-1])


PRE = 'import json, os\nfrom jarvis import config, paths, screens\npaths.ensure()\npaths.use_tools()\n'


class Screens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_shipped_patterns_load_and_catch_the_usual_secrets(self):
        got = run(PRE + f'n = len(screens.secret_patterns())\n'
                  f'print(json.dumps([n, screens.has_secret("the {PW} = \\"{"x" * 14}\\" works"), screens.has_secret({KEY!r}), screens.has_secret({CONN!r}),'
                  ' screens.has_secret("PASS: all 12 checks"), screens.has_secret("nothing here")]))', self.home)
        self.assertGreaterEqual(got[0], 10)
        self.assertTrue(got[1] and got[2] and got[3], got)
        self.assertEqual(got[4:], ['', ''])                              # a test result line is not a secret

    def test_personal_words_are_the_owners_own_and_hold_a_text_back(self):
        got = run(PRE + 'a = screens.screen("the trip to Lisbon is booked", "work")\n'
                  'open(screens.words_file(), "w").write("# mine\\nLisbon\\nDr Okafor\\n")\n'
                  'print(json.dumps([a, screens.screen("the trip to Lisbon is booked", "work"), screens.screen("Lisbonne is a typeface", "work"),'
                  ' screens.screen("see dr okafor at 4", "work"), screens.screen("plain work", "personal"), screens.screen("plain work", None), screens.words_file()]))', self.home)
        self.assertEqual(got[0], '')                                     # no list yet: nothing is personal by a word
        self.assertEqual(got[1], 'personal word "Lisbon"')
        self.assertEqual(got[2], '')                                     # whole words only
        self.assertEqual(got[3], 'personal word "Dr Okafor"')
        self.assertEqual(got[4:6], ['scope personal', 'waiting for its scope label'])
        self.assertEqual(got[6], str(self.home / 'state' / 'personal_words.txt'))   # theirs: in the data home, never in the code

    def test_redaction_takes_the_value_and_keeps_the_sense(self):
        got = run(PRE + f'import kb_redact as R, collections\nc = collections.Counter()\n'
                  f'out = R.redact("connect with {CONN} and key {KEY}; then PASS: ok", c)\nprint(json.dumps([out, sum(c.values())]))', self.home)
        text, n = got
        self.assertIn('db.example.org:5432/sales', text)                 # where it connects stays readable
        self.assertNotIn('hunter2', text)
        self.assertNotIn(KEY, text)
        self.assertIn('[redacted', text)
        self.assertIn('PASS: ok', text)
        self.assertGreaterEqual(n, 2)

    def test_a_transcript_stays_valid_after_redaction_and_the_scan_shows_no_value(self):
        got = run(PRE + f'import kb_redact as R, kb_secret_scan as S\nd = paths.brain("sessions"); os.makedirs(d, exist_ok=True)\n'
                  f'open(d / "a.md", "w").write("the key is {KEY}\\nand nothing else\\n")\n'
                  'arch = paths.brain("raw_archive", "claude_projects", "p"); os.makedirs(arch, exist_ok=True)\n'
                  f'open(arch / "c.jsonl", "w").write(json.dumps({{"type": "user", "message": {{"content": "use {CONN}"}}}}) + "\\n")\n'
                  'files, n, hits = S.scan()\nbefore = [[h[2], h[3]] for h in hits]\nR.run()\n'
                  'rec = json.loads(open(arch / "c.jsonl").read().strip())\n'
                  'print(json.dumps([before, S.scan()[2], rec["message"]["content"], open(d / "a.md").read()]))', self.home)
        before, after, chat, note = got
        self.assertTrue(before and all(KEY not in m for _, m in before))  # the scan names the pattern and masks the match
        self.assertIn('[MASKED', before[0][1])
        self.assertEqual(after, [])                                       # nothing left after redaction
        self.assertNotIn('hunter2', chat)
        self.assertIn('db.example.org', chat)
        self.assertNotIn(KEY, note)

    def test_whole_copies_of_secrets_are_kept_only_when_asked(self):
        make = (f'import kb_secrets_vault as V\nd = paths.brain("sessions"); os.makedirs(d, exist_ok=True)\n'
                f'open(d / "a.md", "w").write("the key is {KEY}\\n")\n')
        off = run(PRE + make + 'V.build()\nprint(json.dumps([sorted(os.listdir(paths.secrets())), open(d / "a.md").read()]))', self.home)
        self.assertEqual(off[0], ['SECRETS_MAP.md'])                       # the map (kinds, never values), no copy
        self.assertNotIn(KEY, off[1])
        self.assertNotIn(KEY, (self.home / 'state' / 'secrets' / 'SECRETS_MAP.md').read_text())
        home2 = pathlib.Path(self.tmp.name) / 'home2'; home2.mkdir()
        on = run(PRE + 'config.set("knowledge.keep_secrets", True)\n' + make + 'V.build()\n'
                 'kept = paths.secrets("sessions", "a.md")\nprint(json.dumps([os.path.exists(kept), oct(os.stat(kept).st_mode & 0o777), open(d / "a.md").read()]))', home2)
        self.assertEqual(on[:2], [True, '0o600'])
        self.assertNotIn(KEY, on[2])                                       # the knowledge base's own copy is redacted either way

    def test_only_chats_from_the_work_folders_are_read_and_secrets_are_blanked(self):
        claude = pathlib.Path(self.tmp.name) / 'claude'
        work = pathlib.Path(self.tmp.name) / 'Work Stuff'; work.mkdir()
        other = pathlib.Path(self.tmp.name) / 'Private'; other.mkdir()
        enc = lambda p: ''.join(ch if ch.isalnum() else '-' for ch in str(p))
        for i, (folder, text) in enumerate(((work, f'please check the March export, {PW}="{"q" * 14}"'), (work / 'sub', 'a chat started inside the work folder'),
                                            (other, 'my holiday plans'), (self.home, 'one of the assistant\'s own runs'))):
            d = claude / 'projects' / enc(folder); d.mkdir(parents=True)
            (d / f'abcd{i}234-0000.jsonl').write_text(json.dumps({'type': 'user', 'timestamp': '2026-03-04T10:00:00Z', 'message': {'role': 'user', 'content': text}}) + '\n' +
                                              json.dumps({'type': 'assistant', 'timestamp': '2026-03-04T10:00:05Z', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Done.'}]}}) + '\n')
        got = run(PRE + f'config.set("work.folders", [{str(work)!r}])\nprint(json.dumps([d.name for d in paths.work_chat_dirs()]))', self.home, CLAUDE_CONFIG_DIR=str(claude))
        self.assertEqual(sorted(got), sorted([enc(work), enc(work / 'sub')]))
        out = pathlib.Path(self.tmp.name) / 'out'
        e = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_')}
        e.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(claude), PYTHONDONTWRITEBYTECODE='1')
        p = subprocess.run([sys.executable, str(REPO / 'tools' / 'extract_sessions.py'), '--out', str(out)], capture_output=True, text=True, env=e, cwd=self.tmp.name, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        texts = '\n'.join(f.read_text() for f in sorted(out.glob('*.txt')))
        self.assertEqual(len(list(out.glob('*.txt'))), 2)
        self.assertIn('March export', texts)
        self.assertIn('started inside the work folder', texts)
        self.assertNotIn('holiday', texts)
        self.assertNotIn("assistant's own runs", texts)
        self.assertNotIn('q' * 14, texts)                                  # the secret was blanked before anything was written


if __name__ == '__main__':
    unittest.main()
