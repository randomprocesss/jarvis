#!/usr/bin/env python3
"""Pull-request watch: new pull requests, review requests to the owner, and changes on their own pull requests, as an
inbox round-up. For a code host reached through the Atlassian connector (Bitbucket); another host needs its own reader.

Read-only. Every 30 minutes (the module jarvis/modules/prwatch.py, on when settings say modules.prwatch) it asks a small
run, with ONLY the connector's read tool, for: the open pull requests of the workspace, those where the owner is a
reviewer, and their own (open, merged, declined). It compares them with the last snapshot (prwatch/state.json in the data
home) and writes what changed as a round-up, which the inbox picks up by itself:
  - a review requested from the owner         -> action (worked like any inbox item)
  - their own pull request merged, declined, approved, changes requested, new comments or tasks -> action
  - a new pull request by someone else        -> FYI
The first run only records a baseline (plus one FYI list of the pull requests opened in the last 7 days), so nothing old
floods the inbox. A run whose fetch fails changes nothing and is retried next time.

The workspace comes from settings (sources.code_workspace). With none set, nothing is read.

    prwatch.py [--dry-run] [--force]
"""
import datetime as dt, json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config, paths  # noqa: E402

HUB = str(paths.home())
DIR = os.path.join(HUB, "prwatch")
STATE = os.path.join(DIR, "state.json")
LOG = os.path.join(DIR, "runs.jsonl")
ROUNDUPS = str(paths.roundups())
TOOL = "mcp__claude_ai_Atlassian_MCP__executeRead"
EVERY_S = 25 * 60
FIELDS = ["values.id", "values.title", "values.state", "values.author.display_name", "values.reviewers.display_name",
          "values.participants.user.display_name", "values.participants.state", "values.created_on", "values.updated_on",
          "values.links.html.href", "values.comment_count", "values.task_count", "size"]


def ws():
    """The workspace on the code host ('' = the watch is not set up)."""
    return str(config.load().get('sources.code_workspace') or '').strip()


def href_rx(workspace):
    return re.compile(r"^https://bitbucket\.org/" + re.escape(workspace) + r"/([A-Za-z0-9_.-]+)/pull-requests/(\d+)$")


def calls(workspace):
    """name -> (operation, inputs)"""
    return {
        "open": ("listBitbucketWorkspacePullRequests", {"workspaceId": workspace, "state": "OPEN", "pagelen": 50}),
        "review": ("listMyBitbucketReviewRequests", {"workspaceId": workspace, "pagelen": 50}),
        "mine_open": ("listMyBitbucketPullRequests", {"workspaceId": workspace, "state": "OPEN", "pagelen": 50}),
        "mine_merged": ("listMyBitbucketPullRequests", {"workspaceId": workspace, "state": "MERGED", "pagelen": 20}),
        "mine_declined": ("listMyBitbucketPullRequests", {"workspaceId": workspace, "state": "DECLINED", "pagelen": 20}),
    }


PROMPT = """You are a read-only data fetcher. Use ONLY the tool {tool}. Make exactly this one call:
name "{op}", inputs {inputs}, responseFields {fields}.
If it fails, retry it once. Reply with ONLY one JSON object, no prose, no code fence:
{{"ok": true, "values": <the "values" array exactly as the tool returned it>}}
or, if both tries failed, {{"ok": false, "error": "<the error text>"}}.
Copy every value exactly as returned (ids, titles, names, timestamps, links). Never call any other operation."""


def now():
    return dt.datetime.now().astimezone()


