#!/usr/bin/env python3
"""One tracker that follows every role: found -> CV built -> applied. Shared by jobserve-new-jobs and
targeted-cv-from-master (each skill ships an identical copy so both stay self-contained).

The tracker is a local CSV path OR a Google Sheets URL (https://docs.google.com/spreadsheets/d/<id>/...).
Columns (COLS order, used for a new tracker; an existing tracker keeps the member's own order):
  code,url,title,location,rate,rate_min,rate_max,ir35,agent,agency,first_seen,jd_file,fit,missing_terms,cv_pdf,
  cv_built_on,status,applied_on,rate_submitted,consultancy,client,notes,reason
  Tools fill: url..agency, first_seen, jd_file, fit, missing_terms, cv_pdf, cv_built_on, reason, and status new /
  cv_ready / not relevant. ir35 / rate_min / rate_max / agent come from the JD text via jd_fields.py (ir35 and agent
  only while blank; rate_min/max per day for a contract, per year for a permanent role). jd_file and cv_pdf are the
  Google Drive links of the JD and the CV once uploaded (a local path never replaces a link; jd_local() maps a JD link
  back to the text on disk). The member owns applied_on, rate_submitted (the rate sent), consultancy, client, notes
  and any manual status (applied/skipped/rejected/...).

Rules:
- rows are keyed by `code` (the short JobServe permalink code, e.g. hGGC9; else the long job code)
- tools never overwrite applied_on, rate_submitted, consultancy, client, notes, or a status the member set
- a row counts as applied when applied_on or rate_submitted is filled (an older tracker's "applied" = yes also counts)
- statuses may be numbered ("3-applied", "5.sent-to-client"): compared without the number; tools write numbered
  statuses (1-new, 2-cv_ready, 8-not relevant) when the tracker already uses them or config "status_style" is "numbered"
- extra columns the member adds are kept; CSV writes go to a temp file then replace the CSV
- if the CSV cannot be replaced (e.g. locked by Excel) rows go to <name>.pending.csv and are merged next time

Google Sheet: reads use the Apps Script web app in config "tracker_sheet" (script_url, token, tab), else the
public CSV export (read-only). Writes need the web app (references/tracker-apps-script.gs) and send only the
changed cells. If the sheet cannot be reached, records go to <config dir>/tracker-<sheet id>.pending.csv and
are applied next run. Config: $JOBSERVE_CONFIG or <job-search>/.jobsearch/config.json; the token may also
come from $JOBSERVE_TRACKER_TOKEN. The same web app can upload CV PDFs into Drive (upload(); config "cv_upload",
used by targeted-cv-from-master).

Everything the skills keep lives in the member's job-search folder, in <job-search>/.jobsearch/ (see home()):
nothing is written to ~/.config, ~/.cache, /tmp or the Trash.

  tracker.py seed TRACKER --applied A.csv [--available B.csv]   # import URL,Title,Location,Rate lists
  tracker.py list TRACKER [--status new,cv_ready]
  tracker.py upload-jds TRACKER [--dry-run]                       # JDs still on disk -> Drive; jd_file = the link
  tracker.py enrich TRACKER [--migrate] [--force C1,C2] [--dry-run]  # ir35, rate_min/max (per day | per year), agent, JD links
  tracker.py push TRACKER --from LOCAL.csv                       # upsert a local CSV into TRACKER (e.g. a sheet)
  tracker.py home [--create]                                     # print the job-search folder (make .jobsearch/)
  tracker.py migrate-home [--dry-run]                            # move files older versions kept in ~/.config, ~/.cache
"""
import base64, time, csv, glob, io, json, os, re, sys, tempfile, urllib.error, urllib.request
from datetime import date, datetime

PY_MIN = (3, 12)   # the skills target Python 3.14 (see local_env.sh); older interpreters are end-of-life or near it
if sys.version_info < PY_MIN:
    sys.exit(f'FAIL: Python {sys.version_info.major}.{sys.version_info.minor} is too old: the job-search skills need '
             f'{PY_MIN[0]}.{PY_MIN[1]}+ (3.14 recommended). Run scripts through the launchers: bash <skill>/scripts/py '
             '<script.py>, or bash <skill>/scripts/cv: they fetch Python 3.14 into your job-search folder with uv.')

COLS = ['code', 'url', 'title', 'location', 'rate', 'rate_min', 'rate_max', 'ir35', 'agent', 'agency', 'first_seen',
        'jd_file', 'fit', 'missing_terms', 'cv_pdf', 'cv_built_on', 'status', 'applied_on', 'rate_submitted',
        'consultancy', 'client', 'notes', 'reason']
MEMBER_COLS = {'applied', 'applied_on', 'rate_submitted', 'consultancy', 'client', 'notes'}   # "applied": older trackers
FILL_ONCE = {'first_seen', 'ir35', 'agent'}    # tools fill these only while blank: a member correction stays
FIELD_COLS = ('ir35', 'rate_min', 'rate_max', 'agent')   # derived from the JD text by jd_fields.py (enrich, find_jobs)
TOOL_STATUS = {'', 'new', 'cv_ready'}          # any other status was set by the member and is kept
DONE_STATUS = {'applied', 'skipped', 'rejected', 'interview', 'offer', 'closed', 'not relevant', 'contacted',
               'sent-to-client', 'dropped-out', 'expired'}
