#!/usr/bin/env python3
"""Pull requests and their review comments into the knowledge base, read like tickets. For a code host reached through
the Atlassian connector (Bitbucket); another host needs its own reader.

The local copies of a repository hold only commits; a pull request's description, reviewers, approvals and review
comments live on the code host. A small headless run (kb_live.fetch) calls only the connector's read tool; this code
takes the tool results word for word from that run's transcript (the model never re-types them). Read-only: nothing is
posted, approved or resolved.

What is read comes from settings ([sources]); with no workspace set, nothing is read:
  code_workspace   the workspace on the code host
  pr_repos         the repositories whose pull requests are read (default: the git repositories in the work folders)
  shared_repos     of those, the ones shared with many teams: only the pull requests whose title, branch or description
                   names the work (mail_terms, else work.search_words) are read
One unit per repository and month (by the pull request's creation date): brain/sources_extra/l_pr_<repo>_<YYYY-MM>.txt,
through the personal screen and the secrets vault; then kb_extra reads the units into notes and kb_fold files them.

    kb_prs.py sizes        how many PRs per repository and state (one cheap run)
    kb_prs.py fetch        every PR page, then the comments of each PR in scope; writes the units
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository: a tool run by itself finds the package
from jarvis import config, entities, paths
import datetime as dt, json, math, os, re, sys

HUB = str(paths.home())
paths.use_tools()
import kb_live as L  # noqa: E402

TOOL = 'mcp__claude_ai_Atlassian_MCP__executeRead'


def ws():
    return str(config.load().get('sources.code_workspace') or '').strip()


def repos():
    return [str(r).strip() for r in config.load().get('sources.pr_repos') or [] if str(r).strip()] or entities.repos()


def shared():
    return {str(r).strip() for r in config.load().get('sources.shared_repos') or [] if str(r).strip()}


def q():
    """The code host's own filter for a shared repository: the pull requests that name the work, so the others are
    never fetched (a page with descriptions is costly)."""
    return ' OR '.join(f'{f} ~ "{w}"' for w in L.mail_terms() for f in ('title', 'description', 'source.branch.name'))


STATES = ['MERGED', 'DECLINED', 'OPEN']
PAGE = 25                                  # a page with descriptions stays small enough for the fetch run's context
PR_FIELDS = ['size', 'page', 'values.id', 'values.title', 'values.state', 'values.description', 'values.author.display_name',
             'values.created_on', 'values.updated_on', 'values.comment_count', 'values.task_count',
             'values.source.branch.name', 'values.destination.branch.name', 'values.merge_commit.hash',
             'values.reviewers.display_name', 'values.participants.user.display_name', 'values.participants.state',
             'values.closed_by.display_name']
C_FIELDS = ['size', 'page', 'values.id', 'values.content.raw', 'values.user.display_name', 'values.created_on',
            'values.inline.path', 'values.inline.to', 'values.inline.from', 'values.parent.id', 'values.deleted']
STATE = os.path.join(HUB, 'state', 'kb_prs.json')
PROMPT = """Use ONLY the tool {tool}. Make each call below exactly as written, one after another, with cloudId "{cloud}"
(the name, the inputs and the responseFields exactly as given; never change or drop one). If a call fails, retry it once.
Do not summarise or repeat any result. When every call is done, reply only DONE and the number of calls.

