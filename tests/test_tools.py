"""The runtime tools a worker or the server calls by path: each finds the package from its own place and keeps the
owner's data in the data home."""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
T = REPO / 'tools'


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k != 'PYTHONPATH'}
        self.env.update(JARVIS_HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, name, *args, stdin='', **env):
        return subprocess.run([sys.executable, str(T / name), *args], input=stdin, capture_output=True, text=True,
                              env=dict(self.env, **env), cwd=self.tmp.name, timeout=120)

    def test_locks_one_run_per_item(self):
        self.assertEqual(self.tool('worklock.py', 'claim', 'r1', 'a1', '--by', 'first').returncode, 0)
        self.assertEqual(self.tool('worklock.py', 'claim', 'r1', 'a1', '--by', 'second').returncode, 1)
        self.assertEqual(self.tool('worklock.py', 'claim', 'r1', 'a2', '--by', 'second').returncode, 0)
        self.assertEqual(self.tool('worklock.py', 'release', 'r1', 'a1').returncode, 0)
        self.assertEqual(self.tool('worklock.py', 'claim', 'r1', 'a1', '--by', 'second').returncode, 0)
        self.assertTrue((self.home / 'work' / 'locks').is_dir())                 # the claims are the owner's data

    def test_a_long_job_runs_and_its_check_decides(self):
        for name, check, want in (('good', 'import sys; sys.exit(0)', 'done'), ('bad', 'print("3 cells failed"); raise SystemExit(1)', 'failed')):
            d = self.home / 'work' / 'outputs' / f'job-{name}'
            d.mkdir(parents=True)
            (d / 'check.py').write_text(check)
            p = self.tool('run_job.py', 'start', str(d), '--', sys.executable, '-c', 'print(6 * 7)', JARVIS_JOBS_PYTHON=sys.executable)
            self.assertEqual(p.returncode, 0, p.stderr[-600:])
            state = {}
            for _ in range(150):
                state = json.loads((d / 'job.json').read_text())
                if state.get('state') in ('done', 'failed'):
                    break
                time.sleep(0.2)
            self.assertEqual(state.get('state'), want, state)
            self.assertIn('42', (d / 'run.log').read_text())
        refused = self.tool('run_job.py', 'start', self.tmp.name, '--', 'true')        # a folder outside the data home's outputs
        self.assertNotEqual(refused.returncode, 0)

    def test_the_forge_keeps_tools_with_the_owners_data(self):
        p = self.tool('forge.py', 'list')
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        src = (T / 'forge.py').read_text()
        self.assertIn("os.path.join(HUB, 'state', 'forge')", src)
        self.assertNotIn("'git'", src)                                                # nothing of the owner's is committed to the code

    def test_the_stop_hook_only_judges_jarvis_workers_and_never_crashes(self):
        self.assertEqual(self.tool('worker_stop.py', stdin='{}').returncode, 0)                                   # not a Jarvis worker
        self.assertEqual(self.tool('worker_stop.py', stdin='{}', JARVIS_PROC='1', JARVIS_LANE='asked').returncode, 0)   # no task: nothing to judge

    def test_the_stop_hook_sends_back_a_worker_that_left_nothing(self):
        prep = ('from jarvis import paths\npaths.ensure()\nfrom jarvis.spine import ledger\n'
                "ledger.x(\"INSERT INTO tasks(id, lane, state, rid, aid, origin, kind, prompt, started) VALUES('t1','asked','running','2026-03-04-0900-all','a1','owner','item','x', ?)\", (ledger.now(),))\n")
        r = subprocess.run([sys.executable, '-c', prep], env=dict(self.env, PYTHONPATH=str(REPO)), capture_output=True, text=True, cwd=self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        p = self.tool('worker_stop.py', stdin='{}', JARVIS_PROC='1', JARVIS_LANE='asked', JARVIS_TASK_ID='t1')
        self.assertEqual(p.returncode, 2, p.stderr[-400:] + p.stdout[-200:])
        self.assertIn('no work entry', p.stderr)
        self.assertTrue((self.home / 'state' / 'stopchecks' / 't1.json').exists())

    def test_the_gate_refuses_a_candidate_that_does_not_parse(self):
        cand = pathlib.Path(self.tmp.name) / 'candidate'
        shutil.copytree(REPO / 'jarvis', cand / 'jarvis', ignore=shutil.ignore_patterns('__pycache__'))
        (cand / 'tools').mkdir()
        (cand / 'jarvis' / 'learned.py').write_text('def broken(:\n')
        p = self.tool('gate.py', '--repo', str(cand), '--json')
        self.assertEqual(p.returncode, 1)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(r['failed'], ['compile'])
        self.assertIn('learned.py', r['detail']['compile'])


if __name__ == '__main__':
    unittest.main()
