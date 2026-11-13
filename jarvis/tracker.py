"""Direct Jira Cloud calls, made with the owner's own API token. Off unless they turn it on (settings: tracker.use_token,
tracker.site, tracker.email): by default Jarvis does Jira the way Claude does, through the Atlassian connector.

The token lives in the macOS Keychain (service "jarvis-jira", account = their Atlassian e-mail) and is read only at call
time; it is never written to disk or returned by any route. Every write has a dry-run form (the screen shows that plan
and asks before the real call), skips anything already present, and re-reads after an error before retrying, because a
"failed" call sometimes landed.
"""
from jarvis import config
import base64, datetime as dt, json, os, subprocess, urllib.error, urllib.parse, urllib.request

KEYCHAIN = 'jarvis-jira'


def _cfg():
    c = config.load()
    own = [str(p).strip().upper() for p in c.get('tracker.own_projects') or [] if str(p).strip()]
    return {'site': str(c.get('tracker.site') or '').strip().rstrip('/'), 'email': str(c.get('tracker.email') or '').strip(),
            'use_token': bool(c.get('tracker.use_token')), 'project': own[0] if own else ''}


def setup_line():
    """What the owner runs once in Terminal to store the token (it asks for the token; nothing is shown or kept elsewhere)."""
    return f"security add-generic-password -U -s {KEYCHAIN} -a {_cfg()['email'] or '<your Atlassian e-mail>'} -w"


def use_token():
    return _cfg()['use_token']


class NoToken(Exception):
    pass


class JiraError(Exception):
    def __init__(self, code, text):
        super().__init__(f'Jira {code}: {text}')
        self.code = code


def _token():
    c = _cfg()
    if not c['use_token']:
        raise NoToken('Jarvis does Jira through the Atlassian connector (no token).')
    if not c['site'] or not c['email']:
        raise NoToken('Direct Jira calls need tracker.site and tracker.email in settings.')
    try:
        p = subprocess.run(['security', 'find-generic-password', '-s', KEYCHAIN, '-a', c['email'], '-w'], capture_output=True, text=True)
    except OSError:
        raise NoToken('The token store of this computer is not supported yet; use the Atlassian connector.') from None
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()
    raise NoToken(f'No Jira token in the Keychain. In Terminal run: {setup_line()}')


def call(method, path, body=None, params=None):
    token, c = _token(), _cfg()                        # raises NoToken before any address is built
    url = c['site'] + path + ('?' + urllib.parse.urlencode(params) if params else '')
    auth = base64.b64encode(f"{c['email']}:{token}".encode()).decode()
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Authorization': 'Basic ' + auth, 'Accept': 'application/json',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read() or b'{}')
            msg = '; '.join(j.get('errorMessages', []) + [f'{k}: {v}' for k, v in (j.get('errors') or {}).items()])
        except Exception:
            msg = ''
        raise JiraError(e.code, msg or e.reason)
    except urllib.error.URLError as e:
        raise JiraError(0, f'cannot reach Jira ({e.reason})')


# ---------- helpers ----------
def adf(text):
    paras = [p for p in str(text or '').split('\n\n') if p.strip()] or ['']
    content = []
    for p in paras:
        nodes = []
        for i, line in enumerate(p.split('\n')):
            if i:
                nodes.append({'type': 'hardBreak'})
            if line:
                nodes.append({'type': 'text', 'text': line})
        content.append({'type': 'paragraph', 'content': nodes})
    return {'type': 'doc', 'version': 1, 'content': content}


def adf_text(node):
    if not node:
        return ''
    if isinstance(node, str):
        return node
    out = []

    def walk(n):
        if n.get('type') == 'text':
            out.append(n.get('text', ''))
        elif n.get('type') == 'hardBreak':
            out.append('\n')
        for c in n.get('content', []) or []:
            walk(c)
        if n.get('type') == 'paragraph':
            out.append('\n\n')
    walk(node)
    return ''.join(out).strip()


def me():
    j = call('GET', '/rest/api/3/myself')
    return {'accountId': j['accountId'], 'name': j.get('displayName', '')}


