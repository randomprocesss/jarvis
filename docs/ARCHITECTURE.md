# How Jarvis is built

One Python server on your computer, standard library only, serving a web app and running Claude Code for the work.
Your data lives in one folder (`~/.jarvis`, or `JARVIS_HOME`); the code never holds any.

```
 Browser or the Mac app ──► jarvis/server.py ──► jarvis/spine/  (the queue, the rules, the record)
        (web/out)                │                    │
                                 │                    └─► workers: Claude Code runs, each in its own process,
                                 │                         following the skills in skills/ (filled in from your settings)
                                 ├─► jarvis/world/  (what Jarvis notices: needs, promises, documents, initiatives)
                                 └─► tools/kb_*.py  (the knowledge base: built once, updated every night)
```

## The parts

| Folder | What it holds |
|---|---|
| `jarvis/` | The server and everything it runs. `config.py` and `paths.py` are the only two places that know who the owner is and where the data lives. |
| `jarvis/spine/` | The dispatcher (one queue of work, with lanes for asked work, background work and Jarvis's own checks), the governor (usage), the gateway and policy (what may act, and when), the ledger (one SQLite record of everything), health. |
| `jarvis/world/` | What Jarvis notices on its own: open needs, promises, documents, research, initiatives, the notes vault. |
| `jarvis/modules/` | Optional parts, each switched on in settings: the deploy gate, the notes vault, the pull-request watch. |
| `jarvis/migrations/` | Steps that carry your data from one version to the next. |
| `tools/` | The knowledge-base tools, the checks and proofs, the leak check. Each runs on its own too. |
| `skills/` | The instructions the workers follow, as templates; installed into Claude Code's skills folder with your values. |
| `web/` | The screens (Next.js source) and `web/out/`, built, so Node is not needed to run Jarvis. |
| `mac/`, `voice/` | The Mac app and its listening helpers (optional, macOS only). |
| `tests/` | Offline tests: a made-up owner, a stand-in for Claude, no network. |

## The rules that hold everything

- **Nothing outward without the owner's press.** The gateway checks every request that sends, approves or writes: it
  must come from the Jarvis page with that session's key. A worker cannot press for the owner.
- **Usage is metered, not guessed.** The governor reads Claude's own usage numbers (or, with an API key, the cost
  Claude Code reports per run) and holds background work at the owner's lines. Asked work is never held.
- **One record.** Every action, its receipt, every task and every learned rule is in `state/jarvis.db`.
- **Screens before keeping.** Every copy of the owner's text passes the secret and personal screens before it is kept,
  searched or mirrored (`jarvis/screens.py`).
- **Portable.** Locks, process groups and detached starts go through `jarvis/procs.py`; everything else is plain Python.

## Running it

`python3 -m jarvis start` (or `jarvis start`) starts the server in the background. `python3 -m jarvis.server --port N`
runs it in the foreground. `JARVIS_NO_BACKGROUND=1` starts it without any background work (tests, the demo).