NUMBERED = {'new': '1-new', 'cv_ready': '2-cv_ready', 'applied': '3-applied', 'contacted': '4-contacted',
            'sent-to-client': '5.sent-to-client', 'dropped-out': '7-dropped-out', 'not relevant': '8-not relevant'}
SHEET_RE = re.compile(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)')
_DIRTY = {}                                    # sheet path -> {code: changed keys} from pending records applied in load()

class TrackerUnavailable(Exception):
    pass

class NoHome(SystemExit):
    """Raised by home(create=True) outside a job-search folder (exits with the message unless caught)."""

# ---------------------------------------------------------------- paths, config, statuses
def is_sheet(p): return bool(p) and bool(SHEET_RE.match(str(p).strip()))

def resolve(p):
    """Sheet URLs unchanged; local paths expanded to absolute."""
    if not p: return p
    p = str(p).strip()
    return p if is_sheet(p) else os.path.abspath(os.path.expanduser(p))

def exists(p): return is_sheet(p) or (bool(p) and os.path.exists(p))

# ---------------------------------------------------------------- the job-search folder (nothing is written outside it)
LOCAL = '.jobsearch'   # <job-search>/.jobsearch/: config, answer memory, venv, tmp, trash, pending rows, logs

def _skill_dir(d):
    """True inside one of these skills (SKILL.md + scripts/tracker.py) or the skills repo (check_release.sh +
    jobserve-new-jobs/), here or above: never a member's folder."""
    j = os.path.join
    while True:
        if (os.path.exists(j(d, 'SKILL.md')) and os.path.exists(j(d, 'scripts', 'tracker.py'))) or \
           (os.path.exists(j(d, 'check_release.sh')) and os.path.isdir(j(d, 'jobserve-new-jobs'))): return True
        up = os.path.dirname(d)
        if up == d: return False
        d = up

def _find_local(start):
    cur = os.path.abspath(start)
    while not os.path.isdir(os.path.join(cur, LOCAL)):
        up = os.path.dirname(cur)
        if up == cur: return None
        cur = up
    return cur

def home(create=False):
    """The member's job-search folder: $JOB_SEARCH_HOME, else the nearest folder holding .jobsearch/ at or above the
    current directory (or above $CV_WS, for build agents started elsewhere), else the current directory.
    create=True makes <home>/.jobsearch (chmod 700), refusing $HOME, / and skill folders."""
    env = os.environ.get('JOB_SEARCH_HOME')
    if env: d = os.path.abspath(os.path.expanduser(env))
    else:
        ws = os.environ.get('CV_WS')
        d = _find_local(os.getcwd()) or (ws and _find_local(os.path.dirname(os.path.abspath(ws)))) or os.path.abspath(os.getcwd())
    if create and not os.path.isdir(os.path.join(d, LOCAL)):
        if d in (os.path.abspath(os.path.expanduser('~')), os.path.abspath(os.sep)) or _skill_dir(d):
            raise NoHome(f'FAIL: {d} is not a job-search folder: run from your job-search folder (e.g. cd ~/job-search) '
                     'or set JOB_SEARCH_HOME')
        os.makedirs(os.path.join(d, LOCAL), mode=0o700, exist_ok=True)
        print(f'created {os.path.join(d, LOCAL)} (settings, caches and scratch files for the job-search skills)', file=sys.stderr)
    return d

def local(*parts, create=False):
    """A path inside <home>/.jobsearch; create=True makes .jobsearch (not the path itself)."""
    return os.path.join(home(create), LOCAL, *parts)

def use_local_tmp(create=False):
    """Scratch files go to <home>/.jobsearch/tmp (also for child processes) once a job-search folder exists."""
    if not (create or os.path.isdir(os.path.join(home(), LOCAL))): return None
    t = local('tmp', create=True); os.makedirs(t, exist_ok=True)
    tempfile.tempdir = os.environ['TMPDIR'] = t
    return t

def legacy_files():
    """Files earlier versions kept outside the job-search folder: [(kind, path)], existing ones only."""
    h = os.path.expanduser('~'); cfg = os.path.join(h, '.config', 'jobserve-new-jobs')
    cache = os.path.join(h, '.cache')
    out = [('config', os.path.join(cfg, 'config.json'))]
    out += [('pending', p) for p in sorted(glob.glob(os.path.join(cfg, 'tracker-*.pending.csv')))]
    out += [('log', os.path.join(cfg, 'daily.log'))]
    out += [('script', p) for p in sorted(glob.glob(os.path.join(cfg, '*.gs')))]
    out += [('memory', os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.join(h, '.config'),
                                    'targeted-cv-from-master', 'memory.json'))]
    out += [('cache', os.path.join(cache, n)) for n in ('targeted-cv-py', 'targeted-cv-ws', 'targeted-cv-venv')]
    return [(k, p) for k, p in out if os.path.exists(p)]

