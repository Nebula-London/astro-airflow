#!/usr/bin/env python3
"""One entry point for the skill, driven by a workspace file so paths are given once.

  cv.py prep OUT_DIR --jd FILE|URL --keywords "..." [--slug S] [--master M.json|DIR] [--save-pending]
        # single JD, one call: latest master, save pending answers to vN+1, workspace, JD, plan, evidence,
        # bullets of the planned roles, open questions. Then: answers -> skeleton -> write content.json -> build
  cv.py answers '[{"term":"X","has":true,"detail":"...","roles":[1,3],"also":["Y"]}, {"term":"Z","has":false}]'
        # record several answers in one call (JSON list, or @file.json)
  cv.py skeleton SLUG                                     # writes SLUG/content.skeleton.json (facts pre-filled, TODO slots)
  cv.py init OUT_DIR --master M.json [--jd-dir DIR]      # writes OUT_DIR/workspace.json (jd dir default OUT_DIR/jds)
  cv.py import-jds JSON [--jd-dir DIR]                   # turn a jobserve-new-jobs JSON into one <slug>.txt per role
  cv.py triage                                            # ranked table (batch)
  cv.py ready                                             # roles needing NO question and not built yet: build these first
  cv.py next [N]                                          # next N questions (ask them in one message)
  cv.py answer TERM yes|no ["what you did"] [--roles 0,1] [--also A,B] [--replace]
  cv.py digest SLUG --keywords "..." [--section evidence,plan,clearance]   # plan + filtered digest
  cv.py roles SLUG 0,1,4 --keywords "..."                 # bullets for the roles you will write
  cv.py build SLUG [PDF_NAME] [--pages N]                 # arrange -> lint -> render -> check -> report.json (N default 2)
  cv.py docx SLUG [OUT.docx]                              # convert built SLUG PDF to editable Word .docx
  cv.py layout SLUG [--size S]                            # deterministic ReportLab line-fill & char-budget report
  cv.py final                                             # final table (with job permalink & local CV file links)
  cv.py brief                                             # compact build brief for parallel build agents
  cv.py save-master OUT.json [--only ...] [--skip ...] [--dry-run]
  cv.py daily JSON --master M.json|DIR [--tracker CSV] [--out DIR]
        # daily run, one call: import the jobserve JSON (skipping roles already applied/skipped in the tracker),
        # workspace with remembered answers, ranked table, fit + missing terms into the tracker, ONE question batch
  cv.py memory [list | forget TERM | add TERM no | add TERM yes "what you did" --roles 1,3]
        # answers remembered across days (<job-search>/.jobsearch/memory.json, or $CV_MEMORY)
  cv.py refs [REGEX]                                      # role refs (ref | company | title | dates) for answers
  cv.py track [CSV] [--slugs a,b]                         # built CVs (or only those slugs) -> tracker (fit, gaps, cv_pdf, status=cv_ready)
  cv.py upload SLUG... | --pending [DIR...] | --backfill DIR... [--dry-run]   # --pending also retries JDs
        # local PDFs -> Google Drive via the tracker web app (jobserve config "cv_upload": {"folder_id": ...} or {"folder": "CVs"});
        # "build" does this itself when cv_upload is set; report.json and the tracker then hold the Drive link
  cv.py remember                                          # save this workspace's answers to memory now
  cv.py check-master [M.json|DIR]                         # check a master before the first CV (read-only)

The workspace is found via $CV_WS, or ./workspace.json, or the single */workspace.json below the cwd, or the
last one created by "init" (remembered in <job-search>/.jobsearch/last-ws), so it works when each command runs in a
fresh shell (Claude.ai, OpenCode). Prefer the launcher: bash <skill-dir>/scripts/cv <command>.
Per-JD files live in OUT_DIR/<slug>/ (plan.json, content.json, PDF, report.json, gaps.md); with "cv_upload" set
the PDF lives in Drive instead (<sheet folder>/<folder>/<batch date>/) and report.json "pdf" is its link.
Nothing is written outside the job-search folder: memory, the venv, scratch files and uploaded local PDFs (kept
30 days) all live in <job-search>/.jobsearch/.
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile
from datetime import date
HERE = os.path.dirname(os.path.abspath(__file__))
# job-board / agency boilerplate answered "no" (e.g. "ANY", "ARE", "energy"): never a reason to drop a role
NOISE = {'any', 'are', 'energy', 'gbp', 'ftse', 'mx', 'fcsa', 'teksystems'}
sys.path.insert(0, HERE); sys.dont_write_bytecode = True
os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')   # also for the scripts run as children
import tracker
PY = sys.executable
TRASH_DAYS = 30

def last_path(create=False): return tracker.local('last-ws', create=create)

def ws_path():
    if os.environ.get('CV_WS'): return os.environ['CV_WS']
    if os.path.exists('workspace.json'): return 'workspace.json'
    found = glob.glob('*/workspace.json') + glob.glob('*/*/workspace.json')
    if len(found) == 1: return found[0]
    if os.path.exists(last_path()):
        p = open(last_path()).read().strip()
        if os.path.exists(p): return p
    sys.exit('FAIL: no unique workspace.json (run "cv.py init" or set CV_WS)')

def load():
    p = ws_path(); w = json.load(open(p, encoding='utf-8')); w['_dir'] = os.path.dirname(os.path.abspath(p)); return w

def run(script, *args, capture=False):
    cmd = [PY, os.path.join(HERE, script)] + [str(a) for a in args]
    if capture: r = subprocess.run(cmd, capture_output=True, text=True); return r.returncode, r.stdout + r.stderr
    return subprocess.call(cmd)

def jd(w, slug): return os.path.join(w['jd_dir'], slug + '.txt') if w.get('jd_dir') else w.get('jd', '')

SECTIONS = ('CONTACT', 'WORK AUTH', 'CLEARANCE', 'ROLES', 'EDUCATION', 'CERTIFICATIONS', 'INTEGRITY', 'EVIDENCE', 'TIMELINE PLAN')

def filt(text, want):
    """Keep only the digest sections asked for (token saving: contact/education/certs are needed once)."""
    if not want: return text
    keep, out = False, []
    for line in text.splitlines():
        head = next((s for s in SECTIONS if line.startswith(s)), None)
        if head: keep = head.split()[0].lower() in want or head.lower() in want
        if keep: out.append(line)
    return '\n'.join(out)

def init_ws(out, master, jd_dir=None):
    """Create or refresh OUT/workspace.json; keeps answers.json (confirmed facts carry over between JDs)."""
    out = os.path.abspath(out); os.makedirs(out, exist_ok=True)
    jd_dir = os.path.abspath(jd_dir or os.path.join(out, 'jds')); os.makedirs(jd_dir, exist_ok=True)
    wp = os.path.join(out, 'workspace.json')
    # keep what "daily" recorded (tracker, codes, source): without them build stops updating the tracker
    try: w = json.load(open(wp, encoding='utf-8')) if os.path.exists(wp) else {}
    except ValueError: w = {}
    if not w.get('tracker'):
        ct = tracker.configured()
        if ct: w['tracker'] = ct
    w.update(master=os.path.abspath(master), jd_dir=jd_dir, out=out, answers=os.path.join(out, 'answers.json'))
    json.dump(w, open(wp, 'w'), indent=1)
    try: open(last_path(create=True), 'w').write(wp)
    except (OSError, tracker.NoHome): pass   # only a convenience for fresh shells
    return wp

# ---------------------------------------------------------------- answer memory (across days)
def mem_path(create=False):
    return os.path.expanduser(os.environ.get('CV_MEMORY') or tracker.local('memory.json', create=create))

def mem_load():
    p = mem_path()
    try: return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    except ValueError: return {}

def mem_save(m):
    p = mem_path(create=True); os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'; json.dump(m, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False); os.replace(tmp, p)

def role_keys(master):
    """'company|title|start' per role ref. Refs are newest-first positions in the date-sorted master, so a role
    added later shifts them; memory stores these keys too and remaps refs on load."""
    import digest_master as dm
    roles = dm.load_roles(json.load(open(master, encoding='utf-8')))
    return roles, [f"{dm.g(r, 'client') or dm.g(r, 'organisation')}|{dm.g(r, 'role_title', 'title')}|{r.get('start_date', '')}" for r in roles]

def mem_merge(ans_path, master):
    """Copy remembered answers the batch does not have yet into answers.json. Refs are remapped through the stored
    role keys (so a role added to the master does not shift them); without keys a ref is kept only when it exists
    in this master. A "yes" with no valid role is not carried (it would claim a skill with no role to put it in)
    and is asked again. "no" always carries."""
    m = mem_load()
    if not m: return 0
    _, keys = role_keys(master); pos = {k: i for i, k in enumerate(keys)}
    ans = json.load(open(ans_path, encoding='utf-8')) if os.path.exists(ans_path) else {}
    added = 0
    for k, v in m.items():
        if k in ans: continue
        v = dict(v); rk = v.pop('role_keys', None); old = [int(i) for i in v.get('roles', [])]
        if rk and len(rk) == len(old):
            remap = {o: pos[key] for o, key in zip(old, rk) if key in pos}
        else:
            remap = {o: o for o in old if 0 <= o < len(keys)}
        if v.get('has') and not remap: continue
        v['roles'] = sorted(remap.values())
        v['detail_by_role'] = {str(remap[int(i)]): d for i, d in (v.get('detail_by_role') or {}).items() if int(i) in remap}
        v.pop('remembered', None); ans[k] = v; added += 1
    if added: json.dump(ans, open(ans_path, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    return added

def mem_store(ans_path, master=None):
    """Write every batch answer back to memory (latest answer wins), with role keys for remapping."""
    if not os.path.exists(ans_path): return 0
    ans = json.load(open(ans_path, encoding='utf-8')); m = mem_load(); n = 0
    keys = role_keys(master)[1] if master else []
    for k, v in ans.items():
        v = dict(v)
        if keys: v['role_keys'] = [keys[int(i)] for i in v.get('roles', []) if 0 <= int(i) < len(keys)]
        old = m.get(k, {}); old_cmp = {x: y for x, y in old.items() if x != 'remembered'}
        if old_cmp != v: m[k] = dict(v, remembered=date.today().isoformat()); n += 1
    if n: mem_save(m)
    return n

def list_refs(w, pat=''):
    """Role refs for answers: 'ref | company | title | dates' (filter by a regex on company or title)."""
    roles, keys = role_keys(w['master'])
    for i, k in enumerate(keys):
        c, t, s = k.split('|')
        if not pat or re.search(pat, c + ' ' + t, re.I): print(f"{i:3d} | {c} | {t} | {s} - {roles[i].get('end_date') or 'present'}")
    return 0

def track_all(w, csv_path=None, slugs=None):
    """Write fit / gaps / PDF / status=cv_ready for every built CV (or only `slugs`) into the tracker. "build" already
    does this for roles imported by "daily"; use this for other workspaces (rows are matched by jd_file name)."""
    OUT = w['out']; csv_path = tracker.resolve(csv_path or w.get('tracker') or tracker.configured()
                                               or os.path.join(os.path.dirname(OUT), 'applications.csv'))
    try: _, rows = tracker.load(csv_path) if tracker.exists(csv_path) else (None, [])
    except tracker.TrackerUnavailable as e: print(f'WARN: {e}', file=sys.stderr); rows = []
    by_jd = {os.path.splitext(os.path.basename(tracker.jd_local(r['jd_file']) or r['jd_file']))[0]: r['code']
             for r in rows if r.get('jd_file')}   # a Drive link maps back to its local JD (tracker.jd_local)
    codes = w.get('codes') or {}
    tri = os.path.join(OUT, 'triage.json'); T = json.load(open(tri, encoding='utf-8')).get('jds', {}) if os.path.exists(tri) else {}
    recs, skipped = [], []
    for rp in sorted(glob.glob(os.path.join(OUT, '*', 'report.json'))):
        slug = os.path.basename(os.path.dirname(rp))
        if slugs is not None and slug not in slugs: continue
        rep = json.load(open(rp, encoding='utf-8'))
        code = codes.get(slug) or by_jd.get(slug)
        if not code: skipped.append(slug); continue
        g = os.path.join(os.path.dirname(rp), 'gaps.md')
        gaps = '; '.join(l.strip('- \n') for l in open(g, encoding='utf-8') if l.startswith('-')) if os.path.exists(g) else ''
        fit = T.get(slug + '.txt', {}).get('match', '')
        recs.append({'code': code, 'jd_file': jd(w, slug), 'fit': f'{fit}%' if fit != '' else '',
                     'missing_terms': gaps[:300], 'cv_pdf': rep.get('pdf', ''), 'cv_built_on': date.today().isoformat(),
                     'status': 'cv_ready'})
    if recs: tracker.upsert(csv_path, recs)
    print(f'tracker: {len(recs)} row(s) -> {csv_path} (status=cv_ready; applied/notes/manual status untouched)')
    if skipped: print('no tracker row for:', ', '.join(skipped))
    missing = sorted((slugs or set()) - {os.path.basename(os.path.dirname(rp)) for rp in glob.glob(os.path.join(OUT, '*', 'report.json'))})
    if missing: print('no built CV (report.json) for:', ', '.join(missing))
    return 0

def memory(rest):
    m = mem_load(); sub = rest[0] if rest else 'list'
    if sub == 'list':
        print(f'MEMORY: {mem_path()} ({len(m)} term(s))')
        for k, v in sorted(m.items()):
            print(f"  {v.get('term', k)}: {'yes' if v.get('has') else 'no'}"
                  + (f" roles {v['roles']}" if v.get('roles') else '') + (f" - {v['detail'][:70]}" if v.get('detail') else ''))
        return 0
    if sub == 'forget' and len(rest) > 1:
        gone = [k for k in list(m) if k == rest[1].lower()]
        for k in gone: del m[k]
        mem_save(m); print(f"forgot: {rest[1]}" if gone else f"not in memory: {rest[1]}"); return 0
    if sub == 'add' and len(rest) > 2:
        args = rest[3:]; refs = []
        if '--roles' in args:
            i = args.index('--roles'); refs = [int(x) for x in args[i + 1].split(',') if x.strip()]; args = args[:i] + args[i + 2:]
        has = rest[2].lower().startswith('y'); detail = ' '.join(args)
        if has and not (refs and detail):
            print('FAIL: a "yes" needs what you did and where: cv memory add TERM yes "what you did" --roles 1,3'); return 1
        m[rest[1].lower()] = {'term': rest[1], 'has': has, 'detail': detail, 'roles': refs,
                              'detail_by_role': {str(i): detail for i in refs}, 'aliases': [],
                              'remembered': date.today().isoformat()}
        mem_save(m); print(f"remembered: {rest[1]} = {'yes' if has else 'no'}" + (f' roles {refs}' if refs else '')); return 0
    print('usage: cv memory [list | forget TERM | add TERM no | add TERM yes "what you did" --roles 1,3]'); return 2

# ---------------------------------------------------------------- tracker
def _tracker_code(w, slug):
    """Resolve the tracker row code for slug: workspace codes, else match jd_file stem / code in tracker, else slug."""
    code = (w.get('codes') or {}).get(slug, '')
    if code: return code
    t = w.get('tracker') or tracker.configured()
    if not t: return ''
    try: _, rows = tracker.load(t) if tracker.exists(t) else (None, [])
    except tracker.TrackerUnavailable: rows = []
    for r in rows:
        if r.get('code') == slug: return slug
        jf = r.get('jd_file', '')
        if jf and os.path.splitext(os.path.basename(tracker.jd_local(jf) or jf))[0] == slug:
            return r['code']
    return slug

def track_update(w, slug, **cols):
    """Update the member's tracker (CSV or Google Sheet) for a role when a tracker is configured."""
    t = w.get('tracker') or tracker.configured()
    if not t: return ''
    code = _tracker_code(w, slug)
    if not code: return ''
    rec = dict(code=code, **{k: str(v) for k, v in cols.items()})
    tracker.upsert(t, [rec])
    return code

