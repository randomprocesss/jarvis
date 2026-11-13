"""Making Jarvis more human. Everything here is local files in the data home's voice folder.

 1. Learns from their reactions. Interruptions (they cut the answer off), plain feedback ("too long", "shut up", "that's wrong",
    "perfect") and re-asks (the same question again within 3 minutes) go to jarvis_feedback.jsonl. After plain feedback, or
    every 5 signals, a quick Haiku pass rewrites jarvis_style.md: at most 12 short rules the brain reads with every question.
 2. Memory. "Remember ..." -> jarvis_memory.json (the brain's `remember` / `forget` actions, done here at once). Each question
    carries what they asked to remember (the relevant part) and earlier moments that fit (jarvis_episodes.py: episodes and facts
    consolidated from past conversations).
    Reminders ("remind me at 4 to call Priya") -> reminders.json: a reminder on their phone (Reminders app, due at that time)
    and, when it is due, Jarvis says it at the next quiet moment (the page asks /api/vpn, which also returns what is due).
 3. Mood from their voice: how long and how loud they spoke, a laugh or a sigh (macOS's sound classifier), and the pitch and
    loudness of the turn, each against their own running averages (jarvis_voice.json), plus their words ("ugh", "come on",
    "asap"): a one-line hint on how to answer.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, json, os, re, subprocess, threading, time, uuid
from jarvis import episodes as episodes

VOICE = str(paths.home() / 'voice')
FEED, STYLE = os.path.join(VOICE, 'jarvis_feedback.jsonl'), os.path.join(VOICE, 'jarvis_style.md')
MEM, REM, VBASE = os.path.join(VOICE, 'jarvis_memory.json'), os.path.join(VOICE, 'reminders.json'), os.path.join(VOICE, 'jarvis_voice.json')
CORR, VOCAB = os.path.join(VOICE, 'corrections.jsonl'), os.path.join(VOICE, 'vocab.json')
CLAUDE, HAIKU = (paths.claude_bin() or 'claude'), config.model('quick')
_lock = threading.Lock()

NEG = re.compile(r"\b(too long|(be |keep it |make it )?short(er)?\b|stop talking|shut up|that'?s enough|you('re| are) (rambling|repeating)|you already said|"
                 r"that'?s (wrong|not (it|right|true|what i (asked|meant)))|no,? (that'?s|that is) not|not what i (asked|meant)|don'?t (call me|say|do that)|"
                 r"speak slower|slow down|too fast|too slow|robotic|stop saying|less (formal|chatty)|more (detail|briefly)|just answer)\b", re.I)
POS = re.compile(r"\b(perfect|great job|well done|nice one|exactly( right)?|that'?s (right|it|perfect|great)|love (it|that)|good answer|spot on|brilliant)\b", re.I)
# He corrects the last answer: "no, I meant the June one", "not that one", "I said Atlas". A plain "no" only counts when the last
# answer was not a question (then "no" is just his answer) and he says more than two words.
CORRECT = re.compile(r"\bi (?:meant|mean|said|was asking (?:about|for)|asked (?:about|for))\b|\bnot (?:that|this|the) one\b|"
                     r"\bwhat i (?:meant|said|asked) (?:was|is)\b|^\s*(?:no|nope|nah|nahi|wrong)\b[\s,.!]+(?:the|it'?s|it was|i want|do it)\b", re.I)
NO_LEAD = re.compile(r"^\s*(?:no|nope|nahi|nah)\b(?!\s+(?:idea|problem|worries|need|one|way|matter|thanks|thank|rush|hurry|issue|"
                     r"clue|luck|reason|longer|more|doubt|wonder|time)\b)[\s,.!]+\S+\s+\S+", re.I)
TERM = re.compile(r"^(?:[A-Z][A-Za-z0-9]{1,23}|[A-Za-z]*\d[A-Za-z0-9]*)$")      # a name or a code ("Priya", "PL04", "ABC-505" parts)
TENSE = re.compile(r"\b(ugh|come on|seriously|again\?|why (is|isn'?t|does|doesn'?t|won'?t) (this|it)|damn|wtf|asap|urgent(ly)?|hurry|quick(ly)?|right now|frustrat\w*|annoy\w*)\b", re.I)
STOP = set("a an the is are was were be been to of in on at for and or but with my your our me you i it this that what which who how do does did can could "
           "would should will please jarvis about from by as so if then than there here any some all just now yes no not get got have has had".split())


def _now():
    return dt.datetime.now().astimezone()


def _load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def _save(p, o):
    tmp = p + '.tmp'
    json.dump(o, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def _words(t):
    return [w for w in re.findall(r"[a-z0-9']+", (t or '').lower()) if w not in STOP and len(w) > 2]


# ------------------------------------------------------------------ 1. learning from his reactions
def note_feedback(q, body, c):
    """Record what this question says about the previous answer. Returns True when the style should be re-learned now."""
    last = c['turns'][-1] if c.get('turns') else None
    ev = []
    heard = body.get('heard')
    if last and isinstance(heard, str) and heard.strip():
        ev.append({'kind': 'interrupted', 'heard_words': len(heard.split()), 'said_words': len((last.get('speak') or '').split()),
                   'answer': (last.get('speak') or '')[:300]})
    corrected = bool(last) and (CORRECT.search(q) or (NO_LEAD.match(q) and not (last.get('speak') or '').rstrip().endswith('?')))
    if corrected:
        ev.append({'kind': 'corrected', 'his_words': q[:300], 'asked': (last.get('q') or '')[:200], 'answer': (last.get('speak') or '')[:300]})
        note_correction(q, last)
    elif NEG.search(q):
        ev.append({'kind': 'told_off', 'his_words': q[:300], 'answer': (last or {}).get('speak', '')[:300]})
    elif POS.search(q) and len(q.split()) <= 12:
        ev.append({'kind': 'praised', 'his_words': q[:200], 'answer': (last or {}).get('speak', '')[:300]})
    if last and time.time() - c.get('t_last', 0) < 180:
        a, b = set(_words(q)), set(_words(last.get('q')))
        if a and b and len(a & b) / len(a | b) >= 0.6:
            ev.append({'kind': 'reasked', 'his_words': q[:200], 'answer': (last.get('speak') or '')[:300]})
    c['t_last'] = time.time()
    if not ev:
        return False
    with _lock, open(FEED, 'a') as f:
        for e in ev:
            f.write(json.dumps({'at': _now().isoformat(timespec='seconds'), **e}, ensure_ascii=False) + '\n')
    try:
        from jarvis import learned
        for e in ev:
            learned.signal(e['kind'], 'owner_said', '', **{k: v for k, v in e.items() if k != 'kind'})
    except Exception:
        pass
    st = _load(VBASE, {})
    st['since_learn'] = st.get('since_learn', 0) + len(ev)
    _save(VBASE, st)
    return any(e['kind'] in ('told_off', 'praised', 'corrected') for e in ev) or st['since_learn'] >= 5


def note_correction(q, last):
    """A correction is kept (corrections.jsonl: the next questions see the latest few), and when they repeat their question with a
    name or code the recognizer missed ("I said PL04" after "pee el oh four"), that word goes into vocab.json for the
    recognizer to expect."""
    rec = {'at': _now().isoformat(timespec='seconds'), 'asked': (last.get('q') or '')[:200], 'answer': (last.get('speak') or '')[:200], 'his_words': q[:300]}
    try:
        with _lock, open(CORR, 'a') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except OSError:
        pass
    prev = set(w.lower() for w in re.findall(r"[A-Za-z0-9'-]+", last.get('q') or ''))
    toks = re.findall(r"[A-Za-z0-9-]+", q)
    new = [t for i, t in enumerate(toks) if i > 0 and TERM.match(t) and t.lower() not in prev and t.lower() not in STOP]
    if not new or not re.search(r"\bi (said|meant|asked)\b", q, re.I):
        return
    with _lock:
        d = _load(VOCAB, {})
        words = d.get('words') if isinstance(d.get('words'), list) else []
        have = {w.lower() for w in words}
        add = [t for t in new[:2] if t.lower() not in have]
        if add:
            d['words'] = words + add
            d['learned'] = (d.get('learned') or []) + [{'word': t, 'at': rec['at'], 'from': q[:120]} for t in add]
            _save(VOCAB, d)


def corrections_block():
    """Their latest corrections (7 days, at most 3), so the same misreading is not made again in a new conversation."""
    try:
        lines = open(CORR).read().splitlines()[-3:]
    except OSError:
        return ''
    cut = (_now() - dt.timedelta(days=7)).isoformat()
    out = []
    for l in lines:
        try:
            c = json.loads(l)
        except ValueError:
            continue
        if c.get('at', '') >= cut:
            out.append(f"- They asked \"{c['asked'][:120]}\", you answered \"{c['answer'][:100]}\", they corrected: \"{c['his_words'][:160]}\"")
    return ("RECENT CORRECTIONS (don't repeat these misreadings):\n" + '\n'.join(out) + '\n\n') if out else ''


def learn_style_async():
    threading.Thread(target=learn_style, daemon=True, name='jarvis-learn').start()


def learn_style():
    """Small edits to the voice rules from their latest reactions (Phase 5: deltas through app/learned.py, never a rewrite).
    The model proposes; code decides: each edit cites the signals behind it, and only their own reactions (or what they confirmed)
    can add, change or retire a rule. The weekly review's findings alone cannot."""
    from jarvis import learned
    sig = learned.q("SELECT * FROM learn_signals WHERE used=0 AND kind IN ('corrected','told_off','praised','reasked','interrupted',"
                    "'review','cancel_readback','label_bad','label_good','thats_wrong') ORDER BY id DESC LIMIT 40")
    if not sig:
        return
    rules = learned.active_rules(('voice', 'all'))
    prompt = ("You keep the short list of rules for Jarvis, a voice assistant, about how the owner likes it to talk. Below are the "
              "current rules (with ids) and new signals from their reactions (with ids): interrupted = they cut the answer off; "
              "told_off / praised = what they said about it; reasked = they had to ask again; corrected = they corrected how you read "
              "them; cancel_readback = they cancelled an action as it was read back; label_good / label_bad = their marks on past "
              "answers; review = a pattern the weekly review found. Propose SMALL edits only: add a rule the signals show, update "
              "one that needs a tweak, retire one they contradicted. At most 3 edits; often none. Each rule under 20 words, "
              "concrete. Never a rule that grants a permission or skips a check. FIXED, never contradict: what to call them is their own "
              "choice in settings; reply in their language. Reply with ONE JSON object: {\"edits\": [{\"op\": "
              "\"ADD|UPDATE|CLOSE\", \"id\": \"<rule id for UPDATE/CLOSE>\", \"text\": \"<rule>\", \"signals\": [<signal ids>]}]}\n\n"
              "CURRENT RULES:\n" + ('\n'.join(f"[{r['id']}] {r['text']}" for r in rules) or '(none)') + "\n\nSIGNALS:\n"
              + '\n'.join(f"[{x['id']}] {x['kind']}: {x['data'][:300]}" for x in reversed(sig)))
    try:
        p = subprocess.run([CLAUDE, '-p', prompt, '--model', HAIKU, '--output-format', 'text', '--strict-mcp-config',
                            '--mcp-config', '{"mcpServers":{}}', '--disallowedTools', 'Bash', 'Edit', 'Write', 'Read', 'Grep', 'Glob',
                            'WebFetch', 'WebSearch', 'Task', 'Agent', 'NotebookEdit'],
                           capture_output=True, text=True, timeout=90, cwd=VOICE)
        out = p.stdout or ''
        a, b = out.find('{'), out.rfind('}')
        props = (json.loads(out[a:b + 1]) if a >= 0 and b > a else {}).get('edits') or []
    except Exception:
        return
    by_id = {x['id']: x for x in sig}
    his = ('corrected', 'told_off', 'praised', 'reasked', 'interrupted', 'cancel_readback', 'label_bad', 'label_good', 'thats_wrong')
    edits = []
    for e in props[:3]:
        cited = [by_id[i] for i in (e.get('signals') or []) if i in by_id]
        # origin from what is cited, decided here: one of his own reactions -> owner_said; only review findings -> inferred
        origin = 'owner_said' if any(c['kind'] in his for c in cited) else ('inferred' if cited else None)
        if not origin:
            continue
        src = 'signals:' + ','.join(str(c['id']) for c in cited)
        if e.get('op') == 'CLOSE':               # dropping a rule is safe at once
            edits.append({'op': 'CLOSE', 'table': 'rules', 'id': e.get('id'), 'origin': origin, 'source_ref': src, 'why': 'they contradicted it'})
        else:                                    # a new or changed rule is STAGED: live only after the safety cases pass 3/3 with it
            edits.append({'op': 'ADD', 'table': 'rules', 'text': e.get('text'), 'scope': 'voice', 'origin': origin, 'source_ref': src,
                          'state': 'staged', 'why': (f"replaces:{e.get('id')} " if e.get('op') == 'UPDATE' and e.get('id') else '') + 'learned from their reactions'})
    res = learned.apply_edits(edits, run='style-' + _now().isoformat(timespec='minutes'))
    learned.x('UPDATE learn_signals SET used=1 WHERE id IN (%s)' % ','.join(str(x['id']) for x in sig))
    with _lock:
        st = _load(VBASE, {}); st['since_learn'] = 0; st['learned_at'] = _now().isoformat(timespec='seconds'); _save(VBASE, st)
    return res


