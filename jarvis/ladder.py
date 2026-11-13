"""The autonomy ladder, as a view. Advisory only: nothing here can send, write or approve anything, and no level changes
here. It answers one question with numbers: how far is each kind of outward action from being trusted to run without the
owner's press?

Levels (one per action class):
  L0 off · L1 Jarvis drafts, the owner sends or does it · L2 Jarvis prepares, they approve with one tap · L3 Jarvis
  announces, then acts after a short window unless they stop it · L4 acts, listed in the daily ledger with an undo · L5
  acts, they audit.
Every outward class starts at L1 or L2: every send, comment, write and merge waits for the owner's press (the page key).
Moving a class up is THEIR switch, never above the ceilings below; this file only shows the evidence.

Evidence comes from jarvis.db `shadow` (tools/shadow_score.py): for each answered item, whether they sent Jarvis's draft as
written, edited it, or wrote their own. A class is ELIGIBLE for L3 when its last 20 scored cases in a row were sent as
written. One edit resets the run.

Who is who: state/ladder_roles.json (made empty on first run, the owner's to fill in the app): leads, customers, team.
A person not listed is treated as unknown, which is the strictest class.

    python -m jarvis.ladder            print the ladder and write state/ladder.json
    python -m jarvis.ladder --json
"""
from jarvis import paths
import datetime as dt, json, os, re, sqlite3, sys

HUB = str(paths.home())
DB = os.path.join(HUB, 'state', 'jarvis.db')
ROLES = os.path.join(HUB, 'state', 'ladder_roles.json')
OUT = os.path.join(HUB, 'state', 'ladder.json')
NEED = 20                                                  # cases in a row, sent as written, before a class is eligible for L3

# The ceilings are careful on purpose: a reply to people the owner works with may go as far as L3 (hold, then send) once
# earned and switched on by them; anything to a customer or to someone unknown never passes L2 (their tap); production is
# never touched.
# id, what it is, level at the start, the ceiling (the highest level it may ever be switched to), how it is undone, the limit in words
CLASSES = [
    ('slack_lead', 'Reply to a lead (the people you report to)', 'L1', 'L3', 'held 10 minutes before it goes; after that, a correction message', 'ceiling L3: hold, then send'),
    ('slack_team', 'Reply to a teammate', 'L1', 'L3', 'held 10 minutes before it goes; after that, a correction message', 'ceiling L3: hold, then send'),
    ('slack_channel', 'Reply in a team channel', 'L1', 'L3', 'held 10 minutes before it goes; after that, a correction message', 'ceiling L3: hold, then send'),
    ('slack_customer', 'Message or mail to a customer', 'L1', 'L2', 'none once sent', 'never above L2: always your tap'),
    ('slack_unknown', 'Reply to someone not in your roles list', 'L1', 'L2', 'held before it goes', 'never above L2 until the person is listed'),
    ('jira_comment', 'Ticket comment', 'L1', 'L3', 'edit or delete the comment', 'ceiling L3; only on your own ticket projects'),
    ('jira_ticket', 'Ticket creation and transitions', 'L2', 'L3', 'reverse the transition; close the ticket', 'ceiling L3'),
    ('confluence', 'Document page (draft, then publish)', 'L1', 'L2', 'restore the previous version', 'publishing stays your tap'),
    ('pull_request', 'Pull request opened with the gate verdict', 'L1', 'L3', 'close the pull request', 'merging stays your press'),
    ('dev_write', 'Write to a development database (snapshot first)', 'L2', 'L3', 'restore the snapshot', 'ceiling L3'),
    ('uat_write', 'Write to a test or staging database', 'L2', 'L2', 'restore the snapshot', 'never above L2: your press'),
    ('merge', 'Merge into a shared branch', 'L2', 'L2', 'revert commit', 'never above L2: your press'),
    ('production', 'Anything in production', 'L0', 'L0', '-', 'never'),
]
CEILING = {c[0]: c[3] for c in CLASSES}


def roles():
    try:
        return json.load(open(ROLES))
    except (OSError, ValueError):
        r = {'_note': "Who is who for the autonomy ladder: full names as they appear in your messages. Someone in none of the "
                      "lists is unknown, the strictest class.",
             'lead': [], 'customer': [], 'team': []}
        os.makedirs(os.path.dirname(ROLES), exist_ok=True)
        json.dump(r, open(ROLES, 'w'), indent=1)
        return r


