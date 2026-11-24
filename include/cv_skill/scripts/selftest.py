#!/usr/bin/env python3
"""Regression test the deterministic pipeline over a folder of JD text files.

Usage:
  selftest.py MASTER.json JD_DIR [--record] [--content content.json]

For every JD_DIR/*.txt:
  1. clearance.decide  -> required level, disclosed level, notes
  2. digest_master.py --jd --keywords (auto-extracted from the JD) --plan  -> must exit 0, plan.json valid
  3. if --content is given: arrange_roles + lint_content on a copy -> no clearance FAIL, clearance line
     present only when the plan discloses a level
  4. batch_triage over the folder (temp answers): no agency/boilerplate token asked as a skill; a repeated
     answer merges roles; an --also alias counts as answered; "no" on a tech-list term keeps its value;
     out-of-range role refs are rejected. Gap terms per JD are part of the baseline.
  5. single-JD fast path on the first JD, in a scratch workspace with its own cache: prep picks the latest
     *-vN master and stays compact; answers records a batch and rejects bad refs; pending answers go to vN+1
     once (original untouched); skeleton covers every planned role and pre-fills facts; build rejects TODO and
     builds a skeleton filled with verbatim master bullets; lint accepts "Assess"/"Process", rejects "Builds".
  6. daily run: tracker keeps member columns; "cv daily" skips applied roles, writes fit to the tracker; an
     answer is remembered and not asked again the next day; "memory add yes" without roles is rejected.
Results are compared with JD_DIR/selftest-expected.json (clearance decisions + main-list refs).
--record writes that file from the current run (review it once by hand, then keep it as the baseline).
Exit 1 on any crash, invariant break or difference from the baseline. Prints timing per JD.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clearance

STOP = set('''the and for with you your our are will this that from have has into who what about their they them
role team work working experience strong ability skills including within across using day per week months month
contract london hybrid remote must essential desirable job description apply posted company client candidate
'''.split())

def jd_keywords(text, n=20):
    """Cheap deterministic keyword list for testing: capitalised / technical tokens by frequency."""
    toks = re.findall(r'\b[A-Z][A-Za-z0-9+#.]{1,20}\b|\b[a-z]+[A-Z0-9][A-Za-z0-9]*\b', text)
    freq = {}
    for t in toks:
        t = t.strip('.')
        if t.lower() in STOP or len(t) < 2: continue
        freq[t] = freq.get(t, 0) + 1
    return ','.join(k for k, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:n])

def triage_checks(py, master_path, jd_dir, tmp):
    """Invariants of batch_triage.py; returns (problems, {jd: [gap terms]})."""
    import batch_triage as bt, digest_master as dm
    probs = []
    T = [py, os.path.join(HERE, 'batch_triage.py'), master_path, jd_dir, '--answers', os.path.join(tmp, 'ans.json'),
         '--out', os.path.join(tmp, 'tri.json')]
    rc, out, err = run(T)
    if rc != 0: return [f'triage crashed: {(err or out).strip().splitlines()[-1:]}'], {}
    tri = json.load(open(os.path.join(tmp, 'tri.json')))
    gaps = {f: sorted(g['term'] for g in v['gaps']) for f, v in tri['jds'].items()}
    for f, v in tri['jds'].items():
        agency = {w.lower() for w in re.findall(r'[A-Za-z0-9]+', v['meta'].get('agency', '')) if len(w) <= 6}
        bad = [g for g in gaps[f] if g.lower() in agency]
        if bad: probs.append(f'{f}: agency token asked as a skill: {bad}')
    master = json.load(open(master_path, encoding='utf-8')); roles = dm.load_roles(master)
    term = next((g for f in gaps for g in gaps[f]), None)
    if term and len(roles) >= 2:
        run(T + ['--answer', term, 'yes', 'first', '--roles', '0']); run(T + ['--answer', term, 'yes', 'second', '--roles', '1'])
        a = json.load(open(os.path.join(tmp, 'ans.json'))).get(term.lower(), {})
        if sorted(a.get('roles', [])) != [0, 1]: probs.append(f'repeated answer did not merge roles: {a.get("roles")}')
        rc, out, _ = run(T + ['--answer', 'SelftestTool', 'yes', 'x', '--roles', str(len(roles) + 5)])
        if 'out of range' not in out: probs.append('out-of-range role ref not rejected')
        other = next((g for f in gaps for g in gaps[f] if g != term), None)
        if other:
            run(T + ['--answer', 'SelftestAlias', 'yes', 'x', '--roles', '0', '--also', other])
            rc, out, _ = run(T + ['--next', '--count', '50'])
            if f'Essential: {other} -' in out or f'Desirable: {other} -' in out: probs.append(f'alias {other} still asked after --also')
    # "no" on a tech-list-only term must not drop it to zero
    for f, v in tri['jds'].items():
        tl = [t for t in v['terms'] if t['tier'] == 'Tech-list only']
        if tl:
            t = tl[0]['term']; os.remove(os.path.join(tmp, 'ans.json')); run(T + ['--answer', t, 'no'])
            v2 = json.load(open(os.path.join(tmp, 'tri.json')))['jds'][f]
            tier = next(x['tier'] for x in v2['terms'] if x['term'] == t)
            if tier != 'Tech-list only': probs.append(f'{f}: "no" on tech-list term {t} changed tier to {tier}')
            break
    return probs, gaps

def workflow_checks(py, master_path, jd_path, tmp):
    """Single-JD fast path (cv.py prep -> answers -> skeleton -> build) in a scratch workspace.
    Uses its own job-search folder so the user's remembered workspace is not touched; the real master is only copied."""
    import hashlib, digest_master as dm
    probs = []
    root = os.path.join(tmp, 'wf'); out = os.path.join(root, 'cv-out'); os.makedirs(out); os.makedirs(os.path.join(root, '.jobsearch'))
    stem = re.sub(r'-v\d+\.json$', '', os.path.basename(master_path)).replace('.json', '')
    for n in (3, 12): shutil.copy(master_path, os.path.join(root, f'{stem}-v{n}.json'))
    env = dict(os.environ, JOB_SEARCH_HOME=root, CV_WS=os.path.join(out, 'workspace.json'),
               CV_MEMORY=os.path.join(tmp, 'memory.json'))
    def cv(*args):
        p = subprocess.run([py, os.path.join(HERE, 'cv.py')] + [str(x) for x in args], capture_output=True, text=True, env=env)
        return p.returncode, p.stdout + p.stderr
    kw = jd_keywords(open(jd_path, encoding='utf-8').read())
    rc, o = cv('prep', out, '--jd', jd_path, '--slug', 'st', '--keywords', kw, '--save-pending')
    if rc != 0: return [f'prep crashed: {o.strip().splitlines()[-1:]}']
    if f'{stem}-v12.json' not in o.splitlines()[0]: probs.append(f'prep did not pick the latest master: {o.splitlines()[0]}')
    for s in ('EVIDENCE', 'TIMELINE PLAN', 'ROLE DETAIL', 'OPEN QUESTIONS', 'NEXT:'):
        if s not in o: probs.append(f'prep output missing {s}')
    for s in ('CONTACT:', 'EDUCATION:', 'CERTIFICATIONS ('):
        if s in o: probs.append(f'prep output not compact: has {s}')
    if len(o) > 30000: probs.append(f'prep output too large: {len(o)} bytes')
    if not os.path.exists(os.path.join(out, 'jds', 'st.txt')): probs.append('prep did not save the JD into jds/')
    plan = json.load(open(os.path.join(out, 'st', 'plan.json')))
    # several answers in one call; out-of-range refs rejected with a non-zero exit
    rc, o = cv('answers', json.dumps([{'term': 'SelftestToolA', 'has': True, 'detail': 'Used SelftestToolA for tests', 'roles': [0], 'also': ['STA']},
                                      {'term': 'SelftestToolB', 'has': False}]))
    if rc != 0 or o.count('recorded:') != 2: probs.append(f'answers batch failed: {o.strip()[-200:]}')
    rc, o = cv('answers', json.dumps([{'term': 'SelftestToolC', 'has': True, 'detail': 'x', 'roles': [9999]}]))
    if rc == 0 or 'out of range' not in o: probs.append('answers: out-of-range ref not rejected')
    # pending answers -> new version, original untouched
    h = hashlib.sha256(open(os.path.join(root, f'{stem}-v12.json'), 'rb').read()).hexdigest()
    rc, o = cv('prep', out, '--jd', jd_path, '--slug', 'st', '--keywords', kw, '--save-pending')
    if not os.path.exists(os.path.join(root, f'{stem}-v13.json')) or 'saved' not in o: probs.append('pending answers not saved to v13')
    if hashlib.sha256(open(os.path.join(root, f'{stem}-v12.json'), 'rb').read()).hexdigest() != h: probs.append('master v12 was modified')
    rc, o = cv('prep', out, '--jd', jd_path, '--slug', 'st', '--keywords', kw, '--save-pending')
    if os.path.exists(os.path.join(root, f'{stem}-v14.json')): probs.append('already-saved answers bumped the version again')
    # skeleton: facts pre-filled, every planned role present, unfilled TODO rejected by lint
    rc, o = cv('skeleton', 'st')
    if rc != 0: return probs + [f'skeleton crashed: {o.strip().splitlines()[-1:]}']
    sk = json.load(open(os.path.join(out, 'st', 'content.skeleton.json')))
    m = json.load(open(master_path, encoding='utf-8')); roles = dm.load_roles(m)
    exp = next(s for s in sk['sections'] if 'experience' in s['heading'].lower())['items']
    want = {r['ref'] for l in ('main', 'other_detailed', 'earlier') for r in plan[l]}
    if {i['ref'] for i in exp} != want: probs.append(f'skeleton roles {sorted(i["ref"] for i in exp)} != plan {sorted(want)}')
    if 'expir' in json.dumps(sk).lower(): probs.append('skeleton shows an expiry date')
    if not sk.get('contact', {}).get('email') or sk['contact']['email'] not in json.dumps(m): probs.append('skeleton contact email not from master')
    cal = ((m.get('member') or {}).get('profiles') or {}).get('calendar') if isinstance((m.get('member') or {}).get('profiles'), dict) else None
    if cal and sk.get('contact', {}).get('calendar') != cal: probs.append('skeleton contact.calendar not taken from master profiles.calendar')
    shutil.copy(os.path.join(out, 'st', 'content.skeleton.json'), os.path.join(out, 'st', 'content.json'))
    rc, o = cv('build', 'st')
    if rc == 0 or 'TODO placeholder' not in o: probs.append('build accepted an unfilled skeleton')
    # filled with verbatim master facts: lint must pass and the PDF must build
    def fill(it):
        if it.get('type') == 'role' and 'ref' in it:
            bl = [b for b in dm.bullets(roles[it['ref']]) if not re.match(r'[A-Za-z]+s\b', b)] or ['Delivered the role.']
            if 'bullets' in it: it['bullets'] = [bl[k % len(bl)] for k in range(min(2, len(it['bullets'])))][:len(set(bl[:2]))]
        return it
    sk['headline'] = dm.g(roles[plan['main'][0]['ref']], 'role_title'); sk['meta']['title'] = sk['name'] + ' - CV - Selftest'
    for s in sk['sections']:
        if s['heading'] == 'Profile': s['items'] = [{'type': 'paragraph', 'text': 'Engineer delivering production systems for clients in several sectors.'}]
        if s['heading'] == 'Core Capabilities': s['items'] = [{'type': 'labelled', 'label': 'Delivery', 'text': 'Stakeholder engagement and documentation'}]
        s['items'] = [fill(i) for i in s['items']]
    json.dump(sk, open(os.path.join(out, 'st', 'content.json'), 'w'), indent=1)
    rc, o = cv('build', 'st')
    if rc != 0 and 'pages, expected 2' in o:   # a short master (e.g. references/example-master.json) fills 1 page
        rc, o = cv('build', 'st', '--pages', '1')
    if rc != 0 or not o.strip().splitlines()[-1].startswith('OK st:'): probs.append(f'filled skeleton did not build: {o.strip().splitlines()[-2:]}')
    rep_st = json.load(open(os.path.join(out, 'st', 'report.json')))
    if '-st.pdf' not in os.path.basename(rep_st.get('local_pdf', '')):
        probs.append(f'build PDF filename does not include slug for uniqueness: {rep_st.get("local_pdf")}')
    rc_l, o_l = cv('layout', 'st')
    if rc_l != 0 or 'LAYOUT (' not in o_l: probs.append(f'cv layout failed: {o_l.strip()[-200:]}')
    rc_d, o_d = cv('docx', 'st')
    if rc_d != 0 or not os.path.exists(re.sub(r'\.pdf$', '.docx', rep_st.get('local_pdf', ''))):
        probs.append(f'cv docx failed: {o_d.strip()[-200:]}')
    # "build" in a single-JD workspace with a configured tracker matches existing row by jd_file stem and updates cv_pdf
    tr_wf = os.path.join(root, 'wf-tracker.csv')
    import tracker as _tr
    _tr.upsert(tr_wf, [{'code': 'ST-ROW-1', 'title': 'Existing JD', 'jd_file': os.path.join(root, 'jds', 'st.txt'), 'status': '3-applied', 'applied_on': '2026-10-20'}])
    cfg_wf = os.path.join(root, 'wf-cfg.json'); json.dump({'tracker': tr_wf}, open(cfg_wf, 'w'))
    env_wf = dict(env, JOBSERVE_CONFIG=cfg_wf)
    p_wf = subprocess.run([py, os.path.join(HERE, 'cv.py'), 'build', 'st', '--pages', str(rep_st.get('pages', 2))],
                          capture_output=True, text=True, env=env_wf)
    row_wf = {r['code']: r for r in _tr.load(tr_wf)[1]}.get('ST-ROW-1', {})
    if p_wf.returncode != 0 or not row_wf.get('cv_pdf') or row_wf.get('status') != '3-applied':
        probs.append(f'build did not update configured tracker row by jd_file while preserving applied status: rc={p_wf.returncode} {row_wf}')
    # voice rule: base forms ending in -ss are fine, third person fails
    for verb, bad in (('Assess', False), ('Process', False), ('Builds', True)):
        c = {'name': 'X', 'headline': '', 'sections': [{'heading': 'Professional Experience', 'items': [
            {'type': 'role', 'title': 't', 'company': 'c', 'bullets': [f'{verb} the data platform.']}]}]}
        cp = os.path.join(tmp, 'voice.json'); json.dump(c, open(cp, 'w'))
        r, lo, _ = run([py, os.path.join(HERE, 'lint_content.py'), cp, master_path])
        if ('third person' in lo) != bad: probs.append(f'lint voice rule wrong for "{verb}"')
    return probs

