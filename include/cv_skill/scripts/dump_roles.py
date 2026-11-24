#!/usr/bin/env python3
"""Full-fidelity dump of master roles, so CV claims come from ALL the master says, not from the digest's top bullets.

Usage:
  dump_roles.py MASTER.json [--roles 1,2,5] [--since 2015] [--facts-only]

Why this exists: the digest ranks and truncates bullets, shows near-duplicates once, and drops low-scoring roles, so
specific facts (numbers, named mechanisms, employment type, client-scale details) never reach the writer. Reviewers then
call the CV generic. This script prints, per role and WITHOUT cross-role de-duplication:
  - header: title | contracting entity | client | dates | employment_type | team | location
  - every responsibility / achievement / deliverable line (de-duplicated inside the role only)
  - technologies_used
  - FACTS: every line containing a number (volumes, headcounts, percentages, dates inside text) - use these first
Then, once at the end: the master's top-level achievements (title, description, impact) and clearance sponsor
(sponsor is data, NEVER written on the CV).
Indices are the master's own career_history order after sorting newest-first, as in the digest.
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
import digest_master as dm

NUM = re.compile(r'(?<![\w.])\d[\d,]*(?:\.\d+)?\s?(?:%|GB|TB|PB|MB|ms|us|ns|k|K|M|x)?(?![\w])')


def has_fact(line):
    for m in NUM.finditer(line):
        tok = m.group(0).strip()
        if re.fullmatch(r'(19|20)\d{2}', tok):
            continue
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('master')
    ap.add_argument('--roles', default='')
    ap.add_argument('--since', type=int, default=0)
    ap.add_argument('--facts-only', action='store_true')
    a = ap.parse_args()
    d = json.load(open(a.master, encoding='utf-8'))
    roles = dm.load_roles(d)
    want = {int(x) for x in a.roles.split(',') if x.strip()} if a.roles else None
    for i, r in enumerate(roles):
        if want is not None and i not in want:
            continue
        start = dm.g(r, 'start_date')
        if a.since and (not start or int(start[:4]) < a.since):
            continue
        team = r.get('team') or {}
        print(f"\n#{i} {dm.g(r,'role_title')} | {dm.g(r,'organisation')} | client: {dm.g(r,'client') or '-'} | "
              f"{dm.fmt(start)} - {dm.fmt(dm.g(r,'end_date')) if dm.g(r,'end_date') else 'Present'} | "
              f"type: {dm.g(r,'employment_type') or '?'} | team: {team.get('size','?')}"
              f"{' (leads)' if team.get('leadership') else ''} | {dm.g(r,'location') or '?'}")
        seen, facts = set(), []
        for k in ('responsibilities', 'achievements', 'deliverables'):
            v = r.get(k) or []
            for b in (v if isinstance(v, list) else [v]):
                key = re.sub(r'\W+', ' ', str(b).lower()).strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                if has_fact(str(b)):
                    facts.append(str(b))
                if not a.facts_only:
                    print(f"  [{k[:3]}] {b}")
        if facts:
            print('  FACTS (numbers - use these first):')
            for f in facts:
                print(f"    * {f}")
        if not a.facts_only and dm.techs(r):
            print('  tech:', ', '.join(str(t) for t in dm.techs(r)))
    ach = d.get('achievements') or []
    if ach and want is None:
        print('\n== TOP-LEVEL ACHIEVEMENTS (organisation, date, description, impact) ==')
        for x in ach:
            if isinstance(x, dict):
                print(f"- {x.get('title','')} | {x.get('organisation','')} | {x.get('date','')}\n    {x.get('description','')}\n    impact: {x.get('impact','')}")
    for c in d.get('security_clearances') or []:
        if isinstance(c, dict) and want is None:
            print(f"\n(clearance data, NOT for the CV: {c.get('level_code') or c.get('level')} sponsor {c.get('sponsor','?')}; "
                  f"expires {c.get('expiry_date','?')} - active today is what counts; a new cleared role extends it, do not raise renewal)")


if __name__ == '__main__':
    main()
