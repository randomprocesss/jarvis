"""Jarvis's longer memory. Three ideas from the research:
  - episodes: what happened, when, with whom and how it ended, kept per topic (arXiv 2502.06975, "episodic memory is the
    missing piece for long-term agents"); LongMemEval's best tricks: search keys written at storage time, time-aware lookup;
  - consolidation while idle, off the answer path (Letta's "sleep-time compute");
  - a fact that stops being true gets an end date instead of being overwritten (Zep / Graphiti's temporal facts), so
    "the old rows are on the test system" can end on a date and "what was it before?" still has an answer.

Files in the data home's voice folder (local, the owner's own data):
  episodes.jsonl     {id, at, end, conv, title, summary, people, topics, keys, outcome, open}, one per line
  profile.json       {"facts": [{id, text, kind, since, until, src, at}]}; until = null while it holds
  consolidated.json  {"convs": {conv_id: last turn time done}, "consults": [ids], "tidied": "YYYY-MM-DD"}

consolidate()   conversations quiet for 10 minutes with turns not done yet, plus finished Claude consults -> one Sonnet pass
                (no tools) -> new episodes, new facts, facts that ended. start() runs it every 10 minutes in a server thread.
tidy()          once a day after 02:00: duplicate facts merged, stale ones ended.
recall_block()  the episodes that best match their words (BM25 over title, summary and keys; "yesterday", "on Monday", "last
                week" narrow it by date), plus matching turns of conversations not consolidated yet.
profile_block() the facts that hold now, grouped (current state first), at most ~4500 characters (~1,100 tokens, given once
                per brain process, not every turn).
Nothing that looks like a password, token or key is stored.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, json, math, os, re, subprocess, threading, time, uuid

VOICE = str(paths.home() / 'voice')
CONV_DIR, CONSULTS = os.path.join(VOICE, 'conversations'), os.path.join(VOICE, 'consults')
EPI, PROF, DONE = os.path.join(VOICE, 'episodes.jsonl'), os.path.join(VOICE, 'profile.json'), os.path.join(VOICE, 'consolidated.json')
LOG = os.path.join(VOICE, 'consolidate.log')
CLAUDE, MODEL = (paths.claude_bin() or 'claude'), config.model('mid')
QUIET_MIN, BATCH_CHARS = 10, 40000
KINDS = ('person', 'project', 'state', 'preference', 'term')
_lock, _run = threading.Lock(), threading.Lock()
STOP = set("a an the is are was were be been to of in on at for and or but with my your our me you i it this that what which who how do does did "
           "can could would should will please jarvis about from by as so if then than there here any some all just now yes no not get got "
           "have has had sir okay ok tell said say one two its it's let's thanks thank hello hi hey bye good great nice cool fine sure "
           "yeah yep alright right please know want need".split())
SECRET = re.compile(r"\b(password|passwd|pgpass|api[_ -]?key|secret|token|bearer)\s*(?:[:=]|\bis\b)\s*\S+|\b[A-Za-z0-9+/_-]{32,}\b", re.I)


def _load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def _save(p, o):
    tmp = p + '.tmp'
    json.dump(o, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def _log(m):
    try:
        with open(LOG, 'a') as f:
            f.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}\n')
    except OSError:
        pass


def _clean(t, n=600):
    return SECRET.sub('[hidden]', re.sub(r'\s+', ' ', str(t or ''))).strip()[:n]


def _toks(t):
    out = []
    for w in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(t or '').lower()):
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith('ss') else w)
    return out


def episodes():
    out = []
    try:
        for l in open(EPI):
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def facts(current=True):
    """The facts, from the learned store (app/learned.py), in the shape this module has always used."""
    from jarvis import learned
    rows = learned.current_facts() if current else learned.q('SELECT * FROM facts ORDER BY created')
    return [{'id': r['id'], 'text': r['text'], 'kind': r['kind'], 'since': r['valid_from'], 'until': r['valid_to'],
             'src': r['source_ref'], 'at': r['created'], 'origin': r['origin'], 'tainted': r['tainted']} for r in rows]


# ------------------------------------------------------------------ consolidation (off the answer path)
def _pending(now=None):
    """(conversations to do: [(cid, new turns)], consults to do: [record]) — only conversations quiet for QUIET_MIN."""
    now = now or dt.datetime.now()
    st = _load(DONE, {'convs': {}, 'consults': []})
    convs, cons = [], []
    if os.path.isdir(CONV_DIR):
        for f in sorted(os.listdir(CONV_DIR)):
            if not f.endswith('.json'):
                continue
            d = _load(os.path.join(CONV_DIR, f), {})
            cid, turns = f[:-5], d.get('turns') or []
            if not turns:
                continue
            last = str(turns[-1].get('at') or '')
            try:
                if (now - dt.datetime.fromisoformat(last[:19])).total_seconds() < QUIET_MIN * 60:
                    continue
            except ValueError:
                continue
            mark = st['convs'].get(cid, '')
            new = [t for t in turns if str(t.get('at') or '') > mark]
            if new:
                convs.append((cid, new))
    if os.path.isdir(CONSULTS):
        for f in sorted(os.listdir(CONSULTS)):
            d = _load(os.path.join(CONSULTS, f), {})
            if f.endswith('.json') and d.get('state') in ('done', 'failed') and d.get('id') not in st['consults']:
                cons.append(d)
    return convs, cons


def _material(convs, cons):
    """The new turns and consults as text, cut into batches of about BATCH_CHARS."""
    blocks = []
    for cid, turns in convs:
        lines = [f'CONVERSATION {cid}']
        for k, t in enumerate(turns):
            acts = ', '.join(a.get('type', '') + (':' + a.get('title', '') if a.get('title') else '') for a in t.get('actions') or [])
            lines.append(f"[{cid[-4:]}.{k} {str(t.get('at', ''))[:16]}] OWNER: {_clean(t.get('q'), 400)}\n  JARVIS: {_clean(t.get('speak'), 500)}"
                         + (f"\n  SCREEN TEXT: {_clean(t.get('text'), 500)}" if t.get('text') and t.get('text') != t.get('speak') else '')
                         + (f"\n  ACTIONS: {acts}" if acts else '') + (' (they interrupted it)' if t.get('interrupted') else ''))
        blocks.append(('conv', cid, turns[-1].get('at', ''), '\n'.join(lines)))
    for c in cons:
        blocks.append(('consult', c.get('id'), '', f"[consult.{str(c.get('id'))[-4:]}] CLAUDE CONSULT {c.get('id')} [{str(c.get('started', ''))[:16]}] ({c.get('state')})\n"
                       f"  QUESTION: {_clean(c.get('q'), 400)}\n  ANSWER: {_clean(c.get('speak'), 500)}\n  DETAIL: {_clean(c.get('text'), 900)}"))
    batches, cur, size = [], [], 0
    for b in blocks:
        if cur and size + len(b[3]) > BATCH_CHARS:
            batches.append(cur); cur, size = [], 0
        cur.append(b); size += len(b[3])
    if cur:
        batches.append(cur)
    return batches


PROMPT = """You keep the long-term memory of Jarvis, {owner}'s assistant{work}. In these instructions "they", "their" and "them" mean \
{owner}, whoever they are. Below are the CURRENT FACTS (with ids) and NEW MATERIAL: their conversations with Jarvis \
(times are their local time{tz}) and answers Claude dug up for them.
Reply with ONE JSON object and nothing else:
{"episodes": [{"conv": "<conversation or consult id>", "at": "YYYY-MM-DDTHH:MM", "end": "YYYY-MM-DDTHH:MM", "title": "<5-9 words>", \
"summary": "<1-3 sentences: what they wanted, what was found or decided, with the exact numbers, names and dates>", \
"people": ["<names>"], "topics": ["<short topics>"], "keys": ["<8-15 words they might use later to ask about it: synonyms, ids, \
tables, related terms>"], "outcome": "answered|done|started|pending|failed|chat", "open": "<what is still open, or empty>"}],
 "facts_add": [{"text": "<one fact, present tense, under 35 words; for a state, say since when>", "kind": "person|project|state|preference|term", \
"since": "YYYY-MM-DD", "evidence": ["<label>:owner" or "<label>:jarvis" or "consult.<id>"]}],
 "facts_end": [{"id": "<id of a current fact that is no longer true>", "why": "<few words>", "evidence": ["..."]}]}