def daily_checks(py, master_path, jd_dir, tmp):
    """Tracker + memory + "cv daily": finished roles skipped, member columns kept, a remembered "no" never re-asked."""
    import tracker
    probs = []
    root = os.path.join(tmp, 'daily'); os.makedirs(os.path.join(root, '.jobsearch'))
    tr = os.path.join(root, 'applications.csv')
    # tracker round trip: member columns, member status and extra columns survive a tool update
    tracker.upsert(tr, [{'code': 'zKeep1', 'title': 'Kept', 'status': 'new'}])
    f, rows = tracker.load(tr); rows[0].update(notes='call Tue', status='skipped'); f.append('my_col'); rows[0]['my_col'] = 'x'
    tracker.save(tr, f, rows)
    tracker.upsert(tr, [{'code': 'zKeep1', 'title': 'Kept v2', 'status': 'cv_ready', 'notes': 'tool'}])
    r = tracker.load(tr)[1][0]
    if (r['notes'], r['status'], r.get('my_col'), r['title']) != ('call Tue', 'skipped', 'x', 'Kept v2'):
        probs.append(f'tracker overwrote member columns: {r}')
    if tracker.code_of('http://www.jobserve.com/hGGC9') != 'hGGC9' or tracker.code_of('permalink:hGGC9') != 'hGGC9':
        probs.append('tracker.code_of wrong')
    # fixture JSON from the first JDs; one of them already applied for
    jds = sorted(x for x in os.listdir(jd_dir) if x.endswith('.txt'))[:3]
    jobs = [{'slug': os.path.splitext(x)[0], 'uid': f'permalink:zSt{n}', 'code': f'C{n}', 'title': f'Role {n}',
             'permalink': f'http://www.jobserve.com/zSt{n}', 'jd_text': open(os.path.join(jd_dir, x), encoding='utf-8').read()}
            for n, x in enumerate(jds)]
    src = os.path.join(root, '2026-01-02_jd-extracted-from-jobserve.json'); json.dump({'jobs': jobs}, open(src, 'w'))
    last = len(jobs) - 1
    tracker.upsert(tr, [{'code': f'zSt{last}', 'title': 'Applied', 'status': 'applied', 'rate_submitted': '650', 'applied_on': '2026-10-20'}])
    env = dict(os.environ, JOB_SEARCH_HOME=root, CV_MEMORY=os.path.join(root, 'memory.json'))
    def cv(*args, ws=None):
        e = dict(env, **({'CV_WS': ws} if ws else {}))
        p = subprocess.run([py, os.path.join(HERE, 'cv.py')] + [str(x) for x in args], capture_output=True, text=True, env=e)
        return p.returncode, p.stdout + p.stderr
    out1 = os.path.join(root, 'day1')
    rc, o = cv('daily', src, '--master', master_path, '--out', out1)
    if rc != 0: return probs + [f'daily crashed: {o.strip().splitlines()[-3:]}']
    if f'{len(jobs) - 1} to triage, 1 skipped' not in o: probs.append('daily did not skip the applied role')
    if os.path.exists(os.path.join(out1, 'jds', jobs[last]['slug'] + '.txt')): probs.append('applied role imported')
    rows = {r['code']: r for r in tracker.load(tr)[1]}
    if not all(rows.get(f'zSt{n}', {}).get('fit') for n in range(last)): probs.append('daily did not write fit to the tracker')
    if rows[f'zSt{last}'].get('status') != 'applied': probs.append('daily changed an applied row')
    m = re.search(r'(?:Essential|Desirable): (.+?) - current evidence', o)
    if not m: return probs + ['daily printed no question']
    term = m.group(1)
    ws1 = os.path.join(out1, 'workspace.json')
    rc, o = cv('answers', json.dumps([{'term': term, 'has': False}]), ws=ws1)
    if 'remembered' not in o: probs.append(f'answers not written to memory: {o.strip()[-200:]}')
    rc, o = cv('daily', src, '--master', master_path, '--out', os.path.join(root, 'day2'))
    if f': {term} - current evidence' in o: probs.append(f'remembered "no" asked again: {term}')
    if 'remembered answers applied: 0' in o: probs.append('memory not merged into the new day')
    rc, o = cv('memory', 'add', 'SelftestYes', 'yes')
    if rc == 0: probs.append('memory add yes without roles accepted')
    # "init" on an existing workspace (e.g. a new master) keeps tracker + codes, so build still updates the tracker
    rc, o = cv('init', out1, '--master', master_path, '--jd-dir', os.path.join(out1, 'jds'))
    w1 = json.load(open(ws1))
    if not (w1.get('tracker') and w1.get('codes')): probs.append(f'init dropped tracker/codes from the workspace: {sorted(w1)}')
    # "track --slugs" writes only the named roles
    sl = sorted(w1.get('codes') or {})
    if len(sl) >= 2:
        for s in sl[:2]:
            os.makedirs(os.path.join(out1, s), exist_ok=True)
            json.dump({'slug': s, 'pdf': f'https://drive.google.com/file/d/selftest-{s}/view'}, open(os.path.join(out1, s, 'report.json'), 'w'))
        before = tracker.load(tr)[1]
        rc, o = cv('track', '--slugs', sl[0], ws=ws1)
        rows = {r['code']: r for r in tracker.load(tr)[1]}
        a, b = rows.get(w1['codes'][sl[0]], {}), rows.get(w1['codes'][sl[1]], {})
        if 'selftest-' + sl[0] not in a.get('cv_pdf', ''): probs.append(f'track --slugs did not update {sl[0]}: {o.strip()[-200:]}')
        if b.get('cv_pdf') or b != next((r for r in before if r['code'] == w1['codes'][sl[1]]), b): probs.append('track --slugs touched another role')
    # a tool-set "not relevant" (reason "answered no: ...") becomes cv_ready when a CV is built; a member one is kept
    tracker.upsert(tr, [{'code': 'zDrop1', 'status': 'not relevant', 'reason': 'answered no: selftest'},
                        {'code': 'zDrop2', 'status': 'not relevant'}, {'code': 'zDrop3', 'status': 'skipped', 'reason': 'answered no: x'}])
    tracker.upsert(tr, [{'code': c, 'status': 'cv_ready'} for c in ('zDrop1', 'zDrop2', 'zDrop3')])
    rows = {r['code']: tracker.norm_status(r.get('status')) for r in tracker.load(tr)[1]}
    if (rows.get('zDrop1'), rows.get('zDrop2'), rows.get('zDrop3')) != ('cv_ready', 'not relevant', 'skipped'):
        probs.append(f'not-relevant handling wrong: {[rows.get(c) for c in ("zDrop1", "zDrop2", "zDrop3")]}')
    return probs

