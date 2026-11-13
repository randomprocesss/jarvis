"""The skills Jarvis's workers follow, filled in for the owner and put where Claude Code finds them.

The skills ship as templates (skills/<name>/SKILL.md, the same {{value}} and [[if x]] ... [[end]] marks as the prompts):
who the owner is, where the data home is, which apps are connected and which ticket projects are theirs all come from
settings. install() writes each one to the Claude Code skills folder under its own name (all start with "jarvis-"), and
writes it again when the settings or the template changed. A skill the owner edited by hand is left alone and said so.

    python3 -m jarvis.skillset            install or refresh, and say what happened to each
    python3 -m jarvis.skillset --check    only say what would happen
"""
import hashlib
import json
import os
import re
import sys
import time

from . import config, paths, texts

MARK = '<!-- jarvis-skill {} -->'          # the last line of an installed skill: the hash of the text above it


def source():
    return paths.repo('skills')


def names():
    """The skills that ship, by folder name."""
    try:
        return sorted(d.name for d in source().iterdir() if (d / 'SKILL.md').is_file())
    except OSError:
        return []


def values():
    """What the templates are filled with."""
    c = config.load()
    own = c.own_projects()
    name = c.owner()
    initials = ''.join(w[0] for w in re.findall(r'[A-Za-z]+', name)).lower()[:3]
    secs = -(time.altzone if time.localtime().tm_isdst > 0 else time.timezone)
    share = os.path.expanduser(str(c.get('sharing.folder') or '').strip())
    return {
        'owner': c.owner_or(), 'identity': c.identity_line(),
        'home': str(paths.home()), 'repo': str(paths.REPO), 'tools': str(paths.repo('tools')), 'schemas': str(paths.repo('skills', 'schemas')),
        'python': paths.python(), 'port': int(c.get('server.port') or 8765), 'workdir': str(paths.workdir()),
        'kb': str(paths.kb()), 'memory': str(paths.claude_memory()), 'skills': str(paths.claude_skills()),
        'tz': f"{time.strftime('%Z')}, UTC{'+' if secs >= 0 else '-'}{abs(secs) // 3600:02d}:{abs(secs) % 3600 // 60:02d}",
        'branch_prefix': str(c.get('work.branch_prefix') or '').strip() or ((initials or 'work') + '/'),
        'slack': c.has('slack'), 'slack_user_id': str(c.get('sources.slack_user_id') or ''),
        'mail': c.has('microsoft 365'), 'wiki': c.has('atlassian'),
        'tracker': c.has('atlassian') and bool(own), 'no_tracker': not (c.has('atlassian') and bool(own)),
        'projects': ' or '.join(own), 'project': own[0] if own else '', 'project_s': 's' if len(own) > 1 else '',
        'cloud_id': str(c.get('tracker.cloud_id') or ''),
        'share': bool(share), 'share_name': os.path.basename(share.rstrip(os.sep)) if share else '',
        'no_ai_words': str(c.get('sharing.ai_wording') or 'never') == 'never',
    }


def render(name, vals=None):
    """One skill's text for this owner."""
    return texts.render_text((source() / name / 'SKILL.md').read_text(encoding='utf-8'), name, **(vals or values()))


def routine(name):
    """A routine's prompt for this owner (skills/routines/<name>.md, without its front matter)."""
    t = texts.render_text(paths.repo('skills', 'routines', name + '.md').read_text(encoding='utf-8'), name, **values())
    return re.sub(r'^---\n.*?\n---\n', '', t, flags=re.S).strip()


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _state(path):
    """'missing' | 'ours' (as we wrote it) | 'edited' (changed by hand since, or not ours at all)."""
    try:
        t = path.read_text(encoding='utf-8')
    except OSError:
        return 'missing', ''
    body, _, last = t.rstrip('\n').rpartition('\n')
    m = re.fullmatch(re.escape(MARK).replace(r'\{\}', '([0-9a-f]{16})'), last.strip())
    return ('ours' if m and m.group(1) == _sha(body + '\n') else 'edited'), t


def install(check=False):
    """{skill: 'installed' | 'updated' | 'same' | 'kept: you edited it'} . Nothing is written with check=True."""
    out, vals = {}, values()
    for name in names():
        text = render(name, vals)
        if not text.endswith('\n'):
            text += '\n'
        full = text + MARK.format(_sha(text)) + '\n'
        dst = paths.claude_skills() / name / 'SKILL.md'
        state, cur = _state(dst)
        if state == 'edited':
            out[name] = 'kept: you edited it'
        elif cur == full:
            out[name] = 'same'
        else:
            out[name] = 'installed' if state == 'missing' else 'updated'
            if not check:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_suffix('.tmp')
                tmp.write_text(full, encoding='utf-8')
                os.replace(tmp, dst)
    return out


if __name__ == '__main__':
    print(json.dumps(install(check='--check' in sys.argv), indent=1))