def migrate_home(dry=False):
    """Move the files legacy_files() finds into <home>/.jobsearch; delete the old caches (rebuilt on demand).
    Prints what it does, never ids (pending file names hold the sheet id). Returns the number of items handled."""
    import shutil
    items = legacy_files()
    if not items: print('nothing to migrate: no files outside the job-search folder'); return 0
    dest = local(create=not dry)
    tilde = lambda p: p.replace(os.path.expanduser('~'), '~', 1)
    for kind, p in items:
        if kind == 'cache':
            print(f'{"would delete" if dry else "deleted"} {tilde(p)} (rebuilt inside {LOCAL}/ on the next CV build)')
            if not dry: shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            continue
        name = {'config': 'config.json', 'memory': 'memory.json', 'log': 'daily.log'}.get(kind) or (
            os.path.basename(p) if kind == 'pending' else os.path.join('old', os.path.basename(p)))
        to = os.path.join(dest, name)
        shown = 'pending tracker rows' if kind == 'pending' else tilde(p)
        if kind == 'memory' and os.path.exists(to):        # both: keep the local answers, add the old ones it lacks
            if not dry:
                with open(to, encoding='utf-8') as f: cur = json.load(f)
                with open(p, encoding='utf-8') as f: old = json.load(f)
                with open(to, 'w', encoding='utf-8') as f: json.dump({**old, **cur}, f, indent=1)
                os.remove(p)
            print(f'{"would merge" if dry else "merged"} {shown} into {LOCAL}/memory.json'); continue
        if os.path.exists(to):
            print(f'WARN: kept {shown}: the {LOCAL}/ copy already exists (compare them, then delete the old one)'); continue
        print(f'{"would move" if dry else "moved"} {shown} -> {LOCAL}/' + ('' if kind == 'pending' else name)
              + (' (old Apps Script copy with the token in it: delete it once the sheet runs the v2 script)' if kind == 'script' else ''))
        if not dry:
            os.makedirs(os.path.dirname(to), exist_ok=True); shutil.move(p, to)
            if kind in ('config', 'pending', 'script'): os.chmod(to, 0o600)
    if not dry:
        for d in {os.path.dirname(p) for _k, p in items if _k != 'cache'} - {os.path.expanduser('~')}:
            try: os.rmdir(d); print(f'removed empty {tilde(d)}')
            except OSError: pass
        os.chmod(dest, 0o700)
    return len(items)

def config_path(create=False):
    return os.environ.get('JOBSERVE_CONFIG') or local('config.json', create=create)

_WARNED = []
def config():
    try:
        with open(config_path(), encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError):
        if not _WARNED and not os.environ.get('JOBSERVE_CONFIG') and any(k == 'config' for k, _ in legacy_files()):
            _WARNED.append(1)
            print(f'WARN: no {LOCAL}/config.json here, but an old one is in ~/.config: run from your job-search folder, '
                  f'or move it in with: bash {os.path.join(os.path.dirname(os.path.abspath(__file__)), "py")} tracker.py migrate-home', file=sys.stderr)
        return {}

def save_config(updates):
    """Merge `updates` into the config (dict values merged one level deep), written atomically and chmod 600:
    it holds private search ids and the web-app token. Returns the path."""
    path = config_path(create=True); d = os.path.dirname(path); os.makedirs(d, exist_ok=True)
    cfg = config()
    for k, v in updates.items():
        cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.json.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600); os.replace(tmp, path)
    return path

def configured():
    """The tracker named in the jobserve config (path or sheet URL), resolved; '' if none."""
    return resolve(config().get('tracker') or '')

def default_dir(p):
    """Folder for files that live 'next to the tracker' (jds-new/, ...): the CSV's folder, or for a sheet the
    config output_dir (else the current directory)."""
    if not is_sheet(p): return os.path.dirname(os.path.abspath(p))
    return os.path.abspath(os.path.expanduser(config().get('output_dir') or '.'))

def norm_status(s): return re.sub(r'^\s*\d+\s*[-.]\s*', '', s or '').strip().lower()

def applied(r):
    """The member applied: applied_on or rate_submitted is filled (an older tracker's "applied" = yes also counts)."""
    return (bool((r.get('applied_on') or '').strip()) or bool((r.get('rate_submitted') or '').strip())
            or (r.get('applied') or '').strip().lower() in ('yes', 'y', 'true', '1'))

def fmt_status(s, numbered):
    return NUMBERED.get(norm_status(s), s) if numbered else s

def _numbered(rows):
    style = (config().get('status_style') or '').lower()
    if style in ('numbered', 'plain'): return style == 'numbered'
    return any(re.match(r'^\s*\d+\s*[-.]', r.get('status') or '') for r in rows)

def code_of(s):
    """hGGC9 from http://www.jobserve.com/hGGC9, 'permalink:hGGC9', or a bare code."""
    s = (s or '').strip()
    if ':' in s and not s.lower().startswith('http'): s = s.split(':', 1)[1]
    m = re.search(r'jobserve\.com/(?:[a-z]{2}/[a-z]{2}/)?([0-9A-Za-z]+)(?:\.jsjob)?', s)
    if m: return m.group(1)
    return s if re.fullmatch(r'[0-9A-Za-z]+', s) else ''

def pending_path(path):
    if is_sheet(path):
        return os.path.join(os.path.dirname(config_path(create=True)), f'tracker-{SHEET_RE.match(path.strip()).group(1)}.pending.csv')
    return os.path.splitext(path)[0] + '.pending.csv'

# ---------------------------------------------------------------- google sheet I/O
def _sheet_conf():
    c = config().get('tracker_sheet') or {}
    return c.get('script_url', ''), os.environ.get('JOBSERVE_TRACKER_TOKEN') or c.get('token', ''), c.get('tab', '')

def _http(url, body=None, timeout=60):
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode('utf-8-sig')
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TrackerUnavailable(f'{url.split("?")[0]}: {e}')