Every fact added or ended cites where it comes from: the turn label in square brackets (e.g. "abcd.2") plus ":owner" when it \
is in their own words or ":jarvis" when only Jarvis said it, or the consult label ("consult.1f2e"). No evidence, no fact.
Rules:
- One episode per distinct topic (a conversation may have several, or none). Greetings, small talk, voice or system tests \
("say just the word ready", "testing", repeated test phrases) and empty exchanges give no episode.
- Facts are only lasting, useful things: who people are and what they work on, the state of projects and data, decisions, \
their preferences about how Jarvis talks or works, their abbreviations and terms. Not one-off questions.
- When the new material contradicts a current fact, end the old one (facts_end) and add the new one. Never duplicate a current fact.
- Never store whether a reply or file is still unsent, drafted, waiting or not yet replied: Jarvis's inbox tracks that live and its tools are the truth (a stored "not sent yet" gets repeated hours after it went). Store what was asked, found or decided, with the numbers.
- Never store passwords, tokens, keys or connection strings, and nothing about health, family or private life.
- Use only what the material says; never guess.

CURRENT FACTS:
{facts}

NEW MATERIAL:
{material}"""


def _prompt_text():
    """The consolidation prompt for this owner: their name, one clause on their work, their time zone."""
    from jarvis import config
    import time as _t
    c = config.load()
    work, tz = c.work_line(), _t.strftime('%Z')
    return PROMPT.replace('{owner}', c.owner_or()).replace('{work}', f' for their work ({work})' if work else '').replace('{tz}', f', {tz}' if tz else '')


def _ask(prompt, timeout=240):
    p = subprocess.run([CLAUDE, '-p', prompt, '--model', MODEL, '--output-format', 'json', '--strict-mcp-config',
                        '--mcp-config', '{"mcpServers":{}}', '--disallowedTools', 'Bash', 'Edit', 'Write', 'Read', 'Grep', 'Glob',
                        'WebFetch', 'WebSearch', 'Task', 'Agent', 'NotebookEdit'],
                       capture_output=True, text=True, timeout=timeout, cwd=VOICE)
    try:                                    # the json envelope: the reply is in "result", the usage goes to the governor
        env = json.loads(p.stdout or '{}')
        out = str(env.get('result') or '')
        try:
            from jarvis.spine import governor
            u = env.get('usage') or {}
            governor.note_learning(governor._weight(u, MODEL))
        except Exception:
            pass
    except ValueError:
        out = p.stdout or ''
    a, b = out.find('{'), out.rfind('}')
    if a < 0 or b <= a:
        raise ValueError('no JSON in the reply: ' + out[:200])
    return json.loads(out[a:b + 1])


_LABEL = re.compile(r"^\[(\w{4}\.\d+) [^\]]*\] (?:OWNER|HE): (.*)$", re.M)   # HE: the label older saved material carries


def _origins(batch):
    """label -> {'he': their words, 'corrected': did their NEXT turn correct Jarvis} from the batch's own text; consult labels."""
    from jarvis import human as H
    out = {}
    for kind, key, last, text in batch:
        if kind == 'consult':
            out['consult.' + str(key)[-4:]] = {'consult': True, 'ref': f'consult:{key}'}
            continue
        turns = _LABEL.findall(text)
        for i, (lab, he) in enumerate(turns):
            nxt = turns[i + 1][1] if i + 1 < len(turns) else ''
            corrected = bool(nxt) and bool(H.CORRECT.search(nxt) or H.NEG.search(nxt) or H.NO_LEAD.match(nxt))
            out[lab] = {'he': he, 'corrected': corrected, 'ref': f'voice:{key}#{lab}'}
    return out


