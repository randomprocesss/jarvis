"""Running Jarvis on an API key instead of a Claude plan: the key's keeping, the launcher, what each run cost, and the
daily limit. A made-up key in a test file; a stand-in for Claude Code. The real password store is never touched."""
import datetime as dt, json, os, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

KEY = 'sk-ant-test-' + 'x' * 40                       # made up: the shape of a key, nothing more
STUB = r'''
import json, os, sys
a = sys.argv[1:]
key = os.environ.get('ANTHROPIC_API_KEY', '')
if a[:1] == ['--version']:
    print('9.9.9 (Claude Code)')
elif a[:2] == ['auth', 'status']:
    print(json.dumps({'loggedIn': bool(key), 'authMethod': 'api_key' if key else 'none'}))
elif '-p' in a:
    seen = {'key_in_env': key.startswith('sk-ant-'), 'key_in_args': any(key and key in x for x in a), 'stdin': sys.stdin.read() if '--read-stdin' in a else ''}
    fmt = a[a.index('--output-format') + 1] if '--output-format' in a else 'text'
    if fmt == 'text':
        print('ready')
    else:
        print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's1', 'mcp_servers': []}))
        print(json.dumps({'type': 'result', 'result': 'ready', 'is_error': False, 'total_cost_usd': float(os.environ.get('STUB_COST', '0.25')), 'seen': seen}))
    sys.exit(int(os.environ.get('STUB_EXIT', '0')))
'''


class ApiKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(os.path.realpath(self.tmp.name))
        self.home, self.keyfile = t / 'home', t / 'store' / 'key'
        stub = t / 'claude-stub'
        stub.write_text('#!' + sys.executable + '\n' + STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.keep = {k: os.environ.get(k) for k in ('JARVIS_HOME', 'JARVIS_CLAUDE', 'JARVIS_KEYSTORE', 'ANTHROPIC_API_KEY', 'CLAUDE_CONFIG_DIR')}
        os.environ.update(JARVIS_HOME=str(self.home), JARVIS_CLAUDE=str(stub), JARVIS_KEYSTORE='file:' + str(self.keyfile), CLAUDE_CONFIG_DIR=str(t / 'claude'))
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from jarvis import config, paths, keystore, claude_run, setup
        from jarvis.spine import governor
        self.config, self.paths, self.keystore, self.run_, self.setup, self.gov = config, paths, keystore, claude_run, setup, governor
        paths.ensure()
        config.set('owner.name', 'Sam Lee')
        config.set('engine.mode', 'api-key')
        config.set('engine.daily_cap_usd', 1.0)
        setup.forget()
        self.stub = str(stub)

    def tearDown(self):
        self.setup.forget()
        for k, v in self.keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def launch(self, *args, stdin='', **env):
        exe = self.paths.claude_bin()
        return subprocess.run([exe, *args], input=stdin, capture_output=True, text=True, env=dict(os.environ, **env), timeout=60)

    def test_on_a_plan_claude_is_started_as_it_is(self):
        self.config.set('engine.mode', 'claude-plan')
        self.assertEqual(self.paths.claude_bin(), self.stub)

    def test_a_key_of_the_wrong_shape_is_refused_and_the_key_is_never_shown(self):
        body, code = self.setup.key({'key': 'not-a-key'})
        self.assertEqual(code, 400)
        self.assertIn('sk-ant-', body['error'])
        self.assertFalse(self.keyfile.exists())
        body, code = self.setup.key({'key': KEY})
        self.assertEqual(code, 200)
        self.assertNotIn(KEY, json.dumps(body))
        self.assertEqual(oct(self.keyfile.stat().st_mode & 0o777), '0o600')
        cl = next(s for s in self.setup.state()['steps'] if s['id'] == 'claude')['claude']
        self.assertTrue(cl['key'] and cl['signed_in'])
        self.assertNotIn(KEY, json.dumps(self.setup.state()))
        self.assertEqual(self.setup.key({'remove': True})[1], 200)
        self.assertFalse(self.keystore.has())

    def test_without_a_key_the_launcher_says_what_to_do(self):
        r = self.launch('-p', 'hi', '--output-format', 'json')
        self.assertEqual(r.returncode, 78)
        self.assertIn('no API key is set', r.stderr)

    def test_the_launcher_passes_everything_through_and_notes_the_cost(self):
        self.keystore.put(KEY)
        launcher = self.paths.claude_bin()
        self.assertTrue(launcher.startswith(str(self.home)))
        self.assertNotIn(KEY, Path(launcher).read_text())                         # the key is in no file of Jarvis's
        r = self.launch('-p', 'hi', '--output-format', 'stream-json', '--model', 'claude-haiku-5-5', STUB_COST='0.125')
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual([l['type'] for l in lines], ['system', 'result'])         # the output is Claude's, unchanged
        seen = lines[-1]['seen']
        self.assertTrue(seen['key_in_env'])
        self.assertFalse(seen['key_in_args'])                                     # never on a command line
        r = self.launch('-p', 'hi', '--output-format', 'json', STUB_COST='0.5', STUB_EXIT='3')
        self.assertEqual(r.returncode, 3)                                         # the exit code is Claude's too
        self.launch('-p', 'hi')                                                    # text output: no cost reported
        s = self.run_.spent_today()
        self.assertEqual((s['usd'], s['runs'], s['unknown']), (0.625, 3, 1))
        rows = [json.loads(l) for l in open(self.home / 'state' / 'spend.jsonl')]
        self.assertEqual(rows[0]['model'], 'claude-haiku-5-5')
        self.assertNotIn(KEY, (self.home / 'state' / 'spend.jsonl').read_text())

    def test_the_daily_limit_stands_where_the_plan_window_would(self):
        self.keystore.put(KEY)
        w = self.gov.window(may_probe=True)
        self.assertEqual((w['mode'], w['five_hour'], w['status']), ('api-key', 0.0, 'allowed'))
        self.assertEqual(self.gov.admit({'kind': 'item', 'origin': 'inbox'})[0], True)
        self.launch('-p', 'hi', '--output-format', 'json', STUB_COST='0.7')
        w = self.gov.window()
        self.assertEqual((w['five_hour'], w['spent_usd'], w['cap_usd']), (0.7, 0.7, 1.0))
        tomorrow = dt.datetime.fromtimestamp(w['five_hour_reset']).astimezone()
        self.assertEqual((tomorrow.hour, tomorrow.minute), (0, 0))
        ok, why = self.gov.admit({'kind': 'initiative', 'origin': 'initiative'})
        self.assertFalse(ok)                                                      # Jarvis's own checks wait at 60% of the limit
        self.launch('-p', 'hi', '--output-format', 'json', STUB_COST='0.2')
        self.assertEqual(self.gov.window()['status'], 'allowed_warning')
        ok, why = self.gov.admit({'kind': 'item', 'origin': 'inbox'})
        self.assertFalse(ok)                                                      # inbox work waits at 85%
        self.assertEqual(self.gov.admit({'kind': 'item', 'lane': 'asked'})[0], True)       # asked work never waits

    def test_the_check_runs_through_the_key_and_the_apps_are_said_to_be_out_of_reach(self):
        self.keystore.put(KEY)
        body, code = self.setup.check({'what': 'claude'})
        self.assertTrue(body['result']['ok'], body)
        src = next(s for s in body['state']['steps'] if s['id'] == 'sources')
        self.assertTrue(src['apps']['api_key'])
        self.assertIn('out of reach', src['status'])
        self.assertEqual({r['state'] for r in src['apps']['rows']}, {'needs its own tool server'})

    def test_the_key_never_reaches_the_record_of_actions(self):
        from jarvis.spine import gateway, ledger
        ledger.connect().close()
        aid, prev = gateway.begin('/api/setup_key', {'key': KEY}, {})
        gateway.end(aid, '/api/setup_key', {'key': KEY}, {'ok': True}, 200)
        import sqlite3
        con = sqlite3.connect(str(self.home / 'state' / 'jarvis.db'))
        dump = '\n'.join(str(r) for r in con.execute('SELECT payload, hash, receipt FROM actions'))
        con.close()
        self.assertIn('(withheld)', dump)
        self.assertNotIn(KEY, dump)
        self.assertNotIn(KEY[8:30], dump)


if __name__ == '__main__':
    unittest.main()