def _sheet_get(path):
    url, tok, tab = _sheet_conf()
    if url:   # POST, so the token never travels in a URL
        text = _http(url, json.dumps({'token': tok, 'tab': tab, 'action': 'read'}).encode('utf-8'))
    else:
        gid = re.search(r'[#&?]gid=(\d+)', path)
        sid = SHEET_RE.match(path.strip()).group(1)
        text = _http(f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv' + (f'&gid={gid.group(1)}' if gid else ''))
    head = text.lstrip()[:200].lower()
    if head.startswith('{'):
        try: err = json.loads(text).get('error', 'unexpected reply')
        except ValueError: err = 'unexpected reply'
        raise TrackerUnavailable(f'sheet read failed: {err}')
    if head.startswith('<!doctype') or head.startswith('<html'):
        raise TrackerUnavailable('sheet read failed: got a web page, not CSV (sheet not shared by link, or script URL wrong)')
    return text

def _sheet_post(fields, rows):
    url, tok, tab = _sheet_conf()
    if not url: raise TrackerUnavailable('no "tracker_sheet.script_url" in config: the sheet is read-only')
    reply = _http(url, json.dumps({'token': tok, 'tab': tab, 'fields': fields, 'rows': rows}).encode('utf-8'))
    try: res = json.loads(reply)
    except ValueError:
        hint = ' (Apps Script error page: redeploy the latest references/tracker-apps-script.gs as a New version)' \
            if reply.lstrip()[:15].lower().startswith('<!doctype') else ''
        raise TrackerUnavailable(f'sheet write failed: unexpected reply{hint} {reply[:80]!r}')
    if res.get('error'): raise TrackerUnavailable(f'sheet write failed: {res["error"]}')
    return res

_sleep = time.sleep   # selftest swaps this out
TRANSIENT = r'(?i)service error|lock timeout|timed out|use POST|unexpected reply|temporar|try again|50[0-9]'

def upload(pdf, folder, key, suffix='', root=''):
    """Upload a PDF into <root folder id, else the folder holding the sheet>/<folder>/ through the web app.
    (action "upload").
    Returns {'id', 'url', 'bytes', 'name'}; raises TrackerUnavailable (no web app, unreachable, error, size mismatch)."""
    url, tok, _ = _sheet_conf()
    if not url: raise TrackerUnavailable('no "tracker_sheet.script_url" in config: uploads need the web app')
    with open(pdf, 'rb') as f: data = f.read()
    body = {'token': tok, 'action': 'upload', 'folder': folder, 'name': os.path.basename(pdf), 'key': key,
            'suffix': suffix, 'root': root, 'data': base64.b64encode(data).decode('ascii')}
    payload = json.dumps(body).encode('utf-8')
    for attempt in range(4):   # Drive/Apps Script hiccups pass on retry; a retry replaces any copy that landed (same key)
        try:
            reply = _http(url, payload, timeout=120)
            try: res = json.loads(reply)
            except ValueError: res = {'error': f'unexpected reply {reply[:80]!r}'}
        except TrackerUnavailable as e: res = {'error': str(e)}
        if attempt == 3 or not re.search(TRANSIENT, str(res.get('error', ''))): break
        _sleep(5 * (attempt + 1))
    if res.get('error'):
        hint = ('; in the Apps Script editor pick "authorize", Run, and approve Drive access (if appsscript.json has '
                '"oauthScopes", add "https://www.googleapis.com/auth/drive" to it first)'
                if 'permission' in str(res['error']).lower() else '')
        raise TrackerUnavailable(f'upload failed: {str(res["error"]).split(" For more information")[0]}{hint}')
    if not res.get('url'):   # an older script treats the call as an empty write and answers {ok, added, updated}
        raise TrackerUnavailable('upload failed: the web app has no "upload" action (redeploy the latest '
                                 'references/tracker-apps-script.gs as a New version)')
    if root and res.get('root') != root:   # an older script ignores "root" and used the sheet's folder instead
        raise TrackerUnavailable(f'upload failed: the web app ignores "folder_id" ({res.get("name")} went to the '
                                 'folder holding the sheet: delete it there) - redeploy the latest '
                                 'references/tracker-apps-script.gs as a New version')
    if int(res.get('bytes') or -1) != len(data):
        raise TrackerUnavailable(f'upload failed: sent {len(data)} bytes, Drive stored {res.get("bytes")}')
    return res

# ---------------------------------------------------------------- read / write
def _parse(f):
    r = csv.DictReader(f)
    fields = [c for c in (r.fieldnames or COLS) if c]
    rows = [{k: (v or '') for k, v in row.items() if k} for row in r]
    return fields + [c for c in COLS if c not in fields], rows

def _read_local(path):
    if not path or not os.path.exists(path): return list(COLS), []
    with open(path, encoding='utf-8-sig', newline='') as f: return _parse(f)

def _read(path):
    if is_sheet(path): return _parse(io.StringIO(_sheet_get(path)))
    return _read_local(path)

def _queue(path, records, replace=False):
    """Keep sheet records locally until the sheet can be written (applied with the upsert rules next run)."""
    p = pending_path(path); os.makedirs(os.path.dirname(p), exist_ok=True)
    old = [] if replace else _read_local(p)[1]
    recs = old + [r for r in records if r.get('code')]
    keys = list(dict.fromkeys(['code'] + [k for r in recs for k in r]))
    with open(p, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore'); w.writeheader()
        for r in recs: w.writerow({k: r.get(k, '') for k in keys})
    return p

def load(path):
    """(fields, rows) with any pending rows from a previous failed write merged in.
    A sheet that cannot be read raises TrackerUnavailable."""
    fields, rows = _read(path)
    pf, prows = _read_local(pending_path(path))
    if prows:
        if is_sheet(path):
            _, _, ch = _apply(fields, rows, prows, _numbered(rows))
            d = _DIRTY.setdefault(path, {})
            for c, ks in ch.items(): d.setdefault(c, set()).update(ks)
        else:
            by = {r.get('code'): r for r in rows}
            for p in prows:
                if p.get('code') in by: by[p['code']].update({k: v for k, v in p.items() if v})
                else: rows.append(p); by[p.get('code')] = p
        fields += [c for c in pf if c not in fields]
    return fields, rows

def save(path, fields, rows, changes=None):
    """CSV: atomic replace (pending file on failure). Sheet: send the changed cells ({code: keys}; all rows if None).
    Returns the path written."""
    if is_sheet(path):
        if changes is None: changes = {r['code']: set(fields) for r in rows if r.get('code')}
        by = {r.get('code'): r for r in rows}
        out = [dict({'code': c}, **{k: by[c].get(k, '') for k in fields if k in ks and k != 'code'})
               for c, ks in changes.items() if c in by and ks]
        pend = pending_path(path)
        if not out:
            if os.path.exists(pend): os.remove(pend)
            return path
        used = [k for k in fields if any(k in r for r in out)]
        try:
            _sheet_post(used, out)
        except TrackerUnavailable as e:
            print(f'WARN: {e}; {len(out)} row(s) kept in {_queue(path, out, replace=True)}, sent next run', file=sys.stderr)
            return pend
        if os.path.exists(pend): os.remove(pend)
        return path
    d = os.path.dirname(os.path.abspath(path)); os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.csv.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, '') for k in fields})
    try:
        os.replace(tmp, path)
        if os.path.exists(pending_path(path)): os.remove(pending_path(path))
        return path
    except OSError:
        os.replace(tmp, pending_path(path))
        print(f'WARN: could not update {path} (open in another program?); saved to {pending_path(path)}, merged next run')
        return pending_path(path)

