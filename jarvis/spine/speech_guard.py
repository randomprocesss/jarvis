"""No claim without a receipt (HARNESS.md §6.1, Phase 1, 5 Oct 2026).

The weekly review found Jarvis saying it had done things it never did (~4 of 40 sampled turns), despite a NEVER rule in the
prompt. Rules in a prompt are advice; this is code. Every spoken sentence passes Gate before it reaches the voice:
  - a sentence that claims a completed or started action ("I've sent it", "Noted", "On it", "I've told Claude") is held back
    until the reply's actions are known (they come after "speak" in the JSON), and everything after it waits too, so the order
    never changes;
  - at the end, each held claim is checked against the actions that will really run (validated actions the page runs, and the
    ones done here with a receipt: remember / forget / remind / ask_claude / run_now);
  - backed: spoken as is, except a send / post / approve claimed as already done, which becomes the present tense ("Sending
    your reply now."): those wait for their read-back and 3-second cancel, and the page says the real result;
  - not backed: replaced by an honest line ("I haven't sent anything yet.") or dropped when the rest of the answer stands alone.
Every hit is logged to voice/claim_guard.ndjson (a learning signal and the Phase 1 metric). Claims about other people or about
the state of things ("the run passed") are the grounding rule, which comes in Phase 3 and only logs until it is measured.
"""
from jarvis import paths
import datetime as dt, json, os, re

LOG = str(paths.home() / 'voice/claim_guard.ndjson')
I = r"(?:\bi(?:'ve| have)?\s+(?:just\s+|already\s+|now\s+)?)"          # "I've", "I have", "I", "I've just"

# class -> (pattern, actions that back it). Patterns match completion/start claims in the first person or bare ("Sent.").
CLAIMS = {
    'send': (re.compile(I + r"(?:sent|posted|replied|emailed|messaged|forwarded)\b|^(?:okay,?\s+|ok,?\s+|right,?\s+)?(?:sent|posted)\b", re.I),
             {'send_reply', 'post_jira'}),
    'approve': (re.compile(I + r"(?:approved|declined|rejected)\b|^(?:approved|declined)\b", re.I), {'approve', 'decline'}),
    'start': (re.compile(I + r"(?:started|kicked off|launched|queued|set (?:it|that|claude) (?:off|going|running))\b"
                         r"|\b(?:i'm on it|on it|running it now|starting (?:it|that|on it|on that) now|working on it now|getting claude (?:on|onto) it)\b", re.I),
              {'new_task', 'start_task', 'run_now', 'week_hours', 'ask_claude', 'running'}),   # running: a task already runs (TASKS NOW)
    'stop': (re.compile(I + r"(?:stopped|cancell?ed|paused|halted|killed)\b|\bstopping (?:it|that) now\b", re.I), {'stop_task'}),
    'remember': (re.compile(r"^(?:okay,?\s+|ok,?\s+|got it,?\s+)?noted\b|\bi(?:'ll| will) (?:remember|keep that in mind)\b|" + I +
                            r"(?:noted|saved|stored|remembered)\b", re.I), {'remember'}),
    'forget': (re.compile(I + r"(?:forgotten|forgot|removed (?:it|that) from (?:my )?memory)\b|\bi(?:'ll| will) forget\b", re.I), {'forget'}),
    'remind': (re.compile(r"\bi(?:'ll| will) remind you\b|" + I + r"set (?:a|the|your) reminder\b|\breminder(?:'s| is) set\b", re.I), {'remind'}),
    'tell': (re.compile(I + r"(?:told|asked|let|passed (?:it|that|this|your note) (?:on )?to)\s+claude\b|\bclaude(?:'s| has) got (?:it|your note)\b", re.I),
             {'tell_claude', 'ask_claude'}),
    'tick': (re.compile(I + r"(?:ticked|marked|checked off)\b|\bmarked (?:it|them|that|those) (?:as )?done\b", re.I), {'tick'}),
    'schedule': (re.compile(I + r"(?:scheduled|rescheduled|booked|moved (?:the|your) meeting)\b", re.I), set()),
    'switch': (re.compile(I + r"switched\b", re.I), {'set_model'}),
    'done': (re.compile(r"^(?:yeah,?\s+|okay,?\s+|ok,?\s+|all\s+)?done[.!]?$", re.I), None),
    # 5 Oct: "I'll create fresh drafts with files attached" while the only action was a read-only lookup
    'promise': (re.compile(r"\b(?:i(?:'ll| will| am going to|'m going to)|let me)\s+(?:now\s+|just\s+|go\s+|quickly\s+)?(?:re-?)?(?:create|make|draft|redraft|redo|fix|attach|"
                           r"build|prepare|write|update|rerun|re-run|finish|send|post)\b", re.I),
                {'new_task', 'start_task', 'tell_claude', 'send_reply', 'post_jira', 'run_now', 'week_hours'}),   # bare "Done." only; "it's done" is a status claim
}
# a backed send/post/approve claimed as already done becomes the present tense: the page reads it back and runs it
PRESENT = {'send_reply': 'Sending your reply now.', 'post_jira': 'Posting it now.', 'approve': 'Approving it now.', 'decline': 'Declining it now.'}
HONEST = {'send': "I haven't sent anything yet.", 'approve': "I haven't approved anything yet.",
          'start': "I haven't started that yet. Want me to?", 'stop': "Nothing's been stopped yet.",
          'remember': 'Okay.', 'forget': 'Okay.', 'remind': "I haven't set a reminder yet.",
          'tell': "I haven't passed that on yet.", 'tick': "I haven't marked anything yet.",
          'schedule': "I can't schedule that myself.", 'switch': "I haven't switched anything.", 'done': 'Okay.',
          'promise': "I haven't started that yet. Say the word and I'll start it as a task."}
