"""Initiatives: work Jarvis starts by itself (HARNESS.md §14.5, Phase 2, 5 Oct 2026). Plain code decides; a worker does it.

When a need's recognizer finds data that could answer it, a background worker takes it on and puts the answer in their To do
(it stays until they say "Got it"). Autonomy by who raised the need (Phase 4):
  A3  their own needs (owner_said / owner_ack): carried out fully, internally: the analysis, a branch, a drafted reply.
  A2  the rest (task_limitation): a read-only check, computed if cheap.
Either way nothing is sent, posted, merged or written to a database; those stay behind their press, enforced in code.
Admission (code):
  - the need may act: origin owner_said / owner_ack, not tainted, kind data_need or answer_need, still open;
    an event_need acts only when its candidate CHANGED after the need was noted (a new range, a new table), never because
    the table merely exists
  - not already tried for this need in the last 14 days
  - at most MAX_PER_DAY started per day (overnight on AC power: MAX_NIGHT more), one at a time (the background lane)
Order: origin (their words first), then data before answers, then the newest need.
"""
from jarvis import paths
import re, datetime as dt, json, os, subprocess, uuid
from jarvis.spine import dispatcher, ledger

HUB = str(paths.home())
MAX_PER_DAY, MAX_NIGHT = 3, 3
ORIGIN_RANK = {'owner_said': 1, 'owner_ack': 2, 'task_limitation': 3}
ACT_KINDS = ('data_need', 'answer_need')


def on_ac():
    """Is the computer on mains power? Jarvis's own checks wait while it runs on battery. A computer that cannot say
    (a desktop, a server, Windows) counts as on mains power."""
    import glob, sys
    try:
        if sys.platform == 'darwin':
            return 'AC Power' in subprocess.run(['pmset', '-g', 'batt'], capture_output=True, text=True, timeout=3).stdout
        supplies = glob.glob('/sys/class/power_supply/*/online')
        if supplies:
            return any(open(f).read().strip() == '1' for f in supplies)
        return True
    except Exception:
        return False


def _started_today():
    day = dt.date.today().isoformat()
    return ledger.q("SELECT count(*) n FROM initiatives WHERE state IN ('admitted','running','done','failed') AND substr(created,1,10)=?", (day,))[0]['n']


def proposals():
    """Needs that may act now, with their strong candidates, best first."""
    out = []
    recent = {r['need_id'] for r in ledger.q("SELECT need_id FROM initiatives WHERE created >= ?",
                                             ((dt.datetime.now().astimezone() - dt.timedelta(days=14)).isoformat(),))}
    for n in ledger.q("SELECT * FROM needs WHERE state IN ('open','partial') AND dismissed_at IS NULL AND tainted=0 AND coalesce(scope, 'work') = 'work'"):
        if n['origin'] not in ORIGIN_RANK or n['id'] in recent:
            continue
        try:
            tried = set((json.loads(n['note']) if (n['note'] or '').startswith('{') else {}).get('tried', []))
        except ValueError:
            tried = set()
        cands = [c for c in json.loads(n.get('candidates') or '[]') if c.get('strength') == 'strong' and c['source'] not in tried]
        if not cands:
            continue
        if n['kind'] == 'event_need':
            ids = [c['source'] for c in cands]
            changed = ledger.q("SELECT source_id, kind FROM catalog_events WHERE at > ? AND kind IN ('new_range','new_source','schema_change','content_read') "
                               "AND source_id IN (%s)" % ','.join('?' * len(ids)), (n['created'], *ids))
            if not changed:
                continue
            cands = [c for c in cands if c['source'] in {x['source_id'] for x in changed}]
        elif n['kind'] not in ACT_KINDS:
            continue
        else:
            # data already pulled locally was what the need was found with: only data never examined (bucket, databases) or
            # data that changed after the need was noted can answer it
            fresh = []
            for c in cands:
                s = ledger.q('SELECT local FROM sources WHERE id=?', (c['source'],))
                changed = ledger.q('SELECT 1 FROM catalog_events WHERE source_id=? AND at > ? LIMIT 1', (c['source'], n['created']))
                if (s and not s[0]['local']) or changed:
                    fresh.append(c)
            cands = fresh
            if not cands:
                continue
        lf = {t.lower() for t in ((json.loads(n['recognizer'] or '{}').get('look_for') or {}).get('tables') or [])}
        exact = sum(1 for c in cands if c['source'].split(':')[-1].split('.')[-1].lower() in lf)
        out.append((ORIGIN_RANK[n['origin']], -exact, 0 if n['kind'] == 'data_need' else 1, n, cands))
    out.sort(key=lambda x: (x[0], x[1], x[2]))
    return [(n, c) for _, _, _, n, c in out]


