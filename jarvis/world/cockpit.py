"""Data for the redesigned app (12 Nov 2026; the owner: "properly redesign the Jarvis app"). Read-only: every function here
reads state that something else writes (the ladder, the scorecard, the plan, the fact ledger, Jarvis's own tasks and
actions, the research trials). Nothing is sent, changed or started from here.

  autonomy()   how far Jarvis may act: the ladder per kind of action (level, their ceiling, the evidence), the scorecard, the
               plan's work items, the proof gate's mode, what the advisory gate said about recent replies
  kb(q)        with q: the best matching ledger records, an old value with the record in force, a conflict with its other
               side; without q: the knowledge base at a glance (counts, the exam, unsettled conflicts, open questions judged
               still open, promises owed, goals)
  activity()   what Jarvis did: finished tasks with their result, and its outward actions (the action ledger)
  research()   outside research: every finding, where its trial stands, and the verdict
"""
from jarvis import paths
import collections, json, os, sqlite3, sys, time

HUB = str(paths.home())
TOOLS = os.path.join(str(paths.REPO), 'tools')
LED = str(paths.kb('ledger'))
DB = os.path.join(HUB, 'state', 'jarvis.db')
_CACHE = {}


def _j(p, d=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def _q(sql, args=()):
    try:
        con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5); con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []                                    # no ledger yet (a first start): nothing to show
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _tool(name):
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    return __import__(name)


def autonomy(q=None):
    lad = _j(os.path.join(HUB, 'state', 'ladder.json'), {}) or {}
    sc = _j(os.path.join(HUB, 'state', 'scorecard.json'), {}) or {}
    try:
        plan = [dict(zip(('id', 'state', 'title', 'detail'), r[:4])) if not isinstance(r, dict) else r for r in _tool('autonomy_plan').rows()]
    except Exception as e:
        plan = [{'id': '', 'state': 'unknown', 'title': 'The plan could not be read', 'detail': f'{e.__class__.__name__}: {e}'[:160]}]
    try:
        mode = _tool('proof').mode()
    except Exception:
        mode = 'unknown'
    log = _q("SELECT at, rid, aid, cls, level, allow, mode, failed FROM policy_log ORDER BY at DESC LIMIT 30")
    classes, on, heldrows = lad.get('classes') or [], {}, []
    try:
        from jarvis.spine import policy
        on = policy.acting_on()
        heldrows = sorted(policy.held(), key=lambda e: e.get('held_at') or '', reverse=True)[:30]
        for c in classes:
            c['level'] = policy.level_of(c['cls'])                       # the level in force (his switch), not the default
            c['release_path'] = c['cls'].startswith('slack_') or c['cls'] == 'jira_comment'   # what the release path can send (13 Nov: Jira comments too)
            c['can_raise'] = bool(c.get('eligible')) and policy.ORDER.index(c['level']) < policy.ORDER.index(c.get('ceiling') or 'L1') and c['release_path']
            c['can_lower'] = policy.ORDER.index(c['level']) > policy.ORDER.index('L2')
    except Exception:
        pass
    note = ('These kinds of action go out by themselves after a 10-minute hold: ' + ', '.join(sorted(on)) + '. You can stop any held reply, and lower a level, at any time.') if on else \
           ('Nothing goes out by itself. A kind of action can be raised to "holds, then sends" only after its last 20 replies went out exactly as drafted, '
            'and only by your switch on this page in the Jarvis app. No kind has earned it yet.')
    return {'ladder': classes, 'ladder_at': lad.get('at'), 'ladder_note': lad.get('note'),
            'scorecard': sc.get('metrics') or [], 'scorecard_at': sc.get('at'), 'plan': plan, 'proof_mode': mode, 'gate_log': log,
            'held': [{k: e.get(k) for k in ('id', 'rid', 'aid', 'cls', 'level', 'to', 'where', 'first_line', 'held_at', 'send_at', 'state', 'why', 'stopped_by')} for e in heldrows],
            'acting': bool(on), 'acting_note': note}


