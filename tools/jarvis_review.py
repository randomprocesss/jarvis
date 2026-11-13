#!/usr/bin/env python3
"""Jarvis's weekly review (The owner, 3 Oct 2026, brain item 8: measure it). Numbers from the app's own logs, plus a Sonnet read of a
sample of the week's turns for what numbers can't see: claims with no action behind them, answers too long for the ear,
agreeing without evidence, missed questions. Writes voice/reviews/<date>.md and .json in the data home; the patterns it finds
go to jarvis_feedback.jsonl as 'review' signals and the learned style is rewritten from them.

    python3 tools/jarvis_review.py [--days 7] [--no-judge]

Run by the server once a week (jarvis_episodes.start). Reads only Jarvis's own files; the judge sees the sampled turns.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config
from jarvis import paths
import datetime as dt, json, os, re, statistics as st, subprocess, sys

H = str(paths.home()); VOICE = os.path.join(H, 'voice')
OUT = os.path.join(VOICE, 'reviews')
CLAUDE, MODEL = (paths.claude_bin() or 'claude'), config.model('mid')
TEST = re.compile(r"^(say just the word|testing|test\b|this is a test|one two three|hello hello)", re.I)


def rows(path, since):
    out = []
    try:
        for l in open(path):
            try:
                d = json.loads(l)
            except ValueError:
                continue
            if str(d.get('day') or d.get('at') or '')[:10] >= since:
                out.append(d)
    except OSError:
        pass
    return out


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * q))]) if xs else None


def numbers(since):
    m = rows(os.path.join(VOICE, 'metrics.ndjson'), since)
    u = rows(os.path.join(VOICE, 'brain_usage.ndjson'), since)
    fb = rows(os.path.join(VOICE, 'jarvis_feedback.jsonl'), since)
    corr = rows(os.path.join(VOICE, 'corrections.jsonl'), since)
    lat = lambda k: [r[k] - r['eot'] for r in m if r.get(k) and r.get('eot')]
    real_cut = sum(1 for r in m if r.get('cutoff') and not (r.get('info') or {}).get('dropped'))
    merged = sum(1 for r in m if (r.get('info') or {}).get('dropped'))
    n = {'turns': len(m), 'days': len({r.get('day') for r in m}),
         'first_audio_ms': {'p50': pct(lat('first_audio'), .5), 'p90': pct(lat('first_audio'), .9)},
         'answer_audio_ms': {'p50': pct(lat('answer_audio'), .5), 'p90': pct(lat('answer_audio'), .9)},
         'real_cutoffs': real_cut, 'merged_turns': merged, 'barge_ins': sum(1 for r in m if r.get('barge')),
         'false_barges': sum(1 for r in m if r.get('false_barge')), 'fast_path': sum(1 for r in m if r.get('fast_path')),
         'feedback': {k: sum(1 for f in fb if f.get('kind') == k) for k in sorted({f.get('kind') for f in fb})},
         'corrections': len(corr)}
    if u:
        tot_in = sum(x.get('input', 0) + x.get('cache_read', 0) + x.get('cache_write', 0) for x in u)
        n['brain'] = {'turns': len(u), 'cache_hit_pct': round(100 * sum(x.get('cache_read', 0) for x in u) / tot_in, 1) if tot_in else None,
                      'input_tokens': tot_in, 'output_tokens': sum(x.get('output', 0) for x in u),
                      'cost_usd': round(sum(x.get('cost') or 0 for x in u), 2),
                      'ttft_ms_p50': pct([x['ttft_ms'] for x in u if x.get('ttft_ms')], .5),
                      'by_stage': {s: sum(1 for x in u if x.get('stage') == s) for s in sorted({x.get('stage') for x in u})},
                      'errors': sum(1 for x in u if x.get('error'))}
    try:
        n['memory'] = {'episodes': sum(1 for _ in open(os.path.join(VOICE, 'episodes.jsonl'))),
                       'facts_now': sum(1 for f in json.load(open(os.path.join(VOICE, 'profile.json')))['facts'] if not f.get('until'))}
    except (OSError, ValueError, KeyError):
        pass
    return n


def sample(since, k=40):
    turns = []
    d = os.path.join(VOICE, 'conversations')
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not f.endswith('.json') or f[:8] < since.replace('-', ''):
            continue
        try:
            c = json.load(open(os.path.join(d, f)))
        except (OSError, ValueError):
            continue
        for t in c.get('turns') or []:
            if t.get('q') and not TEST.search(t['q'].strip()):
                turns.append(t)
    if len(turns) > k:                                  # spread over the week, not just the latest
        step = len(turns) / k
        turns = [turns[int(i * step)] for i in range(k)]
    return turns


JUDGE = """You review a voice assistant ("Jarvis") that talks to the owner, a data scientist, about their work. Below are sampled turns: \
what they said, what Jarvis said aloud ("speak") and the actions its reply carried (an action is the only way it can start, stop, \
send, post or schedule anything).
Flag each turn that has a problem, using only these flags:
  unbacked_claim  - says it is doing/started/stopped/sent/scheduled something with no action that does it
  too_long        - more than two spoken sentences when they did not ask for detail
  agrees_without_evidence - agrees with them or reassures ("it's fine", "that worked") without having checked
  shallow         - reads back records or lists when they needed a judgement (no "so what", no recommendation)
  contradiction   - notices two of its own sources disagree and hands that to them instead of resolving it
  missed          - does not answer what they asked
  repeats         - repeats itself or an earlier answer
  robotic         - stiff, formal or list-like for speech
Then name the main patterns and write 1-4 concrete rules that would fix them (each under 20 words, starting with "- ").
Reply with ONE JSON object only:
{"flags": [{"i": <turn number>, "flags": ["..."], "why": "<few words>", "rubric": "<for shallow or contradiction only: one sentence a good answer to this exact question would satisfy>"}], "patterns": [{"pattern": "...", "count": <n>, "example": "<turn number>"}], "rules": ["- ..."]}

