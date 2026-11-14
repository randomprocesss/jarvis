#!/usr/bin/env python3
"""Write docs/CONFIG.md from the settings themselves: every key of jarvis/config.py's DEFAULTS, its default value and the
comment beside it in that file, so the guide can never drift from the code.

    python3 tools/config_doc.py            write docs/CONFIG.md
    python3 tools/config_doc.py --check    exit 1 when docs/CONFIG.md is out of date
"""
import io, json, os, re, sys, tokenize
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from jarvis import config  # noqa: E402

OUT = os.path.join(REPO, 'docs', 'CONFIG.md')


def comments():
    """{'table.key': 'the comment beside it'} from the DEFAULTS block of config.py: a key's comment is the one on its own
    line, followed by any comment-only lines just below it (the file's style for long notes)."""
    src = open(os.path.join(REPO, 'jarvis', 'config.py'), encoding='utf-8').read()
    a = src.index('DEFAULTS = {'); b = src.index('\n}\n', a)
    lines = src[a:b].splitlines()
    out, table, last = {}, '', None
    for line in lines:
        m = re.match(r"^\s{4}'([a-z_]+)': \{", line)
        if m:
            table = m.group(1)
        code, _, note = line.partition('#')
        keys = re.findall(r"'([a-z_]+)':", code)
        if m:
            keys = keys[1:]
        if keys:
            for k in keys:
                last = f'{table}.{k}'
                out.setdefault(last, '')
            if note.strip():
                out[last] = (out[last] + ' ' + note.strip()).strip()
        elif note.strip() and last and not code.strip():
            out[last] = (out[last] + ' ' + note.strip()).strip()
    return out


# Settings whose line in config.py carries no comment of its own (several keys on one line, or a name that says it all).
DESCRIBE = {
    'owner.name': 'your name, as Jarvis writes it; until it is set, the app opens on Setup',
    'org.names': 'the names of your organisation, masked on screens you share',
    'work.projects': 'the names of your projects or workstreams, in your words',
    'work.folders': 'the folders that hold your work: Jarvis learns from the Claude Code chats held there and works in the first one',
    'work.days': 'your working days, like mon-fri',
    'work.hours': 'your working hours, like 09:00-19:00',
    'engine.work_model': 'the Claude model for real work (drafts, analyses)',
    'engine.mid_model': 'the Claude model for middle-sized steps (notes, sorting)',
    'engine.quick_model': 'the Claude model for small, quick steps',
    'sources.claude_connectors': 'read the apps connected to your Claude account (Slack, Jira, Outlook, ...)',
    'sources.chats': 'read your Claude Code chats held in your work folders',
    'sources.mcp_config': 'a file of your own tool servers that Jarvis runs may use (with an API key, the way to reach your apps)',
    'sources.folders': 'more folders whose data files (csv, json, ...) are listed for Jarvis to find',
    'sources.slack_user_id': 'your Slack user id ("U..."), found by Setup',
    'tracker.site': 'your Atlassian site address, only for direct Jira calls with your own token',
    'tracker.email': 'the e-mail of your Atlassian account, only for direct Jira calls with your own token',
    'modules.deploy_gate': 'the deploy gate: reviews changes to your repositories before they ship (docs/DEPLOY-GATE.md)',
    'modules.vault': 'mirror your work into a notes folder, such as an Obsidian vault (vault.path)',
    'modules.prwatch': 'the pull-request watch: review requests and changes on your pull requests, as inbox items',
    'server.port': 'the port the app is served on, at http://localhost:<port>',
}


def render():
    c = comments()
    for k, v in DESCRIBE.items():
        if not c.get(k):
            c[k] = v
    c = {k: re.sub(r'^[a-z_]+: ', '', v) for k, v in c.items()}       # "address_as: what Jarvis calls them" -> "what Jarvis calls them"
    L = ['# Settings', '', 'Every setting, its default and what it does. Setup in the app changes the common ones; all of them live in',
         '`config.toml` in your data folder (`~/.jarvis/config.toml`), as `[table]` then `key = value`. A setting that is not in',
         'the file has its default. This page is written from the code (`tools/config_doc.py`), so it is always current.', '']
    for table, vals in config.DEFAULTS.items():
        L += [f'## {table}', '', '| Setting | Default | What it does |', '|---|---|---|']
        for k, v in vals.items():
            note = c.get(f'{table}.{k}', '').replace('|', '\\|')
            L.append(f'| `{table}.{k}` | `{json.dumps(v, ensure_ascii=False)}` | {note} |')
        L.append('')
    return '\n'.join(L)


if __name__ == '__main__':
    text = render()
    if '--check' in sys.argv:
        have = open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else ''
        sys.exit(0 if have == text else 1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, 'w', encoding='utf-8').write(text)
    print(f'{OUT}: {text.count(chr(10) + "| `")} settings')
