"""Did they send it themselves? (5 Oct 2026, the owner: "it creates a draft and I send it manually from Slack, it doesn't even get
updated in Jarvis, such basic things".)

pending(): open items whose drafted Slack reply is not marked sent. check(): reads each of those conversations
(app/slack_send.read: Slack's own answer, parsed in code) and marks a reply sent when their message there starts like the
draft; recheck_sent() reopens a sent reply only when a complete read no longer has its message. Triggered right after they
leave the Slack app (heartbeat watches the front app) and every 30 minutes while drafts wait. No model judges anything.
"""
from jarvis import config
from jarvis import paths
import datetime as dt, glob, json, os, re, subprocess, time
from jarvis.spine import ledger

HUB = str(paths.home())
CLAUDE = (paths.claude_bin() or 'claude')
HAIKU = config.model('quick')
MCP_ENV = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='20000', CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS='20000')
TOOLS = ['mcp__claude_ai_Slack__slack_read_thread', 'mcp__claude_ai_Slack__slack_read_channel', 'ToolSearch']
SCHEMA = {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'key': {'type': 'string'}, 'sent': {'type': 'boolean'}, 'ts': {'type': 'string'}, 'text': {'type': 'string'}},
    'required': ['key', 'sent']}}, 'failed': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['items']}


def front_app():
    """Name of the frontmost app (lsappinfo; no permission needed), or ''."""
    try:
        asn = subprocess.run(['lsappinfo', 'front'], capture_output=True, text=True, timeout=3).stdout.strip()
        o = subprocess.run(['lsappinfo', 'info', '-only', 'name', asn], capture_output=True, text=True, timeout=3).stdout
        import re
        m = re.search(r'^"([^"]+)"\s+ASN:', o.strip()) or re.search(r'"(?:LSDisplayName|name)"\s*=\s*"([^"]+)"', o)
        return m.group(1) if m else ''
    except Exception:
        return ''


def pending():
    out = []
    for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
        rid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for aid, w in d.items():
            if not isinstance(w, dict) or aid.startswith('_'):
                continue
            r = w.get('reply') or {}
            if not r.get('text') or r.get('sent_at') or w.get('replied_by_owner') or r.get('channel') != 'slack' or not r.get('channel_id') \
                    or (r.get('send') or {}).get('state') in ('sending', 'unclear'):      # Jarvis is sending it right now
                continue
            since = w.get('updated') or (rid[:10] + 'T00:00:00+05:30')
            out.append({'key': f'{rid}|{aid}', 'channel_id': r['channel_id'], 'thread_ts': r.get('thread_ts') or '', 'to': r.get('to') or '',
                        'since': since, 'draft': r['text'][:200]})
    return out


def check_unclear():
    """Sends whose outcome is unknown (or stuck 'sending' after a restart): read Slack again, in code."""
    from jarvis import slack_send
    done = []
    for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
        rid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for aid, w in d.items():
            if isinstance(w, dict) and not aid.startswith('_') and slack_send.status(w) == 'unclear':
                done.append({f'{rid}|{aid}': slack_send.verify(rid, aid)})
    return done