def _overview():
    rec = os.path.join(LED, 'records.jsonl')
    try:
        key = (os.path.getmtime(rec), os.path.getmtime(os.path.join(LED, 'relations.json')) if os.path.exists(os.path.join(LED, 'relations.json')) else 0)
    except OSError:                                  # no knowledge base yet: an honest empty picture, not an error
        return {'records': 0, 'current': 0, 'superseded': 0, 'with_pointer': 0, 'workstreams': [], 'conflicts': [], 'refreshed': None}
    if _CACHE.get('ov_key') == key:
        return _CACHE['ov']
    byid, st, ws = {}, collections.Counter(), collections.Counter()
    for l in open(rec):
        try:
            r = json.loads(l)
        except ValueError:
            continue
        byid[r['id']] = (r['statement'], r['note'], r['status'], r.get('workstream') or '')
        st[r['status']] += 1
        if r['status'] == 'current':
            ws[r.get('workstream') or ''] += 1
        elif r.get('superseded_by'):
            st['pointer'] += 1
    conf = []
    for g in _j(os.path.join(LED, 'relations.json'), []) or []:
        if g.get('relation') == 'contradiction':
            ids = [x for x in g.get('ids') or [] if x in byid]
            if len(ids) >= 2:
                conf.append({'ids': ids, 'why': str(g.get('why') or '')[:400],
                             'records': [{'id': x, 'statement': byid[x][0][:300], 'note': byid[x][1], 'workstream': byid[x][3]} for x in ids[:4]]})
    ov = {'records': len(byid), 'current': st['current'], 'superseded': st['superseded'], 'with_pointer': st['pointer'],
          'workstreams': [{'name': k, 'current': v} for k, v in ws.most_common() if k], 'conflicts': conf, 'refreshed': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(key[0]))}
    _CACHE.update(ov_key=key, ov=ov)
    return ov


def _jarvis_records():
    out = collections.defaultdict(list)
    try:
        for l in open(os.path.join(LED, 'jarvis_records.jsonl')):
            r = json.loads(l)
            out[r.get('kind')].append(r)
    except (OSError, ValueError):
        pass
    return out


def kb(q=None):
    q = q or {}
    text = str(q.get('q') or '').strip()[:300]
    if text:
        KF = _tool('kb_facts')
        recs = KF.find(text, k=14, superseded=5)
        ids = [x for r in recs for k in ('superseded_by', 'contradicts') for x in (r.get(k) or '').split(',') if x]
        now = KF._statements(ids)
        for r in recs:
            r['origin'] = 'jarvis' if str(r['note']).startswith('jarvis:') else 'ledger'
            r['in_force'] = [{'id': x, 'statement': now[x][0][:400], 'note': now[x][1]} for x in (r.get('superseded_by') or '').split(',') if x in now]
            r['conflicts'] = [{'id': x, 'statement': now[x][0][:400], 'note': now[x][1]} for x in (r.get('contradicts') or '').split(',') if x in now][:3]
        return {'q': text, 'records': recs}
    ov = dict(_overview())
    jr = _jarvis_records()
    pri = {'high': 0, 'medium': 1, 'low': 2}
    ov['open_questions'] = sorted([{k: r.get(k) for k in ('id', 'statement', 'workstream', 'priority', 'as_of', 'why', 'need_kind', 'note')}
                                   for r in jr['open'] if r.get('triage') == 'open'], key=lambda r: (pri.get(r['priority'], 3), r['workstream'] or 'zz'))
    ov['open_counts'] = dict(collections.Counter(r.get('triage') for r in jr['open']))
    ov['promises'] = [{k: r.get(k) for k in ('id', 'statement', 'who', 'to_whom', 'due', 'as_of', 'why', 'note')} for r in jr['promise'] if r.get('triage') == 'owed']
    ov['promise_counts'] = dict(collections.Counter(r.get('triage') for r in jr['promise']))
    ov['goals'] = [{k: r.get(k) for k in ('id', 'statement', 'subject', 'goal_state', 'measure', 'review_at')} for r in jr['goal']]
    ov['exam'] = ((_j(os.path.join(HUB, 'state', 'kb_exam.json'), {}) or {}).get('history') or [])[-3:]
    return ov