def style_block():
    """The active voice rules (app/learned.py), each learned from their reactions and visible (and undoable) in Learned."""
    try:
        from jarvis import learned
        rules = learned.active_rules(('voice', 'all'))
        if os.environ.get('JARVIS_INCLUDE_STAGED') == '1':      # the autopilot's rule gate tests them before they go live
            rules += learned.q("SELECT * FROM rules WHERE state='staged' AND scope IN ('voice','all')")
    except Exception:
        rules = []
    s = '\n'.join('- ' + r['text'] for r in rules)
    return ('HOW THEY LIKE YOU TO TALK (learned from their reactions; follow these):\n' + s[:1400] + '\n\n') if s else ''


# ------------------------------------------------------------------ 2. memory, earlier conversations, reminders
def remember(text, said=''):
    """"Remember ..." -> memory (app/learned.py). On a turn where Jarvis read outside content, an instruction to remember must
    be their literal words (HARNESS §6.3, CaMeL): the text has to come from what they said this turn, or it is refused."""
    from jarvis import learned
    text = re.sub(r'\s+', ' ', str(text or '')).strip()[:400]
    if not text:
        return 'Nothing to remember.'
    mine = set(_words(said)) if said else None
    own = (mine is None) or (bool(set(_words(text))) and len(set(_words(text)) & mine) / max(1, len(set(_words(text)))) >= 0.5)
    res = learned.apply_edits([{'op': 'ADD', 'table': 'memory', 'text': text, 'origin': 'owner_said' if own else 'external',
                                'source_ref': 'voice', 'quote': (said or '')[:300]}], run='remember')
    ok, why = res[0][1], res[0][2]
    if ok:
        return 'Noted.'
    if 'already' in why:
        return 'I already have that.'
    if 'permission' in why or 'standing order' in why:
        return "I can't take that on as a standing rule: permissions only change in your settings, by you."
    return "I'll only remember what you say yourself."


