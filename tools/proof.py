#!/usr/bin/env python3
"""The proof check from the command line: every number in a finished item is tied to a file of that item (jarvis/proof.py).

    proof.py check <round-up id> <item id>     what is not tied yet, or that everything is
    proof.py selftest
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
import runpy

if __name__ == '__main__':
    runpy.run_module('jarvis.proof', run_name='__main__')
