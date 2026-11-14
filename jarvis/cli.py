"""The `jarvis` command: start, stop and look after Jarvis from a terminal. Setup and everything else is in the app.

    jarvis start [--port N] [--no-open]   start Jarvis in the background and open it in the browser
    jarvis stop                           stop it
    jarvis restart                        stop, then start
    jarvis status                         is it running, which version, where the data is, what Setup still needs
    jarvis open                           open the app in the browser
    jarvis doctor                         check what Jarvis needs on this computer, each with what to do about it
    jarvis demo                           look around on made-up data
    jarvis uninstall                      stop Jarvis and say how to remove it and its data (nothing is deleted by this)
    jarvis version

Run as `python3 -m jarvis <command>` from the Jarvis folder, or as `jarvis <command>` after install.sh.
"""
import os
import shutil
import sys
import time
import webbrowser

from . import config, paths, procs


def port(args=None):
    a = args or []
    if '--port' in a and a.index('--port') + 1 < len(a) and a[a.index('--port') + 1].isdigit():
        return int(a[a.index('--port') + 1])
    try:
        return int(os.environ.get('JARVIS_PORT') or config.load().get('server.port') or 8765)
    except (TypeError, ValueError):
        return 8765


def url(p):
    return f'http://localhost:{p}/'


def start(args):
    p = port(args)
    h = procs.health(p)
    if h:
        print(f'Jarvis is already running at {url(p)} (process {h.get("pid")}).')
    else:
        paths.ensure()
        env = dict(os.environ)
        env.pop('JARVIS_PORT', None)
        procs.spawn_detached([paths.python(), '-m', 'jarvis.server', '--port', str(p)], env=env, cwd=str(paths.REPO), log=str(paths.logs('server.log')))
        h = procs.wait_up(p, 30)
        if not h:
            print(f'Jarvis did not start. The last lines of {paths.logs("server.log")} say why:')
            try:
                print(''.join(open(str(paths.logs('server.log')), errors='replace').readlines()[-8:]).rstrip())
            except OSError:
                pass
            return 1
        print(f'Jarvis is running at {url(p)} (process {h.get("pid")}). Your data is kept in {paths.home()}.')
    if '--no-open' not in args:
        webbrowser.open(url(p))
    return 0


def stop(args):
    p = port(args)
    h = procs.health(p)
    if not h:
        print('Jarvis is not running.')
        return 0
    gone = procs.stop(h.get('pid'), 15)
    print('Jarvis has stopped.' if gone else f'Jarvis did not stop (process {h.get("pid")}).')
    return 0 if gone else 1


def restart(args):
    if '--wait-for' in args:                         # an update asked for this: wait until the old server has ended
        try:
            old = int(args[args.index('--wait-for') + 1])
            t0 = time.time()
            while procs.alive(old) and time.time() - t0 < 30:
                time.sleep(0.3)
        except (ValueError, IndexError):
            pass
    else:
        stop(args)
    return start([a for a in args if a not in ('--wait-for',)] + ['--no-open'])


def status(args):
    p = port(args)
    h = procs.health(p)
    from . import product
    print(f'Jarvis {product.version() or "(no version file)"}')
    print(('running at ' + url(p) + f' (process {h.get("pid")}, up {h.get("uptime_s")} s)') if h else 'not running (start it with: jarvis start)')
    print(f'data: {paths.home()}' + ('' if paths.home().is_dir() else ' (not made yet)'))
    c = config.load()
    if not c.is_set_up():
        print('Setup: not started. Open the app and tell Jarvis your name.')
    else:
        from . import setup
        left = [s['title'] for s in setup.steps() if not s['done']]
        print('Setup: ' + ('everything is in place.' if not left else 'still to do: ' + ', '.join(left) + '.'))
    return 0


def doctor(args):
    """Every check prints one line: ok or what to do. Exit 1 when something Jarvis needs is missing."""
    from . import setup
    bad = 0

    def line(ok, what, fix=''):
        nonlocal bad
        bad += 0 if ok else 1
        print(('  ok    ' if ok else '  FIX   ') + what + ('' if ok or not fix else '\n        ' + fix))
    v = sys.version_info
    line(v >= (3, 9), f'Python {v.major}.{v.minor}.{v.micro}', 'Jarvis needs Python 3.9 or newer.')
    line(bool(shutil.which('git')), 'git', 'Install git: Jarvis uses it to update itself and to read your repositories.')
    c = setup.claude()
    line(bool(c.get('found')), 'Claude Code' + (f" {c.get('version')}" if c.get('version') else ''), c.get('fix', ''))
    if c.get('found'):
        line(bool(c.get('signed_in') or c.get('unknown')), 'Claude Code is signed in', c.get('fix', ''))
    try:
        paths.ensure()
        probe = paths.home() / '.write-test'
        probe.write_text('ok'); probe.unlink()
        line(True, f'data folder {paths.home()} can be written')
    except OSError as e:
        line(False, f'data folder {paths.home()}', f'It cannot be written ({e.__class__.__name__}). Set JARVIS_HOME to a folder that can.')
    line(os.path.isfile(os.path.join(str(paths.REPO), 'web', 'out', 'index.html')), 'the built screens (web/out)',
         'They are missing from this copy. Get the Jarvis folder again (git clone), or build them: cd web && npm install && npm run build.')
    p = port(args)
    h = procs.health(p)
    line(True, f'port {p}: ' + ('Jarvis is running' if h else 'free, or Jarvis is not running'))
    try:
        config.load()
        line(True, 'settings can be read')
    except config.ConfigError as e:
        line(False, 'settings', str(e))
    print('Everything Jarvis needs is here.' if not bad else f'{bad} thing(s) to fix, above.')
    return 1 if bad else 0


def demo(args):
    from . import product
    if not procs.health(port(args)):
        if start(args + ['--no-open']) != 0:
            return 1
    os.environ['JARVIS_PORT'] = str(port(args))
    body, code = product.demo_start({})
    if code != 200:
        print(body.get('error') or 'The demo did not start.')
        return 1
    print(f'The demo is at {body["url"]} (a made-up person; nothing is real and nothing is sent).')
    if '--no-open' not in args:
        webbrowser.open(body['url'])
    return 0


def uninstall(args):
    stop(args)
    from . import product
    product.demo_stop()
    print('Jarvis has stopped. Nothing was deleted. To remove it completely:')
    print(f'  1. Your data: open the app once more and use Setup > Privacy > Erase everything, or move this folder to the Trash:\n       {paths.home()}')
    print(f'  2. The program: move this folder to the Trash:\n       {paths.REPO}')
    launcher = os.path.join(os.path.expanduser('~'), '.local', 'bin', 'jarvis')
    if os.path.exists(launcher):
        print(f'  3. The command: delete {launcher}')
    return 0


def version(args):
    from . import product
    print(product.version() or 'unknown')
    return 0


COMMANDS = {'start': start, 'stop': stop, 'restart': restart, 'status': status, 'open': lambda a: (webbrowser.open(url(port(a))), 0)[1],
            'doctor': doctor, 'demo': demo, 'uninstall': uninstall, 'version': version}


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ('-h', '--help', 'help') or a[0] not in COMMANDS:
        print(__doc__.strip())
        return 0 if a and a[0] in ('-h', '--help', 'help') else (1 if a else 0)
    return COMMANDS[a[0]](a[1:])
