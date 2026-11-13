#!/usr/bin/env python3
"""Shadow scoring: Jarvis's answer against what the owner actually sent (autonomy plan, part D; 12 Nov 2026).

An action class can only earn autonomy on evidence, and the evidence was not being kept: how often the owner sends Jarvis's
reply as written, how often they rewrite it, and how often they ignore it and writes their own. Everything needed is already in
the work entries, so this is code only, no model:
  - `reply.text`           what Jarvis drafted;
  - `reply.sent_at/by`     it went out (their Send press in Jarvis, or they sent the draft from Slack);
  - the `draft_edited` signal (learn_signals)   both texts when they changed it before sending, with their likeness;
  - `replied_by_owner`     they answered the ask themselves.
One row per answered item in jarvis.db `shadow`:
  outcome   as_written (they sent Jarvis's text) | edited (they changed it, likeness kept) | own (they wrote their own answer while
            Jarvis's draft sat there) | own_first (they answered before Jarvis had a draft: not scored)
  agree     1.0 for as_written (the same words as the whole text that went out, see slack_send.plain(); corrected 12 Nov,
            before that 12 unchanged replies counted as edited); the likeness (0-1) for edited and own. Wording only: an answer that says the same in other
            words still scores low. A model judge for meaning comes with the exams (phase 4); until then this is the strict,
            conservative number, which is the right one for granting autonomy.
  cls       the action class: channel + who it went to (slack:Priya Nair, jira, slack:#orders).

    shadow_score.py            update the table from the work entries and print the summary per class
    shadow_score.py --json
update() is cheap (it reads the work files) and safe to call often; rows are replaced, never duplicated.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import datetime as dt, difflib, glob, json, os, re, sqlite3, sys

HUB = str(paths.home())
from jarvis import slack_send as SS  # noqa: E402  (plain(), word_likeness(): one definition of "the same words")
DB = os.path.join(HUB, 'state', 'jarvis.db')
_LINK_LINE = re.compile(r'^(File|Attached):.*$', re.M)


def _comparable(t):                                        # as app/slack_send.py compares a draft with what was sent
    t = _LINK_LINE.sub('', str(t or ''))
    t = re.sub(r'<@[A-Z0-9]+(\|[^>]*)?>', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def _like(a, b):
    a, b = _comparable(a), _comparable(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _cls(r):
    ch = str(r.get('channel') or 'slack')
    who = str(r.get('to') or '').strip()
    where = str(r.get('where') or '').strip()
    if ch == 'slack':
        return f"slack:{where if where.startswith('#') else (who or where or 'unknown')}"
    return f'{ch}:{who}' if who else ch


def _full(r, by):
    """The whole text that went out, when it is known: kept at sending (sent_full, from 12 Nov, and filled back for the
    earlier ones from Slack), or the text of their Send press in Jarvis."""
    if r.get('sent_full'):
        return str(r['sent_full'])
    t = str((r.get('send') or {}).get('text') or '')
    return t if by == 'Jarvis' and len(t) > len(str(r.get('sent_text') or '')) else ''


def _edits(con):
    """{rid|aid: likeness} from the draft_edited signals (the full texts were compared when it was sent)."""
    out = {}
    for ref, data in con.execute("SELECT ref, data FROM learn_signals WHERE kind='draft_edited'"):
        try:
            out[ref] = float(json.loads(data or '{}').get('ratio'))
        except (TypeError, ValueError):
            pass
    return out


SHADOW = ("CREATE TABLE IF NOT EXISTS shadow (rid TEXT, aid TEXT, at TEXT, cls TEXT, outcome TEXT, agree REAL, by TEXT, "
          "PRIMARY KEY (rid, aid))")


def ready():
    """The tables this reads exist, also before Jarvis has ever run: the learning store's own, and the shadow table."""
    paths.ensure()
    from jarvis import learned
    learned.q('SELECT 1 FROM learn_signals LIMIT 1')        # opening the store creates its tables
    con = sqlite3.connect(DB, timeout=20)
    with con:
        con.execute(SHADOW)
    con.close()


