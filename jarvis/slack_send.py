"""Slack replies: one path, in code (The owner, 5 Oct 2026: "clean this ... it shouldn't be this difficult"; "make sure nothing is
sent twice, nothing is missed and the draft is cleared; Jarvis should make sure of this").

  draft   After a worker writes a Slack reply (work entry `reply`), sync_drafts() puts it in their Slack Drafts with one tool
          call and keeps the draft id Slack returns. Workers never touch Slack themselves.
  send    Their Send press (or their voice yes after the read-back) -> start(). One send per reply at a time; a reply that is
          sent never goes again; an unclear earlier try is checked in Slack before another is allowed. Files: Excel/CSV go
          as a line pointing into the shared folder, a write-up as one Slack canvas (made once, reused on a retry). The message is posted with
          the draft id, so Slack deletes the draft as it sends. The message link is read from Slack's own reply, in code.
  record  reply.sent_at / sent_link / sent_by 'Jarvis', the item's points ticked; on failure the error and nothing ticked.
If they send from Slack themselves, world/sentcheck.py notices and marks it sent; Slack consumes the draft.

Each Slack call is one short Claude run whose only tool is that call. The worker hook (tools/worker_hook.py, lane "send")
lets it through only if the tool and its arguments equal the ticket written here just before.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, hashlib, json, os, re, subprocess, threading, time

HUB = str(paths.home())
CLAUDE = (paths.claude_bin() or 'claude')
MODEL = config.model('mid')
SETTINGS = os.path.join(HUB, 'state', 'worker-settings.json')        # carries the worker hook
TICKETS = os.path.join(HUB, 'state', 'send')
SLACK = 'mcp__claude_ai_Slack__'
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000')
STUCK_S = 600                     # a "sending" older than this (server restarted mid-send) is checked, never re-sent blindly
_locks, _guard = {}, threading.Lock()
HOOKS = {}                        # set by server.py: tick(rid, keys, done), share_files(att, text)


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def _wpath(rid):
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', rid):
        raise ValueError('bad item id')
    return os.path.join(HUB, 'work', rid + '.json')


def _edit(rid, aid, fn):
    """Read-modify-write one work entry (temp + rename). fn(entry) changes it in place."""
    f = _wpath(rid)
    doc = json.load(open(f))
    w = doc.get(aid)
    if not isinstance(w, dict):
        raise ValueError('no such item')
    fn(w)
    json.dump(doc, open(f + '.tmp', 'w'), indent=1, ensure_ascii=False)
    os.replace(f + '.tmp', f)
    return w


def _entry(rid, aid):
    w = json.load(open(_wpath(rid))).get(aid)
    if not isinstance(w, dict):
        raise ValueError('no such item')
    return w


def _event(kind, ref, data):
    try:
        from jarvis.spine import ledger
        ledger.event(kind, ref, 'slack_send', data)
    except Exception:
        pass


class _Unclear(Exception):
    """The call may have gone through but its outcome is not known: check in Slack, never send again blindly."""


class _NotReady(RuntimeError):
    """The run never reached Slack (no Slack call was made, the hook let nothing through): nothing went out, so one retry is safe.
    13 Oct 2026: three presses of Send failed as 'the call was not made' while the Slack connector was not ready in the run."""


# ------------------------------------------------------------------ one Slack call
def call(tool, args, timeout=240, retry=True):
    """Runs exactly one Slack connector call and returns (result_dict, raw_text). Raises on any failure.
    The model only relays the call; what Slack answered is read from the tool result itself.
    If the run never reached Slack (connector not ready), it waits and tries once more: nothing went out the first time."""
    try:
        return _call(tool, args, timeout)
    except _NotReady as e:
        if not retry:
            raise
        _event('slack_call_retry', tool, {'why': str(e)[:300]})
        time.sleep(20)
        return _call(tool, args, timeout)


def _call(tool, args, timeout=240):
    os.makedirs(TICKETS, exist_ok=True)
    tid = hashlib.sha256(json.dumps([tool, args], sort_keys=True).encode()).hexdigest()[:16]
    tpath = os.path.join(TICKETS, tid + '.json')
    json.dump({'tool': tool, 'args': args, 'at': now()}, open(tpath, 'w'), ensure_ascii=False)
    prompt = (f"Call the tool {SLACK}{tool} exactly once, with exactly the arguments in the JSON below: every argument, every "
              "character unchanged, nothing added. Do not call any other tool except ToolSearch to load it. Then reply DONE.\n"
              "<arguments>\n" + json.dumps(args, ensure_ascii=False) + "\n</arguments>")
    cmd = [CLAUDE, '-p', prompt, '--model', MODEL, '--effort', 'low', '--output-format', 'stream-json', '--verbose', '--max-turns', '4',
           '--tools', '', '--allowedTools', SLACK + tool, 'ToolSearch', '--permission-mode', 'default', '--settings', SETTINGS]
    env = dict(MCP_ENV, JARVIS_PROC='1', JARVIS_LANE='send', JARVIS_SEND_TICKET=tpath)
    used = False
    try:
        p = subprocess.run(cmd, cwd=HUB, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise _Unclear('no answer from Slack in time')
    finally:
        try:
            used = bool(json.load(open(tpath)).get('used'))        # the hook marks the ticket when it lets the call through
            os.remove(tpath)
        except (OSError, ValueError):
            pass
    try:                                             # the last run of each tool, for reading a failure afterwards
        open(os.path.join(TICKETS, f'last-{tool}.jsonl'), 'w').write(p.stdout or '')
    except OSError:
        pass
    uses, results, slack_status = {}, [], None
    for line in (p.stdout or '').splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get('type') == 'system' and o.get('subtype') == 'init':
            slack_status = next((str(x.get('status')) for x in o.get('mcp_servers') or [] if 'Slack' in str(x.get('name'))), 'missing')
        for c in ((o.get('message') or {}).get('content') or []) if isinstance(o.get('message'), dict) else []:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_use' and str(c.get('name', '')).endswith('__' + tool):
                uses[c.get('id')] = c.get('input') or {}
            elif c.get('type') == 'tool_result' and c.get('tool_use_id') in uses:
                body = c.get('content')
                text = body if isinstance(body, str) else ''.join(x.get('text', '') for x in body or [] if isinstance(x, dict))
                results.append((uses[c['tool_use_id']], text, bool(c.get('is_error'))))
    exact = [(t, e) for a, t, e in results if a == args]
    done = [t for t, e in exact if not e]
    if not done:
        why = next((t for t, e in exact if e), '')
        if used and not why:
            raise _Unclear(f'{tool} was let through but its answer was not seen')   # may have gone out: check, never resend
        if not why and not used and not uses:
            raise _NotReady(f'{tool}: the Slack connector was not ready in the send run ({slack_status or "no start-up record"}); '
                            'nothing was sent')
        if not why and uses:                         # a call with other arguments was refused by the hook: say so
            why = 'refused: the call did not carry exactly the given arguments; ' + next((t for a, t, e in results if e), '')[:200]
        raise RuntimeError(f'{tool}: {(why or p.stderr.strip() or "the call was not made")[:300]}')
    text = done[0]                                   # the hook allows one call per ticket, so there is at most one
    try:
        res = json.loads(text)
    except ValueError:
        res = {'text': text}
    if isinstance(res, dict) and (res.get('error') or res.get('ok') is False):
        raise RuntimeError(str(res.get('error') if isinstance(res, dict) else text)[:300] or text[:300])
    return (res if isinstance(res, dict) else {'value': res}), text


# ------------------------------------------------------------------ reading a conversation (code decides, not a model)
def me():
    """The owner's own Slack user id (settings: sources.slack_user_id, found by Setup). '' until it is known: then no
    message is ever taken for one of theirs."""
    from jarvis import config
    return str(config.load().get('sources.slack_user_id') or '').strip()


def workspace():
    """The owner's Slack address, e.g. https://team.slack.com (settings: sources.slack_workspace). '' until known."""
    from jarvis import config
    w = str(config.load().get('sources.slack_workspace') or '').strip().rstrip('/')
    return w if re.fullmatch(r'https://[a-z0-9-]+\.slack\.com', w) else ''


