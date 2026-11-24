#!/usr/bin/env python3
"""Token-efficient view of a master resume JSON. Read this output instead of the raw file.

Usage:
  digest_master.py master.json --jd jd.txt [--keywords "Python*3,REST|RESTful,energy|gas|LNG*3,Kafka"] [--years 10] [--plan plan.json]
  digest_master.py master.json --roles 3,7,10 [--keywords ...] [--max-bullets 8]   # detail for chosen roles only

Default output (compact, ~3-6 KB):
  CONTACT, ROLES outline (index, dates, title, organisation/client, URL, top technologies),
  EDUCATION, CERTIFICATIONS, INTEGRITY findings (duplicate bullets, duplicate technologies,
  title typos, anachronisms, date gaps inside the window), and with --keywords an EVIDENCE
  map: Strong / Partial / None per keyword with the role indices that support it.
--roles prints the de-duplicated bullets and technologies of the given role indices.

Field names vary between master versions; the script detects them heuristically
(career_history / experience / work..., member / personalInfo / basics ...).
"""
import argparse, datetime as dt, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
import clearance

# First public availability (year, month) of tools often named in CVs; used for anachronism checks.
RELEASES = {
    'chatgpt': (2022, 11), 'gpt-4': (2023, 3), 'gpt-4o': (2024, 5), 'chatgpt 4o': (2024, 5),
    'openai api': (2020, 6), 'langchain': (2022, 10), 'langgraph': (2024, 1), 'langfuse': (2023, 5),
    'model context protocol': (2024, 11), 'mcp': (2024, 11), 'claude': (2023, 3), 'gemini': (2023, 12),
    'llm': (2018, 6), 'agentic': (2023, 3), 'openwebui': (2023, 10), 'forgejo': (2022, 12),
    'snowflake cortex': (2023, 6), 'dbt': (2016, 3), 'airflow': (2015, 6), 'astronomer': (2018, 1),
    'kubernetes': (2014, 6), 'docker': (2013, 3), 'eks': (2018, 6), 'ecs': (2015, 4), 'fargate': (2017, 11),
    'lambda': (2014, 11), 'appsync': (2018, 4), 'graphql': (2015, 7), 'react': (2013, 5), 'svelte': (2016, 11),
    'terraform': (2014, 7), 'argocd': (2018, 3), 'databricks': (2013, 1), 'snowflake': (2014, 10),
    'bigquery': (2011, 11), 'pyspark': (2014, 5), 'tensorflow': (2015, 11), 'keras': (2015, 3),
    'fastapi': (2018, 12), 'mwaa': (2020, 11), 'github actions': (2019, 11), 'c++14': (2014, 12),
    'c++17': (2017, 12), 'python 3.6': (2016, 12),
}

ALIASES = {  # canonical key: alternatives seen in member files (JSON Resume, LinkedIn exports, custom)
    'role_title': ('position', 'title', 'job_title', 'jobTitle', 'role'),
    'organisation': ('company', 'name', 'employer', 'organization', 'companyName'),
    'start_date': ('startDate', 'start', 'from', 'date_start'),
    'end_date': ('endDate', 'end', 'to', 'date_end'),
    'organisation_website': ('url', 'website', 'company_url', 'companyUrl'),
    'responsibilities': ('highlights', 'bullets', 'duties', 'summary_points'),
    'technologies_used': ('keywords', 'technologies', 'tech', 'stack', 'skills_used'),
    'employment_type': ('type', 'employmentType', 'contract_type'),
}

def normalise_role(r):
    r = dict(r)
    for k, alts in ALIASES.items():
        if not r.get(k):
            for a in alts:
                v = r.get(a)
                if v and not isinstance(v, dict):
                    r[k] = v; break
    if isinstance(r.get('description'), str) and not r.get('responsibilities'):
        r['responsibilities'] = [x.strip(' -*') for x in re.split(r'\n+|(?<=\.)\s+(?=[A-Z])', r['description']) if x.strip()]
    for k in ('start_date', 'end_date'):
        if not isinstance(r.get(k), str): r[k] = ''
    if r['end_date'].lower() in ('present', 'current', 'now'): r['end_date'] = ''
    for k in ('client', 'client_website', 'role_title', 'organisation', 'employment_type'): r.setdefault(k, '')
    return r

