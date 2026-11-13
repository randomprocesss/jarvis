"""Proof before words: every number in what Jarvis writes is tied to an output, in code.

The rule: no number, claim or scope reaches anyone until it is checked against the data; every number is computed from
an output of this piece of work before the text is written. A rule in a prompt does not hold by itself, so this is the
gate: code that reads the text and the work's files.

What counts as a claim: a number with a unit or scale (53%, $40, 212k, 2.7x), a decimal, or a whole number of 3+ digits,
in the reply, the summary, the points' answers and notes, and the `numbers` list of a work entry. Not claims: dates, times,
years, ticket and PR numbers, long ids, codes (letters with digits), versions, and anything inside
`code`, a link or a file path.

What ties a claim:
  1. the same value in one of the item's own files (its outputs folder, the folders of its done_work and attachments, its
     context files), allowing only the rounding the text shows: "40.3%" is tied by 40.25-40.35 or 0.4025-0.4035, "$164k" by
     163,500-164,499, "6,014" only by 6014;
  2. a receipt in the outputs folder's receipts.json:
       {"claim": "40.3%", "value": 40.3, "source": "<a file that exists and holds the value>"}            a value read off a file
       {"claim": "40.3%", "value": 40.3, "op": "ratio", "from": [25, 62], "source": "<file>"}             a value worked out
     op is ratio | sum | diff | product | count; it is recomputed here, and each `from` value must itself be tied (1 or 2).
A scope sentence ("same method as last quarter", "same as for Atlas") needs `scope` in receipts.json: every condition of the method,
each with carried: true and the file that proves it.

    python -m jarvis.proof check <rid> <aid>      the verdict for one work entry (exit 1 when something is not tied)
    python -m jarvis.proof backfill               every work entry: how many claims, how many tied (nothing is changed)
    python -m jarvis.proof selftest
verdict(rid, aid) is what the Stop hook and the Slack draft step call; it caches by the text and the files' state.
"""
from jarvis import paths
import bisect, datetime as dt, glob, hashlib, json, os, re, sys

HUB = str(paths.home())
OUTS = os.path.join(HUB, 'work', 'outputs')
CACHE = os.path.join(HUB, 'state', 'proof')
MODE = os.path.join(HUB, 'state', 'proof_gate.json')          # {"mode": "enforce" | "observe"}; missing = enforce
EXT = ('.md', '.csv', '.tsv', '.json', '.jsonl', '.txt', '.log', '.out', '.html', '.sql', '.py', '.ipynb', '.yaml', '.yml')
MAX_FILE, MAX_FILES = 8_000_000, 600

# ------------------------------------------------------------------ claims
_STRIP = [
    r'```.*?```', r'`[^`\n]*`',                                   # code
    r'\[\[[^\]\n]*\]\]', r'\]\([^)\n]*\)', r'https?://\S+',       # links
    r'(?<![\w])[~.]?/[\w./~-]+', r'\b[\w.-]+\.(?:py|csv|json|md|sql|html|ipynb|xlsx|pptx|docx|txt|log|parquet|png|pdf)\b',   # paths, files
    r'\b20\d\d-\d\d-\d\d-\d{4,6}(?:-[a-z]+)?\b',             # a round-up id: 2026-10-05-1213-all
    r'\b[A-Z][A-Z0-9]{1,9}-\d+\b(?:\s*(?:\([^)\n]{0,40}\))?\s*[,;/&]\s*(?:and\s+)?\d{2,5}\b(?!\s?(?:%|k\b|rows|lines|parts|items|tests)))+',   # ABC-540, 514; 613
    r'\b20\d\d-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d)?)?\b', r'\b\d{1,2}:\d\d(?::\d\d)?\b',                 # dates, times
    r'\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?(?:\s+20\d\d)?\b',
    r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d\d)?\b',
    r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+20\d\d\b',
    r'\b[A-Z][A-Z0-9]{1,9}-\d+\b', r'#\d+\b', r'\bPR\s?\d+\b',    # tickets, PRs
    r'\b[A-Za-z]+_\d[\w]*\b', r'\b[A-Z]{1,4}\d{2,}\b',            # T1_20, PL04, RX11
    r'\bv\d+(?:\.\d+)*[a-z]?\b', r'\b\d+(?:\.\d+){2,}\b',         # versions
    r'\b[0-9a-f]{7,40}\b(?<![0-9]{7})',                            # commit hashes (with a letter in them)
    r'§\s?\d+[a-z]?', r'\b(?:step|section|row|page|line|item|point|phase|wave|pass|week|day|q)\s?\d+[a-z]?\b',
]
_STRIP_RX = re.compile('|'.join(f'(?:{p})' for p in _STRIP), re.I | re.S)
_NUM = re.compile(r'(?<![\w.,])(?P<cur>[$£€₹]\s?)?(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<suf>\s?%|\s?(?:k|K|m|M|mn|bn|B)\b|x\b)?(?![\w]|\.\d)')
_SCALE = {'k': 1e3, 'm': 1e6, 'mn': 1e6, 'b': 1e9, 'bn': 1e9}


