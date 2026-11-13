"""Demo mode: a made-up person's Jarvis, to look around in before connecting anything.

Everything here is invented (a person called Sam Lee who runs an order desk, three colleagues, one ticket project). It
uses no account and no usage: nothing is fetched, nothing is sent, no model is called. The demo lives in its own data
home (inside the real one, in `demo/`), so it never mixes with the owner's own data.

    python3 -m jarvis.demo [--home DIR]     build it and say how to open it
"""
import datetime as dt
import json
import os
import sys

from . import config, paths

OWNER = 'Sam Lee'


def _w(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, indent=1, ensure_ascii=False)


def build(home=None):
    """Write the demo data home and return its path. Building again starts it afresh (only the files named here)."""
    home = str(home or paths.demo_home())
    now = dt.datetime.now().astimezone().replace(microsecond=0)
    day, iso = now.strftime('%Y-%m-%d'), now.isoformat()
    ago = lambda **kw: (now - dt.timedelta(**kw))
    when = lambda t: t.strftime('%-d %b %H:%M') if os.name != 'nt' else t.strftime('%d %b %H:%M')
    rid = ago(hours=2).strftime('%Y-%m-%d-%H%M') + '-all'
    old, os.environ['JARVIS_HOME'] = os.environ.get('JARVIS_HOME'), home
    try:
        paths.ensure()
        for key, value in (('owner.name', OWNER), ('work.about', 'runs the order desk'), ('work.projects', ['Order desk']),
                           ('tracker.own_projects', ['ABC']), ('knowledge.ticket_keys', ['ABC']),
                           ('knowledge.entities', ['order = \\bSO-\\d{6}\\b'])):
            config.set(key, value)
        kb = str(paths.kb())
    finally:
        if old is None:
            os.environ.pop('JARVIS_HOME', None)
        else:
            os.environ['JARVIS_HOME'] = old
    H = lambda *p: os.path.join(home, *p)

    # ---- the inbox: one round-up, three things waiting, one answer, one thing to know
    _w(H('roundups', rid + '.json'), {
        'id': rid, 'title': 'Round-up', 'kind': 'mixed', 'period': now.strftime('%-d %b %Y') if os.name != 'nt' else day, 'by': 'demo',
        'source': 'Made-up messages for the demo. Nothing was read, posted or sent.', 'created': day,
        'window_start': ago(hours=26).isoformat(), 'window_end': ago(hours=2).isoformat(),
        'sources_checked': {'slack': 'demo', 'email': 'demo', 'jira': 'demo'},
        'actions': [
            {'id': 'a1', 'src': 'slack', 'who': 'Priya Nair', 'where': '#orders', 'when': when(ago(hours=5)), 'title': 'March export',
             'points': [{'text': 'How many March orders went out late, and is that better than February?'},
                        {'text': 'Can you share the list of the late ones?'}]},
            {'id': 'a2', 'src': 'email', 'who': 'Marco Diaz', 'where': 'Mail', 'when': when(ago(hours=9)), 'title': 'Price list',
             'email_to': 'marco@example.com', 'subject': 'Price list for the spring restock',
             'points': [{'text': 'Should the spring price list use the old rounding or the new one?'}]},
            {'id': 'a3', 'src': 'jira', 'who': 'Lena Fischer', 'where': 'ABC-12', 'when': when(ago(hours=20)), 'title': 'Weekly check',
             'points': [{'text': 'The weekly check skipped two regions last Friday. Can you find out why?'}]}],
        'answers': [{'src': 'slack', 'title': 'Depot hours', 'who': 'Omar Haddad', 'where': '#orders', 'when': when(ago(hours=7)),
                     'points': ['The north depot opens at 08:00 from next week.']}],
        'fyi': [{'src': 'jira', 'text': 'ABC-9 (the regional split) was moved to Done by Lena Fischer.'}]})
    _w(H('roundups', 'state.json'), {})
    _w(H('roundup_last.json'), {'checked_at': ago(hours=2).isoformat(), 'file': rid, 'counts': {'actions': 3, 'answers': 1, 'fyi': 1}, 'skipped': {}})
    out = H('work', 'outputs', rid + '-a1')
    _w(os.path.join(out, 'late_orders.csv'), 'month,orders,late\nFebruary,151,19\nMarch,164,12\n')
    _w(os.path.join(out, 'RESULT.md'), '# Late orders, March\n\n12 of 164 March orders went out late (7.3%); February had 19 of 151 (12.6%).\n')
    _w(H('work', rid + '.json'), {
        '_run': {'at': ago(hours=1).isoformat(), 'drafted': 1, 'left': 0, 'note': 'demo'},
        'a1': {'status': 'drafted', 'kind': 'question', 'updated': ago(hours=1).isoformat(),
               'summary': 'Twelve of 164 March orders went out late, down from 19 of 151 in February.',
               'numbers': [{'what': 'Late orders, March', 'now': '12 of 164', 'was': '19 of 151 in February'}],
               'points': [{'key': 'a1.0', 'state': 'drafted', 'note': '12 of 164 late in March; 19 of 151 in February.', 'confidence': 'high',
                           'answer': 'March: 12 of 164 late. February: 19 of 151.', 'sources': [{'type': 'data', 'ref': 'late_orders.csv', 'note': 'counted from the export'}]},
                          {'key': 'a1.1', 'state': 'drafted', 'note': 'The list is in the reply.', 'confidence': 'high',
                           'sources': [{'type': 'data', 'ref': 'late_orders.csv', 'note': ''}]}],
               'needs_you': [],
               'reply': {'channel': 'slack', 'to': 'Priya Nair', 'where': '#orders', 'channel_id': 'C0DEMO0001',
                         'text': 'hey Priya, 12 of the 164 March orders went out late, down from 19 of 151 in February. Breakdown below.',
                         'attachments': [{'title': 'Late orders, March', 'path': os.path.join(out, 'RESULT.md'), 'inline': True}]},
               'done_work': [{'title': 'Late orders, March', 'path': os.path.join(out, 'RESULT.md'), 'summary': 'The count by month'}],
               'context': {'at': ago(hours=1).isoformat(), 'file': '', 'checked': [
                   {'where': 'data', 'what': 'the March export', 'found': 1, 'used': 'the count of late orders'},
                   {'where': 'slack', 'what': 'the #orders thread', 'found': 1, 'used': 'what Priya asked'}]}},
        'a2': {'status': 'needs_you', 'kind': 'question', 'updated': ago(hours=1).isoformat(),
               'summary': 'The price list needs your choice of rounding before it can go to Marco.',
               'points': [{'key': 'a2.0', 'state': 'decide', 'note': 'Old rounding keeps last year\'s prices comparable; new rounding matches the invoices.'}],
               'needs_you': ['Choose the rounding for the spring price list.'],
               'asks': [{'id': 'rounding', 'q': 'Which rounding should the spring price list use?',
                         'detail': 'The old rounding keeps prices comparable with last year. The new one matches what the invoices show.',
                         'options': [{'id': 'new', 'label': 'The new rounding', 'plan': 'Matches the invoices', 'recommended': True},
                                     {'id': 'old', 'label': 'The old rounding', 'plan': 'Comparable with last year'}]}],
               'reply': {'channel': 'email', 'to': 'Marco Diaz', 'email_to': 'marco@example.com', 'subject': 'Re: Price list for the spring restock', 'text': ''}},
        'a3': {'status': 'done', 'kind': 'task', 'updated': ago(hours=3).isoformat(), 'jira_key': 'ABC-12',
               'summary': 'The weekly check skipped two regions because their files arrived after it ran.',
               'points': [{'key': 'a3.0', 'state': 'done', 'note': 'Two files arrived 40 minutes late; the check now waits for all of them.', 'confidence': 'high'}],
               'needs_you': [], 'reply': {'channel': 'jira', 'to': 'Lena Fischer', 'jira_key': 'ABC-12',
                                          'text': 'The two regions were skipped because their files arrived after the check ran. It now waits until every file is there.'}}})
    _w(H('approvals.json'), {'items': [
        {'id': f'ap-{rid}-a3-1', 'kind': 'merge', 'title': 'Merge the fix that makes the weekly check wait for every file',
         'why': 'Lena asked why two regions were skipped (ABC-12).', 'plan': '1 file, 14 lines added, 3 removed. Tests: 12 passed. Rollback: revert the merge commit.',
         'payload': {'repo': 'order-desk', 'branch': 'sl/ABC-12-wait-for-files', 'base': 'main', 'commit': '0000000'},
         'related': {'rid': rid, 'aid': 'a3', 'point': 'a3.0'}, 'status': 'pending', 'created': ago(hours=3).isoformat(), 'created_by': 'demo'}]})
    _w(H('tickets', 'proposals.json'), {'items': [
        {'id': f't-{rid}-a1.1', 'status': 'draft', 'summary': 'Send the list of late orders with every monthly export', 'type': 'Task', 'parent': '', 'link': '',
         'description': 'Add the list of late orders to the monthly export, so nobody has to ask for it.\n\nRaised by Priya Nair in #orders.',
         'source': {'rid': rid, 'key': 'a1.1', 'src': 'slack', 'who': 'Priya Nair', 'where': '#orders', 'when': when(ago(hours=5)), 'text': 'Can you share the list of the late ones?'},
         'key': None, 'created_at': None, 'reuse_key': None, 'reused': False, 'refined': True, 'claude_note': '', 'updated': iso}]})

    # ---- a small knowledge base: one workstream, the notes about the whole of the work, a few facts
    note = ('---\nworkstream: exports\nupdated: ' + day + '\n---\n# Exports\n\n## In one paragraph\nThe monthly export shows how many orders went out on time. '
            'March had 12 late orders of 164.\n\n## Current position\n- March: 12 of 164 orders late (7.3%) [[demo-march]]\n- February: 19 of 151 orders late (12.6%) [[demo-february]]\n\n'
            '## Decisions\n- The weekly check waits for every regional file before it runs (ABC-12) [[demo-march]]\n\n## Open questions\n- Why do two regions send their files late?\n')
    _w(os.path.join(kb, 'topics', 'exports.md'), note)
    _w(os.path.join(kb, 'topic_map.json'), [{'slug': 'exports', 'title': 'Exports', 'scope': 'The monthly export and what it shows.', 'sources': ['S:demo-march', 'S:demo-february']}])
    for name in ('Overview', 'Timeline', 'Decisions', 'People', 'Data sources', 'Glossary', 'Open questions', 'Deliverables'):
        _w(os.path.join(kb, name + '.md'), f'# {name}\n\nMade-up for the demo: the order desk sends a monthly export and checks it weekly.\n')
    for sid, month, late, total in (('demo-march', 'March', 12, 164), ('demo-february', 'February', 19, 151)):
        _w(H('brain', 'sessions', sid + '.md'), f'# {month} export check\n\n*{day} · chat · topics: exports*\n\n**Asked:** how many {month} orders went out late.\n\n'
                                                f'## Results\n- {late} of {total} {month} orders went out late\n')
    recs = [('F-demo0001', 'fact', 'late orders, March', '12 of 164 March orders went out late (7.3%).', 'current'),
            ('F-demo0002', 'fact', 'late orders, February', '19 of 151 February orders went out late (12.6%).', 'current'),
            ('D-demo0003', 'decision', 'weekly check', 'The weekly check waits for every regional file before it runs (ABC-12).', 'current'),
            ('F-demo0004', 'open', 'late files', 'Why do two regions send their files late?', 'current')]
    _w(os.path.join(kb, 'ledger', 'records.jsonl'), ''.join(json.dumps(
        {'id': i, 'kind': k, 'subject': s, 'statement': st, 'status': status, 'workstream': 'exports', 'note': 'exports', 'section': 'Current position',
         'quote': st, 'sources': ['demo-march'], 'entities': ['ticket:ABC-12'] if 'ABC-12' in st else [], 'data_cutoff': None, 'as_of': day,
         'superseded_by': [], 'contradicts': [], 'final': True, 'who': None, 'why': None, 'metric': None, 'value': None, 'unit': None, 'scope': None,
         'checked': None, 'line_date': None}) + '\n' for i, k, s, st, status in recs))
    _w(H('DEMO.md'), '# This is the demo\n\nEverything in this folder is made up. Nothing here was read from, or sent to, anyone.\n')
    return home


if __name__ == '__main__':
    a = sys.argv[1:]
    where = build(a[a.index('--home') + 1] if '--home' in a else None)
    print(f'The demo is in {where}\nOpen it with:\n  JARVIS_HOME="{where}" JARVIS_NO_BACKGROUND=1 "{paths.python()}" -m jarvis.server --port 8766')