QUESTION = re.compile(r"\?\s*$")
PROMISE_SPAN = re.compile(r"\b(?:i'll|i will|i am going to|i'm going to|let me)\b[^.?!]{0,70}?\b(?:re-?)?(?:create|make|draft|redraft|redo|fix|attach|"
                          r"build|prepare|write|update|rerun|re-run|finish|send|post)\b", re.I)
NEGATED = re.compile(r"\b(?:haven't|have not|didn't|did not|won't|can't|cannot|not yet|never|shall i|should i|want me to|do you want)\b", re.I)


CLAUSE = re.compile(r"\s*(?:[,;:]|\s[-\u2013\u2014]\s|\u2014|\u2013|\bbut\b|\band\b)\s*", re.I)


def claims(sentence):
    """The claim classes a sentence makes ([] for none), clause by clause: a question or a negated clause ("I haven't sent
    it", "shall I send it?") claims nothing, but "I've sent it, anything else?" still claims the send."""
    s = re.sub(r'<[^>]{1,20}>', ' ', sentence or '').replace('\u2019', "'").replace('\u2018', "'").strip()
    out = []
    parts = [x for x in CLAUSE.split(s) if x and x.strip()]
    for i, c in enumerate(parts):
        c = c.strip()
        last = i == len(parts) - 1
        if NEGATED.search(c) or (last and QUESTION.search(c)):
            continue
        for k, (rx, _) in CLAIMS.items():
            if k == 'promise':
                continue
            if k not in out and (rx.search(c) or (k == 'done' and rx.search(s))):
                out.append(k)
    # a promise can span clauses ("I'll check the messages and create fresh drafts"): look at the whole sentence
    if 'promise' not in out and not NEGATED.search(s) and not QUESTION.search(s) and PROMISE_SPAN.search(s):
        out.append('promise')
    if 'done' in out and not re.match(r"^(?:yeah,?\s+|okay,?\s+|ok,?\s+|all\s+)?done[.!]?$", s, re.I):
        out.remove('done')
    return out


def verdict(sentence, kinds, done):
    """(new_sentence or None to drop, why). `done` = set of action types that will really run (or ran, with a receipt)."""
    for k in kinds:
        backers = CLAIMS[k][1]
        if backers is None:                                   # "done": any real action backs it
            if not (done - {'open', 'reveal', 'pronounce'}):
                return HONEST['done'], 'done:unbacked'
            continue
        have = backers & done
        if not have:
            return HONEST[k], k + ':unbacked'
        if k in ('send', 'approve'):
            return PRESENT[sorted(have)[0]], k + ':present'
    return sentence, 'backed'


def log_hit(rec):
    try:
        with open(LOG, 'a') as f:
            f.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), **rec}, ensure_ascii=False) + '\n')
    except Exception:
        pass