def load_roles(d):
    roles = [normalise_role(r) for r in first_list_of_dicts(d, ('career', 'experience', 'work', 'employment', 'positions'))]
    return sorted(roles, key=lambda r: r.get('start_date') or '', reverse=True)

def first_list_of_dicts(d, hints):
    for k in d:
        if any(h in k.lower() for h in hints) and isinstance(d[k], list) and d[k] and isinstance(d[k][0], dict):
            return d[k]
    return []

def first_dict(d, hints):
    for k in d:
        if any(h in k.lower() for h in hints) and isinstance(d[k], dict):
            return d[k]
    return {}

def g(r, *keys):
    for k in keys:
        v = r.get(k)
        if v: return v
    return ''

def ym(s):
    m = re.match(r'(\d{4})(?:-(\d{2}))?', s or '')
    return (int(m.group(1)), int(m.group(2) or 1)) if m else None

def fmt(s):
    p = ym(s)
    return dt.date(p[0], p[1], 1).strftime('%b %Y') if p else 'Present'

STOP = set('a an and the of for to in on with by as at from into across including using via our its their'.split())

def toks(b):
    return {w for w in re.findall(r'[a-z0-9+#]+', b.lower()) if w not in STOP}

def bullets(r):
    # near-duplicates (same idea restated in responsibilities/achievements/deliverables) are shown once
    out, sets = [], []
    for k in ('responsibilities', 'achievements', 'highlights', 'bullets', 'description', 'deliverables'):
        v = r.get(k)
        for b in (v if isinstance(v, list) else [v] if isinstance(v, str) and v else []):
            t = toks(b)
            if not t: continue
            if any(len(t & o) / min(len(t), len(o)) >= 0.7 for o in sets): continue
            sets.append(t); out.append(b)
    return out

def role_url(r):
    # client's site when there is a client; never fall back to the contracting entity's site
    if g(r, 'client'): return g(r, 'client_website')
    return g(r, 'organisation_website', 'website', 'url')

def techs(r):
    t = r.get('technologies_used') or r.get('technologies') or r.get('skills_used') or []
    return t if isinstance(t, list) else [t]

def text_of(r):
    return ' '.join([g(r, 'role_title', 'title', 'position'), g(r, 'client'), g(r, 'organisation', 'company')] + bullets(r) + techs(r)).lower()

def has(term, text):
    # word-start match so plurals/inflections count (workshop->workshops, architect->architected, model->modeling);
    # short terms (<=3 chars, e.g. SQL, ECS, dbt) must match the whole word
    words = [re.escape(w) for w in term.lower().split()]
    suf = r'(?:s|es|ed|d|ing|ling|led|er|ers|ure|ures|ural|ion|ions|al)?' if len(term) > 3 else ''
    pat = (r's?[\s-]+').join(words) + suf
    return re.search(r'(?<![a-z0-9])' + pat + r'(?![a-z0-9])', text) is not None

def day(s, default=None):
    m = re.match(r'(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?', s or '')
    if not m: return default
    return dt.date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))