def status():
    c = _cfg()
    try:
        m = me()
        return {'configured': True, 'ok': True, 'name': m['name'], 'site': c['site']}
    except NoToken as e:
        return {'configured': False, 'ok': False, 'error': str(e), 'setup': setup_line(), 'site': c['site'], 'email': c['email'],
                **({'via': 'connector'} if not c['use_token'] else {})}
    except JiraError as e:
        return {'configured': True, 'ok': False, 'error': str(e), 'setup': setup_line(), 'site': c['site'], 'email': c['email']}


def search(jql, fields, limit=100):
    j = call('POST', '/rest/api/3/search/jql', {'jql': jql, 'fields': fields, 'maxResults': limit})
    return j.get('issues', [])


_types = {}


def _project():
    p = _cfg()['project']
    if not p:
        raise JiraError(0, 'no ticket project is listed in settings (tracker.own_projects)')
    return p


def issue_types():
    if not _types:
        j = call('GET', f'/rest/api/3/issue/createmeta/{_project()}/issuetypes')
        for t in j.get('issueTypes', j.get('values', [])):
            _types[t['name'].lower()] = {'id': t['id'], 'subtask': t.get('subtask', False)}
    return _types


def _jql_str(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"')


def create_issue(summary, description, type_name='Task', parent='', link='', account=None):
    """Create one issue assigned to the owner (reusing an identical one they opened in the last 2 days)."""
    account, PROJECT = account or me()['accountId'], _project()
    dup = [i for i in search(f'project = {PROJECT} AND reporter = currentUser() AND created >= -2d AND summary ~ "{_jql_str(summary)}"',
                             ['summary']) if (i['fields'].get('summary') or '').strip() == summary.strip()]
    if dup:
        return {'key': dup[0]['key'], 'deduped': True}
    types = issue_types()
    t = types.get((type_name or 'Task').lower()) or types.get('task') or next(iter(types.values()))
    fields = {'project': {'key': PROJECT}, 'summary': summary[:250], 'issuetype': {'id': t['id']},
              'assignee': {'accountId': account}, 'description': adf(description)}
    if t['subtask']:
        if not parent:
            raise JiraError(0, 'a Sub-task needs a parent ticket')
        fields['parent'] = {'key': parent}
    try:
        key = call('POST', '/rest/api/3/issue', {'fields': fields})['key']
    except JiraError:
        dup = [i for i in search(f'project = {PROJECT} AND reporter = currentUser() AND created >= -1d AND summary ~ "{_jql_str(summary)}"',
                                 ['summary']) if (i['fields'].get('summary') or '').strip() == summary.strip()]
        if not dup:
            raise
        key = dup[0]['key']
    note = ''
    if link:
        try:
            call('POST', '/rest/api/3/issueLink', {'type': {'name': 'Relates'}, 'inwardIssue': {'key': key},
                                                   'outwardIssue': {'key': link}})
        except JiraError as e:
            note = f'created, but the link to {link} failed: {e}'
    return {'key': key, 'note': note}


def create_tickets(items, ids=None, dry=True, write_back=None):
    ready = [t for t in items if t.get('status') == 'ready' and (not ids or t['id'] in ids)]
    if dry:
        return {'ok': True, 'dry': True, 'plan': [{'id': t['id'], 'summary': t.get('summary', ''), 'type': t.get('type', 'Task'),
                                                   'parent': t.get('parent', ''), 'link': t.get('link', ''),
                                                   'reuse_key': t.get('reuse_key') or ''} for t in ready]}
    account = me()['accountId']
    out = []
    for t in ready:
        now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        try:
            if t.get('reuse_key'):
                res = {'key': t['reuse_key'], 'reused': True}
            else:
                res = create_issue(t.get('summary') or 'Untitled', t.get('description', ''), t.get('type', 'Task'),
                                   t.get('parent', ''), t.get('link', ''), account)
            if write_back:
                write_back(t['id'], {'status': 'created', 'key': res['key'], 'created_at': now,
                                     'reused': bool(res.get('reused')), 'claude_note': res.get('note', '')})
            out.append({'id': t['id'], 'ok': True, **res})
        except (JiraError, NoToken) as e:
            out.append({'id': t['id'], 'ok': False, 'error': str(e)})
    return {'ok': True, 'dry': False, 'result': out}
