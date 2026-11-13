#!/usr/bin/env python3
"""The same ask from different places, kept as ONE task (a request made in a meeting and again in a chat is one piece of
work; the app shows it once).

groups.json in the data home  {"groups": [{"id": "g-...", "title": "...", "lead": "<rid>|<point key>",
                                           "members": ["<rid>|<point key>", ...], "why": "...", "at": "...", "by": "..."}]}
A point key is "<aid>.<i>" (or "<aid>" for an item without points). The lead is where the work happens (the one already
being worked, else the one with the most detail, else the newest); members are the same ask seen elsewhere. The app shows a
member as "Same as ..." and ticking either ticks all; the work run works only the lead.

    python3 groups.py list
    python3 groups.py add --title "..." --lead RID|KEY --member RID|KEY [--member ...] --why "..." [--by "..."]
    python3 groups.py remove <group id>
    python3 groups.py check            (every key exists, each key in at most one group, the lead is not a member)
Stdlib only. Writes atomically.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import argparse, datetime as dt, json, os, re, sys

HUB = str(paths.home())
GF = os.path.join(HUB, 'groups.json')
REF = re.compile(r'^\d{4}-\d{2}-\d{2}-\d{4}-[a-z]+\|[a-z]\d+(\.\d+)?$')


def load():
    return json.load(open(GF)) if os.path.exists(GF) else {'groups': []}


def save(d):
    tmp = GF + '.tmp'
    json.dump(d, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, GF)


def exists(ref):
    rid, key = ref.split('|')
    p = os.path.join(HUB, 'roundups', rid + '.json')
    if not os.path.exists(p):
        return False
    aid, _, i = key.partition('.')
    a = next((x for x in json.load(open(p)).get('actions', []) + json.load(open(p)).get('fyi', []) if x.get('id') == aid), None)
    if not a:
        return False
    return not i or int(i) < len(a.get('points') or [])


def check(d):
    errs, seen = [], {}
    for g in d['groups']:
        refs = [g['lead']] + g['members']
        if g['lead'] in g['members']:
            errs.append(f"{g['id']}: the lead is also a member")
        for r in refs:
            if not REF.match(r):
                errs.append(f"{g['id']}: bad reference {r!r}")
            elif not exists(r):
                errs.append(f"{g['id']}: {r} not found in the round-ups")
            if r in seen and seen[r] != g['id']:
                errs.append(f"{r} is in both {seen[r]} and {g['id']}")
            seen[r] = g['id']
    return errs


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list'); sub.add_parser('check')
    a = sub.add_parser('add')
    a.add_argument('--title', required=True); a.add_argument('--lead', required=True)
    a.add_argument('--member', action='append', required=True); a.add_argument('--why', required=True); a.add_argument('--by', default='Claude')
    r = sub.add_parser('remove'); r.add_argument('id')
    o = ap.parse_args(argv)
    d = load()
    if o.cmd == 'list':
        for g in d['groups']:
            print(f"{g['id']}: {g['title']}\n  lead   {g['lead']}\n" + ''.join(f"  member {m}\n" for m in g['members']) + f"  why: {g['why']}")
        return 0
    if o.cmd == 'check':
        errs = check(d)
        print('\n'.join(errs) or f"ok: {len(d['groups'])} groups")
        return 1 if errs else 0
    if o.cmd == 'remove':
        d['groups'] = [g for g in d['groups'] if g['id'] != o.id]
        save(d); print('removed', o.id); return 0
    # add: a reference already in a group joins that group instead of starting a second one
    refs = [o.lead] + o.member
    g = next((g for g in d['groups'] if set(refs) & set([g['lead']] + g['members'])), None)
    if g:
        g['members'] = sorted(set(g['members']) | (set(refs) - {g['lead']}))
        g['why'] += ' | ' + o.why
    else:
        gid = 'g-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        n = 2
        while any(x['id'] == gid for x in d['groups']):
            gid = gid.split('~')[0] + f'~{n}'; n += 1
        g = {'id': gid, 'title': o.title, 'lead': o.lead, 'members': sorted(set(o.member) - {o.lead}), 'why': o.why,
             'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'by': o.by}
        d['groups'].append(g)
    errs = check(d)
    if errs:
        print('not saved:\n' + '\n'.join(errs)); return 1
    save(d)
    print(f"saved {g['id']}: {g['title']} (lead {g['lead']}, {len(g['members'])} member(s))")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
