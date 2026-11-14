"""The deploy gate, offline: local git repositories stand in for the code host (git itself rewrites the address), and a
small script stands in for Claude. No network, no model, nothing outside a temporary folder."""
import importlib.util, json, os, pathlib, stat, subprocess, sys, tempfile, time, unittest

REPO = pathlib.Path(os.environ.get('JARVIS_TEST_REPO') or pathlib.Path(__file__).resolve().parent.parent)   # the copy under test
HOST = 'https://code.example.com/acme/'                 # what settings name; git is told to read a local folder instead
SIBLINGS = ('gateconf', 'osdep', 'recheck', 'reviewer', 'prep', 'repos_mcp')

# The stand-in for Claude. A review call (--agent) takes the next step of the plan file and acts it out: a verdict word
# writes a complete verdict file, "bad" an incomplete one, "none" nothing at all (the turn just ends), "limit" a usage
# limit, "sleep" a review that never ends, "push" a reviewer that tries to push. Every call is written to the log.
STUB = r'''
import json, os, re, subprocess, sys, time, uuid
a = sys.argv[1:]
if a[:2] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True})); sys.exit(0)
if '-p' not in a or '--agent' not in a:
    print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False})); sys.exit(0)
prompt = a[a.index('-p') + 1]
plan_file, log = os.environ['STUB_PLAN'], os.environ['STUB_LOG']
plan = json.load(open(plan_file)) if os.path.exists(plan_file) else []
step = plan.pop(0) if plan else 'GO'
json.dump(plan, open(plan_file, 'w'))
vp = re.search(r'Write your verdict file to: (\S+)', prompt)
vp = vp.group(1) if vp else re.search(r'write the verdict file to (\S+) before', prompt).group(1)
sid = a[a.index('--resume') + 1] if '--resume' in a else str(uuid.uuid4())
rec = {'argv': [x[:300] for x in a if x != prompt], 'prompt': prompt, 'cwd': os.getcwd(), 'step': step, 'resume': '--resume' in a,
       'env': {k: os.environ.get(k) for k in ('JARVIS_PROC', 'JARVIS_LANE', 'JARVIS_TASK_ID', 'REGISTRY_TOKEN', 'DG_LOCAL_ONLY')}}
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': sid}), flush=True)
if step == 'push':
    r = subprocess.run(['git', 'push', 'origin', 'HEAD:refs/heads/pushed-by-the-reviewer'], capture_output=True, text=True)
    rec['push_rc'], step = r.returncode, 'GO'
with open(log, 'a') as f:
    f.write(json.dumps(rec) + '\n')
if step == 'limit':
    print(json.dumps({'type': 'result', 'is_error': True, 'result': 'Claude AI usage limit reached; your limit resets 11pm'}))
    sys.exit(1)
if step == 'sleep':
    time.sleep(120)
if step == 'bad':
    open(vp, 'w').write('# Deploy gate: t\nVerdict: GO\n## What is being pushed\n- a\n')
elif step != 'none':
    head = {'NO-GO': 'FAIL', 'NEEDS-INFO': 'NOT VERIFIED'}.get(step, 'PASS')
    prows = ''.join(f'| {p} | f | earlier: FAIL | PASS fixed now | out.txt |\n' for p in re.findall(r'^(P-\d+): ', prompt, re.M))
    findings = {'NO-GO': '1. **Blocking:** `app.py:2` adds the tax twice. Totals are too high.',
                'GO-WITH-NOTES': '1. **Note:** the helper in `app.py` has no test.'}.get(step, 'none')
    open(vp, 'w').write('# Deploy gate: t\nVerdict: ' + step + '\nStage: s\n## What is being pushed\n- a\n## Intent checklist\n'
                        '| # | What | Base | Head | Evidence |\n|---|---|---|---|---|\n| I-1 | totals are right (ABC-12) | FAIL | ' + head + ' | out.txt |\n'
                        + prows + '## Checks\n| Check | Result | Evidence |\n|---|---|---|\n' + ''.join(f'| c{i} | PASS | e |\n' for i in range(7))
                        + '## Findings\n' + findings + '\n## Before it goes further\nx\n## Not covered\nnone\n')
print(json.dumps({'type': 'result', 'result': 'done', 'is_error': False, 'session_id': sid}))
'''


def vtext(verdict, irows, prows='', findings='1. **x** y', checks=None):
    return ('# Deploy gate: t\nVerdict: ' + verdict + '\n## What is being pushed\n- a\n## Intent checklist\n'
            '| # | What | Base | Head | Evidence |\n|---|---|---|---|---|\n' + irows + prows + '## Checks\n'
            '| Check | Result | Evidence |\n|---|---|---|\n' + (checks if checks is not None else ''.join(f'| c{i} | PASS | e |\n' for i in range(7)))
            + '## Findings\n' + findings + '\n## Before it goes further\nx\n## Not covered\nnone\n')


OK_I = '| I-1 | sorted | FAIL | PASS | head.png |\n'


