# The deploy gate

An optional part of Jarvis. It reviews every change that is about to go out of your repositories, on your own computer,
and says GO or NO-GO before the change goes further.

## What it does

Every 30 minutes it fetches a copy of each repository you named and looks for three kinds of change:

- **a feature branch about to be merged**: a branch with commits that no deploy branch has yet;
- **a push to a deploy branch**: new commits on a branch whose push deploys;
- **a release tag**: a new tag that matches one of your release patterns.

Each change is reviewed once, in its own checkout, by a Claude reviewer that follows fixed instructions:

1. it reads the ticket (when a tracker is connected) and the commit messages, and writes down what a user should be able
   to see or do once the change is out;
2. it reads the whole diff for risks (migrations, configuration, secrets, changes nobody asked for);
3. it builds the code and runs its tests;
4. it runs the change locally and uses it the way a user would, on the new code and on the code before it;
5. it puts the changed parts under load, on this computer only;
6. it writes a verdict file: **GO**, **GO-WITH-NOTES**, **NO-GO** or **NEEDS-INFO**, with its evidence.

The gate itself then checks the verdict. A verdict counts only if it is complete and agrees with itself: a GO with a
failed check, or with an intended behaviour that was not verified, is thrown away and the review is tried again, after a
wait, up to the number of tries you set. After that the change is marked **stuck** and you are told.

When a change that got a NO-GO is pushed again, the next review must answer each earlier finding, one by one. "Fixed" is
only ever what such a review showed. Between reviews, the gate re-checks open findings mechanically against the newest
code, so a finding whose lines were changed by a later commit is listed apart from the ones that are still there.

The Deploy gate screen shows the changes, their verdicts and the open findings. You are pinged for a NO-GO, a
NEEDS-INFO, a stuck change, every release tag, and anything other than a plain GO on a change of your own.

## What it never does

- It never pushes, merges, tags or deploys. It fetches and reads. The reviewer runs with git unable to push at all.
- It never connects to a shared database or service. What it starts, it starts on this computer and removes afterwards.
- It never sends a message, a comment or a page to anyone. Its only output is files in your data folder and a ping to
  you.
- It never reviews anything you did not name in settings. With no repository named, it does nothing and says so.

## What you should know before switching it on

- **It runs code from the changes it reviews.** That is the point: it builds and tests them. The reviewer works under
  limits (a list of allowed commands, no credential stores, no network beyond installing dependencies, the workers'
  guard), but those limits narrow what it does; they are not a sandbox. Use it on repositories whose contributors you
  trust, or run Jarvis inside a virtual machine.
- **It uses your Claude usage.** A review is a long run on your strongest model. Reviews wait while your 5-hour window
  is nearly used, and a run starts at most a few of them (`max_reviews`). With an API key instead of a Claude plan there
  is no spending line for unattended work yet, so the gate reviews nothing by itself; "Review now" still works.
- **It needs macOS or Linux**, with `git` and Claude Code installed. It is not available on Windows yet.
- A review of a change to a screen needs whatever that repository needs to run in a browser; the reviewer installs
  what the repository itself asks for.

## Switching it on

1. Tell Jarvis where your repositories live, if you have not yet: in Setup, or in `config.toml` in your data folder:

   ```toml
   [sources]
   code_url = "https://github.com/acme/"
   ```

   `git` signs in with whatever sign-in it already has on this computer. Jarvis never reads or stores it.

2. Name what to watch, in the same file. Only the first line is needed:

   ```toml
   [gate]
   repos = ["shop", "api"]
   deploy_branches = ["main", "api = develop release/*"]
   release_tags = ["v*"]
   build = ["shop = npm ci && npm run build", "api = make build"]
   test = ["shop = npm test", "api = make test"]
   ```

   The gate's settings are edited in this file by hand, and by nobody else: two of them are commands that run on your
   computer.

3. Switch it on: in Setup, under Optional parts, or `deploy_gate = true` under `[modules]`. It starts with Jarvis the
   next time Jarvis starts. The first look records where each deploy branch stands, so its history is not reviewed;
   feature branches and release tags of the last `days` days are queued, and each run starts at most `max_reviews`
   reviews.

To try it by hand first, from the Jarvis folder:

```bash
python3 gate/gate.py selftest         # offline checks of the gate itself
python3 gate/gate.py scan             # look, and list what it found; reviews nothing
python3 gate/gate.py review --id ID   # review one change now
python3 gate/gate.py status
```

## Every setting

All under `[gate]`. A line may be for every repository (`"main"`) or for one, written `"name = value"` with a space on
each side of the equals sign (`"api = develop staging"`).

| Setting | What it is | If you leave it out |
|---|---|---|
| `repos` | The repositories to watch, by name under `sources.code_url`. | The ones in `sources.pr_repos`; if that is empty too, nothing is watched. |
| `deploy_branches` | Branches whose push deploys. Patterns are allowed (`release/*`). | Each repository's default branch. |
| `release_tags` | Tag patterns that are releases (`v*`). All tags of one pattern are one series; a release is compared with the one before it. | No tag is reviewed. |
| `ignore_branches` | Branch patterns never reviewed (`wip/*`, a bot's branch). Backup and scratch copies of a branch are always left out. | Every other branch counts. |
| `build` | The build command, run in the change's checkout before the reviewer starts. | The reviewer works out how to build. |
| `test` | The test command, run the same way, on the new code and on the code before it. | The reviewer works out how to test. |
| `ui_repos` | Repositories with screens. There a verdict counts only if the change was run and used locally. | No repository has that rule. |
| `env_file` | A file of `NAME=value` lines given to the build and test commands only, for a private package registry's login. It must be named `.env` or `.env.<word>`: a name no worker may read. Its values are never shown to the reviewer and are blanked out of every log. Use a login that can only read. | The commands run with no extra values. |
| `docker` | `true` lets a review start throwaway local containers (a database for the tests). Docker must be running. | No containers; what would need one is marked as not run. |
| `days` | A feature branch counts while its last commit is at most this many days old. | 7 |
| `max_reviews` | Reviews started in one run. | 3 |
| `parallel` | Reviews running at the same time. | 1 |
| `review_minutes` | One review is stopped, with everything it started, after this long. | 50 |
| `max_attempts` | Failed tries before a change is stuck. A Claude usage limit never counts as a try. | 3 |
| `usage_max` | Reviews wait while Claude's 5-hour window is at or above this share. | 0.85 |
| `min_free_gb` | Reviews wait while the disk has less free space than this. | 10 |

The ticket keys the gate looks for in branch names and commit messages are the ones in `knowledge.ticket_keys` (and
`tracker.own_projects`); with none listed, any `KEY-123`.

## Holding it, and where things are

- **Hold** on the screen stops automatic reviews until you resume; the gate still looks and lists what is new.
- Everything it writes is in your data folder: `deploy_gate/` (the record, `verdicts/`, `logs/`, `report.md`, the list
  of everything it has seen in `ledger.jsonl`) and `repos/` (the copies of your repositories). Nothing is written into
  the Jarvis folder or into your own working folders.
- The reviewer's instructions are installed as a Claude Code agent named `jarvis-deploy-gate`, filled in from your
  settings. If you edit that file by hand, Jarvis leaves your version alone.
- `python3 gate/gate.py verdict-for <repository> <commit> [<branch or tag>]` prints the verdict that governs pushing
  that commit (or `none`). A pre-push hook of your own can use it to refuse a push the gate has not passed; the gate
  never installs one.
