"""No secrets in the knowledge base. Archived chats and raw documents hold database connection strings, passwords,
tokens and keys, and everything in the knowledge base is reachable through the search index.

Redacts, in place, every match of the secret patterns (jarvis/screens.py) in the knowledge base's own copies: the chat
archive (record by record, inside the parsed JSON, so the transcripts stay valid), the raw units (sources_extra,
sources_extra_later, sources_raw) and the notes. A connection string keeps its scheme, host and database and loses only
"user:password@"; a private key goes through its END line; every other match becomes "[redacted <pattern>]". Values are
never printed. Claude Code's own transcripts are not touched. archive_sync calls redact_file() on every copy, so a
re-copied chat cannot bring a secret back.

    python tools/kb_redact.py run      redact everything, then print counts per pattern
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import collections, glob, json, os, re, sys

HUB = str(paths.home())
_PATS = None
PEM = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)', re.S)
CRED = re.compile(r'(//)[^/\s:@]+:[^@\s/]+@')


def pats():
    global _PATS
    if _PATS is None:
        from jarvis import screens
        _PATS = screens.secret_patterns()
    return _PATS


def redact(text, counts=None):
    """The text with its secrets taken out; counts (a Counter) gets one per redaction by pattern name."""
    if not isinstance(text, str) or not text:
        return text
    new = PEM.sub(lambda m: (counts.update(['private_key']) if counts is not None else None) or '[redacted private_key]', text)
    for name, rx in pats():
        if name == 'private_key':
            continue
        if name == 'conn_string':
            def keep_host(m):
                s = m.group(0)
                t = CRED.sub(r'\1[redacted credentials]@', s)
                if t != s and counts is not None:
                    counts.update([name])
                return t
            new = rx.sub(keep_host, new)
        else:
            def gone(m, name=name):
                if counts is not None:
                    counts.update([name])
                return f'[redacted {name}]'
            new = rx.sub(gone, new)
    return new


def _walk(o, counts):
    if isinstance(o, str):
        return redact(o, counts)
    if isinstance(o, list):
        return [_walk(x, counts) for x in o]
    if isinstance(o, dict):
        return {k: _walk(v, counts) for k, v in o.items()}
    return o


def redact_file(path, counts=None):
    """Redact one file in place; a .jsonl transcript record by record. Returns the number of redactions."""
    c = collections.Counter()
    try:
        text = open(path, errors='replace').read()
    except OSError:
        return 0
    if path.endswith('.jsonl'):
        out = []
        for line in text.split('\n'):
            if not line.strip():
                out.append(line); continue
            try:
                rec = json.loads(line)
            except ValueError:
                out.append(redact(line, c)); continue
            new = _walk(rec, c)
            out.append(json.dumps(new, ensure_ascii=False) if new != rec else line)
        new_text = '\n'.join(out)
    else:
        new_text = redact(text, c)
    if new_text != text:
        tmp = path + '.tmp'; open(tmp, 'w').write(new_text); os.replace(tmp, path)
    if counts is not None:
        counts.update(c)
    return sum(c.values())


def run():
    files = glob.glob(str(paths.brain('raw_archive', 'claude_projects', '**', '*.jsonl')), recursive=True)
    for d in paths.kb_text_dirs():
        files += [f for f in glob.glob(os.path.join(str(d), '**', '*'), recursive=True) if os.path.isfile(f) and '/versions/' not in f.replace(os.sep, '/')]
    total, changed = collections.Counter(), 0
    for f in files:
        changed += bool(redact_file(f, total))
    print(f'{changed} files redacted; redactions by pattern: {dict(total)}')
    return changed, dict(total)


if __name__ == '__main__':
    if sys.argv[1:2] == ['run']:
        run()
    else:
        print(__doc__)