class Base(unittest.TestCase):
    """A data home, a stand-in for Claude, and a code host that is a folder on this computer."""
    KEYS = ('JARVIS_HOME', 'CLAUDE_CONFIG_DIR', 'JARVIS_CLAUDE', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM', 'STUB_PLAN', 'STUB_LOG')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(os.path.realpath(self.tmp.name))
        self.root, self.home, self.claude, self.g = t, t / 'home', t / 'claude', t / 'home' / 'deploy_gate'
        stub = t / 'claude-stub'
        stub.write_text('#!' + sys.executable + '\n' + STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.plan, self.log = t / 'plan.json', t / 'stub.log'
        gitconfig = t / 'gitconfig'
        gitconfig.write_text(f'[url "file://{t}/host/"]\n\tinsteadOf = {HOST}\n[user]\n\tname = Tester\n\temail = tester@example.com\n'
                             '[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n')
        self.keep = {k: os.environ.get(k) for k in self.KEYS}
        os.environ.update(JARVIS_HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude), JARVIS_CLAUDE=str(stub),
                          GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM='1', STUB_PLAN=str(self.plan), STUB_LOG=str(self.log))
        self.env = {k: v for k, v in os.environ.items() if (not k.startswith('JARVIS_') or k in self.KEYS) and k != 'PYTHONPATH'}
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'
        from jarvis import config, paths
        self.config = config
        paths.ensure()
        self.set(**{'owner.name': 'Sam Lee', 'owner.emails': ['sam@example.com'], 'notify.enabled': False, 'knowledge.ticket_keys': ['ABC']})
        self.usage(0.1)
        self.pings, self.added = [], []

    def tearDown(self):
        for p in self.added:
            if p in sys.path:
                sys.path.remove(p)
        for m in SIBLINGS:
            sys.modules.pop(m, None)
        for k, v in self.keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # ---- settings and the stand-ins
    def set(self, **kv):
        for k, v in kv.items():
            self.config.set(k.replace('__', '.'), v)

    def watch(self, *repos, **kv):
        self.set(**{'sources.code_url': HOST, 'gate.repos': list(repos), **kv})

    def usage(self, five_hour, status='allowed'):
        """What Claude's own meter last said (fresh, so nothing asks it again)."""
        d = self.home / 'state' / 'governor'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'window.json').write_text(json.dumps({'at': time.time(), 'status': status, 'five_hour': five_hour, 'seven_day': 0.1}))

    def steps(self, *plan):
        self.plan.write_text(json.dumps(list(plan)))

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    # ---- the code host
    def git(self, d, *a, env=None):
        p = subprocess.run(['git', '-C', str(d), *a], capture_output=True, text=True, env={**self.env, **(env or {})})
        self.assertEqual(p.returncode, 0, f"git {' '.join(a)}: {p.stderr}")
        return p.stdout.strip()

    def host(self, name='shop'):
        """A repository on the stand-in code host, with one commit on main. Returns the folder its commits are made in."""
        work = self.root / 'work' / name
        work.mkdir(parents=True)
        self.git(work, 'init', '-q', '-b', 'main')
        (work / 'app.py').write_text('def total(rows):\n    return sum(rows) * 1.2 * 1.2\n')
        self.git(work, 'add', '-A')
        self.git(work, 'commit', '-q', '-m', 'start')
        (self.root / 'host').mkdir(exist_ok=True)
        self.git(work, 'clone', '-q', '--bare', str(work), str(self.root / 'host' / (name + '.git')))
        self.git(work, 'remote', 'add', 'origin', str(self.root / 'host' / (name + '.git')))
        return work

    def commit(self, work, branch, text, msg, who='Dana', when=None, push=True, file='app.py'):
        """One commit on a branch (made from main if it is new), pushed to the host. Returns its id."""
        mail = who.split()[0].lower() + '@example.com'
        env = {'GIT_AUTHOR_NAME': who, 'GIT_AUTHOR_EMAIL': mail, 'GIT_COMMITTER_NAME': who, 'GIT_COMMITTER_EMAIL': mail}
        if when:
            env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        have = self.git(work, 'branch', '--list', branch)
        self.git(work, 'checkout', '-q', branch) if have else self.git(work, 'checkout', '-q', '-b', branch, 'main')
        (work / file).write_text(text)
        self.git(work, 'add', '-A')
        self.git(work, 'commit', '-q', '-m', msg, env=env)
        if push:
            self.git(work, 'push', '-q', 'origin', branch)
        return self.git(work, 'rev-parse', 'HEAD')

    # ---- the gate
    def cli(self, *args, script='gate.py', code=0, env=None):
        p = subprocess.run([sys.executable, str(REPO / 'gate' / script), *args], capture_output=True, text=True, env={**self.env, **(env or {})},
                           cwd=self.tmp.name, timeout=180)
        if code is not None:
            self.assertEqual(p.returncode, code, (p.stdout + p.stderr)[-2500:])
        return p.stdout + p.stderr

    def state(self):
        return json.loads((self.g / 'state.json').read_text())

    def cands(self, **want):
        """The candidates of the record that match, oldest first."""
        rows = [c for c in self.state()['candidates'].values() if all(c.get(k) == v for k, v in want.items())]
        return sorted(rows, key=lambda c: c['first_seen'])

    def health(self):
        return json.loads((self.g / 'health.json').read_text())

    def load(self, name, folder='gate'):
        """A file of gate/ as a module of this process, on the data home of this test."""
        for m in SIBLINGS:
            sys.modules.pop(m, None)
        path = REPO / folder / (name + '.py')
        spec = importlib.util.spec_from_file_location('dg_' + name + '_under_test', str(path))
        m = importlib.util.module_from_spec(spec)
        before = list(sys.path)
        spec.loader.exec_module(m)
        self.added += [p for p in sys.path if p not in before]
        if name == 'gate':                               # nothing of the test process reaches the rest of Jarvis
            m.ping = lambda *a, **k: self.pings.append(a) or True
            m.ledger_event = lambda *a, **k: None
            m.usage_ok = lambda *a, **k: (True, '')
            m.note_usage = lambda *a, **k: None
        return m


class Settings(Base):
    def test_with_nothing_set_the_gate_does_nothing_and_says_so(self):
        for cmd in ('run', 'scan', 'status'):
            self.assertIn('not set up: no code host in settings', self.cli(cmd))
        self.assertIn('not set up', self.cli('review', '--id', 'shop:branch:x@0000000000', code=1))
        self.assertFalse(self.g.exists())                                  # not a file written, not a folder made
        self.assertEqual(self.calls(), [])
        self.set(**{'sources.code_url': HOST})
        self.assertIn('no repository to watch', self.cli('run'))
        self.set(**{'sources.pr_repos': ['shop']})                         # the repositories named for pull requests are the default
        self.assertEqual(self.load('gateconf').load().repos, ['shop'])

    def test_a_line_is_for_every_repository_or_for_one(self):
        self.watch('shop', 'api', **{'gate.deploy_branches': ['main', 'api = develop release/*'], 'gate.release_tags': ['v*', 'api = api-v*'],
                                     'gate.ignore_branches': ['wip/*'], 'gate.build': ['shop = npm ci && npm run build', 'make build'],
                                     'gate.test': ['FAST=1 make test', 'web = npm test'], 'gate.ui_repos': ['shop', 'elsewhere'],
                                     'gate.max_reviews': 5, 'gate.review_minutes': 20})
        c = self.load('gateconf').load()
        self.assertEqual(c.ready(), '')
        self.assertEqual(c.deploy_branches('shop', {'main', 'develop', 'x'}, 'main'), ['main'])
        self.assertEqual(c.deploy_branches('api', {'main', 'develop', 'release/2', 'x'}, 'main'), ['develop', 'release/2'])
        self.assertEqual((c.tag_series('shop', 'v1.0'), c.tag_series('api', 'v1.0'), c.tag_series('api', 'api-v2')), ('v*', None, 'api-v*'))
        self.assertTrue(c.ignored('shop', 'wip/try') and c.ignored('shop', 'totals-backup') and not c.ignored('shop', 'ABC-12-totals'))
        self.assertEqual((c.command('build', 'shop'), c.command('build', 'api')), ('npm ci && npm run build', 'make build'))
        self.assertEqual(c.command('test', 'api'), 'FAST=1 make test')     # an equals sign inside a command is not a repository's name
        self.assertTrue(any('"web = ..." names a repository the gate does not watch' in p for p in c.problems), c.problems)
        self.assertEqual((c.ui_repos, c.max_reviews, c.review_s, c.parallel), (['shop'], 5, 1200, 1))
        shown = json.dumps(c.as_dict())
        self.assertNotIn('npm ci', shown)                                  # the screen is told which repositories have a command, never the command

    def test_defaults_and_values_that_cannot_be_used(self):
        self.watch('shop')
        c = self.load('gateconf').load()
        self.assertEqual(c.deploy_branches('shop', {'main', 'x'}, 'main'), ['main'])      # the default branch, when none is named
        self.assertEqual((c.tag_patterns('shop'), c.command('build', 'shop'), c.docker, c.env_file), ([], '', False, ''))
        self.assertEqual((c.days, c.max_reviews, c.parallel, c.max_attempts, c.review_s, c.usage_max), (7, 3, 1, 3, 3000, 0.85))
        for key, bad in (('gate.parallel', 0), ('gate.max_reviews', 2.5), ('gate.usage_max', 1.5), ('gate.repos', 'shop'), ('gate.docker', 'yes'),
                         ('gate.env_file', str(self.root / 'login.txt')), ('gate.min_free_gb', -1)):
            with self.assertRaises(self.config.ConfigError, msg=key):
                self.config.set(key, bad)
        self.set(**{'gate.env_file': str(self.root / '.env.gate')})        # the right name, but not there
        c = self.load('gateconf').load()
        self.assertEqual(c.env_file, '')
        self.assertTrue(any('does not exist' in p for p in c.problems))
        self.set(**{'gate.repos': ['shop', '../other', 'shop']})
        c = self.load('gateconf').load()
        self.assertEqual(c.repos, ['shop'])
        self.assertTrue(any('is not a repository name' in p for p in c.problems))

    def test_without_file_locks_the_gate_says_it_is_not_available(self):
        osdep = self.load('osdep')
        self.assertEqual(osdep.why_not(), '')
        osdep.fcntl = None                                                 # what a Python on Windows looks like
        self.assertIn('not available on Windows', osdep.why_not())
        with self.assertRaises(RuntimeError):
            osdep.spawn([sys.executable, '-c', 'pass'])
        self.assertFalse(osdep.held(self.root / 'nothing.lock'))


