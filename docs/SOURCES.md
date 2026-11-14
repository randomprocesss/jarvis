# What Jarvis reads

| Source | How | Set it up |
|---|---|---|
| Your Claude Code chats | Read from Claude Code's own folder, only for the chats held in your work folders | Setup > Folders |
| Slack, Jira and Confluence, Outlook, Teams and SharePoint | Through the apps connected to your Claude account, read-only | Connect them in Claude (claude.ai, Settings, Connectors), then Setup > Claude > Check |
| Documents | The files in your work folders (text, pages, notebooks, office files) | Setup > Folders |
| Your repositories | A read-only copy of each, kept in your data folder | `sources.code_url` and `sources.pr_repos` ([CONFIG.md](CONFIG.md)) |
| Pull requests | The pull-request watch, read-only | Setup > Optional parts |
| Your own tool servers | Any server you list, with only the read tools you name | `sources.mcp_config` and `sources.read_tools` |

## The inbox

Every round-up reads what reached you since the last one in each connected app and turns it into items: what waits on
you, answers others gave you, and things worth knowing. Calendar events are never read. Nothing is marked read, reacted
to or sent.

## The knowledge base

Learning reads your chats and documents. What it reads from the apps is chosen on **Setup > Sources > What learning
reads from your apps** (or in settings), and with nothing chosen, nothing is read from them. Every night Jarvis reads
what changed there since the night before. Tickets and wiki pages need your Atlassian site: **Find my site** looks it
up through the app connected to your Claude account.

| Setting | What is read |
|---|---|
| `sources.tracker_projects` | every ticket of these projects (default: `knowledge.ticket_keys`) |
| `sources.wiki_spaces` | every page of these spaces |
| `sources.channels` | these chat channels, one `ID name [since]` per line, and your direct messages with the people in them |
| `sources.mail_terms` | mail, Teams messages and meetings that carry these words (default: `work.search_words`) |
| `sources.since` | the first day read (default: twelve months back) |

## With an API key

The apps connected to a Claude account are out of reach with a key alone: each needs its own tool server, named in
`sources.mcp_config` with its read tools in `sources.read_tools`. Setup says this under Sources.