_RANGE = re.compile(r'(?<![\w.,])(?P<c>[$£€₹]?)(?P<a>\d[\d,]*(?:\.\d+)?)(?P<sep>\s?(?:-|–|to)\s?)(?P<c2>[$£€₹]?)(?P<b>\d[\d,]*(?:\.\d+)?)'
                    r'(?P<suf>\s?%|k\b|K\b|m\b|M\b|mn\b|bn\b|B\b|x\b)')
_APPROX = re.compile(r'(?:~|≈|\babout|\baround|\broughly|\bapprox(?:\.|imately)?|\bnearly|\balmost|\bsome|\bcirca|\bc\.)\s*[$£€₹]?\s*$', re.I)


def claims(text):
    """Numeric claims in a text: [{raw, value, dec, pct, scale, approx, ctx}]."""
    text = _RANGE.sub(lambda m: f"{m.group('c')}{m.group('a')}{m.group('suf').strip()}{m.group('sep')}{m.group('c2')}{m.group('b')}{m.group('suf')}",
                      str(text or ''))                     # "$175-178k" means $175k to $178k
    t = _STRIP_RX.sub(lambda m: ' ' * len(m.group(0)), text)
    out = []
    for m in _NUM.finditer(t):
        raw_n = m.group('n')
        suf = (m.group('suf') or '').strip()
        cur = bool(m.group('cur'))
        digits = raw_n.replace(',', '')
        dec = len(digits.split('.')[1]) if '.' in digits else 0
        v = float(digits)
        unit = cur or bool(suf)
        if not unit and not dec:
            if len(digits) < 3:                            # a bare small whole number: "3 files", "2 runs"
                continue
            if ',' not in raw_n and re.fullmatch(r'20[0-3]\d', digits):   # a year
                continue                                   # ("1,904" is a count, not the year 1904)
            if len(digits) >= 8 and ',' not in raw_n:      # a long id, a page id, a timestamp
                continue
        sl = suf.lower()
        approx = bool(_APPROX.search(t[max(0, m.start() - 16): m.start()]))
        zeros = len(digits) - len(digits.rstrip('0')) if not dec else 0
        out.append({'raw': m.group(0).strip(), 'value': v, 'dec': dec, 'pct': sl == '%', 'scale': _SCALE.get(sl, 1.0),
                    'approx': approx, 'zeros': zeros if approx else 0,
                    'ctx': re.sub(r'\s+', ' ', str(text)[max(0, m.start() - 50): m.end() + 40]).strip()})
    return out


# ------------------------------------------------------------------ evidence
_EV = re.compile(r'(?<![\w.])-?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)')


def _numbers_in(text):
    out = []
    for m in _EV.finditer(text):
        try:
            out.append(float(m.group(1).replace(',', '')))
        except ValueError:
            pass
    return out


def _read(f):
    try:
        t = open(f, errors='ignore').read()
    except OSError:
        return ''
    if f.endswith('.ipynb'):                               # outputs and sources, not the base64 images
        try:
            nb = json.loads(t)
            parts = []
            for c in nb.get('cells') or []:
                parts.append(''.join(c.get('source') or []))
                for o in c.get('outputs') or []:
                    parts.append(''.join(o.get('text') or []) if isinstance(o.get('text'), list) else str(o.get('text') or ''))
                    d = o.get('data') or {}
                    for k in ('text/plain', 'text/html'):
                        parts.append(''.join(d.get(k) or []) if isinstance(d.get(k), list) else str(d.get(k) or ''))
            return '\n'.join(parts)
        except ValueError:
            return ''
    return t