class Candidates(Base):
    def test_a_feature_branch_a_deploy_push_and_a_release_tag_are_found(self):
        work = self.host()
        self.watch('shop', **{'gate.release_tags': ['v*']})
        b1 = self.commit(work, 'ABC-12-totals', 'def total(rows):\n    return sum(rows) * 1.2\n', 'ABC-12 tax once')
        out = self.cli('scan')
        got = self.cands()
        self.assertEqual([(c['kind'], c['ref'], c['sha'], c['status']) for c in got], [('branch', 'ABC-12-totals', b1, 'pending')], out)
        c = got[0]
        self.assertEqual((c['target'], c['commits_ahead'], c['tickets'], c['mine'], c['author']), ('main', 1, ['ABC-12'], False, 'Dana'))
        self.assertIn('about to be merged into main', c['stage'])
        self.assertEqual(c['base'], self.git(work, 'rev-parse', 'main'))   # where it left main; main itself is only the baseline
        self.assertTrue(pathlib.Path(c['verdict_path']).is_relative_to(self.g) if hasattr(pathlib.Path, 'is_relative_to') else True)
        self.assertTrue((self.home / 'repos' / 'shop.git').is_dir())       # the mirror is in the data home, beside the others
        before = self.git(work, 'rev-parse', 'main')
        m1 = self.commit(work, 'main', 'def total(rows):\n    return sum(rows)\n', 'ABC-14 no tax here', who='Sam Lee')
        self.git(work, 'tag', 'v1.0')
        self.git(work, 'push', '-q', 'origin', 'v1.0')
        self.cli('scan')
        dep, tag = self.cands(kind='deploy'), self.cands(kind='tag')
        self.assertEqual([(c['ref'], c['sha'], c['base'], c['mine']) for c in dep], [('main', m1, before, True)])
        self.assertEqual([(c['ref'], c['sha'], c['series'], c['base']) for c in tag], [('v1.0', m1, 'v*', None)])
        self.commit(work, 'main', 'def total(rows):\n    return sum(rows) + 0\n', 'ABC-15 tidy')
        self.git(work, 'tag', '-a', 'v1.1', '-m', 'second release')
        self.git(work, 'push', '-q', 'origin', 'v1.1')
        self.cli('scan')
        self.assertEqual(self.cands(kind='tag')[-1]['base'], 'v1.0')        # judged against the release before it
        seen = [json.loads(l) for l in (self.g / 'ledger.jsonl').read_text().splitlines()]
        self.assertEqual([(e['kind'], e['ref']) for e in seen], [('branch', 'ABC-12-totals'), ('deploy', 'main'), ('tag', 'v1.0'), ('deploy', 'main'), ('tag', 'v1.1')])
        self.assertEqual(self.calls(), [])                                 # looking never asks Claude anything

    def test_a_newer_commit_replaces_the_older_one_that_was_waiting(self):
        work = self.host()
        self.watch('shop')
        b1 = self.commit(work, 'ABC-12-totals', 'a = 1\n', 'ABC-12 first try')
        self.cli('scan')
        b2 = self.commit(work, 'ABC-12-totals', 'a = 2\n', 'ABC-12 second try')
        self.cli('scan')
        old, new = self.cands(sha=b1)[0], self.cands(sha=b2)[0]
        self.assertEqual((old['status'], old['superseded_by'], new['status']), ('superseded', new['id'], 'pending'))
        self.assertIn('a newer push on the same branch arrived', old['superseded_reason'])
        self.commit(work, 'main', 'x = 1\n', 'one', file='x.py')            # two pushes to the deploy branch between two looks:
        m2 = self.commit(work, 'main', 'x = 2\n', 'two', file='x.py')       # one candidate, the newest, holding both
        self.cli('scan')
        self.assertEqual([(c['sha'], c['commits_ahead']) for c in self.cands(kind='deploy')], [(m2, 2)])

    def test_what_is_not_on_its_way_is_not_queued(self):
        work = self.host()
        self.watch('shop', **{'gate.ignore_branches': ['wip/*'], 'gate.days': 7})
        self.commit(work, 'feature/export', 'a = 1\n', 'ABC-20 export')
        self.commit(work, 'feature/export-backup', 'a = 1\n', 'a copy')
        self.commit(work, 'wip/idea', 'a = 3\n', 'an idea')
        self.commit(work, 'old/forgotten', 'a = 4\n', 'long ago', when='2020-01-02T10:00:00+00:00')
        self.git(work, 'branch', 'same-as-main', 'main')
        self.git(work, 'push', '-q', 'origin', 'same-as-main')
        self.cli('scan')
        self.assertEqual([c['ref'] for c in self.cands()], ['feature/export'])
        self.git(work, 'checkout', '-q', 'main')
        self.git(work, 'merge', '-q', '--ff-only', 'feature/export')
        self.git(work, 'push', '-q', 'origin', 'main')
        self.cli('scan')
        br = self.cands(kind='branch')[0]
        self.assertEqual(br['status'], 'superseded')
        self.assertIn('merged into main', br['superseded_reason'])
        self.assertEqual(len(self.cands(kind='deploy')), 1)                 # the push to the deploy branch is what is reviewed now
        self.git(work, 'push', '-q', 'origin', '--delete', 'wip/idea')
        self.commit(work, 'feature/gone', 'a = 5\n', 'ABC-21 soon deleted')
        self.cli('scan')
        self.git(work, 'push', '-q', 'origin', '--delete', 'feature/gone')
        self.cli('scan')
        gone = self.cands(ref='feature/gone')[0]
        self.assertEqual(gone['status'], 'superseded')
        self.assertIn('deleted on the code host before it was reviewed', gone['superseded_reason'])

    def test_a_network_failure_is_one_state_and_never_a_repository_problem(self):
        g = self.load('gate')
        dns = "fatal: unable to access 'https://code.example.com/acme/shop.git/': Could not resolve host: code.example.com"
        for text, net in ((dns, True), ('fetch timed out', True), ("fatal: Authentication failed for 'https://code.example.com/x'", False),
                          ('remote: Repository not found.', False), ('error: RPC failed; curl 56 Recv failure: Connection reset by peer', True)):
            self.assertEqual(bool(g.NETWORK_ERROR.search(text)), net, text)
        seq, slept = [dns, dns, ''], []
        self.assertEqual(g.fetch('shop', once=lambda r: seq.pop(0), sleep=slept.append), '')
        self.assertEqual(slept, list(g.FETCH_RETRIES_S))                    # a blip is tried again, with a wait
        tries = []
        self.assertIn('Authentication', g.fetch('shop', once=lambda r: tries.append(r) or 'fatal: Authentication failed', sleep=slept.append))
        self.assertEqual(len(tries), 1)                                     # any other error is said at once
        repos, st = ['shop', 'api'], {}
        self.assertEqual(g.network_state(st, {r: dns for r in repos}, repos), {})
        since = st['offline']['since']
        g.network_state(st, {r: dns for r in repos}, repos)
        self.assertEqual(st['offline']['since'], since)                     # a lasting outage keeps the time it began
        self.assertEqual(set(g.network_state(st, {'shop': dns, 'api': 'fatal: Authentication failed'}, repos)), {'shop', 'api'})
        self.assertNotIn('offline', st)
        self.assertEqual(g.network_state(st, {}, repos), {})
        self.assertTrue(st.get('fetched_at'))
        self.assertEqual(g._clean('fatal: https://sam:hunter2@example.com/x failed'), 'fatal: https://example.com/x failed')