def headline(t, n=80):
    """One plain line: no (asides), first letter capitalised, cut at a word boundary."""
    t = re.sub(r'\s*\([^)]*\)', '', str(t or '')).strip().rstrip('.')
    t = t[:1].upper() + t[1:]
    return t if len(t) <= n else t[:n].rsplit(' ', 1)[0].rstrip(',;:') + '…'


def _set_noticed(rid, aid, **kv):
    """Merge display fields into a noticed item's action (temp file + rename)."""
    p = os.path.join(HUB, 'roundups', rid + '.json')
    try:
        d = json.load(open(p))
        for a in d.get('actions', []):
            if a.get('id') == aid:
                a.setdefault('noticed', {}).update(kv)
        json.dump(d, open(p + '.tmp', 'w'), indent=1)
        os.replace(p + '.tmp', p)
    except (OSError, ValueError):
        pass


def noticed_fields(n, cands):
    """What the page shows for a noticed item: short labelled fields, never the worker brief."""
    srcs = []
    for c in cands[:5]:
        s = ledger.q('SELECT * FROM sources WHERE id=?', (c['source'],))
        if s:
            s = s[0]
            kind = {'local': 'a file in your folders', 'doc': 'a document', 'branch': 'a branch'}.get(s['system'], str(s['system']).replace('db_', '') + ' source')
            srcs.append(f"{s['name']} ({kind})")
    ref = str(n.get('source_ref') or '')
    origin = 'your notes' if ref.startswith('memory:') else 'a past Claude session' if ref.startswith('cc:') else 'a report' if ref.startswith('report:') else 'earlier work'
    return {'need': n['statement'], 'target': n.get('target') or '', 'assumed': n.get('assumed') or '', 'why': n.get('why') or '',
            'checked': srcs, 'origin': origin}


A3_ORIGINS = ('owner_said', 'owner_ack')       # Phase 4: needs he raised or agreed to are carried out fully (internal work only)


LEVELS = ('A1', 'A2', 'A3')
PROMOTE_AFTER = 3          # three useful results in a row that he acknowledged (ticked) raise a class one level
LEVELS_TABLE = """CREATE TABLE IF NOT EXISTS autonomy_levels(cls TEXT PRIMARY KEY, level TEXT, streak INTEGER, misses INTEGER,
  updated TEXT, why TEXT)"""


def _cls(n):
    return f"need_probe:{n.get('origin') or 'unknown'}"


def autonomy(n):
    """A3 (execute fully, up to drafts and branches, then tell) for their own needs; A2 (prepare, then tell) for the rest, unless
    the class has earned a different level from outcomes (Phase 6, HARNESS §14.5: promotions and demotions). Tainted needs
    (outside content) never act above A1, and never rise."""
    if n.get('tainted'):
        return 'A1'
    base = 'A3' if n.get('origin') in A3_ORIGINS else 'A2'
    ledger.x(LEVELS_TABLE)
    row = ledger.q('SELECT level FROM autonomy_levels WHERE cls=?', (_cls(n),))
    return row[0]['level'] if row else base


