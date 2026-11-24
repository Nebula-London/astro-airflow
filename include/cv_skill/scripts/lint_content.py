#!/usr/bin/env python3
"""Lint a CV content.json against the master resume: unsupported claims and weak writing.

Usage:  lint_content.py content.json master.json [--plan plan.json]

Only role items carrying "ref" (digest indices from plan.json) are checked per role.
Claims (FAIL = must fix before rendering; WARN = review / ask the member):
- role item with "ref": every technology/skill term from the master's vocabulary that appears in the
  item must be evidenced in THAT role (bullets); tech-list only -> WARN; only in other roles -> FAIL
  (scope moved); numbers not present in that role's text -> FAIL; title differing from the master -> WARN
- Profile / Core Capabilities: vocabulary terms must exist somewhere in the master; "N+ years" claims
  must not exceed the years computed for that keyword (plan.json "years") or the career span
- certification paragraph: a course/training must not be presented as a certification (OSCP etc.); expiry/validity
  dates or "expired" -> FAIL; a certification relevant to the JD keywords missing, or an irrelevant one
  listed -> INFO (advisory, not counted). A leftover TODO placeholder -> FAIL. Expired certifications are judged like current ones
Voice (FAIL): pronouns (I/my/he/she) in the Profile; Profile sentences or bullets starting with a
  third-person verb ("Uses", "Coaches") - implied first person is the UK convention.
Writing (WARN): duty phrases, US spellings, HTML entities, bullets repeated across roles,
semicolon chains, verbless openers.
Exit code 1 if any FAIL.
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
import digest_master as dm

DUTY = re.compile(r'^(responsible for|participat\w+ in|involved in|managed testing|worked on|helped|assisted|tasked with|duties)', re.I)
US = re.compile(r'\b(optimiz\w*|organiz\w*|analyz\w*|modeling|modeled|color\w*|behavior\w*|center\w*)', re.I)
VERBLESS = re.compile(r'^(multi-|production-grade|solution architecture|end-to-end|enterprise-level|scalable)\b', re.I)
# Reviewer lessons (references/reviewer-lessons.md): empty adjectives and unexplained architecture labels read as
# buzzwords and sank credibility scores. WARN only; fix by stating the concrete thing built, or drop the term.
BUZZ = re.compile(r"\b(enterprise-level|enterprise-grade|scalable|robust|innovative|cutting-edge|state-of-the-art|production-grade|"
                  r"data as a product|domain-driven design|sovereign|target-state|data strategy|operational control|"
                  r"architectural guidance|production stability|operational support|iterative project decomposition|"
                  r"optimi[sz]\w*|improved maintainability|interoperability|seamless\w*|best-in-class|world-class|"
                  r"synerg\w*|leverag\w+|spearhead\w*|holistic)\b", re.I)
ARCH = re.compile(r"\b(medallion|zero[- ]trust|event-driven architecture|cloud-agnostic|cloud-portable|multi-cloud|serverless architecture|"
                  r"microservices? architecture|data mesh|lakehouse|schema-enforced|enterprise architecture)\b", re.I)
COINED = re.compile(r"\b[A-Z]{5,}\b\s*\([A-Z][a-z]+(?:,\s*[A-Z][a-z]+){2,}\)")   # an internal acronym expanded in brackets (e.g. FELTON (Find, Extract, ...))
NUM = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)(\s?%|\+)?')

def strip(t): return re.sub(r'<[^>]+>', '', t or '').replace('&amp;', '&')

def vocab(d, roles):
    terms = set()
    for r in roles:
        terms |= {t.strip() for t in dm.techs(r) if isinstance(t, str) and 2 <= len(t.strip()) <= 40}
    for s in d.get('skills', []):
        n = s.get('name') if isinstance(s, dict) else s
        if isinstance(n, str) and 2 <= len(n) <= 40: terms.add(n.strip())
    # drop generic words that cause noise
    generic = {'Integration', 'Development', 'Software Development', 'Cloud', 'Web', 'API', 'APIs', 'DB', 'OS', 'Data Analysis',
               'Documentation', 'Security', 'Mobile', 'Networking', 'Sales', 'Data Pipelines', 'Data Engineering', 'DevOps'}
    return sorted((t for t in terms if t not in generic), key=len, reverse=True)

def found(term, text):
    t = text.lower()
    if dm.has(term, t) or (term.endswith('s') and len(term) > 4 and dm.has(term[:-1], t)): return True
    # versioned names: "C++" is evidenced by "C++14" / "C++11/14", "Python" by "Python 3.6"
    return re.search(r'(?<![a-z0-9])' + re.escape(term.lower()) + r'\s?\d', t) is not None

CERT_DOMAINS = [   # (pattern on the certification/course name, words that make it relevant when found in the JD keywords)
    (r'giac|sans|gsec|gfact|oscp|pen-200|penetration|malware|cyber|security|cissp|ceh|comptia|crypt',
     ['security', 'cyber', 'devsecops', 'penetration', 'zero trust', 'hardening', 'malware', 'assurance', 'iso 27001', 'soc',
      'siem', 'threat', 'vulnerab', 'secure', 'nppv', 'clearance']),
    (r'data engineer|google cloud|gcp|aws|azure|snowflake|databricks|mongodb|bigquery',
     ['data', 'gcp', 'google cloud', 'bigquery', 'aws', 'azure', 'snowflake', 'dbt', 'pipeline', 'etl', 'analytics',
      'warehouse', 'lakehouse', 'mongodb', 'nosql', 'cloud']),
    (r'machine learning|deep learning|\bai\b|ai |tensorflow|llm|agent|vector',
     ['machine learning', ' ml ', 'mlops', 'genai', 'generative', 'llm', 'deep learning', 'rag', 'agent', 'mcp', ' ai ',
      'nlp', 'embedding', 'vector']),
    (r'python', ['python']),
]
CERT_STOP = {'and', 'the', 'of', 'with', 'for', 'in', 'certification', 'certificate', 'professional', 'giac', 'sans',
             'specialization', 'specialisation', 'foundational', 'essentials', 'technologies', 'network', 'endpoint',
             # generic words that matched unrelated JD keywords ("unit testing" -> OSCP, "software engineering" -> malware)
             'testing', 'engineering', 'engineer', 'building', 'applications', 'powered', 'search', 'memory', 'reverse', 'kali'}

def _word(w, k): return re.search(r'(?<![a-z0-9])' + re.escape(w.strip()) + r'(?![a-z0-9])', k) is not None

def cert_relevance(d, jdk, cleared=False):
    """[(name, relevant, is_certification)] for every certification/course/training in the master.
    A JD requiring security clearance makes security certifications relevant."""
    out, k = [], ' ' + jdk + ' '
    items = [(x, True) for x in dm.first_list_of_dicts(d, ('certification', 'certificate', 'licenses'))] + \
            [(x, False) for x in (d.get('training') or [])]
    for cert, is_cert in items:
        nm = (cert.get('name') or cert.get('title') or '').strip() if isinstance(cert, dict) else ''
        if not nm: continue
        typ = (cert.get('type') or '').lower()
        is_cert = is_cert and 'certif' in typ
        low = nm.lower(); rel = False
        for n, (pat, words) in enumerate(CERT_DOMAINS):
            if re.search(pat, ' ' + low + ' ') and (any(_word(w, k) for w in words) or (n == 0 and cleared)): rel = True
        toks = [t for t in re.findall(r'[a-z0-9+]+', low) if t not in CERT_STOP and len(t) > 2]
        if any(re.search(r'(?<![a-z0-9])' + re.escape(t) + r'(?![a-z0-9])', k) for t in toks): rel = True
        out.append((nm, rel, is_cert))
    return out

def cert_listed(nm, certsec):
    toks = [t for t in re.findall(r'[a-z0-9+]+', nm.lower()) if t not in CERT_STOP and len(t) > 1]
    acr = re.findall(r'\(([A-Z0-9-]{2,})\)', nm)
    hit = sum(1 for t in toks if re.search(r'(?<![a-z0-9])' + re.escape(t) + r'(?![a-z0-9])', certsec))
    return any(x.lower() in certsec for x in acr) or bool(toks and hit / len(toks) >= 0.6)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('content'); ap.add_argument('master'); ap.add_argument('--plan')
    ap.add_argument('--answers', help='batch answers.json: confirmed answers count as evidence in the roles named')
    a = ap.parse_args()
    c = json.load(open(a.content, encoding='utf-8')); d = json.load(open(a.master, encoding='utf-8'))
    roles = dm.load_roles(d)
    plan = json.load(open(a.plan, encoding='utf-8')) if a.plan else {}
    V = vocab(d, roles)
    master_text = json.dumps(d, ensure_ascii=False).lower()
    btext = {i: ' '.join([dm.g(r, 'role_title', 'title'), dm.g(r, 'client'), dm.g(r, 'organisation')] + dm.bullets(r)).lower()
             for i, r in enumerate(roles)}
    if a.answers:
        for ans in json.load(open(a.answers, encoding='utf-8')).values():
            if not ans.get('has'): continue
            by_role = ans.get('detail_by_role') or {}
            names = ' '.join([ans.get('term', '')] + ans.get('aliases', []))
            for i in ans.get('roles', []):
                if i in btext: btext[i] += f" {names} {by_role.get(str(i), ans.get('detail', ''))}".lower()
            master_text += f" {names} {ans.get('detail', '')}".lower()
    ttext = {i: ' '.join(dm.techs(r)).lower() for i, r in enumerate(roles)}
    fails, warns, infos = [], [], []
    if re.search(r'\bTODO\b', json.dumps(c, ensure_ascii=False)):
        fails.append('TODO placeholder left in content.json (fill it or delete the item)')
    seen_bullets = []

    for sec in c.get('sections', []):
        head = sec.get('heading', '')
        for it in sec.get('items', []):
            texts = [strip(it.get('text', '')), strip(it.get('para', ''))] + [strip(b) for b in it.get('bullets', [])]
            body = ' '.join(t for t in texts if t)
            raw = json.dumps(it, ensure_ascii=False)
            if '&amp;' in raw or '&nbsp;' in raw: warns.append(f'{head}: HTML entity in text (write a bare "&")')
            for b in it.get('bullets', []):
                sb = strip(b)
                if DUTY.match(sb): warns.append(f'duty phrasing: "{sb[:60]}"')
                if VERBLESS.match(sb): warns.append(f'verbless opener: "{sb[:60]}"')
                if sb.count(';') >= 2: warns.append(f'semicolon chain: "{sb[:60]}"')
                t = dm.toks(sb)
                for o, ob in seen_bullets:
                    if t and o and len(t & o) / min(len(t), len(o)) > 0.6:
                        warns.append(f'near-duplicate bullets: "{sb[:45]}" ~ "{ob[:45]}"'); break
                seen_bullets.append((t, sb))
            for m in US.finditer(body): warns.append(f'US spelling: "{m.group(0)}"')

            if it.get('type') == 'role' and 'ref' in it:
                i = it['ref']
                if not 0 <= i < len(roles): fails.append(f'ref {i} out of range'); continue
                r = roles[i]; who = f"[{i}] {it.get('company', '')}"
                mt = dm.g(r, 'role_title', 'title')
                if it.get('title') and it['title'].strip().lower() != mt.strip().lower():
                    warns.append(f'{who}: title "{it["title"]}" differs from master "{mt}" (needs member approval)')
                text = ' '.join([strip(it.get('title', ''))] + [strip(x) for x in texts[1:]])
                for term in V:
                    if not found(term, text): continue
                    if found(term, btext[i]): continue
                    if found(term, ttext[i]): continue   # listed for this role: acceptable in its bullets
                    else:
                        other = [j for j in btext if j != i and (found(term, btext[j]) or found(term, ttext[j]))]
                        fails.append(f'{who}: "{term}" not in this role' + (f' (found in roles {other[:5]}: scope moved)' if other else ' (not in master)'))
                if it.get('para') and not it.get('bullets'):
                    pt = dm.toks(strip(it['para'])); bt = dm.toks(btext[i])
                    if pt and len(pt & bt) / len(pt) < 0.35:
                        warns.append(f'{who}: para looks built from the technologies list, not the role bullets (use a bullet fact, or move the role to other_line)')
                rt = (btext[i] + ' ' + ttext[i] + ' ' + json.dumps(r).lower())
                for m in NUM.finditer(text):
                    n = m.group(1)
                    if len(n) == 4 and n.startswith(('19', '20')): continue
                    if n not in rt: fails.append(f'{who}: number "{m.group(0).strip()}" not in this role')
            elif 'experience' not in head.lower():
                low = body.lower()
                if 'certif' in head.lower() or 'education' in head.lower():
                    for cert in dm.first_list_of_dicts(d, ('certification', 'certificate', 'licenses')):
                        typ = (cert.get('type') or '').lower()
                        if typ in ('training', 'course') and re.search(r'\bOSCP\b(?!\s+Penetration)', body) and 'pen-200' in cert.get('name', '').lower():
                            fails.append('certifications: PEN-200 is a course; do not present it as the OSCP certification')
                    if re.search(r'\b(expir\w*|exp\.?\s*\d|valid (until|to|through)|lapsed)\b', body, re.I):
                        fails.append('certifications: no expiry/validity dates or "expired" on the CV (list the certification without them)')

                allb = ' '.join(btext.values())
                for term in V:
                    if not found(term, low): continue
                    if not found(term, master_text): fails.append(f'{head}: "{term}" not in master')
                    elif head.lower().startswith(('profile', 'summary')) and not found(term, allb):
                        warns.append(f'{head}: "{term}" appears only in technology/skills lists; the Profile states it as an activity')
                for m in re.finditer(r'(\d+)\+?\s*years?', low):
                    n = int(m.group(1)); ctx = low[max(0, m.start() - 60):m.end() + 60]
                    yrs = plan.get('years', {})
                    best = max([v for k, v in yrs.items() if any(found(x, ctx) for x in k.split('|'))] or [0])
                    starts = [dm.day(dm.g(r, 'start_date')) for r in roles if dm.day(dm.g(r, 'start_date'))]
                    span = (dm.dt.date.today() - min(starts)).days / 365.25 if starts else 0
                    limit = best or span
                    if n > limit + 0.5: fails.append(f'{head}: "{m.group(0)}" exceeds evidence ({limit:.0f}y{" for " + ", ".join(k for k in yrs if any(found(x, ctx) for x in k.split("|"))) if best else " career span"})')

    # certifications: list the ones RELEVANT to this JD (expired count the same; never show expiry dates).
    # Relevance = the certification's domain (or its own name words) appears in the JD keywords stored in plan.json.
    certsec = ' '.join(strip(it.get('text', '')) + ' ' + strip(it.get('title', '')) for sec in c.get('sections', [])
                       if re.search(r'certif|education', sec.get('heading', ''), re.I) for it in sec.get('items', [])).lower()
    jdk = ' '.join(plan.get('years', {}).keys()).lower().replace('|', ' ')
    if certsec and jdk:
        cleared = bool((plan.get('clearance') or {}).get('disclose'))
        for nm, rel, is_cert in cert_relevance(d, jdk, cleared):
            listed = cert_listed(nm, certsec)
            # advisory only (INFO, not counted): relevance is a keyword heuristic and the member decides
            if rel and is_cert and not listed:
                infos.append(f'certifications: "{nm}" matches this JD but is not on the CV')
            elif listed and not rel:
                infos.append(f'certifications: "{nm}" does not match this JD keywords (keep only if wanted)')
    import clearance
    allt = strip(json.dumps(c, ensure_ascii=False))
    allowed = (plan.get('clearance') or {}).get('disclose') if plan else None
    named = clearance.mentions(allt)
    if named and not plan: warns.append('clearance mentioned; pass --plan so disclosure can be checked')
    elif named - ({allowed} if allowed else set()):
        fails.append(f"clearance over-disclosed: CV names {sorted(named)}, the JD allows only {allowed or 'none'} (use the plan's single clearance line)")
    if re.search(r'(clearance|vetting|cleared)[^.]{0,80}\b(19|20)\d{2}\b|\b(19|20)\d{2}\b[^.]{0,40}(clearance|vetting)', allt, re.I):
        fails.append('clearance dates on the CV: give the level only; details are offered in a secure setting')
    if re.search(r'(clearance|vetting)[^.]{0,80}(MOD|Ministry of Defence|UKSV|sponsor)', allt, re.I):
        fails.append('clearance sponsor/authority on the CV: give the level only')
    titles = {dm.g(r, 'role_title').lower() for r in roles}
    # Profile must not call the member a role noun they never held ("Hands-on architect with 15 years'")
    prof = ' '.join(strip(it.get('text', '')) for sec in c.get('sections', []) if sec.get('heading', '').lower().startswith(('profile', 'summary'))
                    for it in sec.get('items', []))
    for m in re.finditer(r"\b(architect|manager|director|head of \w+|scientist|analyst|consultant|lead)\b(?=[^.]{0,40}\b(with|years?)\b)", prof, re.I):
        noun = m.group(1).lower()
        if not any(noun in t for t in titles):
            warns.append(f'Profile: "{m.group(0)} ..." describes the member as a {noun}, a title never held (use a held title or "engineer")')
    # Voice: implied first person (UK convention) - no pronouns, no third-person verbs ("Uses", "Coaches")
    for m in re.finditer(r"\b(I|I'm|I've|my|me|myself|he|she|his|her|him)\b", prof, re.I):
        if m.group(1) != 'i':
            fails.append(f'Profile: pronoun "{m.group(1)}" - write in implied first person without pronouns ("Led ...", "Use ...")'); break
    def verb3(w):   # "Builds", "Uses"; not base forms ending in -ss/-us/-is ("Assess", "Process", "Focus", "Analysis")
        return bool(w) and re.fullmatch(r'[A-Za-z]{3,}s', w) is not None and not w.lower().endswith(('ss', 'us', 'is'))
    for s in re.split(r'(?<=[.;:])\s+', prof):
        if re.search(r'clearance', s, re.I): continue   # fixed wording added by arrange_roles.py
        s = re.sub(r'^(At|For|With|In|Since|Across)\b[^,]{0,80},\s*', '', s.strip())
        w = (re.match(r'[A-Za-z]+(?![-\'])', s) or [None])[0]
        if verb3(w):
            fails.append(f'Profile: "{w} ..." is third person - use the base form (present) or past tense, no pronoun')
    for sec in c.get('sections', []):
        for it in sec.get('items', []):
            for b in it.get('bullets', []) or []:
                w = (re.match(r'[A-Za-z]+(?![-\'])', strip(b)) or [None])[0]
                if verb3(w): fails.append(f'bullet starts "{w} ...": third person - use present ("Build") for the current role, past ("Built") otherwise')
    head1 = strip(c.get('headline', '')).split('|')[0].strip()
    if head1 and not any(head1.lower() == t or head1.lower() in t for t in titles):
        warns.append(f'headline "{head1}" is not a title held in the master (fine as a target title only if the member agrees)')
    # reviewer-lesson checks over every text the CV shows
    alltxt = []
    for sec in c.get('sections', []):
        if re.search(r'certif|education', sec.get('heading', ''), re.I): continue
        for it in sec.get('items', []):
            alltxt += [strip(it.get('text', '')), strip(it.get('para', ''))] + [strip(b) for b in it.get('bullets', []) or []]
    for t in alltxt:
        for m in BUZZ.finditer(t):
            warns.append(f'buzzword \"{m.group(0)}\": say what was built or measured instead (or drop it)')
        for m in ARCH.finditer(t):
            warns.append(f'architecture label \"{m.group(0)}\": keep only if THIS role names the mechanism (tool, pattern, data volume); otherwise describe the concrete component')
        if COINED.search(t):
            warns.append('internal acronym expanded in brackets adds no technical value: describe the pipeline in standard terms (ELT, ingestion, orchestration)')
    if not any(re.search(r'(?<![\w.])\d[\d,]*(?:\.\d+)?\s?(?:%|GB|TB|k|K|M)?(?![\w])', t) and not re.fullmatch(r'.*\b(19|20)\d{2}\b.*', t) for t in alltxt):
        warns.append('no number anywhere in the experience text: add every figure the master holds (run dump_roles.py: FACTS lines)')
    for w in dict.fromkeys(infos): print('INFO:', w)
    for w in dict.fromkeys(warns): print('WARN:', w)
    for f in dict.fromkeys(fails): print('FAIL:', f)
    print(('OK' if not fails else 'FAIL') + f': {len(set(fails))} fail(s), {len(set(warns))} warning(s)')
    return 1 if fails else 0

if __name__ == '__main__':
    sys.exit(main())
