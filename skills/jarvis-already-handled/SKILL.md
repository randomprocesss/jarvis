---
name: jarvis-already-handled
description: Use when an inbox item asks for something the owner may already have answered, sent or measured (a message asking again, a list, a file, a repeated analysis), before doing any new work.
allowed-tools: Read, Grep, Glob
---
# Already-handled check

Read-only. Nothing is sent, posted or written to any app, to memory or to any database. The output is a short status
note in the item's result.

## Steps
1. State the ask in one line: who asked, where, at what time, and the exact deliverable (list, file, number, decision).
2. Read the thread or conversation in full, including {{owner}}'s own messages, screenshots and attached files. Their
   replies after the ask are the main evidence. Record each with its time.
3. Check Jarvis's earlier items with the same need and the result files they list. Check the context files in
   `{{home}}/work/context/` for the same target.
4. Check whether anything is newer than the earlier answer: new data, new messages, new replies. If the inputs are
   unchanged, the earlier result stands; do not recompute. Name the inputs compared.
5. Compare the ask with what was delivered, item by item. Label each: delivered (with time), partly delivered, still
   owed, or waiting on someone else (say who, and since when).
6. If something is still owed, do only that part, using jarvis-inbox-work and the matching profile (analyst or writer).
7. Do not draft an acceptance or a reply that commits {{owner}} to something they have told people they will first
   check. Withdraw such drafts and say why.
8. When the answer is an interim one, say plainly what the messages show so far and which fact is unconfirmed.

## Checks that caught mistakes
- A later message from {{owner}} exists that the inbox card did not show.
- A superseded file is still listed next to the current one; name which is current.
- Times are matched to the right day and time zone ({{tz}}).
- Claims about who did what are tied to a dated message or record, not recalled from memory.
- The earlier verdict is quoted with its own numbers; do not restate it with new, unverified figures.

## Rails
- Read only; no messages, comments or memory edits.
- Use they/them for people unless their pronouns are stated.
- Separate what is confirmed from what is waiting on someone.

## Write at the end
One short block in the item result:
- Verdict line: "Already done at HH:MM on DD Mon" or "Part done; owed: X".
- Evidence: 2-4 bullets with sources and times.
- Still owed / waiting on: names and what.
- Links to the earlier result files. If new work was done, list its files.