def outcome(n, useful, acknowledged, stop=False):
    """One finished check of a need class: useful (verdict yes or partly) and acknowledged (they ticked it done) build a streak;
    PROMOTE_AFTER in a row raise the class a level (never above A3, never for outside content). Not useful twice in a row, or
    a need they dismissed, lowers it a level; "stop doing this" (stop=True) drops it to A1 at once."""
    ledger.x(LEVELS_TABLE)
    if n.get('tainted'):
        return 'A1'
    cls, cur = _cls(n), autonomy(n)
    row = ledger.q('SELECT * FROM autonomy_levels WHERE cls=?', (cls,))
    streak, misses = (row[0]['streak'], row[0]['misses']) if row else (0, 0)
    level, why = cur, ''
    if stop:
        level, streak, misses, why = 'A1', 0, 0, 'they said stop doing this'
    elif useful and acknowledged:
        streak, misses = streak + 1, 0
        if streak >= PROMOTE_AFTER and cur != 'A3':
            level, streak, why = LEVELS[LEVELS.index(cur) + 1], 0, f'{PROMOTE_AFTER} useful results in a row they acknowledged'
    elif useful:
        pass                                     # useful but he never ticked it: neutral (no promotion, no demotion)
    else:
        streak, misses = 0, misses + 1
        if misses >= 2 and cur != 'A1':
            level, misses, why = LEVELS[LEVELS.index(cur) - 1], 0, 'two results in a row that were not useful'
    ledger.x('INSERT OR REPLACE INTO autonomy_levels(cls, level, streak, misses, updated, why) VALUES(?,?,?,?,?,?)',
             (cls, level, streak, misses, ledger.now(), why or (row[0]['why'] if row else '')))
    if level != cur:
        ledger.event('autonomy_' + ('promoted' if LEVELS.index(level) > LEVELS.index(cur) else 'demoted'), cls, 'initiative',
                     {'from': cur, 'to': level, 'why': why})
    return level


def _brief(n, cands):
    srcs = []
    for c in cands[:5]:
        s = ledger.q('SELECT * FROM sources WHERE id=?', (c['source'],))
        if s:
            s = s[0]
            where = {'local': f"file {s['path']}" + (f" ({s['rows']} rows)" if s.get('rows') else ''), 'doc': f"document at {s['path']}",
                     'branch': f"branch {s['path'] or s['name']}"}.get(
                s['system'], f"{str(s['system']).replace('db_', '')} source {s['name']}" + (f" (~{s['rows']} rows, read-only)" if s.get('rows') else ''))
            srcs.append(f"- {s['name']}: {where}")
    return (f"Jarvis noticed this by itself (a standing need from {n['source_ref']}; nobody asked today).\n"
            f"NEED: {n['statement']}\nTARGET (what the answer must be): {n['target'] or n['statement']}\n"
            f"WHY: {n['why']}\nASSUMED MEANWHILE: {n['assumed'] or 'not stated'}\n"
            f"CANDIDATE DATA (it may or may not be able to answer):\n" + '\n'.join(srcs) + "\n\n"
            + (_A3_STEPS if autonomy(n) == 'A3' else _A2_STEPS))


_A2_STEPS = ("Do a READ-ONLY check and prepare the answer: (1) look at or sample the candidate data (copy remote files "
            "into this item's outputs folder; databases read-only; never write to any database, bucket or shared file); count the rows "
            "with a blank key, and make sure the data covers the period the analysis needs; (2) decide whether it can compute the TARGET: yes / partly (say "
            "exactly what is still missing) / no (say why); (3) if yes and it takes minutes, compute it and compare with what is assumed "
            "now; (4) write the result plainly for the owner with the numbers and where each came from. Do not change shipped files, "
            "notebooks, memory notes or anything shared. Nothing is sent to anyone. (5) Start the work entry's summary with exactly one of "
            "'VERDICT: yes', 'VERDICT: partly' or 'VERDICT: no', then the one-line reason.")