# ---------------------------------------------------------------- Drive upload (jobserve config "cv_upload")
def built(p):
    """A built CV: a Drive link, or a PDF still on disk."""
    return bool(p) and (str(p).startswith('https://') or os.path.exists(p))

def upload_conf():
    """config "cv_upload": {"folder_id": "<Drive folder id>" and/or "folder": "CVs", "name": "cvs", "keep_local": false}.
    folder_id = start in that Drive folder (else the folder holding the sheet); folder = sub-folder path below it;
    name = how to show folder_id in messages. None when unset or there is no web app."""
    c = tracker.config().get('cv_upload') or {}
    return c if (c.get('folder') or c.get('folder_id')) and tracker._sheet_conf()[0] else None

def _where(conf):
    """Display name of the upload folder (never the folder id)."""
    return '/'.join(x for x in ((conf.get('name') or 'Drive folder') if conf.get('folder_id') else '',
                                (conf.get('folder') or '').strip('/')) if x)

def _norm(p): return os.path.normpath(os.path.abspath(os.path.expanduser(p))) if p else ''

def _day_of(pdf):
    """Drive sub-folder: the batch date in the path (cv/2026-10-19-pm -> 2026-10-19), else the PDF's file date."""
    m = re.search(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)', _norm(pdf))
    return m.group(1) if m else date.fromtimestamp(os.path.getmtime(pdf)).isoformat()