def plan_timeline(roles, scores, today, cutoff_day, min_score, prefer='relevance', tolerance_days=31):
    """Split window roles into main (continuous, no overlaps), other_detailed (overlapping but relevant)
    and other_line (low relevance or overlapping low relevance). Returns dict of lists of indices."""
    win = []
    for i, r in enumerate(roles):
        s0 = day(g(r, 'start_date', 'startDate')); e0 = day(g(r, 'end_date', 'endDate'), today)
        if s0 and e0 >= cutoff_day: win.append((i, s0, e0))
    relevant = [w for w in win if scores.get(w[0], 0) >= min_score]
    low = [w[0] for w in win if scores.get(w[0], 0) < min_score]
    ov = lambda a, b: (min(a[2], b[2]) - max(a[1], b[1])).days > tolerance_days
    # connected components of overlapping relevant roles
    clusters, seen = [], set()
    for w in relevant:
        if w[0] in seen: continue
        comp, stack = [], [w]
        while stack:
            x = stack.pop()
            if x[0] in seen: continue
            seen.add(x[0]); comp.append(x)
            stack += [y for y in relevant if y[0] not in seen and ov(x, y)]
        clusters.append(comp)
    main, detailed = [], []
    for comp in clusters:
        best, best_key = None, None
        n = len(comp)
        for mask in range(1, 1 << n):
            pick = [comp[k] for k in range(n) if mask >> k & 1]
            if any(ov(a, b) for x, a in enumerate(pick) for b in pick[x + 1:]): continue
            cover = sum((e - s).days for _, s, e in pick)
            weighted = sum((e - s).days * scores.get(i, 0) for i, s, e in pick)
            ft = sum('part' not in g(roles[i], 'employment_type').lower() for i, _, _ in pick)
            # relevance: months x keyword score (a highly relevant role beats a longer generic one);
            # coverage: fewest gaps first
            key = (weighted, cover, ft) if prefer == 'relevance' else (cover, weighted, ft)
            if best_key is None or key > best_key: best, best_key = pick, key
        ids = {i for i, _, _ in best}
        main += [i for i, _, _ in best]
        detailed += [i for i, _, _ in comp if i not in ids]
    # swap: an overlapping role clearly more relevant (>= 1.3x) than every main role it overlaps takes their place
    span = {w[0]: w for w in win}
    changed = True
    while changed:
        changed = False
        for j in sorted(detailed, key=lambda j: -scores.get(j, 0)):
            hit = [m for m in main if ov(span[j], span[m])]
            if hit and scores.get(j, 0) >= 1.3 * max(scores.get(m, 0) for m in hit):
                main = [m for m in main if m not in hit] + [j]
                detailed = [d for d in detailed if d != j] + hit
                changed = True; break
    order = lambda ids: sorted(ids, key=lambda i: g(roles[i], 'start_date', 'startDate'), reverse=True)
    return {'main': order(main), 'other_detailed': order(detailed), 'other_line': order(low)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('master'); ap.add_argument('--keywords', default='',
                    help='comma list; a|b = synonyms; term*N = weight N (use 3 for must-haves / domain)')
    ap.add_argument('--years', type=int, default=10); ap.add_argument('--roles', default='')
    ap.add_argument('--max-bullets', type=int, default=8, help='with --roles and --keywords: most relevant N bullets per role')
    ap.add_argument('--jd', help='job description text file: enables clearance minimum-disclosure (and is recorded in plan.json)')
    ap.add_argument('--plan', help='write the timeline plan (plan.json) here; needs --keywords')
    ap.add_argument('--min-score', type=int, default=0,
                    help='roles matching fewer distinct keywords go to the other-engagements line (default: 25%% of the top score, min 2)')
    ap.add_argument('--prefer', choices=('relevance', 'coverage'), default='relevance',
                    help='overlap tie-break for the main list: relevance (months x score, default) or coverage (fewest gaps)')
    a = ap.parse_args()
    d = json.load(open(a.master, encoding='utf-8'))
    roles = load_roles(d)
    today = dt.date.today(); cutoff = (today.year - a.years, today.month)

    if a.roles:
        kwl = [x.strip() for k in a.keywords.split(',') for x in re.sub(r'\*\d+$', '', k).split('|') if x.strip()]
        for i in [int(x) for x in a.roles.split(',') if x.strip()]:
            r = roles[i]
            bl = bullets(r)
            if kwl and len(bl) > a.max_bullets:  # keep the most JD-relevant bullets, original order
                rank = sorted(range(len(bl)), key=lambda k: -sum(has(x, bl[k].lower()) for x in kwl))[:a.max_bullets]
                bl = [bl[k] for k in sorted(rank)]
            print(f"== [{i}] {g(r,'role_title','title','position')} | {g(r,'client') or g(r,'organisation','company','employer')}"
                  f" | {fmt(g(r,'start_date','startDate'))} - {fmt(g(r,'end_date','endDate'))}"
                  f" | url: {role_url(r) or '-'}")
            for b in bl[:a.max_bullets if kwl else None]: print(' -', b)
            print(' tech:', ', '.join(dict.fromkeys(techs(r))))
            team = r.get('team')
            if team: print(' team:', json.dumps(team))
        return

    m = first_dict(d, ('member', 'personal', 'basics', 'contact', 'profile_info'))
    flat = {}
    def walk(x, p=''):
        if isinstance(x, dict):
            for k, v in x.items(): walk(v, k)
        elif isinstance(x, str) and x: flat.setdefault(p, x)
    walk(m)
    for pr in (m.get('profiles') if isinstance(m.get('profiles'), list) else []):
        if isinstance(pr, dict) and pr.get('url'): flat[(pr.get('network') or 'profile').lower()] = pr['url']
    print('CONTACT:', json.dumps({k: v for k, v in flat.items() if k not in ('postal_code', 'profile_photo', 'member_id')}))
    wa = first_dict(d, ('authorisation', 'authorization', 'right_to_work'))
    if wa: print('WORK AUTH:', json.dumps(wa))
    jd_text = open(a.jd, encoding='utf-8').read() if a.jd else ''
    clr = clearance.decide(d, jd_text, today)
    if a.jd:
        print('CLEARANCE (minimum disclosure for this JD):', f"disclose {clr['disclose']}" if clr['disclose'] else 'nothing to disclose')
        for n in clr['notes']: print('  note:', n)
    elif clearance.held(d, today)[0]:
        print('CLEARANCE: held (not shown). Pass --jd to compute what this JD allows to disclose.')
    print(f'\nROLES (newest first; * = older than {a.years} years):')
    for i, r in enumerate(roles):
        s = g(r, 'start_date', 'startDate'); e = g(r, 'end_date', 'endDate')
        old = '*' if (ym(e) or (today.year, today.month)) < cutoff else ' '
        org = g(r, 'organisation', 'company', 'employer'); cl = g(r, 'client')
        who = f'{cl} (via {org})' if cl and cl != org else org
        url = role_url(r)
        tech = ', '.join(list(dict.fromkeys(techs(r)))[:12 if old == ' ' else 4])
        print(f"{old}[{i}] {fmt(s)} - {fmt(e)} | {g(r,'role_title','title','position')} | {who}"
              f" | {g(r,'employment_type')} | {url or 'no url'} | {tech}")

    edu = first_list_of_dicts(d, ('education',))
    if edu:
        print('\nEDUCATION:')
        for e in edu:
            print(f" - {g(e,'qualification','degree','studyType')} {g(e,'field_of_study','area')} | {g(e,'institution','school')}"
                  f" | {g(e,'start_date','startDate')}-{g(e,'end_date','endDate')} | {g(e,'institution_url','url') or 'no url'}")
    certs = first_list_of_dicts(d, ('certification', 'certificate', 'licenses'))
    if certs:
        print('\nCERTIFICATIONS (type matters: courses/training are not certifications):')
        for c in certs:
            exp = g(c, 'expiry_date')
            print(f" - {g(c,'name')} | {g(c,'issuer')} | {g(c,'type')} | {g(c,'issue_date')}"
                  f"{' exp ' + exp if exp else ''} | {g(c,'credential_url','url') or 'no url'}")

    print('\nINTEGRITY:')
    found = False; dups = []
    for i, r in enumerate(roles):
        title = g(r, 'role_title', 'title', 'position')
        raw = sum(len(r.get(k) or []) for k in ('responsibilities', 'achievements') if isinstance(r.get(k), list))
        dup = raw - len([b for b in bullets(r) if b in (r.get('responsibilities') or []) + (r.get('achievements') or [])])
        tl = [t.lower() for t in techs(r)]
        dtech = sorted({t for t in tl if tl.count(t) > 1})
        typo = re.search(r'/\w+$|\s{2,}|[-/]{2}|\w/\w+/\w', title)
        s = ym(g(r, 'start_date', 'startDate'))
        ana = [k for k, rel in RELEASES.items() if s and has(k, text_of(r)) and (ym(g(r, 'end_date')) or (today.year, today.month)) < rel]
        msgs = []
        if dup > 0: dups.append(i)
        if dtech: msgs.append('duplicate tech: ' + ', '.join(dtech))
        if typo: msgs.append(f'title looks malformed: "{title}"')
        if ana: msgs.append('possible anachronism (tool released after role ended): ' + ', '.join(ana))
        if msgs: found = True; print(f' [{i}] ' + '; '.join(msgs))
    win = [r for r in roles if (ym(g(r, 'end_date')) or (today.year, today.month)) >= cutoff]
    spans = sorted((ym(g(r, 'start_date')), ym(g(r, 'end_date')) or (today.year, today.month)) for r in win if ym(g(r, 'start_date')))
    reach = None
    for s, e in spans:
        if reach and (s[0] * 12 + s[1]) - (reach[0] * 12 + reach[1]) > 3:
            found = True; print(f' gap: {reach[0]}-{reach[1]:02d} to {s[0]}-{s[1]:02d}')
        reach = max(reach, e) if reach else e
    if dups: found = True; print(f' repeated bullets (responsibilities/achievements) in roles: {dups} (digest shows them once)')
    if not found: print(' none detected')

    if a.keywords:
        print('\nEVIDENCE (Strong = in a role within 5 years; Partial = older only; None = absent):')
        recent = (today.year - 5, today.month)
        years_by_kw = {}
        print(' (tech-only = named in a technologies list but not in any bullet: confirm before claiming in a bullet; years = union of role durations)')
        for kw in [k.strip() for k in a.keywords.split(',') if k.strip()]:
            alts = [x.strip() for x in re.sub(r'\*\d+$', '', kw).split('|')]
            hits = [i for i, r in enumerate(roles) if any(has(x, text_of(r)) for x in alts)]
            inb = [i for i in hits if any(has(x, ' '.join(bullets(roles[i])).lower()) for x in alts)]
            strong = [i for i in inb if (ym(g(roles[i], 'end_date')) or (today.year, today.month)) >= recent]
            skill = any(any(has(x, json.dumps(s).lower()) for x in alts) for s in d.get('skills', []))
            level = ('Strong' if strong else 'Partial' if inb else 'Tech-list only' if hits
                     else 'Skills-list only' if skill else 'None')
            months = set()
            for i in hits:
                s0 = day(g(roles[i], 'start_date')); e0 = day(g(roles[i], 'end_date'), today)
                if s0: months |= {(y, m) for y in range(s0.year, e0.year + 1) for m in range(1, 13) if (s0.year, s0.month) <= (y, m) <= (e0.year, e0.month)}
            yrs = f' ~{len(months) / 12:.0f}y' if months else ''
            years_by_kw[re.sub(r'\*\d+$', '', kw)] = round(len(months) / 12, 1)
            print(f' {kw}: {level}{yrs} {hits[:12] if hits else ""}')

        kws, wts = [], []
        for k in [k.strip() for k in a.keywords.split(',') if k.strip()]:
            m = re.match(r'(.*?)\*(\d+)$', k); kws.append([x.strip() for x in (m.group(1) if m else k).split('|')]); wts.append(int(m.group(2)) if m else 1)
        scores = {i: sum(w for alts, w in zip(kws, wts) if any(has(x, text_of(r)) for x in alts)) for i, r in enumerate(roles)}
        OUTCOME = re.compile(r'\b(\d+\s?%|reduc|increas|improv|decommission|no (?:subsequent )?(?:support tickets|incidents)|saved|cut|grew|launched|recommended to|cto|ceo|board)', re.I)
        for i, r in enumerate(roles):  # small boost for evidenced outcomes (only when already relevant)
            if scores[i] and OUTCOME.search(' '.join(r.get('achievements') or [])): scores[i] += 1
        cut_day = dt.date(cutoff[0], cutoff[1], 1)
        inwin = [i for i, r in enumerate(roles) if day(g(r, 'end_date', 'endDate'), today) >= cut_day]
        top = max((scores[i] for i in inwin), default=0)
        min_score = a.min_score or max(2, round(top * 0.25))
        pl = plan_timeline(roles, scores, today, cut_day, min_score, a.prefer)
        # fill main-list gaps (> 3 months) with roles that do not overlap any main role: shown briefly
        def span(i): return day(g(roles[i], 'start_date')), day(g(roles[i], 'end_date'), today)
        def ovl(i, j):
            (a0, a1), (b0, b1) = span(i), span(j); return (min(a1, b1) - max(a0, b0)).days > 31
        brief = []
        for j in sorted(pl['other_line'] + pl['other_detailed'], key=lambda j: -scores[j]):
            if any(ovl(j, m) for m in pl['main']): continue
            for l in ('other_line', 'other_detailed'):
                if j in pl[l]: pl[l].remove(j)
            pl['main'].append(j); brief.append(j)
        pl['main'].sort(key=lambda i: g(roles[i], 'start_date'), reverse=True)
        older = [i for i, r in enumerate(roles) if day(g(r, 'end_date', 'endDate'), today) < cut_day and scores[i] >= min_score]
        pl['earlier'] = sorted(sorted(older, key=lambda i: -scores[i])[:4], key=lambda i: g(roles[i], 'start_date'), reverse=True)
        def label(i):
            r = roles[i]
            return (f"[{i}] {g(r,'client') or g(r,'organisation','company')} | {g(r,'role_title','title')} | "
                    f"{fmt(g(r,'start_date'))} - {fmt(g(r,'end_date'))} | score {scores[i]}")
        print(f'\nTIMELINE PLAN (last {a.years} years; min score {min_score} of {len(kws)} keywords; overlaps > 31 days; prefer {a.prefer}):')
        print(' MAIN (reverse chronological, no overlaps; B = brief gap-filler, write a 1-line para):'); [print('  ' + ('B ' if i in brief else '') + label(i)) for i in pl['main']]
        print(' OTHER ENGAGEMENTS - full detail (overlap a main role, still relevant):'); [print('  ' + label(i)) for i in pl['other_detailed']]
        print(' OTHER ENGAGEMENTS - one line (low relevance):'); [print('  ' + label(i)) for i in pl['other_line']]
        print(' EARLIER (older than window, relevant; 1-2 line paras under "Earlier roles with directly relevant experience"):'); [print('  ' + label(i)) for i in pl['earlier']]
        gaps, prev = [], None
        others = pl['other_detailed'] + pl['other_line']
        for i in reversed(pl['main']):
            s0 = day(g(roles[i], 'start_date')); e0 = day(g(roles[i], 'end_date'), today)
            if prev and (s0 - prev).days > 62:
                cover = [f"[{j}]" for j in others if day(g(roles[j], 'start_date')) < s0 and day(g(roles[j], 'end_date'), today) > prev]
                gaps.append(f"{prev:%b %Y} - {s0:%b %Y}" + (f" (covered by {' '.join(cover)}; consider moving to main)" if cover else ''))
            prev = max(prev, e0) if prev else e0
        if gaps: print(' main-list gaps over 2 months:', '; '.join(gaps))
        print(' keyword weights: append *N to must-have / domain keywords (e.g. "energy|gas|LNG*3") so domain roles win overlaps')
        if a.plan:
            def rec(i):
                r = roles[i]
                return {'ref': i, 'company': g(r, 'client') or g(r, 'organisation', 'company'), 'brief': i in brief,
                        'title': g(r, 'role_title', 'title'), 'url': role_url(r), 'score': scores[i],
                        'start': g(r, 'start_date'), 'end': g(r, 'end_date'),
                        'right': f"{fmt(g(r,'start_date'))} - {fmt(g(r,'end_date'))}"}
            out = {k: [rec(i) for i in v] for k, v in pl.items()}; out['years'] = years_by_kw
            out['clearance'] = {'disclose': clr['disclose'], 'line': clr['line'], 'notes': clr['notes']}
            json.dump(out, open(a.plan, 'w', encoding='utf-8'), indent=1)
            print(f' plan written: {a.plan} (edit the lists to override, then run arrange_roles.py)')

if __name__ == '__main__':
    sys.exit(main())
