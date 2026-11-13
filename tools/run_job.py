#!/usr/bin/env python3
"""Start, check or list long jobs that keep running after the Claude run that started them has ended.

    python tools/run_job.py start <job dir> -- <command ...>
    python tools/run_job.py status <job dir>
    python tools/run_job.py list
    python tools/run_job.py stop <job dir>      (ends the job and every process it started)

<job dir> must be inside work/outputs/ in the data home. The command runs there, in its own session (so closing the
Claude run does not kill it, which nohup does not prevent), with output in <job dir>/run.log and its state in
<job dir>/job.json: {"state": "running|done|failed", "pid", "cmd", "started", "ended", "exit"}.
Only one job runs at a time; start refuses while another is still running or waiting to start.
The computer's load is checked too (a job started beside other heavy work swaps and takes many times longer): while more than MAX_KERNELS notebook kernels run, or less
than MIN_FREE % of memory is free, the job waits (state "waiting_capacity", re-checked every 2 minutes, up to 12 hours). Job dirs may be nested at any depth
(e.g. outputs/<item>/scenario/); list and the one-job guard search the whole tree.

An exit code of 0 is not success: a notebook run with --allow-errors exits 0 with 42 failed cells. If
<job dir>/check.py exists, the wrapper runs it (cwd = job dir) after the command ends; a non-zero exit from the
check marks the job "failed" even when the command exited 0, and the check's last output lines go into
job.json "check". Write the check BEFORE starting the job: it should read the outputs and fail on cell errors,
[FAIL] gates, missing or empty result files, or whatever "it worked" means for that job.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import paths
import glob, json, os, subprocess, sys
from datetime import datetime

OUTS = os.path.realpath(str(paths.home() / 'work/outputs'))
now = lambda: datetime.now().astimezone().isoformat(timespec='seconds')


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def read(d):
    p = os.path.join(d, 'job.json')
    if not os.path.exists(p):
        return None
    j = json.load(open(p))
    # A job whose process is gone without writing its end state died (reboot, kill, out of memory).
    if j.get('state') == 'running' and not alive(j.get('pid')):
        j.update(state='failed', ended=j.get('ended') or now(), exit=j.get('exit', 'process gone'))
        write(d, j)
    return j


def write(d, j):
    tmp = os.path.join(d, '.job.json.tmp')
    json.dump(j, open(tmp, 'w'), indent=1)
    os.replace(tmp, os.path.join(d, 'job.json'))


def jobdir(arg):
    d = os.path.realpath(os.path.expanduser(arg))
    if not d.startswith(OUTS + os.sep):
        sys.exit(f'job dir must be inside {OUTS}')
    os.makedirs(d, exist_ok=True)
    return d


def all_jobs():
    return sorted(os.path.dirname(p) for p in glob.glob(os.path.join(OUTS, '**', 'job.json'), recursive=True))


def running():
    return [(d, j) for d in all_jobs() for j in [read(d)] if j and j.get('state') in ('running', 'starting', 'waiting_capacity')]


MAX_KERNELS, MIN_FREE = 2, 30     # at most 3 notebook runs at once on this Mac: a job starts only beside 2 or fewer


def kernels(ps=None):
    """Notebook kernels running now (ipykernel_launcher), not counting the pool workers a kernel forks (same command line,
    parent also a kernel)."""
    ps = ps if ps is not None else subprocess.run(['ps', '-axo', 'pid=,ppid=,command='], capture_output=True, text=True).stdout
    rows = [l.split(None, 2) for l in ps.splitlines() if len(l.split(None, 2)) == 3]
    k = {pid: ppid for pid, ppid, c in rows if 'ipykernel_launcher' in c}
    return [pid for pid, ppid in k.items() if ppid not in k]


def mem_free():
    """macOS's free-memory percentage (the number memory_pressure reports)."""
    try:
        return int(subprocess.run(['sysctl', '-n', 'kern.memorystatus_level'], capture_output=True, text=True, timeout=5).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 100


def capacity():
    """(room to start, why not)."""
    n, free = len(kernels()), mem_free()
    if n > MAX_KERNELS:
        return False, f'{n} notebook kernels already running (at most {MAX_KERNELS} beside a new job)'
    if free < MIN_FREE:
        return False, f'only {free}% of memory free (needs {MIN_FREE}%)'
    return True, f'{n} kernels, {free}% memory free'


def main(argv):
    if len(argv) < 1:
        sys.exit(__doc__)
    cmd = argv[0]
    if cmd == 'list':
        for d in all_jobs():
            j = read(d)
            print(f"{os.path.relpath(d, OUTS)}: {j['state']} since {j.get('started')}"
                  + (f", ended {j.get('ended')} exit {j.get('exit')}" if j['state'] != 'running' else '')
                  + (f", check: {j['check']}" if j.get('check') else ''))
        return
    if cmd == 'stop':
        # Stops the job AND everything it started (the whole process group), so no leftover keeps writing.
        d = jobdir(argv[1]); j = read(d) or {}
        pid = j.get('pid')
        if not pid:
            sys.exit('no job here')
        import signal
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        j.update(state='failed', exit='stopped', ended=now()); write(d, j)
        print(json.dumps(j, indent=1))
        return
    if cmd == 'status':
        print(json.dumps(read(jobdir(argv[1])) or {'state': 'none'}, indent=1))
        return
    if cmd == 'start':
        if '--' not in argv:
            sys.exit('usage: start <job dir> -- <command ...>')
        d, run = jobdir(argv[1]), argv[argv.index('--') + 1:]
        busy = running()
        if busy:
            sys.exit(f'another job is still running: {busy[0][0]} (pid {busy[0][1].get("pid")}); check it instead')
        # The wrapper runs the command, then records how it ended; both live in their own session.
        tools = os.path.dirname(os.path.abspath(__file__))
        jobs_py = os.environ.get('JARVIS_JOBS_PYTHON') or str(paths.home() / 'envs' / 'jobs' / 'bin' / 'python')   # a Python with the owner's data packages, if they made one
        wrapper = ("import json,os,subprocess,sys,time\nfrom datetime import datetime\n"
                   "d=sys.argv[1]\n"
                   # wait for room on the computer (notebook kernels, free memory) before starting
                   f"sys.path.insert(0, {tools!r}); import run_job\n"
                   "p=os.path.join(d,'job.json'); ok,why=run_job.capacity(); t0=time.time()\n"
                   "while not ok and time.time()-t0 < 12*3600:\n"
                   "    j=json.load(open(p)); j.update(state='waiting_capacity', waiting=why); t=p+'.tmp'; json.dump(j,open(t,'w'),indent=1); os.replace(t,p)\n"
                   "    time.sleep(120); ok,why=run_job.capacity()\n"
                   "if not ok:\n"
                   "    j=json.load(open(p)); j.update(state='failed', exit='capacity', check='the computer stayed too busy for 12 hours: ' + why, ended=datetime.now().astimezone().isoformat(timespec='seconds'))\n"
                   "    t=p+'.tmp'; json.dump(j,open(t,'w'),indent=1); os.replace(t,p); sys.exit(4)\n"
                   "j=json.load(open(p)); j.pop('waiting', None); j['state']='running'; t=p+'.tmp'; json.dump(j,open(t,'w'),indent=1); os.replace(t,p)\n"
                   "r=subprocess.call(sys.argv[2:], cwd=d)\n"
                   "p=os.path.join(d,'job.json'); j=json.load(open(p))\n"
                   "ok=(r==0)\n"
                   "if os.path.exists(os.path.join(d,'check.py')):\n"
                   # the check needs the same packages as the job: the owner's data Python when there is one
                   f"    jp={jobs_py!r}\n"
                   "    c=subprocess.run([jp if os.path.exists(jp) else sys.executable,'check.py'],cwd=d,capture_output=True,text=True)\n"
                   "    j['check']=((c.stdout+c.stderr).strip().splitlines() or [''])[-1][:300] or ('exit %d' % c.returncode)\n"
                   "    j['check_exit']=c.returncode; ok=ok and c.returncode==0\n"
                   "j.update(state='done' if ok else 'failed', exit=r, ended=datetime.now().astimezone().isoformat(timespec='seconds'))\n"
                   "t=p+'.tmp'; json.dump(j,open(t,'w'),indent=1); os.replace(t,p)\n"
                   # the item's work starts at once (job_done.py asks Jarvis), instead of waiting for the next routine run
                   f"subprocess.call([sys.executable, {os.path.join('TOOLS', 'job_done.py')!r}.replace('TOOLS', {tools!r}), d])\n")
        log = open(os.path.join(d, 'run.log'), 'ab')
        write(d, {'state': 'starting', 'pid': None, 'cmd': run, 'started': now()})
        p = subprocess.Popen([sys.executable, '-c', wrapper, d] + run, cwd=d, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        j = json.load(open(os.path.join(d, 'job.json')))
        j['pid'] = p.pid
        if j['state'] == 'starting':          # a very short command may already have written its end state
            j['state'] = 'running'
        write(d, j)
        room, why = capacity()
        if not room:
            print(f'waiting for room on this computer: {why}; it starts by itself (state "waiting_capacity")', file=sys.stderr)
        print(json.dumps(j, indent=1))
        return
    sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