MSG = re.compile(r'From: [^\n]*?\((U[A-Z0-9]+)\)\s*\nTime: [^\n]*\nMessage TS: (\d+\.\d+)\s*\n(.*?)(?=\n-{3} Reply \d|\n={3} |\n-{3} Message \d|\Z)', re.S)


MSG_DM = re.compile(r'=== Message from [^\n]*?\((U[A-Z0-9]+)\)[^\n]*===[ \t]*\nMessage TS: (\d+\.\d+)\s*\n(.*?)(?=\n=== Message from |\Z)', re.S)


def read(channel_id, thread_ts='', oldest=''):
    """(messages, complete): every message in the thread (or the latest 50 in a DM/channel) as {user, ts, text}, parsed
    in code from Slack's own answer. complete = Slack said there are no more."""
    if thread_ts:
        tool, args = 'slack_read_thread', {'channel_id': channel_id, 'message_ts': thread_ts, 'limit': 1000}
    else:
        tool, args = 'slack_read_channel', {'channel_id': channel_id, 'limit': 50}
    if oldest:
        args['oldest'] = oldest
    res, raw = call(tool, args)
    body = res.get('messages') if isinstance(res.get('messages'), str) else raw
    seen, msgs = set(), []
    for rx in (MSG, MSG_DM):
        for m in rx.finditer(body):
            if m.group(2) not in seen:
                seen.add(m.group(2))
                msgs.append({'user': m.group(1), 'ts': m.group(2), 'text': m.group(3).strip()})
    msgs.sort(key=lambda m: float(m['ts']))
    if not msgs and 'Message TS:' in body:
        raise RuntimeError('could not read the messages in Slack\'s answer')
    return msgs, 'no more' in str(res.get('pagination_info') or '').lower()


def item_since(rid):
    """Unix time of the round-up an item came from: no reply to it can be older."""
    m = re.match(r'(\d{4})-(\d\d)-(\d\d)-(\d\d)(\d\d)', rid)
    try:
        return dt.datetime(*map(int, m.groups())).astimezone().timestamp() if m else 0.0
    except ValueError:
        return 0.0


TAIL = re.compile(r'^\s*(Attached:|File:).*$', re.M)