def _load_or_pending(path):
    """Rows for read-only uses; an unreachable sheet falls back to the pending records (with a warning)."""
    try: return load(path)[1]
    except TrackerUnavailable as e:
        print(f'WARN: {e}; using local pending rows only', file=sys.stderr)
        return _read_local(pending_path(path))[1]

def codes(path):
    return {r['code'] for r in _load_or_pending(path) if r.get('code')}

def _num(s):
    m = re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*(%?)', s)
    return None if not m else float(m.group(1)) / (100 if m.group(2) else 1)

def _day(s):
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: pass
    return None

def _same(a, b):
    """Equal apart from how a spreadsheet displays it: 0.88 == 88%, 10/17/2026 == 10/17/26 == 2026-10-17."""
    a, b = (a or '').strip(), (b or '').strip()
    if a == b: return True
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None: return abs(na - nb) < 1e-9
    da = _day(a)
    return da is not None and da == _day(b)

def _tool_dropped(cur, new_status):
    """A CV was built for a role the tools had marked "not relevant" (reason "answered no: ..."): the member chose
    to apply, so cv_ready may replace it. A "not relevant" the member set (no tool reason) is kept."""
    return (norm_status(new_status) == 'cv_ready' and norm_status(cur.get('status')) == 'not relevant'
            and (cur.get('reason') or '').strip().lower().startswith('answered no:'))

def _apply(fields, rows, records, numbered):
    """Upsert records into rows in place. Returns (added, updated, {code: changed keys})."""
    by = {r.get('code'): r for r in rows}
    added = updated = 0; changes = {}
    for rec in records:
        c = rec.get('code')
        if not c: continue
        cur = by.get(c)
        if cur is None:
            row = {k: '' for k in fields}; row.update({k: v for k, v in rec.items() if v is not None})
            row['first_seen'] = row.get('first_seen') or date.today().isoformat()
            row['status'] = fmt_status(row.get('status') or 'new', numbered)
            rows.append(row); by[c] = row; added += 1
            changes.setdefault(c, set()).update(k for k, v in row.items() if v)
            continue
        ch = changes.setdefault(c, set())
        for k, v in rec.items():
            if v in (None, '') or k == 'code': continue
            if k in MEMBER_COLS and cur.get(k): continue
            if k == 'status':
                if norm_status(cur.get('status')) not in TOOL_STATUS and not _tool_dropped(cur, v): continue
                v = fmt_status(v, numbered)
            if k in FILL_ONCE and cur.get(k): continue
            if k == 'jd_file' and str(cur.get(k) or '').startswith('https://'):
                continue                       # the JD's Drive copy stays linked; jd_local() finds the text on disk
            if cur.get(k) != v and not _same(cur.get(k), v): cur[k] = v; updated += 1; ch.add(k)
        if applied(cur) and norm_status(cur.get('status')) in TOOL_STATUS:
            cur['status'] = fmt_status('applied', numbered); ch.add('status')
    for k in {k for r in records for k in r}:
        if k not in fields: fields.append(k)
    return added, updated, {c: ks for c, ks in changes.items() if ks}

def upsert(path, records):
    """Insert or update rows by code. Member columns and member statuses are never overwritten."""
    try:
        fields, rows = load(path)
    except TrackerUnavailable as e:
        print(f'WARN: {e}; {len(records)} record(s) kept in {_queue(path, records)}, applied next run', file=sys.stderr)
        return 0, 0
    added, updated, ch = _apply(fields, rows, records, _numbered(rows))
    for c, ks in _DIRTY.pop(path, {}).items(): ch.setdefault(c, set()).update(ks)
    save(path, fields, rows, ch if is_sheet(path) else None)
    return added, updated

