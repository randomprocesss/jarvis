"""The source catalog on the owner's own folders: the first look is a baseline, later looks report what changed."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test


def run(code, home):
    e = dict(os.environ, JARVIS_HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=e, cwd=str(home), timeout=120)
    if p.returncode != 0:
        raise AssertionError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


SCAN = ('import json\nfrom jarvis import config, paths\npaths.ensure()\nfrom jarvis.world import catalog\n'
        'r = catalog.scan("local")\nprint(json.dumps({"n": r["sources"], "ev": sorted(k for _, k in r["events"]), "base": r.get("baseline"), "notes": r["notes"]}))')


class Catalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / 'home'
        self.data = pathlib.Path(self.tmp.name) / 'My Data'
        self.home.mkdir(); self.data.mkdir()
        (self.data / 'orders.csv').write_text('order_id,customer,total\n1,a,10\n2,b,20\n3,c,30\n')
        (self.data / 'notes.txt').write_text('not data')
        (self.data / '.hidden').mkdir(); (self.data / '.hidden' / 'secret.csv').write_text('a,b\n1,2\n')
        (self.data / 'node_modules').mkdir(); (self.data / 'node_modules' / 'x.json').write_text('{}')

    def tearDown(self):
        self.tmp.cleanup()

    def choose(self):
        run('import json\nfrom jarvis import config, paths\npaths.ensure()\n'
            f'config.set("sources.folders", [{str(self.data)!r}])\nprint(json.dumps(1))', self.home)

    def test_no_folder_chosen_is_not_an_error(self):
        got = run(SCAN, self.home)
        self.assertEqual((got['n'], got['ev'], got['notes']), (0, [], ['no folder chosen yet']))

    def test_first_look_is_a_baseline_then_changes_are_reported(self):
        self.choose()
        first = run(SCAN, self.home)
        self.assertEqual((first['n'], first['ev'], first['base']), (1, [], True))       # hidden and vendor folders are not read
        self.assertEqual(run(SCAN, self.home)['ev'], [])                                # nothing changed: nothing reported
        (self.data / 'returns.tsv').write_text('order_id\treason\n1\tlate\n')
        self.assertEqual(run(SCAN, self.home)['ev'], ['new_source'])
        (self.data / 'orders.csv').write_text('order_id,customer,total,region\n1,a,10,n\n2,b,20,s\n3,c,30,e\n')
        self.assertEqual(run(SCAN, self.home)['ev'], ['schema_change'])
        (self.data / 'orders.csv').write_text('order_id,customer,total,region\n' + ''.join(f'{i},x,1,n\n' for i in range(40)))
        self.assertEqual(run(SCAN, self.home)['ev'], ['volume_jump'])
        (self.data / 'returns.tsv').unlink()
        self.assertEqual(run(SCAN, self.home)['ev'], ['gone'])

    def test_a_source_is_found_by_its_columns(self):
        self.choose()
        run(SCAN, self.home)
        got = run('import json\nfrom jarvis import paths\npaths.ensure()\nfrom jarvis.world import catalog\n'
                  'print(json.dumps([[s["name"], json.loads(s["columns"]), s["rows"]] for s in catalog.search(["customer"])]))', self.home)
        self.assertEqual(got, [['orders', ['order_id', 'customer', 'total'], 3]])

    def test_a_folder_added_later_is_found_not_received(self):
        self.choose()
        run(SCAN, self.home)
        for i in range(12):
            (self.data / f'export_{i}.csv').write_text('a,b\n1,2\n')
        self.assertEqual(set(run(SCAN, self.home)['ev']), {'first_seen'})


if __name__ == '__main__':
    unittest.main()