class Verdicts(Base):
    def check(self, text, **kw):
        p = self.root / 'v.md'
        p.write_text(text)
        return self.g_mod.check_verdict(p, **kw)

    def setUp(self):
        super().setUp()
        self.g_mod = self.load('gate')

    def test_a_verdict_counts_only_when_it_is_complete_and_agrees_with_itself(self):
        ok = lambda text, **kw: self.check(text, **kw)[0]
        self.assertEqual(self.check(vtext('NO-GO', OK_I)), (True, 'NO-GO', ''))
        self.assertTrue(ok(vtext('GO', OK_I)))
        self.assertEqual(self.g_mod.check_verdict(self.root / 'missing.md'), (False, '', 'no verdict file written'))
        self.assertFalse(ok(vtext('MAYBE', OK_I)))
        self.assertIn('missing sections: ## Not covered', self.check(vtext('GO', OK_I).replace('## Not covered', '## Gone'))[2])
        self.assertFalse(ok(vtext('GO', '')))                                             # no intent item at all
        self.assertFalse(ok(vtext('GO', '| I-1 | x | FAIL | **FAIL** | e |\n')))          # a bold FAIL is a FAIL
        self.assertTrue(ok(vtext('NO-GO', '| I-1 | x | FAIL | **FAIL** | e |\n')))
        self.assertFalse(ok(vtext('GO-WITH-NOTES', '| I-1 | x | y | NOT VERIFIED no data | e |\n')))
        self.assertTrue(ok(vtext('NEEDS-INFO', '| I-1 | x | y | NOT VERIFIED no data | e |\n')))
        self.assertFalse(ok(vtext('GO', '| I-1 | x | y | n/a | e |\n')))                  # an intent item cannot be "not applicable"
        self.assertFalse(ok(vtext('GO', '| I-1 | x | y | NOTED a property of the pipeline | e |\n')))
        self.assertTrue(ok(vtext('GO-WITH-NOTES', '| I-1 | x | y | NOTED a property of the pipeline | e |\n')))
        self.assertFalse(ok(vtext('GO', OK_I).replace('| c0 | PASS', '| c0 | FAIL')))     # a failed check under a GO
        self.assertTrue(ok(vtext('NO-GO', OK_I).replace('| c0 | PASS', '| c0 | FAIL')))
        self.assertIn('checks table has 3 rows', self.check(vtext('GO', OK_I, checks='| a | PASS | e |\n| b | PASS | e |\n| c | PASS | e |\n'))[2])
        rows = {r['id']: r for r in self.g_mod.parse_intent_rows(vtext('NO-GO', '| I-1 | x | y | PASS | pattern a\\|b c |\n| I-2 | x | y | PASS (date) / FAIL (time) | z |\n| I-3 | x | y | | z |\n'))}
        self.assertIn('a\\|b', rows['I-1']['evidence'])                                   # an escaped bar does not shift the columns
        self.assertEqual((rows['I-2']['head'], rows['I-3']['head']), ('FAIL', 'NOT VERIFIED'))

    def test_a_change_to_a_screen_must_have_been_run(self):
        never = vtext('NEEDS-INFO', '| I-1 | x | y | NOT VERIFIED | - |\n').replace('| c0 | PASS', '| Runs locally | NOT RUN').replace('| c1 | PASS', '| Behaviour vs intent | NOT RUN')
        self.assertIn('was not run locally', self.check(never, ui=True)[2])
        self.assertTrue(self.check(never)[0])                                             # the rule is for repositories with screens only
        ran = vtext('GO', OK_I).replace('| c0 | PASS', '| Runs locally | PASS').replace('| c1 | PASS', '| Behaviour vs intent | PASS')
        self.assertTrue(self.check(ran, ui=True)[0])
        self.assertTrue(self.check(never.replace('NEEDS-INFO', 'NO-GO').replace('NOT VERIFIED', 'FAIL'), ui=True)[0])   # a broken build needs no walk

    def test_earlier_findings_must_each_be_answered_and_limit_the_verdict(self):
        P = ['P-1', 'P-2']
        prow = lambda a, b: f'| P-1 | f1 | s: FAIL | {a} | e |\n| P-2 | f2 | s: FAIL | {b} | e |\n'
        ok = lambda v, pr: self.check(vtext(v, OK_I, pr), prior_ids=P)[0]
        self.assertTrue(ok('GO', prow('PASS', 'PASS')))
        self.assertFalse(ok('GO', '| P-1 | f | s | PASS | e |\n'))                        # one is missing
        self.assertFalse(ok('GO', prow('PASS', 'PASS') + '| P-1 | f | s | PASS | e |\n'))  # one twice
        self.assertFalse(ok('GO', prow('PASS', 'PASS') + '| P-3 | f | s | PASS | e |\n'))  # one nobody asked for
        self.assertFalse(ok('GO-WITH-NOTES', prow('PASS', 'FAIL still there')))
        self.assertTrue(ok('NO-GO', prow('PASS', 'FAIL still there')))
        self.assertFalse(ok('GO', prow('PASS', 'NOTED a property of the pipeline')))
        self.assertTrue(ok('GO-WITH-NOTES', prow('PASS', 'NOTED a property of the pipeline')))
        self.assertTrue(ok('GO', prow('PASS', 'N/A nothing is deployed here')))
        self.assertFalse(ok('GO-WITH-NOTES', prow('PASS', 'NOT VERIFIED no data')))
        self.assertTrue(self.check(vtext('GO', OK_I, prow('PASS', 'PASS')))[0])            # with nothing carried, P rows are not demanded
        found = self.g_mod.parse_findings(vtext('NO-GO', OK_I, findings='1. **Blocking:** `export_job.py:3` keeps the password. It is in the diff.\n'
                                                                        '   More about it.\n2. **Stale comment** in the loader.\n3. No test covers it. Add one.'))
        self.assertEqual([f['title'] for f in found], ['export_job.py:3 keeps the password', 'Stale comment', 'No test covers it'])
        self.assertIn('More about it', found[0]['text'])

    def test_tries_are_counted_and_a_failed_one_waits_before_the_next(self):
        g = self.g_mod
        clock = [1000000.0]
        g.ts = lambda: clock[0]
        c = {'status': 'pending', 'attempts': []}
        self.assertTrue(g.due(c))
        g._attempt_failed(c, {'ts': clock[0], 'ok': False, 'error': 'missing sections'})
        self.assertEqual(c['status'], 'retry')
        self.assertFalse(g.due(c))                                                        # not at once
        clock[0] += g.BACKOFF_S[1] - 5
        self.assertFalse(g.due(c))
        clock[0] += 10
        self.assertTrue(g.due(c))                                                         # after the first wait
        g._attempt_failed(c, {'ts': clock[0], 'ok': False, 'error': 'x'})
        clock[0] += g.BACKOFF_S[1] + 5
        self.assertFalse(g.due(c))                                                        # the second wait is longer
        clock[0] += g.BACKOFF_S[2]
        self.assertTrue(g.due(c))
        g._attempt_failed(c, {'ts': clock[0], 'ok': False, 'error': 'Claude usage/rate limit', 'not_counted': True})
        self.assertEqual((c['status'], len(g.counted(c))), ('retry', 2))                  # a usage limit uses up no try
        g._attempt_failed(c, {'ts': clock[0], 'ok': False, 'error': 'x'})
        self.assertEqual(c['status'], 'stuck')                                            # the third counted try
        self.assertFalse(g.due(c))
        again = {'status': 'running', 'attempts': [{'ok': False}, {'ok': False}], 'last_verdict': 'NO-GO', 'verdict_path': str(self.root / 'none.md')}
        g._attempt_failed(again, {'ts': clock[0], 'ok': False, 'error': 'x'})
        self.assertEqual((again['status'], again['verdict']), ('done', 'NO-GO'))          # a second review that got stuck: the first verdict stands

    def test_the_reviewer_is_given_no_way_out(self):
        g = self.g_mod
        self.watch('shop')
        allowed, denied = g.tool_rules(g.gateconf.load(), 'python3')
        for rule in ('Bash(git push:*)', 'Bash(git merge:*)', 'Bash(git commit:*)', 'Bash(gh:*)', 'Bash(kubectl:*)', 'Bash(ssh:*)', 'WebFetch', 'Bash(docker push:*)'):
            self.assertIn(rule, denied)
        self.assertFalse([r for r in allowed if 'docker' in r or 'mcp__' in r or r.startswith(('Bash(env', 'Bash(git -C', 'Bash(git push'))], allowed)
        self.set(**{'gate.docker': True, 'sources.expected': ['claude.ai Atlassian']})
        g._CONF = None
        allowed, _ = g.tool_rules(g.conf(), 'python3')
        self.assertIn('Bash(docker compose:*)', allowed)
        self.assertEqual([r for r in allowed if r.startswith('mcp__')], g.TRACKER_TOOLS)
        self.assertTrue(all('getJira' in t or 'search' in t for t in g.TRACKER_TOOLS))    # reading only
        env = g.review_env({'id': 'shop:branch:x@1'}, 'dg-x')
        pairs = {env[f'GIT_CONFIG_KEY_{i}']: env[f'GIT_CONFIG_VALUE_{i}'] for i in range(int(env['GIT_CONFIG_COUNT']))}
        self.assertEqual(pairs['remote.origin.pushurl'], g.NO_PUSH)
        self.assertEqual(pairs[f'url.{g.NO_PUSH}.pushInsteadOf'], HOST)
        self.assertEqual((env['JARVIS_PROC'], env['JARVIS_LANE']), ('1', 'gate'))         # the workers' guard applies to it


