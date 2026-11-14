"""The deploy gate as a part the owner switches on (settings: modules.deploy_gate, and [gate] for what it watches):
every 30 minutes, in its own process, it looks at the owner's repositories and reviews what is due (gate/gate.py). The
screen reads what the gate wrote; the two presses it offers start a review on this computer or lift a pause. Nothing
here, and nothing in the gate, pushes, merges, deploys or sends anything.

    GET  /api/deploy_gate           what is set up, the changes with their state, the open findings, the gate's health
    GET  /api/deploy_gate_verdict   ?id=<candidate>: one verdict file's text
    POST /api/deploy_gate_review    {"id": "<candidate>"} or {}: review one change now, or start a run
    POST /api/deploy_gate_pause     {"on": true, "why": "..."} or {"on": false}: hold or resume automatic reviews
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess

from jarvis import paths

_mods = {}


def _gate(name):
    """A file of gate/ as a module, loaded by its path (the folder is not a package and is never put on the import path
    of the server)."""
    if name not in _mods:
        spec = importlib.util.spec_from_file_location('jarvis_gate_' + name, str(paths.repo('gate', name + '.py')))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _mods[name] = m
    return _mods[name]


def _root():
    return str(paths.at('deploy_gate'))


def _load(name, default):
    try:
        with open(os.path.join(_root(), name), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _start(*args):
    paths.ensure()
    log = open(str(paths.logs('deploy_gate.log')), 'a')
    return subprocess.Popen([paths.python(), str(paths.repo('gate', 'gate.py')), *args], cwd=str(paths.home()),
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def _run():
    return _start('run')


def _settings():
    """What the settings say, for the screen: ready or why not, and what is watched (never a command, never a secret)."""
    try:
        osdep = _gate('osdep')
        if osdep.why_not():
            return {'ready': False, 'why': osdep.why_not(), 'repos': [], 'problems': []}
        return _gate('gateconf').load().as_dict()
    except Exception as e:  # noqa: BLE001  a settings file that cannot be read is said, not raised
        return {'ready': False, 'why': f'the settings could not be read ({e.__class__.__name__}: {str(e)[:160]})', 'repos': [], 'problems': []}


def _running():
    """Is a run or a review going on now? (the gate's own locks say so; they die with their process)"""
    try:
        osdep, root = _gate('osdep'), _root()
        if osdep.held(os.path.join(root, 'gate.lock')):
            return True
        claims = os.path.join(root, 'claims')
        return any(osdep.held(os.path.join(claims, f)) for f in (os.listdir(claims) if os.path.isdir(claims) else []) if f.endswith('.lock'))
    except Exception:  # noqa: BLE001
        return False


_KEEP = ('id', 'repo', 'kind', 'ref', 'sha', 'author', 'date', 'stage', 'target', 'status', 'verdict', 'reviewed_at', 'first_seen',
         'commits_ahead', 'tickets', 'replaces', 'mine', 'prior', 'prior_results', 'fixed_prior', 'prior_fixed_count', 'last_verdict',
         'rereview_stuck', 'same_code_as')
_WORST = {'NO-GO': 0, 'NEEDS-INFO': 1, 'GO-WITH-NOTES': 2, 'GO': 3}


def _findings(cands):
    """The re-check's findings for the screen (gate/recheck.py): what is still in the code as it is now, one row per
    place in the code however many reviews found it; what later commits changed; what only a new run can tell."""
    rc = _load('recheck.json', {})
    by_id = {c['id']: c for c in cands}
    issues, fixed, unchecked, push = {}, [], [], []
    for cid, v in (rc.get('verdicts') or {}).items():
        c = by_id.get(cid)
        if not c:
            continue                                    # replaced since the re-check ran
        c['recheck'] = {k: v.get(k) for k in ('still_there', 'likely_fixed', 'needs_rerun', 'commits_on_top')}
        for f in v.get('findings') or []:
            sev = f.get('severity') or ('blocking' if v['verdict'] in ('NO-GO', 'NEEDS-INFO') else 'note')
            base = {'title': f.get('title'), 'label': f.get('label') or '', 'repo': v['repo'], 'verdict': v['verdict'], 'candidate_id': cid,
                    'ref': v['ref'], 'sha': v['sha'], 'reviewed': c.get('reviewed_at') or v.get('date'), 'n': f.get('n'), 'severity': sev}
            if f['status'] == 'STILL THERE':
                an = f.get('anchors') or []
                # the same place in the code seen by DIFFERENT reviews is one problem; a finding with no place in the
                # code is never merged, and two findings of one review are never merged
                key = f"{v['repo']}|{an[0].get('path')}|{an[0].get('text')}" if an else f"{v['repo']}|{cid}|{f.get('n')}"
                if an and cid in issues.get(key, {}).get('cids', ()):
                    key = f"{key}|{cid}|{f.get('n')}"
                where = sorted({k for a in an for k, x in a['at'].items() if x.get('state') == 'there'}) or sorted(v.get('targets') or {})
                it = issues.setdefault(key, {**base, 'how': f.get('how', ''), 'places': [f"{a['path']}:{a['line']}" for a in an][:3],
                                             'code': an[0].get('text') if an else None, 'branches': [], 'tell': [], 'cids': [], 'also': []})
                it['branches'] = sorted(set(it['branches']) | set(where))
                it['tell'] = sorted(set(it['tell']) | set(f.get('tell') or []))
                if it['cids'] and cid not in it['cids']:
                    it['also'].append({'ref': v['ref'], 'title': f.get('title'), 'candidate_id': cid, 'n': f.get('n')})
                if cid not in it['cids']:
                    it['cids'].append(cid)
                if sev == 'blocking':
                    it['severity'] = 'blocking'           # blocking when any review's finding is
                if _WORST.get(v['verdict'], 9) < _WORST.get(it['verdict'], 9):
                    it['verdict'] = v['verdict']
            elif f['status'] == 'LIKELY FIXED':
                fixed.append({**base, 'fixed_by': (f.get('fixed_by') or [None])[0]})
            elif f['status'] == 'ABOUT THAT PUSH':
                push.append({**base, 'how': f.get('how', '')})
            else:
                unchecked.append({**base, 'status': f['status'], 'how': f.get('how', '')})
    out = sorted(issues.values(), key=lambda i: (i['severity'] != 'blocking', _WORST.get(i['verdict'], 9), str(i['repo'])))
    return {'recheck_at': rc.get('at'), 'issues': out, 'fixed': fixed, 'unchecked': unchecked, 'about_push': push}


def status(q=None):
    """GET /api/deploy_gate."""
    st, cands = _load('state.json', {}), []
    for c in (st.get('candidates') or {}).values():
        if not isinstance(c, dict) or c.get('status') == 'superseded':
            continue
        last = (c.get('attempts') or [{}])[-1]
        row = {k: c.get(k) for k in _KEEP}
        row.update(error=str(last.get('error') or '') if c.get('status') in ('retry', 'stuck') else '', attempts=len(c.get('attempts') or []),
                   has_verdict=os.path.isfile(str(c.get('verdict_path') or '')))
        cands.append(row)
    paused, pz = None, os.path.join(_root(), 'PAUSED')
    if os.path.exists(pz):
        try:
            paused = {'since': dt.datetime.fromtimestamp(os.path.getmtime(pz)).astimezone().isoformat(timespec='seconds'),
                      'why': open(pz, encoding='utf-8', errors='replace').read().strip()[:400]}
        except OSError:
            paused = {'since': None, 'why': ''}
    s = _settings()
    return {'ready': bool(s.get('ready')), 'why': s.get('why') or '', 'settings': s, 'paused': paused, 'scanned_at': st.get('scanned_at'),
            'problems': st.get('problems') or {}, 'offline': st.get('offline'), 'fetched_at': st.get('fetched_at'), 'candidates': cands,
            'health': _load('health.json', {}), 'running': _running(), **_findings(cands)}


def verdict(q=None):
    """GET /api/deploy_gate_verdict?id=...: the verdict file of one change. Only a file inside the gate's own folder."""
    c = (_load('state.json', {}).get('candidates') or {}).get(str((q or {}).get('id') or ''))
    if not c:
        return {'text': '', 'error': 'unknown change'}
    p = str(c.get('verdict_path') or '')
    if not os.path.realpath(p).startswith(os.path.realpath(_root()) + os.sep) or not os.path.isfile(p):
        return {'text': '', 'error': 'no verdict yet'}
    with open(p, encoding='utf-8', errors='replace') as f:
        return {'text': f.read()[:200000]}


def review(b):
    """POST /api/deploy_gate_review: the owner pressed Review now, for one change (id) or for everything that is due.
    It starts at once, in its own process; the gate's own locks keep it single."""
    s = _settings()
    if not s.get('ready'):
        return {'ok': False, 'error': s.get('why') or 'the deploy gate is not set up'}
    cid = str((b or {}).get('id') or '')
    if cid and cid not in (_load('state.json', {}).get('candidates') or {}):
        return {'ok': False, 'error': 'unknown change'}
    _start(*(['review', '--id', cid] if cid else ['run']))
    return {'ok': True, 'started': 'review' if cid else 'run'}


def pause(b):
    """POST /api/deploy_gate_pause: hold automatic reviews (the gate still looks, and Review now still works), or lift
    the hold. A lifted note is kept beside the others, never deleted."""
    root = _root()
    pz, on = os.path.join(root, 'PAUSED'), bool((b or {}).get('on'))
    if on:
        os.makedirs(root, exist_ok=True)
        with open(pz, 'w', encoding='utf-8') as f:
            f.write(' '.join(str((b or {}).get('why') or 'paused from the screen').split())[:400] + '\n')
        return {'ok': True, 'paused': True}
    if os.path.exists(pz):
        os.replace(pz, os.path.join(root, 'PAUSED.lifted-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')))
    return {'ok': True, 'paused': False}


GET = {'/api/deploy_gate': status, '/api/deploy_gate_verdict': verdict}
POST = {'/api/deploy_gate_review': review, '/api/deploy_gate_pause': pause}


def jobs():
    return [('deploy_gate', 1800, _run)]