def done_codes(path):
    """Codes the member has finished with (applied, skipped, ...): never re-surface or re-build them."""
    out = set()
    for r in _load_or_pending(path):
        if norm_status(r.get('status')) in DONE_STATUS or applied(r): out.add(r.get('code'))
    return out

def find_duplicates(path, code='', url='', title='', company='', text=''):
    """Check tracker rows for an existing role by code, URL, or matching title + company.
    Returns a list of dicts: [{'row': row_dict, 'reasons': [str]}]"""
    if not path or not exists(path): return []
    try: _, rows = load(path)
    except TrackerUnavailable: return []
    dups = []
    def norm(s): return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()
    c_norm = norm(code_of(code or url))
    u_norm = (url or '').strip().lower()
    t_norm = norm(title)
    co_norm = norm(company)

    # Heuristic extraction from JD text if title or company are empty
    if text and (not t_norm or not co_norm):
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        if not t_norm and lines:
            t_norm = norm(lines[0])
        if not co_norm:
            m = re.search(r'(?:Client|Company|Organisation|Employer):\s*([^\n\r]+)', text, re.I)
            if m: co_norm = norm(m.group(1))

    for r in rows:
        reasons = []
        r_code = norm(r.get('code'))
        r_url = (r.get('url') or '').strip().lower()
        r_title = norm(r.get('title'))
        r_co = norm(r.get('consultancy') or r.get('client') or r.get('agency'))

        # 1. Code match
        if c_norm and r_code and c_norm == r_code:
            reasons.append(f'same job code ({r.get("code")})')
        # 2. URL match
        elif u_norm and r_url and (u_norm in r_url or r_url in u_norm):
            reasons.append('same URL')
        # 3. Title + Company match
        elif t_norm and r_title and (t_norm in r_title or r_title in t_norm):
            if co_norm and r_co and (co_norm in r_co or r_co in co_norm):
                reasons.append(f'matching title and company ("{r.get("title")}" at "{r.get("client") or r.get("consultancy")}")')

        if reasons:
            dups.append({'row': r, 'reasons': reasons})
    return dups

def read_list(path):
    """A pasted URL,Title,Location,Rate list -> records (duplicate codes: first kept, later flagged)."""
    recs, seen = [], {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f):
            if not row or row[0].strip().lower() == 'url': continue
            c = code_of(row[0].split()[0] if row[0].strip() else '')
            if not c: continue
            rec = {'code': c, 'url': row[0].split()[0], 'title': row[1] if len(row) > 1 else '',
                   'location': row[2] if len(row) > 2 else '', 'rate': ','.join(row[3:]) if len(row) > 3 else ''}
            if c in seen:
                seen[c]['notes'] = (seen[c].get('notes', '') + f' | same code also listed as "{rec["title"]}"').strip(' |')
                continue
            seen[c] = rec; recs.append(rec)
    return recs

JD_UPLOADS = 'jd-uploads.json'    # <job-search>/.jobsearch/: {"<date>/<slug>": {url, name, folder, src}} (JDs in Drive)

def jd_uploads():
    """The JD Drive manifest: {local path (absolute): Drive link}, plus {Drive link: local path}."""
    try: p = local(JD_UPLOADS)
    except SystemExit: return {}, {}
    if not os.path.exists(p): return {}, {}
    with open(p, encoding='utf-8') as f: man = json.load(f)
    h = home(); to_url, to_src = {}, {}
    for v in man.values():
        src = v.get('src') or ''
        if src:
            src = src if os.path.isabs(src) else os.path.join(h, src)
            to_url[os.path.normpath(src)] = v['url']; to_url.setdefault(os.path.basename(src), v['url'])
        to_src[v['url']] = src or re.sub(r'^JD-|\.pdf$', '', v.get('name', '')) + '.txt'   # bare name: found by jd_local
    return to_url, to_src

def _jd_manifest():
    p = local(JD_UPLOADS)
    if not os.path.exists(p): return p, {}
    with open(p, encoding='utf-8') as f: return p, json.load(f)

def upload_conf():
    """config "cv_upload" ({"folder_id": ..., "name": ..., "folder": ...}) when Drive uploads are set up, else None."""
    c = config().get('cv_upload') or {}
    return c if (c.get('folder') or c.get('folder_id')) and _sheet_conf()[0] else None