def _body(t):
    """Letters and digits of a message without its Attached:/File: link lines (Slack rewrites links and formatting)."""
    return _sig(TAIL.sub('', str(t or '')), cap=None)


def same_text(a, b):
    """True when two messages are the same reply: equal, or at least 92% alike for a long one (Slack's formatting,
    a small edit before sending). Replies under 40 letters must be identical."""
    import difflib
    x, y = _body(a), _body(b)
    if not x or not y:
        return False
    if len(x) < 40:
        return x == y
    return x == y or difflib.SequenceMatcher(None, x, y, autojunk=False).ratio() >= 0.92


def find_mine(msgs, text, since, skip=()):
    """Their message after `since` that is this reply (see same_text), or None. `skip`: message ts already recorded as
    another item's reply, never counted twice."""
    for m in msgs:
        if m['user'] == me() and float(m['ts']) > since and m['ts'] not in skip and same_text(text, m['text']):
            return m
    return None


def taken_ts(except_ref=''):
    """Message ts already recorded as sent for some item."""
    import glob
    out = set()
    for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
        rid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        for aid, w in d.items():
            ts = ((w or {}).get('reply') or {}).get('sent_ts') if isinstance(w, dict) else None
            if ts and f'{rid}|{aid}' != except_ref:
                out.add(str(ts))
    return out


def link_for(r, ts):
    host = (re.match(r'^https://[a-z0-9-]+\.slack\.com', str(r.get('slack_link') or '')) or [''])[0] or workspace()
    if not host:
        return ''                                         # the workspace is not known yet: no link rather than a wrong one
    tt = str(r.get('thread_ts') or '')
    return f"{host}/archives/{r['channel_id']}/p{ts.replace('.', '')}" + (f"?thread_ts={tt}&cid={r['channel_id']}" if re.fullmatch(r'\d+\.\d+', tt) else '')


# ------------------------------------------------------------------ drafts
def _conversation_drafts(channel_id, thread_ts, except_ref=''):
    """Other items whose Slack draft sits in this same conversation (DM/channel, or thread). 2 Nov: Slack keeps ONE draft per
    conversation, so a second item's draft replaced the first one's text and came back without an id (two leftovers)."""
    import glob
    tt = str(thread_ts or '') if re.fullmatch(r'\d+\.\d+', str(thread_ts or '')) else ''
    out = []
    for f in sorted(glob.glob(os.path.join(HUB, 'work', '20*.json')))[-80:]:
        rid = os.path.basename(f)[:-5]
        try:
            doc = json.load(open(f))
        except ValueError:
            continue
        for aid, w in doc.items():
            r = (w or {}).get('reply') if isinstance(w, dict) else None
            if not r or f'{rid}|{aid}' == except_ref or r.get('channel_id') != channel_id or r.get('slack_draft_dismissed'):
                continue
            rt = str(r.get('thread_ts') or '') if re.fullmatch(r'\d+\.\d+', str(r.get('thread_ts') or '')) else ''
            if rt != tt or r.get('slack_draft') not in ('created', 'exists'):
                continue
            unsent = bool(str(r.get('text') or '').strip()) and not r.get('sent_at') and not r.get('dropped') and not w.get('merged_into') \
                and not w.get('replied_by_owner') and w.get('status') != 'done'
            out.append({'rid': rid, 'aid': aid, 'id': r.get('slack_draft_id') or '', 'unsent': unsent})
    return out


_held_keys = set()


def _proven(rid, aid, w):
    """Proof before words (The owner, 10 Nov: "draft only after" the numbers are checked; a Slack draft cannot be edited). False
    while a number in the reply is tied to no output file or receipt, or a "same method" sentence has no ticked conditions
    (jarvis/proof.py). The reply stays in Jarvis with the reason; the draft is made once the worker has tied it. A fault in
    the check never holds a draft: it is logged and the draft goes ahead as before."""
    try:
        from jarvis import proof
        if proof.mode() != 'enforce':
            return True
        v = proof.verdict(rid, aid, w)
        bad = [u for u in v.get('untied') or [] if u.get('where') == 'reply']
        if not bad and not v.get('scope'):
            return True
        k = f"{rid}|{aid}|{v.get('key')}"
        if k not in _held_keys:                          # once per version of the text
            _held_keys.add(k)
            _event('proof_held', f'{rid}|{aid}', {'untied': [u['raw'] for u in bad][:12], 'scope': (v.get('scope') or [])[:3],
                                                 'claims': v.get('claims'), 'tied': v.get('tied')})
        return False
    except Exception as e:
        _event('proof_error', f'{rid}|{aid}', {'error': str(e)[:200]})
        return True


