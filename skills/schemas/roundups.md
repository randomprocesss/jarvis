# Round-ups: what the inbox is made from

A round-up is one JSON file in the data home's `roundups/` folder: what turned up in the connected apps in one window of
time. Jarvis merges every round-up of the last 7 days into the inbox, tags each item with its source, and picks up a new
file within 30 seconds. The `jarvis-roundup` skill builds a file from all sources at once.

- File name: `YYYY-MM-DD-HHMM-all.json` (or `YYYY-MM-DD-<source>.json`). The newest name sorts first.
- Windows must not overlap for the same source (the merged inbox does not de-duplicate).
- `state.json` holds the owner's ticks. Never write it; never reuse an old file's id for new content.
- Summaries only, in plain language. Never paste secrets, tokens or passwords.

```json
{
  "id": "2026-03-04-1100-all",          // = file name without .json
  "title": "Round-up",
  "kind": "mixed",                      // the default source of its items: slack | email | teams | jira | confluence | meeting | mixed
                                        // (never calendar events). "jarvis" = the owner asked Jarvis; "desk" = Jarvis noticed it
                                        // itself (*-desk.json): not the inbox, worked only by its own workers, shown in the brief
  "period": "3-4 Mar 2026",
  "source": "What was read, and that nothing was posted or sent",
  "by": "which run wrote it",
  "created": "2026-03-04",
  "window_start": "2026-03-03T09:00:00+01:00",
  "window_end": "2026-03-04T11:03:30+01:00",
  "sources_checked": {"slack": "ok", "email": "skipped: not connected"},
  "actions": [                          // waiting on the owner; each point is ticked separately
    {"id": "a1", "src": "slack", "who": "Priya N", "where": "#orders", "when": "4 Mar 10:08",
     "channel_id": "C0…", "thread_ts": "1759….", "email_to": "", "subject": "",   // so a reply can be drafted in place
     "title": "optional tag", "text": "optional one-liner",
     "points": [
       {"text": "question", "note": "optional context for the reply", "points": ["sub-bullets"]},
       {"text": "something the owner is waiting on", "waiting": true}
     ]}
  ],
  "answers": [{"src": "slack", "title": "Answers about the export", "who": "…", "where": "…", "when": "…", "points": ["…"]}],
  "fyi": ["plain string", {"src": "jira", "text": "with sub-points", "points": ["…"]}]
}
```