def load(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d


def save(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, p)


def usage_ok():
    """Nothing that needs Claude starts while the usage limit is hit."""
    try:
        from jarvis.spine import limits
        u = limits.until_time()
        return (False, f"usage limit until {u}") if u else (True, "")
    except Exception as e:
        return True, f"limits unavailable ({e.__class__.__name__})"


def parse(values, workspace):
    """{repo#id: the fields the watch compares} from the code host's list. A record whose link and id disagree is dropped."""
    rx, prs = href_rx(workspace), {}
    for v in values:
        href = ((v.get("links") or {}).get("html") or {}).get("href", "") if isinstance(v, dict) else ""
        m = rx.match(str(href))
        if not m or str(v.get("id")) != m.group(2):          # a record whose link and id disagree is a copy slip: drop it
            continue
        parts = v.get("participants") or []
        prs[f"{m.group(1)}#{m.group(2)}"] = {
            "repo": m.group(1), "id": int(m.group(2)), "url": href, "title": str(v.get("title") or "")[:200],
            "state": v.get("state") or "", "author": ((v.get("author") or {}).get("display_name") or ""),
            "reviewers": sorted(x.get("display_name", "") for x in v.get("reviewers") or [] if isinstance(x, dict)),
            "approved": sorted(((p.get("user") or {}).get("display_name", "")) for p in parts
                               if isinstance(p, dict) and p.get("state") == "approved"),
            "changes": sorted(((p.get("user") or {}).get("display_name", "")) for p in parts
                              if isinstance(p, dict) and p.get("state") == "changes_requested"),
            "comments": int(v.get("comment_count") or 0), "tasks": int(v.get("task_count") or 0),
            "created_on": v.get("created_on") or "", "updated_on": v.get("updated_on") or ""}
    return prs


def fetch(name, workspace):
    op, inputs = calls(workspace)[name]
    prompt = PROMPT.format(tool=TOOL, op=op, inputs=json.dumps(inputs), fields=json.dumps(FIELDS))
    env = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS="20000", CLAUDE_CODE_MCP_CONNECTOR_PREWAIT_MS="20000")
    os.makedirs(DIR, exist_ok=True)
    r = subprocess.run([paths.claude_bin() or "claude", "-p", prompt, "--model", config.model("quick"), "--output-format", "json", "--tools", "",
                        "--allowedTools", TOOL], capture_output=True, text=True, timeout=600, env=env, cwd=DIR,
                       stdin=subprocess.DEVNULL)
    out = json.loads(r.stdout or "{}")
    text = out.get("result", "") if isinstance(out, dict) else ""
    m = re.search(r"\{.*\}", text, re.S)
    one = json.loads(m.group(0)) if m else None
    if not isinstance(one, dict) or not one.get("ok") or not isinstance(one.get("values"), list):
        raise ValueError(f"{name}: {(one or {}).get('error') or text[:160]!r}")
    return parse(one["values"], workspace)


def when(iso):
    try:
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone().strftime("%d %b %H:%M")
    except ValueError:
        return ""


def where(p):
    return f"{p['repo']} PR {p['id']}"


def names(xs):
    return ", ".join(xs) if xs else "none"


def events(prev, cur, baseline):
    """(actions, fyi) from the previous snapshot to this one."""
    acts, fyi = [], []
    for k, p in sorted(cur["review"].items()):
        if baseline or k not in prev.get("review", {}):
            acts.append({"src": "bitbucket", "who": p["author"], "where": where(p), "when": when(p["updated_on"]), "link": p["url"],
                         "title": f"Review requested: {p['title']}",
                         "points": [{"text": f"Review {p['author']}'s PR \"{p['title']}\" ({where(p)}).",
                                     "note": f"Reviewers: {names(p['reviewers'])}. Approved so far: {names(p['approved'])}. "
                                             f"{p['comments']} comments, {p['tasks']} open tasks."}]})
    pm = prev.get("mine", {})
    for k, p in sorted(cur["mine"].items()):
        o = pm.get(k)
        if not o:
            if not baseline and p["state"] == "OPEN":
                fyi.append({"src": "bitbucket", "link": p["url"], "text": f"Your PR is open: {p['title']} ({where(p)})."})
            continue
        what = []
        if p["state"] != o["state"]:
            what.append({"MERGED": "it was merged", "DECLINED": "it was declined"}.get(p["state"], f"its state is now {p['state']}"))
        new_ok = [x for x in p["approved"] if x not in o["approved"]]
        new_ch = [x for x in p["changes"] if x not in o["changes"]]
        if new_ok:
            what.append(f"approved by {names(new_ok)}")
        if new_ch:
            what.append(f"changes requested by {names(new_ch)}")
        if p["comments"] > o["comments"]:
            what.append(f"{p['comments'] - o['comments']} new comment(s)")
        if p["tasks"] > o["tasks"]:
            what.append(f"{p['tasks'] - o['tasks']} new task(s)")
        if what:
            need = bool(new_ch or p["comments"] > o["comments"] or p["tasks"] > o["tasks"])
            item = {"src": "bitbucket", "who": "Bitbucket", "where": where(p), "when": when(p["updated_on"]), "link": p["url"],
                    "title": f"Your PR: {p['title']}",
                    "points": [{"text": f"Your PR \"{p['title']}\" ({where(p)}): {'; '.join(what)}." +
                                (" Read and answer the comments or requested changes." if need else ""),
                                "note": f"Approved by: {names(p['approved'])}."}]}
            (acts if need else fyi).append(item if need else {"src": "bitbucket", "link": p["url"], "text": item["points"][0]["text"]})
    po = prev.get("open", {})
    cut = (now() - dt.timedelta(days=7)).isoformat()
    for k, p in sorted(cur["open"].items(), key=lambda kv: kv[1]["created_on"]):
        if k in cur["mine"] or k in cur["review"]:
            continue
        fresh = (k not in po) if not baseline else p["created_on"] >= cut
        if fresh:
            fyi.append({"src": "bitbucket", "link": p["url"], "text": f"New PR by {p['author']}: {p['title']} ({where(p)}, opened "
                                                                        f"{when(p['created_on'])}; reviewers {names(p['reviewers'])}; approved by {names(p['approved'])})."})
    return acts, fyi