def needs_draft(w, rid='', aid=''):
    """A Slack reply waiting to go whose Slack draft is missing, or older than the text in Jarvis (a redo rewrote it).
    A second draft call in the same conversation replaces the draft there (seen on every repeat call, 30 Sep-5 Oct).
    Never for an item that is closed, merged, dropped or answered by them."""
    r = (w or {}).get('reply') or {}
    if not (r.get('channel') == 'slack' and str(r.get('text') or '').strip() and re.fullmatch(r'[CDG][A-Z0-9]{6,}', str(r.get('channel_id') or ''))
            and not r.get('sent_at') and not (w or {}).get('merged_into') and not r.get('dropped') and (w or {}).get('status') != 'done'
            and not (w or {}).get('replied_by_owner')
            and (r.get('send') or {}).get('state') not in ('sending', 'unclear', 'sent') and _recent(w)):
        return False
    if rid and aid and all_ticked(rid, aid):
        return False
    if rid and aid and not _proven(rid, aid, w):
        return False                                     # 12 Nov: no Slack draft while a number in the reply is unproven
    st = r.get('slack_draft')
    if not st:
        # one draft per conversation: wait while another unsent reply holds it (this one stays in Jarvis until that goes)
        return not any(h['unsent'] for h in _conversation_drafts(r['channel_id'], r.get('thread_ts'), f'{rid}|{aid}'))
    if st == 'created':
        return _norm_full(r.get('slack_draft_text')) != _norm_full(r.get('text'))
    if st in ('exists', 'failed', 'unclear') and int(r.get('slack_draft_tries') or 0) < 3:     # retry, gently
        try:
            return time.time() - dt.datetime.fromisoformat(r.get('slack_draft_at')).timestamp() > 1800
        except (TypeError, ValueError):
            return True
    return False


def all_ticked(rid, aid):
    """Every own point of the item is ticked on the page (they closed it)."""
    try:
        st = (json.load(open(os.path.join(HUB, 'roundups', 'state.json'))) or {}).get(rid) or {}
        a = next(x for x in json.load(open(os.path.join(HUB, 'roundups', rid + '.json'))).get('actions', []) if x.get('id') == aid)
    except (OSError, ValueError, StopIteration):
        return False
    pts = a.get('points') or []
    keys = [f'{aid}.{i}' for i, p in enumerate(pts) if not (isinstance(p, dict) and p.get('waiting'))] if pts else [aid]
    return bool(keys) and all(st.get(k) for k in keys)


def _norm_full(t):
    return re.sub(r'\s+', ' ', str(t or '')).strip()


def _recent(w, days=3):
    try:
        return time.time() - dt.datetime.fromisoformat(w.get('updated')).timestamp() < days * 86400
    except (TypeError, ValueError):
        return False


def _ledger_last(kind, ref):
    try:
        from jarvis.spine import ledger
        r = ledger.q('SELECT data FROM events WHERE kind=? AND ref=? ORDER BY id DESC LIMIT 1', (kind, ref))
        return json.loads(r[0]['data']) if r else None
    except Exception:
        return None


def sent_in_ledger(ref):
    """The last 'slack_sent' record for this reply, unless a later 'reply_unsent' (its message was deleted) undid it."""
    try:
        from jarvis.spine import ledger
        r = ledger.q("SELECT kind, data FROM events WHERE ref=? AND kind IN ('slack_sent', 'reply_unsent') ORDER BY id DESC LIMIT 1", (ref,))
        return json.loads(r[0]['data']) if r and r[0]['kind'] == 'slack_sent' else None
    except Exception:
        return None


def make_draft(rid, aid):
    """Their Slack draft for this reply. Files are linked in it (shared-folder line, canvas line), so sending it from Slack is
    complete too. Records the draft id Slack returns (used to delete it on send)."""
    ref = f'{rid}|{aid}'
    w = _entry(rid, aid)
    if not needs_draft(w, rid, aid):
        return {'skipped': True}
    r = w['reply']
    if not r.get('slack_draft'):
        old = _ledger_last('slack_draft_created', ref)               # a worker's whole-file write dropped our record
        if old is not None:
            _edit(rid, aid, lambda w: w['reply'].update(slack_draft='created', slack_draft_id=old.get('draft_id') or '', slack_draft_text=''))
    try:
        att = [a for a in (r.get('attachments') or []) if isinstance(a, dict) and a.get('path')]
        text = _files(rid, aid, att, r['text'])                     # made once: a canvas link is reused, a File line kept
        if _norm_full(text) != _norm_full(r['text']):
            _edit(rid, aid, lambda w: w['reply'].update(text=text))  # Jarvis shows exactly what the Slack draft holds
        args = {'channel_id': r['channel_id'], 'message': text}
        if re.fullmatch(r'\d{6,}\.\d+', str(r.get('thread_ts') or '')):
            args['thread_ts'] = r['thread_ts']
        res, raw = call('slack_send_message_draft', args)
    except Exception as e:
        cur = _entry(rid, aid)['reply']
        state = 'unclear' if isinstance(e, _Unclear) else 'exists' if 'draft_already_exists' in str(e) else 'failed'
        if cur.get('slack_draft') == 'created':
            state = 'created'                                        # keep the old draft's record; retried later
        _edit(rid, aid, lambda w: w['reply'].update(slack_draft=state, slack_draft_error=str(e)[:200], slack_draft_at=now(),
                                                    slack_draft_tries=int(w['reply'].get('slack_draft_tries') or 0) + 1))
        _event('slack_draft_' + state, ref, {'error': str(e)[:200]})
        return {'ok': False, 'state': state}
    did = res.get('draft_id') or (re.search(r'"draft_id"\s*:\s*"([^"]+)"', raw) or [None, None])[1]
    others = _conversation_drafts(r['channel_id'], r.get('thread_ts'), ref)
    # a replaced draft keeps its id (Slack returns none then): ours, or the one it replaced in this conversation
    did = did or _entry(rid, aid)['reply'].get('slack_draft_id') or next((o['id'] for o in others if o['id']), '')
    for o in others:                                  # their leftover text is gone from Slack now: nothing for him to delete
        if not o['unsent']:
            _edit(o['rid'], o['aid'], lambda w: w['reply'].update(slack_draft='replaced', slack_draft_replaced_by=ref))
    _edit(rid, aid, lambda w: w['reply'].update(slack_draft='created', slack_draft_id=did, slack_draft_text=args['message'],
                                                slack_draft_at=now(), slack_link=res.get('channel_link') or w['reply'].get('slack_link', '')))
    _event('slack_draft_created', ref, {'draft_id': did or ''})
    return {'ok': True, 'draft_id': did}


