# How Jarvis learns

No language model is trained. Jarvis builds and keeps four things on your computer, from your own data.

| What it learns | From what | When |
|---|---|---|
| A knowledge base: facts, decisions, open points, who asked for what | Your Claude Code chats held in your work folders; the messages, tickets and pages in your connected apps; documents in your folders | The first pass when you press Start learning on Setup; then every night between 01:00 and 05:00 |
| How you write to each person | The difference between what Jarvis drafted and what you actually sent | After every reply you send |
| Rules for how to behave | Your corrections ("never do X", "always check Y") | When you say them |
| How far it may act alone | Your record: how many of its drafts went out unchanged, per kind of action | Continuously; a level only rises by your own press ([SAFETY.md](SAFETY.md)) |

On a Mac, the one real training is the wake word: about five minutes of your own voice (you say "Jarvis" 30 times and
read a short paragraph) make a small model that stays on your Mac; the recordings are deleted after training.

## The knowledge base

1. **Reading.** Each of your chats becomes a unit of text. Chats held outside your work folders are never read; a chat
   about something personal is left out.
2. **Notes.** Each unit becomes a short note of what was worked on, decided and found, with the numbers as they were
   said, their unit and their date. Secrets and private matters are screened out first ([PRIVACY.md](PRIVACY.md)).
3. **Workstreams.** The notes are filed under your workstreams, and each workstream note keeps its current position: what
   holds now, what was replaced and by what.
4. **Records.** Every fact, decision and open point becomes a record with the note it came from. A record that a later
   one replaces is marked as replaced, never given as current.
5. **The self-exam.** Jarvis asks itself questions made from its own records and checks its answers, so you can see how
   far to trust it.

Every night it adds the day's new work the same way. It spends at most a set amount a night, waits when your Claude
allowance is nearly used, and stops at your weekly line.

## How it uses what it learned

When you ask something, or when it works an item from your inbox, it looks in the knowledge base first and treats what
it finds as a lead, never as proof: anything it tells someone else is checked against the source again.

## What you can change

- **Folders**: which folders count as work (Setup).
- **Personal words**: names and places that must never enter it (`state/personal_words.txt` in your data folder).
- **Forget**: tell Jarvis to forget something, or retire a rule it learned (Trust > Learned).
- **Erase everything**: Setup > Privacy.