# A3: the owner raised or agreed to this need himself, so the work is carried out fully, internally. The hard limits
# are unchanged and enforced in code: no sends, no database or bucket writes, no merges, nothing shared without his press.
_A3_STEPS = ("The owner raised this need themselves (or agreed to it), so carry it out FULLY, not just a probe: (1) get the candidate "
            "data (copy remote files into this item's outputs folder; databases read-only; never write to any "
            "database, bucket or shared file); count the rows with a blank key, and make sure the data covers the period the analysis needs; (2) decide "
            "whether it can compute the TARGET: yes / partly / no; (3) if yes or partly, compute it properly even if it takes a while "
            "(long jobs under tools/run_job.py; notebooks and scenario runs in copies in your outputs folder), compare it with what is "
            "assumed now, and say what it changes; (4) when it means a code change, make it on a branch as the jarvis-inbox-work skill says (never "
            "merge); when it means telling someone, draft the reply in the work entry (never send); when it would change their brief, "
            "a deck or a shipped file, write the proposed change in your write-up instead (it is applied after review, with their OK); "
            "(5) write the result plainly for the owner with the numbers and where each came from. Never change memory notes. (6) Start "
            "the work entry's summary with exactly one of 'VERDICT: yes', 'VERDICT: partly' or 'VERDICT: no', then the one-line reason.")


def admit(dry=False):
    """Start what may start now. Returns the list of (need_id, title) started (or that would start, with dry=True)."""
    night = dt.datetime.now().hour < 7 or dt.datetime.now().hour >= 22
    cap = MAX_PER_DAY + (MAX_NIGHT if night and on_ac() else 0)
    left = cap - _started_today()
    started = []
    if left <= 0:
        return started
    if ledger.q("SELECT 1 FROM initiatives WHERE state IN ('admitted','running') LIMIT 1"):
        return started                                     # one at a time
    for n, cands in proposals()[:1]:
        title = headline(n['target'] or n['statement'])
        if dry:
            started.append((n['id'], title)); continue
        from jarvis import assistant                                       # new_item writes the To do entry the worker fills
        level = autonomy(n)
        rid, aid = assistant.new_item(_brief(n, cands), title, noticed={**noticed_fields(n, cands), 'autonomy': level})
        _mark_noticed(rid, aid)
        iid = 'ini-' + uuid.uuid4().hex[:8]
        t = dispatcher.submit('background', dispatcher.item_prompt(rid, aid), rid, aid, title=title, origin='initiative',
                              kind='initiative', initiative_id=iid)
        ledger.upsert('initiatives', {'id': iid, 'created': ledger.now(), 'class': 'need_probe', 'need_id': n['id'],
                                      'trigger': json.dumps([c['source'] for c in cands[:5]]), 'title': title,
                                      'plan': 'A3 full internal execution' if level == 'A3' else 'A2 read-only probe',
                                      'origin_rank': ORIGIN_RANK.get(n['origin'], 9), 'autonomy': level, 'state': 'admitted', 'task_id': t['id']}, ('id',))
        ledger.event('initiative_started', iid, 'initiative', {'need': n['id'], 'title': title, 'task': t['id'], 'rid': rid})
        started.append((n['id'], title))
    return started


def _mark_noticed(rid, aid):
    """The To do entry says it came from Jarvis noticing, not from their ask."""
    p = os.path.join(HUB, 'roundups', rid + '.json')
    try:
        d = json.load(open(p))
        for a in d.get('actions', []):
            if a.get('id') == aid:
                a['who'], a['where'], a['src'] = 'Jarvis (noticed)', 'Jarvis', 'noticed'
        json.dump(d, open(p + '.tmp', 'w'), indent=1); os.replace(p + '.tmp', p)
    except Exception:
        pass


def verdict(t):
    """yes | partly | no from the worker's work entry ('VERDICT: ...' first; else the summary's opening words)."""
    try:
        w = (json.load(open(os.path.join(HUB, 'work', t['rid'] + '.json'))) or {}).get(t['aid']) or {}
    except Exception:
        return 'partly'
    s = str(w.get('summary') or '') + ' ' + ' '.join(str(p.get('answer') or '') for p in w.get('points') or [] if isinstance(p, dict))
    import re
    m = re.search(r'VERDICT:\s*(yes|partly|no)\b', s, re.I)
    if m:
        return m.group(1).lower()
    head = s.strip().lower()[:80]
    if re.match(r'(answered|computed|yes\b|done\b)', head):
        return 'yes'
    if re.match(r'(no\b|cannot|can\'t|not answerable|not computable)', head):
        return 'no'
    return 'partly'