def upload_jd(txt, day=None, conf=None, slug=None, code='', text=None):
    """Upload a JD .txt as JD-<slug>.pdf into <Drive CV folder>/<day>/ (same folder as that day's CVs; day = the date
    in the path, else today) and record it in .jobsearch/jd-uploads.json. Returns the Drive link ('' when uploads are
    off). `text` instead of a file: the JD text itself (find_jobs before any .txt exists; the local file is linked
    later, when a CV workspace saves it). A JD already uploaded (same day/slug, or the same JobServe code) returns its
    link without a second upload, and gains `txt` as its local copy if it had none. Raises TrackerUnavailable."""
    conf = conf or upload_conf()
    has_file = bool(txt) and os.path.exists(txt)
    if not conf or not (has_file or text): return ''
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
    import text_pdf
    slug = slug or os.path.splitext(os.path.basename(txt))[0]
    m = re.search(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)', os.path.abspath(txt) if txt else '')
    day = day or (m.group(1) if m else date.today().isoformat())
    mp, man = _jd_manifest()
    h = home()
    rel = lambda p: os.path.relpath(p, h) if p.startswith(h + os.sep) else p          # noqa: E731
    hit = man.get(f'{day}/{slug}') or next((v for v in man.values() if code and v.get('code') == code), None)
    if hit:
        if has_file and not jd_local(hit['url']):         # first local copy of a JD uploaded from its text
            hit['src'] = rel(os.path.abspath(txt)); _save_manifest(mp, man)
        return hit['url']
    key = f'{day}/{slug}'
    tmp = local('tmp', create=True); os.makedirs(tmp, exist_ok=True)
    pdf = os.path.join(tmp, f'JD-{slug}.pdf')
    try:
        if has_file: text_pdf.render_file(txt, pdf)
        else: text_pdf.render(text, pdf)
        res = upload(pdf, '/'.join(x for x in ((conf.get('folder') or '').strip('/'), day) if x), f'jd:{slug}',
                     slug.rsplit('-', 1)[-1], conf.get('folder_id', ''))
    finally:
        if os.path.exists(pdf): os.remove(pdf)
    shown = '/'.join(x for x in ((conf.get('name') or 'Drive folder') if conf.get('folder_id') else '',
                                 (conf.get('folder') or '').strip('/'), day) if x)
    man[key] = {'url': res['url'], 'name': res['name'], 'folder': shown, 'code': code,
                'src': rel(os.path.abspath(txt)) if has_file else ''}
    _save_manifest(mp, man)
    return res['url']

