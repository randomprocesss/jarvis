"""Jarvis's launcher for Claude Code when the owner runs it with an API key (settings: engine.mode = "api-key").

Every Jarvis run starts Claude through it (paths.claude_bin() returns it). It:
  - hands Claude the key from the password store (jarvis/keystore.py) in its environment, so the key is in no file
    and on no command line;
  - passes everything through unchanged (standard input, output and error, the exit code, a stop signal);
  - notes what each run cost, from the cost Claude Code itself reports at the end of a run, in state/spend.jsonl.
    A run started with plain text output reports no cost; such runs are short ones and are counted as unknown.
The governor reads the day's spend and holds background work at the owner's daily limit (engine.daily_cap_usd).

    launcher(real)      the path of the launcher script in the data home, written when missing or changed
    spent_today()       {'usd': dollars, 'runs': n, 'unknown': runs that reported no cost}
"""
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import threading
import time

from . import paths

COST = b'"total_cost_usd"'


def _spend_file():
    return str(paths.state('spend.jsonl'))


def launcher(real):
    """Write (when missing or different) and return the launcher script: a tiny file that runs this module with the
    Python that runs Jarvis, for the Claude Code found at `real`."""
    d = paths.home() / 'bin'
    p = d / ('claude-key.cmd' if os.name == 'nt' else 'claude-key')
    if os.name == 'nt':
        body = f'@echo off\r\nset JARVIS_CLAUDE_REAL={real}\r\n"{paths.python()}" -m jarvis.claude_run %*\r\n'
    else:
        body = (f'#!/bin/sh\n# Jarvis: runs Claude Code with the API key from the password store, and notes what each run cost.\n'
                f'JARVIS_CLAUDE_REAL="{real}" PYTHONPATH="{paths.REPO}${{PYTHONPATH:+:$PYTHONPATH}}" exec "{paths.python()}" -m jarvis.claude_run "$@"\n')
    try:
        have = p.read_text()
    except OSError:
        have = ''
    if have != body:
        d.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(body)
        os.chmod(str(tmp), 0o700)
        os.replace(str(tmp), str(p))
    return str(p)


def _note(cost, args):
    model = ''
    if '--model' in args and args.index('--model') + 1 < len(args):
        model = str(args[args.index('--model') + 1])[:60]
    row = {'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'usd': cost, 'model': model}
    try:
        os.makedirs(os.path.dirname(_spend_file()), exist_ok=True)
        with open(_spend_file(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(row) + '\n')
    except OSError:
        pass


def spent_today(now=None):
    day = (now or dt.datetime.now().astimezone()).strftime('%Y-%m-%d')
    usd, runs, unknown = 0.0, 0, 0
    try:
        with open(_spend_file(), encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not str(r.get('at', '')).startswith(day):
                    continue
                runs += 1
                if isinstance(r.get('usd'), (int, float)):
                    usd += float(r['usd'])
                else:
                    unknown += 1
    except OSError:
        pass
    return {'usd': round(usd, 4), 'runs': runs, 'unknown': unknown}


def _cost_in(line):
    """The run's cost from Claude Code's final line ({"type": "result", ..., "total_cost_usd": 0.0123}), else None."""
    if COST not in line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if isinstance(d, dict) and isinstance(d.get('total_cost_usd'), (int, float)):
        return float(d['total_cost_usd'])
    return None


def main(argv=None):
    from . import keystore
    args = list(sys.argv[1:] if argv is None else argv)
    real = os.environ.get('JARVIS_CLAUDE_REAL') or paths.claude_real()
    if not real:
        sys.stderr.write('Jarvis: Claude Code was not found on this computer.\n')
        return 127
    key = keystore.get()
    if not key:
        sys.stderr.write('Jarvis: no API key is set. Add it on the Setup page (Claude), or set ANTHROPIC_API_KEY.\n')
        return 78
    env = dict(os.environ, ANTHROPIC_API_KEY=key)
    env.pop('JARVIS_CLAUDE_REAL', None)
    if '--version' in args or args[:1] == ['auth']:
        return subprocess.call([real] + args, env=env)
    p = subprocess.Popen([real] + args, env=env, stdout=subprocess.PIPE, bufsize=0)
    for s in (signal.SIGTERM, signal.SIGINT):        # a stop for the launcher is a stop for Claude
        try:
            signal.signal(s, lambda n, f: p.send_signal(n))
        except (ValueError, OSError):
            pass
    out = getattr(sys.stdout, 'buffer', sys.stdout)
    costs, tail = [], b''
    while True:
        chunk = p.stdout.read1(65536) if hasattr(p.stdout, 'read1') else p.stdout.read(65536)
        if not chunk:
            break
        out.write(chunk)
        out.flush()
        tail += chunk
        *lines, tail = tail.split(b'\n')
        for line in lines:
            c = _cost_in(line)
            if c is not None:
                costs.append(c)
    if tail:
        c = _cost_in(tail)
        if c is not None:
            costs.append(c)
    rc = p.wait()
    _note(costs[-1] if costs else None, args)
    return rc


if __name__ == '__main__':
    sys.exit(main())
