"""The live sources: nothing is read until the owner's settings name it, and a ticket project that is named is read word
for word into units (a stand-in plays the fetch run and leaves a transcript the way Claude Code does)."""
import datetime as dt, json, os, pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_learning import Base


class LiveSources(Base):
    def test_nothing_is_read_until_it_is_named(self):
        what = json.loads(self.tool('kb_live.py', 'what'))
        self.assertEqual(what['tracker']['projects'], [])
        self.assertEqual((what['wiki']['spaces'], what['chat']['channels'], what['shared files']['folder']), ([], [], ''))
        for cmd, said in ((['jira'], 'no ticket project'), (['confluence', 'list'], 'no wiki space'), (['slack'], 'no chat channel'), (['mail', 'list'], 'no words')):
            self.assertIn(said, self.tool('kb_live.py', *cmd))
        self.assertIn('no pull requests to read', self.tool('kb_prs.py', 'sizes'))
        self.assertEqual(self.prompts(), [])                                   # no run was started

    def test_a_named_ticket_project_is_read_word_for_word(self):
        since = dt.date.today() - dt.timedelta(days=7)
        self.py('from jarvis import config\nconfig.set("sources.tracker_projects", ["abc"])\nconfig.set("tracker.cloud_id", "site-1")\n'
                f'config.set("sources.since", "{since}")\nconfig.set("sources.channels", ["C0123 orders 2026-01-05", "#C0456 desk"])\n')
        what = json.loads(self.tool('kb_live.py', 'what'))
        self.assertEqual(what['tracker']['projects'], ['ABC'])
        self.assertEqual(what['chat']['channels'], ['orders', 'desk'])
        out = self.tool('kb_live.py', 'jira')
        self.assertIn('1 tickets in 1 units', out)
        unit = (self.home / 'brain' / 'sources_extra' / f'l_jira_{since}.txt').read_text()
        self.assertIn('Jira ABC tickets created', unit.split('\n')[0])
        self.assertIn('## ABC-12: Late orders in the March export', unit)
        self.assertIn('Twelve of 164 orders were late.', unit)                  # the description, word for word
        self.assertIn('**Comment by Pat Roe, 2026-03-05T09:00:**', unit)
        sent = [p for p in self.prompts() if 'jql' in p]
        self.assertEqual(len(sent), 1)                                          # one run for every window
        self.assertIn('project = ABC AND created >=', sent[0])
        self.assertIn('cloudId site-1', sent[0])
        state = json.loads((self.home / 'state' / 'kb_live.json').read_text())
        self.assertEqual((state['jira']['tickets'], state['jira']['capped']), (1, []))


    def test_what_changed_since_is_read_without_overwriting(self):
        out = self.tool('kb_live_delta.py', 'run')                             # nothing named: nothing read
        self.assertIn('0 units merged', out)
        self.assertEqual(self.prompts(), [])
        self.py('from jarvis import config\nconfig.set("sources.tracker_projects", ["ABC"])\nconfig.set("tracker.cloud_id", "site-1")\n')
        day = dt.date.today() - dt.timedelta(days=1)
        extra = self.home / 'brain' / 'sources_extra'
        for run in (1, 2):
            out = self.tool('kb_live_delta.py', 'run', '--since', str(day))
            self.assertIn('1 tickets updated', out)
        names = sorted(f.name for f in extra.glob('l_jira_upd_*.txt'))
        tag = dt.date.today().strftime('%m%d')
        self.assertEqual(names, [f'l_jira_upd_{day}.txt', f'l_jira_upd_{day}_upd{tag}.txt'])   # the second read sits beside the first
        self.assertIn('## ABC-12: Late orders in the March export', (extra / names[0]).read_text())
        self.assertIn('project = ABC AND updated >=', [p for p in self.prompts() if 'jql' in p][0])


if __name__ == '__main__':
    unittest.main()