def _save_manifest(mp, man):
    fd, tp = tempfile.mkstemp(dir=os.path.dirname(mp), suffix='.json.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(man, f, indent=1)
    os.replace(tp, mp)
    jd_local.__defaults__[0].clear()          # the link -> file cache is stale now

def upload_jds(path, dry=False):
    """Every tracker row whose jd_file is still a local JD file: upload it (upload_jd) and put the Drive link in
    jd_file. Used to retry failed uploads and to backfill. Prints a RESULT line; returns the number of failures."""
    conf = upload_conf()
    if not conf:
        print('FAIL: Drive uploads are not set up ("cv_upload" in the config; jobserve-new-jobs setup_google.py)'); return 1
    _, rows = load(path)
    mp, man = _jd_manifest(); by_url = {v['url']: v for v in man.values()}; filled = 0
    for r in rows:                    # older manifest entries lack the JobServe code (used to avoid a second upload)
        v = by_url.get(r.get('jd_file') or '')
        if v is not None and not v.get('code') and r.get('code'): v['code'] = r['code']; filled += 1
    if filled and not dry: _save_manifest(mp, man)
    todo = [(r['code'], r['jd_file']) for r in rows if r.get('code') and r.get('jd_file')
            and not str(r['jd_file']).startswith('https://') and os.path.exists(r['jd_file'])]
    print(f'upload-jds: {len(todo)} JD(s) with a local jd_file')
    if dry:
        for c, p in todo[:20]: print(f'  {c:10} {p}')
        return 0
    recs, bad = [], 0
    for c, p in todo:
        try:
            u = upload_jd(p, conf=conf, code=c)
            if u: recs.append({'code': c, 'jd_file': u})
        except TrackerUnavailable as e:
            bad += 1; print(f'WARN: {c}: {e}', file=sys.stderr)
    if recs: upsert(path, recs)
    print(f'RESULT uploaded={len(recs)} failed={bad}'); return bad

def jd_local(v, _cache={}):
    """Local JD .txt for a jd_file cell: the path itself, or the file a Drive link was uploaded from ('' if unknown)."""
    v = (v or '').strip()
    if not v: return ''
    if not v.startswith('https://'): return v if os.path.exists(v) else ''
    if 'm' not in _cache: _cache['m'] = jd_uploads()[1]
    p = _cache['m'].get(v, '')
    if p and not os.path.isabs(p):                          # uploaded from its text: the .txt saved later, by name
        if 'idx' not in _cache: _cache['idx'] = _jd_index()
        p = _cache['idx'].get(p, '')
    return p if p and os.path.exists(p) else ''

def _jd_index():
    """basename -> path of every JD .txt in the job-search folder (finds a jd_file whose folder was moved)."""
    try: h = home()
    except SystemExit: return {}
    out = {}
    for p in glob.glob(os.path.join(h, '**', '*.txt'), recursive=True):
        if f'{os.sep}.' not in p[len(h):]: out.setdefault(os.path.basename(p), p)
    return out

def enrich(path, dry=False, migrate=False, force_codes=()):
    """Fill ir35, rate_min, rate_max, agent for every row from its JD text (jd_file; else the rate/title/notes),
    using jd_fields.py. ir35 and agent are filled only while blank. --migrate (one-off): rows marked applied get
    rate_submitted (the rate the notes state, else "yes"); "client" values like "Consultancy > End client" are
    split into consultancy + client (only while consultancy is blank). Prints what changes; --dry-run writes nothing.
    Rates are normalised (jd_fields.rates): per day for a contract, per year for a permanent role. A jd_file whose JD
    was uploaded to Drive (.jobsearch/jd-uploads.json) becomes the Drive link. --force CODE,... re-derives ir35 and
    agent for those rows even when filled (to correct a value the tools got wrong)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
    import jd_fields as J
    fields, rows = load(path)
    for k in COLS:
        if k not in fields: fields.append(k)
    idx, ch, mapping, subm = None, {}, [], []
    stats = {k: 0 for k in FIELD_COLS + ('jd_file',)}; ir = {}
    to_url = jd_uploads()[0]; force_codes = set(force_codes or ())
    for r in rows:
        c = r.get('code')
        if not c: continue
        cell, text = r.get('jd_file') or '', ''
        jf = jd_local(cell)
        if cell and not jf and not cell.startswith('https://'):
            if idx is None: idx = _jd_index()
            jf = idx.get(os.path.basename(cell), '')
        if jf:
            with open(jf, encoding='utf-8', errors='replace') as f: text = f.read()
        link = '' if cell.startswith('https://') else to_url.get(os.path.normpath(jf or cell)) or to_url.get(os.path.basename(cell))
        if link: r['jd_file'] = link; ch.setdefault(c, set()).add('jd_file'); stats['jd_file'] += 1
        st = J.ir35(text, '' if text else r.get('title', ''), '' if text else r.get('rate', ''))[0]
        force = c in force_codes
        perm = ((r.get('ir35') or '').strip() if not force else '') or st
        lo, hi = J.rates(r.get('rate') or (J._parts(text, '', '')[1] if text else ''), permanent=perm == 'Permanent',
                         text=text, hours=config().get('hours_per_day'), days=config().get('days_per_year'))
        new = {'ir35': st, 'rate_min': lo, 'rate_max': hi, 'agent': J.agent(text, r.get('notes', ''))}
        ir[st or '(blank)'] = ir.get(st or '(blank)', 0) + 1
        for k, v in new.items():
            if k in FILL_ONCE and (r.get(k) or '').strip() and not force: continue
            if (v or k in ('rate_min', 'rate_max') or force) and (r.get(k) or '') != v and not _same(r.get(k), v):
                r[k] = v; ch.setdefault(c, set()).add(k); stats[k] += 1
        if not migrate: continue
        if 'applied' in fields and (r.get('applied') or '').strip().lower() in ('yes', 'y', 'true', '1') \
                and not (r.get('rate_submitted') or '').strip():
            v = J.submitted_rate(r.get('notes', '')) or 'yes'
            r['rate_submitted'] = v; ch.setdefault(c, set()).add('rate_submitted')
            if v != 'yes': subm.append((c, v, re.sub(r'\s+', ' ', r.get('notes', ''))[:70]))
        if (r.get('client') or '').strip() and not (r.get('consultancy') or '').strip():
            con, cli = J.split_client(r['client'])
            if con:
                mapping.append((c, r['client'], con, cli))
                r['consultancy'], r['client'] = con, cli; ch.setdefault(c, set()).update({'consultancy', 'client'})
    print(f'rows: {len(rows)}  ir35 (as derived): ' + ', '.join(f'{k} {v}' for k, v in sorted(ir.items(), key=lambda x: -x[1])))
    print('cells to fill: ' + ', '.join(f'{k} {v}' for k, v in stats.items()))
    if migrate:
        n = sum('rate_submitted' in ks for ks in ch.values())
        print(f'rate_submitted: {n} applied row(s) -> {len(subm)} with the rate from notes, {n - len(subm)} "yes"')
        for c, v, note in subm: print(f'  {c:10} {v:>6}   notes: {note}')
        print(f'client -> consultancy + client: {len(mapping)} row(s)')
        for c, old, con, cli in mapping: print(f'  {c:10} {old[:45]:45} -> consultancy={con!r} client={cli!r}')
    if dry: print('DRY RUN: nothing written'); return 0
    save(path, fields, rows, ch if is_sheet(path) else None)
    print(f'TRACKER={path} rows_changed={len(ch)}'); return 0

def main(argv):
    if not argv or argv[0] in ('-h', '--help'): print(__doc__); return 0
    if argv[0] == 'home': print(home(create='--create' in argv)); return 0
    if argv[0] == 'migrate-home':
        migrate_home(dry='--dry-run' in argv); return 0
    cmd, path, rest = argv[0], resolve(argv[1]), argv[2:]
    opt = dict(zip(rest[::2], rest[1::2]))
    if cmd == 'seed':
        recs = []
        if opt.get('--applied'):
            recs += [dict(r, status='applied', rate_submitted='yes') for r in read_list(os.path.expanduser(opt['--applied']))]
        if opt.get('--available'):
            have = {r['code'] for r in recs}
            recs += [dict(r, status='new') for r in read_list(os.path.expanduser(opt['--available'])) if r['code'] not in have]
        a, u = upsert(path, recs)
        print(f'TRACKER={path} added={a} updated={u} rows={len(load(path)[1])}'); return 0
    if cmd == 'push' and opt.get('--from'):
        recs = _read_local(resolve(opt['--from']))[1]
        a, u = upsert(path, recs)
        print(f'TRACKER={path} added={a} updated={u} rows={len(_load_or_pending(path))}'); return 0
    if cmd == 'upload-jds':
        return 1 if upload_jds(path, dry='--dry-run' in rest) else 0
    if cmd == 'enrich':
        return enrich(path, dry='--dry-run' in rest, migrate='--migrate' in rest,
                      force_codes=[c for c in opt.get('--force', '').split(',') if c])
    if cmd == 'list':
        want = {norm_status(s) for s in opt.get('--status', '').split(',') if s.strip()}
        for r in _load_or_pending(path):
            if not want or norm_status(r.get('status')) in want:
                print(f"{r.get('code', ''):10} {r.get('status', ''):16} {r.get('fit', ''):>4} {r.get('title', '')[:60]}")
        return 0
    print(__doc__); return 2

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
