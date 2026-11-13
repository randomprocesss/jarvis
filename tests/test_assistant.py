"""The assistant is written for whoever owns this copy: its instructions carry their name, their work in their words and
the apps they have, and nothing of anyone else; its tools are read-only."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
sys.path.insert(0, str(REPO))
from jarvis import texts


def run(code, home):
    e = dict(os.environ, JARVIS_HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, cwd=str(home), timeout=120)
    if p.returncode != 0:
        raise AssertionError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


SETUP = ('import json, os, time\nfrom jarvis import config, paths\npaths.ensure()\n'
         'def touch():\n    t = time.time() + 5\n    os.utime(paths.config_file(), (t, t))\n')


class Templates(unittest.TestCase):
    def test_blocks_and_values(self):
        t = 'a [[if x]]X [[if y]]Y[[end]] Z[[end]] {{n}}'
        self.assertEqual(texts.render_text(t, x=1, y=0, n='N'), 'a X  Z N')
        self.assertEqual(texts.render_text(t, x=1, y=1, n='N'), 'a X Y Z N')
        self.assertEqual(texts.render_text(t, x=0, y=1, n='N'), 'a  N')

    def test_a_missing_value_is_an_error_not_a_hole(self):
        with self.assertRaises(KeyError):
            texts.render_text('hello {{name}}')
        with self.assertRaises(ValueError):
            texts.render_text('[[if a]]never closed', a=1)

    def test_shipped_templates_assume_no_gender_and_no_person(self):
        for f in sorted((REPO / 'jarvis' / 'texts').glob('*.md')):
            words = set(f.read_text(encoding='utf-8').lower().replace('"', ' ').replace('(', ' ').split())
            self.assertFalse(words & {'he', 'his', 'him', 'she', 'her', 'sir'}, f.name)


class Assistant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_prompt_before_setup_is_whole(self):
        p = run(SETUP + 'from jarvis import assistant as A\nprint(json.dumps(A.system_prompt(False)))', self.home)
        self.assertIn("the owner's work assistant", p)
        self.assertIn('none is connected yet', p)
        for hole in ('{{', '}}', '[[if', '[[end'):
            self.assertNotIn(hole, p)

    def test_prompt_is_about_this_owner(self):
        p = run(SETUP + 'config.set("owner.name", "Sam Lee"); config.set("owner.address_as", "boss")\n'
                'config.set("work.about", "product design at a small studio"); config.set("work.projects", ["Atlas"])\n'
                'config.set("sources.expected", ["claude.ai Slack"]); touch()\n'
                'from jarvis import assistant as A\nprint(json.dumps([A.system_prompt(False), A.system_prompt(True)]))', self.home)
        slow, fast = p
        for want in ("Sam Lee's work assistant", '"boss"', 'product design at a small studio', 'projects: Atlas', 'HOW SAM LEE THINKS', 'WHAT EXISTS'):
            self.assertIn(want, slow)
        for absent in ('THEIR CALENDAR', 'post_jira', 'cloudId', 'OneDrive'):      # apps this owner does not have
            self.assertNotIn(absent, slow)
        self.assertIn('SPEED: this is live speech', fast)
        self.assertNotIn('SPEED: this is live speech', slow)

    def test_prompt_follows_a_change_of_settings(self):
        got = run(SETUP + 'from jarvis import assistant as A\na = "THEIR CALENDAR" in A.system_prompt(False)\n'
                  'config.set("sources.expected", ["claude.ai Microsoft 365", "claude.ai Atlassian Rovo"]); config.set("tracker.cloud_id", "abc-123"); touch()\n'
                  'p = A.system_prompt(False)\nprint(json.dumps([a, "THEIR CALENDAR" in p, "post_jira" in p, "cloudId abc-123" in p]))', self.home)
        self.assertEqual(got, [False, True, True, True])

    def test_tools_only_read(self):
        got = run(SETUP + 'config.set("sources.read_tools", ["mcp__mine__search", "mcp__claude_ai_Slack__slack_send_message", "Bash", "rm -rf"])\n'
                  'from jarvis import assistant as A\nprint(json.dumps({"read": A.read_tools(), "deny": A.deny_tools()}))', self.home)
        read, deny = got['read'], got['deny']
        self.assertIn('mcp__mine__search', read)                     # their own tool server's read tool, as listed
        self.assertIn('mcp__jarvis__inbox_find', read)
        for bad in ('mcp__claude_ai_Slack__slack_send_message', 'Bash', 'rm -rf', 'Edit', 'Write'):
            self.assertNotIn(bad, read)
        self.assertFalse(set(read) & set(deny))
        for t in ('Bash', 'Edit', 'Write', 'mcp__claude_ai_Slack__slack_send_message', 'mcp__claude_ai_Microsoft_365__outlook_send_mail'):
            self.assertIn(t, deny)
        self.assertTrue(any(t.endswith('/state/page.key*)') for t in deny))

    def test_connected_apps_can_be_switched_off_whole(self):
        got = run(SETUP + 'config.set("sources.claude_connectors", False)\nfrom jarvis import assistant as A\n'
                  'print(json.dumps([t for t in A.read_tools() if "claude_ai" in t]))', self.home)
        self.assertEqual(got, [])

    def test_local_tool_server_file_is_written_for_this_computer(self):
        got = run(SETUP + 'from jarvis import assistant as A\nf = A.mcp_configs()\nprint(json.dumps([f, json.load(open(f[0]))]))', self.home)
        files, d = got
        self.assertEqual(files, [str(self.home / 'state' / 'mcp.json')])
        s = d['mcpServers']['jarvis']
        self.assertEqual(s['args'], ['-m', 'jarvis.mcp'])
        self.assertEqual(s['env'], {'PYTHONPATH': str(REPO), 'JARVIS_HOME': str(self.home)})

    def test_weekly_hours_is_not_an_action_any_more(self):
        got = run(SETUP + 'from jarvis import assistant as A\n'
                  'print(json.dumps([A.valid_action({"type": "week_hours", "hours": 40}), A.valid_action({"type": "open", "route": "close"}),'
                  ' A.valid_action({"type": "open", "route": "inbox"})]))', self.home)
        self.assertEqual(got, [None, None, {'type': 'open', 'route': 'inbox'}])


if __name__ == '__main__':
    unittest.main()
