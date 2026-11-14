"""The spine on an empty data home: every module loads, nothing is written outside the data home, and what used to be
fixed for one person now comes from settings."""
import importlib, json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
SPINE = sorted(p.stem for p in (REPO / 'jarvis' / 'spine').glob('*.py') if p.stem != '__init__')


def run(code, home, **env):
    """Run `code` in a fresh Python with its own data home; returns the last line it printed, read as JSON."""
    e = dict(os.environ, JARVIS_HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1', **env)
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, cwd=str(home), timeout=120)
    if p.returncode != 0:
        raise AssertionError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


class SpineOnEmptyHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_module_loads(self):
        got = run('import importlib, json\n'
                  f'names = {SPINE!r}\n'
                  'for n in names: importlib.import_module("jarvis.spine." + n)\n'
                  'print(json.dumps(names))', self.home)
        self.assertEqual(got, SPINE)
        self.assertGreaterEqual(len(SPINE), 12)

    def test_page_origin_follows_the_port_the_server_listens_on(self):
        # Found in the new-user trial (14 Nov): the origin was fixed at 8765, so on any other port every send, approval
        # and write was refused as "not from the Jarvis page", the Mac app's window included.
        got = run('import json\n'
                  'from jarvis.spine import gateway\n'
                  'from jarvis import assistant\n'
                  'h = lambda o: {"Origin": o}\n'
                  'before = gateway.from_page(h("http://localhost:8765"))\n'
                  'gateway.set_port(8793)\n'
                  'print(json.dumps([before, gateway.from_page(h("http://localhost:8793")), gateway.from_page(h("http://127.0.0.1:8793")),\n'
                  '    gateway.from_page({"Referer": "http://localhost:8793/#/inbox"}), gateway.from_page(h("http://localhost:8765")),\n'
                  '    gateway.from_page(h("http://localhost:87930")), gateway.from_page({"Referer": "http://localhost:8793.evil/"}),\n'
                  '    gateway.from_page({}), assistant.origin_ok(h("http://localhost:8793"))]))', self.home)
        self.assertEqual(got, [True, True, True, True, False, False, False, False, True])

    def test_loading_writes_nothing_into_the_code(self):
        before = sorted(str(p) for p in REPO.rglob('*') if '.git' not in p.parts and '__pycache__' not in p.parts)
        run('import importlib, json\n'
            f'[importlib.import_module("jarvis.spine." + n) for n in {SPINE!r}]\n'
            'print(json.dumps(1))', self.home)
        after = sorted(str(p) for p in REPO.rglob('*') if '.git' not in p.parts and '__pycache__' not in p.parts)
        self.assertEqual(before, after)

    def test_worker_settings_name_no_person(self):
        s = run('import json\nfrom jarvis import paths\nfrom jarvis.spine import dispatcher\npaths.ensure()\n'
                'dispatcher.write_settings()\nprint(json.dumps(json.load(open(dispatcher.SETTINGS))))', self.home)
        deny = s['permissions']['deny']
        self.assertTrue(any('.ssh' in r for r in deny))
        self.assertTrue(any(r.startswith('Edit(') and r.endswith('/state/**)') for r in deny), deny[:6])
        hook = s['hooks']['PreToolUse'][0]['hooks'][0]['command']
        self.assertIn('worker_hook.py', hook)
        self.assertIn(str(REPO / 'tools'), hook)                 # the hook is code: it runs from the repository
        self.assertNotIn(str(self.home), hook)

    def test_never_read_list_from_settings_reaches_the_workers(self):
        s = run('import json\nfrom jarvis import config, paths\nfrom jarvis.spine import dispatcher\npaths.ensure()\n'
                'config.set("work.never_read", ["~/Private/**"])\ndispatcher.write_settings()\n'
                'print(json.dumps(json.load(open(dispatcher.SETTINGS))["permissions"]["deny"]))', self.home)
        self.assertIn('Read(~/Private/**)', s)

    def test_workers_run_in_the_owners_folder(self):
        proj = pathlib.Path(self.tmp.name) / 'My Project'
        proj.mkdir()
        got = run('import json\nfrom jarvis import config, paths\nfrom jarvis.spine import dispatcher\npaths.ensure()\n'
                  'a = dispatcher._workdir()\n'
                  f'config.set("work.folders", [{str(proj)!r}])\n'
                  'print(json.dumps([a, dispatcher._workdir(), dispatcher._transcripts()]))', self.home)
        self.assertEqual(got[0], str(self.home / 'work'))        # nothing chosen yet: the data home's work folder
        self.assertEqual(got[1], str(proj))
        self.assertTrue(got[2].endswith('-My-Project'), got[2])

    def test_number_work_words_come_from_settings(self):
        got = run('import json, os\nfrom jarvis import config, paths\nfrom jarvis.spine import dispatcher\npaths.ensure()\n'
                  'os.makedirs(paths.roundups(), exist_ok=True)\n'
                  'json.dump({"actions": [{"id": "a1", "title": "check the zorbtable against the qledger for March", "text": ""}]}, open(paths.roundups("r1.json"), "w"))\n'
                  'a = [p for p, _ in dispatcher.route("r1", "a1")]\n'
                  'config.set("work.analyst_words", ["zorbtable", "qledger"])\n'
                  'print(json.dumps([a, [p for p, _ in dispatcher.route("r1", "a1")]]))', self.home)
        self.assertNotIn('analyst', got[0])
        self.assertIn('analyst', got[1])

    def test_no_ticket_comment_goes_out_until_a_project_is_listed(self):
        got = run('import json\nfrom jarvis import config, paths\nfrom jarvis.spine import policy\npaths.ensure()\n'
                  'a = policy._own_ticket("ABC-12")\nconfig.set("tracker.own_projects", ["abc"])\n'
                  'print(json.dumps([a, policy._own_ticket("ABC-12"), policy._own_ticket("XYZ-1"), policy._own_ticket("ABC-12; DROP")]))', self.home)
        self.assertEqual(got, [False, True, False, False])

    def test_changing_its_own_code_is_off_until_turned_on(self):
        got = run('import json\nfrom jarvis import config, paths\nfrom jarvis.spine import autopilot\npaths.ensure()\n'
                  'a = autopilot.code_changes_on()\nconfig.set("modules.self_improve", True)\n'
                  'print(json.dumps([a, autopilot.code_changes_on()]))', self.home)
        self.assertEqual(got, [False, True])

    def test_the_optimiser_may_not_touch_its_judges(self):
        from jarvis.spine import autopilot
        for f in ('jarvis/spine/policy.py', 'jarvis/spine/gateway.py', 'jarvis/proof.py', 'jarvis/ladder.py', 'jarvis/config.py',
                  'jarvis/learned.py', 'jarvis/spine/autopilot.py', 'jarvis/spine/speech_guard.py', 'jarvis/spine/ledger.py'):
            self.assertTrue(autopilot.ALLOWED.match(f) and f in autopilot.PROTECTED, f)
        for f in ('jarvis/assistant.py', 'jarvis/spine/limits.py'):                # what it tunes stays open
            self.assertTrue(autopilot.ALLOWED.match(f) and f not in autopilot.PROTECTED, f)
        self.assertFalse(autopilot.ALLOWED.match('tools/gate.py'))
        self.assertFalse(autopilot.ALLOWED.match('tests/test_spine.py'))

    def test_a_worker_is_denied_everything_the_ship_check_protects(self):
        s = run('import json\nfrom jarvis import paths\nfrom jarvis.spine import autopilot, dispatcher\npaths.ensure()\n'
                'deny = dispatcher.worker_settings()["permissions"]["deny"]\n'
                'roots = (dispatcher._rule(paths.REPO), dispatcher._rule(paths.worktree("ap-*")))\n'
                'want = [f"Edit({r}/{f})" for r in roots for f in sorted(autopilot.PROTECTED) + list(autopilot.JUDGES)]\n'
                'print(json.dumps({"missing": [w for w in want if w not in deny], "n": len(want), "tests": any("/tests/**)" in x for x in deny)}))', self.home)
        self.assertEqual(s['missing'], [])
        self.assertGreaterEqual(s['n'], 2 * (len(__import__('jarvis.spine.autopilot', fromlist=['PROTECTED']).PROTECTED) + 8))
        self.assertTrue(s['tests'])                                               # the tests themselves are judges

    def test_health_runs_with_nothing_set_up(self):
        got = run('import json\nfrom jarvis import paths\nfrom jarvis.spine import health\npaths.ensure()\n'
                  'd = health.deep()\nprint(json.dumps({"keys": sorted(d), "problems": [p["key"] for p in d["problems"]]}))', self.home)
        self.assertIn('logins', got['keys'])
        self.assertNotIn('vpn', got['keys'])
        self.assertEqual([p for p in got['problems'] if p.startswith(('gcloud', 'connectors'))], [])


if __name__ == '__main__':
    unittest.main()
