# Privacy

## Where your data is

Everything Jarvis knows about you is in one folder on your computer: `~/.jarvis` (or wherever `JARVIS_HOME` points).
The program itself, this repository, holds no data of yours, and nothing of yours is ever written into it.

Jarvis sends nothing anywhere on its own: no usage data, no crash reports, no copy of your data. The only network calls
it makes are:

- **Claude**, through Claude Code on your computer, under your own Claude account or API key. What Claude sees is what
  a run needs: the text Jarvis is working on.
- **Your apps** (Slack, Jira, Outlook, ...), only through the connections you made in your Claude account, and only to
  read, unless you press to send.
- **Updates**: once a day, one `git ls-remote` of the repository you installed from, with your own git sign-in.
- **The wake word models**, once, if you run `voice/setup.py` and say yes.

## What is kept out

Before anything is kept, searched or mirrored, it passes two screens (`jarvis/screens.py`):

- **Secrets.** Passwords, keys, tokens and connection strings are found by pattern and replaced. A value is never written
  to a log, a note or a message.
- **Private matters.** Chats you held outside your work folders are never read. A chat about something personal is
  labelled and left out. You can add your own words (people, places) that must never enter the knowledge base.

## Your API key

If you use an API key, it is kept in your system's password store (the macOS Keychain, or the Secret Service on Linux),
never in a file of Jarvis's, never on a command line and never in its record of actions.

## A copy, and erasing everything

**Setup > Privacy**:

- **Download a copy**: a zip of everything in your data folder, saved in your home folder.
- **Erase everything Jarvis knows about me**: lists what will go, with sizes, and asks you to type ERASE. It moves your
  data folder, the worker skills Jarvis installed into Claude Code, and Claude Code's records of Jarvis's own runs to
  the Trash (or erases them for good, if you choose). Your own Claude Code chats, your work folders and everything in
  your apps stay exactly as they are.
