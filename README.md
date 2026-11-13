# Jarvis

An assistant that works from your own messages, learns from your own data, and asks before it acts.

It reads what reaches you (chat, mail, tickets, documents), turns it into a short list of what needs you, does the
work it can (research, drafts, analyses), and waits for your press before anything is sent or changed. Overnight it
folds the day's work into a knowledge base of your own, so tomorrow it starts from what is already known.

This repository holds the code only. Everything about you (settings, messages, the knowledge base, logs) lives in a
data folder on your own computer, `~/.jarvis` unless `JARVIS_HOME` says otherwise. Nothing of yours is ever written
into this folder.

## Status

Version 0.0.1: an early working copy, being built in stages. It is not ready for someone to install and use yet.

In place:

- the assistant, its server and its screens (`jarvis/`, `web/`)
- the knowledge base tools: the nightly refresh, the records ledger, the screens that keep secrets and private
  matters out (`tools/`)
- the skills its workers follow, as templates filled in from your settings (`skills/`)
- the Mac app (`mac/`)
- the tests (`tests/`), passing on Python 3.9 and 3.14

Not in place yet:

- setting it up from the app (today the settings are a file, `config.toml` in the data folder)
- running with an API key instead of a Claude plan, and on Windows or Linux
- the voice helpers of the Mac app, and the deploy gate
- the guides

## Try it on made-up data

Needs Python 3.9 or newer, and nothing else. The demo builds a data folder for a made-up person and tells you how to
open it; nothing is read from, or sent to, anyone.

```bash
python3 -m jarvis.demo
```

It prints the command that starts the server on that folder. Then open `http://localhost:8766`.

## Run the tests

```bash
python3 -W ignore -m unittest discover -s tests
```

## Before anything is shared

`tools/leakcheck.py` searches a folder, and its commit history, for words that must not leave your computer: you give
it your own list. This repository is checked with it before every push.

## Licence

No licence has been chosen yet.