def folders(rid, aid, w):
    cited = [d.get('path') for d in (w.get('done_work') or []) if isinstance(d, dict)] + \
            [a.get('path') for a in ((w.get('reply') or {}).get('attachments') or []) if isinstance(a, dict)]
    ctx = w.get('context') or []
    ctx = [c.get('path') if isinstance(c, dict) else c for c in (ctx if isinstance(ctx, list) else [ctx])]
    out = set(glob.glob(os.path.join(OUTS, f'{rid}-{aid}*')))
    files = set()
    for p in cited + ctx:
        p = os.path.expanduser(str(p or ''))
        if not p or p.startswith('http') or '/' not in p:
            continue
        if os.path.isdir(p):
            out.add(p)
        elif os.path.isfile(p):
            files.add(p)
            if p.startswith(OUTS):                         # a file in another item's outputs: that item's folder counts
                out.add(os.path.dirname(p))
    return sorted(d for d in out if os.path.isdir(d)), sorted(files)


def evidence_files(rid, aid, w):
    """(the item's own files, a key of their names, sizes and times). No file is read."""
    dirs, files = folders(rid, aid, w)
    fs = list(files)
    for d in dirs:
        for f in glob.glob(os.path.join(d, '**', '*'), recursive=True):
            if os.path.isfile(f) and f.lower().endswith(EXT) and os.path.basename(f) != 'receipts.json' and '/.' not in f:
                fs.append(f)
    out, key = [], hashlib.sha1()
    for f in sorted(set(fs))[:MAX_FILES]:
        try:
            st = os.stat(f)
        except OSError:
            continue
        if st.st_size <= MAX_FILE:
            out.append(f)
            key.update(f'{f}:{st.st_size}:{int(st.st_mtime)}'.encode())
    return out, key.hexdigest()


def evidence(rid, aid, w):
    """(sorted values, files read, state key) from the item's own files. receipts.json is read apart."""
    fs, key = evidence_files(rid, aid, w)
    vals = []
    for f in fs:
        vals += _numbers_in(_read(f))
    vals.sort()
    return vals, fs, key


def _in(vals, lo, hi):
    i = bisect.bisect_left(vals, lo)
    return i < len(vals) and vals[i] < hi


def _ranges(c):
    """The value ranges that tie this claim: only the rounding its text shows."""
    half = 0.5 * 10 ** (-c['dec'])
    if c.get('zeros'):                                     # "~1,800" is a rounded figure: tied by 1,750-1,849
        half = 0.5 * 10 ** c['zeros']
    v, s = c['value'], c['scale']
    r = [((v - half) * s, (v + half) * s)]
    if c['pct']:
        r.append(((v - half) / 100, (v + half) / 100))
    if c['dec'] == 0 and s == 1.0 and not c.get('zeros'):
        r[0] = (v - 1e-9, v + 1e-9)                        # a whole number is tied only by itself
    return r


def tied_by_files(c, vals):
    return any(_in(vals, lo, hi) for lo, hi in _ranges(c))


# ------------------------------------------------------------------ receipts
def _receipts(rid, aid):
    out = {'items': [], 'scope': None, 'path': None, 'error': None}
    for d in glob.glob(os.path.join(OUTS, f'{rid}-{aid}*')):
        p = os.path.join(d, 'receipts.json')
        if os.path.isfile(p):
            out['path'] = p
            try:
                r = json.load(open(p))
            except ValueError as e:
                out['error'] = f'receipts.json is not valid JSON ({e})'
                continue
            if isinstance(r, list):
                out['items'] += [x for x in r if isinstance(x, dict)]
            elif isinstance(r, dict):
                out['items'] += [x for x in (r.get('receipts') or []) if isinstance(x, dict)]
                out['scope'] = r.get('scope') if isinstance(r.get('scope'), list) else out['scope']
    return out


def _file_has(path, value, dec=0, base=None):
    p = os.path.expanduser(str(path or ''))
    if base and not os.path.isabs(p):
        p = os.path.join(base, p)
    if not os.path.isfile(p):
        return None                                        # the source does not exist
    vals = sorted(_numbers_in(_read(p)))
    half = 0.5 * 10 ** (-dec) if dec else 1e-9
    return _in(vals, value - half, value + half)


def _recompute(op, xs):
    try:
        if op == 'ratio':
            return xs[0] / xs[1]
        if op == 'sum':
            return sum(xs)
        if op == 'diff':
            return xs[0] - xs[1]
        if op == 'product':
            p = 1.0
            for x in xs:
                p *= x
            return p
    except (ZeroDivisionError, IndexError):
        return None
    return None


