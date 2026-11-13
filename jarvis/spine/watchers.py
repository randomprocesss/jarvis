"""Watchers turn the world into events (HARNESS.md §3c, Phase 2, 5 Oct 2026). Plain code: no model call on any poll.

Inbox (replaces the hourly :00 fetch):
  - every 30 s the unread badges of Slack, Teams and Outlook on the Dock are read (`lsappinfo`, zero tokens). A badge that
    goes up means something new for them: a round-up runs ~2 minutes later (messages settle), at most one per 20 minutes;
  - a safety round-up runs when none has run for 60 minutes while they are at the Mac (90 when away), 07:00-23:00, so Jira,
    Confluence and a closed Teams are still covered.
  The round-up's window is its own cursor (roundup_last.json), so nothing is fetched twice, even while the old routine runs.
  Measured 5 Oct: a model-based "anything new?" check costs ~$0.17 a run (the connectors' tool list alone is ~80k tokens),
  so a model never polls.
"""
from jarvis import paths
import datetime as dt, glob, json, os, re, subprocess, time

HUB = str(paths.home())
APPS = ('Slack', 'Microsoft Teams', 'Microsoft Outlook')
SETTLE_S, MIN_GAP_S, SAFETY_ACTIVE_S, SAFETY_AWAY_S = 120, 20 * 60, 60 * 60, 90 * 60


def badge(app):
    """The Dock badge of a running app as a number (0 = none or not running; a dot badge counts as 1)."""
    try:
        o = subprocess.run(['lsappinfo', 'info', '-only', 'StatusLabel', app], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return 0
    m = re.search(r'"label"\s*=\s*"([^"]*)"', o)
    if not m:
        return 0
    s = m.group(1).strip()
    return int(s) if s.isdigit() else (1 if s else 0)


def badges():
    return {a: badge(a) for a in APPS}


def idle_secs():
    """Seconds since the last keyboard or mouse input (IOHIDSystem), or None."""
    try:
        o = subprocess.run(['ioreg', '-c', 'IOHIDSystem', '-d', '4'], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r'"HIDIdleTime" = (\d+)', o)
        return int(m.group(1)) / 1e9 if m else None
    except Exception:
        return None


def last_roundup_at():
    """When the last round-up finished checking (roundup_last.json checked_at), as epoch seconds, or 0."""
    try:
        return dt.datetime.fromisoformat(json.load(open(os.path.join(HUB, 'roundup_last.json')))['checked_at']).timestamp()
    except Exception:
        return 0


class InboxWatcher:
    """Decides when a round-up should run. tick() is called by the heartbeat; it returns a reason string when a round-up
    should start now, else None. State is in memory (a restart just re-reads the badges)."""

    def __init__(self):
        self.prev = None
        self.pending_since = None        # a badge went up at this time: run after SETTLE_S
        self.last_started = 0

    def tick(self, now=None, b=None, idle=None, last=None, hour=None):
        now = now or time.time()
        b = badges() if b is None else b
        reason = None
        if self.prev is not None and any(b.get(a, 0) > self.prev.get(a, 0) for a in APPS):
            self.pending_since = self.pending_since or now
        self.prev = b
        last = last_roundup_at() if last is None else last
        since_any = now - max(last, self.last_started)
        if self.pending_since and now - self.pending_since >= SETTLE_S and since_any >= MIN_GAP_S:
            up = [a for a in APPS if b.get(a)]
            reason = 'new messages (' + ', '.join(up or ['badge']) + ')'
        hour = dt.datetime.now().hour if hour is None else hour
        idle = idle_secs() if idle is None else idle
        away = idle is not None and idle > 15 * 60
        if not reason and 7 <= hour < 23 and since_any >= (SAFETY_AWAY_S if away else SAFETY_ACTIVE_S):
            reason = 'safety check (no round-up for %d min)' % (since_any // 60)
        if reason:
            self.pending_since = None
            self.last_started = now
        return reason