def sync_drafts():
    """World job: every new Slack reply gets its Slack draft. Zero model calls when there is nothing new."""
    import glob
    made = []
    for f in sorted(glob.glob(os.path.join(HUB, 'work', '20*.json')))[-40:]:
        rid = os.path.basename(f)[:-5]
        from jarvis.world.desk import is_desk
        if is_desk(rid):
            continue                                                 # a nudge Jarvis drafted by itself waits for the brief
        try:
            doc = json.load(open(f))
        except ValueError:
            continue
        for aid, w in doc.items():
            if isinstance(w, dict) and not aid.startswith('_') and needs_draft(w, rid, aid) and _quiet(w):
                try:
                    made.append({f'{rid}|{aid}': make_draft(rid, aid)})
                except Exception as e:                               # one item never blocks the others
                    made.append({f'{rid}|{aid}': {'error': str(e)[:150]}})
    return {'made': made}


def _quiet(w):
    """Only after the worker is done with the entry (not touched for 2 minutes)."""
    try:
        return time.time() - dt.datetime.fromisoformat(w.get('updated')).timestamp() > 120
    except (TypeError, ValueError):
        return True


# ------------------------------------------------------------------ send
def _lock(key):
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def status(w):
    """'sent' | 'sending' | 'unclear' | 'failed' | 'ready' | 'none' for a work entry."""
    r = (w or {}).get('reply') or {}
    s = r.get('send') or {}
    if r.get('sent_at'):
        return 'sent'
    st = s.get('state')
    if st == 'sending':
        try:
            if time.time() - dt.datetime.fromisoformat(s.get('at')).timestamp() > STUCK_S:
                return 'unclear'
        except (TypeError, ValueError):
            return 'unclear'
        return 'sending'
    if st in ('unclear', 'failed'):
        return st
    return 'ready' if str(r.get('text') or '').strip() else 'none'


def start(rid, aid, text, voice=False, by=None):
    """Their press. Returns at once; the send runs on its own thread. Refuses a second send of the same reply."""
    key = f'{rid}|{aid}'
    lk = _lock(key)
    if not lk.acquire(blocking=False):
        return {'ok': True, 'state': 'sending', 'already': 'sending'}
    handed, check = False, False
    try:
        w = _entry(rid, aid)
        r = w.get('reply') or {}
        st = status(w)
        if st == 'sent':
            return {'ok': False, 'state': 'sent', 'error': 'That reply was already sent.', 'link': r.get('sent_link', '')}
        old = sent_in_ledger(key)                                    # a worker's file write may have dropped sent_at
        if old is not None:
            _edit(rid, aid, lambda w: w['reply'].update(sent_at=w['reply'].get('sent_at') or now(), sent_link=old.get('link', ''), sent_by='Jarvis'))
            return {'ok': False, 'state': 'sent', 'error': 'That reply was already sent.', 'link': old.get('link', '')}
        if w.get('merged_into') or r.get('dropped'):
            return {'ok': False, 'error': f"This reply was merged into {w.get('merged_into') or 'another item'}: send that one."}
        if st == 'unclear':
            check = True
            return {'ok': False, 'state': 'unclear', 'error': 'Checking in Slack whether the last try went out. If it did not, Send works again in a minute.'}
        if r.get('channel') != 'slack' or not re.fullmatch(r'[CDG][A-Z0-9]{6,}', str(r.get('channel_id') or '')):
            return {'ok': False, 'error': 'That reply has no Slack conversation.'}
        text = str(text or r.get('text') or '').strip()
        if not text:
            return {'ok': False, 'error': 'There is no text to send.'}
        where = conversations(rid, aid)
        if where and r['channel_id'] not in where:
            return {'ok': False, 'error': 'This reply points at a different Slack conversation than the message it answers, so it was not sent. Open the item and check where it should go.'}
        _edit(rid, aid, lambda w: w['reply'].update(send={'state': 'sending', 'at': now(), 'by': by or ('voice' if voice else 'press')}))
        threading.Thread(target=_run, args=(rid, aid, text, lk), daemon=True).start()
        handed = True
        return {'ok': True, 'state': 'sending'}
    finally:
        if not handed:
            lk.release()
        if check:
            threading.Thread(target=verify, args=(rid, aid), daemon=True).start()