def _cite(origins, evidence):
    """(origin, source_ref, quote) for an edit's evidence, decided here, never by the model: their words -> owner_said; Jarvis's
    words they did not correct -> owner_ack; a consult (what Claude read in Slack, Jira, files) -> external. Nothing usable ->
    (None, '', ''): no evidence, no fact."""
    best, ref, quote = None, '', ''
    rank = {'owner_said': 3, 'owner_ack': 2, 'external': 1}
    for ev in evidence or []:
        ev = str(ev).strip()
        lab, _, who = ev.partition(':')
        o = origins.get(lab)
        if not o:
            continue
        if o.get('consult'):
            cand, r, qt = 'external', o['ref'], ''
        elif who in ('owner', 'he'):
            cand, r, qt = 'owner_said', o['ref'], o['he'][:300]
        elif o.get('corrected'):
            continue                          # he corrected what Jarvis said there: it is not evidence
        else:
            cand, r, qt = 'owner_ack', o['ref'], ''
        if best is None or rank[cand] > rank[best]:
            best, ref, quote = cand, r, qt
    return best, ref, quote


def _apply(r, batch):
    """Stores one pass's episodes and fact changes; marks the batch done."""
    today = dt.date.today().isoformat()
    now = dt.datetime.now().isoformat(timespec='seconds')
    srcs = ','.join(str(b[1]) for b in batch)[:300]
    with _lock:
        eps = []
        for e in r.get('episodes') or []:
            if not isinstance(e, dict) or not e.get('summary'):
                continue
            eps.append({'id': uuid.uuid4().hex[:8], 'at': _clean(e.get('at'), 16), 'end': _clean(e.get('end'), 16), 'conv': _clean(e.get('conv'), 40),
                        'title': _clean(e.get('title'), 100), 'summary': _clean(e.get('summary'), 600),
                        'people': [_clean(x, 40) for x in (e.get('people') or [])][:8], 'topics': [_clean(x, 40) for x in (e.get('topics') or [])][:8],
                        'keys': [_clean(x, 40) for x in (e.get('keys') or [])][:16], 'outcome': _clean(e.get('outcome'), 12), 'open': _clean(e.get('open'), 200)})
        if eps:
            with open(EPI, 'a') as f:
                for e in eps:
                    f.write(json.dumps(e, ensure_ascii=False) + '\n')
        edits = []
        origins = _origins(batch)
        for x in r.get('facts_end') or []:
            o, ref, quote = _cite(origins, (x or {}).get('evidence'))
            if o:
                edits.append({'op': 'CLOSE', 'table': 'facts', 'id': str((x or {}).get('id')), 'why': _clean(x.get('why'), 100),
                              'origin': o, 'source_ref': ref})
        for x in r.get('facts_add') or []:
            o, ref, quote = _cite(origins, (x or {}).get('evidence'))
            # the raw text goes to the validator: _clean masks a secret as [hidden], and the rest of the line would pass
            t = re.sub(r'\s+', ' ', str((x or {}).get('text') or '')).strip()[:400]
            if not o or not t:
                _log(f'fact refused (no usable evidence): {t[:80]}'); continue
            edits.append({'op': 'ADD', 'table': 'facts', 'text': t, 'kind': x.get('kind') if x.get('kind') in KINDS else 'project',
                          'since': _clean(x.get('since'), 10) or today, 'origin': o, 'source_ref': ref, 'quote': quote,
                          'tainted': 1 if o == 'external' else 0})
        from jarvis import learned
        res = learned.apply_edits(edits, run='consolidate-' + now[:16], source_ref=srcs)
        added = sum(1 for e, ok, _ in res if ok and e['op'] == 'ADD')
        ended = sum(1 for e, ok, _ in res if ok and e['op'] == 'CLOSE')
        st = _load(DONE, {'convs': {}, 'consults': []})
        for kind, key, last, _ in batch:
            if kind == 'conv':
                st['convs'][key] = max(st['convs'].get(key, ''), str(last))
            else:
                st['consults'] = (st['consults'] + [key])[-500:]
        _save(DONE, st)
    return len(eps), added, ended


