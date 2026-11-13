#!/usr/bin/env python3
"""Grounding detector eval (HARNESS.md Phase 3: "the false-positive eval before grounding gates"). Plain code, no model.

Runs spine/speech_guard.state_claims over tools/fixtures/grounding_labels.json: made-up sentences of the kind Jarvis says,
labelled by hand with the state-claim kind (sent, replied, running, finished, draft, ready, waiting) or none; plus hand-written negatives
(future, conditional, questions, their assistant's own actions, a model or voice "running").
  recall           labelled claims it detects (any kind)
  kind accuracy    of those, the right kind
  false positives  sentences labelled none that it flags (what would wrongly hold a sentence once grounding gates)
Proof bar: false positives <= 2% before any gate. Exit 1 if above.
Run: python3 tools/grounding_eval.py [-v]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import json, os, sys

HUB = str(paths.home())
from jarvis.spine import speech_guard as g  # noqa: E402


def main(verbose=False):
    cases = json.load(open(os.path.join(str(paths.REPO), 'tools', 'fixtures', 'grounding_labels.json')))['cases']
    claims = [c for c in cases if c['kind'] not in (None, 'skip')]
    nones = [c for c in cases if c['kind'] is None]
    hit = right = 0
    miss, fps = [], []
    for c in claims:
        k = [x[0] for x in g.state_claims(c['s'])]
        if k:
            hit += 1
            right += c['kind'] in k
        else:
            miss.append(c)
    for c in nones:
        k = g.state_claims(c['s'])
        if k:
            fps.append((c, k))
    fp_rate = len(fps) / max(1, len(nones))
    print(f"recall {hit}/{len(claims)} ({hit / max(1, len(claims)):.0%}); kind right {right}/{hit}; "
          f"false positives {len(fps)}/{len(nones)} ({fp_rate:.1%}, bar <= 2%)")
    if verbose:
        for c in miss:
            print('  MISS', c['kind'], '|', c['s'][:140])
        for c, k in fps:
            print('  FP  ', k, '|', c['s'][:140])
    return fp_rate <= 0.02, {'recall': hit / max(1, len(claims)), 'fp_rate': fp_rate, 'n_claims': len(claims), 'n_none': len(nones)}


if __name__ == '__main__':
    ok, _ = main('-v' in sys.argv)
    sys.exit(0 if ok else 1)
