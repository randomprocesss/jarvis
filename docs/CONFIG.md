# Settings

Every setting, its default and what it does. Setup in the app changes the common ones; all of them live in
`config.toml` in your data folder (`~/.jarvis/config.toml`), as `[table]` then `key = value`. A setting that is not in
the file has its default. This page is written from the code (`tools/config_doc.py`), so it is always current.

## owner

| Setting | Default | What it does |
|---|---|---|
| `owner.name` | `""` | your name, as Jarvis writes it; until it is set, the app opens on Setup |
| `owner.address_as` | `""` | what Jarvis calls them ("Sam", "boss") |
| `owner.emails` | `[]` | their own addresses: how their sent mail and commits are told apart |

## org

| Setting | Default | What it does |
|---|---|---|
| `org.names` | `[]` | the names of your organisation, masked on screens you share |
| `org.domains` | `[]` | their organisation's names and mail domains, masked on shared screens |
| `org.never_name` | `[]` | first names never shown on the map (someone who has left) |

## work

| Setting | Default | What it does |
|---|---|---|
| `work.projects` | `[]` | the names of your projects or workstreams, in your words |
| `work.folders` | `[]` | the folders that hold your work: Jarvis learns from the Claude Code chats held there and works in the first one |
| `work.days` | `"mon-fri"` | your working days, like mon-fri |
| `work.hours` | `"09:00-19:00"` | your working hours, like 09:00-19:00 |
| `work.quiet` | `"22:30-07:00"` | when they usually work |
| `work.about` | `""` | one line on what they do, in their words (for prompts) |
| `work.search_words` | `[]` | words that mark their work when Jarvis searches the apps |
| `work.branch_prefix` | `""` | the start of the branches Jarvis makes ("sl/"); default: their initials |
| `work.skip_words` | `[]` | a file whose path holds one of these words stays out of the knowledge base (e.g. an admin tool's exports) |
| `work.never_read` | `[]` | more places a worker must never read (keys, private folders) |
| `work.analyst_words` | `[]` | words that mark a request as number work (their systems, tables) |

## engine

| Setting | Default | What it does |
|---|---|---|
| `engine.mode` | `"claude-plan"` | claude-plan \| api-key |
| `engine.work_model` | `"claude-opus-5-5"` | the Claude model for real work (drafts, analyses) |
| `engine.mid_model` | `"claude-sonnet-5-5"` | the Claude model for middle-sized steps (notes, sorting) |
| `engine.quick_model` | `"claude-haiku-5-5"` | the Claude model for small, quick steps |
| `engine.background_share` | `0.25` | of a plan's usage window, for work nobody asked for |
| `engine.learn_window_max` | `0.7` | learning waits while the 5-hour window is at or above this |
| `engine.learn_week_max` | `0.5` | and stops for the week at this share of the weekly limit |
| `engine.learn_night_extra` | `0.0` | the nightly update may go this far past that line (0: never) |
| `engine.daily_cap_usd` | `5.0` | with an API key: background work stops here |

## sources

| Setting | Default | What it does |
|---|---|---|
| `sources.claude_connectors` | `true` | read the apps connected to your Claude account (Slack, Jira, Outlook, ...) |
| `sources.chats` | `true` | read your Claude Code chats held in your work folders |
| `sources.mcp_config` | `""` | a file of your own tool servers that Jarvis runs may use (with an API key, the way to reach your apps) |
| `sources.folders` | `[]` | more folders whose data files (csv, json, ...) are listed for Jarvis to find |
| `sources.read_tools` | `[]` | read-only tools of their own MCP servers the assistant may call |
| `sources.slack_user_id` | `""` | your Slack user id ("U..."), found by Setup |
| `sources.slack_workspace` | `""` | theirs, found by Setup: "U…" and "https://<team>.slack.com" |
| `sources.expected` | `[]` | connectors their work needs: Jarvis says when one drops |
| `sources.shared_repos` | `[]` | repositories shared with many teams: only their own commits and the ones that name their work are read what the knowledge base reads from the connected apps (tools/kb_live.py); nothing set, nothing read |
| `sources.since` | `""` | the first day read, YYYY-MM-DD (default: twelve months back) |
| `sources.tracker_projects` | `[]` | ticket projects read in full (default: knowledge.ticket_keys) |
| `sources.wiki_spaces` | `[]` | wiki spaces read in full |
| `sources.channels` | `[]` | chat channels: "ID name [since]" per line |
| `sources.mail_terms` | `[]` | words that mark work mail, Teams messages and meetings (default: work.search_words) |
| `sources.client_domains` | `[]` | a meeting with someone from these mail domains is work |
| `sources.shared_folder` | `""` | the shared-files folder to catalogue (its file:/// address) |
| `sources.shared_read` | `[]` | its top folders whose documents are read in full |
| `sources.code_workspace` | `""` | the workspace on the code host, for pull requests |
| `sources.code_url` | `""` | where their repositories live, e.g. https://github.com/name/ (read-only mirror for the assistant) |
| `sources.pr_repos` | `[]` | repositories whose pull requests are read (default: the git repositories in the work folders) |

## tracker

| Setting | Default | What it does |
|---|---|---|
| `tracker.own_projects` | `[]` | ticket projects that are theirs (e.g. "ABC"): the only ones a comment may ever reach without their press |
| `tracker.cloud_id` | `""` | their Atlassian site's id, found by Setup (the connector needs it) |
| `tracker.site` | `""` | your Atlassian site address, only for direct Jira calls with your own token |
| `tracker.email` | `""` | the e-mail of your Atlassian account, only for direct Jira calls with your own token |
| `tracker.use_token` | `false` | only for direct Jira calls with their own API token; off = Jarvis does Jira through the Atlassian connector |

## knowledge

| Setting | Default | What it does |
|---|---|---|
| `knowledge.name` | `"main"` | the knowledge base's folder inside brain/ |
| `knowledge.keep_secrets` | `false` | keep whole copies of files that held a secret, in a locked folder? Off: the knowledge base's copies are redacted and no second copy of a secret is made |
| `knowledge.acronyms` | `[]` | words kept in capitals on the map ("API", their project codes) |
| `knowledge.ticket_keys` | `[]` | the ticket projects that are theirs ("ABC" for ABC-123); none: any |
| `knowledge.entities` | `[]` | their own kinds of thing to keep a page for: "kind = pattern" |
| `knowledge.pipelines` | `[]` | code whose settings are read: "name = folder"; none: every git repository in the work folders |

## vault

| Setting | Default | What it does |
|---|---|---|
| `vault.path` | `""` | the folder the work is mirrored into (an Obsidian vault, or a folder inside one), when modules.vault is on |
| `vault.kb_folder` | `"Knowledge"` | the knowledge base's folder inside it |

## research

| Setting | Default | What it does |
|---|---|---|
| `research.topics` | `[]` | standing topics for the weekly look outside (general terms) |

## sharing

| Setting | Default | What it does |
|---|---|---|
| `sharing.ai_wording` | `"never"` | may text that goes to other people name Claude, Jarvis or AI? |
| `sharing.folder` | `""` | a synced folder (a cloud drive) spreadsheets are shared through |
| `sharing.folder_link` | `""` | the link to that folder, for the line that names a shared file never \| allowed (takes effect at the next start) |

## notify

| Setting | Default | What it does |
|---|---|---|
| `notify.enabled` | `true` | pings to the owner's phone or desktop |

## health

| Setting | Default | What it does |
|---|---|---|
| `health.watch_logins` | `[]` | command-line logins to watch, e.g. "gcloud" the optional parts (jarvis/modules), each switched on here |

## modules

| Setting | Default | What it does |
|---|---|---|
| `modules.deploy_gate` | `false` | the deploy gate: reviews changes to your repositories before they ship (docs/DEPLOY-GATE.md) |
| `modules.vault` | `false` | mirror your work into a notes folder, such as an Obsidian vault (vault.path) |
| `modules.prwatch` | `false` | the pull-request watch: review requests and changes on your pull requests, as inbox items |
| `modules.self_improve` | `false` | Jarvis changing its own code (tested, watched, undone if worse) |

## links

| Setting | Default | What it does |
|---|---|---|
| `links.ticket_base` | `""` | e.g. their tracker's "browse" address, for ticket links |

## server

| Setting | Default | What it does |
|---|---|---|
| `server.port` | `8765` | the port the app is served on, at http://localhost:<port> |
