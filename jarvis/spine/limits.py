"""Degraded mode (HARNESS.md Phase 3, 5 Oct 2026): Claude's usage limit.

The CLI answers a limit with text like "You've hit your individual spend limit · ... · your session limit resets 4:50am
(Asia/Calcutta)" (seen in transcripts as error=rate_limit, status 429). seen(text) recognises it wherever Claude's output
is read, records until when (cursor 'limited_until'), and says so once in the ledger. While limited: no new workers or
noticing jobs start (the queue keeps everything), and the voice brain says plainly when it can think again.
"""
import datetime as dt, re
from jarvis.spine import ledger

# Claude's own wording only (a worker's notes about a Slack API "rate limit" must not pause everything)
LIMIT = re.compile(r"(you'?ve hit your [\w -]*limit|claude ai usage limit|usage limit reached|\"error\"\s*:\s*\"rate_limit\")", re.I)
RESET = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*([ap]m)?", re.I)


def seen(text):
    """If `text` is a usage-limit message: record it and return the time it lifts (datetime); else None."""
    t = str(text or '')
    if not LIMIT.search(t):
        return None
    now = dt.datetime.now().astimezone()
    until = now + dt.timedelta(minutes=30)                      # no time given: look again in half an hour
    m = RESET.search(t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or '').lower()
        if ap == 'pm' and h < 12:
            h += 12
        if ap == 'am' and h == 12:
            h = 0
        if h < 24 and mi < 60:
            cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            until = cand if cand > now else cand + dt.timedelta(days=1)
    prev = until_time()
    ledger.cursor('limited_until', until.isoformat(timespec='seconds'))
    if not prev:
        ledger.event('rate_limited', '', 'limits', {'until': until.isoformat(timespec='minutes'), 'text': t[:200]})
    return until


def until_time():
    """When the limit lifts, or None if Claude is usable now."""
    v = ledger.cursor('limited_until')
    if not v:
        return None
    try:
        u = dt.datetime.fromisoformat(str(v))
    except ValueError:
        return None
    if u <= dt.datetime.now().astimezone():
        ledger.cursor('limited_until', '')
        ledger.event('rate_limit_lifted', '', 'limits', {})
        return None
    return u


def say_until(u):
    return u.strftime('%-I:%M %p').lower().replace(':00 ', ' ')
