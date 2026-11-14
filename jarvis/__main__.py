"""`python3 -m jarvis <command>`: the jarvis command (jarvis/cli.py)."""
import sys

from .cli import main

sys.exit(main())
