#!/usr/bin/env python3
"""Stress test for the deploy gate, on this computer only (standard library, nothing to install).

Calls one or more addresses of a service running HERE with more and more callers at once, and reports how long the calls
took and how many failed, per step. Any host that is not this computer is refused.

  stress.py --url http://127.0.0.1:8000/api/health --url http://127.0.0.1:8000/api/x \
            --steps 1,10,25,50 --seconds 20 [--header 'Authorization: Bearer test'] \
            [--method POST --body '{"a":1}'] [--json out.json]

Limits: at most 100 callers at once, at most 60 seconds per step, this computer only.
"""
import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

LOCAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def hit(url, method, body, headers, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    return code, (time.perf_counter() - t0) * 1000


def step(urls, conc, seconds, method, body, headers, timeout):
    stop = time.perf_counter() + seconds
    lat, codes, lock = [], {}, threading.Lock()

    def worker(i):
        n = 0
        while time.perf_counter() < stop:
            code, ms = hit(urls[(i + n) % len(urls)], method, body, headers, timeout)
            n += 1
            with lock:
                lat.append(ms)
                codes[code] = codes.get(code, 0) + 1

    with ThreadPoolExecutor(conc) as ex:
        list(ex.map(worker, range(conc)))
    total = len(lat)
    errs = sum(v for k, v in codes.items() if k == 0 or k >= 500)
    q = statistics.quantiles(lat, n=100) if total >= 2 else [lat[0] if lat else 0] * 99
    return {"concurrency": conc, "requests": total, "rps": round(total / seconds, 1),
            "p50_ms": round(q[49], 1), "p95_ms": round(q[94], 1), "p99_ms": round(q[98], 1),
            "max_ms": round(max(lat), 1) if lat else 0,
            "error_rate": round(errs / total, 4) if total else 1.0, "codes": codes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", required=True)
    ap.add_argument("--steps", default="1,10,25,50")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--method", default="GET")
    ap.add_argument("--body")
    ap.add_argument("--header", action="append", default=[])
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--json")
    a = ap.parse_args()
    for u in a.url:
        host = urllib.parse.urlparse(u).hostname
        if host not in LOCAL:
            raise SystemExit(f"refusing non-local host: {host}")
    headers = dict(h.split(":", 1) for h in a.header)
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    body = a.body.encode() if a.body else None
    if body and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    out = []
    for c in [min(int(s), 100) for s in a.steps.split(",")]:
        r = step(a.url, c, min(a.seconds, 60), a.method, body, headers, a.timeout)
        out.append(r)
        print(f"c={r['concurrency']:>3}  n={r['requests']:>6}  rps={r['rps']:>7}  p50={r['p50_ms']:>8}  "
              f"p95={r['p95_ms']:>8}  p99={r['p99_ms']:>8}  err={r['error_rate']:.2%}  codes={r['codes']}")
        if r["error_rate"] > 0.5:
            print("stopping: error rate above 50%")
            break
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"urls": a.url, "method": a.method, "steps": out}, f, indent=2)


if __name__ == "__main__":
    main()
