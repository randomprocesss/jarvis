"""The learning jobs (HARNESS.md §3d, Phase 5, 5 Oct 2026). They propose; app/learned.py decides and records.

tick()                 every 10 minutes from the consolidation loop (inside the governor's 5% learning budget):
  recipient_notes()    their edits to drafts (draft_edited signals: what Jarvis drafted vs what they sent) -> one short note per
                       person on how they write to them ("drops the greeting with Priya, keeps it to two lines"); the writer
                       reads it before drafting (GET /api/recipient_note, the jarvis-inbox-work skill)
  style                enough new reactions (5+) -> learn_style() proposes small rule edits
  index                recall index rebuilt every 30 minutes
  export               after any accepted change: brain/ (a git repo of markdown) gets a commit whose message is the edit list
label_candidates()     10-20 answers worth their 10-minute look on the weekly label page: re-asked, corrected, cut off, cancelled,
                       claim-guard hits, the slowest and the longest
"""
from jarvis import config
from jarvis import paths
import datetime as dt, json, os, re, subprocess, threading, time

from jarvis import learned

HUB = str(paths.home())
BRAIN = os.path.join(HUB, 'brain')
VOICE = os.path.join(HUB, 'voice')
CLAUDE, SONNET = (paths.claude_bin() or 'claude'), config.model('mid')
STATE = os.path.join(HUB, 'state', 'learning.json')
_lock = threading.Lock()


def _load(p, d):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def _save(p, o):
    json.dump(o, open(p + '.tmp', 'w'), indent=1)
    os.replace(p + '.tmp', p)


def _ask(prompt, timeout=180):
    """One model call, no tools; its usage is counted as learning (the governor's 5%)."""
    p = subprocess.run([CLAUDE, '-p', prompt, '--model', SONNET, '--output-format', 'json', '--strict-mcp-config',
                        '--mcp-config', '{"mcpServers":{}}', '--disallowedTools', 'Bash', 'Edit', 'Write', 'Read', 'Grep', 'Glob',
                        'WebFetch', 'WebSearch', 'Task', 'Agent', 'NotebookEdit'],
                       capture_output=True, text=True, timeout=timeout, cwd=VOICE, env=dict(os.environ, JARVIS_PROC='1', JARVIS_LANE='learning'))
    env = json.loads(p.stdout or '{}')
    try:
        from jarvis.spine import governor
        governor.note_learning(governor._weight(env.get('usage') or {}, SONNET))
    except Exception:
        pass
    out = str(env.get('result') or '')
    a, b = out.find('{'), out.rfind('}')
    return json.loads(out[a:b + 1]) if a >= 0 and b > a else {}


# ------------------------------------------------------------------ how he writes to each person
def recipient_notes(min_new=1):
    sig = learned.q("SELECT * FROM learn_signals WHERE kind='draft_edited' AND used=0 ORDER BY id")
    by = {}
    for s in sig:
        d = json.loads(s['data'] or '{}')
        who = str(d.get('to') or '').strip()
        if who:
            by.setdefault((who, d.get('channel') or 'slack'), []).append((s, d))
    edits, used = [], []
    for (who, ch), items in by.items():
        if len(items) < min_new:
            continue
        cur = learned.recipient_note(who, ch)
        prompt = ("The owner edited drafts written for them before sending them to " + who + " (" + ch + "). Below, each DRAFT and what they "
                  "actually SENT. Write how they write to this person, as a note the drafter will follow: at most 3 short lines, "
                  "concrete (greeting or not, length, tone, what they cut or adds, how they name things). Only what the edits show; "
                  "if they show nothing about style (only facts changed), say so in one line. Keep what still holds from the "
                  "current note. Never a rule about sending, approving or skipping checks. Reply with ONE JSON object: "
                  "{\"note\": \"...\", \"style_shown\": true|false}\n\nCURRENT NOTE: " + (cur[0]['note'] if cur else '(none)') + "\n\n"
                  + '\n\n'.join(f"DRAFT: {d.get('draft', '')[:1500]}\nSENT: {d.get('sent', '')[:1500]}" for _, d in items[-5:]))
        try:
            r = _ask(prompt)
        except Exception as e:
            learned.signal('learning_error', 'executed', 'recipient_notes', error=str(e)[:200])
            continue
        used += [s['id'] for s, _ in items]
        if r.get('style_shown') and str(r.get('note') or '').strip():
            edits.append({'op': 'UPDATE' if cur else 'ADD', 'table': 'recipient_notes', 'recipient': who, 'channel': ch,
                          'text': r['note'], 'origin': 'owner_said', 'source_ref': 'signals:' + ','.join(str(s['id']) for s, _ in items)})
    res = learned.apply_edits(edits, run='recipients-' + learned.now()[:16]) if edits else []
    if used:
        learned.x('UPDATE learn_signals SET used=1 WHERE id IN (%s)' % ','.join(str(i) for i in used))
    return res


