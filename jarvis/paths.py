"""Where things live. This is the only module that knows a path.

The code runs from the repository. Everything that belongs to the person using Jarvis (settings, the knowledge base, the
inbox, drafts, logs) lives in their data home: the folder JARVIS_HOME names, or ~/.jarvis. Nothing of theirs is ever
written inside the repository, and nothing in the repository is ever specific to one person."""
import os, re, shutil, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PARTS = ('state', 'brain', 'roundups', 'work', 'logs', 'skills', 'voice')


def home() -> Path:
    """The data home: JARVIS_HOME if set, else ~/.jarvis."""
    return Path(os.environ.get('JARVIS_HOME') or (Path.home() / '.jarvis')).expanduser()


def ensure() -> Path:
    """Create the data home and its parts (private to the user) and return it."""
    h = home()
    for d in (h, *(h / p for p in PARTS)):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass                                    # a file system without permissions (some Windows setups)
    return h


def at(*parts: str) -> Path:
    """A path inside the data home."""
    return home().joinpath(*parts)


def config_file() -> Path:
    return at('config.toml')


def state(*parts: str) -> Path:
    return at('state', *parts)


def brain(*parts: str) -> Path:
    return at('brain', *parts)


def kb(*parts: str) -> Path:
    """The knowledge base: one folder inside brain/, named in settings (knowledge.name; 'main' unless they chose another)."""
    from . import config
    name = str(config.load().get('knowledge.name') or 'main')
    return brain(name if re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,40}', name) else 'main', *parts)


def trash(name: str = '') -> Path:
    """Where a removed file goes so it can still be taken back: the system's own Trash on a Mac or a Linux desktop, else a
    trash folder in the data home. Jarvis never deletes for good by itself."""
    if sys.platform == 'darwin':
        base = Path.home() / '.Trash'
    elif (Path.home() / '.local' / 'share' / 'Trash' / 'files').is_dir():
        base = Path.home() / '.local' / 'share' / 'Trash' / 'files'
    else:
        base = at('trash')
    return base / name if name else base


def secrets(*parts: str) -> Path:
    """The locked folder for whole copies of files that held a secret (only when the owner asked for them to be kept).
    Inside state/: no worker may read or edit it."""
    return state('secrets', *parts)


def kb_text_dirs() -> list:
    """Every folder that holds text of the knowledge base (its own copies): the raw units, the notes and the knowledge
    base itself. What a screen or a redaction must cover."""
    return [brain(d) for d in ('sources_extra', 'sources_extra_later', 'sources_raw', 'sources', 'sessions')] + [kb()]


def roundups(*parts: str) -> Path:
    """The inbox round-ups: one file per check of the owner's sources."""
    return at('roundups', *parts)


def work(*parts: str) -> Path:
    return at('work', *parts)


def logs(*parts: str) -> Path:
    return at('logs', *parts)


def voice(*parts: str) -> Path:
    """What the voice side keeps: conversations and its measurements."""
    return at('voice', *parts)


def repo(*parts: str) -> Path:
    """A path inside the code (the repository), never inside the data home."""
    return REPO.joinpath(*parts)


def tool(name: str) -> Path:
    return REPO / 'tools' / name


def use_tools() -> None:
    """Let the caller import a command-line tool as a module (the tools folder joins the import path, once)."""
    t = str(REPO / 'tools')
    if t not in sys.path:
        sys.path.insert(0, t)


def python() -> str:
    """The Python that is running Jarvis, for the helpers it starts."""
    return sys.executable or 'python3'


def claude_bin() -> str:
    """The Claude Code command every Jarvis run starts. With an API key in place of a Claude plan (settings: engine.mode =
    "api-key"), that is Jarvis's own launcher, which hands Claude the key from the password store and counts what each run
    cost (jarvis/claude_run.py); otherwise Claude Code itself. '' if Claude Code is not found."""
    real = claude_real()
    if not real:
        return ''
    from . import config                             # config imports this module, so not at the top
    try:
        mode = config.load().get('engine.mode')
    except Exception:  # noqa: BLE001  unreadable settings: the plan's way, which needs no key
        mode = ''
    if mode == 'api-key':
        from . import claude_run
        return claude_run.launcher(real)
    return real


def claude_real() -> str:
    """Claude Code itself: JARVIS_CLAUDE if set, else the one on PATH, else the usual install places. '' if none."""
    named = os.environ.get('JARVIS_CLAUDE')
    if named:
        return named
    found = shutil.which('claude')
    if found:
        return found
    for c in (Path.home() / '.claude' / 'local' / 'claude', Path.home() / '.local' / 'bin' / 'claude',
              Path('/opt/homebrew/bin/claude'), Path('/usr/local/bin/claude')):
        if c.exists():
            return str(c)
    return ''


def workdir() -> Path:
    """The folder workers run in: the first work folder in settings that exists, else the data home's work folder. Read
    each time it is needed, so a folder chosen in Setup counts from the next run on."""
    from . import config                             # config imports this module, so not at the top
    for f in config.load().get('work.folders') or []:
        d = Path(f).expanduser()
        if d.is_dir():
            return d
    return work()


def claude_memory() -> Path:
    """The owner's memory notes: Claude Code's memory folder for their work folder."""
    return claude_chats_for(workdir()) / 'memory'


def claude_chats_for(folder) -> Path:
    """Where Claude Code keeps the chats it had in `folder` (its own naming: every other character becomes a dash)."""
    return claude_chats() / re.sub(r'[^A-Za-z0-9]', '-', str(folder))


def work_chat_dirs() -> list:
    """Every Claude Code chat folder that belongs to the owner's work: the chats held in a work folder or anywhere inside
    it (Claude Code keeps one folder per place a chat was started)."""
    from . import config
    roots = [workdir()] + [Path(f).expanduser() for f in config.load().get('work.folders') or []]
    names = {claude_chats_for(r).name for r in roots}
    out = []
    try:
        for d in sorted(claude_chats().iterdir()):
            if d.is_dir() and any(d.name == n or d.name.startswith(n + '-') for n in names) and d not in out:
                out.append(d)
    except OSError:
        pass
    return out


def own_runs_prefix() -> str:
    """The name Claude Code gives the chats of Jarvis's own processing runs (they run inside the data home). Those are
    never read as the owner's work: their results live elsewhere."""
    return claude_chats_for(home()).name


def worktree(name: str) -> Path:
    """A git worktree beside the repository: Jarvis's own code changes are made and tested there, never in the running copy."""
    return REPO.parent / f'{REPO.name}-{name}'


def demo_home() -> Path:
    """Demo mode's own data home, apart from the real one."""
    return at('demo')


def claude_dir() -> Path:
    """Claude Code's own folder (chats, skills): CLAUDE_CONFIG_DIR if set, else ~/.claude."""
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or (Path.home() / '.claude')).expanduser()


def claude_chats() -> Path:
    return claude_dir() / 'projects'


def claude_skills() -> Path:
    return claude_dir() / 'skills'