def main():
    dry, force = "--dry-run" in sys.argv, "--force" in sys.argv
    workspace = ws()
    if not workspace:
        return "not set up: no code workspace in settings (sources.code_workspace)"
    os.makedirs(DIR, exist_ok=True)
    st = load(STATE, {})
    if not force and time.time() - st.get("ts", 0) < EVERY_S:
        return "not due"
    ok, why = usage_ok()
    if not ok:
        return why
    t0, cur, errs = now(), {}, []
    for name in calls(workspace):
        try:
            cur[name] = fetch(name, workspace)
        except Exception as e:
            errs.append(f"{e.__class__.__name__}: {str(e)[:200]}")
    if errs:                                          # never diff against a partial picture: it would invent changes
        st["error"], st["error_at"] = "; ".join(errs)[:600], t0.isoformat(timespec="seconds")
        if not dry:
            save(STATE, st)
        with open(LOG, "a") as f:
            f.write(json.dumps({"at": t0.isoformat(timespec="seconds"), "ok": False, "errors": errs}) + "\n")
        return "fetch failed: " + st["error"][:200]
    cur["mine"] = {**cur.pop("mine_merged"), **cur.pop("mine_declined"), **cur.pop("mine_open")}
    baseline = not st.get("snap")
    acts, fyi = events(st.get("snap", {}), cur, baseline)
    rid = ""
    if acts or fyi:
        rid = t0.strftime("%Y-%m-%d-%H%M%S") + "-bitbucket"
        for i, a in enumerate(acts, 1):
            a["id"] = f"a{i}"
        ru = {"id": rid, "title": "Bitbucket pull requests" + (" (first check)" if baseline else ""), "kind": "bitbucket",
              "period": (when(st.get("at")) + " to " if st.get("at") else "") + t0.strftime("%d %b %H:%M"),
              "source": f"Bitbucket (the {workspace} workspace, every repository {config.load().who()} can read) through the Atlassian connector, read-only. "
                        "Nothing was approved, commented or merged.",
              "by": "Jarvis pull-request watch (tools/prwatch.py)", "created": t0.strftime("%Y-%m-%d"),
              "window_start": st.get("at") or (t0 - dt.timedelta(days=7)).isoformat(timespec="seconds"),
              "window_end": t0.isoformat(timespec="seconds"), "sources_checked": {"bitbucket": "ok"},
              "actions": acts, "fyi": fyi}
        if not dry:
            save(os.path.join(ROUNDUPS, rid + ".json"), ru)
        else:
            print(json.dumps(ru, indent=1))
    counts = {k: len(v) for k, v in cur.items()}
    if not dry:
        save(STATE, {"ts": time.time(), "at": t0.isoformat(timespec="seconds"), "snap": cur, "counts": counts, "error": ""})
        with open(LOG, "a") as f:
            f.write(json.dumps({"at": t0.isoformat(timespec="seconds"), "ok": True, "counts": counts, "actions": len(acts),
                                "fyi": len(fyi), "roundup": rid, "secs": round((now() - t0).total_seconds())}) + "\n")
    return f"ok {counts}; {len(acts)} actions, {len(fyi)} fyi" + (f" -> {rid}" if rid else "")


if __name__ == "__main__":
    print(main())
