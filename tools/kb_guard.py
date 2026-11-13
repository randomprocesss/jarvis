"""Safeguard 4 of the KB plan (16 Oct 2026): a rewritten note may never drop what the old one held.

Every rewrite of a knowledge-base note goes through guarded_write():
  1. the old note is copied to versions/<name>.<YYYYmmdd-HHMMSS>.md (in the knowledge base) (the vault mirrors only *.md (in the knowledge base) and
     topics/*.md, so versions never reach Obsidian);
  2. every fact of the old note must still be in the new one: numbers (2+ digits, decimals, percentages, money), table names and
     other upper-case codes, Jira keys and [[citations]]. A replaced value stays under "Superseded" with its reason, so it
     is still in the note. Front matter and single digits are not counted;
  3. otherwise the write is refused and the old note stays; the caller may retry once with the list of what went missing.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import datetime as dt, os, re, shutil

HUB = str(paths.home())
VERSIONS = os.path.join(str(paths.kb()), 'versions')


def _body(t):
    return t.split('---\n', 2)[2] if t.startswith('---\n') and t.count('---\n') >= 2 else t


def facts(text):
    b = _body(text)
    nums = set(re.findall(r'\$?\d[\d,]*\.\d+%?|\$?\d{2,}(?:,\d{3})*%?|\d%', b))
    codes = set(re.findall(r'\b[A-Z][A-Z0-9_]{1,}(?:-\d+)?\b', b))
    cites = set(re.findall(r'\[\[([^\]|#]+)', b))
    return {('n', x.rstrip('.,')) for x in nums} | {('c', x) for x in codes} | {('l', x.strip()) for x in cites}


def missing(old, new):
    lost = facts(old) - facts(new)
    return sorted(v for _, v in lost)


def save_version(path):
    if not os.path.exists(path):
        return None
    os.makedirs(VERSIONS, exist_ok=True)
    rel = os.path.relpath(path, str(paths.kb())).replace(os.sep, '__')
    dest = os.path.join(VERSIONS, f"{rel[:-3] if rel.endswith('.md') else rel}.{dt.datetime.now():%Y%m%d-%H%M%S}.md")
    shutil.copy2(path, dest)
    return dest


def guarded_write(path, new_text):
    """Returns (written, lost). Writes only when nothing of the old note is lost; the old note is versioned first."""
    old = open(path, errors='replace').read() if os.path.exists(path) else ''
    lost = missing(old, new_text) if old else []
    if lost:
        return False, lost
    save_version(path)
    tmp = path + '.tmp'
    open(tmp, 'w').write(new_text)
    os.replace(tmp, path)
    return True, []


def carry_over(old, lost):
    """The old note's lines that hold the lost facts, one line per fact, in note order (22 Oct: a rewrite that drops a
    handful of facts keeps them this way instead of paying for a second full rewrite)."""
    lines, out = _body(old).splitlines(), []
    for v in lost:                                        # 23 Oct: the fact as the guard reads it ("1%" is not in "11%")
        hit = next((l for l in lines if v in l and l.strip() and v in {x for _, x in facts(l)}), None)
        if hit and hit not in out:
            out.append(hit)
    return '\n'.join(sorted(out, key=lines.index))


def retry_note(lost):
    """The line a caller adds to its prompt for the one retry."""
    return ('YOUR LAST REWRITE WAS REFUSED because it dropped facts the old note held: ' + ', '.join(lost[:80]) +
            '. Return the whole note again with every one of them kept (a replaced value goes under "Superseded" with its reason).\n\n')