class Gate:
    """Wraps the turn's emit(): sentences stream through until the first claim; from then on they wait for finish()."""

    def __init__(self, emit, conv=''):
        self.raw, self.conv = emit, conv
        self.held, self.passed = [], []       # held: (event, kinds)
        self.holding = False

    def emit(self, ev):
        if ev.get('type') != 'speak' or ev.get('filler') or not ev.get('text'):
            return self.raw(ev)
        kinds = claims(ev['text'])
        if kinds or self.holding:
            self.holding = True
            self.held.append((ev, kinds))
            return True                        # "still listening": the turn goes on
        self.passed.append(ev['text'])
        return self.raw(ev)

    def finish(self, actions, receipts=(), q=''):
        """Speaks what was held, checked against `actions` (validated, for the page to run) and `receipts` (action types done
        here). Returns the whole spoken answer as it was really said ('' if nothing streamed at all)."""
        done = {a.get('type') for a in actions or [] if isinstance(a, dict)} | set(receipts or ())
        out = list(self.passed)
        for i, (ev, kinds) in enumerate(self.held):
            text = ev['text']
            if kinds:
                new, why = verdict(text, kinds, done)
                if why != 'backed':
                    log_hit({'conv': self.conv, 'q': (q or '')[:200], 'said': text, 'kinds': kinds, 'actions': sorted(done), 'why': why,
                             'now': new})
                    try:                                  # Phase 5: a learning signal (a claim with nothing behind it)
                        from jarvis import learned
                        learned.signal('claim_guard', 'executed', self.conv, said=text[:300], why=why, kinds=list(kinds), q=(q or '')[:200])
                    except Exception:
                        pass
                    rest = [e['text'] for e, _ in self.held[i + 1:]] + out
                    if new == 'Okay.' and rest:          # a bare acknowledgement adds nothing when the rest stands alone
                        continue
                    if new is None:
                        continue
                    ev = dict(ev, text=new)
                    if new in out:                       # never the same honest line twice in one answer
                        continue
            out.append(ev['text'])
            self.raw(ev)
        self.held, self.holding = [], False
        return ' '.join(out).strip()


def guard_text(speak, actions, receipts=()):
    """The same check for a whole answer that was not streamed (the cold path, or a reply that arrived in one piece)."""
    sents = [s for s in re.split(r'(?<=[.!?…])\s+', (speak or '').strip()) if s]
    said = []
    g = Gate(lambda ev: said.append(ev['text']) or True)
    for s in sents:
        g.emit({'type': 'speak', 'text': s})
    g.finish(actions, receipts)
    return ' '.join(said).strip()


# ------------------------------------------------------------------ grounding v2 (Phase 3, 5 Oct 2026): LOG ONLY
# Claims about the state of things, not about Jarvis's own actions (those are CLAIMS above): "Priya replied", "it's sent",
# "the check finished", "Claude is working on it", "it's in your Slack drafts". Each is checked against what the turn
# actually looked at (its read tools) and, failing that, against Jarvis's own records. Nothing spoken changes yet: hits go
# to voice/grounding.ndjson so the false-alarm rate is measured before any gate (HARNESS.md Phase 3 proof: <= 2%).
GLOG = str(paths.home() / 'voice/grounding.ndjson')
_EXPAND = [(r"[’‘]", "'"), (r"\bit's\b", 'it is'), (r"\bthat's\b", 'that is'), (r"\bhasn't\b", 'has not'), (r"\bhaven't\b", 'have not'),
           (r"\bisn't\b", 'is not'), (r"\baren't\b", 'are not'), (r"\bwasn't\b", 'was not'), (r"\bdidn't\b", 'did not'),
           (r"\bwon't\b", 'will not'), (r"\b(\w+)'ll\b", r'\1 will'), (r"\bclaude's\b", 'claude is'),
           (r"\b(she|he|they|who)'s\s+(sent|asked|replied|written|answered|told)\b", r'\1 has \2'),       # she's sent = she has sent
           (r"\b(\w+)'s\s+(?=(?:still |already |now |all |just )*(?:posted|sent|done|running|ready|merged|pushed|finished|queued|"
            r"waiting|on |showing|clean|gone|in |out|pending|cooking|going)\b)", r'\1 is ')]                             # reply's posted = reply is posted