def rows():
    ready()
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=10)
    edits = _edits(con)
    con.close()
    out = []
    for f in sorted(glob.glob(os.path.join(HUB, 'work', '20*.json'))):
        rid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        for aid, w in d.items():
            if not isinstance(w, dict) or aid.startswith('_') or w.get('merged_into'):
                continue
            r = w.get('reply') if isinstance(w.get('reply'), dict) else {}
            draft = str(r.get('text') or '').strip()
            own = w.get('replied_by_owner') if isinstance(w.get('replied_by_owner'), dict) else {}
            ref = f'{rid}|{aid}'
            if r.get('dropped'):
                continue
            if draft and r.get('sent_at'):
                by = str(r.get('sent_by') or '')
                if by.startswith('ladder'):                # it went out by itself: no press of his to score, and it never counts toward a run
                    out.append((rid, aid, str(r['sent_at']), _cls(r), 'auto', None, by))
                    continue
                full = _full(r, by)
                if isinstance(r.get('sent_same'), bool):   # the verdict recorded at sending (or filled back, 12 Nov) against the whole text
                    outcome, agree = ('as_written', 1.0) if r['sent_same'] else ('edited', float(r.get('sent_like') or 0))
                elif r.get('sent_same') == 'unknown':      # the draft of that moment is gone: not scored
                    continue
                elif full:                                 # the whole text that went out: word for word, or it is an edit
                    same = SS.plain(draft) == SS.plain(full)
                    outcome, agree = ('as_written', 1.0) if same else ('edited', SS.word_likeness(draft, full))
                elif ref in edits:
                    outcome, agree = 'edited', edits[ref]
                else:                                      # only the first 200 characters of what went out were kept
                    a, b = _comparable(draft), _comparable(r.get('sent_text') or '')
                    pre = _like(a[:len(b)], b) if b else 1.0
                    outcome, agree = ('as_written', 1.0) if pre >= 0.9 else ('edited', pre)
                out.append((rid, aid, str(r['sent_at']), _cls(r), outcome, round(agree, 3), by))
            elif draft and own.get('text'):
                at = str(own.get('at') or w.get('updated') or '')
                drafted = str(r.get('slack_draft_at') or '')
                if drafted and at and drafted > at:        # his answer came first; Jarvis's draft was written after it
                    out.append((rid, aid, at, _cls(r), 'own_first', None, 'The owner'))
                else:
                    out.append((rid, aid, at, _cls(r), 'own', round(_like(draft, own['text']), 3), 'The owner'))
            elif own.get('text') and not draft:
                out.append((rid, aid, str(own.get('at') or ''), f"{own.get('where') or 'slack'}:", 'own_first', None, 'The owner'))
    return out


def update():
    rs = rows()
    con = sqlite3.connect(DB, timeout=20)
    con.execute(SHADOW)
    with con:
        con.executemany("INSERT OR REPLACE INTO shadow (rid, aid, at, cls, outcome, agree, by) VALUES (?,?,?,?,?,?,?)", rs)
        keep = {(r[0], r[1]) for r in rs}                  # the table is derived from the work entries: a row they no longer give goes
        for rid, aid in con.execute('SELECT rid, aid FROM shadow').fetchall():
            if (rid, aid) not in keep:
                con.execute('DELETE FROM shadow WHERE rid=? AND aid=?', (rid, aid))
    con.close()
    return rs


def summary(rs=None):
    rs = rs if rs is not None else rows()
    by = {}
    for rid, aid, at, cls, outcome, agree, _ in rs:
        if outcome in ('own_first', 'auto'):
            continue
        c = by.setdefault(cls, {'n': 0, 'as_written': 0, 'edited': 0, 'own': 0, 'agree': 0.0, 'last': ''})
        c['n'] += 1; c[outcome] += 1; c['agree'] += agree or 0.0; c['last'] = max(c['last'], at[:10])
    for c in by.values():
        c['agree'] = round(c['agree'] / c['n'], 3)
    scored = [x for x in rs if x[4] not in ('own_first', 'auto')]
    tot = {'scored': len(scored), 'as_written': sum(1 for x in scored if x[4] == 'as_written'),
           'edited': sum(1 for x in scored if x[4] == 'edited'), 'own': sum(1 for x in scored if x[4] == 'own'),
           'own_first': sum(1 for x in rs if x[4] == 'own_first'), 'auto': sum(1 for x in rs if x[4] == 'auto'), 'agree': round(sum(x[5] or 0 for x in scored) / len(scored), 3) if scored else None}
    return {'at': dt.datetime.now().isoformat(timespec='seconds'), 'total': tot, 'classes': dict(sorted(by.items(), key=lambda kv: -kv[1]['n']))}


if __name__ == '__main__':
    s = summary(update())
    if '--json' in sys.argv:
        print(json.dumps(s, indent=1))
    else:
        t = s['total']
        print(f"Shadow score, {s['at']}: {t['scored']} answered items with a Jarvis draft; sent as written {t['as_written']}, "
              f"edited {t['edited']}, they wrote their own {t['own']}; mean likeness {t['agree']}. "
              f"Not scored (they answered before a draft existed): {t['own_first']}.")
        for k, c in s['classes'].items():
            print(f"  {k[:44]:44s} n={c['n']:3d}  as written {c['as_written']:2d}  edited {c['edited']:2d}  own {c['own']:2d}  likeness {c['agree']:.2f}  last {c['last']}")
