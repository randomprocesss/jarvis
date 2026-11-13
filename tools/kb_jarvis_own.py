"""KB plan 4c (what is left): what Jarvis itself has learned, into brain/sources_extra/l_jarvis_* units for the knowledge
base. No model use here; each unit passes the personal screen before it is written (kb_live.screened_write).

  - facts: everything Jarvis learned as a fact, with when it held (valid from / to), where it came from and the quote;
    a closed fact is kept as history with why it closed; a tainted one is marked;
  - their answers to Jarvis's questions (answers.json): the decisions they gave on items;
  - learned edits and signals: what Jarvis changed in itself from their feedback, and whether they kept it;
  - what they asked Jarvis to remember.

    kb_jarvis_own.py run
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import datetime as dt, json, os, sqlite3, sys

HUB = str(paths.home())
paths.use_tools()
import kb_live as L  # noqa: E402


def run():
    c = sqlite3.connect(os.path.join(HUB, 'state', 'jarvis.db'))
    c.row_factory = sqlite3.Row
    today = dt.date.today()
    out = {}
    facts = c.execute('SELECT * FROM facts ORDER BY created').fetchall()
    lines = []
    for f in facts:
        when = f"from {str(f['valid_from'] or f['created'])[:10]}" + (f" to {str(f['valid_to'])[:10]}" if f['valid_to'] else ', still holds')
        lines.append(f"- [{f['kind']}] {f['text']} ({when}; from {f['origin']}" + (f", {f['source_ref']}" if f['source_ref'] else '') + ')'
                     + (f"\n  quote: \"{f['quote']}\"" if f['quote'] else '') + (f"\n  closed: {f['closed_why']}" if f['closed_why'] else '')
                     + ('\n  (marked tainted: not to be trusted)' if f['tainted'] else ''))
    if lines:
        out['facts'] = L.screened_write('l_jarvis_facts', f'What Jarvis learned as facts: {len(lines)} | read {today}', '\n'.join(lines))
    mem = c.execute('SELECT * FROM memory WHERE removed_at IS NULL').fetchall()
    if mem:
        out['memory'] = L.screened_write('l_jarvis_remembered', f'What the owner asked Jarvis to remember: {len(mem)} | read {today}',
                                         '\n'.join(f"- {m['text']} ({str(m['created'])[:10]})" for m in mem))
    edits = c.execute('SELECT * FROM learn_edits ORDER BY at').fetchall()
    if edits:
        out['edits'] = L.screened_write('l_jarvis_learned_edits', f'What Jarvis changed in itself from their feedback: {len(edits)} | read {today}',
                                        '\n'.join(f"- {str(e['at'])[:10]} {e['op']} {e['tbl']}: {e['text']}" + (f" (why: {e['why']})" if e['why'] else '')
                                                  + (' (undone)' if e['undone_at'] else '') + ('' if e['accepted'] in (None, 1) else ' (not accepted)') for e in edits))
    sig = c.execute('SELECT * FROM learn_signals ORDER BY at').fetchall()
    if sig:
        out['signals'] = L.screened_write('l_jarvis_signals', f'Feedback signals Jarvis recorded: {len(sig)} | read {today}',
                                          '\n'.join(f"- {str(s['at'])[:10]} {s['kind']} ({s['origin']}): {str(s['data'])[:600]}" for s in sig))
    try:
        ans = json.load(open(os.path.join(HUB, 'answers.json')))
    except (OSError, ValueError):
        ans = {}
    if ans:
        out['answers'] = L.screened_write('l_jarvis_answers', f"The owner's answers to Jarvis's questions on items: {len(ans)} | read {today}",
                                          '\n\n'.join(f'### {k}\n{json.dumps(v, ensure_ascii=False, indent=1)[:4000]}' for k, v in ans.items()))
    print(out)
    return out


if __name__ == '__main__':
    if sys.argv[1:2] == ['run']:
        run()
    else:
        print(__doc__)
