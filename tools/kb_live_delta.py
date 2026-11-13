#!/usr/bin/env python3
"""The live sources again, for what changed since they were last read: the nightly update reads only chats and Jarvis's
own work, so tickets, threads, pages, mail and Teams messages that changed afterwards would be in the knowledge base only
where a chat mentioned them.

How: kb_live's readers, unchanged, pointed at a staging folder (their own "already read" checks look there, so a page or
month read before is read again in full); then every staged unit is merged into brain/sources_extra WITHOUT overwriting:
a unit whose name exists gets the suffix _upd<MMDD> (the old unit and its note stay; the new one gets its own note).
Tickets: every ticket of the listed projects UPDATED since the cut-off (not only created), with description, status and
comments. Wiki: every page or post of the listed spaces modified since. Chat: the listed channels and the work DMs. Mail,
Teams and meetings since the cut-off. What is listed comes from settings (see kb_live.py); a source with nothing set is
not read. Read-only toward every source; the personal screen runs on every unit.

    kb_live_delta.py run [--since YYYY-MM-DD]     default: where the last run ended, else 14 days back
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import datetime as dt, glob, json, os, re, shutil, sys

HUB = str(paths.home())
paths.use_tools()
import kb_live as L  # noqa: E402

STAGE = os.path.join(HUB, 'state', 'live_delta_staging')
STATE = os.path.join(HUB, 'state', 'kb_live_delta.json')


def jira_updated(since, until):
    """The project's tickets updated in [since, until), two days per call (a window over 100 is split by day)."""
    out, cost = 0, 0.0
    d = since
    while d < until:
        e = min(d + dt.timedelta(days=2), until)
        calls = f'1. jql "project = {L.KEY} AND updated >= \\"{d}\\" AND updated < \\"{e}\\" ORDER BY updated ASC"'
        prompt = (f"Call {L.JIRA} once for the JQL below, with cloudId {L.cloud()}, maxResults 100, responseContentFormat \"markdown\", "
                  f"fields {L.FIELDS}. Do not read or summarise the answer.\n{calls}\nThen reply DONE.")
        res, c, _ = L.fetch(prompt, [L.JIRA], turns=8, expect=1)
        cost += c
        text, ks = ('', [])
        if res:
            text, ks = L.jira_text(res[0][1])
        if len(ks) >= 100 and (e - d).days > 1:            # capped: read the window again a day at a time
            mid = d + dt.timedelta(days=1)
            for a, b in ((d, mid), (mid, e)):
                n, c2 = jira_updated(a, b); out += n; cost += c2
        elif text:
            L.write_unit(f'l_jira_upd_{L._jn()}{d}', f'Jira {L.KEY} tickets updated {d} to {e - dt.timedelta(days=1)} (status, description and comments as '
                                            f'they are now) | live source, read {dt.date.today()}', text)
            out += len(ks)
        d = e
    return out, cost


def confluence_modified(since):
    """Every page or post of the space modified since `since`, one query per week (a query over 250 is cut, and said so).
    10 Nov: the answer is {"content": {"totalCount", "nodes": [...]}}; this read only "results", so the 6 Nov run listed 0
    pages and read none (kb_live.confluence_list already read "nodes")."""
    wk, a = [], since
    end = dt.date.today() + dt.timedelta(days=1)
    while a < end:
        b = min(a + dt.timedelta(days=7), end); wk.append((a, b)); a = b
    calls = '\n'.join(f'{i + 1}. cql "space = {L.SPACE} AND type in (page, blogpost) AND lastmodified >= \\"{x}\\" AND lastmodified < \\"{y}\\""'
                      for i, (x, y) in enumerate(wk))
    prompt = (f"Call {L.CQL} once for each CQL below, in order, each with cloudId {L.cloud()} and limit 250. Do not read or "
              f"summarise the answers.\n{calls}\nThen reply DONE.")
    res, cost, _ = L.fetch(prompt, [L.CQL], turns=len(wk) + 6, expect=len(wk))
    pages = {}
    for inp, raw in res:
        try:
            d = json.loads(raw)
            nodes = (d.get('content') or {}).get('nodes') or d.get('results') or []
        except (ValueError, AttributeError):
            print(f'confluence: unreadable answer for {str(inp.get("cql", ""))[-60:]}', flush=True); continue
        if len(nodes) >= 250:
            print(f'confluence: CAPPED at 250 for {str(inp.get("cql", ""))[-60:]}', flush=True)
        for r in nodes:
            c = r.get('content') or r
            if c.get('id'):
                pages[str(c['id'])] = {'id': str(c['id']), 'space': L.SPACE, 'title': c.get('title') or r.get('title') or '',
                                       'modified': r.get('lastModified') or '',
                                       'author': (r.get('author') or {}).get('displayName', '') or
                                                 ((c.get('history') or {}).get('createdBy') or {}).get('displayName', ''),
                                       'url': r.get('webUrl') or ('https://' + str((c.get('_links') or {}).get('webui', '')))}
    print(f'confluence: {len(res)} of {len(wk)} weekly queries answered, {len(pages)} pages', flush=True)
    return list(pages.values()), cost


