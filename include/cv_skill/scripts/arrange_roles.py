#!/usr/bin/env python3
"""Order the Professional Experience section of content.json from plan.json (written by
digest_master.py --plan), so the main timeline has no overlaps and the rest sits under
"Other engagements".

Usage:  arrange_roles.py content.json plan.json [--out content.json] [--move REF=LIST ...] [--plan-only]
        --move 10=main 11=other_line   move roles between lists (main | other_detailed | other_line),
                                        saved back to plan.json; --plan-only applies moves and prints the plan

In content.json, give every experience role item a "ref" (the role index from the digest).
Write roles in any order; this script rebuilds the section as:
  1. MAIN roles (plan "main", newest first)
  2. optional "Earlier roles with directly relevant experience": role items whose ref is not in the plan
     (older-than-window niche roles), newest first
  3. subheading "Other engagements", then the plan "other_detailed" roles (full detail, newest first)
  4. one line listing the plan "other_line" roles: "Also: Company (2021-22), ..." (built automatically;
     same company merged; legal suffixes dropped)
Fills missing "right" (dates) and "company_url" from the plan. To override the selection, edit the
lists in plan.json (move a {"ref": ...} record between lists) and re-run. Other sections are untouched.
"""
import argparse, json, os, re, sys

SUFFIX = re.compile(r'\s*\((formerly|via)[^)]*\)|\s+(Group\s+)?(Ltd|Limited|LLP|LP|Plc|PLC|S\.p\.A\.|Inc\.?)\b\.?', re.I)

def short(name):
    return re.sub(r'\s{2,}', ' ', SUFFIX.sub('', name)).strip()

