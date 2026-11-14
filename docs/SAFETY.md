# What Jarvis may do on its own

Jarvis reads and drafts on its own. Everything that leaves your computer, or changes something outside its own folder,
waits for your press. This page says exactly where that line is and how it can move.

## The line

| Jarvis does by itself | Jarvis never does without your press |
|---|---|
| Reads your Claude Code chats in your work folders, and the apps connected to your Claude account | Sends a message, an e-mail or a reply |
| Turns what reaches you into a list of what needs you | Posts a comment, creates or moves a ticket |
| Researches, analyses and drafts | Writes to any database or system |
| Builds and updates its knowledge base, overnight | Merges, pushes or deploys code |
| Suggests what to do next, with its reason | Changes how far it may act |

Your press is checked in the server, not just on the screen: a send, an approval or a write is refused unless it comes
from the Jarvis page with that session's key (`jarvis/spine/gateway.py`). The worker that drafts cannot press for you.

## How far it may act, kind by kind

Each kind of outward action has a level. A new install starts every kind at L1 or L2: Jarvis drafts, and you send or
approve.

| Level | Meaning |
|---|---|
| L0 | off |
| L1 | Jarvis drafts; you send or do it |
| L2 | Jarvis prepares; you approve with one press |
| L3 | Jarvis says what it will do, waits ten minutes, then does it unless you stop it |
| L4 | acts, listed in the day's record with an undo |
| L5 | acts; you audit |

A level only rises by your own press on the **Trust** page, and only after your own record has earned it: the last 20
drafts of that kind went out exactly as Jarvis wrote them. One edit starts the count again. Some kinds have a ceiling
they can never pass (anything in production: never; a merge into a shared branch, or a reply to someone not on your
list: never above your press). Stopping a held reply on the Trust page always wins, and sends that kind back to your
approval.

## Usage

Work you ask for is never held back. Background work keeps to a share of your allowance:

- with a Claude plan: inbox work waits when the 5-hour window is 85% used; Jarvis's own checks keep to a small share of
  a typical day and wait at 60%; learning has its own line (settings: `engine.learn_window_max`);
- with an API key: the same rules against the day's spend and your daily limit in dollars.

Before a task you ask for that looks large (10% of a typical day or more), Jarvis says so and waits for your yes.

## When something goes wrong

Every action is recorded with what was asked and what happened (Activity). A worker that stops is restarted from where
it was, up to twice, and then it asks you. Jarvis never deletes your files: anything it removes goes to the Trash.
