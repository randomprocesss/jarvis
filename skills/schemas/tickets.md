# Ticket proposals

`tickets/proposals.json` in the data home holds the ticket proposals made from inbox action items. Jarvis writes it
through `POST /api/tickets_save`; the `jarvis-tickets` skill refines drafts and writes back the key after creating a
ticket. A created ticket's key and status on disk always win over a save from the page.

```json
{"items": [{
  "id": "t-2026-03-04-1100-all-a1.1",      // t-<round-up id>-<action key>, [A-Za-z0-9._-], at most 80 characters
  "status": "draft",                      // draft | ready | created | dismissed
  "summary": "Split the weekly export into one run per region",
  "description": "Objective first…\n\nRaised by Priya N in #orders (4 Mar 10:08).",
  "type": "Task",                         // Task | Sub-task | Story | Bug
  "parent": "",                           // parent key when type is Sub-task
  "link": "",                             // a colleague's ticket it supports ("relates to")
  "estimate_h": null,
  "source": {"rid": "2026-03-04-1100-all", "key": "a1.1", "src": "slack", "who": "Priya N", "where": "#orders", "when": "4 Mar 10:08", "text": "…"},
  "key": null, "created_at": null,        // set when the ticket is created
  "reuse_key": null, "reused": false,     // set when an existing open ticket covers it
  "refined": false, "claude_note": "",    // set by the draft step
  "approved_at": null,                    // set by Jarvis when the owner presses Create
  "updated": "2026-03-04T12:00:00.000Z"
}]}
```