def conversations(rid, aid):
    """Slack channels this item (and any item linked to it as the same ask) came from. Empty = unknown (Jarvis's own items)."""
    out = set()
    refs = {f'{rid}|{aid}'}
    try:
        for g in (json.load(open(os.path.join(HUB, 'groups.json'))) or {}).get('groups', []):
            members = {g['lead'].split('.')[0], *[m.split('.')[0] for m in g.get('members', [])]}
            if f'{rid}|{aid}' in members:
                refs |= members
    except (OSError, ValueError, KeyError):
        pass
    for ref in refs:
        r_, a_ = ref.split('|', 1)
        try:
            a = next(x for x in json.load(open(os.path.join(HUB, 'roundups', r_ + '.json'))).get('actions', []) if x.get('id') == a_)
        except (OSError, ValueError, StopIteration):
            continue
        if re.fullmatch(r'[CDG][A-Z0-9]{6,}', str(a.get('channel_id') or '')):
            out.add(a['channel_id'])
    return out


def _run(rid, aid, text, lk):
    key = f'{rid}|{aid}'
    posted, check = False, False
    try:
        r = _entry(rid, aid)['reply']
        att = [a for a in (r.get('attachments') or []) if isinstance(a, dict) and a.get('path')]
        text = _files(rid, aid, att, text)
        args = {'channel_id': r['channel_id'], 'message': text}
        if re.fullmatch(r'\d{6,}\.\d+', str(r.get('thread_ts') or '')):
            args['thread_ts'] = r['thread_ts']
        if r.get('slack_draft') == 'created' and r.get('slack_draft_id'):
            args['draft_id'] = r['slack_draft_id']                  # Slack deletes the draft as it sends
        _edit(rid, aid, lambda w: w['reply']['send'].update(text=text, args_hash=hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]))
        # Is it already there (sent from Slack, or an earlier try that went out)? Then record it and post nothing.
        try:
            msgs, _ = read(r['channel_id'], args.get('thread_ts', ''))
        except Exception as e:
            raise RuntimeError(f'could not check Slack before sending ({str(e)[:120]}); nothing was sent')
        skip = taken_ts(key)
        mine = find_mine(msgs, text, item_since(rid), skip) or find_mine(msgs, r.get('text') or '', item_since(rid), skip)
        if mine:
            _sent(rid, aid, link_for(r, mine['ts']), mine['ts'], mine['text'], draft_cleared=False, by='already in Slack')
            return
        res, raw = call('slack_send_message', args)
        posted = True
        link = res.get('message_link') or res.get('permalink') or (re.search(r'https://[a-z0-9-]+\.slack\.com/archives/[A-Z0-9]+/p\d{16}\S*', raw) or [''])[0]
        ts = res.get('ts') or res.get('message_ts') or ''
        if not ts and link:
            m = re.search(r'/p(\d{10})(\d{6})', link)
            ts = f'{m.group(1)}.{m.group(2)}' if m else ''
        _sent(rid, aid, link.rstrip('",)'), ts, text, draft_cleared=bool(args.get('draft_id')))
    except _Unclear as e:
        _edit(rid, aid, lambda w: w['reply']['send'].update(state='unclear', error=str(e), ended=now()))
        _event('slack_send_unclear', key, {'error': str(e)})
        check = True
    except Exception as e:
        state = 'unclear' if posted else 'failed'      # after a post went out, never offer a blind retry
        _edit(rid, aid, lambda w: w['reply']['send'].update(state=state, error=str(e)[:300], ended=now()))
        _event('slack_send_' + state, key, {'error': str(e)[:300]})
        check = posted
    finally:
        lk.release()
        if check:                                       # only now: verify() takes the same lock
            threading.Thread(target=verify, args=(rid, aid), daemon=True).start()


_LINK_LINE = re.compile(r'^(File|Attached):.*$', re.M)


def _comparable(t):
    t = _LINK_LINE.sub('', str(t or ''))
    t = re.sub(r'<@[A-Z0-9]+(\|[^>]*)?>', '', t)
    return re.sub(r'\s+', ' ', t).strip()


_TAIL_ADD = re.compile(r'(?:^|(?<=\n)|(?<=[.!?)]))[ \t]*(?:Attached:|File:|Files:|Reactions:|[*_]*Sent using[*_]*)[^\n]{0,600}\Z')


def plain(t):
    """A reply as its words only, without what Slack or the sending adds: the trailing Attached:/File:/Files:/Reactions:
    lines and the connector's "Sent using" footer (Slack's reader may join them to the last sentence), mentions, link
    wrappers, HTML entities, curly quotes, bold and italic marks, bullets. Two texts with the same plain() are the same
    reply word for word. 12 Nov: the old comparison held the whole draft against the first 200 characters of what was
    sent, so 12 replies that went out exactly as drafted were recorded as edited."""
    import html
    t = html.unescape(str(t or '')).rstrip()
    while True:
        u = _TAIL_ADD.sub('', t).rstrip()
        if u == t:
            break
        t = u
    t = re.sub(r'<@[A-Z0-9]+(\|[^>]*)?>', '', t)
    lab = lambda m: '' if '.' in m.group(1) and ' ' not in m.group(1).strip() else m.group(1)   # a label that is the link itself, shortened
    t = re.sub(r'<https?://[^|>]+\|([^>]*)>', lab, t)
    t = re.sub(r'\[([^\]]*)\]\(https?://[^)]+\)', lab, t)
    t = re.sub(r'<?https?://[^\s>]+>?', '', t)
    t = t.replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
    return re.sub(r'[^a-z0-9]+', ' ', t.lower()).strip()


