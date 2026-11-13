"""What the knowledge base knows beyond its notes, with a stand-in for the model: each note's properties, the things it
names (tickets, and a kind the owner named in settings), the ledger of facts, a page per thing, and the vault that mirrors
it all, which stays off until the owner switches it on and names a folder."""
import json, os, pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_learning import Base                                            # the made-up person, their chats and the stand-in model


class Ledger(Base):
    def setUp(self):
        super().setUp()
        self.py('from jarvis import config\nconfig.set("knowledge.ticket_keys", ["ABC"])\n'
                'config.set("knowledge.entities", ["order = \\\\bSO-\\\\d{6}\\\\b", "broken = ((", "ticket = x"])\n')
        self.tool('extract_sessions.py', '--rich')
        self.tool('kb_build.py', 'sessions', '--lanes', '1')
        self.tool('kb_build.py', 'kb')
        self.kb = self.home / 'brain' / 'main'
        (self.kb / 'known').mkdir()
        (self.kb / 'known' / 'order.json').write_text(json.dumps({'SO-000123': {'name': 'Spring restock', 'region': 'North', 'lines': 14}}))

    def test_properties_facts_and_a_page_per_thing(self):
        self.tool('kb_props.py', 'build')
        index = json.loads((self.kb / 'entity_index.json').read_text())
        self.assertEqual(sorted(index), ['order:SO-000123', 'ticket:ABC-12'])   # UTF-8 is not a ticket; a broken pattern is left out
        props = json.loads((self.kb / 'props.json').read_text())
        self.assertEqual(props['exports']['tickets'], ['ABC-12'])
        self.assertEqual(props['exports']['orders'], ['SO-000123'])
        self.assertEqual(props['exports']['type'], 'workstream')
        self.tool('kb_ledger.py', 'parse')
        self.tool('kb_ledger.py', 'build')
        recs = [json.loads(l) for l in (self.kb / 'ledger' / 'records.jsonl').read_text().splitlines()]
        self.assertEqual(len(recs), 1)
        self.assertIn('82.6% on 164 orders', recs[0]['statement'])
        self.assertEqual(sorted(recs[0]['entities']), ['order:SO-000123', 'ticket:ABC-12'])
        self.assertEqual((recs[0]['status'], recs[0]['workstream']), ('current', 'exports'))
        self.assertTrue(any('work of Sam Lee' in p and 'LINES (n | section | line)' in p for p in self.prompts()))
        before = len(self.prompts())
        self.tool('kb_ledger.py', 'parse')                                      # a line already parsed is not paid for again
        self.assertEqual(len(self.prompts()), before)
        self.tool('kb_entities.py', 'build')
        ticket = (self.kb / 'entities' / 'ticket' / 'ABC-12.md').read_text()
        self.assertIn('82.6% on 164 orders', ticket)                           # the page carries the ledger's fact
        self.assertIn('[[exports]]', ticket)                                   # and where the ticket is mentioned
        order = (self.kb / 'entities' / 'order' / 'SO-000123.md').read_text()
        self.assertIn('**Order SO-000123**: Spring restock', order)            # what it is, from the owner's own list
        self.assertIn('region: "North"', order)
        self.assertIn('lines: 14', order)
        self.tool('kb_lint.py')
        self.tool('kb_search.py', 'build')
        self.tool('kb_audit.py', 'run')                                        # the audit asks the knowledge base what its own ledger holds
        audit = json.loads((self.home / 'state' / 'kb_audit.json').read_text())
        self.assertEqual([k for k, v in audit.items() if isinstance(v, dict) and 'error' in v], [])
        self.assertEqual((audit['retrieval']['questions'], audit['retrieval']['answered from the knowledge base']), (1, 1))
        self.assertEqual(audit['retrieval']['detail'][0]['expected'], ['82.6%'])
        self.assertEqual(audit['secrets']['files with a hit, by pattern'], {})
        self.assertEqual(audit['citations']['cited by a workstream'], 1)

    def test_no_thing_named_is_not_an_error(self):
        self.py('from jarvis import config\nconfig.set("knowledge.ticket_keys", ["ZZZ"])\nconfig.set("knowledge.entities", [])\n')
        self.tool('kb_props.py', 'build')
        self.assertEqual(json.loads((self.kb / 'entity_index.json').read_text()), {})
        self.tool('kb_entities.py', 'build')
        self.assertTrue((self.kb / 'entities').is_dir())

    def test_the_vault_is_off_until_switched_on_and_stays_in_its_folder(self):
        run = 'import json\nfrom jarvis.world import vault\nprint(json.dumps(vault.sync(admit=False)))\n'
        self.assertIn('the vault is off', json.loads(self.py(run))['skipped'])
        vault = pathlib.Path(self.tmp.name) / 'Notes vault' / 'Jarvis'
        self.py(f'from jarvis import config\nconfig.set("modules.vault", True)\nconfig.set("vault.path", {str(vault)!r})\n')
        before = {p for p in pathlib.Path(self.tmp.name).rglob('*') if 'Notes vault' not in p.parts and 'home' not in p.parts and p.name != 'stub.log'}
        out = json.loads(self.py(run))
        self.assertNotIn('skipped', out)
        self.assertTrue((vault / 'Knowledge' / 'Knowledge.md').exists(), out)
        self.assertTrue((vault / 'Home.md').exists())
        self.assertIn('[[Knowledge]]', (vault / 'Home.md').read_text())
        state = json.loads((self.home / 'state' / 'vault.json').read_text())
        self.assertNotIn('error', state)                                       # the Today page and the views were built too
        self.assertIn('## Views', (vault / 'Today.md').read_text())
        for view in ('Jarvis work.base', 'Tickets.base', 'Workstreams.base', 'Orders.base'):   # Orders: the owner's own kind
            self.assertTrue((vault / 'Views' / view).exists(), view)
        self.assertIn('Knowledge/Entities/Orders', (vault / 'Views' / 'Orders.base').read_text())
        ws = list((vault / 'Knowledge' / 'Workstreams').glob('*.md'))
        self.assertEqual(len(ws), 1)
        self.assertIn('82.6% on 164 orders', ws[0].read_text())
        after = {p for p in pathlib.Path(self.tmp.name).rglob('*') if 'Notes vault' not in p.parts and 'home' not in p.parts and p.name != 'stub.log'}
        self.assertEqual(after, before)                                        # nothing written beside the vault and the data home
        mod = json.loads(self.py('import json\nfrom jarvis import server\nprint(json.dumps(server.load_modules(log=lambda *a: None)))\n'
                                 'print(json.dumps(server.GET["/api/vault"]({})))\nprint(json.dumps([j[0] for j in server.MODULES["vault"].jobs()]))\n').split('\n')[0])
        self.assertEqual(mod, ['vault'])                                       # switched on: the server loads it with its route and its timer
        (vault / 'Notes' / 'mine.md').write_text('my own note')
        (vault / 'Home.md').write_text('my own home page')
        self.py(run)
        self.assertEqual((vault / 'Notes' / 'mine.md').read_text(), 'my own note')      # their notes and their edits are left alone
        self.assertEqual((vault / 'Home.md').read_text(), 'my own home page')


if __name__ == '__main__':
    unittest.main()