def sheet_checks(tmp):
    """Google Sheet tracker against a fake Apps Script web app on localhost: reads, changed-cells-only writes,
    numbered statuses kept/written, member columns kept, pending file when the sheet is down, replay next run."""
    import threading, http.server, io, csv as _csv, base64, glob
    import tracker
    probs = []
    S = {'header': ['code', 'title', 'status', 'applied', 'applied_on', 'client'],
         'rows': [['zA', 'Applied one', '3-applied', 'yes', '10/12/2026', 'DXC'], ['zB', 'New one', '1-new', '', '', '']],
          'posts': [], 'fail': False, 'files': [], 'old_script': False}
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype):
            self.send_response(code); self.send_header('Content-Type', ctype); self.end_headers(); self.wfile.write(body.encode())
        def do_GET(self):
            return self._send(200, '{"error": "use POST"}', 'application/json')
        def do_POST(self):
            if S['fail']: return self._send(500, 'down', 'text/plain')
            b = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if b.get('token') != 'tok': return self._send(200, '{"error": "bad token"}', 'application/json')
            if b.get('action') == 'read':
                f = io.StringIO(); w = _csv.writer(f); w.writerow(S['header']); w.writerows(S['rows'])
                return self._send(200, f.getvalue(), 'text/csv')
            if b.get('action') == 'upload':   # mirrors upload_() in tracker-apps-script.gs
                if S['old_script']: return self._send(200, '{"ok": true, "added": 0, "updated": 0}', 'application/json')
                if S.get('flaky'):
                    S['flaky'] -= 1; return self._send(200, '{"error": "Exception: Lock timeout"}', 'application/json')
                data = base64.b64decode(b['data'])
                root = '' if S.get('no_root') else b.get('root', '')
                if root: b['folder'] = f'<{root}>/' + b['folder']
                live = [f for f in S['files'] if f['folder'] == b['folder'] and not f['trashed']]
                for f in live:
                    if f['key'] == b['key']: f['trashed'] = True
                clash = any(f['name'] == b['name'] and f['key'] != b['key'] for f in live)
                name = b['name'][:-4] + '-' + b['suffix'] + '.pdf' if clash else b['name']
                n = len(S['files']); S['files'].append({'name': name, 'key': b['key'], 'folder': b['folder'], 'trashed': False})
                return self._send(200, json.dumps({'ok': True, 'id': f'f{n}', 'url': f'https://drive.google.com/file/d/f{n}/view',
                                                   'bytes': len(data), 'name': name, 'root': root}), 'application/json')
            S['posts'].append(b); hd = S['header']
            for k in b['fields']:
                if k not in hd: hd.append(k); [r.append('') for r in S['rows']]
            at = {r[0]: r for r in S['rows']}
            for row in b['rows']:
                r = at.get(row['code'])
                if r is None: r = [''] * len(hd); S['rows'].append(r); at[row['code']] = r
                for k, v in row.items(): r[hd.index(k)] = v
            self._send(200, '{"ok": true}', 'application/json')
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfgp = os.path.join(tmp, 'sheet-config.json')
    json.dump({'tracker_sheet': {'script_url': f'http://127.0.0.1:{srv.server_address[1]}/exec', 'token': 'tok'}}, open(cfgp, 'w'))
    old = os.environ.get('JOBSERVE_CONFIG'); os.environ['JOBSERVE_CONFIG'] = cfgp
    sheet = 'https://docs.google.com/spreadsheets/d/zSelftestSheet/edit'
    try:
        if not (tracker.is_sheet(sheet) and tracker.resolve(sheet) == sheet and tracker.exists(sheet)): probs.append('sheet URL not recognised')
        for s, n in (('3-applied', 'applied'), ('5.sent-to-client', 'sent-to-client'), ('8-not relevant', 'not relevant'), ('new', 'new')):
            if tracker.norm_status(s) != n: probs.append(f'norm_status({s!r}) != {n!r}')
        for a_, b_, same in (('0.88', '88%', True), ('1', '100%', True), ('10/17/2026', '10/17/26', True),
                             ('10/17/2026', '2026-10-17', True), ('0.88', '89%', False), ('10/17/2026', '10/18/26', False)):
            if tracker._same(a_, b_) != same: probs.append(f'_same({a_!r}, {b_!r}) != {same}')
        if tracker.codes(sheet) != {'zA', 'zB'}: probs.append(f'sheet codes wrong: {tracker.codes(sheet)}')
        if tracker.done_codes(sheet) != {'zA'}: probs.append('numbered "3-applied" not treated as done')
        tracker.upsert(sheet, [{'code': 'zA', 'status': 'cv_ready', 'fit': '90%', 'applied_on': '2026-01-01'},
                               {'code': 'zB', 'status': 'cv_ready', 'fit': '80%'}, {'code': 'zC', 'title': 'Fresh'}])
        rows = {r['code']: r for r in tracker.load(sheet)[1]}
        if rows['zA']['status'] != '3-applied' or rows['zA']['applied_on'] != '10/12/2026': probs.append(f'sheet member status/column overwritten: {rows["zA"]}')
        if rows['zB']['status'] != '2-cv_ready': probs.append(f'numbered style not written: {rows["zB"]["status"]}')
        if rows.get('zC', {}).get('status') != '1-new': probs.append('new sheet row missing or not "1-new"')
        sent = {r['code']: set(r) - {'code'} for r in S['posts'][-1]['rows']} if S['posts'] else {}
        if sent.get('zA') != {'fit'} or sent.get('zB') != {'fit', 'status'}: probs.append(f'sheet write not limited to changed cells: {sent}')
        if 'notes' in S['header']: probs.append('empty "notes" column pushed to the sheet')
        S['fail'] = True; n = len(S['posts'])
        tracker.upsert(sheet, [{'code': 'zB', 'cv_pdf': '/x.pdf'}])
        pend = tracker.pending_path(sheet)
        if not os.path.exists(pend): probs.append('no pending file when the sheet is down')
        if 'zB' not in tracker.codes(sheet): probs.append('codes() lost pending rows while the sheet is down')
        S['fail'] = False
        tracker.upsert(sheet, [])
        rows = {r['code']: r for r in tracker.load(sheet)[1]}
        if rows['zB'].get('cv_pdf') != '/x.pdf' or os.path.exists(pend) or len(S['posts']) != n + 1:
            probs.append('pending sheet rows not replayed on the next run')
        probs += upload_checks(tmp, S, sheet, cfgp, srv.server_address[1])
    except Exception as e:
        probs.append(f'sheet checks crashed: {type(e).__name__}: {e}')
    finally:
        srv.shutdown()
        if old is None: os.environ.pop('JOBSERVE_CONFIG', None)
        else: os.environ['JOBSERVE_CONFIG'] = old
    return probs