def word_likeness(a, b):
    import difflib
    return difflib.SequenceMatcher(None, plain(a).split(), plain(b).split(), autojunk=False).ratio()


def _edited(rid, aid, draft, sent, by, r):
    """Phase 5: they changed the draft before it went (in Jarvis's dialog or in Slack): both texts kept as a signal; the
    learning job turns these into a note per person (how they write to them)."""
    a, b = _comparable(draft), _comparable(sent)
    if not a or not b or len(b) < 20 or plain(draft) == plain(sent):      # the same words: not an edit (see plain())
        return
    ratio = word_likeness(draft, sent)
    try:
        from jarvis import learned
        learned.signal('draft_edited', 'owner_said', f'{rid}|{aid}', to=r.get('to'), channel=r.get('channel') or 'slack',
                       where=r.get('where'), by=by, ratio=round(ratio, 3), draft=a[:6000], sent=b[:6000], full=True)
    except Exception:
        pass


def _sent(rid, aid, link, ts, text, draft_cleared, by='Jarvis'):
    def upd(w):
        r = w['reply']
        _edited(rid, aid, r.get('text'), text, by, r)
        # the verdict is fixed now, against the draft as it stands at sending: reply.text may be rewritten later
        r.update(sent_at=now(), sent_by=by, sent_link=link, sent_ts=ts, sent_text=text[:200], sent_full=text[:8000],
                 sent_same=plain(r.get('text')) == plain(text), sent_like=round(word_likeness(r.get('text'), text), 3))
        r['send'] = {**(r.get('send') or {}), 'state': 'sent', 'ended': now(), 'link': link}
        if draft_cleared:
            r['slack_draft'] = 'sent'                                 # Slack deleted it with the send
        elif by == 'The owner (from Slack)' and r.get('slack_draft') == 'created':
            r['slack_draft'] = 'consumed'                             # he sent the draft itself
        w['status'] = 'done'
    _edit(rid, aid, upd)
    _tick(rid, aid)
    _event('slack_sent', f'{rid}|{aid}', {'link': link, 'draft_cleared': draft_cleared})


OPEN_STATES = ('queued', 'working', 'decide', 'needs_you', 'you')   # work still owed after the reply: not closed by it


def _tick(rid, aid):
    """The item's own points are done once its reply is out (same keys the page uses)."""
    try:
        rr = json.load(open(os.path.join(HUB, 'roundups', rid + '.json')))
        a = next(x for x in rr.get('actions', []) if x.get('id') == aid)
        pts = a.get('points') or []
        states = {p.get('key'): p.get('state') for p in (_entry(rid, aid).get('points') or []) if isinstance(p, dict)}
        keys = [f'{aid}.{i}' for i, p in enumerate(pts) if not (isinstance(p, dict) and p.get('waiting'))
                and states.get(f'{aid}.{i}') not in OPEN_STATES] if pts else [aid]
        if keys:
            HOOKS['tick'](rid, keys, True)
    except Exception as e:
        _event('slack_tick_error', f'{rid}|{aid}', {'error': str(e)[:200]})


ALLOWED_EXT = ('.md', '.xlsx', '.csv', '.docx', '.pdf', '.pptx')


def _files(rid, aid, att, text):
    """Excel/CSV -> a line pointing into the shared folder; a write-up (.md, not inline) -> one canvas, made once and reused. Only files in
    Jarvis's outputs folder, of an allowed type and size; anything else stops the send (nothing goes out)."""
    outs = os.path.realpath(os.path.join(HUB, 'work', 'outputs'))
    for a in att:
        f = os.path.realpath(os.path.expanduser(str(a.get('path') or '')))
        ext = os.path.splitext(f)[1].lower()
        cap = 40_000 if ext == '.md' else 5_000_000
        if not f.startswith(outs + os.sep) or ext not in ALLOWED_EXT or not os.path.isfile(f) or os.path.getsize(f) > cap:
            raise RuntimeError(f"the attachment {os.path.basename(f) or '?'} is not a Jarvis output file of an allowed type and size; nothing was sent")
    # 'shared' dropped so the link line is always (re)added; share_files reuses the same shared file, never a "(2)"
    att2, text = HOOKS['share_files']([{k: v for k, v in a.items() if k != 'shared'} | {'path': os.path.expanduser(a['path'])} for a in att], text)
    for a in att2:
        p = a['path']
        if a.get('inline') or a.get('shared') or not p.lower().endswith('.md'):
            continue
        url = a.get('canvas_url')
        if not url:
            content = open(p).read()
            res, raw = call('slack_create_canvas', {'title': str(a.get('title') or os.path.basename(p))[:120], 'content': content})
            url = res.get('canvas_url') or res.get('url') or (re.search(r'https://[a-z0-9-]+\.slack\.com/docs/[A-Z0-9]+/[A-Z0-9]+', raw) or [''])[0]
            if not url:
                raise RuntimeError('the canvas was made but Slack gave no link')
            a['canvas_url'] = url
            def keep_url(w, path=a['path'], url=url):
                for b in w['reply'].get('attachments') or []:
                    if os.path.expanduser(str(b.get('path') or '')) == path:
                        b['canvas_url'] = url
            _edit(rid, aid, keep_url)
        line = f"Attached: {a.get('title') or os.path.basename(p)} {url}"
        if url not in text:
            text = text.rstrip() + '\n' + line
    def keep(w):
        w['reply']['attachments'] = [{k: v for k, v in a.items()} for a in att2] or w['reply'].get('attachments') or []
    _edit(rid, aid, keep)                                            # canvas links kept, so a retry never makes a second one
    return text