def tied_by_receipt(c, rec, vals):
    """(True, how) or (False, why) for the receipts that name this claim's value."""
    why = ''
    base = os.path.dirname(rec['path']) if rec.get('path') else None
    for r in rec['items']:
        try:
            rv = float(str(r.get('value')).replace(',', ''))
        except (TypeError, ValueError):
            continue
        if not any(lo <= rv * (c['scale'] if r.get('unscaled') else 1) < hi or lo <= rv < hi for lo, hi in _ranges(c)) and \
                not (c['pct'] and any(lo <= rv / 100 < hi for lo, hi in _ranges(c))):
            continue
        op, frm = r.get('op'), r.get('from')
        if op in ('ratio', 'sum', 'diff', 'product') and isinstance(frm, list) and len(frm) >= 2:
            try:
                xs = [float(str(x).replace(',', '')) for x in frm]
            except (TypeError, ValueError):
                why = f"receipt for {c['raw']}: `from` holds something that is not a number"; continue
            got = _recompute(op, xs)
            if got is None:
                why = f"receipt for {c['raw']}: {op} of {frm} cannot be computed"; continue
            if not any(lo <= g < hi for lo, hi in _ranges(c) for g in ((got, got * 100) if c['pct'] else (got,))):
                why = f"receipt for {c['raw']}: {op} of {frm} is {got:.6g}, not {c['raw']}"; continue
            loose = [x for x in xs if not (_in(vals, x - 1e-9, x + 1e-9) or _file_has(r.get('source'), x, 0, base))]
            if loose:
                why = f"receipt for {c['raw']}: its inputs {loose} are in no output file"; continue
            return True, f'{op} of {frm}'
        has = _file_has(r.get('source'), rv, c['dec'], base)
        if has is None:
            why = f"receipt for {c['raw']}: its source {r.get('source')!r} does not exist"; continue
        if not has:
            why = f"receipt for {c['raw']}: {os.path.basename(str(r.get('source')))} does not hold {rv:g}"; continue
        return True, f"in {os.path.basename(str(r.get('source')))}"
    return False, why


SCOPE = re.compile(r'\bsame\s+(?:method|approach|rules?|settings?|logic|policy|conditions?)\b|'
                   r'\bsame as (?:for |in |at |on )(?:the )?(?-i:[A-Z])[\w-]*|'           # "same as for Atlas": a named case
                   r'\bas (?:we did |done )(?:for|in|at|on) (?:the )?(?-i:[A-Z])[\w-]*|\bidentical (?:method|approach|settings?)\b', re.I)


def scope_gaps(texts, rec):
    """A sentence that says a method was carried over needs every condition ticked, each with its proof."""
    said = [m.group(0) for _, t in texts for m in SCOPE.finditer(str(t or ''))]
    if not said:
        return []
    sc = rec.get('scope')
    if not sc:
        return [f'the text says "{said[0]}" but receipts.json has no `scope` list: write every condition of that method '
                f'(filters, inputs, date windows, what was included and what was left out), each with carried and proof']
    base = os.path.dirname(rec['path']) if rec.get('path') else ''
    out = []
    for x in sc:
        if not isinstance(x, dict) or not str(x.get('condition') or '').strip():
            out.append('a `scope` entry has no condition'); continue
        if x.get('carried') is not True:
            out.append(f"scope condition not carried: {x['condition']} (say so in the text, or carry it)")
            continue
        p = os.path.expanduser(str(x.get('proof') or ''))
        if not p or not os.path.exists(p if os.path.isabs(p) else os.path.join(base, p)):
            out.append(f"scope condition {x['condition']!r} has no proof file that exists")
    return out


# ------------------------------------------------------------------ the check
def texts_of(w):
    """(where, text) for everything in a work entry that people read."""
    out = []
    r = w.get('reply') or {}
    if str(r.get('text') or '').strip() and not r.get('dropped') and not w.get('merged_into'):
        out.append(('reply', r['text']))
    if str(w.get('summary') or '').strip():
        out.append(('summary', w['summary']))
    for p in w.get('points') or []:
        if isinstance(p, dict):
            for k in ('answer', 'note'):
                if str(p.get(k) or '').strip():
                    out.append((f"point {p.get('key') or ''} {k}".strip(), p[k]))
    for i, n in enumerate(w.get('numbers') or []):
        if isinstance(n, dict):
            vals = ' ; '.join(str(v) for k, v in n.items() if k not in ('what', 'source', 'unit', 'note', 'scope', 'cutoff') and isinstance(v, (str, int, float)))
            if vals.strip():
                out.append((f"numbers[{i}] {str(n.get('what') or '')[:40]}".strip(), vals))
    if str(w.get('jira_comment_draft') or '').strip():
        out.append(('jira comment', w['jira_comment_draft'] if isinstance(w['jira_comment_draft'], str) else json.dumps(w['jira_comment_draft'])))
    return out


