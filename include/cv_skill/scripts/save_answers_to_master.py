#!/usr/bin/env python3
"""Save confirmed batch answers into a NEW, version-bumped master (the original is never modified).

Usage:
  save_answers_to_master.py MASTER.json answers.json OUT.json [--only "GKE,Tableau"] [--skip "NPPV,defence"] [--dry-run]

For every answer with "has": true and roles:
- appends its per-role detail (detail_by_role, else detail) to that role's achievements, as a sentence
- adds the term (not its --also aliases) to that role's technologies_used, unless the term is all lower case
  (domain/practice words such as "healthcare", "retail": no technology entry)
- adds the term to skills if missing
Digest role indices are mapped to career_history entries by (client, start_date, role_title), because
digest_master.load_roles returns sorted copies. Answers without roles (e.g. "defence", "NPPV") are reported
and skipped: record those by hand in the right master section if wanted.
Then re-reads OUT and verifies field by field that only appended items differ; exit 1 otherwise.
"""
import argparse, copy, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
import digest_master as dm

def key(r): return (dm.g(r, 'client'), dm.g(r, 'start_date'), dm.g(r, 'role_title'))

def sentence(t):
    # digest refs ("Example Bank [4],[11]") are session-local indices: never store them in the master
    t = re.sub(r'\s*\[\d+\](?:\s*,\s*\[\d+\])*', '', t or '')
    t = re.sub(r'\s*\((?:do not|never|not claimable)[^)]*\)', '', t, flags=re.I).strip()
    return (t[0].upper() + t[1:] + ('' if t.endswith('.') else '.')) if t else ''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('master'); ap.add_argument('answers'); ap.add_argument('out')
    ap.add_argument('--only', default=''); ap.add_argument('--skip', default=''); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if os.path.abspath(a.out) == os.path.abspath(a.master): print('FAIL: OUT must differ from MASTER'); return 1
    orig = json.load(open(a.master, encoding='utf-8')); d = copy.deepcopy(orig)
    hist = dm.first_list_of_dicts(d, ('career_history', 'experience', 'work'))
    by_key = {}
    for r in hist: by_key.setdefault(key(r), []).append(r)
    roles = []
    for r in dm.load_roles(d):
        m = by_key.get(key(r), [])
        if len(m) != 1: print(f'FAIL: cannot map role {key(r)} uniquely'); return 1
        roles.append(m[0])
    only = {x.strip().lower() for x in a.only.split(',') if x.strip()}
    skip = {x.strip().lower() for x in a.skip.split(',') if x.strip()}
    log, skipped = [], []
    skills = d.setdefault('skills', [])
    names = {(s.get('name') if isinstance(s, dict) else s or '').lower() for s in skills}
    for k, ans in json.load(open(a.answers, encoding='utf-8')).items():
        term = ans.get('term', k)
        if not ans.get('has') or (only and k not in only) or k in skip: continue
        if not ans.get('roles'): skipped.append(term); continue
        # aliases only stop repeat questions ("threat", "assurance"): never store them as technologies
        tech = [term] if term != term.lower() else []
        for i in ans['roles']:
            r = roles[i]
            s = sentence((ans.get('detail_by_role') or {}).get(str(i)) or ans.get('detail'))
            if s and s not in r.setdefault('achievements', []): r['achievements'].append(s); log.append(f'[{i}] {term}: + achievement')
            for t in tech:
                if t not in r.setdefault('technologies_used', []): r['technologies_used'].append(t); log.append(f'[{i}] {term}: + tech {t}')
        if tech and term.lower() not in names:
            skills.append({'name': term, 'category': 'Technical'}); names.add(term.lower()); log.append(f'skills: + {term}')
    for l in log: print(l)
    if skipped: print('SKIPPED (no roles; add by hand if wanted):', ', '.join(skipped))
    if a.dry_run: print(f'DRY RUN: {len(log)} change(s)'); return 0
    json.dump(d, open(a.out, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    # verify: original untouched, OUT differs only by appended list items
    new = json.load(open(a.out, encoding='utf-8')); bad = []
    if json.load(open(a.master, encoding='utf-8')) != orig: bad.append('master changed on disk')
    def cmp(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            for f in set(x) | set(y):
                if f not in y: bad.append(f'{path}.{f} removed')
                elif f not in x: bad.append(f'{path}.{f} added') if not isinstance(y[f], list) else None
                else: cmp(x[f], y[f], f'{path}.{f}')
        elif isinstance(x, list) and isinstance(y, list):
            if y[:len(x)] != x:
                if len(x) == len(y): [cmp(p, q, f'{path}[{n}]') for n, (p, q) in enumerate(zip(x, y))]
                else: bad.append(f'{path}: existing items altered')
        elif x != y: bad.append(f'{path} changed')
    cmp(orig, new, '$')
    for b in bad: print('FAIL:', b)
    print(('OK' if not bad else 'FAIL') + f': {len(log)} change(s) -> {a.out}')
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