# ------------------------------------------------------------------ brain/ : a git history of what was learned
def export_brain(force=False):
    """Markdown snapshot of facts, rules, recipient notes and memory; one commit per change set, the edit list as its
    message (so "what changed this week" is `git log`, and any change can be found and undone in Learned)."""
    st = _load(STATE, {})
    last = st.get('exported_at', '')
    new = learned.q("SELECT * FROM learn_edits WHERE at > ? AND accepted=1 ORDER BY at", (last,))
    if not new and not force:
        return 'nothing new'
    os.makedirs(BRAIN, exist_ok=True)
    if not os.path.isdir(os.path.join(BRAIN, '.git')):
        subprocess.run(['git', 'init', '-q', BRAIN], check=False)
        open(os.path.join(BRAIN, 'README.md'), 'w').write(
            "# What Jarvis has learned\n\nWritten by app/learning.py after every accepted change (app/learned.py is the store and the only way "
            "in). One commit per change set; the message lists the edits. Undo is in the Learned view.\n")
    def md(name, title, rows, fmt):
        open(os.path.join(BRAIN, name), 'w').write(f'# {title}\n\n' + ('\n'.join(fmt(r) for r in rows) or '(none)') + '\n')
    md('facts.md', 'Facts that hold now', learned.current_facts(),
       lambda f: f"- {f['text']}  _(since {f['valid_from']}; {f['origin']}{'; from outside content' if f['tainted'] else ''}; {f['id']})_")
    md('facts-closed.md', 'Facts that stopped being true', learned.q('SELECT * FROM facts WHERE valid_to IS NOT NULL ORDER BY valid_to DESC'),
       lambda f: f"- {f['text']}  _({f['valid_from']} to {f['valid_to']}: {f['closed_why'] or ''}; {f['id']})_")
    md('rules.md', 'How Jarvis behaves (active rules)', learned.q("SELECT * FROM rules WHERE state='active' ORDER BY scope, created"),
       lambda r: f"- [{r['scope']}] {r['text']}  _({r['origin']}; {r['source_ref']}; {r['id']})_")
    md('recipients.md', 'How they write to each person', learned.q('SELECT * FROM recipient_notes ORDER BY recipient'),
       lambda r: f"- **{r['recipient']}** ({r['channel']}): {r['note']}")
    md('memory.md', 'What they asked Jarvis to remember', learned.memory_items(), lambda m: f"- {m['text']}  _({m['created'][:10]})_")
    msg = (f"{len(new)} learned change(s)\n\n" + '\n'.join(f"{e['op']} {e['tbl']} {e['row_id']}: {e['text'][:100]} [{e['origin']}]" for e in new[:60])) if new else 'snapshot'
    gi = os.path.join(BRAIN, '.gitignore')              # 16 Oct: only the learning files, never the rest of brain/ (it held
    if not os.path.exists(gi):                          # the transcript archive and raw chat text, personal chats included)
        open(gi, 'w').write('/*\n' + ''.join(f'!/{n}\n' for n in ('.gitignore', 'README.md', 'facts.md', 'facts-closed.md', 'rules.md', 'recipients.md', 'memory.md')))
    subprocess.run(['git', '-C', BRAIN, 'add', '-A'], check=False)
    subprocess.run(['git', '-C', BRAIN, '-c', 'user.name=Jarvis learning', '-c', 'user.email=jarvis@localhost', 'commit', '-q', '-m', msg],
                   check=False, capture_output=True)
    st['exported_at'] = learned.now()
    _save(STATE, st)
    return f'exported {len(new)}'