def merge(tag):
    """Every staged unit into brain/sources_extra, never over an existing one."""
    moved = []
    os.makedirs(L.EXTRA_REAL, exist_ok=True)              # a new knowledge base has no source units yet
    for f in sorted(glob.glob(os.path.join(STAGE, '*.txt'))):
        name = os.path.basename(f)[:-4]
        dst = os.path.join(L.EXTRA_REAL, name + '.txt')
        if os.path.exists(dst):
            name = f'{name}_upd{tag}'
            k = 2
            while os.path.exists(os.path.join(L.EXTRA_REAL, name + '.txt')):
                name = f'{os.path.basename(f)[:-4]}_upd{tag}-{k}'; k += 1
            dst = os.path.join(L.EXTRA_REAL, name + '.txt')
        shutil.move(f, dst)
        moved.append(name)
    return moved


def run(since):
    os.makedirs(STAGE, exist_ok=True)
    L.EXTRA_REAL = L.EXTRA
    L.EXTRA = STAGE                                       # the readers write here, and look here for what they have read
    st = {'since': str(since), 'started': dt.datetime.now().isoformat(timespec='seconds'), 'parts': {}}
    tag = dt.date.today().strftime('%m%d')
    total = 0.0

    def note(k, **kw):
        st['parts'][k] = kw; json.dump(st, open(STATE, 'w'), indent=1)

    try:
        n, c = 0, 0.0
        for L.KEY in L.projects() if L.cloud() else []:        # one listed project at a time
            n1, c1 = jira_updated(since, dt.date.today() + dt.timedelta(days=1)); n += n1; c += c1
        total += c; note('jira', tickets=n, cost=round(c, 2))
        print(f'jira: {n} tickets updated since {since}, ${c:.2f}', flush=True)
        pages, c = [], 0.0
        for L.SPACE in L.spaces():                             # one listed space at a time
            p1, c1 = confluence_modified(since); pages += p1; c += c1
        total += c
        orig = L._state
        L._state = lambda: {'confluence_pages': pages}
        try:
            d, n, c2 = L.confluence_pages()
        finally:
            L._state = orig
        total += c2; note('confluence', pages=d, listed=len(pages), cost=round(c + c2, 2))
        print(f'confluence: {d} of {len(pages)} pages modified since {since}, ${c + c2:.2f}', flush=True)
        for cid, name, start in L.channels():
            m, th, c = L.slack_channel(cid, name, str(since)); total += c
            note('slack ' + name, messages=m, threads=th, cost=round(c, 2))
            print(f'#{name}: {m} messages, {th} threads, ${c:.2f}', flush=True)
        L.EXTRA = L.EXTRA_REAL; ppl = L._people(); L.EXTRA = STAGE          # who to read DMs with: from every channel unit
        orig_p = L._people
        L._people = lambda min_msgs=3: ppl
        try:
            got, c = L.slack_dms(start=since); total += c
        finally:
            L._people = orig_p; note('slack dms', people=len(got), messages=sum(got.values()), cost=round(c, 2))
        print(f'DMs: {sum(got.values())} messages with {len(got)} people, ${c:.2f}', flush=True)
        if not L.mail_terms():                               # no words that mark work mail: mail, Teams and meetings are not read
            return
        found, c = L.mail_list(start=since); total += c
        n, th, c2 = L.mail_read(found); total += c2; note('mail', messages=n, threads=th, cost=round(c + c2, 2))
        print(f'mail: {n} messages in {th} threads, ${c + c2:.2f}', flush=True)
        n, g, c = L.teams(start=since); total += c; note('teams', messages=n, units=g, cost=round(c, 2))
        print(f'teams: {n} messages, ${c:.2f}', flush=True)
        n, t, c = L.meetings(start=since); total += c; note('meetings', meetings=n, transcripts=t, cost=round(c, 2))
        print(f'meetings: {n}, transcripts {t}, ${c:.2f}', flush=True)
    finally:
        L.EXTRA = L.EXTRA_REAL
        moved = merge(tag)
        st.update(finished=dt.datetime.now().isoformat(timespec='seconds'), units=moved, cost=round(total, 2))
        json.dump(st, open(STATE, 'w'), indent=1)
        print(f'{len(moved)} units merged into the knowledge base (none overwritten), ${total:.2f}')


def _default_since():
    """Where the last finished run started (what changed after it began is read again), else 14 days back."""
    try:
        st = json.load(open(STATE))
        if st.get('finished'):
            return dt.date.fromisoformat(str(st.get('started'))[:10])
    except (OSError, ValueError):
        pass
    return dt.date.today() - dt.timedelta(days=14)


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['run']:
        run(dt.date.fromisoformat(a[a.index('--since') + 1]) if '--since' in a else _default_since())
    else:
        print(__doc__)
