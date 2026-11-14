"""The leak check finds what it must and stays quiet on clean text. Run: python3 -m unittest tests.test_leakcheck"""
import os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.environ.get('JARVIS_TEST_REPO') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'), 'tools'))
import leakcheck

WORDS = """# a made-up private list
[owner]
Quentin Vexmoor
vexmoor
[organisation]
Zorblat Corp
[tickets]
re:\\bZRB-\\d+\\b
[short]
Qix
"""


class LeakCheck(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = self.d.name
        self.words = os.path.join(self.root, '..', os.path.basename(self.root) + '.words')
        open(self.words, 'w').write(WORDS)
        self.cats = leakcheck.load_words(self.words)

    def tearDown(self):
        os.remove(self.words)
        self.d.cleanup()

    def put(self, name, text):
        p = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').write(text)

    def cats_found(self):
        hits, skipped, n = leakcheck.scan_files(leakcheck.files_under(self.root), self.cats, self.root)
        return sorted({h[0] for h in hits}), hits

    def test_clean_text_passes(self):
        self.put('a.py', 'owner = config.owner_name\nprint("Hello, Sam")\npath = "/Users/you/jarvis"\nmail = "sam@example.com"\n')
        self.assertEqual(self.cats_found()[0], [])

    def test_private_words(self):
        self.put('a.md', 'Written for quentin   vexmoor at ZORBLAT corp, see ZRB-42.\n')
        found, hits = self.cats_found()
        self.assertEqual(found, ['organisation', 'owner', 'tickets'])
        self.assertTrue(any(h[2] == 1 for h in hits))

    def test_whole_words_only_and_short_words_keep_their_case(self):
        self.put('a.txt', 'vexmoorish qix QIX Qixote\n')            # none of these is the word itself
        self.assertEqual(self.cats_found()[0], [])
        self.put('b.txt', 'ask Qix\n')
        self.assertEqual(self.cats_found()[0], ['short'])

    def test_a_word_inside_a_pattern_or_an_escaped_string_is_found(self):
        self.put('a.py', "x = re.sub(r'\\bQix\\b', 'someone', s)\n")
        self.put('b.py', 'msg = "first line\\nvexmoor said so"\n')
        cats, hits = self.cats_found()
        self.assertEqual(cats, ['owner', 'short'])
        self.assertEqual(sorted((h[1], h[2]) for h in hits), [('a.py', 1), ('b.py', 1)])   # each found once, on its line

    def test_shapes(self):
        self.put('a.py', 'p = "/Users/' + 'realperson/x"\nm = "someone@' + 'realcompany.io"\n')
        self.put('b.py', 't = "xox' + 'b-123456789012-abcdefghij"\nk = "-----BEGIN RSA ' + 'PRIVATE KEY-----"\n')
        self.put('c.py', 'i = "12345678-0000-4000-8000-' + '000000000000"\n')
        self.assertEqual(self.cats_found()[0], ['e-mail address', 'home path', 'long id', 'secret-shaped string'])

    def test_a_public_value_named_in_the_allow_file_passes_but_a_private_word_never_does(self):
        sha = 'ab' * 32
        self.put('setup.py', 'CHECKSUM = "' + sha + '"\n')
        self.assertEqual(leakcheck.main([self.root, '--words', self.words]), 1)
        self.put('.leakcheck-allow', sha + '   # the published checksum of a model file\nvexmoor   # not a shape: stays private\n')
        self.assertEqual(leakcheck.main([self.root, '--words', self.words]), 1)          # the allow file itself names a private word
        self.put('.leakcheck-allow', sha + '   # the published checksum of a model file\n')
        self.assertEqual(leakcheck.main([self.root, '--words', self.words]), 0)
        self.put('note.md', 'ask vexmoor\n')
        self.assertEqual(leakcheck.main([self.root, '--words', self.words]), 1)
        leakcheck.ALLOWED.clear()

    def test_a_worktrees_pointer_to_its_git_folder_is_not_part_of_the_tree(self):
        code = '/' + 'Users' + '/realperson/code'
        self.put('.git', 'gitdir: ' + code + '/.git/worktrees/copy\n')   # what git writes in a worktree
        self.put('a.txt', 'clean\n')
        self.assertEqual(self.cats_found()[0], [])
        self.put('notes/git.txt', 'see ' + code + '\n')                  # any other file is still read
        self.assertEqual(self.cats_found()[0], ['home path'])

    def test_file_name_counts(self):
        self.put('notes/vexmoor-plan.md', 'nothing here\n')
        self.assertEqual(self.cats_found()[0], ['owner'])

    def test_lock_files_skip_shapes_but_not_words(self):
        self.put('package-lock.json', '{"integrity": "' + 'a' * 64 + '"}\n')
        self.assertEqual(self.cats_found()[0], [])
        self.put('package-lock.json', '{"name": "zorblat corp"}\n')
        self.assertEqual(self.cats_found()[0], ['organisation'])

    def test_history(self):
        def git(*a):
            subprocess.run(['git', '-C', self.root, '-c', 'user.name=Quentin Vexmoor', '-c', 'user.email=t@example.com', *a],
                           check=True, capture_output=True)
        git('init', '-q')
        self.put('a.txt', 'for Zorblat Corp\n'); git('add', '-A'); git('commit', '-qm', 'first')
        self.put('a.txt', 'clean now\n'); git('add', '-A'); git('commit', '-qm', 'remove the name')
        self.assertEqual(self.cats_found()[0], [])                    # the files are clean now
        hits, n = leakcheck.scan_history(self.root, self.cats)
        self.assertEqual(n, 2)
        self.assertEqual(sorted({h[0] for h in hits}), ['organisation', 'owner'])   # the old change, and the author's name

    def test_exit_codes(self):
        self.put('a.txt', 'clean\n')
        self.assertEqual(leakcheck.main([self.root, '--words', self.words]), 0)
        self.put('b.txt', 'Zorblat Corp\n')
        self.assertEqual(leakcheck.main([self.root, '--words', self.words]), 1)


if __name__ == '__main__':
    unittest.main()
