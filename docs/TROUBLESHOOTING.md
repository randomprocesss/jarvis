# When something is not right

Start with:

```bash
jarvis doctor
```

It checks what Jarvis needs, one line each, and says what to do about anything missing. The Setup page shows the same
checks in the app.

| What you see | What to do |
|---|---|
| `jarvis: command not found` | The installer put it in `~/.local/bin`, which is not on your PATH. Run it as `~/.local/bin/jarvis`, or add that folder to your PATH. |
| The page does not open | `jarvis status` says whether Jarvis is running and on which port. `jarvis start` starts it. |
| "Jarvis did not start" | The last lines of `~/.jarvis/logs/server.log` say why. A port already in use: `jarvis start --port 8790`. |
| Setup says Claude Code is not signed in | Run `claude` in a terminal once and sign in, then press Check again on Setup. |
| An app shows "needs-auth" or "not connected" | Connect it in your Claude account (claude.ai, Settings, Connectors), then press Check again. |
| Learning stopped | Setup > Learn says why. Press Start learning to carry on: nothing is read or paid for twice. |
| Background work is not happening | It waits when your Claude allowance is nearly used, and outside your working hours for some kinds. Work you ask for is never held. |
| The wake word does not answer | `python3 voice/setup.py --check` in the Jarvis folder says what is missing. |
| An update is offered but cannot run | Files of the program were changed by hand. `git status` in the Jarvis folder shows them. |

Where things are:

| What | Where |
|---|---|
| Your settings | `~/.jarvis/config.toml` (or Setup) |
| Logs | `~/.jarvis/logs/` |
| Everything Jarvis keeps | `~/.jarvis/` (or wherever `JARVIS_HOME` points) |
| The program | the folder you cloned |
