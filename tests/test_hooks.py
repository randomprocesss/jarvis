"""The worker hook is the guard on everything a worker does. It must refuse what it should, allow ordinary work, stand
alone (no Jarvis package behind it), and find the same data home as the rest of Jarvis."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
HOOK = REPO / 'tools' / 'worker_hook.py'


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        (self.home / 'state').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, tool, inp, lane='background', proc='1', task=None, **env):
        self.n = getattr(self, 'n', 0) + 1                     # a task of its own per call: refusals in a row belong to one task
        e = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_')}
        e.update(JARVIS_HOME=str(self.home), JARVIS_PROC=proc, JARVIS_LANE=lane, JARVIS_TASK_ID=task or f't{self.n}', **env)
        e.pop('PYTHONPATH', None)                              # the hook gets no help finding the package: it must not need it
        p = subprocess.run([sys.executable, '-I', str(HOOK), 'pre'], input=json.dumps({'tool_name': tool, 'tool_input': inp}),
                           capture_output=True, text=True, env=e, cwd=self.tmp.name, timeout=30)
        return p.returncode, p.stderr.strip(), p.stdout.strip()

    def refused(self, tool, inp, **kw):
        code, err, out = self.call(tool, inp, **kw)
        self.assertEqual(code, 2, f'{tool} {inp} was not refused: exit {code} {err} {out}')
        return err

    def allowed(self, tool, inp, **kw):
        code, err, out = self.call(tool, inp, **kw)
        self.assertEqual((code, out), (0, ''), f'{tool} {inp} was not allowed: {err} {out}')

    def approve(self, **payload):
        (self.home / 'approvals.json').write_text(json.dumps({'items': [{'id': 'ap1', 'kind': payload.pop('kind', 'slack'), 'status': 'approved', 'payload': payload}]}))

    def test_it_stands_alone(self):
        src = HOOK.read_text()
        self.assertNotIn('import jarvis', src)
        self.assertNotIn('from jarvis', src)

    def test_ordinary_work_is_allowed(self):
        self.allowed('Read', {'file_path': '/tmp/x.csv'})
        self.allowed('Bash', {'command': 'python3 analysis.py && git status'})
        self.allowed('mcp__claude_ai_Slack__slack_read_thread', {'channel_id': 'C1', 'message_ts': '1.2'})

    def test_it_does_nothing_outside_jarvis_workers(self):
        self.allowed('mcp__claude_ai_Slack__slack_send_message', {'channel_id': 'C1', 'text': 'hi'}, proc='')

    def test_kill_switch_stops_everything(self):
        (self.home / 'state' / 'KILL').write_text('now')
        code, err, out = self.call('Read', {'file_path': '/tmp/x'})
        self.assertEqual(code, 0)
        self.assertIs(json.loads(out)['continue'], False)

    def test_a_worker_never_writes_to_chat_or_sends(self):
        self.assertIn('never write to Slack', self.refused('mcp__claude_ai_Slack__slack_send_message', {'channel_id': 'C1', 'text': 'hi'}))
        self.refused('mcp__claude_ai_Slack__slack_send_message_draft', {'channel_id': 'C1', 'text': 'hi'})
        self.refused('mcp__claude_ai_Microsoft_365__outlook_send_mail', {'to': 'a@b.c', 'body': 'hi'})
        self.refused('mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue', {'issueIdOrKey': 'ABC-1', 'commentBody': 'hi'})
        self.refused('mcp__some_connector__executeWrite', {'anything': 1})
        self.refused('mcp__other__delete_page', {'id': 1})

    def test_the_approvals_worker_sends_only_what_was_approved(self):
        self.approve(channel_id='C1', text='The numbers are attached.')
        send = 'mcp__claude_ai_Microsoft_365__teams_send_chat_message'
        self.allowed(send, {'channel_id': 'C1', 'text': 'The numbers are attached.'}, lane='operator')
        self.refused(send, {'channel_id': 'C1', 'text': 'The numbers are attached. Also, call me.'}, lane='operator')
        self.refused(send, {'channel_id': 'C2', 'text': 'The numbers are attached.'}, lane='operator')
        self.approve(kind='jira', key='ABC-1', text='Done as agreed.')
        jira = 'mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue'
        self.allowed(jira, {'issueIdOrKey': 'ABC-1', 'commentBody': 'Done as agreed.'}, lane='operator')
        self.refused(jira, {'issueIdOrKey': 'ABC-2', 'commentBody': 'Done as agreed.'}, lane='operator')

    def test_the_servers_outward_routes_are_not_for_a_workers_shell(self):
        for port in ('8765', '9123'):
            self.refused('Bash', {'command': f"curl -X POST http://localhost:{port}/api/slack_send -d '{{}}'"})
            self.refused('Bash', {'command': f'curl http://127.0.0.1:{port}/api/approvals_set -d x'})
        self.refused('Bash', {'command': 'curl -H "Host: localhost:8765" http://0.0.0.0:8765/api/ladder_raise -d x'})
        self.refused('Bash', {'command': 'cat ~/.jarvis/state/page.key'})

    def test_credential_stores_are_not_read(self):
        for cmd in ('cat ~/.aws/credentials', 'grep token ~/.netrc', 'security find-generic-password -s x -w', 'head .env', 'cat ~/.git-credentials'):
            self.refused('Bash', {'command': cmd})
        self.allowed('Bash', {'command': 'gcloud storage ls gs://bucket/'})

    def test_shared_records_do_not_name_the_assistant_unless_allowed(self):
        commit = {'command': 'git commit -m "Fix the export\n\nCo-Authored-By: Claude"'}
        branch = {'command': 'git checkout -b claude/fix-export'}
        page = ('mcp__claude_ai_Atlassian_Rovo__createConfluencePage', {'title': 'Notes', 'body': 'Written by Claude.'})
        self.refused('Bash', commit); self.refused('Bash', branch); self.refused(*page)
        self.allowed('Bash', {'command': 'git commit -m "Fix the export"'})
        self.allowed('Bash', commit, JARVIS_AI_WORDING='allowed'); self.allowed('Bash', branch, JARVIS_AI_WORDING='allowed')
        self.allowed(*page, JARVIS_AI_WORDING='allowed')

    def test_a_worker_refused_again_and_again_is_stopped(self):
        last = None
        for _ in range(3):
            last = self.call('mcp__claude_ai_Slack__slack_send_message', {'channel_id': 'C1', 'text': 'hi'}, task='one-task')
        self.assertEqual(last[0], 0)
        self.assertIs(json.loads(last[2])['continue'], False)

    def test_it_finds_the_same_data_home_as_the_package(self):
        code = 'import json, runpy, sys\nsys.argv = ["worker_hook.py"]\nm = runpy.run_path(sys.argv[1] if len(sys.argv) > 1 else %r, run_name="hook")\n' % str(HOOK)
        code += 'from jarvis import paths\nprint(json.dumps([m["HOME"], str(paths.home()), m["KILL"], str(paths.state("KILL"))]))'
        for env in ({'JARVIS_HOME': str(self.home)}, {'JARVIS_HOME': '~/somewhere else'}, {}):
            e = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_')}
            e.update(env, PYTHONPATH=str(REPO))
            p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            hook_home, pkg_home, hook_kill, pkg_kill = json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual((hook_home, hook_kill), (pkg_home, pkg_kill), env)


if __name__ == '__main__':
    unittest.main()