def _facts_text(fs):
    return '\n'.join(f"[{f['id']}] ({f['kind']}, since {f.get('since', '')}) {f['text']}" for f in fs) or '(none yet)'


def consolidate(now=None):
    """One round: everything pending, in batches. Returns a short summary (also in consolidate.log). One round at a time."""
    if not _run.acquire(blocking=False):
        return 'busy'
    try:
        convs, cons = _pending(now)
        if not convs and not cons:
            return 'nothing new'
        total = [0, 0, 0]
        for batch in _material(convs, cons):
            prompt = _prompt_text().replace('{facts}', _facts_text(facts())).replace('{material}', '\n\n'.join(b[3] for b in batch))
            try:
                r = _ask(prompt)
            except Exception as e:
                _log(f'consolidate failed: {e}'[:300]); return f'failed: {e}'[:200]
            n = _apply(r, batch)
            total = [a + b for a, b in zip(total, n)]
        msg = f'consolidated {len(convs)} conversations + {len(cons)} consults: {total[0]} episodes, {total[1]} facts added, {total[2]} ended'
        _log(msg)
        return msg
    finally:
        _run.release()


TIDY = """These are the facts Jarvis currently holds about the owner's work (ids in brackets, today is {today}). Merge facts that say the \
same thing, end facts that are clearly stale or superseded by another fact in the list, and rewrite a fact that is cut off \
mid-sentence or longer than 35 words (keep its meaning and numbers). Change nothing else.
Reply with ONE JSON object only: {"merge": [{"ids": ["<id>", "..."], "text": "<the merged fact, under 35 words>", "kind": "person|project|state|preference|term"}], \
"rewrite": [{"id": "<id>", "text": "<the fact, under 35 words>"}], "end": [{"id": "<id>", "why": "<few words>"}]}

FACTS:
{facts}"""