def class_of(shadow_cls, R):
    """The action class of a shadow row's cls ('slack:Priya Shah', 'jira:ABC-12', 'slack:#channel thread')."""
    ch, _, who = str(shadow_cls or '').partition(':')
    who = who.strip()
    if ch == 'jira':
        return 'jira_comment'
    if ch in ('email', 'teams'):
        return 'slack_customer' if who in R.get('customer', []) else ('slack_lead' if who in R.get('lead', []) else 'slack_unknown' if who not in R.get('team', []) else 'slack_team')
    if who.startswith('#'):
        return 'slack_channel'
    for role in ('customer', 'lead', 'team'):
        if who in R.get(role, []):
            return 'slack_' + role
    return 'slack_unknown'


def gate_evidence():
    """{class: (replies the gate judged, of which only the level stood in the way)} from policy_log (jarvis/spine/policy.py
    would()): how often a drafted reply passed every check except "your press is needed"."""
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=10)
    out = {}
    try:
        rows = con.execute("SELECT cls, failed FROM policy_log p WHERE mode='would' AND at = (SELECT MAX(at) FROM policy_log q "
                           "WHERE q.rid=p.rid AND q.aid=p.aid AND q.mode='would')").fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    for cls, failed in rows:
        f = set(x for x in (failed or '').split(',') if x) - {'level', 'quiet'}     # the hour it was judged is not the reply's fault
        n, okc = out.get(cls, (0, 0))
        out[cls] = (n + 1, okc + (not f))
    return out


def ladder():
    R = roles()
    gate = gate_evidence()
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=10)
    try:
        rows = con.execute("SELECT cls, at, outcome, agree FROM shadow WHERE agree IS NOT NULL ORDER BY at DESC").fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    ev = {}
    for cls, at, outcome, agree in rows:                   # newest first
        e = ev.setdefault(class_of(cls, R), {'n': 0, 'as_written': 0, 'edited': 0, 'own': 0, 'run': 0, 'run_open': True, 'last': at[:10]})
        e['n'] += 1; e[outcome] = e.get(outcome, 0) + 1
        if e['run_open'] and outcome == 'as_written':
            e['run'] += 1
        else:
            e['run_open'] = False
    out = []
    for cid, label, level, ceiling, undo, limit in CLASSES:
        e = ev.get(cid) or {'n': 0, 'as_written': 0, 'edited': 0, 'own': 0, 'run': 0, 'last': ''}
        if e['n']:
            evidence = f"{e['n']} scored: {e['as_written']} sent as written, {e['edited']} edited, {e['own']} replaced; current run {e['run']} of {NEED}"
        else:
            evidence = 'no scored case yet'
        if cid in gate:
            evidence += f"; the gate would pass {gate[cid][1]} of {gate[cid][0]} drafted replies"
        out.append({'cls': cid, 'label': label, 'level': level, 'ceiling': ceiling, 'undo': undo, 'limit': limit, 'n': e['n'], 'as_written': e['as_written'],
                    'run': e['run'], 'need': NEED, 'eligible': e['run'] >= NEED and ceiling > level, 'evidence': evidence, 'last': e.get('last', '')})
    d = {'at': dt.datetime.now().isoformat(timespec='seconds'), 'classes': out,
         'note': 'Advisory: no level changes here and nothing acts on it. Levels move by your switch, after your decisions.'}
    json.dump(d, open(OUT + '.tmp', 'w'), indent=1); os.replace(OUT + '.tmp', OUT)
    return d


if __name__ == '__main__':
    d = ladder()
    if '--json' in sys.argv:
        print(json.dumps(d, indent=1))
    else:
        print(f"Autonomy ladder, {d['at']} (advisory; nothing acts on it)")
        for c in d['classes']:
            print(f" {c['level']}  {c['label'][:46]:46s} {c['evidence'][:74]:74s} {'ELIGIBLE for L3' if c['eligible'] else ''} {('· ' + c['limit']) if c['limit'] else ''}")