def _trash(pdf):
    """Uploaded local PDF -> <job-search>/.jobsearch/trash/<today>/<slug>/ (kept TRASH_DAYS days), else deleted."""
    try: t = tracker.local('trash', create=True)
    except tracker.NoHome: os.remove(pdf); return
    for d in glob.glob(os.path.join(t, '????-??-??')):
        try: old = (date.today() - date.fromisoformat(os.path.basename(d))).days > TRASH_DAYS
        except ValueError: old = False
        if old: shutil.rmtree(d, ignore_errors=True)
    dst = os.path.join(t, date.today().isoformat(), os.path.basename(os.path.dirname(pdf)), os.path.basename(pdf))
    os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.move(pdf, dst)

def upload_pdf(rp, code='', conf=None):
    """Upload the local PDF named in report.json; on success report.json "pdf" becomes the Drive link (the old path
    stays in "local_pdf") and the local file goes to .jobsearch/trash unless keep_local. Returns the link, or '' when
    uploads are off or the upload failed (the PDF stays on disk; retry with: cv upload --pending)."""
    conf = conf or upload_conf()
    rep = json.load(open(rp, encoding='utf-8')); pdf = rep.get('pdf', '')
    if not conf or not pdf or pdf.startswith('https://') or not os.path.exists(pdf): return ''
    slug = os.path.basename(os.path.dirname(rp))
    folder = '/'.join(x for x in ((conf.get('folder') or '').strip('/'), _day_of(pdf)) if x)
    try: res = tracker.upload(pdf, folder, slug, code or slug.rsplit('-', 1)[-1], conf.get('folder_id', ''))
    except tracker.TrackerUnavailable as e:
        print(f'WARN: {e}; {os.path.basename(pdf)} kept on disk (retry: cv upload --pending)', file=sys.stderr); return ''
    rep.update(pdf=res['url'], local_pdf=pdf, drive={'id': res['id'], 'name': res['name'], 'folder': '/'.join(x for x in (_where(conf), _day_of(pdf)) if x)})
    json.dump(rep, open(rp, 'w'), indent=1)
    if conf.get('keep_local') is False: _trash(pdf)
    return res['url']

def upload_jd_for(w, slug, day, code=''):
    """The role's JD -> Drive (JD-<slug>.pdf next to its CV, tracker.upload_jd). '' when uploads are off or it failed
    (warned; retried by: cv upload --pending)."""
    if not upload_conf(): return ''
    try: return tracker.upload_jd(jd(w, slug), day=day, code=code)
    except tracker.TrackerUnavailable as e:
        print(f'WARN: JD upload failed for {slug}: {e} (retry: cv upload --pending)', file=sys.stderr); return ''

def upload_cmd(rest):
    """cv upload SLUG... | --pending [DIR...] | --backfill DIR... [--dry-run]: local PDFs -> Drive, links -> tracker."""
    dry = '--dry-run' in rest; rest = [a for a in rest if a != '--dry-run']
    conf = upload_conf()
    if not conf:
        print('FAIL: uploads are off: add "cv_upload": {"folder_id": "<Drive folder id>"} or {"folder": "CVs"} to the jobserve config '
              '(needs "tracker_sheet.script_url" and the latest tracker-apps-script.gs deployed)'); return 1
    if rest and rest[0] in ('--pending', '--backfill'):
        dirs = rest[1:] or ([] if rest[0] == '--backfill' else [load()['out']])
        if not dirs: print('usage: cv upload --backfill DIR [DIR...] [--dry-run]'); return 2
        reps = sorted({p for d in dirs for p in glob.glob(os.path.join(os.path.expanduser(d), '**', 'report.json'), recursive=True)})
        codes = {}
    else:
        w = load(); codes = w.get('codes') or {}
        reps = [os.path.join(w['out'], s, 'report.json') for s in rest if os.path.exists(os.path.join(w['out'], s, 'report.json'))]
    todo = []
    for rp in reps:
        pdf = json.load(open(rp, encoding='utf-8')).get('pdf', '')
        if pdf and not pdf.startswith('https://') and os.path.exists(pdf): todo.append((rp, pdf))
    size = sum(os.path.getsize(p) for _, p in todo)
    print(f'upload: {len(todo)} local PDF(s), {size // 1024} KB -> Drive "{_where(conf)}/<date>/"'
          + (f' (local copies move to {tracker.LOCAL}/trash, kept {TRASH_DAYS} days)' if conf.get('keep_local') is False else ' (local copies kept)'))
    if dry:
        days = {}
        for _, p in todo: days[_day_of(p)] = days.get(_day_of(p), 0) + 1
        for day, n in sorted(days.items()): print(f'  {_where(conf)}/{day}: {n} PDF(s)')
        return 0
    done = failed = 0
    for i, (rp, _) in enumerate(todo, 1):
        slug = os.path.basename(os.path.dirname(rp))
        if upload_pdf(rp, codes.get(slug, ''), conf): done += 1
        else: failed += 1
        if i % 20 == 0: print(f'  {i}/{len(todo)}')
    # tracker: every uploaded report.json (this run or an earlier, interrupted one) -> rows still pointing at the old path
    link = {}
    for rp in reps:
        rep = json.load(open(rp, encoding='utf-8'))
        if rep.get('local_pdf') and str(rep.get('pdf', '')).startswith('https://'): link[_norm(rep['local_pdf'])] = rep['pdf']
    t = tracker.configured(); fixed = 0
    if t and link:
        try:
            recs = [{'code': r['code'], 'cv_pdf': link[_norm(r['cv_pdf'])]} for r in tracker.load(t)[1]
                    if r.get('code') and r.get('cv_pdf') and _norm(r['cv_pdf']) in link]
            if recs: tracker.upsert(t, recs); fixed = len(recs)
        except tracker.TrackerUnavailable as e: print(f'WARN: {e}; tracker links not updated (re-run to retry)', file=sys.stderr)
    print(f'RESULT uploaded={done} failed={failed} tracker_links={fixed}' + (' (failed PDFs kept on disk)' if failed else ''))
    jd_bad = tracker.upload_jds(t) if t and not dry and rest and rest[0] == '--pending' else 0   # JDs not in Drive yet
    return 1 if failed or jd_bad else 0