class Reviews(Base):
    def one_branch(self, **settings):
        work = self.host()
        self.watch('shop', **settings)
        sha = self.commit(work, 'ABC-12-totals', 'def total(rows):\n    return sum(rows) * 1.2 * 1.2\n# a note\n', 'ABC-12 totals')
        self.cli('scan')
        return work, self.cands()[0]

    def test_a_run_reviews_what_is_due_and_keeps_its_hands_off_everything_else(self):
        work, c = self.one_branch()
        out = self.cli('run')
        done = self.cands()[0]
        self.assertEqual((done['status'], done['verdict'], len(done['attempts'])), ('done', 'GO', 1), out)
        self.assertIn('Verdict: GO', pathlib.Path(done['verdict_path']).read_text())
        self.assertIn('| GO | shop | branch | `ABC-12-totals`', (self.g / 'report.md').read_text())
        h = self.health()
        self.assertEqual((h['state'], h['ok'], h['reviewed']), ('idle', True, 1))
        call, = self.calls()
        argv = call['argv']
        self.assertEqual(argv[argv.index('--agent') + 1], 'jarvis-deploy-gate')
        self.assertEqual(argv[argv.index('--settings') + 1], str(self.home / 'state' / 'worker-settings.json'))
        dirs = [argv[i + 1] for i, x in enumerate(argv) if x == '--add-dir']
        self.assertTrue(all(d.startswith(str(self.g) + os.sep) for d in dirs), dirs)       # its worktrees and its verdict folder, nothing else
        self.assertFalse([d for d in dirs if d.startswith(str(self.home / 'repos'))])      # never the mirror
        self.assertTrue(call['cwd'].startswith(str(self.g / 'wt' / 'shop')))               # it worked in a worktree of its own
        self.assertEqual(call['env'], {'JARVIS_PROC': '1', 'JARVIS_LANE': 'gate', 'JARVIS_TASK_ID': call['env']['JARVIS_TASK_ID'], 'REGISTRY_TOKEN': None, 'DG_LOCAL_ONLY': '1'})
        for said in (c['id'], 'commit: ' + c['sha'], 'ABC-12', 'Write your verdict file to: ' + done['verdict_path'], 'time limit for the whole review: 50 minutes'):
            self.assertIn(said, call['prompt'])
        self.assertNotIn(str(self.home / 'repos'), call['prompt'])
        self.assertEqual(list((self.g / 'wt').rglob('app.py')), [])                        # the worktrees are gone again
        agent = (self.claude / 'agents' / 'jarvis-deploy-gate.md').read_text()
        self.assertIn('name: jarvis-deploy-gate', agent)
        self.assertNotIn('{{', agent)
        guard = json.loads((self.home / 'state' / 'worker-settings.json').read_text())
        self.assertIn('worker_hook.py', json.dumps(guard['hooks']))
        self.assertEqual(self.git(work, 'ls-remote', 'origin'), self.git(work, 'ls-remote', 'origin'))
        heads = self.git(self.root / 'host' / 'shop.git', 'for-each-ref', '--format=%(refname)')
        self.assertEqual(sorted(heads.split()), ['refs/heads/ABC-12-totals', 'refs/heads/main'])   # the host is as it was
        self.cli('run')
        self.assertEqual(len(self.calls()), 1)                                             # nothing is reviewed twice
        self.assertIn('GO', self.cli('status'))
        self.assertEqual(self.cli('verdict-for', 'shop', c['sha'], 'refs/heads/main').strip(), 'GO')
        self.assertEqual(self.cli('verdict-for', 'shop', '0' * 40).strip(), 'none')

    def test_a_rejected_verdict_is_tried_again_after_a_wait_and_then_the_change_is_stuck(self):
        work, c = self.one_branch()
        self.steps('bad', 'bad', 'bad')
        self.cli('run', code=None)
        first = self.cands()[0]
        self.assertEqual((first['status'], len(first['attempts'])), ('retry', 1))
        self.assertIn('missing sections', first['attempts'][0]['error'])
        vp = pathlib.Path(first['verdict_path'])
        self.assertFalse(vp.exists())                                                      # a rejected verdict is never left where a verdict is read
        self.assertTrue(vp.with_name(vp.stem + '.attempt1.md').exists())
        self.cli('run')
        self.assertEqual(len(self.calls()), 1)                                             # the scheduler waits before the next try
        self.cli('review', '--id', c['id'], code=1)                                        # the owner's own press does not wait
        self.assertIn('This is try 2. Earlier tries failed with: missing sections', self.calls()[-1]['prompt'])
        self.cli('review', '--id', c['id'], code=1)
        stuck = self.cands()[0]
        self.assertEqual((stuck['status'], len(stuck['attempts'])), ('stuck', 3))
        self.assertIn('stuck (missing sections', (self.g / 'report.md').read_text())
        sha2 = self.commit(work, 'ABC-12-totals', 'def total(rows):\n    return sum(rows)\n', 'ABC-12 again')
        self.cli('scan')
        old, new = self.cands(sha=c['sha'])[0], self.cands(sha=sha2)[0]
        self.assertEqual((old['status'], old['superseded_by']), ('superseded', new['id']))   # a newer push takes over from a stuck one
        self.assertIn('while this one was stuck', old['superseded_reason'])

    def test_a_turn_that_ends_early_is_taken_up_again_not_paid_for_twice(self):
        work, c = self.one_branch()
        self.steps('none', 'GO-WITH-NOTES')
        self.cli('review', '--id', c['id'])
        done = self.cands()[0]
        self.assertEqual((done['status'], done['verdict'], len(done['attempts'])), ('done', 'GO-WITH-NOTES', 1))
        a, b = self.calls()
        self.assertEqual((a['resume'], b['resume']), (False, True))
        self.assertIn('You ended your turn before writing the verdict file', b['prompt'])

    def test_a_usage_limit_uses_up_no_try_and_reviews_wait_for_the_window(self):
        work, c = self.one_branch()
        self.steps('limit')
        self.cli('review', '--id', c['id'], code=1)
        got = self.cands()[0]
        self.assertEqual((got['status'], got['attempts'][0].get('not_counted')), ('retry', True))
        self.assertTrue(got['attempts'][0]['error'].startswith('Claude usage/rate limit'))
        g = self.load('gate')
        got['attempts'][0]['ts'] = 0
        self.assertTrue(g.due(got))                                                        # no wait either: it was not the change's fault
        self.cli('run')
        self.assertEqual(len(self.calls()), 1)                                             # Claude said until when: nothing starts before
        self.assertIn('Claude usage limit until', self.health()['waiting'])

    def test_reviews_wait_while_the_usage_window_is_nearly_used(self):
        work, c = self.one_branch()
        self.usage(0.9)
        self.cli('run')
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.health()['waiting'], '5-hour window 90% used')
        self.assertEqual(self.cands()[0]['status'], 'pending')                             # still waiting, nothing lost
        self.usage(0.5, status='allowed_warning')
        self.cli('run')
        self.assertIn('nearly used', self.health()['waiting'])
        self.set(**{'gate.usage_max': 0.95})
        self.usage(0.9)
        self.cli('run')
        self.assertEqual(len(self.calls()), 1)                                             # the owner's own line decides
        self.assertEqual(self.cands()[0]['verdict'], 'GO')

    def test_with_an_api_key_nothing_is_reviewed_unattended(self):
        work, c = self.one_branch(**{'engine.mode': 'api-key'})
        self.cli('run')
        self.assertEqual(self.calls(), [])
        self.assertIn('automatic reviews are off with an API key', self.health()['waiting'])
        self.cli('review', '--id', c['id'])                                                # the owner's press still works
        self.assertEqual(self.cands()[0]['verdict'], 'GO')

    def test_one_scheduler_at_a_time_a_pause_and_a_limit_per_run(self):
        work = self.host()
        self.watch('shop', **{'gate.max_reviews': 2})
        for n in (1, 2, 3):
            self.commit(work, f'ABC-{n}-change', f'a = {n}\n', f'ABC-{n} change', file=f'f{n}.py')
        self.cli('scan')
        g = self.load('gate')
        with g.single_runner() as mine:
            self.assertTrue(mine)
            with g.single_runner() as second:
                self.assertFalse(second)
            self.assertIn('another gate scheduler is active', self.cli('run'))
            self.assertIn('another gate run is active', self.cli('scan', code=1))
        self.assertEqual(self.calls(), [])
        cid = self.cands()[0]['id']
        with g.claim(cid) as got:                                                          # a change under review is not reviewed beside itself
            self.assertTrue(got and g.claim_held(cid))
            self.assertEqual([c['id'] for c in g._todo(self.state()) if c['id'] == cid], [])
            self.assertIn('already being reviewed', self.cli('review', '--id', cid))
        self.assertFalse(g.claim_held(cid))
        (self.g / 'PAUSED').write_text('holding while the rules change\n')
        self.cli('run')
        self.assertEqual(self.calls(), [])
        self.assertIn('paused (holding while the rules change)', self.health()['waiting'])
        (self.g / 'PAUSED').unlink()
        (self.g / 'MAINTENANCE').write_text('upgrade\n')
        self.assertIn('maintenance switch is on', self.cli('run'))
        (self.g / 'MAINTENANCE').unlink()
        self.cli('run')
        self.assertEqual(len(self.calls()), 2)                                             # the run's limit
        self.assertIn('limit of 2 reviews; 1 left for the next run', self.health()['waiting'])
        self.assertEqual(sorted(c['status'] for c in self.cands()), ['done', 'done', 'pending'])

    def test_a_review_is_stopped_at_its_time_limit(self):
        work, c = self.one_branch()
        self.steps('sleep')
        t0 = time.time()
        self.cli('review', '--id', c['id'], '--timeout', '2', code=1)
        self.assertLess(time.time() - t0, 60)
        got = self.cands()[0]
        self.assertEqual(got['status'], 'retry')
        self.assertIn('timed out', got['attempts'][0]['error'])
        self.assertEqual(list((self.g / 'wt').rglob('app.py')), [])

    def test_the_reviewer_cannot_push(self):
        work, c = self.one_branch()
        self.steps('push')
        self.cli('review', '--id', c['id'])
        self.assertNotEqual(self.calls()[0]['push_rc'], 0)
        self.assertNotIn('pushed-by-the-reviewer', self.git(self.root / 'host' / 'shop.git', 'for-each-ref', '--format=%(refname)'))

    def test_a_fix_must_answer_each_earlier_finding(self):
        work, c = self.one_branch()
        self.steps('NO-GO', 'GO')
        self.cli('run')
        self.assertEqual(self.cands()[0]['verdict'], 'NO-GO')
        sha2 = self.commit(work, 'ABC-12-totals', 'def total(rows):\n    return sum(rows) * 1.2\n# a note\n', 'ABC-12 tax once')
        self.cli('run')
        old, new = self.cands(sha=c['sha'])[0], self.cands(sha=sha2)[0]
        self.assertEqual([f['p'] for f in new['prior']['findings']], ['P-1'])
        self.assertEqual(new['prior']['findings'][0]['title'], 'app.py:2 adds the tax twice')
        prompt = self.calls()[-1]['prompt']
        self.assertIn('THIS CHANGE FOLLOWS AN EARLIER NO-GO REVIEW', prompt)
        self.assertIn('P-1: app.py:2 adds the tax twice', prompt)
        self.assertEqual((new['verdict'], new['fixed_prior'], new['prior_results']['pass']), ('GO', True, 1))
        self.assertEqual((old['status'], old['prior_verdict'], old['superseded_by']), ('superseded', 'NO-GO', new['id']))
        self.assertEqual([r['verdict'] for r in new['replaces']], ['NO-GO'])               # the history is kept on the newer one

    def test_the_same_code_on_the_same_base_is_not_reviewed_twice(self):
        work, c = self.one_branch()
        self.cli('run')
        self.git(work, 'checkout', '-q', 'main')
        self.git(work, 'merge', '-q', '--no-ff', '-m', 'Merge ABC-12-totals', 'ABC-12-totals')
        self.git(work, 'push', '-q', 'origin', 'main')
        self.cli('run')
        dep = self.cands(kind='deploy')[0]
        self.assertEqual((dep['status'], dep['verdict'], dep['same_code_as']), ('done', 'GO', c['id']))
        self.assertEqual(len(self.calls()), 1)

    def test_open_findings_are_checked_again_on_the_code_as_it_is_now(self):
        work, c = self.one_branch()
        self.steps('NO-GO')
        self.cli('run')
        rc = json.loads((self.g / 'recheck.json').read_text())
        f, = rc['verdicts'][c['id']]['findings']
        self.assertEqual((f['status'], f['severity'], f['title']), ('STILL THERE', 'blocking', '`app.py:2` adds the tax twice. Totals are too high.'))
        self.assertEqual(f['anchors'][0]['at']['ABC-12-totals']['state'], 'there')
        self.assertIn('Tester', f['tell'][0])                                              # whoever wrote that line, not whoever pushed last
        (self.g / 'PAUSED').write_text('no review of the next push\n')
        fix = self.commit(work, 'ABC-12-totals', 'def total(rows):\n    return sum(rows) * 1.2\n# a note\n', 'ABC-12 tax once', who='Priya')
        self.cli('scan')
        state = self.state()
        state['candidates'][self.cands(sha=fix)[0]['id']]['status'] = 'superseded'          # as if nobody had reviewed the fix yet
        state['candidates'][c['id']]['status'] = 'done'
        (self.g / 'state.json').write_text(json.dumps(state))
        out = self.cli(script='recheck.py')
        self.assertIn('1 open verdicts re-checked', out)
        f, = json.loads((self.g / 'recheck.json').read_text())['verdicts'][c['id']]['findings']
        self.assertEqual(f['status'], 'LIKELY FIXED')
        self.assertEqual((f['fixed_by'][0]['author'], f['fixed_by'][0]['commit']), ('Priya', fix[:len(f['fixed_by'][0]['commit'])]))