def check(rid, aid, w=None):
    if w is None:
        try:
            w = json.load(open(os.path.join(HUB, 'work', rid + '.json'))).get(aid) or {}
        except (OSError, ValueError):
            w = {}
    texts = texts_of(w)
    found = [(where, c) for where, t in texts for c in claims(t)]
    vals, files, _ = evidence(rid, aid, w) if found else ([], [], '')
    rec = _receipts(rid, aid)
    untied, seen, n_tied = [], set(), 0
    for where, c in found:
        k = (round(c['value'], 6), c['pct'], c['scale'], c['dec'])
        if k in seen:                                      # the same figure twice is one claim
            continue
        seen.add(k)
        if tied_by_files(c, vals):
            n_tied += 1; continue
        ok, why = tied_by_receipt(c, rec, vals)
        if ok:
            n_tied += 1; continue
        untied.append({'where': where, 'raw': c['raw'], 'ctx': c['ctx'], 'why': why})
    gaps = scope_gaps(texts, rec)
    if rec.get('error'):
        gaps.append(rec['error'])
    return {'claims': len(seen), 'tied': n_tied, 'untied': untied, 'scope': gaps, 'files': len(files), 'receipts': len(rec['items']),
            'ok': not untied and not gaps, 'at': dt.datetime.now().astimezone().isoformat(timespec='seconds')}


def mode():
    try:
        return json.load(open(MODE)).get('mode') or 'enforce'
    except (OSError, ValueError):
        return 'enforce'


def verdict(rid, aid, w=None):
    """check(), cached by what was written and the state of the files it rests on."""
    if w is None:
        try:
            w = json.load(open(os.path.join(HUB, 'work', rid + '.json'))).get(aid) or {}
        except (OSError, ValueError):
            w = {}
    _, fkey = evidence_files(rid, aid, w)
    rp = [p for d in glob.glob(os.path.join(OUTS, f'{rid}-{aid}*')) for p in [os.path.join(d, 'receipts.json')] if os.path.isfile(p)]
    key = hashlib.sha1((json.dumps(texts_of(w), sort_keys=True) + fkey + ''.join(f'{p}{os.path.getmtime(p)}' for p in rp)).encode()).hexdigest()
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, re.sub(r'[^\w.-]', '_', f'{rid}-{aid}') + '.json')
    try:
        d = json.load(open(cp))
        if d.get('key') == key:
            return d
    except (OSError, ValueError):
        pass
    d = check(rid, aid, w)
    d['key'] = key
    json.dump(d, open(cp + '.tmp', 'w'), indent=1); os.replace(cp + '.tmp', cp)
    return d


def say(d, n=8):
    """The reason, in the worker's words, for the Stop hook."""
    lines = []
    if d['untied']:
        lines.append(f"{len(d['untied'])} of {d['claims']} numbers are tied to no output file (The owner's rule of 10 Nov: every number is "
                     f"computed from an output before it is written). For each: save the output that holds it in this item's "
                     f"outputs folder, or add a receipt to receipts.json there ({{\"claim\", \"value\", \"source\"}}, or "
                     f"{{\"claim\", \"value\", \"op\": \"ratio|sum|diff|product\", \"from\": [...], \"source\"}}), or take the number out "
                     f"and say what is not known. Never round by eye or write \"about\" without the computed value behind it.")
        for u in d['untied'][:n]:
            lines.append(f"   - {u['raw']} in the {u['where']}: \"...{u['ctx'][:110]}...\"" + (f" ({u['why']})" if u['why'] else ''))
    for g in d['scope'][:4]:
        lines.append('   - ' + g)
    return '\n'.join(lines)


# ------------------------------------------------------------------ command line
def backfill():
    tot = {'entries': 0, 'with_claims': 0, 'claims': 0, 'tied': 0, 'clean': 0, 'reply': 0, 'reply_clean': 0}
    worst = []
    for f in sorted(glob.glob(os.path.join(HUB, 'work', '20*.json'))):
        rid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        for aid, w in d.items():
            if not isinstance(w, dict) or aid.startswith('_'):
                continue
            tot['entries'] += 1
            r = check(rid, aid, w)
            if not r['claims']:
                continue
            tot['with_claims'] += 1; tot['claims'] += r['claims']; tot['tied'] += r['tied']; tot['clean'] += r['ok']
            if any(wh == 'reply' for wh, _ in texts_of(w)):
                tot['reply'] += 1
                tot['reply_clean'] += not any(u['where'] == 'reply' for u in r['untied'])
            if r['untied']:
                worst.append((len(r['untied']), rid, aid, r))
    return tot, sorted(worst, reverse=True)