TURNS:
"""


def judge(turns):
    lines = []
    for i, t in enumerate(turns, 1):
        acts = ', '.join(a.get('type', '') for a in t.get('actions') or []) or 'none'
        lines.append(f"[{i}] {str(t.get('at', ''))[:16]} THEY: {str(t.get('q'))[:300]}\n    SPEAK: {str(t.get('speak'))[:500]}\n    ACTIONS: {acts}")
    p = subprocess.run([CLAUDE, '-p', JUDGE + '\n'.join(lines), '--model', MODEL, '--output-format', 'text', '--strict-mcp-config',
                        '--mcp-config', '{"mcpServers":{}}', '--disallowedTools', 'Bash', 'Edit', 'Write', 'Read', 'Grep', 'Glob',
                        'WebFetch', 'WebSearch', 'Task', 'Agent', 'NotebookEdit'], capture_output=True, text=True, timeout=300, cwd=VOICE)
    out = p.stdout or ''
    a, b = out.find('{'), out.rfind('}')
    return json.loads(out[a:b + 1]) if a >= 0 and b > a else {'error': out[:300] or p.stderr[-300:]}


def report(day, n, turns, j):
    L = [f"# Jarvis weekly review, {day}", '', f"{n['turns']} voice turns over {n['days']} days.", '',
         '## How it talks and listens',
         f"- First sound after you stop: {n['first_audio_ms']['p50']} ms typical, {n['first_audio_ms']['p90']} ms slowest tenth",
         f"- The real answer: {n['answer_audio_ms']['p50']} ms typical, {n['answer_audio_ms']['p90']} ms slowest tenth",
         f"- Cut you off: {n['real_cutoffs']} (turns quietly merged because you carried on: {n['merged_turns']})",
         f"- You interrupted it: {n['barge_ins']} (false alarms: {n['false_barges']})",
         f"- Answered on the page without the brain: {n['fast_path']}",
         f"- Your feedback: {n['feedback'] or 'none'}; corrections: {n['corrections']}"]
    if n.get('brain'):
        b = n['brain']
        L += ['', '## The brain', f"- {b['turns']} brain turns, by stage {b['by_stage']}; errors {b['errors']}",
              f"- Prompt cache hit: {b['cache_hit_pct']}% of input tokens; first token {b['ttft_ms_p50']} ms typical",
              f"- Tokens in {b['input_tokens']:,}, out {b['output_tokens']:,}; cost ${b['cost_usd']}"]
    if n.get('memory'):
        L += ['', f"Memory: {n['memory']['episodes']} episodes, {n['memory']['facts_now']} facts that hold now."]
    if j and not j.get('error'):
        fl = j.get('flags') or []
        L += ['', f"## What the transcript read found ({len(turns)} turns sampled, {len(fl)} flagged)"]
        for p in j.get('patterns') or []:
            L.append(f"- {p.get('pattern')} ({p.get('count')}x, e.g. turn {p.get('example')})")
        if j.get('rules'):
            L += ['', 'Rules handed to the style learner:'] + [r for r in j['rules'] if str(r).startswith('- ')]
    elif j:
        L += ['', f"The transcript read failed: {j.get('error')}"]
    return '\n'.join(L) + '\n'


def main():
    a = sys.argv[1:]
    days = int(a[a.index('--days') + 1]) if '--days' in a else 7
    today = dt.date.today()
    since = (today - dt.timedelta(days=days - 1)).isoformat()
    n = numbers(since)
    turns = sample(since) if '--no-judge' not in a else []
    j = judge(turns) if turns else None
    os.makedirs(OUT, exist_ok=True)
    md = report(today.isoformat(), n, turns, j)
    open(os.path.join(OUT, f'{today}.md'), 'w').write(md)
    json.dump({'since': since, 'numbers': n, 'judge': j}, open(os.path.join(OUT, f'{today}.json'), 'w'), indent=1)
    if j and not j.get('error') and (j.get('patterns') or j.get('rules')):
        from jarvis import human as human
        with open(human.FEED, 'a') as f:
            for p in (j.get('patterns') or [])[:3]:
                f.write(json.dumps({'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'kind': 'review',
                                    'note': str(p.get('pattern'))[:200], 'count': p.get('count')}, ensure_ascii=False) + '\n')
            for r in (j.get('rules') or [])[:4]:
                f.write(json.dumps({'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'kind': 'review', 'rule': str(r)[:200]},
                                   ensure_ascii=False) + '\n')
        human.learn_style()
    depth_tickets(turns, j)
    print(md)


def depth_tickets(turns, j, cap=2):
    """9 Oct: the deepest misses become Level 5 tickets (spine/autopilot.py reads 'review_bad' signals, at most 2 a week) with
    the judge's rubric as the grader, so the autopilot can fix what makes Jarvis seem shallow, not only what they corrected."""
    if not j or j.get('error'):
        return 0
    from jarvis import learned
    n = 0
    for f in j.get('flags') or []:
        if n >= cap or not ({'shallow', 'contradiction'} & set(f.get('flags') or [])) or not str(f.get('rubric') or '').strip():
            continue
        try:
            t = turns[int(f.get('i')) - 1]
        except (ValueError, TypeError, IndexError):
            continue
        learned.signal('review_bad', origin='review', ref=str(t.get('at') or ''), q=str(t.get('q') or '')[:500],
                       answer=str(t.get('speak') or '')[:800], rubric=str(f['rubric'])[:300], why=str(f.get('why') or '')[:200],
                       flags=f.get('flags'))
        n += 1
    return n


if __name__ == '__main__':
    main()
