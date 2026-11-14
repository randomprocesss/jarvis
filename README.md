# Jarvis

An assistant that works from your own messages, learns from your own data, and asks before it acts.

It reads what reaches you (chat, mail, tickets, documents), turns it into a short list of what needs you, does the
work it can (research, drafts, analyses), and waits for your press before anything is sent or changed. Overnight it
folds the day's work into a knowledge base of your own, so tomorrow it starts from what is already known.

This repository holds the code only. Everything about you (settings, messages, the knowledge base, logs) lives in a
data folder on your own computer, `~/.jarvis` unless `JARVIS_HOME` says otherwise. Nothing of yours is ever written
into this folder.

## Get it and start it

```bash
git clone <the address you were given> jarvis
cd jarvis
./install.sh
```

It opens on a Setup page in your browser; everything else is done there. The steps, the commands and what Jarvis will
and will not do are in [docs/QUICKSTART.md](docs/QUICKSTART.md). To look around on made-up data first: `jarvis demo`.

## Status

Version 0.0.1: an early working copy, being built in stages. Releases are git tags (`v0.1.0`, ...); the app offers a
newer one on its Setup page and updates only on your press. What changed in each is in [CHANGELOG.md](CHANGELOG.md).

Not in place yet: running with an API key in place of a Claude plan, and Windows; the guides beyond the quick start.

## Run the tests

```bash
python3 -W ignore -m unittest discover -s tests
```

## Before anything is shared

`tools/leakcheck.py` searches a folder, and its commit history, for words that must not leave your computer: you give
it your own list. This repository is checked with it before every push.

## Licence

No licence has been chosen yet.
