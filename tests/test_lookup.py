"""Jarvis's own lookup tools, as the assistant reaches them: a small server that speaks JSON lines and nothing else."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


class Lookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, JARVIS_HOME=str(self.home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        self.tmp.cleanup()

    def talk(self, *msgs, prepare=''):
        if prepare:
            subprocess.run([sys.executable, '-c', 'from jarvis import config, paths\npaths.ensure()\n' + prepare], env=self.env, check=True, cwd=str(self.home))
        p = subprocess.run([sys.executable, '-m', 'jarvis.mcp'], input='\n'.join(json.dumps(m) for m in msgs) + '\n', capture_output=True,
                           text=True, env=self.env, cwd=str(self.home), timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return [json.loads(line) for line in p.stdout.splitlines()]          # every line it prints must be JSON

    def call(self, name, **args):
        return {'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}

    def test_it_speaks_only_json_on_an_empty_data_home(self):
        out = self.talk({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        self.assertEqual(out[0]['result']['serverInfo']['name'], 'jarvis')
        names = [t['name'] for t in out[1]['result']['tools']]
        self.assertEqual(names, ['inbox_find', 'inbox_item', 'tasks_now', 'memory_recall', 'project_brief', 'kb_entity', 'kb_facts', 'kb_search', 'needs_find'])
        text = json.dumps(out[1])
        for word in ('Work' + 'Hub', 'work' + 'hub'):                # the name this had before it was a package
            self.assertNotIn(word, text)

    def test_the_assistant_is_allowed_exactly_these_tools(self):
        out = self.talk({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        served = {'mcp__jarvis__' + t['name'] for t in out[0]['result']['tools']}
        sys.path.insert(0, str(REPO))
        os.environ['JARVIS_HOME'] = str(self.home)
        try:
            from jarvis import assistant
            self.assertEqual(served, set(assistant.LOOKUP))
        finally:
            os.environ.pop('JARVIS_HOME', None)

    def test_nothing_found_is_an_answer_not_an_error(self):
        out = self.talk(self.call('inbox_find', query='budget excel'), self.call('needs_find', query='cost per order'), self.call('tasks_now'),
                        self.call('project_brief', topic=''), self.call('kb_entity', id='ABC-1'))
        for r in out:
            self.assertFalse(r['result']['isError'], r['result']['content'][0]['text'][:200])
        self.assertIn('No matching open items', out[0]['result']['content'][0]['text'])

    def test_an_entity_page_is_found_by_its_name_or_its_last_part(self):
        prep = ('import os\nfor kind, name in (("ticket", "ABC-753"), ("site", "north-PL04")):\n'
                '    d = paths.kb("entities", kind); os.makedirs(d, exist_ok=True); open(d / (name + ".md"), "w").write("# " + name + "\\n")\n')
        out = self.talk(self.call('kb_entity', id='what about abc-753?'), self.call('kb_entity', id='PL04'), self.call('kb_entity', id='north'), prepare=prep)
        self.assertIn('# ABC-753', out[0]['result']['content'][0]['text'])
        self.assertIn('# north-PL04', out[1]['result']['content'][0]['text'])
        self.assertIn('No entity page', out[2]['result']['content'][0]['text'])

    def test_the_knowledge_base_folder_follows_settings(self):
        prep = ('import os\nconfig.set("knowledge.name", "atlas")\nd = paths.kb("entities", "ticket"); os.makedirs(d, exist_ok=True)\n'
                'open(d / "XY-1.md", "w").write("# XY-1\\n")\nassert str(d).endswith(os.path.join("brain", "atlas", "entities", "ticket")), d\n')
        out = self.talk(self.call('kb_entity', id='XY-1'), prepare=prep)
        self.assertIn('# XY-1', out[0]['result']['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