def yrs(rec):
    s = (rec.get('start') or '')[:4]; e = (rec.get('end') or '')[:4]
    if not e: return f'{s}-present'
    return s if s == e else f'{s}-{e[2:]}' if s[:2] == e[:2] else f'{s}-{e}'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('content'); ap.add_argument('plan'); ap.add_argument('--out')
    ap.add_argument('--move', nargs='*', default=[]); ap.add_argument('--plan-only', action='store_true')
    ap.add_argument('--answers', help='batch answers.json: a confirmed "NPPV" answer (detail "NPPV3") is added to the clearance line only when the JD asks for NPPV')
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding='utf-8'))
    if a.move:
        lists = ('main', 'other_detailed', 'other_line', 'earlier')
        for mv in a.move:
            ref, dest = mv.split('='); ref = int(ref)
            if dest not in lists: print(f'FAIL: unknown list {dest}'); return 1
            rec = next((x for l in lists for x in plan.get(l, []) if x['ref'] == ref), None)
            if not rec: print(f'FAIL: ref {ref} not in plan'); return 1
            for l in lists: plan[l] = [x for x in plan.get(l, []) if x['ref'] != ref]
            plan[dest].append(rec)
        for l in lists:
            plan.setdefault(l, []); plan[l].sort(key=lambda x: x.get('start', ''), reverse=True)
        json.dump(plan, open(a.plan, 'w', encoding='utf-8'), indent=1)
    if a.plan_only:
        for l in ('main', 'other_detailed', 'other_line', 'earlier'):
            print(l + ':', ', '.join(f"[{x['ref']}] {x['company']} {x['right']}" for x in plan.get(l, [])))
        return 0
    content = json.load(open(a.content, encoding='utf-8'))
    sec = next((s for s in content['sections'] if 'experience' in s['heading'].lower()), None)
    if not sec: print('FAIL: no Experience section'); return 1
    roles = {it['ref']: it for it in sec['items'] if it.get('type') == 'role' and 'ref' in it}
    unref = [it for it in sec['items'] if it.get('type') == 'role' and 'ref' not in it]
    keep_other = [it for it in sec['items'] if it.get('type') not in ('role', 'subheading', 'paragraph')]
    if unref: print('WARNING: role items without "ref" are dropped:', [u.get('title') for u in unref])
    LISTS = ('main', 'other_detailed', 'other_line', 'earlier')
    rec = {r['ref']: r for l in LISTS for r in plan.get(l, [])}
    planned = {r['ref'] for l in ('main', 'other_detailed', 'other_line') for r in plan.get(l, [])}

    def fill(ref):
        it = roles[ref]; p = rec.get(ref, {})
        if not it.get('right') and p.get('right'): it['right'] = p['right']
        if p.get('url') and not it.get('company_url'): it['company_url'] = p['url']
        return it

    items, warn = [], []
    for p in plan.get('main', []):
        if p['ref'] in roles: items.append(fill(p['ref']))
        else: warn.append(f"main role [{p['ref']}] {p['company']} has no item in content.json")
    # refs are digest indices, already newest first
    earlier = [fill(ref) for ref in sorted(roles) if ref not in planned]
    if earlier:
        items.append({'type': 'subheading', 'text': 'Earlier roles with directly relevant experience'})
        items += earlier
    promoted = [p for p in plan.get('other_line', []) if p['ref'] in roles]
    for p in promoted: warn.append(f"one-line role [{p['ref']}] {p['company']} has a full item; kept as a full-detail other engagement")
    detailed = sorted([p for p in plan.get('other_detailed', []) if p['ref'] in roles] + promoted,
                      key=lambda p: p.get('start', ''), reverse=True)
    for p in plan.get('other_detailed', []):
        if p['ref'] not in roles: warn.append(f"other_detailed role [{p['ref']}] {p['company']} has no item; listed in the one-liner")
    line_recs = [p for p in plan.get('other_line', []) if p['ref'] not in roles] + [p for p in plan.get('other_detailed', []) if p['ref'] not in roles]
    line_recs += [p for p in plan.get('main', []) if p['ref'] not in roles]
    if detailed or line_recs:
        items.append({'type': 'subheading', 'text': 'Other engagements', 'space_before': 6})
        for p in detailed:
            it = dict(fill(p['ref']))
            # For roles with bullet points under Other engagements, omit dates
            if it.get('bullets'):
                it['right'] = ''
            items.append(it)
        merged = {}
        for p in sorted(line_recs, key=lambda p: p.get('start', ''), reverse=True):
            merged.setdefault(short(p['company']), []).append(p)
        def span_txt(ps):  # one range per organisation: earliest start to latest end
            lo = min(p.get('start', '') for p in ps); hi = '' if any(not p.get('end') for p in ps) else max(p['end'] for p in ps)
            return yrs({'start': lo, 'end': hi})
        if merged:
            def fmt_co(c, ps):
                u = next((p.get('url') for p in ps if p.get('url')), '')
                return f'<a href="{u}">{c}</a> ({span_txt(ps)})' if u else f'{c} ({span_txt(ps)})'
            text = ('Also: ' if detailed else '') + ', '.join(fmt_co(c, v) for c, v in merged.items()) + '.'
            items.append({'type': 'paragraph', 'text': text, 'space_before': 3 if detailed else 0})
    sec['items'] = items + keep_other
    # security clearance: exactly one line, from the plan's minimum-disclosure decision (never hand-written)
    clr = plan.get('clearance') or {}
    for sct in content['sections']:
        sct['items'] = [x for x in sct['items'] if not (x.get('type') == 'labelled' and x.get('label', '').lower().startswith('security clearance'))]
    line = clr.get('line')
    if line and a.answers and os.path.exists(a.answers) and any('NPPV' in n for n in clr.get('notes', [])):
        nppv = json.load(open(a.answers, encoding='utf-8')).get('nppv') or {}
        lvl = re.search(r'NPPV\s?[1-3]', nppv.get('detail', '') + ' ' + nppv.get('term', ''), re.I) if nppv.get('has') else None
        if lvl: line += f" Also holds active {lvl.group(0).upper().replace(' ', '')} police vetting."
    if line:
        clr = {**clr, 'line': line}
        prof = next((x for x in content['sections'] if x['heading'].lower().startswith(('profile', 'summary'))), content['sections'][0])
        prof['items'].append({'type': 'labelled', 'label': 'Security clearance', 'text': clr['line']})
        print(f"clearance line added: {clr['disclose']}")
    json.dump(content, open(a.out or a.content, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print(f"OK: main {len(plan.get('main', []))}, earlier {len(earlier)}, other detailed {len(detailed)}, one-line {len(line_recs)}")
    for w in warn: print('WARNING:', w)
    return 0

if __name__ == '__main__':
    sys.exit(main())