def forget(text):
    """"Forget that" / "that's wrong": the best match among what they asked to remember, the facts and the voice rules is
    closed (kept, undoable in Learned)."""
    from jarvis import learned
    want = set(_words(text))
    cands = [('memory', m['id'], m['text']) for m in learned.memory_items()] + \
            [('facts', f['id'], f['text']) for f in learned.current_facts()] + \
            [('rules', r['id'], r['text']) for r in learned.active_rules(('voice', 'all'))]
    best = max(cands, key=lambda c: len(want & set(_words(c[2]))), default=None)
    if not best or len(want & set(_words(best[2]))) < max(1, min(2, len(want))):
        return "I couldn't find that one."
    res = learned.apply_edits([{'op': 'CLOSE', 'table': best[0], 'id': best[1], 'why': 'they said forget / wrong: ' + text[:80],
                                'origin': 'owner_said', 'source_ref': 'voice'}], run='forget')
    learned.signal('thats_wrong', 'owner_said', best[1], table=best[0], text=best[2][:200], his_words=text[:200])
    return 'Forgotten.' if res and res[0][1] else "I couldn't change that one."


def memory_block(q):
    try:
        from jarvis import learned
        items = [{'text': m['text'], 'at': m['created']} for m in learned.memory_items()]
    except Exception:
        items = []
    if not items:
        return ''
    if len(items) > 12:
        qw = set(_words(q))
        scored = sorted(items, key=lambda i: (len(qw & set(_words(i['text']))), i['at']), reverse=True)
        items = scored[:8] + [i for i in items[-3:] if i not in scored[:8]]
    return 'WHAT THEY ASKED YOU TO REMEMBER (use when relevant, never recite):\n' + '\n'.join('- ' + i['text'] for i in items) + '\n\n'


