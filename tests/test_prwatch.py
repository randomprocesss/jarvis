"""The pull-request watch: what it makes of two looks at the code host, with no network and no model."""
import importlib, json, os, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def pr(repo, n, title, author, state='OPEN', reviewers=(), approved=(), changes=(), comments=0, tasks=0, ws='acme'):
    return {'id': n, 'title': title, 'state': state, 'author': {'display_name': author},
            'reviewers': [{'display_name': r} for r in reviewers],
            'participants': [{'user': {'display_name': a}, 'state': 'approved'} for a in approved] +
                            [{'user': {'display_name': c}, 'state': 'changes_requested'} for c in changes],
            'created_on': '2026-03-02T09:00:00+00:00', 'updated_on': '2026-03-03T10:00:00+00:00',
            'links': {'html': {'href': f'https://bitbucket.org/{ws}/{repo}/pull-requests/{n}'}}, 'comment_count': comments, 'task_count': tasks}


class PrWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get('JARVIS_HOME')
        os.environ['JARVIS_HOME'] = str(Path(self.tmp.name) / 'home')
        from jarvis import config, paths
        paths.ensure()
        config.set('owner.name', 'Sam Lee')
        sys.path.insert(0, str(REPO / 'tools'))
        sys.modules.pop('prwatch', None)
        self.w = importlib.import_module('prwatch')

    def tearDown(self):
        sys.path.remove(str(REPO / 'tools'))
        sys.modules.pop('prwatch', None)
        if self.old is None:
            os.environ.pop('JARVIS_HOME', None)
        else:
            os.environ['JARVIS_HOME'] = self.old
        self.tmp.cleanup()

    def look(self, review=(), mine=(), opened=()):
        return {'review': self.w.parse(list(review), 'acme'), 'mine': self.w.parse(list(mine), 'acme'), 'open': self.w.parse(list(opened), 'acme')}

    def test_with_no_workspace_nothing_is_read(self):
        self.assertIn('not set up', self.w.main())
        self.assertFalse(os.path.exists(self.w.STATE))

    def test_a_record_from_another_workspace_or_with_a_wrong_id_is_dropped(self):
        got = self.w.parse([pr('shop', 7, 'Fix totals', 'Dana'), pr('shop', 8, 'Other', 'Dana', ws='elsewhere'),
                            dict(pr('shop', 9, 'Slip', 'Dana'), id=10)], 'acme')
        self.assertEqual(list(got), ['shop#7'])

    def test_first_look_is_a_baseline_and_later_looks_report_only_what_changed(self):
        first = self.look(review=[pr('shop', 7, 'Fix totals', 'Dana', reviewers=['Sam Lee'])],
                          mine=[pr('shop', 5, 'Add export', 'Sam Lee')], opened=[pr('shop', 7, 'Fix totals', 'Dana'), pr('api', 2, 'Old one', 'Priya')])
        acts, fyi = self.w.events({}, first, True)
        self.assertEqual([a['title'] for a in acts], ['Review requested: Fix totals'])
        self.assertEqual(fyi, [])                                   # 'Old one' was opened long ago: the baseline does not list it
        second = self.look(review=[pr('shop', 7, 'Fix totals', 'Dana', reviewers=['Sam Lee'])],
                           mine=[pr('shop', 5, 'Add export', 'Sam Lee', changes=['Priya'], comments=2)],
                           opened=[pr('shop', 7, 'Fix totals', 'Dana'), pr('api', 2, 'Old one', 'Priya'), pr('api', 3, 'New page', 'Priya')])
        acts, fyi = self.w.events(first, second, False)
        self.assertEqual(len(acts), 1)
        self.assertIn('changes requested by Priya', acts[0]['points'][0]['text'])
        self.assertIn('2 new comment(s)', acts[0]['points'][0]['text'])
        self.assertEqual([f['text'].split(':')[0] for f in fyi], ['New PR by Priya'])
        third = self.look(review=[], mine=[pr('shop', 5, 'Add export', 'Sam Lee', state='MERGED', changes=['Priya'], comments=2)],
                          opened=[pr('api', 2, 'Old one', 'Priya'), pr('api', 3, 'New page', 'Priya')])
        acts, fyi = self.w.events(second, third, False)
        self.assertEqual(acts, [])
        self.assertIn('it was merged', fyi[0]['text'])

    def test_a_run_writes_a_round_up_and_the_snapshot(self):
        from jarvis import config
        config.set('sources.code_workspace', 'acme')
        self.w.usage_ok = lambda: (True, '')
        lists = {'open': [pr('shop', 7, 'Fix totals', 'Dana')], 'review': [pr('shop', 7, 'Fix totals', 'Dana', reviewers=['Sam Lee'])],
                 'mine_open': [], 'mine_merged': [], 'mine_declined': []}
        self.w.fetch = lambda name, workspace: self.w.parse(lists[name], workspace)
        sys.argv = ['prwatch.py', '--force']
        said = self.w.main()
        self.assertIn('1 actions', said)
        files = sorted(os.listdir(self.w.ROUNDUPS))
        self.assertEqual(len(files), 1)
        ru = json.load(open(os.path.join(self.w.ROUNDUPS, files[0])))
        self.assertEqual(ru['actions'][0]['title'], 'Review requested: Fix totals')
        self.assertIn('the acme workspace', ru['source'])
        self.assertIn('Sam', ru['source'])
        self.assertEqual(json.load(open(self.w.STATE))['counts']['review'], 1)
        self.w.fetch = lambda name, workspace: (_ for _ in ()).throw(ValueError('no answer'))
        self.assertIn('fetch failed', self.w.main())
        self.assertEqual(json.load(open(self.w.STATE))['counts']['review'], 1)          # a failed look keeps the last snapshot
        self.assertEqual(len(os.listdir(self.w.ROUNDUPS)), 1)


if __name__ == '__main__':
    unittest.main()
