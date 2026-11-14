"""The knowledge base's exam, with a stand-in for the model: questions are written from the ledger's checked facts
only, each is answered from what the lookup tools return for it, and the grades are made in code. An old value given as
the current one is a miss, a miss is read once more before it counts, and with nothing to ask no exam is recorded."""
import datetime as dt, json, pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_learning import Base                                            # the made-up person, their chats and the stand-in model
from test_people import stub_with                                         # the stand-in, with this file's own answers first

# What the stand-in answers in an exam, by the structure it is asked for:
#   the questions    one per fact, the fact's own opening words with the number left out; a draft's word count is trivia
#   the answers      read from the passages given with the question: the ledger line marked current that opens with the
#                    question's words. STUB_EXAM=old gives the replaced value first, =words writes 2 as "two", =blind finds nothing
#   the second look  right when the answer wrote the record's number as a word; anything else stays a miss
ANSWERS = r'''
if '--json-schema' in a:
    item = (((json.loads(a[a.index('--json-schema') + 1]).get('properties') or {}).get('items') or {}).get('items') or {}).get('properties') or {}
    mode, out = os.environ.get('STUB_EXAM', ''), None
    if 'useful' in item:
        out = {'items': [{'n': int(n), 'useful': 'word count' not in s, 'question': re.split(r'\s*\d', s, 1)[0].rstrip(' :') + ': how many, or how much?'}
                         for n, s in re.findall(r'^(\d+) \| (.*?) \| scope: ', text, re.M)]}
    elif 'answer' in item:
        out = {'items': []}
        for n, q, passages in re.findall(r'^QUESTION (\d+): ([^\n]*)\nPASSAGES:\n(.*?)(?=^QUESTION \d+: |\Z)', text, re.M | re.S):
            opening = q.rsplit(': how many', 1)[0]
            lines = [(i, tag, s) for i, tag, s in re.findall(r'^- \[(\S+) \| fact \| (current|SUPERSEDED)[^\]]*\] (.*)$', passages, re.M) if s.startswith(opening)]
            now = [l for l in lines if l[1] == 'current']
            old = [l for l in lines if l[1] == 'SUPERSEDED']
            num = lambda s: re.search(r'\d[\d.]*%?', s[len(opening):]).group(0)
            if mode == 'blind' or not now:
                out['items'].append({'q': int(n), 'answer': 'The passages do not say.', 'value': None, 'source': None, 'sure': 'no'})
            elif mode == 'old' and old:
                out['items'].append({'q': int(n), 'answer': old[0][2] + ' A later record says: ' + now[0][2], 'value': num(old[0][2]), 'source': old[0][0], 'sure': 'yes'})
            elif mode == 'words' and num(now[0][2]) == '2':
                out['items'].append({'q': int(n), 'answer': 'Two of them.', 'value': 'two', 'source': now[0][0], 'sure': 'yes'})
            else:
                out['items'].append({'q': int(n), 'answer': now[0][2], 'value': num(now[0][2]), 'source': now[0][0], 'sure': 'yes'})
    elif 'verdict' in item:
        out = {'items': [{'n': int(n), 'verdict': 'right' if 'Two of them' in body and re.search(r'RECORD: .*\b2\b', body) else 'wrong', 'why': 'read against the record'}
                         for n, body in re.findall(r'^\[(\d+)\] (QUESTION: .*?)(?=^\[\d+\] QUESTION: |\Z)', text, re.M | re.S)]}
    if out is not None:
        print(json.dumps({'structured_output': out, 'total_cost_usd': 0.03, 'usage': {'input_tokens': 10, 'output_tokens': 5}})); sys.exit(0)
'''

# A small ledger. Asked: F-2 (it replaced F-1, so the old value must not come back) and F-3. Never asked: a replaced record,
# a number that was not found in its source, a decision, a line from the memory notes; and the word count is trivia.
RECORDS = [
    {'id': 'F-1', 'kind': 'fact', 'status': 'superseded', 'superseded_by': ['F-2'], 'workstream': 'exports', 'subject': 'on-time delivery',
     'statement': 'On-time delivery was 82.6% on 164 orders.', 'value': '82.6%', 'unit': 'of orders', 'scope': 'all orders', 'data_cutoff': '2026-02-28',
     'checked': 'found', 'note': '2026-03-04_abcd0234'},
    {'id': 'F-2', 'kind': 'fact', 'status': 'current', 'workstream': 'exports', 'subject': 'on-time delivery',
     'statement': 'On-time delivery was 91.2% on 170 orders.', 'value': '91.2%', 'unit': 'of orders', 'scope': 'all orders', 'data_cutoff': '2026-03-31',
     'checked': 'found', 'note': '2026-04-02_ef560234'},
    {'id': 'F-3', 'kind': 'fact', 'status': 'current', 'workstream': 'deliveries', 'subject': 'orders without a delivery date',
     'statement': 'Orders without a delivery date: 2 of 170 orders.', 'value': '2', 'unit': 'orders', 'scope': 'March export', 'data_cutoff': '2026-03-31',
     'checked': 'verified', 'note': '2026-04-02_ef560234'},
    {'id': 'F-4', 'kind': 'fact', 'status': 'current', 'workstream': 'deliveries', 'subject': 'returned orders',
     'statement': 'Returned orders were 4 of 170 orders.', 'value': '4', 'unit': 'orders', 'checked': 'missing', 'note': '2026-04-02_ef560234'},
    {'id': 'F-5', 'kind': 'fact', 'status': 'current', 'workstream': 'exports', 'subject': 'draft of the export note',
     'statement': 'The draft of the export note has a word count of 250.', 'value': '250', 'unit': 'words', 'checked': 'found', 'note': '2026-04-02_ef560234'},
    {'id': 'D-1', 'kind': 'decision', 'status': 'current', 'workstream': 'exports', 'subject': 'weekly export',
     'statement': 'The export stays weekly, decided on 5 March.', 'value': '5', 'checked': 'found', 'note': '2026-03-04_abcd0234'},
    {'id': 'F-6', 'kind': 'fact', 'status': 'current', 'workstream': '(memory)', 'subject': 'usual carrier pick-up',
     'statement': 'The carrier picks up at 16 on weekdays.', 'value': '16', 'checked': 'found', 'note': 'memory:carrier'}]


