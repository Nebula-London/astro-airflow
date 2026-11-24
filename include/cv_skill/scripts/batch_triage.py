#!/usr/bin/env python3
"""Batch triage: understand every JD, rank them against the master, drive one-at-a-time questions,
and re-score with the member's answers.

Usage:
  batch_triage.py MASTER.json JD_DIR [--answers answers.json] [--out triage.json]       # table (ranked)
  batch_triage.py MASTER.json JD_DIR --answers answers.json --next                       # next question
  batch_triage.py MASTER.json JD_DIR --answers answers.json --answer "Tableau" yes "Built exec dashboards" --roles 3,7
  batch_triage.py MASTER.json JD_DIR --answers answers.json --answer "Svelte" no
  batch_triage.py MASTER.json JD_DIR --answers answers.json --final [--cv-dir OUT]       # final table

Per JD it extracts: title, company/agency, rate, location/working pattern, duration, IR35, clearance
(required -> minimum disclosure via clearance.py), and JD terms (technologies/practices/domain) split into
essential vs desirable by section headings. Each term gets the master evidence tier from the digest logic:
Strong 1.0 | Partial 0.7 | Tech-list only 0.5 | Skills-list only 0.3 | None 0.
  Match %      = weighted coverage (essential x2) with current evidence (+ clearance met/unmet)
  Potential %  = match if every None/weak term were confirmed by the member (upper bound)
Ranking: Match desc, then Potential desc. Questions (--next) come from the highest-ranked JD first,
essential gaps before desirable, highest weight first; a term answered once is reused for every JD.
Answers are only evidence for the CV once confirmed (yes + roles); "no" answers are never claimed.
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
import digest_master as dm
import clearance

# common terms not always present in a member's vocabulary (detected in JDs so gaps can be asked about)
LEXICON = '''Python Java Scala Go Golang Rust C++ C# .NET JavaScript TypeScript Node.js React Angular Vue Svelte Next.js
SQL NoSQL PostgreSQL MySQL Oracle SQL Server MongoDB DynamoDB Cassandra Redis Elasticsearch OpenSearch Neo4j
Snowflake Databricks BigQuery Redshift Synapse Fabric dbt Airflow Dagster Prefect Spark PySpark Kafka Flink Kinesis
Pub/Sub RabbitMQ Iceberg Delta Lake Parquet AWS Azure GCP S3 ECS EKS Lambda Glue Athena EMR AppSync API Gateway
CloudFormation Terraform Pulumi Ansible Docker Kubernetes Helm ArgoCD Jenkins GitHub Actions GitLab CI/CD
REST GraphQL gRPC microservices serverless event-driven OOP object-oriented TDD BDD Agile Scrum Kanban SAFe
Data Mesh Data Products Data Contracts Data Modelling data governance data quality data lineage data catalogue
master data MDM GDPR least privilege RBAC IAM DevSecOps SAST DAST penetration testing SIEM Splunk ISO 27001
machine learning MLOps LLM GenAI RAG LangChain LangGraph MCP vector database prompt engineering
Tableau Power BI Looker Qlik Salesforce SAP ServiceNow Workday Dynamics
Domain-Driven Design DDD Team Topologies stakeholder management workshops architecture solution design
retail eCommerce banking capital markets trading front office commodities energy insurance healthcare NHS
public sector government defence telecoms payments fintech
PRINCE2 MSP PMP Agile delivery programme management project management portfolio management RAID budget management
risk management benefits realisation change management vendor management governance boards business case
TOGAF ArchiMate enterprise architecture integration MuleSoft Kong Apigee ITIL SRE observability Prometheus Grafana Datadog
PKI virtualisation VMware OpenStack KVM Proxmox Vault Okta Entra CISSP Zero Trust hardening NiFi Looker Cloud Run Vertex AI'''
# extra terms (one per line, '#' comments) the member can extend without editing code
_LEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references', 'jd-lexicon.txt')
EXTRA = [l.strip() for l in open(_LEX_FILE, encoding='utf-8')] if os.path.exists(_LEX_FILE) else []
EXTRA = [l for l in EXTRA if l and not l.startswith('#')]

MULTI = sorted({'SQL Server', 'Delta Lake', 'API Gateway', 'GitHub Actions', 'Data Mesh', 'Data Products', 'Data Contracts',
                'Data Modelling', 'data governance', 'data quality', 'data lineage', 'data catalogue', 'master data',
                'least privilege', 'penetration testing', 'ISO 27001', 'machine learning', 'vector database',
                'prompt engineering', 'Power BI', 'Domain-Driven Design', 'Team Topologies', 'stakeholder management',
                'solution design', 'capital markets', 'front office', 'public sector', 'Pub/Sub', 'event-driven',
                'programme management', 'project management', 'portfolio management', 'budget management', 'risk management',
                'benefits realisation', 'change management', 'vendor management', 'governance boards', 'business case',
                'enterprise architecture', 'Agile delivery', 'Zero Trust', 'Cloud Run', 'Vertex AI'}
               | {t for t in EXTRA if ' ' in t}, key=len, reverse=True)
SINGLE = {w for w in LEXICON.split() if w not in ('Server', 'Lake', 'Gateway', 'Actions', 'Mesh', 'Products', 'Contracts',
          'Modelling', 'governance', 'quality', 'lineage', 'catalogue', 'master', 'data', 'least', 'privilege', 'testing',
          'ISO', '27001', 'learning', 'vector', 'database', 'prompt', 'engineering', 'Power', 'BI', 'Domain-Driven', 'Design',
          'Team', 'Topologies', 'stakeholder', 'management', 'solution', 'design', 'capital', 'markets', 'front', 'office',
          'public', 'sector', 'machine', 'Data', 'architecture', 'Zero', 'Trust', 'Run', 'Vertex', 'AI')} | {t for t in EXTRA if ' ' not in t}
# words that only count inside a multi-word phrase, never alone
_PARTS = {w for t in MULTI if 0 < t.count(' ') <= 3 for w in t.split()}   # real phrases only (not lexicon lines)
SINGLE = {w for w in SINGLE if w not in _PARTS or w in {'SQL', 'Delta', 'Agile', 'architecture'}} - {'architecture', 'integration'}
NOISE = set('''UK GB EU US USA IR35 SC DV CTC BPSS NPPV AC CV HR IT PM UI UX OK FTE PAYE LTD PLC MOD NHS GDS BAU KPI KPIs
ASAP TBC N/A AM PM CEO CTO CIO CDO CFO VP IC JD TV PDF SME SMEs ROI SLA SLAs BYOD WFH HQ'''.split())
SHOUT = re.compile(r'^[A-Z]{5,}$')   # shouted English words (CLIENT, ESSENTIAL); real acronyms are rarely > 4 letters

def canonical(t):
    m = re.search(r'\(([^)]+)\)', t)          # "Google Cloud Platform (GCP)" -> "GCP"
    if m: t = m.group(1)
    for k, v in SYN.items():
        if t.lower() == k.lower() or t.lower() in [x.lower() for x in v.split('|')]: return k
    return t

def master_vocab(master, roles):
    v = set()
    for r in roles: v |= {t.strip() for t in dm.techs(r) if isinstance(t, str) and 2 <= len(t.strip()) <= 30}
    for sk in master.get('skills', []):
        n = sk.get('name') if isinstance(sk, dict) else sk
        if isinstance(n, str) and 2 <= len(n) <= 30: v.add(n.strip())
    generic = {'Integration', 'Development', 'Software Development', 'Cloud', 'Web', 'DB', 'OS', 'Documentation', 'Security',
               'Mobile', 'Networking', 'Sales', 'Data Pipelines', 'Data Engineering', 'Data', 'Architecture', 'Design', 'Support'}
    return {t for t in v if t not in generic}
SYN = {'Golang': 'Go', 'TOGAF': 'TOGAF', 'programme management': 'programme management|program management|programme manager',
       'project management': 'project management|project manager', 'risk management': 'risk management|RAID', 'Data Modelling': 'Data Model|data modeling|dimensional|medallion', 'OOP': 'object-oriented|OOP',
       'object-oriented': 'object-oriented|OOP', 'Data Products': 'Data Product|data as a product', 'eCommerce': 'ecommerce|e-commerce',
       'Domain-Driven Design': 'Domain-Driven Design|Domain-Driven|DDD', 'CI/CD': 'CI/CD|continuous integration',
       'GCP': 'GCP|Google Cloud', 'AWS': 'AWS|Amazon Web Services', 'Azure': 'Azure|Microsoft Azure', 'NoSQL': 'NoSQL|MongoDB|DynamoDB|Cassandra',
       'PostgreSQL': 'PostgreSQL|Postgres', 'workshops': 'workshop', 'microservices': 'microservice', 'serverless': 'serverless|Lambda',
       'stakeholder management': 'stakeholder', 'front office': 'front office|trading desk', 'MLOps': 'MLOps|ML-Ops'}
TIER = {'Strong': 1.0, 'Partial': 0.7, 'Tech-list only': 0.5, 'Skills-list only': 0.3, 'None': 0.0}
DES_HEAD = re.compile(r'^\s*(desirable|nice to have|preferred|bonus|advantageous|good to have|plus)\b', re.I | re.M)
ESS_HEAD = re.compile(r'^\s*(essential|required|requirements|must have|key skills|skills|experience|responsibilities|technical environment)\b', re.I | re.M)

def meta(text, fname):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = lines[0][:70] if lines else fname
    g = lambda p: (re.search(p, text, re.I) or [None])[0]
    rate = g(r'£\s?\d[\d,]*(?:\s?-\s?£?\s?\d[\d,]*)?\s*(?:per day|/day|p/d|pd|a day|per annum|p\.a\.|k)?')
    dur = g(r'\b\d{1,2}[- ]?(?:month|mth)s?\b')
    ir35 = g(r'(inside|outside)\s+IR35')
    loc = g(r'\b(remote|hybrid[^.\n]{0,40}|on-?site[^.\n]{0,30}|london|manchester|leeds|birmingham|bristol|edinburgh|glasgow)\b')
    agency = g(r'(?<=posted by:)\s*[^\n]+') or g(r'(?<=posted by)\s*[^\n|]+') or g(r'(?<=employment business)\s*[^\n]+')
    url_m = re.search(r'(?:permalink|job\s*link|url)\s*:\s*(https?://\S+)', text, re.I)
    return {'title': title, 'rate': (rate or '').strip(), 'duration': dur or '', 'ir35': ir35 or '',
            'location': (loc or '').strip()[:40], 'agency': (agency or '').strip()[:60],
            'url': url_m.group(1) if url_m else ''}

BOILER = re.compile(r'^\s*(posted by|posted:|posted date|apply\b|employment business|contact|email|reference|permalink|url:|'
                    r'start date|duration|rate|industry|category|location|function:|department:|team:)', re.I)

def jd_body(text, agency=''):
    """JD text with job-board boilerplate blanked (same length, so section positions still line up) and the
    agency / team names removed, so recruiter or function names are never asked about as skills."""
    lines = text.splitlines(keepends=True)
    boiler = lambda l: BOILER.match(l) and (len(l.strip()) <= 70 or re.match(r'^\s*(permalink|url)\s*:', l, re.I))     # short label lines + URLs only
    names = {l.strip() for l in lines if boiler(l)}
    out = [(' ' * (len(l) - 1) + '\n') if (boiler(l) or (agency and l.strip() and l.strip() in agency + ' ' + ' '.join(names)))
           else l for l in lines]
    body = ''.join(out)
    for w in set(re.findall(r'[A-Za-z0-9]+', agency)):
        if len(w) <= 6: body = re.sub(r'(?<![A-Za-z0-9])' + re.escape(w) + r'(?![A-Za-z0-9])', ' ' * len(w), body)
    return body

def context(text, term):
    """First JD line mentioning the term (shown with the question so the agent need not grep)."""
    for l in text.splitlines():
        if re.search(r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])', l, re.I): return l.strip()[:160]
    return ''

def jd_terms(text, vocab=()):
    """[(term, essential)] found in the JD; essential unless it only appears under a desirable heading."""
    des_start = [m.start() for m in DES_HEAD.finditer(text)]
    ess_start = [m.start() for m in ESS_HEAD.finditer(text)]
    def section_is_desirable(pos):
        d = max([s for s in des_start if s <= pos] or [-1]); e = max([s for s in ess_start if s <= pos] or [-1])
        return d > e
    low = text.lower(); found = {}
    taken = []
    for t in MULTI:
        for m in re.finditer(r'(?<![a-z0-9])' + re.escape(t.lower()) + r'(?![a-z0-9])', low):
            if any(a <= m.start() < b for a, b in taken): continue
            taken.append((m.start(), m.end()))
            found.setdefault(t, []).append(not section_is_desirable(m.start()))
    for t in sorted(vocab, key=len, reverse=True):  # member's own terms present in the JD
        if t in found or t in NOISE: continue
        for m in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(t) + r'(?![A-Za-z0-9])', text, re.I if len(t) > 4 else 0):
            if any(a <= m.start() < b for a, b in taken): continue
            taken.append((m.start(), m.end())); found.setdefault(t, []).append(not section_is_desirable(m.start()))
    # unknown product / acronym tokens repeated in the JD (e.g. TOGAF, MuleSoft): candidates to ask about
    cand = {}
    for m in re.finditer(r'\b([A-Z][A-Za-z]*[A-Z][A-Za-z0-9]*|[A-Z]{3,8}\d?)\b', text):
        w = m.group(1)
        if w in NOISE or SHOUT.match(w) or any(a <= m.start() < b for a, b in taken) or w.lower() in {f.lower() for f in found}: continue
        cand.setdefault(w, []).append(m.start())
    for w, pos in cand.items():
        if len(pos) >= 2: found[w] = [not section_is_desirable(p) for p in pos]
    for m in re.finditer(r'[A-Za-z][A-Za-z0-9+#./-]*', text):
        w = m.group(0).rstrip('.')
        if w in SINGLE and w not in found and not any(a <= m.start() < b for a, b in taken):
            found.setdefault(w, []).append(not section_is_desirable(m.start()))
    merged = {}
    for t, v in found.items():                  # one entry per concept (GCP / Google Cloud Platform (GCP); DDD / Domain-Driven Design)
        c = canonical(t); merged.setdefault(c, []).extend(v)
    return [(t, any(v)) for t, v in merged.items()]

def find_answer(answers, term):
    t = term.lower()
    if t in answers: return answers[t]
    for a in answers.values():
        if t in [x.lower() for x in a.get('aliases', [])]: return a
    return None

def evidence(master, roles, term, answers):
    a = find_answer(answers, term)
    alts = SYN.get(term, term).split('|')
    hits = [i for i, r in enumerate(roles) if any(dm.has(x, dm.text_of(r)) for x in alts)]
    inb = [i for i in hits if any(dm.has(x, ' '.join(dm.bullets(roles[i])).lower()) for x in alts)]
    today = dm.dt.date.today(); recent = dm.dt.date(today.year - 5, today.month, 1)
    strong = [i for i in inb if dm.day(dm.g(roles[i], 'end_date'), today) >= recent]
    skill = any(any(dm.has(x, json.dumps(s).lower()) for x in alts) for s in master.get('skills', []))
    tier = 'Strong' if strong else 'Partial' if inb else 'Tech-list only' if hits else 'Skills-list only' if skill else 'None'
    if a and a.get('has') and TIER[tier] < TIER['Partial']: tier = 'Confirmed'
    # "no" = never write a bullet; a tech-list mention still counts for Core Capabilities
    if a and a.get('has') is False: tier = 'Confirmed none' if tier in ('None', 'Skills-list only') else tier
    return tier, (inb or hits)[:6]

def score_jd(master, roles, text, answers, vocab=(), agency=''):
    terms = jd_terms(jd_body(text, agency), vocab)
    rows, num, den, pot = [], 0.0, 0.0, 0.0
    for t, ess in terms:
        tier, refs = evidence(master, roles, t, answers)
        w = 2.0 if ess else 1.0
        val = {'Confirmed': 0.7, 'Confirmed none': 0.0}.get(tier, TIER.get(tier, 0))
        num += w * val; den += w
        pot += w * (val if tier in ('Strong', 'Partial', 'Confirmed', 'Confirmed none') else 0.7)
        rows.append({'term': t, 'essential': ess, 'tier': tier, 'roles': refs, 'weight': w})
    c = clearance.decide(master, text)
    if c['required']:
        den += 3; met = 3 if c['disclose'] else 0; num += met; pot += met if c['disclose'] else 0
    match = round(100 * num / den) if den else 0
    potential = round(100 * pot / den) if den else 0
    gaps = [r for r in rows if r['tier'] in ('None', 'Skills-list only', 'Tech-list only')]
    gaps.sort(key=lambda r: (-r['weight'], r['term'].lower()))
    unclaimed = [r['term'] for r in rows if r['essential'] and r['tier'] in ('None', 'Skills-list only', 'Tech-list only', 'Confirmed none')]
    return {'match': match, 'potential': potential, 'terms': rows, 'gaps': gaps, 'unclaimed': unclaimed,
            'clearance': {'required': c['required'], 'disclose': c['disclose'], 'notes': c['notes']}}

def load_answers(path):
    if path and os.path.exists(path): return json.load(open(path, encoding='utf-8'))
    return {}

def table(results, final=False, cvdir=None, ws=None):
    hdr = '| # | JD | Title | Rate | Duration | IR35 | Location | Clearance | Match % | Potential % | Open questions |'
    if final: hdr = '| # | JD | Title | Match % (before -> after) | Clearance on CV | Unclaimed essentials | Job Link | Local CV | Drive CV |'
    out = [hdr, '|' + '---|' * (hdr.count('|') - 1)]
    tr_urls = {}
    if final:
        try:
            import tracker
            t_path = (ws or {}).get('tracker') or tracker.configured()
            if t_path:
                for tr_r in tracker.load(t_path)[1]:
                    if tr_r.get('code') and tr_r.get('url'): tr_urls[tr_r['code']] = tr_r['url']
                    jf = tr_r.get('jd_file', '')
                    if jf and tr_r.get('url'):
                        tr_urls.setdefault(os.path.splitext(os.path.basename(tracker.jd_local(jf) or jf))[0], tr_r['url'])
        except Exception:
            tr_urls = {}
    for n, r in enumerate(results, 1):
        m, s = r['meta'], r['score']
        clr = s['clearance']['disclose'] or ('needed: ' + str(s['clearance']['required']) if s['clearance']['required'] else '-')
        if final:
            unc = ', '.join(s.get('unclaimed', []))[:80] or '-'
            slug = os.path.splitext(r['file'])[0]
            rep, local_p, drive_col = {}, '', '-'
            if cvdir:
                d = os.path.join(cvdir, slug)
                rp = os.path.join(d, 'report.json')
                if os.path.exists(rp): rep = json.load(open(rp, encoding='utf-8'))
                lp = rep.get('local_pdf') or (rep.get('pdf') if not str(rep.get('pdf', '')).startswith('https://') else '')
                if lp and os.path.exists(lp): local_p = lp
                elif os.path.isdir(d):
                    pdfs = sorted(f for f in os.listdir(d) if f.endswith('.pdf'))
                    if pdfs: local_p = os.path.join(d, pdfs[0])
                if str(rep.get('pdf', '')).startswith('https://'):
                    dname = (rep.get('drive') or {}).get('name') or 'Drive'
                    drive_col = f"[{dname}]({rep['pdf']})"
            code = ((ws or {}).get('codes') or {}).get(slug, '')
            jurl = rep.get('job_url') or ((ws or {}).get('urls') or {}).get(slug) or m.get('url') or tr_urls.get(code) or tr_urls.get(slug, '')
            job_col = f'[Job]({jurl})' if jurl else '-'
            local_col = f'[{os.path.basename(local_p)}](file://{os.path.abspath(local_p)})' if local_p else 'not built'
            out.append(f"| {n} | {r['file']} | {m['title'][:45]} | {r.get('match_before', s['match'])} -> {s['match']} | {s['clearance']['disclose'] or '-'} | {unc} | {job_col} | {local_col} | {drive_col} |")
        else:
            out.append(f"| {n} | {r['file']} | {m['title'][:45]} | {m['rate'][:22]} | {m['duration']} | {m['ir35']} | {m['location'][:20]} | {clr} | {s['match']} | {s['potential']} | {len(s['gaps'])} |")
    return '\n'.join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('master'); ap.add_argument('jd_dir')
    ap.add_argument('--answers'); ap.add_argument('--out')
    ap.add_argument('--next', action='store_true'); ap.add_argument('--final', action='store_true'); ap.add_argument('--cv-dir')
    ap.add_argument('--workspace', help='optional workspace.json to resolve job permalinks and tracker URLs')
    ap.add_argument('--answer', nargs='+', metavar=('TERM', 'yes|no'))
    ap.add_argument('--roles', nargs='*', default=[], help='role refs: "0,1,2" or "0 1 2"')
    ap.add_argument('--also', default='', help='comma list of aliases answered by the same fact (e.g. "Elasticsearch")')
    ap.add_argument('--replace', action='store_true', help='overwrite an earlier answer instead of merging')
    ap.add_argument('--count', type=int, default=1, help='with --next: up to N questions (ask them in one message)')
    a = ap.parse_args()
    master = json.load(open(a.master, encoding='utf-8')); roles = dm.load_roles(master)
    vocab = master_vocab(master, roles)
    answers = load_answers(a.answers)

    if a.answer:
        term, yn = a.answer[0], a.answer[1].lower() if len(a.answer) > 1 else 'yes'
        if not a.answers: print('FAIL: --answers path required'); return 1
        refs = [int(x) for tok in a.roles for x in tok.split(',') if x.strip()]
        bad = [i for i in refs if not 0 <= i < len(roles)]
        if bad: print(f'FAIL: role refs out of range: {bad}'); return 1
        detail, has = ' '.join(a.answer[2:]), yn.startswith('y')
        new = {'term': term, 'has': has, 'detail': detail, 'roles': refs,
               'detail_by_role': {str(i): detail for i in refs}, 'aliases': [x.strip() for x in a.also.split(',') if x.strip()]}
        old = answers.get(term.lower())
        if old and not a.replace and old.get('has') and has:   # merge: a second answer adds roles, never erases
            old.setdefault('detail_by_role', {str(i): old.get('detail', '') for i in old.get('roles', [])})
            old['detail_by_role'].update(new['detail_by_role'])
            old['roles'] = sorted(set(old.get('roles', [])) | set(refs))
            old['detail'] = '; '.join(dict.fromkeys(x for x in (old.get('detail', ''), detail) if x))
            old['aliases'] = sorted(set(old.get('aliases', [])) | set(new['aliases']))
            new = old
        answers[term.lower()] = new
        # warn when a credited role ended before the tool existed (e.g. "GitHub Actions for 10+ years")
        ry = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references', 'tool-release-years.json')
        rel = json.load(open(ry, encoding='utf-8')) if os.path.exists(ry) else {}
        yr = next((v for k, v in rel.items() if k.lower() in [term.lower()] + [x.lower() for x in new['aliases']]), None)
        if has and yr:
            early = [i for i in refs if dm.day(dm.g(roles[i], 'end_date'), dm.dt.date.today()).year < yr]
            if early: print(f'WARN: {term} was first released in {yr}; roles {early} ended before then: drop them or confirm')
        # warn when a named employer has several stints and only some were chosen
        for i in refs:
            c = dm.g(roles[i], 'client') or dm.g(roles[i], 'organisation')
            sib = [j for j, r in enumerate(roles) if j not in refs and (dm.g(r, 'client') or dm.g(r, 'organisation')) == c]
            if sib: print(f'NOTE: {c} has other stints {sib} not included: confirm scope')
        json.dump(answers, open(a.answers, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        print(f"recorded: {term} = {yn} roles {answers[term.lower()]['roles']}"); a.next = True

    files = sorted(f for f in os.listdir(a.jd_dir) if f.lower().endswith('.txt'))
    prev = json.load(open(a.out)) if a.out and os.path.exists(a.out) else {}
    results = []
    for f in files:
        text = open(os.path.join(a.jd_dir, f), encoding='utf-8', errors='replace').read()
        m = meta(text, f)
        r = {'file': f, 'meta': m, 'text': text, 'score': score_jd(master, roles, text, answers, vocab, m['agency'])}
        before = (prev.get('jds', {}).get(f) or {}).get('match_before')
        r['match_before'] = before if before is not None else r['score']['match']
        results.append(r)
    results.sort(key=lambda r: (-r['score']['match'], -r['score']['potential'], r['file']))

    if a.out:
        json.dump({'ranking': [r['file'] for r in results],
                   'jds': {r['file']: {'meta': r['meta'], 'match_before': r['match_before'], **r['score']} for r in results}},
                  open(a.out, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)

    if a.next:
        asked = {k for k, v in answers.items()} | {x.lower() for v in answers.values() for x in v.get('aliases', [])}
        open_terms = {g['term'].lower() for x in results for g in x['score']['gaps']} - asked
        shown = 0
        for r in results:
            for gp in r['score']['gaps']:
                if gp['term'].lower() in asked or shown >= a.count: continue
                asked.add(gp['term'].lower()); shown += 1
                others = [x['file'] for x in results if any(g['term'].lower() == gp['term'].lower() for g in x['score']['gaps'])]
                hint = f" (closest evidence: roles {gp['roles']})" if gp['roles'] else ''
                print(f"NEXT QUESTION [{r['file']} - {r['meta']['title'][:50]}; match {r['score']['match']}%]")
                print(f"  {'Essential' if gp['essential'] else 'Desirable'}: {gp['term']} - current evidence: {gp['tier']}{hint}")
                print(f"  JD: \"{context(r['text'], gp['term'])}\"")
                print(f"  Also needed by: {', '.join(others)}")
            if shown >= a.count: break
        if shown:
            print('  Record: batch_triage.py ... --answer "<term>" yes|no "<what you did>" --roles <refs> [--also <aliases>]')
            print(f'  Remaining questions after these: ~{len(open_terms) - shown}')
            return 0
        for r in results:
            for n in r['score']['clearance']['notes']:
                if 'nothing disclosed' not in n: print(f"CLEARANCE NOTE [{r['file']}]: {n}")
        print('NO MORE QUESTIONS: all gaps answered. Proceed to build the CVs.')
        return 0

    ws = None
    wsp = a.workspace or (os.path.join(a.cv_dir, 'workspace.json') if a.cv_dir else '')
    if wsp and os.path.exists(wsp): ws = json.load(open(wsp, encoding='utf-8'))
    print(table(results, final=a.final, cvdir=a.cv_dir, ws=ws))
    return 0

if __name__ == '__main__':
    sys.exit(main())