def daily(rest):
    """Daily run in one call: JSON -> jds (minus finished roles) -> workspace + memory -> triage -> tracker -> questions."""
    if not rest or rest[0].startswith('--'): sys.exit('usage: cv daily JSON --master M.json|DIR [--tracker CSV] [--out DIR]')
    src = os.path.abspath(os.path.expanduser(rest[0])); args = rest[1:]
    def opt(name, default=None): return args[args.index(name) + 1] if name in args else default
    jdir = src if os.path.isdir(src) else os.path.dirname(src)
    co = tracker.config().get('output_dir'); conf_out = os.path.abspath(os.path.expanduser(co)) if co else ''
    for up in (jdir, os.path.dirname(jdir), os.path.dirname(os.path.dirname(jdir))):   # json dir from check_live.py
        if os.path.exists(os.path.join(up, 'applications.csv')) or up == conf_out: jdir = up; break
    day = (re.match(r'\d{4}-\d{2}-\d{2}', os.path.basename(src)) or [date.today().isoformat()])[0]
    out = os.path.abspath(os.path.expanduser(opt('--out') or os.path.join(jdir, 'cv', day)))
    # tracker: --tracker, else the jobserve config "tracker" (CSV path or Google Sheet URL), else applications.csv here
    track = tracker.resolve(opt('--tracker') or tracker.configured() or os.path.join(jdir, 'applications.csv'))
    M = latest_master(os.path.expanduser(opt('--master') or jdir))
    jobs = []
    for f in (sorted(glob.glob(os.path.join(src, '*.json'))) if os.path.isdir(src) else [src]):
        data = json.load(open(f, encoding='utf-8'))
        jobs += data.get('jobs', []) if isinstance(data, dict) else data
    done = tracker.done_codes(track)
    import import_jds
    keep, codes, urls, recs = [], {}, {}, []
    for j in jobs:
        c = tracker.code_of(j.get('uid') or '') or j.get('code', '')
        if c in done: continue
        keep.append(j); s = import_jds.slug_of(j); codes[s] = c
        u = j.get('permalink') or j.get('url', '')
        if u: urls[s] = u
        recs.append({'code': c, 'url': u, 'title': j.get('title', ''),
                     'location': j.get('location', ''), 'rate': j.get('rate', ''), 'agency': j.get('agency', '')})
    wp = init_ws(out, M)
    w = json.load(open(wp)); w.update(tracker=track, codes=codes, urls=urls, source=src); json.dump(w, open(wp, 'w'), indent=1)
    for j in keep:
        open(os.path.join(w['jd_dir'], import_jds.slug_of(j) + '.txt'), 'w', encoding='utf-8').write(
            (j.get('jd_text') or import_jds.compose(j)).rstrip() + '\n')
    jd_links = {}                                        # every JD -> Drive (or its existing upload, by JobServe code)
    if upload_conf():
        for j in keep:
            s = import_jds.slug_of(j)
            u = upload_jd_for(w, s, day, codes.get(s, ''))
            if u: jd_links[s] = u
        print(f'JDs in Drive: {len(jd_links)}/{len(keep)}')
    n_mem = mem_merge(w['answers'], M)
    print(f'master: {M}\nworkspace: {wp}\ntracker: {track}')
    print(f'roles: {len(keep)} to triage, {len(jobs) - len(keep)} skipped (already applied/skipped in tracker) | '
          f'remembered answers applied: {n_mem}')
    if not keep: print('NOTHING NEW: no roles left to triage today.'); return 0
    tri = ['batch_triage.py', M, w['jd_dir'], '--answers', w['answers'], '--out', os.path.join(out, 'triage.json')]
    code, table = run(*tri, capture=True)
    if code != 0: print(table); return code
    t = json.load(open(os.path.join(out, 'triage.json'), encoding='utf-8'))
    for f, v in t['jds'].items():
        s = os.path.splitext(f)[0]; base = next((r for r in recs if r['code'] == codes.get(s)), None)
        if base is None: continue
        miss = [g['term'] for g in v['gaps']][:8]
        base.update(jd_file=jd_links.get(s) or os.path.join(w['jd_dir'], f), fit=f"{v['match']}%", missing_terms='; '.join(miss))
    tracker.upsert(track, recs)
    print(table)
    _, q = run(*tri, '--next', '--count', opt('--questions', 8), capture=True)
    print('\nQUESTIONS (only terms not answered before; ask in ONE message, record with cv answers, they are remembered):')
    print('\n'.join(l for l in q.splitlines() if not l.strip().startswith('Record:')))
    print('\nNEXT: cv answers \'[...]\' ; pick roles; per role: cv digest SLUG --keywords "..." ; cv skeleton SLUG ; '
          'write content.json ; cv build SLUG (the tracker gets cv_pdf + status=cv_ready)')
    return 0

VER = re.compile(r'-v(\d+)\.json$', re.I)

def latest_master(where):
    """A master file as given, or the highest *-vN.json in a folder."""
    if os.path.isfile(where): return os.path.abspath(where)
    found = [f for f in glob.glob(os.path.join(where, '*-v*.json')) if VER.search(f)]
    if not found: sys.exit(f'FAIL: no *-vN.json master in {where} (pass --master FILE)')
    return os.path.abspath(max(found, key=lambda f: int(VER.search(f).group(1))))

def next_version(m):
    n = int(VER.search(m).group(1)) if VER.search(m) else 0
    while True:
        n += 1; p = VER.sub(f'-v{n}.json', m) if VER.search(m) else m[:-5] + f'-v{n}.json'
        if not os.path.exists(p): return p

def html_text(raw):
    """Page text without site chrome: <title> first, then the span from the first to the last prose line
    (12+ words), which drops menus, cookie banners and footers that would otherwise become fake JD terms."""
    import html
    title = re.search(r'(?is)<title[^>]*>(.*?)</title>', raw)
    raw = re.sub(r'(?is)<(script|style|noscript|head|nav|footer|header)[^>]*>.*?</\1>', ' ', raw)
    raw = re.sub(r'(?i)<br\s*/?>|</(p|div|li|h\d|tr|dd|dt)>', '\n', raw)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', raw))
    lines = [l for l in (re.sub(r'[ \t\xa0]+', ' ', l).strip() for l in t.splitlines()) if l]
    prose = [i for i, l in enumerate(lines) if len(l.split()) >= 12]
    if prose: lines = lines[max(0, prose[0] - 3):prose[-1] + 1]   # keep a short heading block above the first paragraph
    head = [html.unescape(re.sub(r'\s+', ' ', title.group(1))).strip()] if title else []
    return '\n'.join(head + lines)

def fetch_jd(url):
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (cv-skill)'})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode(r.headers.get_content_charset() or 'utf-8', errors='replace')
    return html_text(body) if '<' in body[:2000] else body

def kw_terms(kw):
    return [x.strip().lower() for k in kw.split(',') for x in re.sub(r'\*\d+$', '', k.strip()).split('|') if x.strip()]

