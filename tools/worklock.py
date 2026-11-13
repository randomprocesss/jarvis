#!/usr/bin/env python3
"""Per-item claims so two work runs (the routine and a Jarvis "start this" run) never work the same item at once.

    python3 worklock.py claim <rid> <aid> [--by "<who>"]   exit 0 = yours now; exit 1 = someone else is on it (skip it)
    python3 worklock.py release <rid> <aid>
    python3 worklock.py list
A claim older than STALE_MIN minutes is stale and can be taken over (a run that died). Stdlib only.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import argparse, datetime as dt, json, os, re, sys

D = str(paths.home() / 'work/locks')
STALE_MIN = 90
OK = re.compile(r'^[A-Za-z0-9._-]+$')


def path(rid, aid):
    if not (OK.match(rid) and OK.match(aid)):
        sys.exit('bad id')
    return os.path.join(D, f'{rid}__{aid}.json')


def age_min(p):
    try:
        t = dt.datetime.fromisoformat(json.load(open(p))['at'])
        return (dt.datetime.now().astimezone() - t).total_seconds() / 60
    except Exception:
        return 1e9


def main(a):
    ap = argparse.ArgumentParser()
    s = ap.add_subparsers(dest='cmd', required=True)
    c = s.add_parser('claim'); c.add_argument('rid'); c.add_argument('aid'); c.add_argument('--by', default='work run')
    r = s.add_parser('release'); r.add_argument('rid'); r.add_argument('aid')
    s.add_parser('list')
    o = ap.parse_args(a)
    os.makedirs(D, exist_ok=True)
    if o.cmd == 'list':
        for f in sorted(os.listdir(D)):
            p = os.path.join(D, f)
            j = json.load(open(p))
            print(f"{f[:-5].replace('__', ' ')}: {j.get('by')} since {j.get('at')}" + (' (stale)' if age_min(p) > STALE_MIN else ''))
        return 0
    p = path(o.rid, o.aid)
    if o.cmd == 'release':
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        print('released'); return 0
    if os.path.exists(p) and age_min(p) <= STALE_MIN:
        j = json.load(open(p))
        print(f"busy: {j.get('by')} has had it since {j.get('at')}"); return 1
    if os.path.exists(p):
        os.remove(p)                                   # stale: the run that held it died
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # atomic: of two runs racing, only one gets it
    except FileExistsError:
        print('busy: just taken by another run'); return 1
    with os.fdopen(fd, 'w') as f:
        json.dump({'by': o.by, 'at': dt.datetime.now().astimezone().isoformat(timespec='seconds')}, f)
    print('claimed'); return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