class Prep(Base):
    def test_the_owners_commands_run_before_the_reviewer_and_their_login_is_never_shown(self):
        work = self.host()
        env_file = self.root / '.env.gate'
        env_file.write_text('# the registry\nexport REGISTRY_TOKEN="made-up-token-123"\nSHORT=ab\n')
        self.watch('shop', **{'gate.build': ['echo building with $REGISTRY_TOKEN && test -f app.py'],
                              'gate.test': ['shop = grep -q "1.2 \\* 1.2" app.py && echo the tax is there twice'], 'gate.env_file': str(env_file)})
        self.commit(work, 'ABC-12-totals', 'def total(rows):\n    return sum(rows) * 1.2\n', 'ABC-12 tax once')
        self.cli('scan')
        c = self.cands()[0]
        self.cli('review', '--id', c['id'])
        call, = self.calls()
        self.assertIsNone(call['env']['REGISTRY_TOKEN'])                                   # the reviewer's own environment never holds it
        self.assertNotIn('made-up-token-123', call['prompt'])
        self.assertIn("THE HARNESS HAS ALREADY RUN THE OWNER'S BUILD AND TEST COMMANDS", call['prompt'])
        vp = pathlib.Path(c['verdict_path'])
        rec = json.loads(vp.with_name(vp.stem + '.prep.json').read_text())
        self.assertEqual([(s['name'], s['rc']) for s in rec['head']['steps']], [('build', 0), ('test', 1)])   # the fix: the tax is there once
        self.assertEqual([(s['name'], s['rc']) for s in rec['base']['steps']], [('build', 0), ('test', 0)])   # the base still has it twice
        log = vp.with_name(vp.stem + '.build.head.log').read_text()
        self.assertEqual(log.strip(), 'building with ***')                                 # the command had the value; the log does not
        everything = ''.join(f.read_text(errors='replace') for f in self.g.rglob('*') if f.is_file())
        self.assertNotIn('made-up-token-123', everything)
        self.assertIn('exit 1', call['prompt'])
        self.assertIn('prep.py again --repo shop --step <build|test>', call['prompt'])

    def test_a_failed_build_skips_the_tests_and_a_step_is_only_run_again_in_a_worktree_of_the_gate(self):
        self.watch('shop', **{'gate.build': ['exit 3'], 'gate.test': ['echo never']})
        prep = self.load('prep', 'gate/tools')
        wt = self.root / 'somewhere'
        wt.mkdir()
        rec = prep.run('shop', wt, 'a' * 40, self.root / 'out', 'stem', log=lambda m: None)
        self.assertEqual([(s['name'], s['rc']) for s in rec['head']['steps']], [('build', 3)])
        self.assertIn('head: test was not run, because the step before it failed', rec['notes'])
        self.assertIn('build  `exit 3`  exit 3', prep.summary(rec))
        said = []
        self.assertEqual(prep.again('shop', 'test', wt, out=type('O', (), {'write': lambda s, x: said.append(x)})()), 2)
        self.assertIn('not a worktree of the deploy gate', ''.join(said))
        self.assertEqual(prep.env_values(str(self.root / 'missing')), {})
        self.assertEqual(prep.blank('a made-up-token-123 b ab', ['made-up-token-123', 'ab']), 'a *** b ab')


