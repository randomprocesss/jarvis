"""The screens every copy of the owner's text passes before it is kept, searched or mirrored: no secrets, nothing personal.

  secrets   tools/vault_secret_patterns.txt, shipped with the code: one "name<TAB>regex" per line (passwords, keys, tokens,
            connection strings). tools/kb_secret_patterns_extra.txt adds the stricter ones the knowledge-base scan uses.
  words     the owner's own personal words, one per line, in state/personal_words.txt in their data home (people, places,
            anything that must never leave a private note). Never shipped, never printed: a hit names the word only to them.

Nothing here prints a match. A text that hits a screen is held back or has the match replaced; the value itself never
appears in a log, a note or a message.
"""
import os, re
from jarvis import paths

SECRETS = str(paths.tool('vault_secret_patterns.txt'))
SECRETS_EXTRA = str(paths.tool('kb_secret_patterns_extra.txt'))
ALLOWED = ('work', 'jarvis', 'setup')                # the scopes that may enter the knowledge base


def words_file():
    return str(paths.state('personal_words.txt'))


def _read(path):
    try:
        return open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return ''


def patterns(path, words=False):
    """[(name, compiled)] from a pattern file. words=True: each line is a plain word or phrase, matched as a whole word."""
    out = []
    for line in _read(path).splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        if words:
            out.append((line.strip(), re.compile(r'(?<![A-Za-z0-9])' + re.escape(line.strip()) + r'(?![A-Za-z0-9])', re.I)))
        elif '\t' in line:
            name, rx = line.split('\t', 1)
            try:
                out.append((name, re.compile(rx, re.I)))
            except re.error:
                continue                             # a broken line in a pattern file never stops the screen
    return out


def secret_patterns(extra=False):
    return patterns(SECRETS) + (patterns(SECRETS_EXTRA) if extra else [])


def personal_words():
    return patterns(words_file(), words=True)


def has_secret(text, pats=None):
    """The name of the first secret pattern the text matches, or ''."""
    for name, rx in (pats if pats is not None else secret_patterns()):
        if rx.search(text):
            return name
    return ''


def screen(text, scope, secrets=None, words=None):
    """'' when the text may be kept; otherwise why it is held back (never the matched value)."""
    if scope is None:
        return 'waiting for its scope label'
    if scope not in ALLOWED:
        return f'scope {scope}'
    name = has_secret(text, secrets)
    if name:
        return f'secret pattern {name}'
    for w, rx in (words if words is not None else personal_words()):
        if rx.search(text):
            return f'personal word "{w}"'
    return ''
