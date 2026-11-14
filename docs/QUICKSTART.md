# Quick start

Jarvis runs on your own computer. It needs three things: Python 3.9 or newer, git, and Claude Code signed in to your
Claude account (a Pro or Max plan, or an API key).

## 1. Get it

Download the newest release and unpack it, or, better, clone it so it can update itself later:

```bash
git clone https://github.com/OWNER/jarvis.git
cd jarvis
```

(Use the address you were given. A copy made with git clone can update itself from the app; a downloaded zip cannot.)

## 2. Install and start

On a Mac or on Linux:

```bash
./install.sh
```

It checks what Jarvis needs, adds a `jarvis` command, starts Jarvis and opens it in your browser. It uses no sudo,
downloads nothing and changes nothing outside your data folder (`~/.jarvis`) and one small file, `~/.local/bin/jarvis`.

## 3. Set it up in the app

Jarvis opens on its Setup page. Everything is done there:

1. **You**: your name, what you do, your working and quiet hours.
2. **Claude**: one press checks that Claude Code answers and which of your apps (Slack, Jira, Outlook, ...) are
   connected to your Claude account. Apps are connected in Claude itself, never in Jarvis.
3. **Folders**: the folders that hold your work. Jarvis learns from the Claude Code chats you had there.
4. **Learn**: one press starts the first learning pass. It can take hours and uses your Claude allowance; it waits when
   the allowance is nearly used. After that it updates itself every night.

Setup also suggests what is worth doing next, each from something it found on your computer. It never acts on a
suggestion by itself.

Want to look around first? Press **Look around first** on the Setup page, or run `jarvis demo`: a made-up person's
week, where nothing is real and nothing is sent.

## Every day

| Command | What it does |
|---|---|
| `jarvis start` | start Jarvis and open it |
| `jarvis stop` | stop it |
| `jarvis status` | is it running, which version, what Setup still needs |
| `jarvis doctor` | check what Jarvis needs, each with what to do about it |
| `jarvis demo` | the made-up demo |
| `jarvis uninstall` | stop Jarvis and say how to remove it (deletes nothing itself) |

## What Jarvis will and will not do

- Every kind of action starts at "draft only". Nothing is sent, posted, merged or written anywhere without your press.
- Your data stays in `~/.jarvis` on your computer. No usage data, crash report or copy is ever sent anywhere.
- **Setup > Privacy** makes a copy of everything, or erases everything (to the Trash by default).
- **Setup > Updates** shows when a new release is out. It updates only when you press Update, and keeps a copy of your
  settings first.

## The Mac app and voice (optional, Mac only)

The browser is enough. On a Mac there is also a small app with a menu-bar icon, office mode and voice. Build it with
`mac/build.sh` (it installs to `~/Applications/Jarvis.app`). The wake word ("Hey Jarvis") needs five small open models,
about 15 MB: `python3 voice/setup.py` fetches them after asking. Without them the app listens when you press and speaks
with the Mac's own voice.
