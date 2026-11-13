# Work files: what was prepared for each inbox item

`work/<round-up id>.json` in the data home holds what a worker prepared for each action item of that round-up (written
by the `jarvis-inbox-work` skill, shown in Jarvis under the item). Keyed by action id.

```json
{
  "_run": {"at": "2026-03-04T13:07:00+01:00", "drafted": 5, "left": 0, "note": ""},
  "a1": {
    "status": "drafted",                       // drafted | needs_you | done
    "kind": "question",                        // question (reply only) | task (real work; a ticket when a tracker is connected)
    "ticket_id": null,                         // proposal id in tickets/proposals.json when kind is task
    "updated": "2026-03-04T13:07:00+01:00",
    "summary": "One sentence that starts with the answer.",
    "points": [{"key": "a1.0", "state": "done", "note": "One short line shown under the point.", "answer": "…", "confidence": "high",
                "sources": [{"type": "memory|repo|slack|jira|confluence|email|data", "ref": "file, link or ticket", "note": "why it matters"}]}],
    "needs_you": ["Decide: publish under the old name or a new one?"],
    "reply": {
      "channel": "slack",                      // slack | email | teams | jira | none
      "to": "Priya N", "where": "#orders",
      "channel_id": "C0…", "thread_ts": "1759…", "email_to": "", "subject": "", "teams_to": "", "jira_key": "",
      "text": "the reply, in the owner's style",
      "attachments": [{"title": "Breakdown", "path": "<a file in this item's outputs folder>", "inline": true}]
    },
    "done_work": [{"title": "Delivery review", "path": "<data home>/work/outputs/<rid>-a3/review.md", "summary": "…"}],
    "numbers": [{"what": "On-time delivery", "now": "82.6%", "was": "79% assumed"}],
    "notes": ["at most three one-line caveats"],
    "jira_key": "ABC-700",                     // the ticket created or reused for a task (the worker keeps it current)
    "branch": {"repo": "<repository>", "name": "<prefix>/ABC-700-weekly-check", "base": "main",
               "worktree": "<data home>/work/branches/ABC-700", "commit": "abc1234", "pushed": true},   // merging waits for the owner's OK
    "tests": {"cmd": "pytest", "passed": 12, "failed": 0}, "code_review": {"findings": 1, "fixed": 1, "notes": "…"},
    "running": {"started": "…", "dir": "<data home>/work/outputs/<rid>-<aid>", "what": "…", "eta": "…"},   // a long job (tools/run_job.py)
    "attempts": [{"at": "…", "error": "<the exact first error>", "fix": "<what was changed>", "result": "worked | new error: … | same error"}],
    "checks": [{"claim": "…", "first": "…", "second": "…", "result": "holds | corrected: …"}],
    "context": {                               // where the worker looked before answering (shown as "Where Jarvis looked")
      "at": "2026-03-04T13:07:00+01:00",
      "file": "<data home>/work/context/<rid>-a1.md",   // the facts found, with where each came from
      "checked": [{"where": "slack|email|teams|jira|confluence|sharepoint|claude_sessions|memory|repo|data|jarvis|live",
                   "what": "query, thread or document", "found": 2, "used": "one line on what it added, or empty"}]
    }
  }
}
```
`work/context/<rid>-<aid>.md` keeps what the full sweep found for an item; later runs re-read it and only look for what is new.

## `state` per point (shown as a coloured tag under each point)
- `done`: the worker did it and it is solved; tick the point too. `note` = what was done, with the key number.
- `you`: the owner answered it themselves; tick it. `note` = where and when, and the gist.
- `decide`: needs a decision from the owner. `note` = the choice in one line. Known alternatives go on the work entry as
  `"options": [{"id", "label", "plan", "recommended"?}]` (2-4); Jarvis shows a button per option, and a press saves
  "Chose: <label>" to answers.json and starts a worker on the item at once (so does a typed answer).
- `needs_you`: needs something only the owner can do or know. `note` = exactly what. The questions also go on the work
  entry as `asks: [{"id", "q", "detail", "options": [{"id", "label", "plan", "recommended"?}]}]`, one per question (one
  card each, option buttons plus "Already done" / "Skip" and free text; the answers come back with `parts`).
- `drafted`: answered in the draft reply, not sent yet. `note` = the answer in one line.
- `working`: the job for this point is running right now; set it when the job starts, with `running` on the work entry.
- `queued`: work the worker will do itself in a later run (a long analysis waiting its turn, or a step that follows
  another). `note` = what and when. Never `needs_you` for the worker's own queue.
A job that ran but did not solve the point is NOT `done`.
Approval requests exist only for writes to shared systems and branch merges; everything else the worker does itself.
An approval request names the point it serves: `"related": {"rid": …, "aid": …, "point": "a2.0"}`.
