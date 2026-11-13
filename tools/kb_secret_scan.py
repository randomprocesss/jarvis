#!/usr/bin/env python3
"""Secret scan of the knowledge base. Patterns come from the shipped pattern files (jarvis/screens.py); never inline.
Prints file:line, the pattern name and the line with every match masked, so no secret value is ever shown.
    python tools/kb_secret_scan.py [dir ...]     (default: the knowledge base, its chat notes and its source notes)
Exit 1 when anything is found.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths, screens


def scan(dirs=None):
    """(files scanned, patterns, [(path, line number, pattern name, the line with each match masked)])."""
    pats = screens.secret_patterns(extra=True)
    dirs = dirs or [str(paths.brain('sources')), str(paths.kb()), str(paths.brain('sessions'))]
    hits, files = [], 0
    for d in dirs:
        for root, _, fs in os.walk(d):
            for f in fs:
                if not f.endswith(('.md', '.txt', '.json')):
                    continue
                p = os.path.join(root, f); files += 1
                try:
                    lines = open(p, errors='replace').read().splitlines()
                except OSError:
                    continue
                for i, line in enumerate(lines, 1):
                    for name, rx in pats:
                        if rx.search(line):
                            masked = rx.sub(lambda m: f'[MASKED {len(m.group(0))} chars]', line)
                            hits.append((p, i, name, masked[:220]))
    return files, len(pats), hits


if __name__ == '__main__':
    files, n, hits = scan(sys.argv[1:])
    print(f'{files} files scanned, {n} patterns, {len(hits)} hits')
    for p, i, name, masked in hits:
        print(f'{p}:{i} [{name}] {masked}')
    sys.exit(1 if hits else 0)
