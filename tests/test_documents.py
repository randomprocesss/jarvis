"""The documents in the owner's work folders reach the knowledge base, with a stand-in for the model: a report becomes a
unit and then a note; a personal folder, a personal-looking file and a file the owner's skip words name are never read."""
import json, os, pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_learning import Base                                            # the made-up person, their chats and the stand-in model

PAGE = '<html><body><h1>{}</h1><p>{}</p></body></html>'


class Documents(Base):
    def setUp(self):
        super().setUp()
        (self.work / 'reports').mkdir()
        (self.work / 'personal').mkdir()
        (self.work / 'reports' / 'Delivery review.html').write_text(PAGE.format('Delivery review', 'Late orders fell to 12 of 164 in March. ' * 20))
        (self.work / 'personal' / 'Weekend.html').write_text(PAGE.format('Weekend', 'A walk in Sintra and dinner after. ' * 20))
        (self.work / 'reports' / 'visa letter.html').write_text(PAGE.format('Letter', 'Appointment at the consulate on Monday. ' * 20))
        (self.work / 'reports' / 'timelog export.html').write_text(PAGE.format('Hours', 'Hours booked per day this week. ' * 20))
        self.py('from jarvis import config\nconfig.set("work.skip_words", ["timelog"])\n')

    def test_a_report_becomes_a_note_and_private_files_are_never_read(self):
        self.tool('kb_extra.py', 'build')
        units = {f.name: f.read_text() for f in (self.home / 'brain' / 'sources_extra').glob('*.txt')}
        self.assertEqual(len(units), 1, sorted(units))
        name, text = next(iter(units.items()))
        self.assertIn('Late orders fell to 12 of 164', text)
        self.assertIn('Delivery review', text)
        self.tool('kb_extra.py', 'run', '--lanes', '1')
        notes = list((self.home / 'brain' / 'sources').glob('*.md'))
        self.assertEqual([n.name for n in notes], [name[:-4] + '.md'])
        sent = self.prompts()
        self.assertTrue(any('Late orders fell to 12 of 164' in p and 'work record of Sam Lee' in p for p in sent))
        for word in ('Sintra', 'consulate', 'Hours booked'):
            self.assertFalse(any(word in p for p in sent), word)               # never reached the model
        before = len(sent)
        self.tool('kb_extra.py', 'build'); self.tool('kb_extra.py', 'run', '--lanes', '1')
        self.assertFalse(any('work record of' in p for p in self.prompts()[before:]))   # a second run pays for nothing


if __name__ == '__main__':
    unittest.main()
