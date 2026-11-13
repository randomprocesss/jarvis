"""What Jarvis never shows on a screen that may be shared: the names of the owner's organisation and its mail domains.
They are settings (org.names, org.domains), so the code names no employer and the list changes without a code change.
Python reads them here; the page gets the same list from GET /presenter.js, loaded before its own code. With nothing
listed, nothing is stripped. Nothing here prints: the lookup server speaks JSON on its standard output."""
import json, re
from jarvis import config

NOTHING = re.compile(r'(?!x)x')


def load():
    """([words], [domains]) from settings."""
    c = config.load()
    clean = lambda key: [w for w in (' '.join(str(x).split()) for x in c.get(key) or []) if w]
    return clean('org.names'), clean('org.domains')


def org_words():
    words, _ = load()
    # letters/digits around the word, not \b: "X_Release_Notes" has no word boundary at the underscore
    return re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(re.escape(w) for w in words) + r')(?![A-Za-z0-9])', re.I) if words else NOTHING


def js():
    words, domains = load()
    return 'window.__WH_PRESENTER=' + json.dumps({'words': words, 'domains': domains}) + ';'
