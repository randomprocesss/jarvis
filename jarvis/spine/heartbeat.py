"""The one scheduler (HARNESS.md §3c, Phase 2, 5 Oct 2026): a thread in the server, ticking every 10 s, plain code.

Each tick (what used to be three clock routines and several loops):
  - dispatch: start whatever may start now in the asked / background lanes (spine/dispatcher.py)
  - inbox: Dock badges every 30 s and the safety cadence decide when a round-up runs (spine/watchers.py; replaces :00)
  - items: when the inbox files change, the ledger mirror is refreshed; every NEW item that needs work gets a background
    worker at once (replaces the :30 run for new items: nothing waits up to 59 minutes)
  - approvals: an approval older than 2 minutes that nobody is carrying out starts the operator (replaces the :15 run)
  - world: catalog scans and need matching when their time comes (spine/world, registered by server.py)
Kill switch: while state/KILL exists nothing new starts. Every decision is an event in the ledger.
"""
from jarvis import paths
import datetime as dt, json, os, threading, time
from jarvis.spine import dispatcher, health, ledger, watchers

HUB = str(paths.home())
KILL = os.path.join(HUB, 'state', 'KILL')
TICK = 10


class Heartbeat:
    def __init__(self, changed_v, run_now, spawn_operator, operator_alive, extra=None):
        self.changed_v, self.run_now, self.spawn_operator, self.operator_alive = changed_v, run_now, spawn_operator, operator_alive
        self.extra = extra or []                 # [(name, every_s, fn)]: world jobs
        self.inbox = watchers.InboxWatcher()
        self.last = {}
        self.items_v = None
        self.status = {'started': ledger.now()}
        self.world_busy = None
        self.on_leave_slack, self.front, self.last_front, self.leave_slack_at = None, (lambda: ''), '', None

    def due(self, name, every):
        t = time.time()
        if t - self.last.get(name, 0) >= every:
            self.last[name] = t
            return True
        return False

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name='heartbeat').start()

    def _loop(self):
        time.sleep(5)
        while True:
            health.beat('heartbeat')
            try:
                self.tick()
            except Exception as e:
                ledger.event('heartbeat_error', '', 'spine', {'error': f'{e.__class__.__name__}: {e}'[:300]})
            time.sleep(TICK)

    def tick(self):
        if os.path.exists(KILL):
            self.status['killed'] = True
            return
        self.status['killed'] = False
        dispatcher.dispatch()
        if self.due('incidents', 60):                 # Phase 4: a crash, a dead thread or a restart storm -> a fix task
            from jarvis.spine import incidents
            incidents.tick()
        if self.due('autopilot', 600):                # Level 5 (The owner, 5 Oct): notice, fix, gate, ship, watch, roll back (own thread)
            from jarvis.spine import autopilot
            threading.Thread(target=autopilot.tick, daemon=True, name='autopilot').start()
        if self.due('health', 600):                   # logins, connectors, stale watchers -> said once (off this thread)
            threading.Thread(target=health.report_problems, daemon=True, name='health-deep').start()
        if self.due('inbox', 30):
            reason = self.inbox.tick()
            self.status['badges'] = self.inbox.prev
            if reason:
                r = self.run_now('roundup')
                ledger.event('roundup_started', '', 'spine', {'reason': reason, 'result': r})
                self.status['last_roundup'] = {'at': ledger.now(), 'reason': reason, 'state': r.get('state')}
        v = self.changed_v()
        if v != self.items_v:
            self.items_v = v
            rows, new = ledger.ingest_items()
            self.status['items'] = rows
            for rid, aid in new:
                self.new_item(rid, aid)
        if self.due('approvals', 60):
            self.reconcile_approvals()
        # he sends drafts himself from Slack: when he leaves the Slack app, check those threads soon (world/sentcheck.py)
        if self.on_leave_slack:
            app = self.front()
            if self.last_front == 'Slack' and app and app != 'Slack':
                self.leave_slack_at = time.time()
            self.last_front = app or self.last_front
            if self.leave_slack_at and time.time() - self.leave_slack_at > 20 and time.time() - self.last.get('sent_check', 0) > 180 \
                    and not self.world_busy:
                self.leave_slack_at = None
                self.last['sent_check'] = time.time()
                self.world_busy = 'sent_check'
                threading.Thread(target=self._world, args=('sent_check', self.on_leave_slack), daemon=True, name='world-sent_check').start()
        if not self.world_busy:                  # world jobs (scans, mining, matching) run one at a time on their own thread,
            for name, every, fn in self.extra:   # so a slow scan never delays dispatch or the inbox
                if name not in self.last:         # last runs survive restarts (a restart must not re-run daily jobs)
                    try:
                        self.last[name] = float(ledger.cursor('world_last:' + name) or 0)
                    except ValueError:
                        self.last[name] = 0
                if self.due(name, every):
                    ledger.cursor('world_last:' + name, self.last[name])
                    self.world_busy = name
                    threading.Thread(target=self._world, args=(name, fn), daemon=True, name='world-' + name).start()
                    break

    def _world(self, name, fn):
        t0 = time.time()
        try:
            from jarvis.spine import limits
            if limits.until_time():                   # degraded mode: nothing that needs Claude starts until the limit lifts
                self.status.setdefault('world', {})[name] = {'at': ledger.now(), 'secs': 0, 'result': 'waiting: Claude usage limit'}
                return
            r = fn()
            self.status.setdefault('world', {})[name] = {'at': ledger.now(), 'secs': round(time.time() - t0), 'result': str(r)[:200]}
        except Exception as e:
            ledger.event('world_error', name, 'spine', {'error': f'{e.__class__.__name__}: {e}'[:300]})
        finally:
            self.world_busy = None

    def new_item(self, rid, aid):
        """A new inbox item that needs work gets a background worker now. Jarvis's own items are asked work (already started);
        items already done, worked, or running are left alone."""
        it = ledger.q('SELECT * FROM items WHERE rid=? AND aid=?', (rid, aid))
        if not it:
            return
        it = it[0]
        if it['kind'] in ('jarvis', 'desk') or it['done'] or it['has_work']:
            return
        if ledger.q("SELECT 1 FROM tasks WHERE rid=? AND aid=? AND state IN ('queued','running','paused')", (rid, aid)):
            return
        t = dispatcher.submit('background', dispatcher.item_prompt(rid, aid), rid, aid, title=it['title'], origin='inbox',
                              kind='item')
        ledger.event('item_dispatched', f'{rid}|{aid}', 'spine', {'task': t['id'], 'state': t['state'], 'title': it['title'][:120]})

    def reconcile_approvals(self):
        """Approved more than 2 minutes ago, not done, and no operator running: start it (their press already approved it)."""
        try:
            items = json.load(open(os.path.join(HUB, 'approvals.json'))).get('items', [])
        except Exception:
            return
        cut = dt.datetime.now().astimezone() - dt.timedelta(minutes=2)
        stuck = []
        for it in items:
            if it.get('status') != 'approved' or not it.get('approved_at'):
                continue
            try:
                if dt.datetime.fromisoformat(it['approved_at']) < cut:
                    stuck.append(it.get('id'))
            except ValueError:
                pass
        # A Jira comment the operator posted: code records it on the item (once the worker posted the comment but left the
        # item unsent, so Jarvis and Jira disagreed). Link from the operator's result; the item's answered points ticked.
        for it in items:
            rel = it.get('related') or {}
            if it.get('kind') == 'jira' and it.get('status') == 'done' and rel.get('rid') and rel.get('aid') \
                    and (it.get('payload') or {}).get('action') == 'comment':
                try:
                    from jarvis import slack_send
                    import re
                    w = slack_send._entry(rel['rid'], rel['aid'])
                    if not (w.get('reply') or {}).get('sent_at'):
                        m = re.search(r'https://\S+?focusedCommentId=\d+', str(it.get('result') or ''))
                        slack_send._sent(rel['rid'], rel['aid'], m.group(0) if m else '', '', it['payload'].get('text', ''),
                                         draft_cleared=False, by='ladder (Jira)' if it.get('created_by') == 'Jarvis (ladder)' else 'Jarvis (Jira)')
                except Exception as e:
                    ledger.event('jira_record_error', it.get('id', ''), 'spine', {'error': str(e)[:150]})
        if stuck and not self.operator_alive():
            r = self.spawn_operator()
            ledger.event('approvals_reconciled', ','.join(map(str, stuck))[:200], 'spine', {'operator': r})
            self.status['last_reconcile'] = {'at': ledger.now(), 'stuck': len(stuck), 'operator': r}
