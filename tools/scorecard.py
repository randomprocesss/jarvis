#!/usr/bin/env python3
"""The scorecard: how well Jarvis is doing its job. One number per metric, counted from the records, never estimated:
Jarvis's ledger (state/jarvis.db, read-only), the knowledge base's state files and, when it is switched on, the vault.

    scorecard.py            print the scorecard and write state/scorecard.json (+ one line a day in scorecard_history.jsonl)
    scorecard.py --json     the same as JSON on stdout

cached(max_age) is what the tracker calls: the saved file if it is fresh, else a new count (under a second).
Each metric: key, label, value (a number or None when it cannot be counted yet), show (the text), target, ok (True / False /
None), src (where it was counted). A metric that fails to count says why in `show`; it never breaks the others.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import datetime as dt, json, os, re, sqlite3, subprocess, sys, time

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'jarvis.db')
OUT = os.path.join(HUB, 'state', 'scorecard.json')
HIST = os.path.join(HUB, 'state', 'scorecard_history.jsonl')
DAYS = 14                                                  # rates are over the last 14 days: a rate since 5 Oct hides change


def _db():
    if not os.path.exists(DB):                             # before Jarvis has ever run: an empty ledger, so every metric reads 0
        paths.ensure()
        from jarvis.spine import ledger
        ledger.connect().close()
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _since(days=DAYS):
    return (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec='seconds')


def _rate(c, sql_bad, sql_all, args=()):
    bad, tot = c.execute(sql_bad, args).fetchone()[0], c.execute(sql_all, args).fetchone()[0]
    return bad, tot, (bad / tot if tot else None)


def _m(key, label, value, show, target, ok, src):
    return {'key': key, 'label': label, 'value': value, 'show': show, 'target': target, 'ok': ok, 'src': src}


def _pct(x):
    return '-' if x is None else f'{100 * x:.1f}%'


def metrics():
    out, c = [], _db()
    s = _since()

    def add(fn):
        try:
            out.append(fn())
        except Exception as e:                             # one metric that cannot be counted never hides the rest
            out.append(_m(fn.__name__, fn.__name__.replace('_', ' '), None, f'could not count: {str(e)[:80]}', '', None, ''))

    def must_fix():
        rows = c.execute("SELECT data FROM events WHERE kind='review' AND at >= ?", (s,)).fetchall()
        v = [(json.loads(r['data'] or '{}') or {}).get('verdict') for r in rows]
        bad, tot = sum(1 for x in v if x == 'must_fix'), sum(1 for x in v if x in ('must_fix', 'ok'))
        r = bad / tot if tot else None
        return _m('must_fix', "Work Jarvis's own reviewer sent back", r, f'{_pct(r)} ({bad} of {tot})', 'under 15%',
                  None if r is None else r < 0.15, f'events kind=review, last {DAYS} days')

    def tasks_failed():
        bad, tot, r = _rate(c, "SELECT COUNT(*) FROM tasks WHERE state='failed' AND created >= ?", "SELECT COUNT(*) FROM tasks WHERE created >= ?", (s,))
        return _m('tasks_failed', 'Tasks that failed', r, f'{_pct(r)} ({bad} of {tot})', 'under 2%', None if r is None else r < 0.02,
                  f'tasks, last {DAYS} days')

    def roundups_failed():
        bad, tot, r = _rate(c, "SELECT COUNT(*) FROM tasks WHERE kind='run-roundup' AND state='failed' AND created >= ?",
                            "SELECT COUNT(*) FROM tasks WHERE kind='run-roundup' AND created >= ?", (s,))
        return _m('roundups_failed', 'Inbox checks that failed', r, f'{_pct(r)} ({bad} of {tot})', 'under 1%', None if r is None else r < 0.01,
                  f'tasks kind=run-roundup, last {DAYS} days')

    def longest_gap():
        rows = [r[0] for r in c.execute("SELECT ended FROM tasks WHERE kind='run-roundup' AND state='done' AND ended >= ? ORDER BY ended", (s,))]
        ts = [dt.datetime.fromisoformat(x) for x in rows if x]
        now = dt.datetime.now(ts[-1].tzinfo) if ts else None
        gaps = [(b - a).total_seconds() / 3600 for a, b in zip(ts, ts[1:] + [now])] if ts else []
        g = max(gaps) if gaps else None
        return _m('longest_gap', 'Longest stretch with no successful inbox check', g, '-' if g is None else f'{g:.1f} h', 'under 3 h',
                  None if g is None else g < 3, f'tasks kind=run-roundup state=done, last {DAYS} days (nights included)')

    def proof_tied():
        paths.use_tools()
        from jarvis import proof
        import glob
        cl = ti = rep = repok = 0
        cut = time.time() - DAYS * 86400
        for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
            if os.path.getmtime(f) < cut:
                continue
            rid = os.path.basename(f)[:-5]
            try:
                d = json.load(open(f))
            except ValueError:
                continue
            for aid, w in d.items():
                if not isinstance(w, dict) or aid.startswith('_') or str(w.get('updated') or '') < s:
                    continue
                v = proof.verdict(rid, aid, w)
                cl += v['claims']; ti += v['tied']
                if any(wh == 'reply' for wh, _ in proof.texts_of(w)):
                    rep += 1
                    repok += not any(u['where'] == 'reply' for u in v['untied']) and not v['scope']
        r = ti / cl if cl else None
        held = c.execute("SELECT COUNT(*) FROM events WHERE kind='proof_held' AND at >= ?", (s,)).fetchone()[0]
        return _m('proof_tied', 'Numbers tied to an output file or receipt', r, f'{_pct(r)} ({ti:,} of {cl:,}); replies fully tied {repok} of {rep}; drafts held {held}',
                  '100%', None if r is None else r >= 0.999, f'tools/proof.py over work entries, last {DAYS} days')

    def cli_login():
        r = subprocess.run([(paths.claude_bin() or 'claude'), 'auth', 'status'], capture_output=True, text=True, timeout=30)
        ok = json.loads(r.stdout or '{}').get('loggedIn') is True
        return _m('cli_login', "Jarvis's own login (background runs)", 1 if ok else 0, 'ok' if ok else 'LOGGED OUT: run `claude auth login`',
                  'ok', ok, 'claude auth status, now')

    def outward_classes():
        paths.use_tools()
        from jarvis import ladder
        cl = ladder.ladder()['classes']
        above = sum(1 for x in cl if x['level'] in ('L3', 'L4', 'L5'))
        elig = [x['label'] for x in cl if x['eligible']]
        best = max(cl, key=lambda x: x['run'])
        return _m('outward_classes', 'Outward action classes that may act without your press', above,
                  f"{above} of {len(cl)}; eligible for the next level: {len(elig)}; longest run: {best['run']} of {best['need']} ({best['label']})",
                  '8 or more', above >= 8, 'tools/ladder.py (advisory)')

    def shadow():
        try:
            paths.use_tools()
            import shadow_score
            shadow_score.update()                          # from the work entries, code only
        except Exception:
            pass
        try:                                               # what the policy gate would say about each drafted reply (advisory)
            import glob
            from jarvis.spine import policy
            cut = time.time() - DAYS * 86400
            for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
                if os.path.getmtime(f) < cut:
                    continue
                for aid, w in (json.load(open(f)) or {}).items():
                    if isinstance(w, dict) and not aid.startswith('_') and str((w.get('reply') or {}).get('text') or '').strip() and not w.get('merged_into'):
                        policy.would(os.path.basename(f)[:-5], aid)
                        try:                               # 13 Nov: the findings as they stand now, for the replay exam (one file per text version)
                            import replay_exam
                            replay_exam.snapshot(os.path.basename(f)[:-5], aid, w)
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            n, aw, av = c.execute("SELECT COUNT(agree), SUM(outcome='as_written'), AVG(agree) FROM shadow WHERE agree IS NOT NULL AND at >= ?", (s,)).fetchone()
            N, AW = c.execute("SELECT COUNT(agree), SUM(outcome='as_written') FROM shadow WHERE agree IS NOT NULL").fetchone()
        except sqlite3.OperationalError:
            return _m('shadow', "Jarvis's drafts you sent as written", None, 'not measured yet', '90% per class', None, '')
        r = (aw or 0) / n if n else None
        return _m('shadow', "Jarvis's drafts you sent as written", r,
                  f'{_pct(r)} ({aw or 0} of {n}, last {DAYS} days); {AW or 0} of {N} since 5 Oct', '90% per class, 20 in a row',
                  None if r is None else r >= 0.9, 'shadow (tools/shadow_score.py): wording only, no model judge yet')

    def goals():
        tot = c.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM goals WHERE state='confirmed'").fetchone()[0]
        return _m('goals', 'Goals you confirmed', n, f'{n} of {tot} (the rest only inferred)', '5 to 8', 5 <= n <= 8, 'goals')

    def open_needs():
        n = c.execute("SELECT COUNT(*) FROM needs WHERE state='open'").fetchone()[0]
        closed = c.execute("SELECT COUNT(*) FROM needs WHERE state IN ('resolved','answered','satisfied')").fetchone()[0]
        return _m('open_needs', 'Open questions', n, f'{n:,} open, {closed} ever closed', 'under 150', n < 150, 'needs')

    def waiting():
        rows = [r[0] for r in c.execute("SELECT created FROM items WHERE needs_you=1 AND done=0 ORDER BY created")]
        age = (dt.datetime.now() - dt.datetime.fromisoformat(rows[0][:19])).days if rows else 0
        return _m('waiting', 'Items waiting on you', len(rows), f'{len(rows)}, oldest {age} days', 'under 5, none over 3 days',
                  len(rows) < 5 and age <= 3, 'items needs_you=1 done=0')

    def promises():
        r = {x[0]: x[1] for x in c.execute("SELECT state, COUNT(*) FROM commitments GROUP BY 1")}
        return _m('promises', 'Promises tracked', r.get('open', 0), f"{r.get('open', 0)} open, {r.get('stale', 0)} stale", 'none stale',
                  r.get('stale', 0) == 0, 'commitments')

    def self_improve():
        cut = _since(7)
        kept = c.execute("SELECT COUNT(*) FROM autopilot WHERE state='kept' AND at >= ?", (cut,)).fetchone()[0]
        tried = c.execute("SELECT COUNT(*) FROM autopilot WHERE at >= ?", (cut,)).fetchone()[0]
        allk, allt = c.execute("SELECT SUM(state='kept'), COUNT(*) FROM autopilot").fetchone()
        return _m('self_improve', 'Self-improvement changes kept this week', kept, f'{kept} of {tried} this week ({allk or 0} of {allt} since 5 Oct)',
                  '2 or more a week', kept >= 2, 'autopilot')

    def exam():
        h = json.load(open(os.path.join(HUB, 'state', 'kb_exam.json')))['history'][-1]
        return _m('exam', 'Knowledge-base exam', h['score'], f"{100 * h['score']:.0f}% on {h['n']} questions ({h['at'][:10]})", '95% on 100 or more',
                  h['score'] >= 0.95 and h['n'] >= 100, 'state/kb_exam.json')

    def ledger():
        d = json.load(open(os.path.join(HUB, 'state', 'kb_ledger_cache.json')))
        n = sum(len(e.get('records') or []) for e in d.values())
        return _m('ledger', 'Fact ledger', n, f'{n:,} records from {len(d):,} lines', 'every settled line', None, 'state/kb_ledger_cache.json')

    def vault():
        from jarvis.world import vault as V
        VAULT = V.root()
        if not VAULT:
            return _m('vault', 'Vault: database views / maps / notes over 200 KB', None, 'the vault is switched off', '', None, 'settings')
        bases = canv = big = notes = 0
        for dp, dn, fn in os.walk(VAULT):
            if '/.obsidian' in dp or '/.trash' in dp:
                continue
            for f in fn:
                p = os.path.join(dp, f)
                if f.endswith('.base'):
                    bases += 1
                elif f.endswith('.canvas'):
                    canv += os.path.getsize(p) > 50
                elif f.endswith('.md'):
                    notes += 1
                    big += os.path.getsize(p) > 200 * 1024
        return _m('vault', 'Vault: database views / maps / notes over 200 KB', bases, f'{bases} / {canv} / {big} ({notes:,} notes)',
                  '8+ / one per workstream / 0', bases >= 8 and big == 0, 'the vault folder')

    for fn in (must_fix, proof_tied, tasks_failed, roundups_failed, longest_gap, cli_login, outward_classes, shadow, goals, open_needs, waiting,
               promises, self_improve, exam, ledger, vault):
        add(fn)
    c.close()
    return out


def compute():
    d = {'at': dt.datetime.now().isoformat(timespec='seconds'), 'metrics': metrics()}
    tmp = OUT + '.tmp'
    json.dump(d, open(tmp, 'w'), indent=1)
    os.replace(tmp, OUT)
    today = d['at'][:10]
    try:
        last = open(HIST).read().strip().splitlines()[-1][:22]
    except (OSError, IndexError):
        last = ''
    if today not in last:                                  # one line a day: the trend
        with open(HIST, 'a') as f:
            f.write(json.dumps({'date': today, **{m['key']: m['value'] for m in d['metrics']}}) + '\n')
    return d


def cached(max_age=600):
    try:
        if time.time() - os.path.getmtime(OUT) < max_age:
            return json.load(open(OUT))
    except (OSError, ValueError):
        pass
    return compute()


if __name__ == '__main__':
    d = compute()
    if '--json' in sys.argv:
        print(json.dumps(d, indent=1))
    else:
        print(f"Scorecard, counted {d['at']}")
        for m in d['metrics']:
            mark = {True: 'ok ', False: 'NO ', None: ' . '}[m['ok']]
            print(f" {mark} {m['label'][:52]:52s} {m['show'][:60]:60s} target: {m['target']}")