def upload_checks(tmp, S, sheet, cfgp, port):
    """CV PDFs to Drive through the same fake web app (config "cv_upload"): an old script is reported, the Drive link
    replaces the local path and the PDF goes to .jobsearch/trash (entries past 30 days pruned), a rebuild replaces its earlier upload, a same-name file of
    another role is suffixed, a failed upload keeps the PDF, and --backfill uploads the rest and relinks the tracker."""
    import glob, tracker, cv as cvm
    probs = []
    json.dump({'tracker': sheet, 'cv_upload': {'folder': 'CVs'},
               'tracker_sheet': {'script_url': f'http://127.0.0.1:{port}/exec', 'token': 'tok'}}, open(cfgp, 'w'))
    tracker._sleep = lambda s: None
    trash = os.path.join(tracker.local('trash', create=True)); stale = os.path.join(trash, '2000-01-01', 'old')
    os.makedirs(stale, exist_ok=True)
    def role(slug, name='Example-CV.pdf', day='2026-10-21'):
        d = os.path.join(tmp, 'cvup', day, slug); os.makedirs(d, exist_ok=True)
        pdf = os.path.join(d, name); open(pdf, 'wb').write(b'%PDF-1.4 selftest ' + slug.encode())
        rp = os.path.join(d, 'report.json'); json.dump({'slug': slug, 'pdf': pdf}, open(rp, 'w')); return rp, pdf
    try:
        rp, pdf = role('up-a-w111')
        S['old_script'] = True
        try: tracker.upload(pdf, 'CVs/x', 'k'); probs.append('upload to a web app without "upload" not reported')
        except tracker.TrackerUnavailable as e:
            if 'no "upload" action' not in str(e): probs.append(f'old web app error unclear: {e}')
        S['old_script'] = False
        url = cvm.upload_pdf(rp, 'zB'); rep = json.load(open(rp))
        if not url or rep.get('pdf') != url or rep.get('local_pdf') != pdf or not os.path.exists(pdf):
            probs.append(f'upload did not swap in the Drive link and keep the local PDF by default: {rep}')
        if S['files'][-1]['folder'] != 'CVs/2026-10-21': probs.append(f'wrong Drive folder: {S["files"][-1]["folder"]}')
        if not cvm.built(url) or cvm.built(os.path.join(tmp, 'missing.pdf')): probs.append('built() wrong for a Drive link / missing PDF')
        open(pdf, 'wb').write(b'%PDF-1.4 rebuilt'); json.dump({'slug': 'up-a-w111', 'pdf': pdf}, open(rp, 'w'))
        cvm.upload_pdf(rp, 'zB')
        if sum(1 for f in S['files'] if f['key'] == 'up-a-w111' and not f['trashed']) != 1: probs.append('rebuilt CV did not replace its upload')
        rp2, _ = role('up-b-w222'); cvm.upload_pdf(rp2)
        if S['files'][-1]['name'] != 'Example-CV-w222.pdf': probs.append(f'same-name upload not suffixed: {S["files"][-1]["name"]}')
        rp3, pdf3 = role('up-c-w333', 'Other.pdf', '2026-10-20'); S['fail'] = True
        if cvm.upload_pdf(rp3) or not os.path.exists(pdf3) or json.load(open(rp3))['pdf'] != pdf3:
            probs.append('failed upload lost the local PDF or changed report.json')
        S['fail'] = False
        tracker.upsert(sheet, [{'code': 'zC', 'cv_pdf': pdf3}])
        rc = cvm.upload_cmd(['--backfill', os.path.join(tmp, 'cvup')])
        link = {r['code']: r for r in tracker.load(sheet)[1]}.get('zC', {}).get('cv_pdf', '')
        if rc != 0 or not link.startswith('https://') or not os.path.exists(pdf3): probs.append(f'backfill did not upload/relink or dropped local PDF: rc={rc} {link}')
        if S['files'][-1]['folder'] != 'CVs/2026-10-20': probs.append('backfill used the wrong date folder')
        json.dump({'tracker': sheet, 'cv_upload': {'folder_id': 'zFolderId0123', 'name': 'cvs', 'keep_local': False},   # folder by id + trash local
                   'tracker_sheet': {'script_url': f'http://127.0.0.1:{port}/exec', 'token': 'tok'}}, open(cfgp, 'w'))
        rp4, pdf4 = role('up-d-w444', 'D.pdf', '2026-10-19'); cvm.upload_pdf(rp4)
        rep4 = json.load(open(rp4))
        if S['files'][-1]['folder'] != '<zFolderId0123>/2026-10-19' or rep4.get('drive', {}).get('folder') != 'cvs/2026-10-19':
            probs.append(f'folder_id upload went to {S["files"][-1]["folder"]} / {rep4.get("drive")}')
        if os.path.exists(pdf4) or not glob.glob(os.path.join(trash, '????-??-??', 'up-d-w444', 'D.pdf')):
            probs.append('keep_local=False did not trash uploaded local PDF')
        if os.path.exists(stale): probs.append('trash entries older than 30 days not pruned')
        rp5, pdf5 = role('up-e-w555', 'E.pdf'); S['no_root'] = True
        if cvm.upload_pdf(rp5) or not os.path.exists(pdf5): probs.append('a web app ignoring folder_id was not reported')
        S['no_root'] = False
        rp6, pdf6 = role('up-f-w666', 'F.pdf'); S['flaky'] = 2
        if not cvm.upload_pdf(rp6) or os.path.exists(pdf6) or S['flaky']: probs.append('a transient Drive error was not retried')
        # JDs: JD-<slug>.pdf next to the CVs of the same day, recorded in .jobsearch/jd-uploads.json (de-dup by code)
        jdd = os.path.join(tmp, 'cv', '2026-10-22', 'jds'); os.makedirs(jdd, exist_ok=True)
        jp = os.path.join(jdd, 'role-x-w777.txt'); open(jp, 'w').write('Role X\nLondon, UK - £600 per day\nType: Contract\n\nBody.')
        n0 = len(S['files']); u = tracker.upload_jd(jp, code='zJ')
        last = S['files'][-1] if len(S['files']) > n0 else {}
        if not u or last.get('name') != 'JD-role-x-w777.pdf' or last.get('folder') != '<zFolderId0123>/2026-10-22' \
                or last.get('key') != 'jd:role-x-w777':
            probs.append(f'JD upload went wrong: {u} {last}')
        if tracker.jd_local(u) != os.path.abspath(jp): probs.append('jd_local() did not map the Drive link back to the JD file')
        jp2 = os.path.join(jdd, 'role-x-other-slug.txt'); shutil.copy(jp, jp2)
        if tracker.upload_jd(jp2, code='zJ') != u or len(S['files']) != n0 + 1: probs.append('same JobServe code uploaded twice')
        jp3 = os.path.join(jdd, 'role-y-w888.txt'); shutil.copy(jp, jp3)
        tracker.upsert(sheet, [{'code': 'zY', 'title': 'Role Y', 'jd_file': jp3}])
        bad = tracker.upload_jds(sheet)
        link = {r['code']: r for r in tracker.load(sheet)[1]}['zY'].get('jd_file', '')
        if bad or not link.startswith('https://'): probs.append(f'upload-jds did not upload and link the JD: {link}')
        tracker.upsert(sheet, [{'code': 'zY', 'jd_file': jp3}])
        if {r['code']: r for r in tracker.load(sheet)[1]}['zY'].get('jd_file') != link: probs.append('a local path replaced the JD Drive link')
        if not (tracker.applied({'applied_on': '2026-10-20'}) and tracker.applied({'rate_submitted': '650'})
                and tracker.applied({'applied': 'yes'}) and not tracker.applied({'status': 'new'})):
            probs.append('applied(): applied_on / rate_submitted / legacy "applied" not recognised')
        # a JD uploaded from its text (find_jobs without --jd-dir) gains its local copy later, by JobServe code
        n1 = len(S['files'])
        ut = tracker.upload_jd('', day='2026-10-23', slug='role-z-w999', code='zZ', text='Role Z\nUK - £500\n\nBody.')
        if not ut or len(S['files']) != n1 + 1 or S['files'][-1]['folder'] != '<zFolderId0123>/2026-10-23':
            probs.append('JD upload from text failed')
        jz = os.path.join(tmp, 'cv', '2026-10-23', 'jds', 'role-z-cv-slug.txt'); os.makedirs(os.path.dirname(jz), exist_ok=True)
        open(jz, 'w').write('Role Z\n')
        if tracker.upload_jd(jz, code='zZ') != ut or len(S['files']) != n1 + 1 or tracker.jd_local(ut) != os.path.abspath(jz):
            probs.append('local copy of a text-uploaded JD not linked (or uploaded twice)')
    finally:
        tracker._sleep = time.sleep
    return probs