class Exam(Base):
    def setUp(self):
        super().setUp()
        self.stub.write_text('#!' + sys.executable + '\n' + stub_with(ANSWERS))
        self.kb = self.home / 'brain' / 'main'
        self.last, self.history = self.home / 'state' / 'kb_exam_last.json', self.home / 'state' / 'kb_exam.json'

    def ledger(self, records=RECORDS):
        """The ledger's records, the workstream note they came from, and the two indexes the lookup tools read."""
        for d in ('ledger', 'topics'):
            (self.kb / d).mkdir(parents=True, exist_ok=True)
        (self.kb / 'ledger' / 'records.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        (self.kb / 'topics' / 'exports.md').write_text('---\ntitle: Exports\nupdated: 2026-04-02\n---\n'
                                                       'The weekly export shows on-time delivery and the orders without a delivery date.\n')
        self.tool('kb_facts.py', 'build')
        self.tool('kb_search.py', 'build')

    def grades(self):
        return {r['q']['id']: r['grade'] for r in json.loads(self.last.read_text())}

    def test_checked_facts_are_asked_and_answered_through_the_lookup_tools(self):
        self.ledger()
        out = self.tool('kb_exam.py', 'run', '--n', '10')
        self.assertIn("score 100% on 2: {'right': 2} $0.03", out)
        rows = json.loads(self.last.read_text())                          # every question, its answer and its grade are kept
        self.assertEqual([r['q']['id'] for r in rows], ['F-2', 'F-3'])    # the fact that replaced another comes first
        self.assertEqual(rows[0]['q']['q'], 'On-time delivery was: how many, or how much?')
        self.assertEqual((rows[0]['q']['expect'], rows[0]['q']['old_values'], rows[0]['a']['value'], rows[0]['a']['source']), ('91.2%', ['82.6%'], '91.2%', 'F-2'))
        self.assertEqual((rows[1]['q']['expect'], rows[1]['a']['value']), ('2', '2'))
        hist = json.loads(self.history.read_text())['history']
        self.assertEqual(len(hist), 1)
        self.assertEqual({k: hist[0][k] for k in ('n', 'score', 'grades', 'cost')}, {'n': 2, 'score': 1.0, 'grades': {'right': 2}, 'cost': 0.03})
        page = (self.kb / 'ledger' / 'exam.md').read_text()               # the page of the exam, in the knowledge base
        self.assertTrue(page.startswith(f'---\ntopic: exam\ntitle: Knowledge base exam\nupdated: {dt.date.today()}\n---\n# Knowledge base exam\n'), page[:200])
        self.assertIn('2 questions from verified ledger records, answered only from project_brief and kb_search', page)
        self.assertIn('score 100%', page)
        self.assertNotIn('\n## ', page)                                   # no miss, nothing to list
        sent = self.prompts()
        asked = [p for p in sent if p.startswith('Each line is a verified fact from the work of Sam Lee (runs the order desk).')]
        self.assertEqual(len(asked), 1)
        lines = asked[0].split('\n\n')[-1]
        self.assertEqual(lines.count('\n'), 2)                            # three facts went to be phrased; the word count was not worth asking
        for never in ('82.6%', 'Returned orders', 'stays weekly', 'carrier picks up'):   # replaced, not found in its source, a decision, a memory line
            self.assertNotIn(never, lines)
        answered = [p for p in sent if p.startswith('You answer questions about the work of Sam Lee (runs the order desk) ONLY from the knowledge-base passages')]
        self.assertEqual(len(answered), 1)                                # two questions, one call
        first = answered[0].split('QUESTION 0: ')[1].split('QUESTION 1: ')[0]
        self.assertIn('CHECKED FACTS FROM THE LEDGER', first)             # what project_brief gave for the question:
        self.assertIn('- [F-2 | fact | current | exports | data cut-off 2026-03-31 | from the note 2026-04-02_ef560234] On-time delivery was 91.2% on 170 orders.', first)
        self.assertIn('- [F-1 | fact | SUPERSEDED by F-2: do not give as current', first)     # the old value is there, and marked
        self.assertIn('[kb:exports | workstream note: the settled view, updated 2026-04-02', first)
        self.assertIn('\n\nSEARCH:\n', first)                             # and what kb_search gave
        self.assertFalse(any('Lisbon' in p or 'holiday' in p for p in sent))   # the chat from the folder that is not work is nowhere in it

    def test_an_old_value_given_as_current_is_a_miss(self):
        self.ledger()
        self.env['STUB_EXAM'] = 'old'
        out = self.tool('kb_exam.py', 'run', '--n', '10')
        self.assertEqual(self.grades(), {'F-2': 'gave the superseded value', 'F-3': 'right'})
        self.assertIn('score 50% on 2', out)
        page = (self.kb / 'ledger' / 'exam.md').read_text()
        self.assertIn('## Gave the superseded value (1)\n\n- F-2 [[exports]] · Q: On-time delivery was: how many, or how much? · expected 91.2% of orders · got: 82.6% (', page)
        self.assertIn('· source used: F-1', page)
        looked = [p for p in self.prompts() if p.startswith('A knowledge-base exam marked the answers below as misses')]
        self.assertEqual(len(looked), 1)                                  # the miss was read once more, and stayed a miss
        self.assertIn('RECORD: On-time delivery was 91.2% on 170 orders.\nOLD VALUES: 82.6%\n', looked[0])
        self.assertEqual(json.loads(self.history.read_text())['history'][-1]['cost'], 0.06)   # the answers and the second look

    def test_a_miss_is_read_once_more_before_it_counts(self):
        self.ledger()
        self.env['STUB_EXAM'] = 'words'
        out = self.tool('kb_exam.py', 'run', '--n', '10')
        self.assertEqual(self.grades(), {'F-2': 'right', 'F-3': 'right on a second look'})   # "two" holds no number to compare
        self.assertIn('score 100% on 2', out)
        row = json.loads(self.last.read_text())[1]['a']
        self.assertEqual((row['first_grade'], row['second_look']), ('wrong', 'read against the record'))   # both readings are kept

    def test_what_the_lookup_does_not_find_is_listed_as_a_repair(self):
        self.ledger()
        self.env['STUB_EXAM'] = 'blind'
        out = self.tool('kb_exam.py', 'run', '--n', '10')
        self.assertEqual(self.grades(), {'F-2': 'not found', 'F-3': 'not found'})
        self.assertIn('score 0% on 2', out)
        page = (self.kb / 'ledger' / 'exam.md').read_text()
        self.assertIn('## Not found (2)\n\n- F-2 [[exports]]', page)
        self.assertIn('- F-3 [[deliveries]] · Q: Orders without a delivery date: how many, or how much? · expected 2 orders · got: - (The passages do not say.) · source used: -', page)

    def test_a_second_exam_asks_the_same_questions_and_keeps_both_results(self):
        self.ledger()
        self.env['STUB_EXAM'] = 'blind'
        self.tool('kb_exam.py', 'run', '--n', '10')
        first = [r['q'] for r in json.loads(self.last.read_text())]
        self.env.pop('STUB_EXAM')                                         # as after a repair: the same exam, taken again
        self.tool('kb_exam.py', 'run', '--n', '10')
        self.assertEqual([r['q'] for r in json.loads(self.last.read_text())], first)
        hist = json.loads(self.history.read_text())['history']
        self.assertEqual([(h['n'], h['score']) for h in hist], [(2, 0.0), (2, 1.0)])
        self.assertIn('score 100%', (self.kb / 'ledger' / 'exam.md').read_text())   # the page shows the latest one

    def test_the_number_of_questions_can_be_set(self):
        self.ledger()
        self.tool('kb_exam.py', 'run', '--n', '1')
        self.assertEqual(len(self.grades()), 1)
        self.assertEqual(json.loads(self.history.read_text())['history'][0]['n'], 1)

    def test_nothing_to_ask_is_no_exam(self):
        self.assertIn('exam: nothing to ask; no exam taken', self.tool('kb_exam.py', 'run'))   # no ledger at all
        self.ledger([r for r in RECORDS if r['id'] in ('F-1', 'F-4', 'D-1', 'F-6')])   # a ledger with no checked, current number in it
        self.assertIn('exam: nothing to ask; no exam taken', self.tool('kb_exam.py', 'run'))
        self.assertEqual(self.prompts(), [])                              # nothing was sent
        for f in (self.history, self.last, self.kb / 'ledger' / 'exam.md'):
            self.assertFalse(f.exists(), f.name)                          # and no score of 0% on 0 questions is left behind


if __name__ == '__main__':
    unittest.main()
