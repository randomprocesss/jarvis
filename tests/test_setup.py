"""The Setup screen's checks and suggestions, on a new data home with a stand-in for Claude Code. No network, no model."""
import json, os, stat, sys, tempfile, unittest
from pathlib import Path

STUB = r'''
import json, sys
a = sys.argv[1:]
if a[:1] == ['--version']:
    print('9.9.9 (Claude Code)')
elif a[:2] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max', 'email': 'sam@example.com', 'orgName': 'Example Org'}))
elif '--json-schema' in a:
    sites = [{'id': '-'.join(['1a2b3c4d', '0000', '4000', '8000', '00000000abcd']), 'name': 'acme', 'url': 'https://acme.example.com'}]
    print(json.dumps({'type': 'result', 'structured_output': {'sites': sites}, 'total_cost_usd': 0.01}))
elif '-p' in a:
    print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's1', 'mcp_servers': [
        {'name': 'claude.ai Slack', 'status': 'connected'}, {'name': 'claude.ai Atlassian', 'status': 'needs-auth'}, {'name': 'notes', 'status': 'connected'}]}))
    print(json.dumps({'type': 'result', 'result': 'ready', 'is_error': False}))
'''


SITE = '-'.join(['1a2b3c4d', '0000', '4000', '8000', '00000000abcd'])      # made up; written in parts so it is no long id in the file


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(os.path.realpath(self.tmp.name))
        self.home, self.claude, self.work = t / 'home', t / 'claude', t / 'My-Work' / 'Atlas'
        self.work.mkdir(parents=True)
        stub = t / 'claude-stub'
        stub.write_text('#!' + sys.executable + '\n' + STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.keep = {k: os.environ.get(k) for k in ('JARVIS_HOME', 'CLAUDE_CONFIG_DIR', 'JARVIS_CLAUDE')}
        os.environ.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude), JARVIS_CLAUDE=str(stub))
        from jarvis import config, paths, setup
        self.config, self.paths, self.setup = config, paths, setup
        setup.forget()
        chats = paths.claude_chats_for(self.work)
        chats.mkdir(parents=True)
        for i in range(3):
            (chats / f'chat{i}.jsonl').write_text('{"type":"user"}\n')

    def tearDown(self):
        self.setup.forget()
        for k, v in self.keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def step(self, sid, st=None):
        return next(s for s in (st or self.setup.state())['steps'] if s['id'] == sid)

    def test_a_new_home_opens_on_you_and_says_what_is_there(self):
        st = self.setup.state()
        self.assertFalse(st['set_up'])
        self.assertEqual(st['next'], 'you')
        self.assertEqual([s['id'] for s in st['steps']], ['you', 'claude', 'sources', 'folders', 'budget', 'learn', 'optional', 'trust'])
        cl = self.step('claude', st)
        self.assertTrue(cl['claude']['found'] and cl['claude']['signed_in'])
        self.assertFalse(cl['done'])                                        # signed in, but it has not been seen to answer yet
        self.assertEqual(self.step('sources', st)['chats']['found'], 3)
        self.assertNotIn('sam@example.com', json.dumps(st))                 # the account's address never reaches the screen
        self.assertNotIn('Example Org', json.dumps(st))
        self.assertEqual(self.step('trust', st)['status'], 'Every kind of action waits for your press.')

    def test_trust_reads_the_ladder_once_the_ledger_exists(self):
        from jarvis.spine import ledger
        ledger.connect().close()                                             # as on a running server
        tr = self.step('trust')['trust']
        self.assertGreaterEqual(tr['kinds'], 5)
        self.assertEqual(tr['raised'], [])
        self.assertEqual(tr['line'], 'Every kind of action waits for your press.')

    def test_the_folder_behind_a_chat_folder_name_is_found_on_disk(self):
        name = self.paths.claude_chats_for(self.work).name
        self.assertIn('-My-Work-Atlas', name)                               # the dash in "My-Work" makes the name ambiguous
        self.assertEqual(self.setup.decode_chat_folder(name), str(self.work))
        self.assertEqual(self.setup.decode_chat_folder(name + '-gone'), '')

    def test_suggestions_come_from_what_is_on_the_computer_and_can_be_hidden(self):
        ids = {s['id']: s for s in self.setup.suggestions()}
        folder = next(s for i, s in ids.items() if i.startswith('folder:'))
        self.assertIn('3 of your Claude Code chats', folder['why'])
        self.assertIn('check', ids)
        self.assertNotIn('learn', ids)                                      # no work folder yet, so no chat is in scope
        body, code = self.setup.save({'set': folder['save']})
        self.assertEqual(code, 200)
        ids = {s['id'] for s in self.setup.suggestions()}
        self.assertIn('learn', ids)
        self.assertFalse(any(i.startswith('folder:') for i in ids))
        self.assertEqual(self.setup.dismiss({'id': 'learn'})[1], 200)
        self.assertNotIn('learn', {s['id'] for s in self.setup.suggestions()})

    def test_save_checks_every_value_before_writing_any(self):
        body, code = self.setup.save({'set': {'owner.name': 'Sam Lee', 'work.hours': 'nine to five'}})
        self.assertEqual(code, 400)
        self.assertIn('work.hours', body['error'])
        self.assertFalse(self.config.load().is_set_up())                    # the good value was not saved either
        self.assertEqual(self.setup.save({'set': {'server.port': 9999}})[1], 400)
        body, code = self.setup.save({'set': {'owner.name': 'Sam Lee', 'work.hours': '08:00-16:00'}})
        self.assertEqual(code, 200)
        self.assertTrue(body['state']['set_up'])
        self.assertEqual(body['state']['next'], 'claude')
        self.assertEqual(self.config.load().get('work.hours'), '08:00-16:00')

    def test_what_learning_reads_from_the_apps_is_named_on_setup(self):
        src = self.step('sources')
        self.assertEqual(src['reads']['values']['sources.tracker_projects'], [])
        self.assertEqual(self.setup.save({'set': {'sources.since': 'last year'}})[1], 400)
        body, code = self.setup.save({'set': {'sources.tracker_projects': ['ABC'], 'sources.channels': ['C0123ABCD orders-team'], 'sources.since': '2026-01-01'}})
        self.assertEqual(code, 200)
        r = self.step('sources', body['state'])['reads']
        self.assertEqual((r['values']['sources.tracker_projects'], r['values']['sources.since'], r['site']), (['ABC'], '2026-01-01', ''))

    def test_background_work_waits_until_claude_answered_and_an_app_is_connected(self):
        # The new-user trial (14 Nov): the first round-up started a minute after install, with Claude signed out.
        import time
        from jarvis.spine import heartbeat
        self.assertEqual(self.setup.ready(), {'claude': False, 'apps': False})
        started = []
        real = (heartbeat.dispatcher.dispatch, heartbeat.ledger.event)
        try:   # only the round-up decision is under test: the queue and the record are left out
            heartbeat.dispatcher.dispatch, heartbeat.ledger.event = (lambda: None), (lambda *a, **k: None)
            hb = heartbeat.Heartbeat(lambda: 0, lambda what: started.append(what) or {'state': 'started'}, None, lambda: False)
            hb.items_v = 0
            hb.inbox.tick = lambda: 'cadence'                      # the inbox watcher says a round-up is due
            quiet = lambda: hb.last.update({k: time.time() for k in ('incidents', 'autopilot', 'health', 'approvals')})
            quiet(); hb.tick()
            self.assertEqual(started, [])
            self.assertTrue(hb.status['waiting_for_setup'])
            body, code = self.setup.check({'what': 'claude'})       # the stand-in answers and reports Slack connected
            self.assertEqual(code, 200)
            self.assertEqual(self.setup.ready(), {'claude': True, 'apps': True})
            hb._ready_at = 0                                         # read again at once (normally once a minute)
            hb.last.pop('inbox', None)
            quiet(); hb.tick()
            self.assertEqual(started, ['roundup'])
            self.assertFalse(hb.status['waiting_for_setup'])
        finally:
            heartbeat.dispatcher.dispatch, heartbeat.ledger.event = real

    def test_the_check_proves_claude_answers_and_finds_the_connected_apps(self):
        body, code = self.setup.check({'what': 'claude'})
        self.assertEqual(code, 200)
        self.assertTrue(body['result']['ok'])
        self.assertEqual(body['result']['expected_set'], ['claude.ai Slack'])
        self.assertEqual(self.config.load().get('sources.expected'), ['claude.ai Slack'])
        st = body['state']
        self.assertTrue(self.step('claude', st)['done'])
        rows = {r['id']: r for r in self.step('sources', st)['apps']['rows']}
        self.assertEqual((rows['slack']['state'], rows['atlassian']['state'], rows['microsoft_365']['state']), ('connected', 'needs-auth', 'not connected'))
        self.assertTrue(rows['slack']['used'])
        self.assertEqual(self.step('sources', st)['apps']['other'], ['notes'])
        ids = {s['id'] for s in st['suggestions']}
        self.assertNotIn('check', ids)
        self.assertIn('connect:atlassian', ids)

    def test_the_atlassian_site_is_found_and_kept(self):
        body, code = self.setup.find_site({})
        self.assertEqual(code, 200, body)
        self.assertEqual(body['chosen'], SITE)
        c = self.config.load()
        self.assertEqual((c.get('tracker.cloud_id'), c.get('links.ticket_base')), (SITE, 'https://acme.example.com/browse/'))
        self.assertEqual(self.step('sources', body['state'])['reads']['site'], SITE)

    def test_learning_starts_only_when_there_is_something_to_learn_from(self):
        body, code = self.setup.learn({})
        self.assertEqual(code, 400)
        self.assertIn('no Claude Code chats in your work folders', body['error'])
        self.assertFalse(self.setup.learning()['running'])

    def test_a_pass_that_stops_says_so_and_can_be_started_again(self):
        self.paths.use_tools()
        sys.modules.pop('learn_first', None)
        import learn_first
        learn_first.STEPS = [('reading your chats', ['no_such_tool.py'])]
        self.assertEqual(learn_first.main(), 1)
        lr = self.setup.learning()
        self.assertFalse(lr['running'])
        self.assertIs(lr['ok'], False)
        self.assertIn('Press Start learning to carry on', lr['error'])
        sys.modules.pop('learn_first', None)


if __name__ == '__main__':
    unittest.main()