def tidy(force=False):
    """Once a day (after 02:00): duplicates merged, stale facts ended."""
    st = _load(DONE, {'convs': {}, 'consults': []})
    today = dt.date.today().isoformat()
    if not force and (st.get('tidied') == today or dt.datetime.now().hour < 2):
        return 'not now'
    fs = facts()
    if len(fs) < 12 and not force:
        st['tidied'] = today; _save(DONE, st); return 'few facts'
    if not _run.acquire(blocking=False):
        return 'busy'
    try:
        r = _ask(TIDY.replace('{today}', today).replace('{facts}', _facts_text(fs)))
        from jarvis import learned
        cur = {f['id']: f for f in facts()}
        rank = {'owner_said': 3, 'owner_ack': 2, 'executed': 2, 'external': 1}
        weakest = lambda ids: min((cur[i].get('origin') or 'owner_ack' for i in ids), key=lambda o: rank.get(o, 1))
        edits, n, w, e = [], 0, 0, 0
        for m in r.get('merge') or []:
            ids = [i for i in (m.get('ids') or []) if i in cur]
            t = _clean(m.get('text'), 400)
            if len(ids) < 2 or not t:
                continue
            o = weakest(ids)                  # a merge is only as trusted as its weakest source
            edits += [{'op': 'CLOSE', 'table': 'facts', 'id': i, 'why': 'merged', 'origin': 'executed', 'source_ref': 'tidy'} for i in ids]
            edits.append({'op': 'ADD', 'table': 'facts', 'text': t, 'kind': m.get('kind') if m.get('kind') in KINDS else cur[ids[0]]['kind'],
                          'since': min(cur[i].get('since') or today for i in ids), 'origin': o, 'source_ref': 'tidy:' + ','.join(ids),
                          'tainted': 1 if o == 'external' else 0})
            n += 1
        for x in r.get('rewrite') or []:
            f, t = cur.get(str((x or {}).get('id'))), _clean((x or {}).get('text'), 400)
            if f and t:
                edits.append({'op': 'UPDATE', 'table': 'facts', 'id': f['id'], 'text': t, 'origin': f.get('origin') or 'owner_ack',
                              'source_ref': 'tidy:' + f['id']})
                w += 1
        for x in r.get('end') or []:
            f = cur.get(str((x or {}).get('id')))
            if f:
                edits.append({'op': 'CLOSE', 'table': 'facts', 'id': f['id'], 'why': _clean(x.get('why'), 100), 'origin': 'executed', 'source_ref': 'tidy'})
                e += 1
        learned.apply_edits(edits, run='tidy-' + today, source_ref='tidy')
        with _lock:
            st = _load(DONE, {'convs': {}, 'consults': []}); st['tidied'] = today; _save(DONE, st)
        _log(f'tidy: {n} merged, {w} rewritten, {e} ended')
        return f'{n} merged, {w} rewritten, {e} ended'
    except Exception as ex:
        _log(f'tidy failed: {ex}'[:300]); return 'failed'
    finally:
        _run.release()


def review_due():
    """The weekly review (tools/jarvis_review.py): when the newest one is 7 days old (or there is none), in the daytime."""
    d = os.path.join(VOICE, 'reviews')
    last = max((f[:10] for f in os.listdir(d) if f.endswith('.md')), default='') if os.path.isdir(d) else ''
    if 9 <= dt.datetime.now().hour < 21 and (not last or (dt.date.today() - dt.date.fromisoformat(last)).days >= 7):
        try:
            subprocess.run(['python3', str(paths.home() / 'tools/jarvis_review.py')], capture_output=True, timeout=900)
            _log('weekly review written')
        except Exception as e:
            _log(f'review failed: {e}'[:300])