def local_checks(py, tmp):
    """Answer memory and the last-workspace pointer land in <job-search>/.jobsearch; nothing in $HOME, /tmp or ~/.Trash."""
    probs = []
    fh = os.path.realpath(os.path.join(tmp, 'home')); js = os.path.join(fh, 'js'); out_tmp = os.path.join(tmp, 'outside-tmp')
    for d in (os.path.join(js, '.jobsearch'), out_tmp): os.makedirs(d)
    env = {k: v for k, v in os.environ.items() if k not in ('JOB_SEARCH_HOME', 'CV_MEMORY', 'CV_WS', 'XDG_CONFIG_HOME',
                                                          'XDG_CACHE_HOME', 'JOBSERVE_CONFIG')}
    env.update(HOME=fh, TMPDIR=out_tmp, PYTHONDONTWRITEBYTECODE='1')
    def cv(*args):
        p = subprocess.run([py, os.path.join(HERE, 'cv.py')] + list(args), cwd=js, capture_output=True, text=True, env=env)
        return p.returncode, p.stdout + p.stderr
    rc, o = cv('memory', 'add', 'Fortran', 'no')
    if rc != 0 or not os.path.exists(os.path.join(js, '.jobsearch', 'memory.json')): probs.append(f'memory not in .jobsearch: {o[-200:]}')
    shutil.copy(os.path.join(HERE, '..', 'references', 'example-master.json'), os.path.join(js, 'alex-morgan-v1.json'))
    rc, o = cv('init', os.path.join(js, 'cv', 'day'), '--master', os.path.join(js, 'alex-morgan-v1.json'))
    if rc != 0 or not os.path.exists(os.path.join(js, '.jobsearch', 'last-ws')): probs.append(f'last-ws not in .jobsearch: {o[-200:]}')
    stray = [os.path.relpath(os.path.join(r, f), fh) for r, _, fs in os.walk(fh) for f in fs
             if not os.path.join(r, f).startswith(js + os.sep)]
    if stray: probs.append(f'written outside the job-search folder: {stray[:5]}')
    if os.listdir(out_tmp): probs.append(f'written to the outside TMPDIR: {os.listdir(out_tmp)[:5]}')
    pyc = [r for r, ds, _ in os.walk(os.path.join(HERE, '..')) if '__pycache__' in ds]
    if pyc: probs.append(f'__pycache__ in the skill folder: {pyc[:3]}')
    return probs

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('master'); ap.add_argument('jd_dir')
    ap.add_argument('--record', action='store_true'); ap.add_argument('--content')
    a = ap.parse_args()
    master = json.load(open(a.master, encoding='utf-8'))
    jds = sorted(f for f in os.listdir(a.jd_dir) if f.endswith('.txt'))
    exp_path = os.path.join(a.jd_dir, 'selftest-expected.json')
    expected = json.load(open(exp_path)) if os.path.exists(exp_path) and not a.record else {}
    import tracker
    tracker.use_local_tmp()                                                 # scratch in <job-search>/.jobsearch/tmp
    got, problems, tmp = {}, [], tempfile.mkdtemp()
    os.environ['JOBSERVE_CONFIG'] = os.path.join(tmp, 'no-config.json')   # never read or write the member's real tracker
    os.environ['JOB_SEARCH_HOME'] = tmp; os.makedirs(os.path.join(tmp, tracker.LOCAL))   # nor its memory, trash, workspace
    py = sys.executable
    for txt, want in [  # clearance.required on known phrasings (agency blurb is not a requirement; "basic" = BPSS)
            ('We are recognised as the European market leader in the delivery of Security Cleared talent to organisations.', None),
            ('Candidates will have or be willing to undergo a basic level of security clearance prior to starting.', 'BPSS'),
            ('We are looking for security cleared candidates for this role.', 'ANY'),
            ('You must hold active SC clearance.', 'SC')]:
        if clearance.required(txt)[0] != want: problems.append(f'clearance.required({txt[:50]!r}...) = {clearance.required(txt)[0]}, expected {want}')
    for f in jds:
        t0 = time.time(); path = os.path.join(a.jd_dir, f); text = open(path, encoding='utf-8').read()
        c = clearance.decide(master, text)
        plan = os.path.join(tmp, f + '.plan.json')
        rc, out, err = run([py, os.path.join(HERE, 'digest_master.py'), a.master, '--jd', path,
                            '--keywords', jd_keywords(text), '--plan', plan])
        if rc != 0: problems.append(f'{f}: digest crashed: {err.strip().splitlines()[-1] if err else rc}'); continue
        pl = json.load(open(plan))
        for k in ('main', 'other_detailed', 'other_line', 'earlier', 'years', 'clearance'):
            if k not in pl: problems.append(f'{f}: plan.json missing "{k}"')
        refs = [r['ref'] for r in pl.get('main', [])]
        if len(refs) != len(set(refs)): problems.append(f'{f}: duplicate refs in main')
        if pl['clearance']['disclose'] != c['disclose']: problems.append(f'{f}: plan clearance differs from clearance.decide')
        if c['disclose'] and c['line'] and re.search(r'\b(19|20)\d{2}\b|MOD|UKSV', c['line']): problems.append(f'{f}: clearance line leaks details')
        if a.content:
            cc = os.path.join(tmp, f + '.content.json'); shutil.copy(a.content, cc)
            rc2, out2, _ = run([py, os.path.join(HERE, 'arrange_roles.py'), cc, plan])
            rc3, out3, _ = run([py, os.path.join(HERE, 'lint_content.py'), cc, a.master, '--plan', plan])
            body = open(cc).read()
            has_line = 'Security clearance' in body
            if bool(c['disclose']) != has_line: problems.append(f'{f}: clearance line present={has_line} but disclose={c["disclose"]}')
            if 'FAIL: clearance' in out3: problems.append(f'{f}: lint clearance FAIL: {out3.strip().splitlines()[0]}')
        got[f] = {'required': c['required'], 'disclose': c['disclose'],
                  'notes': sorted(re.sub(r'\d+-month', 'N-month', n) for n in c['notes']), 'main': refs}
        e = expected.get(f)
        if e:
            for k in ('required', 'disclose', 'notes', 'main'):
                if e.get(k) != got[f][k]: problems.append(f'{f}: {k} changed: expected {e.get(k)} got {got[f][k]}')
        print(f"{f:14} {time.time() - t0:5.2f}s  required={c['required']!s:5} disclose={c['disclose']!s:5} main={len(refs)}")
    tp, tgaps = triage_checks(py, a.master, a.jd_dir, tmp); problems += tp
    if jds:
        t0 = time.time(); wp = workflow_checks(py, a.master, os.path.join(a.jd_dir, jds[0]), tmp); problems += wp
        print(f"workflow       {time.time() - t0:5.2f}s  prep/answers/skeleton/build/voice: {len(wp)} problem(s)")
        t0 = time.time(); dp = daily_checks(py, a.master, a.jd_dir, tmp); problems += dp
        print(f"daily          {time.time() - t0:5.2f}s  tracker/memory/daily: {len(dp)} problem(s)")
    t0 = time.time(); sp = sheet_checks(tmp); problems += sp
    print(f"sheet          {time.time() - t0:5.2f}s  google sheet tracker (fake web app): {len(sp)} problem(s)")
    t0 = time.time(); lp = local_checks(py, tmp); problems += lp
    print(f"local          {time.time() - t0:5.2f}s  nothing written outside the job-search folder: {len(lp)} problem(s)")
    for f, g in tgaps.items():
        if f in got: got[f]['gaps'] = g
        e = expected.get(f)
        if e and 'gaps' in e and e['gaps'] != g: problems.append(f'{f}: triage gaps changed: expected {e["gaps"]} got {g}')
    shutil.rmtree(tmp, ignore_errors=True)
    if a.record:
        json.dump(got, open(exp_path, 'w'), indent=1); print(f'baseline recorded: {exp_path} ({len(got)} JDs)')
    elif not expected:
        print('no baseline yet: review the results above, then run again with --record')
    for p in problems: print('PROBLEM:', p)
    print(('OK' if not problems else 'FAIL') + f': {len(jds)} JD(s), {len(problems)} problem(s)')
    return 1 if problems else 0

if __name__ == '__main__':
    sys.exit(main())