def reap():
    """Initiatives whose worker finished: done / failed from the task row."""
    for i in ledger.q("SELECT * FROM initiatives WHERE state IN ('admitted','running')"):
        t = dispatcher.task(i['task_id']) if i.get('task_id') else None
        if not t:
            continue
        st = {'running': 'running', 'done': 'done', 'failed': 'failed', 'stopped': 'failed'}.get(t['state'])
        if st and st != i['state']:
            ledger.x('UPDATE initiatives SET state=?, decided_at=? WHERE id=?', (st, ledger.now() if st in ('done', 'failed') else None, i['id']))
            if st == 'done' and i.get('need_id'):
                # the verdict decides: yes -> the need is answered; partly -> stays open as 'partial'; no -> stays open. Either
                # way the sources it checked are never proposed again for this need (note.tried).
                v = verdict(t)
                tried = json.loads(i.get('trigger') or '[]')
                n = ledger.q('SELECT note, state FROM needs WHERE id=?', (i['need_id'],))
                note = {}
                if n and (n[0]['note'] or '').startswith('{'):
                    try:
                        note = json.loads(n[0]['note'])
                    except ValueError:
                        note = {}
                note['tried'] = sorted(set(note.get('tried', [])) | set(tried))
                note['last_check'] = {'by': i['id'], 'item': f"{t['rid']}|{t['aid']}", 'verdict': v}
                new = {'yes': 'answered', 'partly': 'partial', 'no': 'open'}[v]
                ledger.x("UPDATE needs SET state=?, note=?, updated=? WHERE id=? AND state IN ('open','partial')",
                         (new, json.dumps(note), ledger.now(), i['need_id']))
                ledger.x('UPDATE initiatives SET result=? WHERE id=?', (v, i['id']))
                _set_noticed(t['rid'], t['aid'], verdict=v)
                ledger.event('need_checked', i['need_id'], 'initiative', {'verdict': v, 'item': f"{t['rid']}|{t['aid']}", 'title': i['title']})
                ledger.x('UPDATE initiatives SET outcome_pending=1 WHERE id=?', (i['id'],))


# ------------------------------------------------------------------ Phase 6: anomalies become noticed items (A2)
ANOMALY_KINDS = ('feed_stopped', 'quality_shift', 'volume_jump')