def compact_roles(txt, kws, max_tech=12):
    """Role detail with the technologies list trimmed: JD-matching items first, then the rest, capped."""
    sys.path.insert(0, HERE); import digest_master as dm
    out = []
    for l in txt.splitlines():
        if l.startswith(' tech:'):
            items = [x.strip() for x in l[6:].split(',') if x.strip()]
            hit = [x for x in items if any(dm.has(k, x.lower()) or dm.has(x.lower(), k) for k in kws)]
            rest = [x for x in items if x not in hit]
            keep = (hit + rest)[:max(max_tech, len(hit))]
            l = ' tech: ' + ', '.join(keep) + (f' (+{len(items) - len(keep)} more)' if len(items) > len(keep) else '')
        if l.startswith(('== [', ' - ', ' tech:', ' team:')): out.append(l)
    return '\n'.join(out)

def prep(rest):
    """Single-JD fast path: everything needed before the clarification questions, in one call."""
    out = rest[0]; args = rest[1:]
    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default
    jd_arg, kw = opt('--jd'), opt('--keywords', '')
    if not jd_arg or not kw: sys.exit('FAIL: prep needs --jd FILE|URL and --keywords "..."')
    out = os.path.abspath(out)
    M = latest_master(opt('--master') or os.path.dirname(out))
    print('master:', M)
    wsp = os.path.join(out, 'workspace.json'); ANS = os.path.join(out, 'answers.json')
    if '--save-pending' in args and os.path.exists(ANS):
        code, txt = run('save_answers_to_master.py', M, ANS, os.path.join(tempfile.gettempdir(), 'cv-pending.json'), '--dry-run', capture=True)
        m = re.search(r'DRY RUN: (\d+) change', txt); n = int(m.group(1)) if m else 0
        if n:
            nv = next_version(M)
            code, txt = run('save_answers_to_master.py', M, ANS, nv, capture=True)
            if code != 0: print(txt.strip().splitlines()[-3:]); return code
            print(f'saved {n} pending answer change(s): {os.path.basename(M)} -> {os.path.basename(nv)}'); M = nv
        else:
            print('pending answers: none (master already up to date)')
    init_ws(out, M, json.load(open(wsp)).get('jd_dir') if os.path.exists(wsp) else None)
    w = load(); jd_dir = w['jd_dir']
    n_mem = mem_merge(ANS, M)
    if n_mem: print(f'remembered answers applied: {n_mem}')
    slug = opt('--slug') or (re.sub(r'[^a-z0-9]+', '-', os.path.splitext(os.path.basename(jd_arg))[0].lower()).strip('-')
                             if not jd_arg.startswith('http') else 'jd')
    jdf = os.path.join(jd_dir, slug + '.txt')
    if jd_arg.startswith(('http://', 'https://')):
        try: text = fetch_jd(jd_arg)
        except Exception as e: sys.exit(f'FAIL: could not fetch the JD ({e}); save its text to a file and pass --jd FILE')
        open(jdf, 'w', encoding='utf-8').write(text)
        w.setdefault('urls', {})[slug] = jd_arg; json.dump(w, open(wsp, 'w'), indent=1)
    else:
        if os.path.abspath(jd_arg) != jdf: shutil.copyfile(jd_arg, jdf)
        um = re.search(r'(?:permalink|job\s*link|url)\s*:\s*(https?://\S+)', open(jdf, encoding='utf-8', errors='replace').read(), re.I)
        if um:
            w.setdefault('urls', {})[slug] = um.group(1); json.dump(w, open(wsp, 'w'), indent=1)
    print(f'JD: {jdf} ({len(open(jdf, encoding="utf-8").read().split())} words) | slug: {slug}')
    code_tr = _tracker_code(w, slug)
    jd_url = upload_jd_for(w, slug, _day_of(jdf), code_tr)
    if code_tr and (w.get('tracker') or tracker.configured()):
        jdt = open(jdf, encoding='utf-8', errors='replace').read()
        tline = next((l.strip()[:70] for l in jdt.splitlines() if l.strip()), '')
        u_tr = (w.get('urls') or {}).get(slug, '')
        track_update(w, slug, jd_file=jd_url or jdf, **({'url': u_tr} if u_tr else {}), **({'title': tline} if tline else {}))

    # Check for existing duplicate role in tracker (unless --force is passed)
    csv_path = tracker.resolve(w.get('tracker') or tracker.configured() or os.path.join(os.path.dirname(out), 'applications.csv'))
    if '--force' not in args and tracker.exists(csv_path):
        jd_content = open(jdf, encoding='utf-8').read()
        dups = tracker.find_duplicates(csv_path, code=opt('--code', ''), url=jd_arg if jd_arg.startswith('http') else '', text=jd_content)
        if dups:
            d_entry = dups[0]['row']
            reasons = ', '.join(dups[0]['reasons'])
            cv_path = d_entry.get('cv_pdf') or ''
            status = d_entry.get('status') or 'tracked'
            print(f'\nDUPLICATE DETECTED: This role was already found/processed ({reasons}).')
            print(f'  Status: {status}')
            print(f'  Existing Title: {d_entry.get("title", "")}')
            print(f'  Company: {d_entry.get("client") or d_entry.get("consultancy") or ""}')
            if cv_path:
                print(f'  Existing CV: {cv_path}')
            if d_entry.get('applied_on'):
                print(f'  Applied on: {d_entry.get("applied_on")}')
            print('\nTo proceed and regenerate a new CV anyway, pass --force to prep.')
            return 3
    d = os.path.join(out, slug); os.makedirs(d, exist_ok=True); plan = os.path.join(d, 'plan.json')
    code, txt = run('digest_master.py', M, '--jd', jdf, '--keywords', kw, '--plan', plan, capture=True)
    if code != 0: print(txt); return code
    # Store JD source/apply link in plan.json if it was a URL
    try:
        p_data = json.load(open(plan, encoding='utf-8'))
        if jd_arg.startswith(('http://', 'https://')):
            p_data['apply_url'] = jd_arg
        else:
            # Check if JD text contains a URL
            m_url = re.search(r'https?://[^\s<>"]+', open(jdf, encoding='utf-8').read())
            if m_url: p_data['apply_url'] = m_url.group(0)
        json.dump(p_data, open(plan, 'w', encoding='utf-8'), indent=1)
    except Exception:
        pass
    print(filt(txt, {'clearance', 'integrity', 'evidence', 'timeline'}))
    p = json.load(open(plan, encoding='utf-8')); kws = kw_terms(kw)
    full = [r['ref'] for l in ('main', 'other_detailed', 'earlier') for r in p.get(l, []) if not r.get('brief')]
    brief = [r['ref'] for r in p.get('main', []) if r.get('brief')]
    print('\nROLE DETAIL (planned roles only; most JD-relevant bullets):')
    for refs, mx in ((full, 6), (brief, 2)):
        if refs:
            _, t = run('digest_master.py', M, '--roles', ','.join(map(str, refs)), '--keywords', kw, '--max-bullets', mx, capture=True)
            print(compact_roles(t, kws))
    with tempfile.TemporaryDirectory() as td:   # questions for THIS JD only (jd_dir may hold earlier JDs)
        shutil.copyfile(jdf, os.path.join(td, slug + '.txt'))
        _, t = run('batch_triage.py', M, td, '--answers', ANS, '--next', '--count', 6, capture=True)
    q = [l for l in t.splitlines() if not l.strip().startswith('Record:')]
    print('\nOPEN QUESTIONS (lexicon gaps not yet answered; add Tech-list-only / None essentials from EVIDENCE):')
    print('\n'.join(q) if q else ' none')
    print('\nNEXT: ask the questions in ONE message, then in ONE call: '
          f'cv answers \'[...]\' && cv skeleton {slug}; write {d}/content.json; cv build {slug}')
    return 0