def selftest():
    c = lambda t: [x['raw'] for x in claims(t)]
    s = 'On-time went from 60.1% to 89.1% on 212 orders, saving $131k (about 3k lines).'
    assert c(s) == ['60.1%', '89.1%', '212', '$131k', '3k'], c(s)
    assert c('ABC-752 on 2026-03-10 at 10:15, part 40012345, PL04, T1_20, v15b, PR #30, 3 files, in 2026.') == []
    assert c('see `SELECT 123456` and [[note 2024 300]] and https://x.io/a/9999 and /tmp/run_500/out.csv') == []
    assert c('12 Mar 2026: 412 rows; on Mar 3, 2026 we loaded 1,904.') == ['412', '1,904']
    assert c('cost fell by 2.4x and 1.35x; 61,208 transfers; 12.5 % failed') == ['2.4x', '1.35x', '61,208', '12.5 %']
    s = 'holding about $110k vs $135-138k; days late 3,011-3,154'
    assert c(s) == ['$110k', '$135k', '138k', '3,011', '3,154'], c(s)
    assert c('open: ABC-540, 514 (Testing); 613, 629, 652 (in progress); see 2026-10-05-1213-all a1') == []
    assert c('ABC-540, 514 rows changed') == ['514'] or c('ABC-540, 514 rows changed') == []
    ap = claims('~250 of ~1,800 lines, about 1,754 exactly')
    assert [x['zeros'] for x in ap] == [1, 2, 0], [(x['raw'], x['zeros']) for x in ap]
    assert tied_by_files(ap[0], [247.0]) and not tied_by_files(ap[0], [256.0]) and tied_by_files(ap[1], [1754.0]) and not tied_by_files(ap[1], [1851.0])
    assert not tied_by_files(claims('1,800 lines')[0], [1754.0])                        # not marked as rounded: only 1800 ties it
    ev = sorted([0.40322581, 25.0, 62.0, 163890.0, 6014.0, 89.1, 412.0, 3012.0])
    t = lambda s: tied_by_files(claims(s)[0], ev)
    assert t('40.3%') and not t('40.4%') and t('$164k') and not t('$165k') and t('6,014') and not t('6,015')
    assert t('3k') and not t('4k') and t('89.1%') and t('412') and not t('413')
    rec = {'items': [{'claim': '40.3%', 'value': 40.3, 'op': 'ratio', 'from': [25, 62]}], 'scope': None, 'path': None}
    assert tied_by_receipt(claims('40.3%')[0], rec, [25.0, 62.0])[0]
    assert not tied_by_receipt(claims('40.3%')[0], rec, [25.0])[0]                    # an input in no file
    bad = {'items': [{'claim': '55%', 'value': 55, 'op': 'ratio', 'from': [25, 62]}], 'scope': None, 'path': None}
    assert not tied_by_receipt(claims('55%')[0], bad, [25.0, 62.0])[0]                # the arithmetic does not hold
    assert scope_gaps([('reply', 'The north site uses the same method as the south one.')], {'scope': None, 'path': None})
    assert scope_gaps([('reply', 'Sized the same as for Atlas.')], {'scope': None, 'path': None})
    assert not scope_gaps([('reply', 'It is the same as for last week, nothing new.')], {'scope': None, 'path': None})
    assert not scope_gaps([('reply', 'The north site was sized on its own settings.')], {'scope': None, 'path': None})
    print('proof: selftest passes')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['selftest']:
        selftest()
    elif a[:1] == ['check'] and len(a) == 3:
        d = check(a[1], a[2])
        print(json.dumps({k: v for k, v in d.items() if k != 'untied'}, indent=1))
        print(say(d) or 'every number is tied')
        sys.exit(0 if d['ok'] else 1)
    elif a[:1] == ['backfill']:
        tot, worst = backfill()
        print(json.dumps(tot))
        for n, rid, aid, r in worst[:int(a[1]) if len(a) > 1 else 5]:
            print(f"\n{rid} {aid}: {n} of {r['claims']} not tied ({r['files']} files, {r['receipts']} receipts)")
            for u in r['untied'][:6]:
                print(f"   {u['raw']:>10s} | {u['where'][:22]:22s} | {u['ctx'][:100]}")
    else:
        print(__doc__)
