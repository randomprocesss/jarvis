"""The nightly update reads the apps only as settings name them, and files what it read with the chat notes."""
import os, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class AppsNightly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'home'
        self.keep = {k: os.environ.get(k) for k in ('JARVIS_HOME',)}
        os.environ['JARVIS_HOME'] = str(self.home)
        from jarvis import config, paths
        self.config = config
        paths.ensure()
        config.set('owner.name', 'Sam Lee')
        sys.path.insert(0, str(REPO / 'tools'))
        for m in ('kb_refresh', 'kb_live', 'kb_live_delta', 'kb_extra', 'kb_build'):
            sys.modules.pop(m, None)
        import kb_refresh, kb_live, kb_live_delta, kb_extra
        self.R, self.L, self.LD, self.X = kb_refresh, kb_live, kb_live_delta, kb_extra
        self.calls = []
        self.LD.run = lambda since: self.calls.append(('read', str(since)))
        self.X.notes = lambda lanes=3, week=None: (self.calls.append(('notes', lanes)), ['l_tickets_2026-03'])[1]

        class K:
            WEEK_MAX = 0.5
            class WeekLine(Exception):
                pass
            lines = []
            @classmethod
            def log(cls, s):
                cls.lines.append(s)
        self.K = K

    def tearDown(self):
        sys.path.remove(str(REPO / 'tools'))
        for k, v in self.keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_nothing_named_means_nothing_read(self):
        self.assertEqual(self.R.apps_to_read(), [])
        self.assertEqual(self.R.apps_step(self.K, 10.0), [])
        self.assertEqual(self.calls, [])

    def test_named_sources_are_read_then_noted(self):
        self.config.set('tracker.cloud_id', 'site-123')
        self.config.set('sources.tracker_projects', ['ABC'])
        self.config.set('sources.wiki_spaces', ['DOCS'])
        self.assertEqual(self.R.apps_to_read(), ['tickets', 'pages'])
        ids = self.R.apps_step(self.K, 10.0)
        self.assertEqual(ids, ['l_tickets_2026-03'])
        self.assertEqual([c[0] for c in self.calls], ['read', 'notes'])

    def test_tickets_need_the_site_and_mail_needs_the_mail_app(self):
        self.config.set('sources.tracker_projects', ['ABC'])
        self.config.set('sources.mail_terms', ['orders'])
        self.assertEqual(self.R.apps_to_read(), [])                      # no Atlassian site yet; mail app not connected
        self.config.set('sources.expected', ['claude.ai Microsoft 365'])
        self.assertEqual(self.R.apps_to_read(), ['mail'])

    def test_off_with_an_api_key_or_without_the_apps_or_with_little_allowance(self):
        self.config.set('sources.wiki_spaces', ['DOCS'])
        self.assertEqual(self.R.apps_to_read(), ['pages'])
        self.assertEqual(self.R.apps_step(self.K, 1.0), [])                 # under $2 left tonight
        self.config.set('engine.mode', 'api-key')
        self.assertEqual(self.R.apps_to_read(), [])
        self.config.set('engine.mode', 'claude-plan')
        self.config.set('sources.claude_connectors', False)
        self.assertEqual(self.R.apps_to_read(), [])
        self.assertEqual(self.calls, [])

    def test_a_note_from_an_app_is_found_beside_the_chat_notes(self):
        app = self.home / 'brain' / 'sources'
        app.mkdir(parents=True, exist_ok=True)
        (app / 'l_tickets_2026-03.md').write_text('# ABC-12\nLate orders fell to 12 of 164 in March.\n')
        self.assertTrue(self.R.note_path('l_tickets_2026-03').endswith(os.path.join('brain', 'sources', 'l_tickets_2026-03.md')))
        ses = self.home / 'brain' / 'sessions'
        ses.mkdir(parents=True, exist_ok=True)
        (ses / 'chat-1.md').write_text('# a chat\n')
        self.assertTrue(self.R.note_path('chat-1').endswith(os.path.join('brain', 'sessions', 'chat-1.md')))


if __name__ == '__main__':
    unittest.main()
