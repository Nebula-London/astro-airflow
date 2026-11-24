#!/usr/bin/env python3
"""Import a "new JobServe roles" JSON (produced by the jobserve-new-jobs skill) as per-role JD text files.

    python3 import_jds.py JSON [--jd-dir DIR] [--quiet]

Reads a file shaped {"jobs": [...]} (or a bare list) and writes <jd-dir>/<slug>.txt for every role, using the
role's ready-made `jd_text` when present, else a plain block built from its fields. The batch workflow then
consumes the JD_DIR exactly like pasted JDs.
"""
import argparse, json, os, re, sys

def slug_of(job):
    s = (job.get('slug') or '').strip()
    if s:
        return s
    base = f"{job.get('title', 'job')}-{job.get('code', '')}"
    return re.sub(r'[^a-z0-9]+', '-', base.lower()).strip('-') or 'job'

def main(argv):
    ap = argparse.ArgumentParser(prog='import_jds.py', description='Write per-role JD text files from a jobserve JSON.')
    ap.add_argument('json', help='JSON from the jobserve-new-jobs skill ({"jobs":[...]} or a list)')
    ap.add_argument('--jd-dir', default='jds', help='directory for the <slug>.txt files (default jds)')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)

    with open(a.json, encoding='utf-8') as f:
        data = json.load(f)
    jobs = data.get('jobs', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    os.makedirs(a.jd_dir, exist_ok=True)
    n = 0
    for j in jobs:
        if not isinstance(j, dict):
            continue
        p = os.path.join(a.jd_dir, slug_of(j) + '.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write((j.get('jd_text') or compose(j)).rstrip() + '\n')
        n += 1
        if not a.quiet:
            print(p)
    print(f'COUNT={n} DIR={a.jd_dir}')
    return 0

def compose(job):
    parts = [job.get('title', '')]
    head = ' - '.join(x for x in (job.get('location'), job.get('rate')) if x)
    if head:
        parts.append(head)
    if job.get('ir35'):
        parts.append(f"IR35: {job['ir35']}")
    when = ' | '.join(x for x in (
        f"Type: {job['employment_type']}" if job.get('employment_type') else '',
        f"Duration: {job['duration']}" if job.get('duration') else '',
        f"Start: {job['start_date']}" if job.get('start_date') else '') if x)
    if when:
        parts.append(when)
    if job.get('agency'):
        parts.append(f"Posted by: {job['agency']}")
    url = job.get('permalink') or job.get('url') or ''
    if url:
        parts.append(f"Permalink: {url}")
    parts.append('')
    parts.append(job.get('description') or '(no description)')
    if job.get('skills'):
        parts += ['', 'Skills:'] + [f'- {s}' for s in job['skills']]
    return '\n'.join(parts)

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