class Reviewer(Base):
    def test_the_instructions_are_filled_in_from_settings_and_a_hand_edit_is_kept(self):
        r = self.load('reviewer')
        self.assertEqual(r.install(check=True), {'jarvis-deploy-gate': 'installed'})
        self.assertFalse(r.target().exists())
        self.assertEqual(r.install(), {'jarvis-deploy-gate': 'installed'})
        text = r.target().read_text()
        self.assertEqual(text.split('\n')[1], 'name: jarvis-deploy-gate')
        self.assertIn('tools: Read, Grep, Glob, Write, Edit, Bash\n', text)                 # no tracker connected: no tracker tool
        for absent in ('{{', '[[if', 'mcp__', 'docker compose', 'REQUIRED for'):
            self.assertNotIn(absent, text, absent)
        self.assertIn('repositories Sam Lee looks after', text)
        self.assertIn('No tracker is connected', text)
        self.assertIn('Containers are\n  not available in this setup', text)
        self.assertIn(str(REPO / 'gate' / 'tools' / 'stress.py'), text)
        self.assertEqual(r.install(), {'jarvis-deploy-gate': 'same'})
        self.watch('shop', **{'gate.docker': True, 'gate.ui_repos': ['shop'], 'sources.expected': ['claude.ai Atlassian']})
        self.assertEqual(r.install(), {'jarvis-deploy-gate': 'updated'})
        text = r.target().read_text()
        for present in ('Bash, mcp__claude_ai_Atlassian_Rovo__getJiraIssue', 'Read every ticket with the tracker tool', 'docker compose -p',
                        'Screens (REQUIRED for shop'):
            self.assertIn(present, text, present)
        r.target().write_text(text.replace('You are the deploy gate', 'You are my deploy gate'))
        self.set(**{'gate.docker': False})
        self.assertEqual(r.install(), {'jarvis-deploy-gate': 'kept: you edited it'})
        self.assertIn('You are my deploy gate', r.target().read_text())