def answers(tri, arg):
    """Record several answers in one call: JSON list of {term, has, detail, roles, also, replace}."""
    src = sys.stdin.read() if arg == '-' else open(arg[1:], encoding='utf-8').read() if arg.startswith('@') else arg
    rc = 0
    for a in json.loads(src):
        cmd = ['--answer', a['term'], 'yes' if a.get('has') else 'no']
        if a.get('has') and a.get('detail'): cmd.append(a['detail'])
        if a.get('roles'): cmd += ['--roles', ','.join(map(str, a['roles']))]
        if a.get('also'): cmd += ['--also', ','.join(a['also']) if isinstance(a['also'], list) else a['also']]
        if a.get('replace'): cmd.append('--replace')
        code, txt = run(*tri, *cmd, capture=True); rc = rc or code
        for l in txt.splitlines():
            if l.startswith(('recorded', 'NOTE', 'WARN', 'FAIL', 'Traceback', 'error')) or 'Error' in l: print(l)
    return rc

RULES = """RULES (full guide: references/writing-guide.md, read only if lint keeps failing):
- Facts only: master bullets/tech + confirmed answers in the roles named. Keep numbers and hedges as given.
- Fill every TODO; delete unused TODO bullets. Paras pre-filled from a master bullet may be reworded, not extended.
- Bullets: past-tense verb (current role: base form "Build", never "Builds"); action + what + tech + outcome; 1-2 lines; no repeats.
- Profile 4-6 lines, no pronouns, years only from EVIDENCE ~Ny; one <b> phrase max; no clearance text.
- Core Capabilities: 4-6 labelled lines mirroring the JD groups, evidence-backed terms only, JD terms first.
- UK spelling for UK roles; bare &; plain hyphen in dates; markup <b> <i> <a href> only.
- Certs paragraph is pre-selected by JD relevance: keep or trim, never add expiry dates.
- Write content.json with the file-write tool (not a shell heredoc: it echoes the file back)."""