# ------------------------------------------------------------------ the weekly label page
def label_candidates(days=7, n=16):
    """Past answers worth their look: each with why it was picked. Already-labelled ones are skipped."""
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    done = {r['trace'] for r in learned.q('SELECT trace FROM labels')}
    import glob
    turns = []
    for f in sorted(glob.glob(os.path.join(VOICE, 'conversations', '*.json'))):
        if os.path.basename(f)[:8] < cut.replace('-', ''):
            continue
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        ts = d.get('turns') or []
        for i, t in enumerate(ts):
            trace = f"{d.get('id')}#{i}"
            if trace in done or not t.get('q') or t.get('tier') == 'handoff':
                continue
            nxt = ts[i + 1].get('q', '') if i + 1 < len(ts) else ''
            why = []
            if t.get('interrupted'):
                why.append('they cut it off')
            from jarvis import human as H
            if nxt and (H.CORRECT.search(nxt) or H.NEG.search(nxt)):
                why.append('they corrected the next turn')
            if nxt and len(set(H._words(nxt)) & set(H._words(t['q']))) / max(1, len(set(H._words(nxt)) | set(H._words(t['q'])))) >= 0.6:
                why.append('they asked again')
            words = len((t.get('speak') or '').split())
            if words > 60:
                why.append(f'long ({words} words)')
            if 'deep' in str(t.get('tier')):
                why.append('went to the slow path')
            if why:
                turns.append({'trace': trace, 'at': t.get('at'), 'q': t.get('q'), 'answer': t.get('speak'), 'why': why})
    for g in learned.q("SELECT * FROM learn_signals WHERE kind='claim_guard' AND at >= ? ORDER BY id DESC LIMIT 10", (cut,)):
        d = json.loads(g['data'] or '{}')
        trace = f"guard#{g['id']}"
        if trace not in done:
            turns.append({'trace': trace, 'at': g['at'], 'q': d.get('q'), 'answer': d.get('said'), 'why': ['the claim guard had to change it']})
    turns.sort(key=lambda t: (-len(t['why']), str(t.get('at'))), reverse=False)
    return turns[:n]


def label(trace, value, note='', q='', answer=''):
    if value not in ('good', 'bad', 'skip'):
        return {'ok': False, 'error': 'label is good, bad or skip'}
    learned.x('INSERT OR REPLACE INTO labels(trace, at, label, note, q, answer) VALUES(?,?,?,?,?,?)',
              (trace, learned.now(), value, str(note)[:300], str(q)[:400], str(answer)[:800]))
    if value in ('good', 'bad'):
        learned.signal('label_' + value, 'owner_said', trace, note=str(note)[:300], q=str(q)[:300], answer=str(answer)[:500])
    return {'ok': True}


# ------------------------------------------------------------------ the loop
def tick():
    """One pass (called every 10 minutes). Each step is cheap when there is nothing new."""
    if not _lock.acquire(blocking=False):
        return 'busy'
    out = {}
    try:
        st = _load(STATE, {})
        out['recipients'] = len(recipient_notes())
        new_sig = learned.q("SELECT count(*) AS n FROM learn_signals WHERE used=0 AND kind NOT IN ('draft_edited','claim_guard','learning_error')")[0]['n']
        if new_sig >= 5:
            from jarvis import human
            human.learn_style()
            out['style'] = new_sig
        if time.time() - st.get('indexed', 0) > 1800:
            out['indexed'] = learned.index_rebuild()
            st = _load(STATE, {}); st['indexed'] = time.time(); _save(STATE, st)
        out['export'] = export_brain()
    except Exception as e:
        out['error'] = str(e)[:200]
    finally:
        _lock.release()
    return out
