"""The helpers that make Jarvis run the same on macOS, Linux and Windows (jarvis/procs.py): locks, process groups,
the exit-code wrapper. Run here on the system the tests run on; the Windows branches are kept small on purpose."""
import os, signal, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

from jarvis import procs


class Portable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_lock_is_held_by_one_holder_at_a_time(self):
        p = self.t / 'x.lock'
        a = open(p, 'w')
        procs.flock(a)
        code = ('import sys; sys.path.insert(0, sys.argv[2]); from jarvis import procs\n'
                'f = open(sys.argv[1], "w")\ntry:\n    procs.flock(f, block=False)\n    print("got it")\nexcept OSError:\n    print("held")\n')
        repo = str(Path(__file__).resolve().parent.parent)
        out = subprocess.run([sys.executable, '-c', code, str(p), repo], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, 'held')
        procs.funlock(a)
        out = subprocess.run([sys.executable, '-c', code, str(p), repo], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, 'got it')
        a.close()

    def test_the_wrapper_writes_the_exit_code_and_keeps_it(self):
        xf = self.t / 'run.exit'
        rc = subprocess.call(procs.exit_file_wrapper(str(xf), [sys.executable, '-c', 'import sys; sys.exit(7)']))
        self.assertEqual((rc, xf.read_text()), (7, '7'))
        rc = subprocess.call(procs.exit_file_wrapper(str(xf), [sys.executable, '-c', 'print("ok")']), stdout=subprocess.DEVNULL)
        self.assertEqual((rc, xf.read_text()), (0, '0'))

    def test_a_stop_reaches_the_process_and_what_it_started(self):
        child = self.t / 'child.pid'
        code = ('import subprocess, sys, time\n'
                'c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                'open(sys.argv[1], "w").write(str(c.pid))\ntime.sleep(60)\n')
        p = subprocess.Popen([sys.executable, '-c', code, str(child)], **procs.session_kw())
        for _ in range(100):
            if child.exists() and child.read_text():
                break
            time.sleep(0.05)
        grand = int(child.read_text())
        self.assertTrue(procs.alive(p.pid) and procs.alive(grand))
        procs.signal_group(p.pid, signal.SIGTERM)
        p.wait(10)
        for _ in range(100):
            if not procs.alive(grand):
                break
            time.sleep(0.05)
        self.assertFalse(procs.alive(grand))

    def test_another_process_is_seen_by_its_command_line_and_this_one_is_not(self):
        tag = 'jarvis-portable-test-' + str(os.getpid())
        self.assertFalse(procs.others_running(tag))
        p = subprocess.Popen([sys.executable, '-c', 'import sys, time; time.sleep(60)', tag])
        try:
            for _ in range(50):
                if procs.others_running(tag):
                    break
                time.sleep(0.1)
            self.assertTrue(procs.others_running(tag))
        finally:
            p.kill(); p.wait()
        self.assertFalse(procs.others_running(tag))

    def test_alive_and_stop(self):
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.assertTrue(procs.alive(p.pid))
        self.assertTrue(procs.stop(p.pid, 10))
        self.assertFalse(procs.alive(p.pid))
        self.assertFalse(procs.alive(0) or procs.alive('x'))


if __name__ == '__main__':
    unittest.main()
