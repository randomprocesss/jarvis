"""The skills the workers follow are filled in for the owner, installed under names that start with "jarvis-", refreshed
when the settings change, and never written over a copy the owner edited by hand."""
import json, os, pathlib, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
NAMES = ['jarvis-already-handled', 'jarvis-approved-actions', 'jarvis-inbox-work', 'jarvis-profile-analyst', 'jarvis-profile-coder',
         'jarvis-profile-writer', 'jarvis-roundup', 'jarvis-tickets']


class Skills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        self.home, self.claude = t / 'home', t / 'claude'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('JARVIS_') and k != 'PYTHONPATH'}
        self.env.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        self.tmp.cleanup()

    def py(self, code):
        p = subprocess.run([sys.executable, '-c', 'from jarvis import config, paths, skillset\npaths.ensure()\n' + code], capture_output=True, text=True,
                           env=self.env, cwd=self.tmp.name, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return p.stdout

    def skill(self, name):
        return (self.claude / 'skills' / name / 'SKILL.md').read_text()

    def test_a_new_owner_gets_every_skill_with_nothing_connected(self):
        out = json.loads(self.py('import json\nprint(json.dumps(skillset.install()))'))
        self.assertEqual(sorted(out), NAMES)
        self.assertEqual(set(out.values()), {'installed'})
        work = self.skill('jarvis-inbox-work')
        self.assertIn(str(self.home), work)                                    # their own data home, not a fixed folder
        self.assertNotIn('{{', work); self.assertNotIn('[[if', work)
        for absent in ('Slack: read the thread', 'searchJiraIssuesUsingJql', 'cloudId', 'sharepoint_search', '/api/share_file'):
            self.assertNotIn(absent, work, absent)                             # an app that is not connected is not in their instructions
        self.assertIn('No tracker with a project of the owner', work)
        self.assertIn('name: jarvis-inbox-work', work.split('\n')[1])
        self.assertEqual(set(json.loads(self.py('import json\nprint(json.dumps(skillset.install()))')).values()), {'same'})

    def test_settings_shape_the_skills_and_a_hand_edit_is_kept(self):
        self.py('skillset.install()')
        share = pathlib.Path(self.tmp.name) / 'Shared work'
        share.mkdir()
        self.py('config.set("owner.name", "Sam Lee")\nconfig.set("sources.expected", ["Slack", "Atlassian Rovo", "Microsoft 365"])\n'
                'config.set("tracker.own_projects", ["ABC"])\nconfig.set("tracker.cloud_id", "site-1")\nconfig.set("sources.slack_user_id", "U0SAM")\n'
                f'config.set("sharing.folder", {str(share)!r})\n')
        edited = self.claude / 'skills' / 'jarvis-profile-writer' / 'SKILL.md'
        edited.write_text(edited.read_text().replace('# Writer profile', '# Writer profile (my own wording)'))
        out = json.loads(self.py('import json\nprint(json.dumps(skillset.install()))'))
        self.assertEqual(out['jarvis-profile-writer'], 'kept: you edited it')
        self.assertIn('my own wording', edited.read_text())
        self.assertEqual({v for k, v in out.items() if k != 'jarvis-profile-writer'}, {'updated'})
        work = self.skill('jarvis-inbox-work')
        for present in ('You work for Sam Lee', 'their user id is U0SAM', "the owner's own project (ABC)", 'cloudId `site-1`', 'sharepoint_search',
                        '("Shared work")', 'branch named' if False else 'sl/<ticket or topic>'):
            self.assertIn(present, work, present)
        self.assertNotIn('No tracker with a project', work)
        self.assertIn('their own project: ABC', self.skill('jarvis-tickets'))
        routine = self.py('print(skillset.routine("work-items"))')
        self.assertIn("Work on Sam Lee's open Jarvis items", routine)
        self.assertNotIn('---', routine.split('\n')[0])                         # the front matter is not part of the prompt
        self.assertIn('Keep Sam Lee', self.py('print(skillset.routine("inbox-roundup"))'))


if __name__ == '__main__':
    unittest.main()
