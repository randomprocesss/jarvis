"""The things the knowledge base keeps a page for: tickets, pull requests, people, and any kind the owner names.

A kind is a name and a pattern. Built in:
  ticket   keys like ABC-123. Settings `knowledge.ticket_keys` (or the tracker's own projects) narrow them to the owner's
           projects; with no keys listed, any KEY-123 counts except the usual look-alikes (UTF-8, ISO-9001, ...).
  pr       owner/repo#12, and "<repository> ... PR 12" for the git repositories in the work folders.
  person   from the people notes of the knowledge base (found by tools/kb_props.py, not here).
The owner adds kinds in settings, `knowledge.entities`, one "kind = regular expression" per line; the first group of the
pattern (or the whole match) is the entity:

    [knowledge]
    entities = ["order = \\\\bSO-\\\\d{6}\\\\b", "client = \\\\b(Acme|Globex|Initech)\\\\b"]

A kind may have a list of known values, <knowledge base>/known/<kind>.json: {"value": "what it is"}, or
{"value": {"name": "...", "any other field": "..."}}. With a list, only matches in it count, and each page says what its
entity is from the list.
"""
import json
import os
import re

from . import config, paths

BUILT_IN = ('ticket', 'pr', 'person')
KEYS = {'ticket': 'tickets', 'pr': 'prs', 'person': 'people'}
FOLDERS = {'ticket': 'Tickets', 'pr': 'Pull requests', 'person': 'People mentions'}
# KEY-123 shapes that are not tickets, when the owner has not listed their ticket keys
NOT_TICKETS = frozenset('UTF ISO SHA MD RFC COVID GPT HTTP TLS SSL UTC GMT AES RSA IPV MP FY CY PY CVE IEEE ANSI DIN EN BS AS NS '
                        'RS USB HDMI WPA SARS HIV ICD NC CC BY PEP ECMA ES TS JS CSS HTML XML UTM EPSG WGS NAD TR SP'.split())
_TICKET = re.compile(r'(?<![A-Za-z0-9-])([A-Z][A-Z0-9]{1,9})-(\d{1,6})(?![A-Za-z0-9]|-\d)')
_PR = re.compile(r'(?<![\w/.-])([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)#(\d{1,6})\b')


def ticket_keys() -> list:
    """The ticket project keys that count as the owner's: knowledge.ticket_keys, plus the projects Jarvis may comment in."""
    c = config.load()
    keys = [str(k).strip().upper() for k in (c.get('knowledge.ticket_keys') or []) + (c.get('tracker.own_projects') or [])]
    return sorted({k for k in keys if re.fullmatch(r'[A-Z][A-Z0-9]{1,9}', k)})


def tickets(text: str) -> list:
    keys = set(ticket_keys())
    got = {f'{k}-{n}' for k, n in _TICKET.findall(text) if (k in keys if keys else k not in NOT_TICKETS)}
    return sorted(got, key=lambda x: (x.rsplit('-', 1)[0], int(x.rsplit('-', 1)[1])))


def repos() -> list:
    """The names of the git repositories in the owner's work folders."""
    out = []
    for d in [str(paths.workdir())] + [os.path.expanduser(str(x)) for x in config.load().get('work.folders') or []]:
        try:
            subs = sorted(os.listdir(d))
        except OSError:
            continue
        for repo in [d] + [os.path.join(d, s) for s in subs]:
            if os.path.isdir(os.path.join(repo, '.git')) and os.path.basename(repo) not in out:
                out.append(os.path.basename(repo))
    return out


def prs(text: str, names=None) -> list:
    got = {f'{r}#{n}' for r, n in _PR.findall(text)}
    names = repos() if names is None else names
    if names:
        rx = re.compile(r'(' + '|'.join(re.escape(r) for r in names) + r')[^\n]{0,60}?(?:\bPR\b|pull request|pull-requests/|pull/)\s*#?\s*(\d{1,6})\b')
        got |= {f'{r}#{n}' for r, n in rx.findall(text)}
    return sorted(got)


_CUSTOM = {}


def custom() -> dict:
    """{kind: compiled pattern} for the kinds the owner named in settings. A line that is not "kind = pattern", a kind that
    is built in, or a pattern that does not compile is left out."""
    lines = tuple(str(x) for x in config.load().get('knowledge.entities') or [])
    if _CUSTOM.get('lines') == lines:
        return _CUSTOM['kinds']
    out = {}
    for line in lines:
        kind, sep, rx = line.partition('=')
        kind = kind.strip().lower()
        if not sep or not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', kind) or kind in BUILT_IN:
            continue
        try:
            out[kind] = re.compile(rx.strip())
        except re.error:
            continue
    _CUSTOM.update(lines=lines, kinds=out)
    return out


def kinds() -> list:
    return list(BUILT_IN) + sorted(custom())


def key(kind: str) -> str:
    """The name of a kind's list in a note's properties: tickets, prs, people, and <kind>s for the owner's own kinds."""
    return KEYS.get(kind, kind + 's')


def pairs() -> list:
    return [(k, key(k)) for k in kinds()]


def folder(kind: str) -> str:
    return FOLDERS.get(kind, kind.replace('_', ' ').capitalize() + 's')


def title(kind: str, x: str, names=None) -> str:
    if kind == 'ticket':
        return x
    if kind == 'pr':
        return 'PR ' + x.replace('#', ' ')
    if kind == 'person':
        return 'Person ' + (names or {}).get(x, x)
    return f"{kind.replace('_', ' ').capitalize()} {x}"


def known(kind: str):
    """{value: what it is} from the knowledge base's known/<kind>.json, or None when the kind has no list."""
    try:
        d = json.load(open(os.path.join(str(paths.kb()), 'known', kind + '.json'), encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def find(text: str, names=None, lists=None) -> dict:
    """{properties key: sorted values} for every kind but people. `lists` ({kind: known values or None}) and `names`
    (repository names) may be passed by a caller that reads many texts, so they are loaded once."""
    out = {'tickets': tickets(text), 'prs': prs(text, names)}
    for kind, rx in custom().items():
        got = set()
        for m in rx.finditer(text):
            got.add(m.group(1) if rx.groups else m.group(0))
        lst = (lists or {}).get(kind) if lists is not None else known(kind)
        if lst is not None:
            got = {g for g in got if g in lst}
        out[key(kind)] = sorted(g for g in got if g)
    return out