# Grounding v3 (5 Oct): measured on 91 real sentences (tools/grounding_eval.py). Any subject; by clause; contractions expanded.
NOT_TASK = re.compile(r"\b(model|voice|haiku|sonnet|opus|chatterbox|kokoro|locally|on your mac|whoever|who is running|the setup|this setup)\b")
STATE = {
    'sent': re.compile(r"\b(?:is|was|were|are|has been|have been|got|has|have|did)\s+(?:not\s+|already\s+|now\s+|just\s+|still\s+|yet\s+)*"
                       r"(?:sent|posted|delivered|gone out|go out|get posted|out)\b|\bwent (?:out|through)\b|\bshows? as sent\b|"
                       r"\b(?:comment|reply|message|excel|file)\s+is\s+on\s+(?:jira|slack)\b|\b(?:sent|posted) at (?:about |around )?\d"),
    'replied': re.compile(r"\b(?!i\b|you\b|we\b|been\b|being\b)([a-z]{3,})\s+(?:has\s+|already\s+|just\s+)*(?:replied|answered|responded|wrote back|got back to you)\b|"
                          r"\b(?:she|he|they)\s+has\s+(?:sent|replied|answered|written)\b|\byou (?:already )?told \w+\b|"
                          r"\bnothing new from (?:him|her|them|[a-z]{3,})\b|\bno (?:reply|answer|response) (?:yet )?from\b|"
                          r"\b(?:has|have) not (?:yet )?(?:replied|answered|got back)\b"),
    'running': re.compile(r"\b(?:is|are)\s+(?:still\s+|now\s+|already\s+)?(?:running|in progress|queued|going|cooking)\b|\bstill (?:running|going|cooking)\b|"
                          r"^\s*(?:already|still)\s+running\b|\bnothing (?:is )?(?:running|queued)\b|\bno (?:tasks?|jobs?|runs?|workers?) (?:are )?(?:running|queued)\b|"
                          r"\b(?:have|has) not finished\b|\bwork running\b|\bnothing (?:is )?cooking\b"),
    'finished': re.compile(r"\b(?:is|are|was|were|has been|have been|has|have)\s+(?:now\s+|all\s+|already\s+|since\s+)?(?:been\s+)?"
                           r"(?:done|finished|completed|merged|pushed|answered|failed)\b|"
                           r"\b(?:check|run|job|task|analysis|push|copy|merge|import|variants?|points?|items?)\s+(?:\w+\s+)?(?:finished|completed|done)\b|"
                           r"\b(?:merged|pushed) (?:and pushed )?(?:fine|ok)\b|\beverything (?:else )?is done\b"),
    'draft': re.compile(r"\b(?:is|are)\s+(?:already\s+|still\s+)?(?:written\s+)?in(?:to)? (?:your |the |his |her |their )?(?:slack )?drafts?\b|"
                        r"\bwritten into the draft\b|\bdrafts? (?:are |is )?(?:still )?waiting\b|\balready created\b"),
    'ready': re.compile(r"\b(?!you\b|i\b)(?:it|they|\w+)\s+(?:is|are)\s+(?:now\s+|all\s+)?ready\b(?! (?:to|for|when))|"
                        r"\b(?:points?|files?|drafts?|items?|reports?|sheets?)\s+ready\b"),
    'waiting': re.compile(r"\b(?:approvals?|items?|requests?)\s+(?:is\s+|are\s+)?(?:still\s+)?(?:waiting|pending)\b|\bis still pending\b|"
                          r"\b(?:reply|draft|answer|message)\s+is\s+still\s+waiting\b|\bstill waiting on your\b|\bno draft yet\b"),
}
READ_TOOL = re.compile(r"(inbox_find|inbox_item|tasks_now|needs_find|slack_(read|search)|getJira|searchJira|outlook_email_search|"
                       r"chat_message_search|^Read$|^Grep$|^Glob$|repos_)", re.I)
OWN = re.compile(r"^\s*(?:i|i've|i have|i'll|i will|i'm|i am|i'd|let me|let's|we'll|we will|marking|removing|starting|running it|done,? sir|"
                 r"done[,.!]?\s*$)\b", re.I)
IF_SOON = re.compile(r"\b(?:when|whenever|once|until|after|if|whether|unless|will|going to|should|could|would|might|can)\b")


def _norm(s):
    t = str(s or '').lower()
    for rx, rep in _EXPAND:
        t = re.sub(rx, rep, t)
    return t