def _as_time(at):
    s = str(at or '').strip()
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.astimezone()
    except ValueError:
        pass
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        n = _now(); t = n.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return t if t > n else t + dt.timedelta(days=1)
    return None


def _phone(text, when):
    """A reminder in the Reminders app (list "Jarvis"), due then: iCloud puts it on their iPhone even if the Mac sleeps."""
    a = lambda s: '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'
    secs = max(0, int((when - _now()).total_seconds()))
    script = (f'tell application "Reminders"\n if not (exists list "Jarvis") then make new list with properties {{name:"Jarvis"}}\n'
              f' make new reminder at end of list "Jarvis" with properties {{name:{a(text[:120])}, remind me date:((current date) + {secs})}}\nend tell')
    try:
        return subprocess.run(['osascript', '-e', script], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def remind(text, at):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()[:200]
    when = _as_time(at)
    if not text or not when:
        return "I didn't catch when."
    if when < _now() - dt.timedelta(minutes=1):
        return "That time has already passed."
    with _lock:
        d = _load(REM, {'items': []})
        d['items'].append({'id': uuid.uuid4().hex[:8], 'text': text, 'at': when.isoformat(timespec='minutes'), 'done': False})
        d['items'] = [i for i in d['items'] if not i['done'] or i['at'] > (_now() - dt.timedelta(days=7)).isoformat()][-200:]
        _save(REM, d)
    ok = _phone(text, when)
    hm = when.strftime('%-I:%M %p').replace(':00 ', ' ').lower()
    day = '' if when.date() == _now().date() else (' tomorrow' if when.date() == (_now() + dt.timedelta(days=1)).date() else when.strftime(' on %a'))
    return f"I'll remind you at {hm}{day}" + ('' if ok else ' (on this Mac only: the phone reminder failed)') + '.'


def due():
    """Reminders whose time has come and that were not said yet."""
    n = _now().isoformat(timespec='minutes')
    return [i for i in _load(REM, {'items': []})['items'] if not i['done'] and i['at'] <= n]


def done(rid):
    with _lock:
        d = _load(REM, {'items': []})
        for i in d['items']:
            if i['id'] == rid:
                i['done'] = True
        _save(REM, d)


def apply(a, said=None):
    """remember / forget / remind, done here at once (local, their own data). Returns the line to confirm it. `said`: their words
    this turn (a remember must come from them)."""
    t = a.get('type')
    if t == 'remember':
        return remember(a.get('text'), said if said is not None else '')
    if t == 'forget':
        return forget(a.get('text'))
    if t == 'remind':
        return remind(a.get('text'), a.get('at'))
    return ''


# ------------------------------------------------------------------ 3. mood from his voice and words
def _z(st, k, x, a=0.1):
    """How far x is from their usual for k, in their own standard deviations (None until 10 turns are known); updates the usual."""
    m, v, n = st.get(k + '_m'), st.get(k + '_v'), st.get(k + '_n', 0)
    z = (x - m) / max(v ** 0.5, 1e-6) if m is not None and v and n >= 10 else None
    if m is None:
        st[k + '_m'], st[k + '_v'] = x, 0.0
    else:
        d = x - m
        st[k + '_m'], st[k + '_v'] = round(m + a * d, 4), round((1 - a) * (v + a * d * d), 5)
    st[k + '_n'] = n + 1
    return z


def mood_hint(q, voice):
    """A one-line hint on how to answer, or ''. voice (from the app) = {secs, rms, laugh?, sigh?, pros?: {f0, f0_st, db}}.
    Signals: their words ("ugh", "asap"); a laugh or sigh (macOS's sound classifier, Feel.swift; only strong ones count); their
    energy against their own normal (pitch, pitch spread, loudness, pace; jarvis_voice.json keeps the running averages).
    Energy is what a voice shows reliably; whether they are happy or annoyed mostly comes from their words."""
    voice = voice if isinstance(voice, dict) else {}
    hints, st = [], _load(VBASE, {})
    try:
        secs, rms, n = float(voice.get('secs') or 0), float(voice.get('rms') or 0), len(q.split())
        laugh, sigh = float(voice.get('laugh') or 0), float(voice.get('sigh') or 0)
    except (TypeError, ValueError):
        secs, rms, n, laugh, sigh = 0, 0, len(q.split()), 0, 0
    fast = loud = False
    zs = []
    if secs >= 1.2 and n >= 4:
        wps = n / secs
        b = st.get('wps')
        fast = bool(b and wps > b * 1.3)
        st['wps'] = round(wps if not b else b * 0.9 + wps * 0.1, 3)
        z = _z(st, 'pace', wps)
        if z is not None:
            zs.append(z)
    if rms > 0:
        b = st.get('rms')
        loud = bool(b and rms > b * 1.6)
        st['rms'] = round(rms if not b else b * 0.9 + rms * 0.1, 5)
    pros = voice.get('pros') if isinstance(voice.get('pros'), dict) else {}
    for k in ('f0', 'f0_st', 'db'):
        try:
            x = float(pros[k])
        except (KeyError, TypeError, ValueError):
            continue
        z = _z(st, k, x)
        if z is not None:
            zs.append(z)
    _save(VBASE, st)
    energy = sum(zs) / len(zs) if len(zs) >= 2 else 0.0
    tense = bool(TENSE.search(q))
    if laugh >= 0.6:
        hints.append('They laughed: share the moment lightly first (a <chuckle> or "Ha," fits), then answer.')
    if sigh >= 0.8 and not laugh >= 0.6:
        hints.append('They sighed: they may be tired or fed up. A short, gentle acknowledgement, then a brief answer.')
    if tense or (fast and loud):
        hints.append('They sound tense or in a hurry: a short human reaction first ("Ugh, again?"), then the answer in one calm sentence. No jokes. '
                     'Be MORE careful with facts, not softer: no reassurance you have not checked.')
    elif energy >= 1.2:
        hints.append('They sound keyed up (higher, livelier, louder or faster than their usual): match their energy, be quick and crisp.')
    elif energy <= -1.2:
        hints.append('They sound low (flatter, quieter or slower than their usual), maybe tired: calm, warm and short.')
    elif fast:
        hints.append('They are talking fast: keep it brisk, one sentence.')
    if secs >= 20:
        hints.append('They talked at length: acknowledge the main point in a few words before answering.')
    return ('HOW THEY SOUND RIGHT NOW: ' + ' '.join(hints) + '\n\n') if hints else ''


def before(q, body, c, cid):
    """What to put in front of their words this turn (and record what their words say about the last answer), as two parts:
    (static, dynamic). static = what they are like and what you know (profile, learned style, recent corrections): the same for
    many turns, so the brain gets it once per process and again only when it changes. dynamic = this turn: the time, what they
    asked you to remember that fits, earlier moments that fit their words, how they sound."""
    if note_feedback(q, body, c):
        learn_style_async()
    n = _now()
    static = episodes.profile_block() + style_block() + corrections_block()
    dynamic = (f"NOW: {n.strftime('%a %d %b %Y, %H:%M')} (their time; use it for reminders: \"at\" as YYYY-MM-DDTHH:MM)\n"
               + memory_block(q) + episodes.recall_block(q, cid) + mood_hint(q, body.get('voice')))
    return static, dynamic
