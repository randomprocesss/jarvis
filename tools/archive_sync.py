#!/usr/bin/env python3
"""Nightly archive sync (KB plan Step 0.4, 16 Oct 2026). Claude Code deletes chat transcripts after 30 days, and Jarvis's
records change; this keeps a copy of the work, never of anything personal (The owner, 16 Oct: "make sure personal chats or
things [never] go in the brain or obsidian or anywhere").

  1. Chats (those held in the work folders -> brain/raw_archive/claude_projects): a chat new to the archive is labelled first
     (archive_scope.label, Sonnet, short samples). Only work, jarvis, setup and probe chats are copied, with their
     sub-agent and workflow transcripts; personal and admin chats are never copied. A grown chat is re-copied.
  2. Jarvis's own records (state/jarvis.db, voice/conversations, work, roundups) are backed up to
     state/backups/jarvis/, outside the brain, because they can hold personal talk. The knowledge base reads them only
     through its personal screen.
Run by kb_refresh.py before each night's refresh, or by hand: archive_sync.py [--no-label]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import glob, json, os, shutil, sqlite3, sys

HUB = str(paths.home())
paths.use_tools()
import archive_scope as A  # noqa: E402
import personal_deep_check as P  # noqa: E402

LIVE = str(paths.claude_chats())
BACKUP = os.path.join(HUB, 'state', 'backups', 'jarvis')
KEEP = ('work', 'jarvis', 'setup', 'probe')
# 16 Oct: Jarvis's own folders hold processing runs only (its voice brain, memory, noticing, relays, and the knowledge base's and
# the personal screens' own model calls). Their results are recorded elsewhere, and they carry the text they process, personal
# text included. They are never archived.
PROCESSING = paths.own_runs_prefix()


def _newer(src, dst):
    return not os.path.exists(dst) or os.path.getsize(src) != os.path.getsize(dst) or os.path.getmtime(src) > os.path.getmtime(dst) + 1


def transcripts(label=True):
    live = {}
    for d in [str(x) + os.sep for x in paths.work_chat_dirs()]:   # only the chats held in the work folders are looked at
        if os.path.basename(d.rstrip(os.sep)).startswith(PROCESSING):
            continue
        for f in glob.glob(os.path.join(d, '*.jsonl')):
            live[os.path.basename(d.rstrip('/')) + '/' + os.path.basename(f)[:-6]] = f
    st = A._load()
    hs = P.personal_hashes()
    todo = {c: p for c, p in live.items() if c not in st or 'scope' not in st[c]}
    if todo and label:
        A.label(only=todo)
        st = A._load()
    n = {'copied': 0, 'kept_out': 0, 'unlabelled': 0, 'same': 0}
    for c, p in live.items():
        scope = (st.get(c) or {}).get('scope')
        if scope is None:
            n['unlabelled'] += 1; continue                 # copied once labelled, never before
        if scope not in KEEP:
            n['kept_out'] += 1; continue
        folder, uuid = c.split('/', 1)
        pairs = [(p, os.path.join(A.ARCH, folder, uuid + '.jsonl'))]
        sub = os.path.join(LIVE, folder, uuid)
        for f in glob.glob(os.path.join(sub, '**', '*.jsonl'), recursive=True):
            pairs.append((f, os.path.join(A.ARCH, folder, uuid, os.path.relpath(f, sub))))
        changed = []
        for src, dst in pairs:
            if _newer(src, dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst); changed.append(dst)
        n['copied' if changed else 'same'] += 1
        if changed:                                     # 16 Oct: a re-copy is the original text again, personal lines included:
            s = st.setdefault(c, {})                    # its words are scanned and judged again below, and every verdict
            s['word_lines'], s['cleaned'] = None, False  # already made is applied again now
            n['personal_lines_reapplied'] = n.get('personal_lines_reapplied', 0) + P.reapply(changed, hs)
            try:                                        # 24 Oct (audit): a re-copy is the original again, secrets included:
                import kb_redact, kb_secrets_vault as SV   # the whole copy goes to the secrets vault FIRST, then the
                for f in changed:                          # knowledge base's copy is redacted (his ask: nothing lost)
                    t = open(f, errors='replace').read()
                    if SV.has_secret(t):
                        SV.keep(f, t)
                        n['secrets_redacted'] = n.get('secrets_redacted', 0) + kb_redact.redact_file(f)
            except Exception as e:
                n['redact_error'] = str(e)[:120]
    A._save(st)
    return n


def jarvis_records():
    os.makedirs(BACKUP, exist_ok=True)
    db = os.path.join(HUB, 'state', 'jarvis.db')
    if os.path.exists(db):                                  # a consistent copy of a live SQLite database
        src = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        dst = sqlite3.connect(os.path.join(BACKUP, 'jarvis.db.tmp'))
        src.backup(dst); dst.close(); src.close()
        os.replace(os.path.join(BACKUP, 'jarvis.db.tmp'), os.path.join(BACKUP, 'jarvis.db'))
    n = 0
    for name, src in (('voice_conversations', os.path.join(HUB, 'voice', 'conversations')), ('work', os.path.join(HUB, 'work')),
                      ('roundups', os.path.join(HUB, 'roundups'))):
        for f in glob.glob(os.path.join(src, '**', '*'), recursive=True):
            if os.path.isfile(f) and '/outputs/' not in f:
                dst = os.path.join(BACKUP, name, os.path.relpath(f, src))
                if _newer(f, dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(f, dst); n += 1
    return {'jarvis_files_copied': n}


def sync(label=True):
    out = transcripts(label)
    A.scan(only_unscanned=True)
    out['personal_lines_cleaned'] = A.clean()
    out.update(jarvis_records())
    return out


if __name__ == '__main__':
    print(json.dumps(sync(label='--no-label' not in sys.argv)))