def state_claims(sentence):
    """[(kind, polarity)] of the state claims in one spoken sentence, clause by clause. Not a claim: a question, Jarvis's
    own action ("I've sent it", "Marking it done": the CLAIMS receipts cover those), anything future or conditional
    ("it'll ping you when it's done", "once it is sent"), and a model or voice that is "running"."""
    s = _norm(sentence)
    if QUESTION.search(s):
        return []
    out = []
    for cl in re.split(r'\s*(?:[—–;:,]|\bbut\b|\bso\b)\s*', s):
        if not cl.strip() or OWN.match(cl):
            continue
        for k, rx in STATE.items():
            m = rx.search(cl)
            if not m or IF_SOON.search(cl[:m.start()]) or (k == 'running' and NOT_TASK.search(cl)):
                continue
            if k == 'ready' and re.search(r"\b(?:you are|you're|i am|when you|ready when)\b", cl):
                continue
            pol = 'neg' if re.search(r"\b(?:not|never|no|nothing|yet)\b", cl) and not re.search(r'\balready\b', cl) else 'pos'
            if (k, pol) not in out:
                out.append((k, pol))
    return out


def _records(kind, pol='pos'):
    """Does Jarvis's own state back a claim of this kind (and polarity) right now? 'running': a worker task or a Claude
    consult is running (pos) or nothing is (neg; 5 Oct: "nothing's running" was flagged although nothing was)."""
    import glob, time
    hub = str(paths.home())
    try:
        if kind in ('running', 'finished'):
            import sqlite3
            con = sqlite3.connect(os.path.join(hub, 'state', 'jarvis.db'), timeout=3)
            if kind == 'running':
                n = con.execute("SELECT count(*) FROM tasks WHERE state='running'").fetchone()[0]
                for f in glob.glob(os.path.join(hub, 'voice', 'consults', '*.json')):
                    if os.path.getmtime(f) > time.time() - 3600:
                        try:
                            n += json.load(open(f)).get('state') == 'running'
                        except (OSError, ValueError):
                            pass
                con.close()
                return n == 0 if pol == 'neg' else n > 0
            else:
                cut = (dt.datetime.now().astimezone() - dt.timedelta(hours=6)).isoformat()
                n = con.execute("SELECT count(*) FROM tasks WHERE state IN ('done','failed') AND ended>?", (cut,)).fetchone()[0]
            con.close()
            return n > 0
        if kind == 'waiting':                    # approvals (or Slack drafts) waiting for him
            try:
                ap = json.load(open(os.path.join(hub, 'approvals.json')))
                if any(isinstance(x, dict) and x.get('status') == 'pending' for x in ap.get('items') or []):
                    return True
            except (OSError, ValueError):
                pass
        cut = time.time() - 24 * 3600
        for f in glob.glob(os.path.join(hub, 'work', '20*.json')):
            if os.path.getmtime(f) < cut:
                continue
            for w in json.load(open(f)).values():
                r = (w or {}).get('reply') if isinstance(w, dict) else None
                if not r:
                    continue
                if kind == 'sent' and r.get('sent_at'):
                    return True
                if kind == 'draft' and r.get('slack_draft') == 'created' and not r.get('sent_at'):
                    return True
                if kind == 'replied' and (w.get('replied_by_owner') or r.get('owner_wrote_after')):
                    return True
                if kind in ('ready', 'waiting') and w.get('status') in ('done', 'drafted') and not r.get('sent_at'):
                    return True
    except Exception:
        return False
    return False


def ground_log(spoken, tools=(), conv='', q=''):
    """Log-only grounding of the turn's state claims. Returns the hits (for tests)."""
    looked = [t for t in tools or () if READ_TOOL.search(str(t))]
    hits = []
    for sent in re.split(r'(?<=[.!?])\s+', str(spoken or '')):
        for kind, pol in state_claims(sent):
            how = 'lookup' if looked else ('records' if _records(kind, pol) else 'none')
            hits.append({'kind': kind, 'polarity': pol, 'said': sent[:200], 'grounded': how != 'none', 'how': how})
    if hits:
        try:
            with open(GLOG, 'a') as f:
                f.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), 'conv': conv, 'q': (q or '')[:160],
                                    'tools': list(tools or ())[:12], 'hits': hits}, ensure_ascii=False) + '\n')
        except OSError:
            pass
    return hits