class Module(Base):
    def setUp(self):
        super().setUp()
        sys.modules.pop('jarvis.modules.deploy_gate', None)
        from jarvis.modules import deploy_gate
        self.m = deploy_gate

    def tearDown(self):
        sys.modules.pop('jarvis.modules.deploy_gate', None)
        super().tearDown()

    def test_the_screen_is_told_what_is_missing_and_then_what_was_found(self):
        self.assertEqual([(n, every) for n, every, _ in self.m.jobs()], [('deploy_gate', 1800)])
        self.assertEqual(sorted(self.m.GET), ['/api/deploy_gate', '/api/deploy_gate_verdict'])
        st = self.m.status({})
        self.assertEqual((st['ready'], st['candidates'], st['running'], st['paused']), (False, [], False, None))
        self.assertIn('no code host in settings', st['why'])
        self.assertEqual(self.m.review({}), {'ok': False, 'error': st['why']})                # nothing starts while nothing is set
        self.assertEqual(self.calls(), [])
        work = self.host()
        self.watch('shop', **{'gate.build': ['make build']})
        sha = self.commit(work, 'ABC-12-totals', 'def total(rows):\n    return sum(rows) * 1.2 * 1.2\n# a note\n', 'ABC-12 totals')
        self.steps('NO-GO')
        self.cli('run')
        st = self.m.status({})
        self.assertTrue(st['ready'])
        self.assertEqual(st['settings']['repos'], ['shop'])
        self.assertNotIn('make build', json.dumps(st))
        c, = st['candidates']
        self.assertEqual((c['repo'], c['ref'], c['sha'], c['status'], c['verdict'], c['has_verdict'], c['attempts']),
                         ('shop', 'ABC-12-totals', sha, 'done', 'NO-GO', True, 1))
        self.assertNotIn('verdict_path', c)                                                 # the screen asks for the text by id, never by path
        issue, = st['issues']
        self.assertEqual((issue['severity'], issue['places'], issue['branches'], issue['cids']), ('blocking', ['app.py:2'], ['ABC-12-totals'], [c['id']]))
        self.assertEqual((st['health']['state'], st['scanned_at'] is not None), ('idle', True))
        self.assertIn('Verdict: NO-GO', self.m.verdict({'id': c['id']})['text'])
        self.assertEqual(self.m.verdict({'id': 'nothing'})['error'], 'unknown change')
        state = self.state()
        outside = self.root / 'outside.md'
        outside.write_text('not a verdict')
        state['candidates'][c['id']]['verdict_path'] = str(outside)
        (self.g / 'state.json').write_text(json.dumps(state))
        self.assertEqual(self.m.verdict({'id': c['id']}), {'text': '', 'error': 'no verdict yet'})   # only a file of the gate's own folder
        self.assertEqual(self.m.review({'id': 'nothing'}), {'ok': False, 'error': 'unknown change'})
        self.assertEqual(self.m.pause({'on': True, 'why': 'release  week'}), {'ok': True, 'paused': True})
        self.assertEqual(self.m.status({})['paused']['why'], 'release week')
        self.assertEqual(self.m.pause({'on': False}), {'ok': True, 'paused': False})
        self.assertIsNone(self.m.status({})['paused'])
        self.assertEqual(len(list(self.g.glob('PAUSED.lifted-*'))), 1)                     # the note is kept, not deleted

    def test_the_setup_screen_says_whether_the_gate_has_something_to_watch(self):
        from jarvis import setup
        setup.forget()
        part = lambda: next(p for p in setup.optional() if p['id'] == 'deploy_gate')
        self.assertEqual((part()['on'], part()['status']), (False, 'off'))
        self.set(**{'modules.deploy_gate': True})
        self.assertTrue(part()['on'])                                                      # the switch is the switch, so it can be switched off again
        self.assertIn('switched on, but not set up: no code host', part()['status'])
        self.watch('shop', 'api')
        self.assertEqual((part()['on'], part()['status']), (True, 'on, watching 2 repositories'))


if __name__ == '__main__':
    unittest.main()