def check():
    """Did they send a drafted reply themselves? Reads each waiting conversation and decides in code (5 Oct: a model's yes/no
    reopened a reply that was still in Slack, which nearly let it go out twice). Their message whose start matches the
    draft (or the reply text) marks it sent; any other message from them there after the draft is noted, not taken as sent."""
    from jarvis import slack_send
    items = pending()
    res = {'pending': len(items), 'marked': [], 'noted': [], 'failed': []}
    for x in items:
        rid, aid = x['key'].split('|', 1)
        try:
            w = json.load(open(os.path.join(HUB, 'work', rid + '.json'))).get(aid) or {}
            r = w.get('reply') or {}
            tt = x['thread_ts'] if re.fullmatch(r'\d+\.\d+', x['thread_ts'] or '') else ''
            msgs, _ = slack_send.read(x['channel_id'], tt)
            since = slack_send.item_since(rid)
            if tt and not any(m['user'] == slack_send.me() and float(m['ts']) > since for m in msgs):
                # They may answer a thread question in the conversation itself (one point of a longer reply); reading only
                # the thread then keeps the item in To do for days. Nothing of theirs in the thread: read the conversation.
                main, _ = slack_send.read(x['channel_id'], '', oldest=str(since))
                msgs = sorted(msgs + [m for m in main if m['ts'] not in {y['ts'] for y in msgs}], key=lambda m: float(m['ts']))
            skip = slack_send.taken_ts(x['key'])
            mine = slack_send.find_mine(msgs, r.get('text') or '', since, skip)
            old = None if mine else (slack_send.find_mine(msgs, r.get('slack_draft_text') or '', since, skip)
                                     if r.get('slack_draft_text') and not slack_send.same_text(r.get('slack_draft_text'), r.get('text')) else None)
            if old:
                # he sent an OLDER Slack draft: that part is out, the newer text is not. Noted, the item stays open.
                slack_send._edit(rid, aid, lambda w, m=old: w['reply'].update(sent_old_draft={'ts': m['ts'], 'text': m['text'][:160]},
                                                                              slack_draft='consumed'))
                res['noted'].append(x['key'])
                continue
            if mine:
                slack_send._sent(rid, aid, slack_send.link_for(r, mine['ts']), mine['ts'], mine['text'], draft_cleared=False,
                                 by='The owner (from Slack)')
                res['marked'].append(x['key'])
                continue
            try:
                after = dt.datetime.fromisoformat(x['since']).timestamp()
            except ValueError:
                after = since
            other = [m for m in msgs if m['user'] == slack_send.me() and float(m['ts']) > after]
            if other and (r.get('owner_wrote_after') or {}).get('ts') != other[-1]['ts']:
                slack_send._edit(rid, aid, lambda w, m=other[-1]: w['reply'].update(owner_wrote_after={'ts': m['ts'], 'text': m['text'][:160]}))
                res['noted'].append(x['key'])
        except Exception as e:
            res['failed'].append(f"{x['key']}: {str(e)[:120]}")
    try:
        res['recheck'] = recheck_sent()
    except Exception as e:
        res['recheck'] = {'error': str(e)[:120]}
    try:
        res['unclear'] = check_unclear()
    except Exception as e:
        res['unclear'] = {'error': str(e)[:120]}
    ledger.event('sentcheck', '', 'sentcheck', res)
    return res


def _untick(rid, aid):
    from jarvis import slack_send
    try:
        rr = json.load(open(os.path.join(HUB, 'roundups', rid + '.json')))
        a = next(x for x in rr.get('actions', []) if x.get('id') == aid)
        pts = a.get('points') or []
        keys = [f'{aid}.{i}' for i, p in enumerate(pts) if not (isinstance(p, dict) and p.get('waiting'))] if pts else [aid]
        slack_send.HOOKS['tick'](rid, keys, False)
    except Exception as e:
        ledger.event('sentcheck_error', f'{rid}|{aid}', {'error': 'untick: ' + str(e)[:150]})


def recheck_sent(hours=24):
    """Replies marked sent in the last `hours`: is that message still there? Reopened only when a complete read of the
    conversation, parsed in code, no longer has that exact ts. A failed or partial read changes nothing."""
    from jarvis import slack_send
    cut = (dt.datetime.now().astimezone() - dt.timedelta(hours=hours)).isoformat()
    out = {'rechecked': 0, 'reopened': [], 'skipped': []}
    for f in glob.glob(os.path.join(HUB, 'work', '20*.json')):
        rid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for aid, w in d.items():
            rep = (w or {}).get('reply') if isinstance(w, dict) else None
            if not (rep and (rep.get('sent_at') or '') >= cut and re.fullmatch(r'\d+\.\d+', str(rep.get('sent_ts') or '')) and rep.get('channel_id')):
                continue
            try:
                if time.time() - dt.datetime.fromisoformat(rep.get('rechecked_at')).timestamp() < 6 * 3600:
                    continue                   # at most every 6 hours per reply
            except (TypeError, ValueError):
                pass
            out['rechecked'] += 1
            slack_send._edit(rid, aid, lambda w: w['reply'].update(rechecked_at=slack_send.now()))
            tt = str(rep.get('thread_ts') or '')
            tt = tt if re.fullmatch(r'\d+\.\d+', tt) else ''
            try:
                msgs, complete = slack_send.read(rep['channel_id'], tt, oldest='' if tt else str(float(rep['sent_ts']) - 1))
            except Exception as e:
                out['skipped'].append(f'{rid}|{aid}: {str(e)[:80]}')
                continue
            if any(m['ts'] == rep['sent_ts'] for m in msgs) or not complete or not msgs:
                continue                       # still there, or not sure: leave it sent
            def reopen(w):
                r = w['reply']
                for k in ('sent_at', 'sent_by', 'sent_ts', 'sent_text', 'sent_link', 'send', 'slack_draft', 'slack_draft_id', 'slack_draft_text'):
                    r.pop(k, None)                       # a fresh Slack draft is made for it
                w['replied_by_owner'] = False
                w['status'] = 'drafted'
            slack_send._edit(rid, aid, reopen)
            _untick(rid, aid)                            # back in To do
            out['reopened'].append(f'{rid}|{aid}')
            ledger.event('reply_unsent', f'{rid}|{aid}', 'sentcheck', {'why': 'message no longer in Slack (checked in code)'})
    return out