def skeleton(w, slug):
    """content.skeleton.json with every fact pre-filled (contact, roles, dates, education, certs); TODO = agent text."""
    sys.path.insert(0, HERE); import digest_master as dm, lint_content as lc
    from arrange_roles import short
    d = json.load(open(w['master'], encoding='utf-8')); roles = dm.load_roles(d)
    dd = os.path.join(w['out'], slug); p = json.load(open(os.path.join(dd, 'plan.json'), encoding='utf-8'))
    kws = kw_terms(','.join(p.get('years', {}).keys()))
    flat = {}
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, (dict, list)): walk(v)
                elif v and k not in flat: flat[k] = v
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(dm.first_dict(d, ('member', 'personal', 'basics', 'contact', 'profile_info')))
    country = {'united kingdom': 'UK', 'great britain': 'UK', 'united states': 'USA'}.get(str(flat.get('country', '')).lower(), flat.get('country', ''))
    city = flat.get('region') if str(flat.get('region', '')).lower() in ('london', 'greater london') else flat.get('city', '')
    name = ' '.join(x for x in (flat.get('preferred_name') or flat.get('first_name'), flat.get('last_name')) if x) or flat.get('name', '')
    # booking link for recruiters (master profiles.calendar): rendered last on the contact line as "Book an interview"
    cal = next((flat[k] for k in ('calendar', 'booking', 'calendly') if re.match(r'https?://', str(flat.get(k, '')))), '')
    contact = {k: v for k, v in (('location', ', '.join(x for x in (city, country) if x)), ('email', flat.get('email')),
               ('phone', flat.get('phone')), ('linkedin', flat.get('linkedin')), ('website', flat.get('website')),
               ('calendar', cal)) if v}

    def best_bullet(i):
        bl = dm.bullets(roles[i])
        past = lambda b: bool(re.match(r'[A-Z][a-z]+ed\b', b))   # "Built ..." reads better than "Supporting ..."
        # Prefer full 1-line (75-95ch) or full 2-line (155-185ch) bullets over 100-145ch trailing half-lines
        full_band = lambda b: 75 <= len(b) <= 95 or 155 <= len(b) <= 185
        return max(bl, key=lambda b: (sum(dm.has(k, b.lower()) for k in kws), past(b), full_band(b), len(b) < 190, -abs(len(b) - 88))) if bl else 'TODO'
    # Page budget (writing guide): the 6 most relevant main roles get bullets (top 3: 4, next 3: 3, the current
    # role at least 3); other main roles, brief gap-fillers and earlier roles get a 1-line para; other engagements 2.
    main = p.get('main', [])
    ranked = sorted((r for r in main if not r.get('brief')), key=lambda r: -r['score'])
    nb = {r['ref']: 4 if k < 3 else 3 for k, r in enumerate(ranked[:6])}
    if main and not main[0].get('brief'): nb[main[0]['ref']] = max(nb.get(main[0]['ref'], 0), 3)
    def item(r, n):
        it = {'type': 'role', 'ref': r['ref'], 'title': dm.g(roles[r['ref']], 'role_title', 'title') or r['title'], 'company': short(r['company'])}
        if n: it['bullets'] = ['TODO'] * n
        else: it['para'] = best_bullet(r['ref'])
        return it
    exp = [item(r, nb.get(r['ref'], 0)) for r in main]
    exp += [item(r, 0) for r in p.get('earlier', [])] + [item(r, 2) for r in p.get('other_detailed', [])]
    def edu_item(e):
        qual = ' '.join(x for x in (dm.g(e, 'qualification', 'degree', 'studyType'), dm.g(e, 'field_of_study', 'area')) if x)
        inst = dm.g(e, 'institution', 'school') or ''
        url = dm.g(e, 'institution_url', 'url', 'website') or ''
        years = '-'.join(x for x in (str(e.get('start_date', ''))[:4], str(e.get('end_date', ''))[:4]) if x)
        inst_text = f'<a href="{url}">{inst}</a>' if url else inst
        return {'type': 'bullet', 'text': f'{qual} — {inst_text}' + (f' ({years})' if years else '')}

    edu = [edu_item(e) for e in sorted(d.get('education') or [], key=lambda e: str(e.get('end_date', '')), reverse=True)]
    jdk = ' '.join(p.get('years', {}).keys()).lower().replace('|', ' ')
    rel = {nm for nm, ok, _ in lc.cert_relevance(d, jdk, bool((p.get('clearance') or {}).get('disclose'))) if ok}
    certs_bullets = []
    for c in dm.first_list_of_dicts(d, ('certification', 'certificate', 'licenses')) + list(d.get('training') or []):
        nm = (c.get('name') or c.get('title') or '').strip()
        if nm not in rel: continue
        url = c.get('credential_url') or c.get('url') or c.get('verification_url') or ''
        iss = c.get('issuer') or ''
        iss_text = f'<a href="{url}">{iss}</a>' if url else iss
        certs_bullets.append({'type': 'bullet', 'text': f'{nm} — {iss_text}' if iss_text else nm})
    content = {'name': name, 'headline': 'TODO', 'contact': contact, 'meta': {'title': f'{name} - CV - {slug}', 'author': name},
               'sections': [{'heading': 'Profile', 'items': [{'type': 'paragraph', 'text': 'TODO'}]},
                            {'heading': 'Core Capabilities', 'items': [{'type': 'labelled', 'label': 'TODO', 'text': 'TODO'} for _ in range(5)]},
                            {'heading': 'Professional Experience', 'items': exp},
                            {'heading': 'Education & Certifications', 'items': edu + certs_bullets}]}
    sk = os.path.join(dd, 'content.skeleton.json')
    json.dump(content, open(sk, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print(f'SKELETON: {sk}  ->  write the filled version to {os.path.join(dd, "content.json")}')
    print(json.dumps({k: v for k, v in content.items() if k != 'sections'}, ensure_ascii=False))
    for s in content['sections']:
        print(f"## {s['heading']}")
        for it in s['items']: print(json.dumps(it, ensure_ascii=False))
    try:
        import render_cv as rc
        print('LINE BUDGETS (Helvetica exact A4 targets - aim for 1L or 2L band, avoid half-filled trailing lines):')
        for b in rc.line_budgets((10.0, 10.5, 11.0)):
            print(f"  {b['size']:.1f}pt: body/capability 1L={b['body_1l'][0]}-{b['body_1l'][1]}ch, 2L={b['body_2l'][0]}-{b['body_2l'][1]}ch"
                  f" | bullet 1L={b['bullet_1l'][0]}-{b['bullet_1l'][1]}ch, 2L={b['bullet_2l'][0]}-{b['bullet_2l'][1]}ch")
    except Exception:
        pass
    print(RULES); return 0

def main(argv):
    if not argv or argv[0] in ('-h', '--help'): print(__doc__); return 0
    cmd, rest = argv[0], argv[1:]
    tracker.use_local_tmp()
    if cmd == 'init':
        out = rest[0]; opt = dict(zip(rest[1::2], rest[2::2]))
        wp = init_ws(out, opt['--master'], opt.get('--jd-dir')); print('workspace:', wp)
        n = mem_merge(os.path.join(os.path.abspath(out), 'answers.json'), os.path.abspath(opt['--master']))
        if n: print(f'remembered answers applied: {n}')
        return 0
    if cmd == 'prep': return prep(rest)
    if cmd == 'daily': return daily(rest)
    if cmd == 'memory': return memory(rest)
    if cmd == 'upload': return upload_cmd(rest)
    if cmd == 'check-master': return run('check_master.py', *rest)
    if cmd == 'import-jds':
        args = list(rest)
        if '--jd-dir' not in args:
            args += ['--jd-dir', load()['jd_dir']]
        return run('import_jds.py', *args)
    w = load(); M, OUT, ANS = w['master'], w['out'], w['answers']
    tri = ['batch_triage.py', M, w['jd_dir'], '--answers', ANS, '--out', os.path.join(OUT, 'triage.json')]
    if cmd == 'triage': return run(*tri)
    if cmd == 'ready':
        # roles with no open question and no built CV yet, best first: build these BEFORE asking anything.
        # A role that hinges on a "no" answer (a blocking gap answered no, or a no-skill named in the job title)
        # is not buildable: it is marked status="not relevant" in the tracker (with the reason) and not listed.
        tp = os.path.join(OUT, 'triage.json')
        if not os.path.exists(tp): run(*tri, capture=True)
        t = json.load(open(tp, encoding='utf-8'))
        ans = json.load(open(ANS, encoding='utf-8')) if os.path.exists(ANS) else {}
        no = {n.lower() for k, v in ans.items() if isinstance(v, dict) and v.get('has') is False
              for n in [k] + list(v.get('aliases') or [])} - NOISE
        dropped = rebuild = 0
        try: rows = {r.get('code'): r for r in tracker.load(w['tracker'])[1]} if w.get('tracker') else {}
        except tracker.TrackerUnavailable as e: print(f'WARN: {e}', file=sys.stderr); rows = {}
        for f in t.get('ranking', []):
            v = t['jds'].get(f, {}); slug = f[:-4] if f.endswith('.txt') else f
            rp = os.path.join(OUT, slug, 'report.json')
            row = rows.get((w.get('codes') or {}).get(slug)) or {}
            pdf = json.load(open(rp, encoding='utf-8')).get('pdf', '') if os.path.exists(rp) else ''
            if any(built(p) for p in (pdf, row.get('cv_pdf'))): continue
            if os.path.exists(rp) or tracker.norm_status(row.get('status')) == 'cv_ready':
                # marked built but the PDF is not on disk: list it again and put the tracker row back to "new"
                if os.path.exists(rp): os.remove(rp)
                track_update(w, slug, status='new'); rebuild += 1
            title = (v.get('meta') or {}).get('title', '')
            gaps = [g.get('term', '') if isinstance(g, dict) else str(g) for g in v.get('gaps') or []]
            hinge = [g for g in gaps if g.lower() in no]
            hinge += [n for n in sorted(no) if n not in {h.lower() for h in hinge}
                      and re.search(r'(?<![a-z0-9])' + re.escape(n) + r'(?![a-z0-9])', title.lower())]
            # a "no" skill the JD keeps repeating (6+ mentions, e.g. a SAS-to-Viya migration) is the job itself
            jdp = os.path.join(w['jd_dir'], f)
            jdt = open(jdp, encoding='utf-8', errors='replace').read().lower() if os.path.exists(jdp) else ''
            hinge += [n for n in sorted(no) if n not in {h.lower() for h in hinge}
                      and len(re.findall(r'(?<![a-z0-9])' + re.escape(n) + r'(?![a-z0-9])', jdt)) >= 6]
            if hinge:
                track_update(w, slug, status='not relevant', reason='answered no: ' + ', '.join(hinge)); dropped += 1
                continue
            cl = v.get('clearance') or {}
            if cl.get('required') and not cl.get('disclose'): continue   # unmet clearance -> ask first
            if gaps: continue
            print(f"{slug} | {title} | match {v.get('match', '')}")
        if dropped: print(f'not relevant (hinges on a "no" answer, marked in tracker): {dropped}', file=sys.stderr)
        if rebuild: print(f'PDF missing on disk, listed again for rebuild (tracker status reset to new): {rebuild}', file=sys.stderr)
        return 0
    if cmd == 'next': return run(*tri, '--next', '--count', rest[0] if rest else 4)
    if cmd in ('answer', 'answers'):
        rc = run(*tri, '--answer', *rest) if cmd == 'answer' else answers(tri, rest[0] if rest else '-')
        n = mem_store(ANS, M)
        if n: print(f'memory: {n} answer(s) remembered for future days')
        return rc
    if cmd == 'refs': return list_refs(w, rest[0] if rest else '')
    if cmd == 'track':
        args, slugs = list(rest), None
        if '--slugs' in args:
            i = args.index('--slugs'); slugs = {s.strip() for s in args[i + 1].split(',') if s.strip()}; del args[i:i + 2]
        return track_all(w, args[0] if args else None, slugs)
    if cmd == 'remember': print(f'memory: {mem_store(ANS, M)} answer(s) saved -> {mem_path()}'); return 0
    if cmd == 'skeleton': return skeleton(w, rest[0])
    if cmd == 'final': return run(*tri, '--final', '--cv-dir', OUT, '--workspace', ws_path())
    if cmd == 'layout':
        slug = rest[0]; c = os.path.join(OUT, slug, 'content.json'); rp = os.path.join(OUT, slug, 'report.json')
        args = ['render_cv.py', c, '--layout'] + rest[1:]
        if '--size' not in rest and os.path.exists(rp):
            sz = json.load(open(rp, encoding='utf-8')).get('font')
            if sz: args += ['--size', str(sz)]
        return run(*args)
    if cmd == 'docx':
        slug = rest[0]; d = os.path.join(OUT, slug); rp = os.path.join(d, 'report.json')
        rep = json.load(open(rp, encoding='utf-8')) if os.path.exists(rp) else {}
        pdf = rep.get('local_pdf') or (rep.get('pdf') if not str(rep.get('pdf', '')).startswith('https://') else '')
        if not pdf or not os.path.exists(pdf):
            pdfs = sorted(f for f in os.listdir(d) if f.endswith('.pdf')) if os.path.isdir(d) else []
            if pdfs: pdf = os.path.join(d, pdfs[0])
        if not pdf or not os.path.exists(pdf):
            print(f'FAIL: no local PDF found for {slug} (run: cv build {slug})'); return 1
        return run('pdf_to_docx.py', pdf, *rest[1:])
    if cmd == 'save-master': return run('save_answers_to_master.py', M, ANS, *rest)
    if cmd == 'digest':
        slug, args = rest[0], rest[1:]
        want = set()
        if '--section' in args:
            i = args.index('--section'); want = {x.strip().lower() for x in args[i + 1].split(',')}; args = args[:i] + args[i + 2:]
        d = os.path.join(OUT, slug); os.makedirs(d, exist_ok=True)
        code, txt = run('digest_master.py', M, '--jd', jd(w, slug), '--plan', os.path.join(d, 'plan.json'), *args, capture=True)
        print(filt(txt, want or {'clearance', 'evidence', 'timeline'})); return code
    if cmd == 'roles':
        slug, refs, args = rest[0], rest[1], rest[2:]
        code, txt = run('digest_master.py', M, '--roles', refs, '--max-bullets', 6, *args, capture=True)
        print('\n'.join(l for l in txt.splitlines() if l.startswith(('== [', ' - ', ' tech:')))); return code
    if cmd == 'build':
        pages = 2
        if '--pages' in rest:   # a short career may honestly fill 1 page: only with the member's agreement
            i = rest.index('--pages'); pages = int(rest[i + 1]); rest = rest[:i] + rest[i + 2:]
        slug = rest[0]; d = os.path.join(OUT, slug)
        c, p = os.path.join(d, 'content.json'), os.path.join(d, 'plan.json')
        if not os.path.exists(c): print(f'FAIL: {c} missing (fill content.skeleton.json and write it there)'); return 1
        cj = json.load(open(c, encoding='utf-8'))
        meta = cj.get('meta', {})
        if len(rest) > 1:
            pdf = os.path.join(d, rest[1])
        else:
            base = re.sub(r'-+', '-', re.sub(r'[^A-Za-z0-9-]+', '-', meta.get('title', slug))).strip('-')
            sc = re.sub(r'-+', '-', re.sub(r'[^A-Za-z0-9-]+', '-', slug)).strip('-')
            if sc and not (base.lower() == sc.lower() or base.lower().endswith('-' + sc.lower())):
                npref = re.sub(r'-+', '-', re.sub(r'[^A-Za-z0-9-]+', '-', cj.get('name', ''))).strip('-')
                if npref and base.lower().startswith(npref.lower() + '-cv') and sc.lower().startswith(base[len(npref) + 4:].lower()):
                    base = f'{npref}-CV-{sc}'
                else:
                    base = f'{base}-{sc}'
            pdf = os.path.join(d, (base or sc or 'cv') + '.pdf')
        code = _tracker_code(w, slug)
        jdp = jd(w, slug)
        job_url = (w.get('urls') or {}).get(slug, '')
        title_line = ''
        if jdp and os.path.exists(jdp):
            jdt = open(jdp, encoding='utf-8', errors='replace').read()
            title_line = next((l.strip()[:70] for l in jdt.splitlines() if l.strip()), '')
            if not job_url:
                um = re.search(r'(?:permalink|job\s*link|url)\s*:\s*(https?://\S+)', jdt, re.I)
                if um: job_url = um.group(1)
        t_path = w.get('tracker') or tracker.configured()
        if not job_url and code and t_path:
            try:
                job_url = next((r.get('url', '') for r in tracker.load(t_path)[1] if r.get('code') == code), '')
            except Exception:
                pass
        rep = {'slug': slug, 'pdf': pdf, 'local_pdf': pdf, 'pages': pages, **({'job_url': job_url} if job_url else {})}
        for step, args in (('arrange', ('arrange_roles.py', c, p, '--answers', ANS)),
                           ('lint', ('lint_content.py', c, M, '--plan', p, '--answers', ANS)),
                           ('render', ('render_cv.py', c, pdf, '--pages', pages)),
                           ('check', ('check_pdf.py', pdf, '--pages', pages, '--content', c))):
            code_rc, txt = run(*args, capture=True)
            issues = [l for l in txt.splitlines() if l.startswith(('FAIL', 'WARN'))]
            rep[step] = {'ok': code_rc == 0, 'issues': issues, 'summary': (txt.strip().splitlines() or [''])[-1]}
            if step == 'render':
                for l in txt.splitlines():
                    if l.startswith('LAYOUT:'): print(f'{slug} {l}')
            if code_rc != 0:
                for l in issues or txt.strip().splitlines()[-5:]: print(f'{slug} {step}: {l}')
                json.dump(rep, open(os.path.join(d, 'report.json'), 'w'), indent=1); return code_rc
            if step == 'lint' and issues:
                for l in issues: print(f'{slug} lint: {l}')
        m = re.search(r'at ([\d.]+)pt.*last page (\d+)%', rep['render']['summary'])
        rep['font'], rep['fill'] = (float(m.group(1)), int(m.group(2))) if m else (None, None)
        json.dump(rep, open(os.path.join(d, 'report.json'), 'w'), indent=1)
        day = _day_of(pdf)
        url = upload_pdf(os.path.join(d, 'report.json'), code)   # CV and JD both go to Drive when "cv_upload" is set
        jd_url = upload_jd_for(w, slug, day, code)
        extra = {}
        if jd_url: extra['jd_file'] = jd_url
        elif jdp and os.path.exists(jdp): extra['jd_file'] = jdp
        if job_url: extra['url'] = job_url
        if title_line: extra['title'] = title_line
        t_code = track_update(w, slug, cv_pdf=url or pdf, cv_built_on=date.today().isoformat(), status='cv_ready', **extra)
        # Look up apply URL from plan.json or tracker (falls back when the tracker row has none)
        apply_url = job_url
        if not apply_url and os.path.exists(p):
            try: apply_url = json.load(open(p, encoding='utf-8')).get('apply_url', '')
            except Exception: pass
        if not apply_url:
            csv_path = tracker.resolve(w.get('tracker') or tracker.configured() or os.path.join(os.path.dirname(OUT), 'applications.csv'))
            if tracker.exists(csv_path):
                try:
                    _, t_rows = tracker.load(csv_path)
                    for r in t_rows:
                        if (code and r.get('code') == code) or (r.get('jd_file') and slug in r.get('jd_file')):
                            apply_url = r.get('url') or ''
                            if apply_url: break
                except Exception: pass

        print(f"OK {slug}: {os.path.basename(pdf)} | {rep['font']}pt | last page {rep['fill']}% | lint {len(rep['lint']['issues'])} warn"
              + (f' | Drive {url}' if url else '') + (f' | Tracker {t_code}' if t_code else '')
              + (f' | Job {job_url}' if job_url else '') + (' | JD in Drive' if jd_url else ''))
        print(f"CV LOCATION: {os.path.abspath(pdf)}")
        if url: print(f"CV DRIVE LINK: {url}")
        if apply_url: print(f"APPLY LINK: {apply_url}")
        return 0
    if cmd == 'brief':
        t = open(os.path.join(HERE, '..', 'references', 'batch-build-brief.md'), encoding='utf-8').read()
        print(t.replace('{SCRIPTS}', HERE).replace('{WORKSPACE}', ws_path()).replace('{OUT}', OUT)); return 0
    print(__doc__); return 2

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
