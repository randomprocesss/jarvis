"""The world modules (needs, promises, the org watch, the desk, research, investigations) for whoever owns this copy:
they load on an empty data home, their prompts carry that person's details and nobody else's, and a watch with nothing
to read from does not run."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
WORLD = sorted(p.stem for p in (REPO / 'jarvis' / 'world').glob('*.py') if p.stem != '__init__')


def run(code, home):
    e = dict(os.environ, JARVIS_HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1', JARVIS_CLAUDE='/nonexistent/claude')
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, cwd=str(home), timeout=120)
    if p.returncode != 0:
        raise AssertionError(p.stderr[-2500:])
    return json.loads(p.stdout.strip().splitlines()[-1])


SETUP = 'import json\nfrom jarvis import config, paths\npaths.ensure()\n'
SAM = ('config.set("owner.name", "Sam Lee"); config.set("owner.emails", ["sam@example.org"])\n'
       'config.set("work.about", "product design at a small studio"); config.set("work.projects", ["Atlas"])\n')


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_module_loads(self):
        got = run(SETUP + f'import importlib\nfor n in {WORLD!r}: importlib.import_module("jarvis.world." + n)\nprint(json.dumps({WORLD!r}))', self.home)
        self.assertEqual(got, WORLD)
        self.assertGreaterEqual(len(WORLD), 15)

    def test_watches_do_not_run_with_nothing_to_read(self):
        got = run(SETUP + 'from jarvis.world import connectors, org, investigate, research\n'
                  'print(json.dumps([connectors.daily().get("skipped"), org.daily().get("skipped"), investigate.seed_goals(), research.scout(ask=lambda p: 1/0)]))', self.home)
        self.assertEqual(got, ['no connected app to read', 'no app or no work words', [], 'nothing to look for'])

    def test_templates_carry_this_owner(self):
        got = run(SETUP + SAM + 'config.set("sources.expected", ["claude.ai Slack", "claude.ai Atlassian Rovo", "claude.ai Microsoft 365"])\n'
                  'config.set("sources.slack_user_id", "U0TESTTEST"); config.set("tracker.cloud_id", "abc-123"); config.set("tracker.own_projects", ["abc", "xy2"])\n'
                  'from jarvis import texts\nc = config.load()\n'
                  'a = texts.render("connectors", identity=c.identity_line(), today="2026-03-04", since="2026-03-01 09:00", since_day="2026-03-01",'
                  ' tracker=True, slack=True, mail=True, slack_id="U0TESTTEST", email="sam@example.org")\n'
                  'own, words = c.own_projects(), c.search_words()\n'
                  'b = texts.render("org_watch", identity=c.identity_line(), owner=c.owner_or(), work=c.work_clause(), today="2026-03-04", since="s", since_day="2026-03-01",'
                  ' tracker=True, pages=True, files=False, words=", ".join(words), projects_text="project " + " or ".join(own),'
                  ' projects_jql="project in (" + ", ".join(own) + ")", words_cql=" OR ".join(f\'text ~ "{w}"\' for w in words))\n'
                  'print(json.dumps([a, b, c.identity_line()]))', self.home)
        con, org, ident = got
        self.assertEqual(ident, 'Sam Lee (Slack user U0TESTTEST; e-mail sam@example.org; Atlassian cloudId abc-123)')
        self.assertIn('from:<@U0TESTTEST>', con)
        self.assertIn('sender = sam@example.org', con)
        self.assertIn('project in (ABC, XY2)', org)
        self.assertIn('text ~ "Atlas"', org)
        for part in ('- SharePoint and OneDrive:', '- Teams:', '- Meetings since then'):      # files=False: that app's parts are left out
            self.assertNotIn(part, org)
        self.assertIn('- Confluence:', org)
        for t in (con, org):
            for hole in ('{{', '}}', '[[if', '[[end'):
                self.assertNotIn(hole, t)

    def test_prompts_in_code_carry_this_owner(self):
        got = run(SETUP + SAM + 'from jarvis.world import needs, desk, docs, promises, semantic\n'
                  'print(json.dumps([needs._prompt()[:400], desk._who(), docs._prompt("d1 | a | b | c")[:300], promises._prompt("p1")[:200]]))', self.home)
        self.assertIn('the work of Sam Lee (product design at a small studio; projects: Atlas)', got[0])
        self.assertEqual(got[1], 'Sam Lee (product design at a small studio; projects: Atlas)')
        self.assertIn('for the work of Sam Lee', got[2])
        self.assertIn('Sam Lee', got[3])
        self.assertTrue(all('{owner}' not in g and '{work}' not in g and '{who}' not in g for g in got))

    def test_who_the_owner_is_comes_from_settings(self):
        got = run(SETUP + 'from jarvis.world import promises\na = [promises._is_owner("Sam"), promises._is_owner("you"), promises._is_owner("the owner")]\n'
                  + SAM + 'print(json.dumps(a + [promises._is_owner("Sam"), promises._is_owner("Lee, S."), promises._is_owner("Priya Shah"), promises._is_owner("Samuel")]))', self.home)
        self.assertEqual(got, [False, True, True, True, True, False, False])

    def test_research_keeps_project_names_out_of_the_search_prompt(self):
        got = run(SETUP + SAM + 'config.set("research.topics", ["forecasting demand that comes in bursts"])\nfrom jarvis.world import research\n'
                  'seen = []\nresearch.scout(ask=lambda p: (seen.append(p), ([], 0))[1])\nprint(json.dumps(seen))', self.home)
        self.assertEqual(len(got), 1)
        self.assertIn('product design at a small studio', got[0])
        self.assertIn('forecasting demand that comes in bursts', got[0])
        self.assertNotIn('Atlas', got[0])
        self.assertNotIn('Sam Lee', got[0])

    def test_repositories_are_found_in_the_work_folders(self):
        proj = pathlib.Path(self.tmp.name) / 'work'
        (proj / 'app' / '.git').mkdir(parents=True); (proj / 'docs').mkdir()
        got = run(SETUP + f'config.set("work.folders", [{str(proj)!r}])\nfrom jarvis.world import org\nprint(json.dumps(org._repos()))', self.home)
        self.assertEqual(got, [str(proj / 'app')])


if __name__ == '__main__':
    unittest.main()
