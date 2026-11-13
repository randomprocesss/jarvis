"""Settings and paths: defaults on an empty home, a faithful round trip, values checked, nothing written elsewhere.
Run: python3 -m unittest tests.test_config"""
import os, stat, sys, tempfile, unittest

sys.path.insert(0, os.environ.get('JARVIS_TEST_REPO') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))   # the copy under test
from jarvis import config, paths


class Settings(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.old = os.environ.get('JARVIS_HOME')
        os.environ['JARVIS_HOME'] = os.path.join(self.d.name, 'home')

    def tearDown(self):
        if self.old is None:
            os.environ.pop('JARVIS_HOME', None)
        else:
            os.environ['JARVIS_HOME'] = self.old
        self.d.cleanup()

    def test_empty_home_is_a_valid_start(self):
        cfg = config.load()
        self.assertFalse(cfg.is_set_up())
        self.assertEqual(cfg.get('engine.mode'), 'claude-plan')
        self.assertEqual(cfg.get('server.port'), 8765)
        self.assertFalse(os.path.exists(os.environ['JARVIS_HOME']))          # reading creates nothing

    def test_set_and_read_back(self):
        config.set('owner.name', 'Sam "the quick" Lee — ñ')
        config.set('work.folders', ['~/Documents/Atlas', 'C:\\Work\\Notes'])
        config.set('engine.daily_cap_usd', 2.5)
        config.set('modules.voice', True)
        cfg = config.load()
        self.assertTrue(cfg.is_set_up())
        self.assertEqual(cfg.owner(), 'Sam "the quick" Lee — ñ')
        self.assertEqual(cfg.address_as(), 'Sam')
        self.assertEqual(cfg.get('work.folders'), ['~/Documents/Atlas', 'C:\\Work\\Notes'])
        self.assertEqual(cfg.get('engine.daily_cap_usd'), 2.5)
        self.assertIs(cfg.get('modules.voice'), True)
        self.assertEqual(cfg.get('work.quiet'), '22:30-07:00')                # untouched settings keep their defaults

    def test_address_as_wins_over_first_name(self):
        config.set('owner.name', 'Sam Lee'); config.set('owner.address_as', 'boss')
        self.assertEqual(config.load().address_as(), 'boss')

    def test_bad_values_are_refused_and_nothing_is_written(self):
        for key, value in [('engine.mode', 'free'), ('work.hours', '7am-10pm'), ('work.days', 'weekdays'), ('server.port', 80),
                           ('server.port', '8765'), ('modules.voice', 'yes'), ('work.folders', 'one'), ('owner.name', 'a\nb'),
                           ('engine.background_share', 1.5), ('engine.daily_cap_usd', -1), ('ownername', 'x')]:
            with self.assertRaises(config.ConfigError, msg=f'{key}={value!r}'):
                config.set(key, value)
        self.assertFalse(paths.config_file().exists())

    def test_a_setting_from_another_version_survives(self):
        paths.ensure()
        paths.config_file().write_text('[owner]\nname = "Sam"\n\n[future]\nthing = "kept"\n', encoding='utf-8')
        config.set('owner.address_as', 'Sam')
        self.assertEqual(config.load().get('future.thing'), 'kept')

    def test_a_broken_file_says_what_to_do(self):
        paths.ensure()
        paths.config_file().write_text('[owner\nname = ', encoding='utf-8')
        with self.assertRaises(config.ConfigError) as e:
            config.load()
        self.assertIn('config.toml', str(e.exception))

    def test_private_to_the_owner_and_inside_the_home(self):
        config.set('owner.name', 'Sam')
        home = paths.home()
        self.assertEqual(str(paths.config_file()), os.path.join(os.environ['JARVIS_HOME'], 'config.toml'))
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(os.stat(paths.config_file()).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(home).st_mode), 0o700)
        self.assertEqual(sorted(os.listdir(self.d.name)), ['home'])           # nothing written beside the home
        self.assertEqual(sorted(p for p in os.listdir(home) if not p.startswith('.')), ['brain', 'config.toml', 'logs', 'roundups', 'skills', 'state', 'voice', 'work'])

    def test_working_hours_come_from_settings(self):
        import datetime as dt
        cfg = config.load()
        self.assertTrue(cfg.in_work_hours(dt.datetime(2026, 3, 4, 10, 0)))        # a Wednesday morning, the defaults
        self.assertFalse(cfg.in_work_hours(dt.datetime(2026, 3, 4, 19, 0)))       # the end hour is outside
        self.assertFalse(cfg.in_work_hours(dt.datetime(2026, 3, 7, 10, 0)))       # a Saturday
        config.set('work.days', 'sun-thu')
        config.set('work.hours', '22:00-06:00')
        cfg = config.load()
        self.assertTrue(cfg.in_work_hours(dt.datetime(2026, 3, 8, 23, 30)))       # Sunday night
        self.assertTrue(cfg.in_work_hours(dt.datetime(2026, 3, 9, 5, 59)))        # Monday before six
        self.assertFalse(cfg.in_work_hours(dt.datetime(2026, 3, 9, 12, 0)))
        self.assertFalse(cfg.in_work_hours(dt.datetime(2026, 3, 6, 23, 0)))       # Friday is not a working day here

    def test_the_command_and_folders_resolve_without_a_fixed_place(self):
        os.environ['JARVIS_CLAUDE'] = '/somewhere/claude'
        try:
            self.assertEqual(paths.claude_bin(), '/somewhere/claude')
        finally:
            del os.environ['JARVIS_CLAUDE']
        self.assertEqual(paths.workdir(), paths.work())                           # no folder chosen: the data home's
        self.assertEqual(paths.claude_chats_for('/a b/c.d').name, '-a-b-c-d')
        self.assertEqual(paths.worktree('ap-1').parent, paths.REPO.parent)
        self.assertEqual(paths.tool('x.py'), paths.REPO / 'tools' / 'x.py')

    def test_settings_read_the_same_without_a_toml_package(self):
        tricky = {'owner': {'name': 'Sam "the quick" Lee — ñ', 'address_as': "it's # not a comment"},
                  'work': {'folders': ['~/a, b', 'c:\\d', 'e]f'], 'projects': [], 'days': 'sun-thu'},
                  'engine': {'background_share': 0.25, 'daily_cap_usd': 5.0}, 'server': {'port': 9123},
                  'modules': {'voice': True, 'vault': False}, 'top': 'kept'}
        text = config._dump(tricky)
        self.assertEqual(config._parse(text), tricky)
        if config.tomllib is not None:
            self.assertEqual(config._parse(text), config.tomllib.loads(text))
        by_hand = '# a note\n[work]\nfolders = [\n  "~/one",   # where the reports are\n  \'~/two\'\n]\nquiet = "22:00-06:00"  # nights\n'
        self.assertEqual(config._parse(by_hand), {'work': {'folders': ['~/one', '~/two'], 'quiet': '22:00-06:00'}})
        for bad in ('[work]\nfolders = ["a"', 'name value', '[work]\ndays = mon-fri'):
            with self.assertRaises(ValueError):
                config._parse(bad)

    def test_a_broken_file_says_which_line(self):
        os.makedirs(os.environ['JARVIS_HOME'])
        with open(os.path.join(os.environ['JARVIS_HOME'], 'config.toml'), 'w') as f:
            f.write('[owner]\nname = "Sam\n')
        with self.assertRaises(config.ConfigError) as e:
            config.load()
        self.assertIn('config.toml', str(e.exception))

    def test_paths_follow_the_home(self):
        self.assertEqual(paths.brain('ledger', 'facts.db'), paths.home() / 'brain' / 'ledger' / 'facts.db')
        self.assertEqual(paths.demo_home(), paths.home() / 'demo')
        self.assertTrue((paths.REPO / 'jarvis' / 'paths.py').exists())


if __name__ == '__main__':
    unittest.main()