# ------------------------------------------------------------------ check an unclear try
def _sig(t, cap=60):
    t = re.sub(r'<@[A-Z0-9]+(\|[^>]*)?>', '', str(t or ''), flags=re.I)      # "<@U..>" and "<@U..|Name>" alike
    t = re.sub(r'<(https?://[^|>]+)\|([^>]*)>', r'\2', t)                     # Slack's <url|label> links -> label
    t = re.sub(r'\[([^\]]*)\]\((https?://[^)]+)\)', r'\1', t)                # Markdown [label](url) -> label
    t = re.sub(r'https?://\S+', '', t)                                         # bare links: Slack may rewrite them
    t = re.sub(r'[^a-z0-9]', '', t.lower())
    return t[:cap] if cap else t


def verify(rid, aid):
    """Did an unclear try go out? Reads the conversation and matches in code. Found -> sent. Read complete and not found
    -> failed (their next press may send). Could not read -> stays unclear."""
    lk = _lock(f'{rid}|{aid}')
    if not lk.acquire(blocking=False):
        return {'busy': True}
    try:
        w = _entry(rid, aid)
        r, s = w.get('reply') or {}, (w.get('reply') or {}).get('send') or {}
        if r.get('sent_at'):
            return {'sent': True}
        msgs, complete = read(r['channel_id'], r.get('thread_ts') if re.fullmatch(r'\d+\.\d+', str(r.get('thread_ts') or '')) else '')
        mine = find_mine(msgs, s.get('text') or r.get('text') or '', item_since(rid))
        if mine:
            _sent(rid, aid, link_for(r, mine['ts']), mine['ts'], mine['text'], draft_cleared=False)
            return {'sent': True, 'ts': mine['ts']}
        if not complete and r.get('thread_ts'):
            raise RuntimeError('Slack gave only part of the thread')
        _edit(rid, aid, lambda w: w['reply']['send'].update(state='failed', error='It did not go out (checked in Slack). Press Send to try again.', checked=now()))
        _event('slack_send_checked', f'{rid}|{aid}', {'found': False, 'seen': len(msgs)})
        return {'sent': False}
    except Exception as e:
        _edit(rid, aid, lambda w: w['reply'].setdefault('send', {}).update(state='unclear', error='Could not check Slack yet: ' + str(e)[:150]))
        return {'error': str(e)[:150]}
    finally:
        lk.release()


# ------------------------------------------------------------------ leftover Slack drafts (he deletes them)
def leftovers():
    """Slack drafts that are still in their Drafts but should not be sent: their reply went out without deleting them, or the
    item was merged or dropped. The connector cannot delete a draft without posting, so the page lists them for them."""
    import glob
    out = []
    for f in sorted(glob.glob(os.path.join(HUB, 'work', '20*.json')))[-60:]:
        rid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        for aid, w in d.items():
            r = (w or {}).get('reply') if isinstance(w, dict) else None
            if not r or r.get('slack_draft') not in ('created', 'dropped', 'exists') or r.get('slack_draft_dismissed'):
                continue
            if r.get('sent_at') or w.get('merged_into') or r.get('dropped') or r.get('slack_draft') == 'dropped' \
                    or (w.get('replied_by_owner') and r.get('answered_elsewhere') and not str(r.get('text') or '').strip()):   # 1 Nov: he
                    # answered outside the draft's thread, so the draft is still in his Slack Drafts (answering where it sat clears it)
                tt = str(r.get('thread_ts') or '')
                host = (re.match(r'^https://[a-z0-9-]+\.slack\.com', str(r.get('slack_link') or '')) or [workspace()])[0]
                link = '' if not host else f"{host}/archives/{r.get('channel_id')}" + (f"/p{tt.replace('.', '')}?thread_ts={tt}&cid={r.get('channel_id')}" if re.fullmatch(r'\d+\.\d+', tt) else '')
                first = re.sub(r'<@[A-Z0-9]+(\|([^>]*))?>', lambda m: '@' + (m.group(2) or ''), str(r.get('slack_draft_text') or r.get('text') or ''))
                first = re.sub(r'[*_`]', '', re.sub(r'\s+', ' ', first)).strip()[:70]
                out.append({'rid': rid, 'aid': aid, 'to': r.get('to', ''), 'where': r.get('where', ''), 'link': link, 'start': first,
                            'why': 'already sent' if r.get('sent_at') else 'you answered it yourself' if w.get('replied_by_owner') and not r.get('dropped')
                            else r['dropped'] if isinstance(r.get('dropped'), str) else 'merged into another reply'})   # 1 Nov: a held draft says why
    return out


def leftover_done(rid, aid):
    _edit(rid, aid, lambda w: w['reply'].update(slack_draft_dismissed=now()))
    return {'ok': True, 'left': leftovers()}