def start():
    """The server's background loop: every 10 minutes consolidate what has gone quiet; tidy once a day; review once a week."""
    def loop():
        time.sleep(60)
        while True:
            try:
                from jarvis.spine import governor              # Phase 4: learning keeps to 5% of a typical day (it waits, never lost)
                typ = governor.typical_day()
                if typ and governor.spent_today('learning') >= governor.CAPS['learning'] * typ:
                    time.sleep(600)
                    continue
            except Exception as e:
                _log(f'governor: {e}'[:200])
            try:
                consolidate()
                tidy()
                review_due()
                from jarvis import learning                         # Phase 5: notes per person, style edits, recall index, brain/ export
                learning.tick()
            except Exception as e:
                _log(f'loop: {e}'[:300])
            time.sleep(600)
    threading.Thread(target=loop, daemon=True, name='jarvis-consolidate').start()


# ------------------------------------------------------------------ recall (on the answer path: local and fast)
WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
# words that say WHEN or that any question has: with a day named, the rest of his words must still match the episode
TIME_WORDS = set(WEEKDAYS) | {'today', 'yesterday', 'morning', 'afternoon', 'evening', 'night', 'week', 'last', 'earlier', 'day', 'days',
                              'ago', 'before', 'kal', 'aaj', 'happen', 'happened', 'find', 'found', 'we', 'us', 'talk', 'talked', 'discus',
                              'discussed', 'did', 'done', 'work', 'worked', 'remember', 'again', 'time', 'thing', 'thing'}


def when_range(q, today=None):
    """(first day, last day) their words point at, or None: today, yesterday, day before yesterday, this morning, last week,
    this week, on <weekday>, N days ago."""
    t = (today or dt.date.today())
    s = q.lower()
    if re.search(r'\bday before yesterday\b', s):
        d = t - dt.timedelta(days=2); return d, d
    if re.search(r'\byesterday\b|\blast night\b|\bkal\b', s):
        d = t - dt.timedelta(days=1); return d, d
    if re.search(r'\b(today|this morning|earlier today|this afternoon|aaj)\b', s):
        return t, t
    m = re.search(r'\b(\d{1,2}|two|three|four|five|six) days ago\b', s)
    if m:
        n = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}.get(m.group(1)) or int(m.group(1))
        d = t - dt.timedelta(days=n); return d, d
    if re.search(r'\blast week\b', s):
        start = t - dt.timedelta(days=t.weekday() + 7); return start, start + dt.timedelta(days=6)
    if re.search(r'\bthis week\b', s):
        return t - dt.timedelta(days=t.weekday()), t
    m = re.search(r'\b(?:on|last) (' + '|'.join(WEEKDAYS) + r')\b', s)
    if m:
        back = (t.weekday() - WEEKDAYS.index(m.group(1))) % 7 or 7
        d = t - dt.timedelta(days=back); return d, d
    return None


# a plain command ("open my inbox", "make it louder") needs no memory, unless he points back ("like last time", "again")
COMMAND = re.compile(r"^\s*(?:(?:please|ok|okay|jarvis|hey jarvis)[,\s]+)*(?:open|show|go to|close|make|turn|set|switch|stop|start|play|pause|"
                     r"mute|unmute|louder|quieter|cancel|send|post|approve|decline|tick|reveal|remind)\b", re.I)
BACK = re.compile(r"\b(again|last time|like before|earlier|yesterday|remember|we did|we talked|you said|previous)\b", re.I)


def _bm25(qt, docs, k1=1.2, b=0.75):
    n = len(docs)
    if not n or not qt:
        return [0.0] * n
    avg = sum(len(d) for d in docs) / n or 1
    df = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    out = []
    for d in docs:
        tf, s = {}, 0.0
        for w in d:
            tf[w] = tf.get(w, 0) + 1
        for w in set(qt):
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(d) / avg))
        out.append(s)
    return out


