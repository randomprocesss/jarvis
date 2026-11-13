"""The two readers of the knowledge base (the fact ledger and the full-text search) on a small made-up knowledge base:
a current fact is found, a replaced one is shown as replaced, and a detail is found word for word with where it came from."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


def run(code, home):
    e = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_')}
    e.update(JARVIS_HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, cwd=str(home), timeout=120)
    if p.returncode != 0:
        raise AssertionError(p.stderr[-2500:])
    return json.loads(p.stdout.strip().splitlines()[-1])


MAKE = '''import json, os
from jarvis import config, paths
paths.ensure()
led = paths.kb('ledger'); os.makedirs(led, exist_ok=True)
recs = [
 {"id": "F-old", "kind": "fact", "status": "superseded", "superseded_by": ["F-new"], "workstream": "pricing", "subject": "price per seat",
  "statement": "The price per seat is 12 dollars a month.", "value": "12", "unit": "USD", "data_cutoff": "2026-01-31", "note": "chat-jan"},
 {"id": "F-new", "kind": "fact", "status": "current", "workstream": "pricing", "subject": "price per seat",
  "statement": "The price per seat is 15 dollars a month from March.", "value": "15", "unit": "USD", "data_cutoff": "2026-03-01", "note": "chat-mar"},
 {"id": "D-1", "kind": "decision", "status": "current", "workstream": "launch", "subject": "launch date",
  "statement": "The launch moves to the second week of April.", "who": "Priya", "note": "meeting-0304"}]
open(led / 'records.jsonl', 'w').write(''.join(json.dumps(r) + chr(10) for r in recs))
os.makedirs(paths.kb('topics'), exist_ok=True)
open(paths.kb('topics', 'pricing.md'), 'w').write('---' + chr(10) + 'title: Pricing' + chr(10) + '---' + chr(10) + 'Seats cost 15 dollars from March; the annual plan has a zebrafish discount code.' + chr(10))
os.makedirs(paths.brain('sessions'), exist_ok=True)
open(paths.brain('sessions', '2026-03-04_abcd.md'), 'w').write('We agreed the invoice template keeps the quokka footer.' + chr(10))
paths.use_tools()
import kb_facts, kb_search
'''


class Knowledge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_current_fact_comes_first_and_the_old_one_is_marked(self):
        got = run(MAKE + 'kb_facts.build()\nr = kb_facts.find("what is the price per seat", k=5)\n'
                  'print(json.dumps({"ids": [x["id"] for x in r], "status": {x["id"]: x["status"] for x in r}, "text": kb_facts.lines("price per seat")}))', self.home)
        self.assertEqual(got['ids'][0], 'F-new')
        self.assertEqual(got['status'].get('F-old'), 'superseded')
        self.assertIn('15 dollars', got['text'])
        self.assertIn('SUPERSEDED', got['text'])
        self.assertLess(got['text'].index('15 dollars'), got['text'].index('SUPERSEDED'))

    def test_a_decision_is_found_by_its_words(self):
        got = run(MAKE + 'kb_facts.build()\nprint(json.dumps([x["id"] for x in kb_facts.find("when is the launch", k=3)]))', self.home)
        self.assertEqual(got[0], 'D-1')

    def test_nothing_on_file_is_an_empty_answer(self):
        got = run('import json\nfrom jarvis import paths\npaths.ensure()\npaths.use_tools()\nimport kb_search\n'
                  'kb_search.build()\nprint(json.dumps(kb_search.search("anything at all")))', self.home)
        self.assertEqual(got, [])

    def test_a_detail_is_found_word_for_word_with_its_source(self):
        got = run(MAKE + 'kb_search.build()\nrows = kb_search.search("zebrafish discount") + kb_search.search("quokka footer")\n'
                  'print(json.dumps([[r.get("kind"), os.path.basename(str(r.get("path") or r.get("source") or "")), str(r.get("text") or r.get("passage") or "")[:200]] for r in rows]))', self.home)
        kinds = {g[0] for g in got}
        self.assertIn('workstream', kinds)
        self.assertIn('chat note', kinds)
        self.assertTrue(any('zebrafish' in g[2] for g in got))
        self.assertTrue(any('quokka' in g[2] for g in got))

    def test_the_lookup_tools_answer_from_them(self):
        got = run(MAKE + 'kb_facts.build(); kb_search.build()\nfrom jarvis import mcp\n'
                  'print(json.dumps([mcp.kb_facts_tool({"query": "price per seat"})[:400], mcp.kb_search_tool({"query": "quokka footer"})[:400], mcp.project_brief({"topic": "pricing"})]))', self.home)
        self.assertIn('15 dollars', got[0])
        self.assertIn('quokka', got[1])
        self.assertIn('Seats cost 15 dollars', got[2])


if __name__ == '__main__':
    unittest.main()
