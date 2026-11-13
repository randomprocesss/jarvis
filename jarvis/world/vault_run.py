#!/usr/bin/env python3
"""The vault sync as its own process: as a thread of the server it held the one background lane for the length of a pass, and
nothing else could start. The server starts this file every 15 minutes and moves on. One pass at a time (the lock is
inside vault.sync). Does nothing while the vault is switched off."""
import datetime as dt, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # the repository
os.environ.pop('JARVIS_PROC', None)

if __name__ == '__main__':
    from jarvis.world import vault
    t = time.time()
    try:
        r = vault.sync()
    except Exception as e:                                # the reason is kept where the next look will find it
        r = {'error': f'{e.__class__.__name__}: {e}'[:300]}
    print(f"{dt.datetime.now().isoformat(timespec='seconds')} {round(time.time() - t)}s {str(r)[:400]}", flush=True)
