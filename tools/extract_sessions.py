#!/usr/bin/env python3
"""Extract the owner's Claude Code chats for the knowledge base build. Code only, no model calls.

For each chat file (and every sub-agent run under it) in the chat folders of their work folders, in order: their messages
and Claude's replies in full, every tool call (name + input, up to CAP characters) and every tool output (up to CAP
characters). Images are skipped. Secret-shaped strings (the shipped secret patterns) are blanked before anything is written.

    python tools/extract_sessions.py [--cap 4000]   -> brain/sessions_deep/<date>_<id>.txt
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import glob, json, os, re, sys

T = str(paths.claude_chats())
if '--from' in sys.argv:                           # 16 Oct: rebuild from the cleaned archive (personal chats and lines removed)
    T = os.path.expanduser(sys.argv[sys.argv.index('--from') + 1])
HUB = str(paths.home())
OUT = os.path.join(HUB, 'brain', 'sessions_deep')
if '--out' in sys.argv:
    OUT = os.path.expanduser(sys.argv[sys.argv.index('--out') + 1])
CAP = int(sys.argv[sys.argv.index('--cap') + 1]) if '--cap' in sys.argv else 4000
SKIP = re.compile(r'^<(system-reminder|local-command|command-name|command-message|command-args|bash-|task-notification)|^Caveat:', re.I)
from jarvis import screens
SECRETS = [rx for _, rx in screens.secret_patterns()]


def clean(s):
    s = re.sub(r'<system-reminder>.*?</system-reminder>', '', str(s or ''), flags=re.S)
    for rx in SECRETS:
        s = rx.sub('[secret removed]', s)
    return s.strip()


def cap(s):
    return s if len(s) <= CAP else s[:CAP] + f' […{len(s) - CAP} more characters]'


def lines_of(path):
    out, first, n_user = [], None, 0
    for line in open(path, errors='replace'):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get('type') not in ('user', 'assistant') or o.get('isMeta'):
            continue
        m = o.get('message') or {}
        role = m.get('role') or o.get('type')
        ts = str(o.get('timestamp') or '')[:16].replace('T', ' ')
        c = m.get('content')
        blocks = [{'type': 'text', 'text': c}] if isinstance(c, str) else (c if isinstance(c, list) else [])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            t = b.get('type')
            if t == 'text':
                x = clean(b.get('text'))
                if not x or SKIP.match(x) or x.startswith('This session is being continued from a previous conversation') or x.startswith('[Request interrupted'):
                    continue
                first = first or ts
                n_user += role == 'user'
                out.append(f"[{ts}] {'OWNER' if role == 'user' else 'CLAUDE'}: {x}")
            elif t == 'tool_use':
                x = clean(json.dumps(b.get('input') or {}, ensure_ascii=False))
                out.append(f"[{ts}] TOOL {b.get('name')}: {cap(x)}")
            elif t == 'tool_result':
                cc = b.get('content')
                parts = cc if isinstance(cc, list) else [{'type': 'text', 'text': str(cc or '')}]
                txt = '\n'.join(str(p.get('text') or '') for p in parts if isinstance(p, dict) and p.get('type') == 'text')
                x = clean(txt)
                if x:
                    out.append(f"[{ts}] OUTPUT{' (error)' if b.get('is_error') else ''}: {cap(x)}")
    return out, first, n_user


# --rich (12 Oct, after the deep read used 4% of the week in 15 minutes): nothing that exists only in the chats is cut,
# what is already on disk becomes a short line. In full: his messages, Claude's replies, plans, his answers to questions,
# sub-agent briefs and final reports, workflow results. Capped: what came from outside (Jira, Confluence, Slack, mail, web,
# browser pages) at 3,000; command output as its head and tail. One line only: files read, written or edited (the files
# hold the content). Records copied into a resumed or forked chat (same uuid) are kept once, in the earliest chat.
# Sub-agent runs are folded into their parent chat: brief, files they wrote, outside reads, final report.
RICH_OUT = os.path.join(HUB, 'brain', 'sessions_rich')
if '--rich-out' in sys.argv:
    RICH_OUT = os.path.expanduser(sys.argv[sys.argv.index('--rich-out') + 1])
FULL_OUT = {'Agent', 'Task', 'Workflow', 'AskUserQuestion', 'ExitPlanMode', 'TaskOutput'}
FILES = {'Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'NotebookRead'}


def _outside(name):
    return name.startswith('mcp__') or name in ('WebFetch', 'WebSearch')


def _short(s, n):
    return s if len(s) <= n else s[:n] + f' […{len(s) - n} more]'


def _headtail(s, h=700, t=500):
    return s if len(s) <= h + t else s[:h] + f'\n[…{len(s) - h - t} characters…]\n' + s[-t:]


def _call(name, inp, sub):
    g = lambda k: str(inp.get(k) or '')
    if name == 'Bash':
        return None if sub else 'RUN ' + _short(clean(g('command')), 300) + (f'  # {g("description")}' if inp.get('description') else '')
    if name in ('Read', 'NotebookRead'):
        return None if sub else 'READ ' + g('file_path') + (f' (from line {g("offset")})' if inp.get('offset') else '')
    if name == 'Write':
        return 'WROTE ' + g('file_path') + f' ({len(g("content"))} chars): ' + _short(clean(g('content')), 0 if sub else 300)
    if name in ('Edit', 'MultiEdit', 'NotebookEdit'):
        return 'EDITED ' + (g('file_path') or g('notebook_path')) + ('' if sub else ': ' + _short(clean(g('new_string') or g('new_source')), 200))
    if name in ('Grep', 'Glob'):
        return None if sub else 'SEARCH ' + _short(g('pattern') + ' ' + g('path'), 200)
    if name == 'StructuredOutput':
        return 'FINAL REPORT: ' + clean(json.dumps(inp, ensure_ascii=False))
    if name in ('Agent', 'Task'):
        return 'SUB-AGENT BRIEF: ' + _short(clean(g('prompt') or json.dumps(inp, ensure_ascii=False)), 3000)
    if name == 'Workflow':
        return 'WORKFLOW: ' + _short(clean(g('script') or g('scriptPath') or g('name')), 2000) + (' ARGS ' + _short(clean(json.dumps(inp.get('args'), ensure_ascii=False)), 1000) if inp.get('args') else '')
    if name in ('AskUserQuestion', 'ExitPlanMode', 'EnterPlanMode'):
        return f'{name}: ' + clean(json.dumps(inp, ensure_ascii=False))
    if name in ('TodoWrite', 'TaskCreate', 'TaskUpdate'):
        return None if sub else f'{name}: ' + _short(clean(json.dumps(inp, ensure_ascii=False)), 800)
    if _outside(name):
        return f'{name}: ' + _short(clean(json.dumps(inp, ensure_ascii=False)), 600)
    return None if sub else f'{name}: ' + _short(clean(json.dumps(inp, ensure_ascii=False)), 300)


def _result(name, txt, err, sub):
    if err:
        return 'ERROR: ' + _short(txt, 600)
    if name in FULL_OUT:
        return txt
    if _outside(name):
        return _short(txt, 2000 if sub else 3000)
    if sub or name in FILES or name in ('ToolSearch', 'Skill'):
        return None
    if name == 'Bash':
        return _headtail(txt)
    if name in ('Grep', 'Glob'):
        return _short(txt, 400)
    return _short(txt, 300)


def rich_lines(path, seen, sub=False, briefs=None, window=None):
    """window=(lo, hi): only records with lo < timestamp <= hi (UTC ISO), for the nightly refresh."""
    out, first, names = [], None, {}
    for line in open(path, errors='replace'):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get('type') not in ('user', 'assistant') or o.get('isMeta'):
            continue
        m = o.get('message') or {}
        c = m.get('content')
        blocks = [{'type': 'text', 'text': c}] if isinstance(c, str) else (c if isinstance(c, list) else [])
        for b in blocks:
            if isinstance(b, dict) and b.get('type') == 'tool_use':
                names[b.get('id')] = b.get('name') or '?'
        u = o.get('uuid')
        if u and u in seen:
            continue
        if window and not (window[0] < str(o.get('timestamp') or '')[:19] <= window[1]):
            continue
        if u:
            seen.add(u)
        role = m.get('role') or o.get('type')
        ts = str(o.get('timestamp') or '')[:16].replace('T', ' ')
        for b in blocks:
            if not isinstance(b, dict):
                continue
            t, x = b.get('type'), None
            if t == 'text':
                x = clean(b.get('text'))
                if not x or SKIP.match(x) or x.startswith('This session is being continued from a previous conversation') or x.startswith('[Request interrupted'):
                    continue
                if sub and role == 'user':
                    k = hash(x)
                    if briefs is not None and k in briefs:
                        x = '(same brief as an earlier sub-agent)'
                    else:
                        if briefs is not None:
                            briefs.add(k)
                        x = 'BRIEF: ' + _short(x, 2500)
                else:
                    x = f"{'OWNER' if role == 'user' and not sub else 'SUB-AGENT' if sub else 'CLAUDE'}: {x}"
            elif t == 'tool_use':
                x = _call(b.get('name') or '?', b.get('input') or {}, sub)
            elif t == 'tool_result':
                cc = b.get('content')
                parts = cc if isinstance(cc, list) else [{'type': 'text', 'text': str(cc or '')}]
                txt = clean('\n'.join(str(p.get('text') or '') for p in parts if isinstance(p, dict) and p.get('type') == 'text'))
                if txt:
                    r = _result(names.get(b.get('tool_use_id'), '?'), txt, b.get('is_error'), sub)
                    x = None if r is None else 'OUTPUT: ' + r
            if x:
                first = first or ts
                out.append(f'[{ts}] {x}')
    return out, first


def _first_ts(path):
    for line in open(path, errors='replace'):
        m = re.search(r'"timestamp":"([^"]+)"', line)
        if m:
            return m.group(1)
    return '9999'


def main_rich():
    os.makedirs(RICH_OUT, exist_ok=True)
    mains, subs = [], {}
    for d in [str(d) for d in paths.work_chat_dirs()]:
        for f in glob.glob(os.path.join(d, '**', '*.jsonl'), recursive=True):
            parts = os.path.relpath(f, d).split(os.sep)
            if len(parts) == 1:
                mains.append((_first_ts(f), f, d))
            else:
                subs.setdefault(parts[0], []).append((_first_ts(f), f))
    seen, briefs, n, tot, nsub = set(), set(), 0, 0, 0
    for _, f, d in sorted(mains):                     # earliest chat first, so a copied record stays where it was made
        sid = os.path.basename(f)[:-6]
        ls, first = rich_lines(f, seen)
        sub_txt = []
        for _, sf in sorted(subs.get(sid, [])):
            sl, sfirst = rich_lines(sf, seen, sub=True, briefs=briefs)
            if sl:
                nsub += 1
                sub_txt.append(f'--- sub-agent run {os.path.basename(sf)[:-6][-8:]} | {sfirst}\n' + '\n'.join(sl))
        if not ls and not sub_txt:
            continue
        first = first or '0000-00-00'
        same = glob.glob(os.path.join(OUT, f'*_{sid[:8]}.txt'))     # the deep read's name, so notes already made are kept
        name = os.path.basename(same[0])[:-4] if same else f'{first[:10]}_{sid[:8]}'
        body = f'session {sid} | folder {os.path.basename(d)} | {first}\n\n' + '\n\n'.join(ls)
        if sub_txt:
            body += f'\n\n=== SUB-AGENT RUNS ({len(sub_txt)}) ===\n\n' + '\n\n'.join(sub_txt)
        open(os.path.join(RICH_OUT, name + '.txt'), 'w').write(body + '\n')
        n += 1; tot += len(body)
    orphan = sum(len(v) for k, v in subs.items() if k not in {os.path.basename(f)[:-6] for _, f, _ in mains})
    print(f'{n} chats with {nsub} sub-agent runs folded in, {tot / 1e6:.1f} MB; sub-agent runs with no parent chat: {orphan}')


def main():
    if '--rich' in sys.argv:
        return main_rich()
    os.makedirs(OUT, exist_ok=True)
    stats, n = [], 0
    for d in [str(d) for d in paths.work_chat_dirs()]:
        for f in glob.glob(os.path.join(d, '**', '*.jsonl'), recursive=True):
            rel = os.path.relpath(f, d)
            parts = rel.split(os.sep)
            sid = parts[0][:-6] if len(parts) == 1 else parts[0]
            sub = '' if len(parts) == 1 else '-sub-' + os.path.basename(f)[:-6][-8:]
            ls, first, n_user = lines_of(f)
            if not ls:
                continue
            name = f"{(first or '0000-00-00')[:10]}_{sid[:8]}{sub}"
            head = f"session {sid}{(' | sub-agent run ' + os.path.basename(f)[:-6]) if sub else ''} | folder {os.path.basename(d)} | {first}"
            body = head + '\n\n' + '\n\n'.join(ls) + '\n'
            open(os.path.join(OUT, name + '.txt'), 'w').write(body)
            stats.append((name, len(body)))
            n += 1
    tot = sum(s[1] for s in stats)
    print(f'{n} transcripts ({sum(1 for s in stats if "-sub-" in s[0])} sub-agent runs), {tot / 1e6:.1f} MB (~{tot / 4 / 1e6:.1f}M tokens), cap {CAP}')


if __name__ == '__main__':
    main()