def recall_block(q, cid, today=None):
    """Earlier moments that fit their words: consolidated episodes first, then turns of other conversations not consolidated yet."""
    qt = _toks(q)
    rng = when_range(q, today)
    if not qt and not rng:
        return ''
    topic = [w for w in qt if w not in TIME_WORDS]
    if COMMAND.match(q) and not BACK.search(q):
        return ''
    eps = episodes()
    picked = []
    if eps:
        docs = [_toks(' '.join([e.get('title', ''), e.get('summary', ''), ' '.join(e.get('keys') or []), ' '.join(e.get('people') or []),
                                ' '.join(e.get('topics') or [])])) for e in eps]
        sc = _bm25(topic or qt, docs)
        df = {}
        for d in docs:
            for w in set(d):
                df[w] = df.get(w, 0) + 1
        rare = max(2, len(docs) // 5)            # in at most a fifth of the episodes
        today = today or dt.date.today()
        ranked = []
        for e, s, doc in zip(eps, sc, docs):
            m = set(topic) & set(doc)
            if not rng and len(m) < 2 and not any(df[w] <= rare for w in m):   # two of his words, or one rare one (a name, an id)
                continue
            try:
                d = dt.date.fromisoformat(str(e.get('at', ''))[:10])
            except ValueError:
                d = None
            if rng:
                if not d or not (rng[0] <= d <= rng[1]):
                    continue
                if topic and s < 1.0:                     # "the Orion rows yesterday": yesterday's episodes about Orion rows only
                    continue
                s += 2.0                                  # the day he named
            if d:
                s *= 1 + 0.25 * math.exp(-(today - d).days / 7)     # a little recency
            if s >= (1.0 if rng else 2.5) or len(m) >= 3:          # (3 of his words always count: a small store has low scores)
                ranked.append((s, e))
        ranked.sort(key=lambda x: (-x[0], x[1].get('at', '')))
        picked = [e for _, e in ranked[:3]]
    done = _load(DONE, {'convs': {}}).get('convs', {})
    raw = []
    if os.path.isdir(CONV_DIR):
        qs = set(topic or qt)
        for f in sorted(os.listdir(CONV_DIR), reverse=True)[:60]:
            if not f.endswith('.json') or f.startswith(cid or '\0'):
                continue
            mark = done.get(f[:-5], '')
            for t in (_load(os.path.join(CONV_DIR, f), {}).get('turns') or []):
                if str(t.get('at') or '') <= mark:
                    continue
                if rng:
                    try:
                        if not (rng[0] <= dt.date.fromisoformat(str(t.get('at', ''))[:10]) <= rng[1]):
                            continue
                    except ValueError:
                        continue
                s = len(qs & set(_toks((t.get('q') or '') + ' ' + (t.get('speak') or ''))))
                if s >= (1 if rng else 2):
                    raw.append((s, str(t.get('at', '')), t))
        raw.sort(key=lambda h: (h[0], h[1]), reverse=True)
    lines = [f"- {e.get('at', '')[:16].replace('T', ' ')}: {e.get('title', '')}. {_clean(e.get('summary'), 320)}"
             + (f" (still open: {e['open']})" if e.get('open') else '') for e in picked]
    lines += [f"- {h[1][:16].replace('T', ' ')}: they asked \"{_clean(h[2].get('q'), 140)}\"; you said \"{_clean(h[2].get('speak'), 160)}\""
              for h in raw[:max(0, 3 - len(picked))]]
    if not lines:
        return ''
    return 'EARLIER (from your memory of past conversations; refer back naturally, e.g. "like yesterday..."):\n' + '\n'.join(lines) + '\n\n'


# a fact that something is still pending (not sent, drafted, waiting, not replied): true only until the inbox moves on.
# A fact that something WAS sent stays true and is left alone.
INBOX_STATE = re.compile(r'\b(drafted|unsent|not (?:yet )?(?:been )?(?:sent|replied|confirmed|recreated|answered)|no file was sent|has not replied|'
                         r'waiting on|still (?:open|pending|to send|unconfirmed))\b', re.I)
NOT_NAMES = {'owner', 'jarvis', 'claude'}


def _known_at(f):
    """When the fact was true: the newest conversation it came from (ids are YYYYMMDD-HHMMSS), else when it was stored."""
    ids = re.findall(r'(20\d{6})-(\d{6})', str(f.get('src') or ''))
    if ids:
        d, t = max(ids)
        return f'{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}'
    return str(f.get('at') or f.get('since') or '')[:19]


def _inbox_now():
    """[(first names, who, state text, last change, file went as a link)] for the last 14 days of inbox items (jarvis/mcp.py)."""
    from jarvis import mcp as wm
    out = []
    for rid, aid, a, w, ticks, d in wm._items():
        s = wm._state(rid, aid, a, w, ticks, set())
        rep = w.get('reply') or {}
        last = max(str(rep.get('sent_at') or '')[:19], str(w.get('updated') or '')[:19])
        who = str(a.get('who') or '')
        names = {x.lower() for x in re.findall(r'[A-Za-z]{3,}', who)[:1]} - NOT_NAMES
        topic = set(re.findall(r'[a-z0-9§.]{3,}', (str(a.get('title') or '') + ' ' + str(a.get('text') or '')[:400]).lower()))
        out.append((names, who, s['state'], last, any(x.get('went_out') for x in s['attachments']), str(a.get('title') or '')[:60], topic))
    return out


def _stale_note(f, inbox):
    """A state fact about a reply, a draft or a send goes stale the moment the inbox moves on ("the Excel has not
    been sent" said hours after it went). Such a fact gets what the inbox says NOW; the inbox is the truth. None = drop
    it: every item of the person it names has moved on since and none is open."""
    if f.get('kind') != 'state' or not INBOX_STATE.search(f['text']):
        return ''
    t = _known_at(f)
    words = set(re.findall(r'[a-z0-9§.]{3,}', f['text'].lower()))
    named = [i for i in inbox if i[0] & words]
    later = [i for i in (named or inbox) if i[3] > t]
    if not later:
        return ''
    if named and all(i[2] == 'done' or i[2].startswith('reply sent') for i in named):
        return None                    # all of that person's items have moved on and none is open: the fact is history
    if named:
        stop = {'owner', 'the', 'and', 'for', 'with', 'that', 'his', 'from', 'asked', 'reply', 'sent', 'not', 'yet', 'has', 'oct', 'sep'}
        best = max(len((i[6] - stop) & (words - stop) - i[0]) for i in later)
        hit = [i for i in later if best >= 2 and len((i[6] - stop) & (words - stop) - i[0]) == best]
        if hit:
            i = max(hit, key=lambda i: i[3])
            return (f' [noted {t[8:10]}/{t[5:7]} {t[11:16]}; OUT OF DATE, the inbox now says: {i[1]}\'s "{i[5]}" is {i[2]}'
                    + (', and the file went out as a link in it' if i[4] else '') + ']')
    return f' [noted {t[8:10]}/{t[5:7]} {t[11:16]}; may be out of date (the inbox has changed since): check inbox_find before saying it]'


def profile_block(limit=4500):
    """The facts that hold now, grouped by kind. Static between consolidations, so the brain is given it once per process."""
    fs = facts()
    if not fs:
        return ''
    try:
        inbox = _inbox_now()
    except Exception as e:
        print('profile inbox:', e, flush=True); inbox = []
    names = {'person': 'People', 'project': 'Projects', 'state': 'Current state', 'preference': 'Their preferences', 'term': 'Their terms'}
    out, size = [], 0
    for k in ('state', 'project', 'person', 'preference', 'term'):
        grp = sorted((f for f in fs if f['kind'] == k), key=lambda f: f.get('at', ''), reverse=True)
        if not grp:
            continue
        lines = []
        for f in grp:
            note = _stale_note(f, inbox) if inbox else ''
            if note is None:                 # e.g. "the reply is still waiting on your two decisions", hours after it was sent
                continue
            line = '- ' + f['text'] + note
            if size + len(line) > limit:
                break
            lines.append(line); size += len(line) + 1
        if lines:
            out.append(names[k] + ':\n' + '\n'.join(lines))
    return ('WHAT YOU KNOW ABOUT THEIR WORK (learned from past conversations; dated facts can be out of date, check before acting on them):\n'
            + '\n'.join(out) + '\n\n') if out else ''


if __name__ == '__main__':
    import sys
    if '--consolidate' in sys.argv:
        print(consolidate(dt.datetime.now() + dt.timedelta(minutes=QUIET_MIN) if '--now' in sys.argv else None))
    if '--tidy' in sys.argv:
        print(tidy(force=True))
    if '--recall' in sys.argv:
        print(recall_block(sys.argv[sys.argv.index('--recall') + 1], ''))
    if '--profile' in sys.argv:
        print(profile_block())
