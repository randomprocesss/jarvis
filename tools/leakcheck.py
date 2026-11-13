#!/usr/bin/env python3
"""Leak check: nothing private may sit in this repository, in any file or any commit.

It looks for two kinds of thing:
  1. Private words: names, organisations, projects, account ids. They come from a word list that is kept OUTSIDE the
     repository (--words FILE, or the JARVIS_PRIVATE_WORDS environment variable). The list never ships; this checker does.
  2. Shapes that are private whatever the words are: a home folder path, an e-mail address, a secret-shaped string
     (a token, a private key), a long account id.

    leakcheck.py PATH                      scan every file under PATH
    leakcheck.py PATH --history            also every commit (changes, messages, author names and addresses)
    leakcheck.py --files-from LIST         scan the files named in LIST (one path per line)
    leakcheck.py PATH --json OUT           write the full result as JSON

Exit code 0 only when nothing was found. Standard library only.

Word list format: one entry per line. "[name]" starts a category. "re:..." is a regular expression. Anything else is a
word or phrase, matched as a whole word (ignoring case when it has 5 letters or more). "#" starts a comment.
"""
import argparse, json, os, re, subprocess, sys

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.next'}
SKIP_FILES = re.compile(r'(package-lock\.json|\.lock)$')
MAX_BYTES = 8_000_000

# shapes that are private whatever the words are (written so that this file does not match itself)
SHAPES = {
    'home path': re.compile(r'/(?:Users|home)/(?!you\b|me\b|name\b|runner\b|Shared\b)[A-Za-z0-9._-]{2,}'),
    'e-mail address': re.compile(r'\b[A-Za-z0-9._%+-]+@(?!example\.(?:com|org)\b|users\.noreply\.github\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    'secret-shaped string': re.compile(
        r'xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}'
        r'|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b_auth\s*=\s*[A-Za-z0-9+/=]{16,}|AIza[0-9A-Za-z_-]{30,}|ATATT[A-Za-z0-9_=-]{20,}'),
    'long id': re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b[0-9a-f]{40,}\b'),
}


ESCAPES = re.compile(r'\\[bBnrtswdSWDAZ]')        # \b, \n, \s and the like: two characters that are not part of a word


def load_words(path):
    """{category: [compiled pattern]} from the private word list."""
    cats, cur = {}, 'private word'
    for line in open(path, errors='replace'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.fullmatch(r'\[(.+)\]', line)
        if m:
            cur = m.group(1).strip()
            continue
        if line.startswith('re:'):
            rx = re.compile(line[3:])
        else:
            flags = re.I if len(re.sub(r'[^A-Za-z]', '', line)) >= 5 else 0
            rx = re.compile(r'(?<![A-Za-z0-9])' + re.escape(line).replace(r'\ ', r'\s+') + r'(?![A-Za-z0-9])', flags)
        cats.setdefault(cur, []).append(rx)
    return cats


def scan_text(text, cats, where, hits, shapes=True):
    """Add every match in `text` to hits as (category, where, line number, matched text)."""
    lines = None
    def lineno(pos):
        nonlocal lines
        if lines is None:
            lines = [0]
            for m in re.finditer('\n', text):
                lines.append(m.end())
        lo, hi = 0, len(lines) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if lines[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1
    for cat, pats in cats.items():
        for rx in pats:
            for m in rx.finditer(text):
                hits.append((cat, where, lineno(m.start()), m.group(0)[:80]))
    # A word written inside a pattern or an escaped string (r'\bName\b', "\nName") has a letter right before it, so the
    # whole-word match above passes over it. The same text with those two-character escapes blanked is searched as well.
    plain = ESCAPES.sub('  ', text)
    if plain != text:
        seen = set(hits)
        for cat, pats in cats.items():
            for rx in pats:
                for m in rx.finditer(plain):
                    h = (cat, where, lineno(m.start()), m.group(0)[:80])
                    if h not in seen:
                        seen.add(h); hits.append(h)
    if shapes:
        for cat, rx in SHAPES.items():
            for m in rx.finditer(text):
                hits.append((cat, where, lineno(m.start()), m.group(0)[:80]))


def files_under(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in sorted(fn):
            yield os.path.join(dp, f)


def scan_files(paths, cats, root=''):
    hits, skipped, n = [], [], 0
    for p in paths:
        rel = os.path.relpath(p, root) if root else p
        try:
            if os.path.getsize(p) > MAX_BYTES:
                skipped.append((rel, 'over 8 MB')); continue
            raw = open(p, 'rb').read()
        except OSError as e:
            skipped.append((rel, str(e))); continue
        scan_text(rel, cats, rel + ' (file name)', hits, shapes=False)      # a private word in a file's own name counts
        if b'\x00' in raw[:4000]:
            skipped.append((rel, 'not text')); continue
        n += 1
        scan_text(raw.decode('utf-8', errors='replace'), cats, rel, hits, shapes=not SKIP_FILES.search(p))
    return hits, skipped, n


def scan_history(root, cats):
    """Every commit: what it changed, its message, and who it says wrote it."""
    def git(*a):
        return subprocess.run(['git', '-C', root, *a], capture_output=True, text=True, errors='replace').stdout
    hits = []
    commits = [c for c in git('rev-list', '--all').split() if c]
    for c in commits:
        scan_text(git('show', '--no-color', '--format=%an <%ae>%n%cn <%ce>%n%B', c), cats, 'commit ' + c[:10], hits)
    return hits, len(commits)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Nothing private in the repository: files and commits.')
    ap.add_argument('path', nargs='?', default='.')
    ap.add_argument('--words', default=os.environ.get('JARVIS_PRIVATE_WORDS', ''))
    ap.add_argument('--history', action='store_true')
    ap.add_argument('--files-from')
    ap.add_argument('--json')
    ap.add_argument('--show', type=int, default=25)
    a = ap.parse_args(argv)
    cats = load_words(a.words) if a.words else {}
    if a.files_from:
        paths = [l.strip() for l in open(a.files_from) if l.strip()]
        hits, skipped, n = scan_files(paths, cats)
    else:
        hits, skipped, n = scan_files(files_under(a.path), cats, a.path)
    ncommits = 0
    if a.history:
        h2, ncommits = scan_history(a.path, cats)
        hits += h2
    by = {}
    for cat, where, ln, text in hits:
        by.setdefault(cat, []).append((where, ln, text))
    print(f"{n} text files scanned" + (f", {ncommits} commits" if a.history else '') + f"; {len(skipped)} not scanned"
          + ('' if a.words else '; NO word list given: only the shapes were checked'))
    for cat in sorted(by, key=lambda c: -len(by[c])):
        v = by[cat]
        print(f"  {len(v):6}  {cat}  (in {len({w for w, _, _ in v})} places)")
    for where, ln, text in [x for c in by for x in by[c]][:a.show]:
        print(f"    {where}:{ln}  {text}")
    for rel, why in skipped[:10]:
        print(f"    not scanned: {rel} ({why})")
    if a.json:
        json.dump({'files': n, 'commits': ncommits, 'skipped': skipped, 'counts': {c: len(v) for c, v in by.items()},
                   'hits': [[c, *x] for c in by for x in by[c]]}, open(a.json, 'w'))
    total = len(hits)
    print('LEAK CHECK: ' + ('PASS, nothing found' if not total else f'FAIL, {total} found'))
    return 0 if not total else 1


if __name__ == '__main__':
    sys.exit(main())