def activity(q=None):
    hours = min(int((q or {}).get('hours') or 72), 24 * 14)
    since = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - hours * 3600))
    tasks = _q("SELECT id, rid, aid, title, kind, lane, origin, state, started, ended FROM tasks WHERE state IN ('done','failed','stopped') "
               "AND COALESCE(ended, created) >= ? ORDER BY COALESCE(ended, created) DESC LIMIT 200", (since,))
    for t in tasks:
        t['summary'] = ''
        if t.get('rid') and t.get('aid'):
            w = _j(os.path.join(HUB, 'work', f"{t['rid']}.json"), {}) or {}
            e = (w.get('items') or w).get(t['aid']) if isinstance(w, dict) else None
            if isinstance(e, dict):
                t['summary'] = str(e.get('summary') or '')[:300]
                t['needs_you'] = bool(e.get('needs_you'))
    acts = _q("SELECT id, at, type, tier, origin, state, why, ended FROM actions WHERE at >= ? ORDER BY at DESC LIMIT 200", (since,))
    running = _q("SELECT id, rid, aid, title, kind, lane, origin, state, started FROM tasks WHERE state IN ('running','queued','paused','held') ORDER BY created")
    return {'since': since, 'tasks': tasks, 'actions': acts, 'running': running,
            'counts': {'done': sum(t['state'] == 'done' for t in tasks), 'failed': sum(t['state'] != 'done' for t in tasks), 'actions': len(acts)}}


def research(q=None):
    rows = _q("SELECT f.*, i.state AS trial_state, i.steps_done, i.budget, i.question AS trial_question, "
              "(SELECT state FROM investigations WHERE id = f.followup) AS followup_state, "
              "(SELECT steps_done FROM investigations WHERE id = f.followup) AS followup_steps FROM research_findings f "
              "LEFT JOIN investigations i ON i.id = f.investigation WHERE f.state != 'not' "
              "ORDER BY CASE f.state WHEN 'tried' THEN 0 WHEN 'trying' THEN 1 WHEN 'worth' THEN 1 ELSE 2 END, COALESCE(f.tried_at, f.created) DESC LIMIT 60")
    for r in rows:
        r['folder'] = os.path.join(HUB, 'work', 'tasks', r['investigation']) if r.get('investigation') else None
        r['steps'] = []
        if r['folder']:                                  # 13 Nov: what the trial has found so far, step by step (its own plan.json)
            try:
                p = json.load(open(os.path.join(r['folder'], 'plan.json')))
                r['steps'] = [{'do': str(s.get('do') or '')[:220], 'found': str(s.get('found') or '')[:700], 'passes': bool(s.get('passes'))}
                              for s in p.get('steps') or [] if isinstance(s, dict)]
            except (OSError, ValueError):
                pass
    # 13 Nov (The owner: "show the verdict there also"): the overall verdict across the trials, written by a review of all of them
    # (state/research_verdict.json): a headline, what is adopted and where, and one decision per finding (adopt / no / lead / open).
    verdict = None
    try:
        verdict = json.load(open(os.path.join(HUB, 'state', 'research_verdict.json')))
        dec = verdict.get('decisions') or {}
        for r in rows:
            if r['id'] in dec:
                r['decision'] = dec[r['id']]
    except (OSError, ValueError):
        pass
    # 13 Nov (The owner: "some are showing verdict pending"): the review is written at one moment; a trial that finishes after it
    # kept "verdict pending" or no decision. A finding whose own trial has a verdict now shows that verdict until the
    # review is rewritten.
    for r in rows:
        d = r.get('decision') or {}
        if r.get('state') == 'tried' and (not d or d.get('call') == 'open'):
            adopt = str(r.get('adopt') or '')
            call = 'no' if adopt.lower().startswith('not worth') else 'lead'
            note = (("Don't adopt: " if call == 'no' else 'From the trial: ') + str(r.get('plain') or adopt)).strip()
            r['decision'] = {'call': call, 'note': note[:600], 'from': 'trial verdict'}
    return {'findings': rows, 'counts': dict(collections.Counter(r['state'] for r in rows)), 'verdict': verdict}
