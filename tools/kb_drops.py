"""What the screens kept out (KB plan safeguard 8, 16 Oct 2026). The owner: nothing personal goes in, but a screen must never
drop work silently. Every screen either writes here with log_drop() or is read from its own state below, and
report() writes state/screen_drops.md for them to glance at. The report stays OUT of the Obsidian vault and the knowledge
base, because a dropped item's title can itself be personal.

Sources merged:
  - knowledge-base chat notes marked "personal (omitted)" or "no project content" (brain/sessions), with the first words
    of the chat so a wrong drop is easy to spot;
  - document and source notes marked the same (brain/sources);
  - the Obsidian vault's held notes and their reasons (state/vault.json);
  - anything any other screen logged with log_drop() (state/screen_drops.jsonl).

    /usr/bin/python3 tools/kb_drops.py          (writes the report and prints its counts)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import collections, datetime as dt, glob, json, os, re

HUB = str(paths.home())
BRAIN = os.path.join(HUB, 'brain')
LOG = os.path.join(HUB, 'state', 'screen_drops.jsonl')
OUT = os.path.join(HUB, 'state', 'screen_drops.md')     # outside brain/: nothing that reads the KB sees it
MARKS = ('personal (omitted)', 'no project content')


def log_drop(screen, item, why, extra=''):
    """One line per dropped item; the same screen and item are written once."""
    seen = set()
    if os.path.exists(LOG):
        for line in open(LOG, errors='replace'):
            try:
                d = json.loads(line); seen.add((d['screen'], d['item']))
            except Exception:
                pass
    if (screen, item) in seen:
        return
    with open(LOG, 'a') as f:
        f.write(json.dumps({'at': dt.datetime.now().isoformat(timespec='seconds'), 'screen': screen, 'item': item,
                            'why': why, 'extra': str(extra)[:200]}) + '\n')


def _first_words(sid):
    """The first thing they typed in the chat behind a session note (from the live transcripts or the raw archive)."""
    tail = sid.split('-sub-')[-1]
    for root in (os.path.expanduser('~/.claude/projects'), os.path.join(BRAIN, 'raw_archive', 'claude_projects')):
        for p in glob.glob(os.path.join(root, '**', f'*{tail}*.jsonl'), recursive=True):
            try:
                for line in open(p, errors='replace'):
                    d = json.loads(line)
                    if d.get('type') != 'user':
                        continue
                    c = (d.get('message') or {}).get('content')
                    t = c if isinstance(c, str) else ' '.join(x.get('text', '') for x in (c or []) if isinstance(x, dict))
                    t = re.sub(r'<[^>]+>', ' ', t)
                    t = re.sub(r'\s+', ' ', t).strip()
                    if t and not t.startswith(('Caveat:', '[Request interrupted')):
                        t = re.sub(r'\d[\d/.\-]{3,}', '####', t)    # no ids, dates of birth or phone numbers
                        return t[:80], os.path.getsize(p)
            except Exception:
                continue
    return '', 0


def _marked(folder):
    out = []
    for f in sorted(glob.glob(os.path.join(BRAIN, folder, '**', '*.md'), recursive=True)):
        head = open(f, errors='replace').read(300)
        m = next((x for x in MARKS if x in head), None)
        if m:
            out.append((os.path.relpath(f, BRAIN), m))
    return out


def report():
    lines = [f'# What the screens kept out', '',
             f'Generated {dt.datetime.now():%Y-%m-%d %H:%M}. Kept out of Obsidian and the knowledge base on purpose: '
             f'titles here can be personal. If anything below is work, tell Jarvis and it goes back in.', '']
    counts = {}
    s = _marked('sessions')
    counts['knowledge-base chat notes'] = len(s)
    # The owner, 16 Oct: nothing personal anywhere, this report included. A personal drop shows only its date and size; the
    # first words of a chat show only for "no project content" drops (work chats with nothing for the record).
    lines += [f'## Knowledge-base chat notes kept out ({len(s)})', '', '| Note | Marked | Chat size | First words |', '|---|---|---|---|']
    for rel, m in s:
        sid = os.path.basename(rel)[:-3].split('_', 1)[-1]
        words, size = _first_words(sid)
        shown = words.replace('|', '/') if m == 'no project content' else '(personal: not shown)'
        lines.append(f"| {rel} | {m} | {size // 1024} KB | {shown} |")
    d = _marked('sources')
    counts['document and source notes'] = len(d)
    lines += ['', f'## Document and source notes kept out ({len(d)})', '']
    lines += [f'- {rel}: {m}' for rel, m in d] or ['- none']
    try:
        v = json.load(open(os.path.join(HUB, 'state', 'vault.json')))
        held = v.get('held', [])
        at = v.get('at', '?')
    except Exception:
        held, at = [], '?'
    counts['Obsidian vault notes held back'] = len(held)
    by = collections.defaultdict(list)
    for h in held:
        by['personal word' if h['why'].startswith('personal word') else h['why']].append(h['path'])   # never name the word
    lines += ['', f'## Obsidian vault: notes held back ({len(held)}, last sync {at})', '',
              'Scope admin and personal are held by your rule. "Waiting for its scope label" clears on the next sync.', '']
    for why, paths in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lines.append(f'### {why} ({len(paths)})')
        if why.startswith(('scope personal', 'personal word')):
            lines += ['- (personal: count only, no titles)', '']       # a title can itself be personal
        else:
            lines += [f'- {p}' for p in sorted(paths)] + ['']
    other = []
    if os.path.exists(LOG):
        for line in open(LOG, errors='replace'):
            try:
                other.append(json.loads(line))
            except Exception:
                pass
    counts['other screens'] = len(other)
    lines += [f'## Other screens ({len(other)})', '']
    lines += [f"- {o['at'][:16]} {o['screen']}: {o['item']} ({o['why']}){' ' + o['extra'] if o.get('extra') else ''}" for o in other] or ['- none yet']
    tmp = OUT + '.tmp'
    open(tmp, 'w').write('\n'.join(lines) + '\n')
    os.replace(tmp, OUT)
    return counts


if __name__ == '__main__':
    for k, n in report().items():
        print(f'{n:4d}  {k}')
    print(OUT)
