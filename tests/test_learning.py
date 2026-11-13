"""Learning from the owner's own chats, end to end, with a stand-in for the model: their work chats become notes, the
notes become a small knowledge base, nothing from a folder that is not work is read, the prompts carry their name, and the
whole thing stops at their usage line."""
import json, os, pathlib, stat, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test

STUB = r'''
import json, os, re, sys
a, text = sys.argv[1:], sys.stdin.read()
if not text.strip() and '-p' in a[:-1]:
    text = a[a.index('-p') + 1]                       # a caller that gives the prompt as an argument
log = os.environ.get('STUB_LOG')
if log and '-p' in a:
    with open(log, 'a') as f:
        f.write(json.dumps({'argv': [x[:80] for x in a], 'prompt': text}) + '\n')
if a[:1] == ['auth']:
    print(json.dumps({'loggedIn': True})); sys.exit(0)
if '--allowedTools' in a:                               # a fetch run: one tool call per numbered line of the prompt, kept in a
    import uuid                                         # transcript the way Claude Code keeps it
    sid, recs = str(uuid.uuid4()), []
    for k, q in enumerate(re.findall(r'^\d+\. jql "(.*)"$', text, re.M)):
        nodes = [{'key': 'ABC-12', 'fields': {'summary': 'Late orders in the March export', 'status': {'name': 'In Progress'},
                  'issuetype': {'name': 'Task'}, 'created': '2026-03-04T10:00:00', 'updated': '2026-03-05T10:00:00',
                  'description': 'Twelve of 164 orders were late.', 'comment': {'comments': [
                      {'author': {'displayName': 'Pat Roe'}, 'created': '2026-03-05T09:00:00', 'body': 'Confirmed on the desk.'}]}}}] if k == 0 else []
        recs.append({'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': f't{k}', 'name': 'jql', 'input': {'jql': q}}]}})
        recs.append({'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': f't{k}', 'content': json.dumps({'issues': {'nodes': nodes}})}]}})
    cwd = os.getcwd()
    for c in {cwd, cwd.replace('/private/var/', '/var/', 1)}:
        d = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects', re.sub(r'[^A-Za-z0-9]', '-', c))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + '.jsonl'), 'w') as f:
            f.write('\n'.join(json.dumps(r) for r in recs) + '\n')
    print(json.dumps({'session_id': sid, 'total_cost_usd': 0.01, 'result': 'DONE'})); sys.exit(0)
if '--json-schema' in a:
    schema = json.loads(a[a.index('--json-schema') + 1])
    props = schema.get('properties') or {}
    if 'sessions' in props:
        said = (re.search(r'\d+\.\d% on \d+ orders', text.rsplit('=== SESSION', 1)[-1]) or ['82.6% on 164 orders'])[0]
        out = {'sessions': [{'id': i, 'title': 'March export check', 'kind': 'chat', 'topics': ['exports'], 'asked': 'What the March export showed.',
                             'done': 'Read the export and counted the orders.', 'results': [f'on-time delivery {said} (data to 2026-02-28)'],
                             'decisions': ['keep the weekly export'], 'open': ['why two orders have no date']}
                            for i in re.findall(r'=== SESSION (.+?) ===', text)]}
    elif 'topics' in props:
        out = {'topics': [{'slug': 'exports', 'title': 'Exports', 'scope': 'The monthly export and what it shows.',
                           'sources': re.findall(r'^(\S+:\S+) \|', text, re.M)}]}
    elif 'assign' in props:                              # filing new notes under the workstreams
        out = {'assign': [{'id': l.split(' | ')[0], 'slugs': ['exports']} for l in text.split('NOTES:\n', 1)[-1].splitlines() if ' | ' in l]}
    elif 'marks' in props:
        out = {'marks': []}
    elif 'items' in props:
        item = (props['items'].get('items') or {}).get('properties') or {}
        rows = re.findall(r'^(d?\d+) \| (.*)$', text, re.M)
        if 'records' in item:                            # the ledger: each settled line with a number is one fact
            out = {'items': [{'n': int(n), 'records': [{'kind': 'fact', 'subject': 'on-time delivery', 'statement': re.sub(r'\s*\[\[.*', '', r.split(' | ', 1)[-1]).strip(),
                                                         'metric': 'on-time delivery', 'unit': '%', 'scope': 'all orders'}] if re.search(r'\d% on', r) else []}
                             for n, r in rows if n.isdigit()]}
        elif 'scope' in item:                              # the label of a chat: work or personal, from a short sample
            out = {'items': [{'id': n, 'scope': 'personal' if 'holiday' in r else 'work', 'personal_part': False} for n, r in rows]}
        elif 'personal' in item:                         # the personal screen, line by line
            out = {'items': [{'n': int(n), 'personal': 'holiday' in r} for n, r in rows]}
        else:                                            # the new facts a workstream note does not hold yet
            new = text.split('=== NEW MATERIAL ===', 1)[-1]
            m = re.search(r'\d+\.\d% on \d+ orders', new)
            out = {'items': [{'kind': 'current', 'text': f'On-time delivery is now {m[0]}.', 'sources': re.findall(r'=== \[\[(.+?)\]\] ===', new)}] if m else []}
    else:
        out = {}
    print(json.dumps({'structured_output': out, 'total_cost_usd': 0.01, 'usage': {'input_tokens': 10, 'output_tokens': 5}})); sys.exit(0)
if '--output-format' in a and a[a.index('--output-format') + 1] == 'json':
    print(json.dumps({'result': '# A note\n## In one paragraph\nOn-time delivery was 82.6% on 164 orders (ticket ABC-12, order SO-000123, kept as UTF-8) [[2026-03-04_abcd0234]].\n', 'total_cost_usd': 0.02}))
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        self.home, self.claude, self.work, self.other = t / 'home', t / 'claude', t / 'Work', t / 'Private'
        for d in (self.home, self.work, self.other):
            d.mkdir()
        self.stub = t / 'claude-stub'
        self.stub.write_text('#!' + sys.executable + '\n' + STUB)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IXUSR)
        self.log = t / 'stub.log'
        self.chat(self.work, 'abcd0234-0000-0000', '2026-03-04T10:00', 'what did the March export show?', 'On-time delivery was 82.6% on 164 orders.')
        self.chat(self.other, 'abcd1234-0000-0000', '2026-03-04T10:00', 'plan my holiday in Lisbon', 'On-time delivery was 82.6% on 164 orders.')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k != 'PYTHONPATH'}
        self.env.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude), JARVIS_CLAUDE=str(self.stub), STUB_LOG=str(self.log), PYTHONDONTWRITEBYTECODE='1')
        self.py('from jarvis import config, paths\npaths.ensure()\nconfig.set("owner.name", "Sam Lee")\nconfig.set("work.about", "runs the order desk")\n'
                f'config.set("work.folders", [{str(self.work)!r}])\n')

    def tearDown(self):
        self.tmp.cleanup()

    def chats_of(self, folder):
        """Where Claude Code keeps the chats it had in `folder`."""
        return self.claude / 'projects' / ''.join(ch if ch.isalnum() else '-' for ch in str(folder))

    def chat(self, folder, sid, minute, said, answer):
        d = self.chats_of(folder); d.mkdir(parents=True, exist_ok=True)
        (d / (sid + '.jsonl')).write_text(
            json.dumps({'type': 'user', 'timestamp': minute + ':00Z', 'message': {'role': 'user', 'content': said}}) + '\n' +
            json.dumps({'type': 'assistant', 'timestamp': minute + ':09Z', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}}) + '\n')

    def py(self, code):
        p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=dict(self.env, PYTHONPATH=str(REPO)), cwd=self.tmp.name, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return p.stdout

    def tool(self, name, *args):
        p = subprocess.run([sys.executable, str(REPO / 'tools' / name), *args], capture_output=True, text=True, env=self.env, cwd=self.tmp.name, timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        return p.stdout + p.stderr

    def prompts(self):
        return [json.loads(l)['prompt'] for l in self.log.read_text().splitlines()] if self.log.exists() else []

class Learning(Base):
    def test_chats_become_notes_and_notes_a_knowledge_base(self):
        self.tool('extract_sessions.py', '--rich')
        rich = list((self.home / 'brain' / 'sessions_rich').glob('*.txt'))
        self.assertEqual(len(rich), 1)                                    # only the chat from the work folder was read
        self.tool('kb_build.py', 'sessions', '--lanes', '1')
        notes = list((self.home / 'brain' / 'sessions').glob('*.md'))
        self.assertEqual(len(notes), 1)
        note = notes[0].read_text()
        self.assertIn('March export check', note)
        self.assertIn('82.6% on 164 orders', note)
        self.tool('kb_build.py', 'kb')
        kb = self.home / 'brain' / 'main'
        self.assertEqual([t['slug'] for t in json.loads((kb / 'topic_map.json').read_text())][:1], ['exports'])
        self.assertTrue((kb / 'topics' / 'exports.md').exists())
        for name in ('Overview', 'Timeline', 'Decisions', 'People', 'Data sources', 'Glossary', 'Open questions', 'Deliverables'):
            self.assertTrue((kb / (name + '.md')).exists(), name)
        sent = self.prompts()
        learn = [p for p in sent if 'work record of Sam Lee' in p]
        self.assertTrue(learn, 'the learning prompts name the owner')
        self.assertTrue(any('runs the order desk' in p for p in learn))
        self.assertTrue(any('March export' in p for p in sent))
        self.assertFalse(any('Lisbon' in p or 'holiday' in p for p in sent))   # the other folder's chat never reached the model
        self.tool('kb_build.py', 'sessions', '--lanes', '1')                   # run again: nothing is paid for twice
        self.assertEqual(len(self.prompts()), len(sent) + sum(1 for p in self.prompts()[len(sent):] if 'reply with just: ok' in p))

    def test_learning_stops_at_the_owners_weekly_line(self):
        self.py('from jarvis import config\nconfig.set("engine.learn_week_max", 0.0)\n')
        self.tool('extract_sessions.py', '--rich')
        out = self.tool('kb_build.py', 'sessions', '--lanes', '1')
        self.assertEqual(list((self.home / 'brain' / 'sessions').glob('*.md')), [])
        self.assertFalse(any('work record of' in p for p in self.prompts()))   # nothing of theirs was sent
        self.assertIn('stopped', out)


class Nightly(Base):
    """The nightly update: the next day's work joins the knowledge base that the first pass built."""
    def test_it_never_starts_a_knowledge_base(self):
        out = self.tool('kb_refresh.py')
        self.assertIn('first learning pass has not run', out)
        self.assertEqual(self.prompts(), [])                                   # nothing was read, labelled or sent
        self.assertFalse((self.home / 'brain' / 'kb_refresh.json').exists())

    def test_the_next_days_work_joins_the_knowledge_base(self):
        self.tool('extract_sessions.py', '--rich')
        self.tool('kb_build.py', 'sessions', '--lanes', '1')
        self.tool('kb_build.py', 'kb')
        kb, state = self.home / 'brain' / 'main', self.home / 'brain' / 'kb_refresh.json'
        state.write_text(json.dumps({'cutoff_local': '2026-03-05T06:00:00', 'runs': []}))   # the first pass ended here
        self.chat(self.work, 'ef560234-0000-0000', '2026-03-06T02:00', 'and what does the export show now?', 'On-time delivery was 91.2% on 170 orders.')
        self.chat(self.work, 'aa110234-0000-0000', '2026-03-06T02:10', 'book a holiday in Porto', 'Here is a plan for the trip.')
        before = len(self.prompts())
        out = self.tool('kb_refresh.py', '--hi', '2026-03-07T00:00:00')
        notes = self.home / 'brain' / 'sessions'
        new = notes / '2026-03-06_ef560234.md'
        self.assertTrue(new.exists(), out[-1500:])
        self.assertIn('91.2% on 170 orders', new.read_text())
        self.assertEqual([f.name for f in notes.glob('*aa110234*')], [])       # the personal chat in the work folder: no note
        topic = (kb / 'topics' / 'exports.md').read_text()
        self.assertIn('91.2% on 170 orders', topic)                            # the workstream note took the new fact,
        self.assertIn('2026-03-06_ef560234', topic)                            # cited to the note it came from
        self.assertIn('S:2026-03-06_ef560234', json.loads((kb / 'topic_map.json').read_text())[0]['sources'])
        st = json.loads(state.read_text())
        self.assertEqual(st['cutoff_local'], '2026-03-07T00:00:00')            # the next run starts where this one ended
        self.assertEqual((st['runs'][-1]['notes'], st['runs'][-1]['left']), (1, 0))
        sent = self.prompts()[before:]
        self.assertFalse(any('Lisbon' in p for p in self.prompts()))           # a folder that is not work: never read at all
        porto = [p for p in sent if 'Porto' in p]
        self.assertTrue(porto and all('ITEMS (id | folder | sample)' in p for p in porto))   # only its short sample, to be labelled
        arch = self.home / 'brain' / 'raw_archive' / 'claude_projects'
        self.assertEqual(sorted(f.name for f in arch.glob('*/*.jsonl')), ['abcd0234-0000-0000.jsonl', 'ef560234-0000-0000.jsonl'])
        self.assertTrue(any('work of Sam Lee' in p for p in sent))             # the nightly prompts name the owner too
        log = (self.home / 'brain' / 'kb_build.log').read_text()
        self.assertEqual([l for l in log.splitlines() if 'failed' in l], [])     # every follow-up step ran: ledger, pages, triage, tidy
        self.assertTrue((self.home / 'MAP.md').exists())
        self.tool('kb_refresh.py', '--hi', '2026-03-08T00:00:00')              # the night after: nothing new, nothing paid for
        self.assertFalse(any('work record of' in p or 'NEW MATERIAL' in p for p in self.prompts()[before + len(sent):]))
        self.assertEqual(json.loads(state.read_text())['runs'][-1]['units'], 0)


if __name__ == '__main__':
    unittest.main()