{calls}"""


def _call(name, inputs, fields):
    return f'- name "{name}", inputs {json.dumps(inputs)}, responseFields {json.dumps(fields)}'


def _run(calls, label, per=25):
    """[(input, parsed data or None)] for the calls, in fetch runs of at most `per` calls each."""
    out, cost = [], 0.0
    for i in range(0, len(calls), per):
        part = calls[i:i + per]
        res, c, sid = L.fetch(PROMPT.format(tool=TOOL, cloud=L.cloud(), calls='\n'.join(part)), [TOOL],
                              turns=len(part) * 2 + 6, expect=len(part))
        cost += c
        for inp, text in res:
            try:
                d = json.loads(text)
                if isinstance(d, list):                  # a large result kept in a file: a list of content blocks
                    d = json.loads(''.join(x.get('text', '') for x in d if isinstance(x, dict)))
                d = d.get('data', d) if isinstance(d, dict) else None
            except ValueError:
                m = re.search(r'\{.*\}', text, re.S)
                try:
                    d = json.loads(m.group(0)).get('data') if m else None
                except ValueError:
                    d = None
            out.append(((inp or {}).get('inputs') or {}, d))
        L.log(f'prs: {label} {i + len(part)}/{len(calls)} calls, ${cost:.2f}')
    return out, cost


def _load():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def _save(s):
    json.dump(s, open(STATE + '.tmp', 'w'), indent=1); os.replace(STATE + '.tmp', STATE)


def _inputs(repo, state, **kw):
    d = {'workspaceId': ws(), 'repoId': repo, 'state': state, **kw}
    if repo in shared() and q():
        d['q'] = q()
    return d


def sizes():
    calls = [_call('listBitbucketRepoPullRequests', _inputs(r, s, pagelen=1), ['size']) for r in repos() for s in STATES]
    got, cost = _run(calls, 'sizes')
    n = {}
    for inp, d in got:
        if d and 'size' in d:
            n[f"{inp.get('repoId')}|{inp.get('state')}"] = int(d['size'])
    s = _load(); s['sizes'] = n; s['sizes_cost'] = cost; s['sizes_at'] = dt.datetime.now().isoformat(timespec='seconds'); _save(s)
    return n, cost


def relevant(repo, pr):
    if repo not in shared():
        return True
    text = ' '.join(str(pr.get(k) or '') for k in ('title', 'description')) + ' ' + \
        str(((pr.get('source') or {}).get('branch') or {}).get('name') or '')
    return L._names_work(text)


def fetch():
    s = _load()
    n = s.get('sizes') if s.get('sizes_q') == q() + '|' + ws() + '|' + ','.join(repos()) else None
    if not n:
        n = sizes()[0]; s = _load(); s['sizes_q'] = q() + '|' + ws() + '|' + ','.join(repos()); _save(s)
    calls = []
    for key, k in sorted(n.items()):
        repo, st = key.split('|')
        for p in range(1, math.ceil(k / PAGE) + 1):
            calls.append(_call('listBitbucketRepoPullRequests', _inputs(repo, st, page=p, pagelen=PAGE), PR_FIELDS))
    got, cost = _run(calls, 'pages', per=1)   # one page a run: a run re-reads every earlier result
    prs, missing = {}, []
    for inp, d in got:
        if not d or not isinstance(d.get('values'), list):
            missing.append(inp); continue
        for v in d['values']:
            if isinstance(v, dict) and v.get('id') is not None:
                prs[(inp.get('repoId'), int(v['id']))] = v
    keep = {k: v for k, v in prs.items() if relevant(k[0], v)}
    L.log(f'prs: {len(prs)} PRs read, {len(keep)} in scope; {len(missing)} pages failed')
    ccalls = []
    for (repo, i), v in sorted(keep.items()):
        if int(v.get('comment_count') or 0) > 0:
            for p in range(1, math.ceil(int(v['comment_count']) / 100) + 1):
                ccalls.append(_call('listBitbucketRepoPullRequestComments', {'workspaceId': ws(), 'repoId': repo, 'prId': i, 'page': p, 'pagelen': 100}, C_FIELDS))
    cgot, ccost = _run(ccalls, 'comments', per=3) if ccalls else ([], 0.0)
    comments = {}
    for inp, d in cgot:
        if d and isinstance(d.get('values'), list):
            comments.setdefault((inp.get('repoId'), int(inp.get('prId'))), []).extend(d['values'])
    units = write_units(keep, comments)
    s.update({'fetched_at': dt.datetime.now().isoformat(timespec='seconds'), 'prs': len(prs), 'in_scope': len(keep),
              'with_comments': len(ccalls), 'pages_failed': missing, 'cost': cost + ccost, 'units': units})
    _save(s)
    L.log(f'prs: {len(units)} units written, ${cost + ccost:.2f}')
    return s


def _names(xs, key='display_name'):
    return ', '.join(sorted({(x or {}).get(key, '') for x in xs or [] if isinstance(x, dict)} - {''})) or 'none'


def _pr_text(repo, v, cs):
    parts = v.get('participants') or []
    appr = _names([p.get('user') for p in parts if isinstance(p, dict) and p.get('state') == 'approved'])
    chg = _names([p.get('user') for p in parts if isinstance(p, dict) and p.get('state') == 'changes_requested'])
    src = ((v.get('source') or {}).get('branch') or {}).get('name', '?')
    dst = ((v.get('destination') or {}).get('branch') or {}).get('name', '?')
    L0 = [f"=== PR #{v.get('id')} · {v.get('state')} · {v.get('title')}",
          f"repo {repo} · {src} -> {dst} · author {(v.get('author') or {}).get('display_name', '?')} · created {str(v.get('created_on'))[:16]} "
          f"· updated {str(v.get('updated_on'))[:16]}" + (f" · merge commit {str((v.get('merge_commit') or {}).get('hash'))[:8]}" if v.get('merge_commit') else '')
          + (f" · closed by {(v.get('closed_by') or {}).get('display_name')}" if v.get('closed_by') else ''),
          f"reviewers: {_names(v.get('reviewers'))} · approved: {appr} · changes requested: {chg} · comments {v.get('comment_count') or 0} · tasks {v.get('task_count') or 0}",
          f"link: https://bitbucket.org/{ws()}/{repo}/pull-requests/{v.get('id')}"]
    d = (v.get('description') or '').strip()
    L0.append('Description:\n' + (d if d else '(none)'))
    live = [c for c in cs if isinstance(c, dict) and not c.get('deleted')]
    if live:
        L0.append(f'Review comments ({len(live)}):')
        for c in sorted(live, key=lambda c: str(c.get('created_on'))):
            where = ''
            if c.get('inline'):
                ln = (c['inline'] or {}).get('to') or (c['inline'] or {}).get('from')
                where = f" [{(c['inline'] or {}).get('path')}{':' + str(ln) if ln else ''}]"
            reply = ' (reply)' if c.get('parent') else ''
            txt = ((c.get('content') or {}).get('raw') or '').strip()
            L0.append(f"- {str(c.get('created_on'))[:16]} · {(c.get('user') or {}).get('display_name', '?')}{reply}{where}: {txt}")
    return '\n'.join(L0)


def write_units(keep, comments):
    import kb_redact, kb_secrets_vault as SV
    by = {}
    for (repo, i), v in keep.items():
        by.setdefault((repo, str(v.get('created_on') or '')[:7] or 'undated'), []).append((i, v))
    names = []
    for (repo, month), items in sorted(by.items()):
        body = '\n\n'.join(_pr_text(repo, v, comments.get((repo, i), [])) for i, v in sorted(items))
        header = (f'Bitbucket pull requests: repository {repo}, created {month} ({len(items)} PRs, every state, with their '
                  f'review comments) | read {dt.date.today()} through the Atlassian connector, word for word')
        name = f'l_pr_{repo}_{month}'
        if L.screened_write(name, header, body):
            p = os.path.join(L.EXTRA, name + '.txt')
            t = open(p, errors='replace').read()
            if SV.has_secret(t):                         # 24 Oct rule: the whole text to the vault first, then redact
                SV.keep(p, t); kb_redact.redact_file(p)
            names.append(name)
    return names


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] in (['sizes'], ['fetch']) and not (ws() and repos() and L.cloud()):
        print('no pull requests to read: set sources.code_workspace and tracker.cloud_id (and sources.pr_repos, or keep a repository in a work folder)')
    elif a[:1] == ['sizes']:
        n, c = sizes(); print(json.dumps(n, indent=1), f'${c:.2f}')
    elif a[:1] == ['fetch']:
        s = fetch(); print({k: (v if not isinstance(v, list) else len(v)) for k, v in s.items() if k != 'sizes'})
    else:
        print(__doc__)