def anomalies():
    """One new anomaly (a feed that stopped, a jump in blank keys, a volume jump) -> one noticed item with an A2 check:
    is it real, since when, what it means for their work. Shares the initiative caps (one at a time,
    MAX_PER_DAY) and the governor's usual rules."""
    from jarvis.spine import governor
    if ledger.q("SELECT 1 FROM initiatives WHERE state IN ('admitted','running') LIMIT 1") or _started_today() >= MAX_PER_DAY:
        return []
    if not governor.admit({'lane': 'background', 'origin': 'initiative', 'kind': 'initiative', 'rid': '', 'aid': ''})[0]:
        return []
    done = {r['trigger'] for r in ledger.q("SELECT trigger FROM initiatives WHERE class='anomaly'")}
    for e in ledger.q("SELECT * FROM catalog_events WHERE kind IN (%s) ORDER BY id" % ','.join('?' * len(ANOMALY_KINDS)), ANOMALY_KINDS):
        key = f"event:{e['id']}"
        if key in done:
            continue
        d = json.loads(e['detail'] or '{}')
        name = d.get('name') or e['source_id']
        group = [e]
        if e['kind'] == 'feed_stopped':              # feeds that stopped on the same day are one outage: one item, not three
            for o in ledger.q("SELECT * FROM catalog_events WHERE kind='feed_stopped' AND id != ?", (e['id'],)):
                if f"event:{o['id']}" not in done and json.loads(o['detail'] or '{}').get('last') == d.get('last'):
                    group.append(o)
            if len(group) > 1:
                names = [json.loads(g['detail'] or '{}').get('name') or g['source_id'] for g in group]
                name = ', '.join(names)
        what = {'feed_stopped': (f"the {name} feeds have all delivered nothing since {d.get('last')} ({d.get('late_days')} days)" if len(group) > 1 else
                                 f"the {name} feed has delivered nothing since {d.get('last')} ({d.get('late_days')} days; it usually arrives every {d.get('usual_gap_days')} day(s))"),
                'quality_shift': f"{name}: the share of rows with a blank key moved from {d.get('was')} to {d.get('now')}",
                'volume_jump': f"{name}: rows went from {d.get('from')} to {d.get('to')}"}[e['kind']]
        title = headline(what)
        brief = (f"Jarvis noticed this by itself (nobody asked today): {what}. Source {e['source_id']}.\n"
                 + "Do a READ-ONLY check: (1) is it real (look at the source itself; nothing is written anywhere); "
                   "(2) since when, and is it one source or several together; (3) what it touches in their work (what reads it, "
                   "results already shared); (4) what they should do, if anything (who to tell, what to hold). Nothing is "
                   "sent to anyone. Start the work entry's summary with 'VERDICT: yes' (real and it matters), 'VERDICT: partly' or "
                   "'VERDICT: no' (a false alarm, say why), then the one-line reason.")
        from jarvis import assistant
        rid, aid = assistant.new_item(brief, title, noticed={'need': what, 'why': 'the data under their work changed', 'checked': [e['source_id']], 'origin': 'the data catalog'})
        _mark_noticed(rid, aid)
        iid = 'ini-' + uuid.uuid4().hex[:8]
        t = dispatcher.submit('background', dispatcher.item_prompt(rid, aid), rid, aid, title=title, origin='initiative', kind='initiative', initiative_id=iid)
        for g in group[1:]:                          # the grouped events are handled by this item too
            ledger.upsert('initiatives', {'id': 'ini-' + uuid.uuid4().hex[:8], 'created': ledger.now(), 'class': 'anomaly', 'need_id': None,
                                          'trigger': f"event:{g['id']}", 'title': title, 'plan': f'grouped into {iid}', 'origin_rank': 5,
                                          'autonomy': 'A2', 'state': 'grouped', 'task_id': None}, ('id',))
        ledger.upsert('initiatives', {'id': iid, 'created': ledger.now(), 'class': 'anomaly', 'need_id': None, 'trigger': key, 'title': title,
                                      'plan': 'A2 read-only check of an anomaly', 'origin_rank': 5, 'autonomy': 'A2', 'state': 'admitted',
                                      'task_id': t['id']}, ('id',))
        ledger.event('initiative_started', iid, 'initiative', {'anomaly': e['kind'], 'title': title, 'task': t['id'], 'rid': rid})
        return [(e['kind'], title)]
    return []


def settle_outcomes(days=7):
    """Finished need checks wait for their reaction: ticked done (acknowledged) or the need dismissed in Watching (not useful).
    After `days` with neither, a useful result counts as unacknowledged (no promotion, no demotion)."""
    st = json.load(open(os.path.join(HUB, 'roundups', 'state.json'))) if os.path.exists(os.path.join(HUB, 'roundups', 'state.json')) else {}
    out = []
    for i in ledger.q("SELECT i.*, t.rid, t.aid FROM initiatives i LEFT JOIN tasks t ON t.id = i.task_id WHERE i.outcome_pending=1 AND i.class='need_probe'"):
        n = ledger.q('SELECT * FROM needs WHERE id=?', (i['need_id'],))
        if not n:
            ledger.x('UPDATE initiatives SET outcome_pending=0 WHERE id=?', (i['id'],)); continue
        n = n[0]
        ticked = any(v for k, v in (st.get(i.get('rid') or '') or {}).items() if str(k).startswith(f"{i.get('aid')}."))
        dismissed = bool(n.get('dismissed_at'))
        old = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(i['created'])).days >= days
        if not (ticked or dismissed or old):
            continue
        useful = i.get('result') in ('yes', 'partly') and not dismissed
        lvl = outcome(n, useful, ticked)
        ledger.x('UPDATE initiatives SET outcome_pending=0 WHERE id=?', (i['id'],))
        out.append((i['id'], useful, ticked, lvl))
    return out
