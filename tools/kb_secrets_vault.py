"""The secrets vault: what happens to a file of the knowledge base that holds a secret.

The knowledge base stays free of secrets: notes, the search index, a mirrored vault and Jarvis itself never see one. Its
own copies are always redacted, each secret replaced by "[redacted <kind>]". SECRETS_MAP.md lists every secret found by
kind, file, chat and date (and, for a database connection string, the host and database), never the value.

Keeping the original is the owner's choice (settings: knowledge.keep_secrets, off unless they turn it on). On: the file
is kept whole, unredacted, in state/secrets/ in the data home (readable by their user only; no worker may read it;
never indexed, mirrored or read by Jarvis). Off: no second copy of a secret is made; Claude Code's own transcript is the
only original, for as long as Claude Code keeps it.

This code never prints a secret. It copies files they already have; it does not enter secrets anywhere.

    python tools/kb_secrets_vault.py build     write the map, redact the knowledge base's copies, keep the originals if asked to
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import collections, datetime as dt, glob, json, os, re, shutil, sys

HUB = str(paths.home())
paths.use_tools()
import kb_redact as R  # noqa: E402

VAULT = str(paths.secrets())
B = os.path.join(HUB, 'brain')
ARCH = os.path.join(B, 'raw_archive', 'claude_projects')
ORIG = str(paths.claude_chats())
MARK = re.compile(r'\[redacted [a-z_ ]+(?:: kept in the secrets vault)?\]')


def _vault_path(f):
    return os.path.join(VAULT, os.path.relpath(f, B))


def has_secret(text):
    return bool(R.PEM.search(text)) or any(rx.search(text) for _, rx in R.pats())


def keeping():
    """Does the owner want whole copies kept? (settings: knowledge.keep_secrets)"""
    from jarvis import config
    return bool(config.load().get('knowledge.keep_secrets'))


def keep(f, text):
    """Save the whole, unredacted text of f in the vault (once; a later copy replaces it). Nothing when the owner did not
    ask for originals to be kept."""
    if not keeping():
        return
    p = _vault_path(f)
    os.makedirs(os.path.dirname(p), exist_ok=True, mode=0o700)
    open(p, 'w').write(text)
    os.chmod(p, 0o600)


def unredacted(f):
    """The file as it was before any redaction: the knowledge base's copy if it was never redacted; for an archived chat
    already redacted, Claude Code's original (with the personal screen applied again, as the archive copy had it)."""
    text = open(f, errors='replace').read()
    if not MARK.search(text):
        return text
    if f.startswith(ARCH):
        o = os.path.join(ORIG, os.path.relpath(f, ARCH))
        if os.path.exists(o):
            tmp = os.path.join(VAULT, '.tmp_' + os.path.basename(f))
            shutil.copy2(o, tmp)
            try:
                import personal_deep_check as P
                P.reapply([tmp])
            except Exception:
                pass
            t = open(tmp, errors='replace').read(); os.remove(tmp)
            return t
    return None                                          # redacted and no original left: those values are gone


def describe(text):
    """The secrets in a text by kind, with the database host for connection strings; never a value."""
    kinds, hosts = collections.Counter(), set()
    if R.PEM.search(text):
        kinds['private_key'] += len(R.PEM.findall(text))
    for name, rx in R.pats():
        if name == 'private_key':
            continue
        for m in rx.finditer(text):
            s = m.group(0)
            if name == 'conn_string':
                if not R.CRED.search(s):
                    continue                             # no credentials in it: not a secret
                h = re.search(r'@([^/\s:"\\]+)(?::\d+)?(/[A-Za-z0-9_\-]+)?', s)
                if h:
                    hosts.add(h.group(1) + (h.group(2) or ''))
            kinds[name] += 1
    return kinds, hosts


def build():
    os.makedirs(VAULT, exist_ok=True)
    try:
        os.chmod(VAULT, 0o700)
    except OSError:
        pass
    files = glob.glob(os.path.join(ARCH, '**', '*.jsonl'), recursive=True)
    for d in paths.kb_text_dirs():
        files += [f for f in glob.glob(os.path.join(str(d), '**', '*'), recursive=True) if os.path.isfile(f) and '/versions/' not in f.replace(os.sep, '/')]
    rows, lost, total = [], [], collections.Counter()
    for f in files:
        try:
            text = open(f, errors='replace').read()
        except OSError:
            continue
        if not (has_secret(text) or MARK.search(text)):
            continue
        full = unredacted(f)
        if full is None:
            lost.append(os.path.relpath(f, B)); full = text
        kinds, hosts = describe(full)
        if not kinds:
            continue
        keep(f, full)
        c = collections.Counter()
        R.redact_file(f, c)                              # the knowledge base's copy: markers only
        total.update(kinds)
        when = (re.search(r'"timestamp":\s*"(\d{4}-\d{2}-\d{2})', full) or re.search(r'(20\d\d-\d\d-\d\d)', os.path.basename(f)) or [None, ''])[1]
        rows.append((os.path.relpath(f, B), when, dict(kinds), sorted(hosts)))
    m = [f'# Secrets map ({dt.date.today()})', '',
         'Every secret found in the knowledge base\'s files' + (', kept whole in this folder (same relative paths)' if keeping() else '') + '. Values are never written',
         'here or anywhere else. The knowledge base\'s copies carry "[redacted <kind>]" instead.', '',
         f'{len(rows)} files; by kind: ' + ', '.join(f'{k} {v}' for k, v in total.most_common()), '',
         '| File (relative to brain/) | Date | Kinds | Database hosts (no credentials) |', '|---|---|---|---|']
    m += [f"| {p} | {w} | {', '.join(f'{k} x{v}' for k, v in kinds.items())} | {', '.join(h)[:120]} |" for p, w, kinds, h in sorted(rows, key=lambda r: r[1], reverse=True)]
    if lost:
        m += ['', 'Redacted before the vault existed and with no original left (those values are gone):'] + [f'- {p}' for p in lost]
    open(os.path.join(VAULT, 'SECRETS_MAP.md'), 'w').write('\n'.join(m) + '\n')
    try:
        os.chmod(os.path.join(VAULT, 'SECRETS_MAP.md'), 0o600)
    except OSError:
        pass
    print(f"{'kept' if keeping() else 'redacted'} {len(rows)} files; by kind {dict(total)}; values with no original left: {len(lost)}")
    return rows, lost


if __name__ == '__main__':
    if sys.argv[1:2] == ['build']:
        build()
    else:
        print(__doc__)
