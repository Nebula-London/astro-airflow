#!/usr/bin/env python3
"""Deterministic (regex) fields from a JD text: IR35 status, day-rate range, agent details. Shared by jobserve-new-jobs
and targeted-cv-from-master (each skill ships an identical copy). Standard library only; no LLM involved.

  ir35(text, title='', rate='')  -> (status, confidence, rule)   status: '' | Inside | Outside | Permanent
  rates(rate_text, permanent, text) -> (min, max) per DAY (contract) or per YEAR (permanent); 'Up to 700' -> ('', '700')
                                    hourly x hours/day (JD, else 7.5); weekly / 5; monthly x 12 / 220; annual / 220
  agent(text, notes='')          -> 'Name <email> phone' (parts that are known)
  split_client(value)            -> (consultancy, client)   'Acme Consulting > Home Office' -> ('Acme Consulting', 'Home Office')
  submitted_rate(notes)          -> '680' when the notes state the rate sent ('Rate: 680 per day'), else ''

IR35 rules, in order (learnt from the stored JobServe JDs):
  1. Agency boilerplate is removed first: "where the role is marked as outside IR35 ... subject to", "employment agency
     for permanent recruitment", "processed outside the UK", "in-scope objects". The "IR35: inside|outside" line that
     find_jobs.py writes is derived from the same text, so it is ignored and the status re-derived.
  2. Explicit wording, strongest place first: title, then the rate, then the body. Inside: "inside (of) IR35",
     "IR35 status: inside", "in scope (of IR35)", "deemed employment", "+ Inside" in the rate. Outside: the same with
     outside / out of scope, or "Outside" / "Out IR35" in the title. The first place that says only one of them
     decides; both in every place -> '' (rule "conflict").
  3. Implicit Inside (no explicit Outside anywhere): umbrella, PAYE, FCSA, "tax and NI deducted at source".
     "Ltd company / umbrella" (either route) is not a signal.
  4. Permanent: JobServe's own "Type: Permanent" line (not "Contract/Permanent"), or "permanent" in the title with no
     contract wording. "Type: Contract" is never Permanent (agency templates say "Contract Type: Permanent" by mistake).

  jd_fields.py --selftest
  jd_fields.py --report DIR|FILE...   # status counts per rule over JD .txt files, plus every blank-with-a-hint case
"""
import glob, os, re, sys

IR = r'ir\s*-?\s*35'
_F = re.I

BOILER = re.compile('|'.join([
    r'where (?:the|a) (?:role|position|contract) is (?:marked|advertised|stated) as outside (?:of )?ir[\s-]?35[^.]*\.?',
    r'(?:employment )?agency (?:for|in relation to) permanent (?:recruitment|vacancies|positions)[^.]*\.?',
    r'(?:transferred|processed|stored)[^.]{0,40}\boutside (?:of )?the (?:uk|u\.k\.|us|eea|european economic area)[^.]*\.?',
    r'\boutside (?:of )?the (?:uk|u\.k\.|us|usa|eea|eu|european economic area|european union)\b',
    r'\bin[\s-]scope (?:objects?|items?|systems?|applications?|data|services?|work|activities)\b',
    r'\bpermanent (?:conversion|opportunit(?:y|ies)|engineering teams?|teams?|staff|employees?|members?)\b',
    r'\b(?:contract|interim|temporary)(?:,|\s+and|\s+or)\s+(?:fixed[\s-]term\s+(?:and|or)\s+)?permanent\b',
    r'\bpermanent(?:,|\s+and|\s+or)\s+(?:fixed[\s-]term\s+(?:and|or)\s+)?(?:contract|interim|temporary)\b',
]), _F)
DERIVED = re.compile(r'^IR35: (?:inside|outside)$', re.M)          # written by find_jobs.py (lower case): re-derived

INSIDE = re.compile('|'.join([
    rf'\binside\s+(?:of\s+)?(?:the\s+)?{IR}',
    rf'\b{IR}\s*(?:status|determination|position)?\s*(?:is\s*)?[:=\-\u2013(|]?\s*(?:is\s+)?(?:inside|in[\s-]scope|caught)\b',
    rf'\b(?:in|within)[\s-]scope\s+(?:of\s+|for\s+)?(?:the\s+)?{IR}',
    rf'\bcaught\s+by\s+{IR}', r'\bdeemed\s+employment\b',
    r'\b(?:status\s+)?determination\s*[:=\-]\s*inside\b',
]), _F)
OUTSIDE = re.compile('|'.join([
    rf'\boutside[\s-]+(?:of\s+)?(?:the\s+)?{IR}',
    rf'\b{IR}\s*(?:status|determination|position)?\s*(?:is\s*)?[:=\-\u2013(|]?\s*(?:is\s+)?(?:outside|out[\s-]of[\s-]scope)\b',
    rf'\bout(?:side)?[\s-]of[\s-]scope\s+(?:of\s+|for\s+)?(?:the\s+)?{IR}',
    rf'\bout\s+{IR}', r'\b(?:status\s+)?determination\s*[:=\-]\s*outside\b',
]), _F)
RATE_IN = re.compile(r'\+\s*inside\b|\(\s*inside\s*\)|\binside\s*$', _F)    # JobServe rate field: "+ Inside", "(Inside)"
RATE_OUT = re.compile(r'\+\s*outside\b|\(\s*outside\s*\)|\boutside\s*$', _F)
TITLE_OUT = re.compile(r'(?:^|[\s(\[|/,-])outside(?:$|[\s)\]|/,-])', _F)    # "AI Engineer - Outside IR35", "| Outside |"
TITLE_IN = re.compile(r'(?:^|[\s(\[|/,-])inside(?:$|[\s)\]|/,-])', _F)
EITHER = re.compile(r'\b(?:ltd|limited|psc)(?:\s+company)?\s*(?:/|or|and|&)\s*umbrella\b|'
                    r'\bumbrella\s*(?:/|or|and|&)\s*(?:ltd|limited|psc)\b', _F)
IMPLICIT = re.compile(r'\bumbrella\b|\bpaye\b|\bfcsa\b|\btax and ni\b[^.]{0,30}\bdeducted at source', _F)
PERM_TYPE = re.compile(r'^Type:\s*Permanent\s*(?:\||$)', re.M | re.I)    # JobServe's Type field only
CONTRACT_TYPE = re.compile(r'^Type:\s*Contract\s*(?:\||$)', re.M | re.I)   # JobServe field; not Contract/Permanent
PERM_TITLE = re.compile(r'\bperm(?:anent)?\b', _F)
_AMT = r'(?:£\s?\d[\d,.]*k?|\b\d{3,4}(?:\.\d\d)?)\s*(?:pd\b|p/?d\b|per\s+day|/\s*day|a\s+day|daily|day\s+rate)?'
SEG = r'(?:{a}|\bper\s+day|\bpd|\bp/d|\bcontract|\brate)\s*[-\u2013|,(/+]?\s*{w}\b|\b{w}\s*[-\u2013|,)/]\s*(?:{a}|contract)'
SEG_IN = re.compile(SEG.format(a=_AMT, w='inside'), _F)     # "£650 pd Inside", "6 Month Contract - Inside", "INSIDE - £500"
SEG_OUT = re.compile(SEG.format(a=_AMT, w='outside'), _F)
CONTRACT_WORD = re.compile(r'\b(?:contract(?:or)?|day rate|per day|p/?d\b|daily rate|ir[\s-]?35|umbrella|interim)\b', _F)


def _ws(s): return re.sub(r'\s+', ' ', s).strip()
def _clean(s): return BOILER.sub(' ', DERIVED.sub('', s or ''))

def _parts(text, title, rate):
    """(title, rate, body) of a JD text as find_jobs.py lays it out (line 1 title, line 2 'location - rate',
    a 'Rate:' meta line); title/rate may also be given directly (tracker rows without a JD)."""
    lines = (text or '').split('\n')
    t = title or (lines[0] if lines else '')
    r = [rate] if rate else []
    if len(lines) > 1 and not rate: r.append(lines[1])
    r += re.findall(r'^(?:Rate|Day Rate|Daily Rate|Pay Rate|Salary/Rate|Rate/Salary):\s*(.*)$', text or '', re.M | re.I)
    return t, ' | '.join(r), text or ''

def ir35(text, title='', rate=''):
    """IR35 status of a role: (status, confidence 'high'|'medium'|'', rule). See the module doc for the rules."""
    t, r, body = (_clean(x) for x in _parts(text, title, rate))
    places = [('title', INSIDE.search(t) or SEG_IN.search(t) or TITLE_IN.search(t) and re.search(IR, t, _F),
               OUTSIDE.search(t) or TITLE_OUT.search(t)),
              ('rate', INSIDE.search(r) or RATE_IN.search(r) or SEG_IN.search(r),
               OUTSIDE.search(r) or RATE_OUT.search(r) or SEG_OUT.search(r)),
              ('body', INSIDE.search(body) or SEG_IN.search(body), OUTSIDE.search(body) or SEG_OUT.search(body))]
    conflict = False
    for where, i, o in places:
        if i and not o: return 'Inside', 'high', f'{where}: {_ws(i.group(0))}'
        if o and not i: return 'Outside', 'high', f'{where}: {_ws(o.group(0))}'
        if i and o: conflict = True
    if conflict: return '', '', 'conflict: inside and outside both stated'
    whole = EITHER.sub(' ', ' | '.join((t, r, body)))
    m = IMPLICIT.search(whole)
    if m: return 'Inside', 'medium', f'implicit: {_ws(m.group(0))}'
    if CONTRACT_TYPE.search(text or ''): return '', '', 'no IR35 wording'      # JobServe's own Type field wins
    if PERM_TYPE.search(text or '') or (PERM_TITLE.search(t) and not CONTRACT_WORD.search(t + ' ' + r)):
        return 'Permanent', 'high', 'permanent role'
    return '', '', 'no IR35 wording'

# ---------------------------------------------------------------- rates
HOURS_PER_DAY = 7.5      # UK contract convention when the JD does not say (config "hours_per_day" overrides)
DAYS_PER_YEAR = 220      # working days in a year, to turn a salary into a day rate or back (config "days_per_year")
NUM = re.compile(r'(?P<cur>£|gbp\s*|eur\s*|€|usd\s*|\$)?\s*(?P<n>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<k>k\b)?', _F)
AFTER_BAD = re.compile(r'\s*(?:-\s*)?(?:months?|mths?|weeks?|wks?|years?|yrs?|hours?\s+(?:a|per|each|/)\s*(?:week|day)|%|'
                       r'days?\s+(?:a|per|p/?)\s*(?:week|w)|x\b|gb\b|tb\b)', _F)
UNIT = [('h', re.compile(r'per\s*hour|p/?h\b|/\s*h(?:ou)?r\b|\bhourly\b|\bph\b|\bper\s*hr\b', _F)),
        ('w', re.compile(r'per\s*week|p/?w\b(?!\s*(?:in|on|office))|/\s*week\b|\bweekly\s+rate', _F)),
        ('m', re.compile(r'per\s*(?:calendar\s+)?month|/\s*month\b|\bp\.?c\.?m\.?\b|\bmonthly\s+(?:rate|salary)', _F)),
        ('d', re.compile(r'per\s*day|/\s*day\b|p/?d\b|\bpd\b|\bdaily\b|day\s*rate|a\s+day\b', _F)),
        ('a', re.compile(r'per\s*annum|\bp\.?a\.?(?=\W|$)|/\s*(?:year|yr|annum)\b|\bannual\b|\bsalary\b|\bper\s*year\b', _F))]
CUR = {'£': '', 'gbp': '', 'eur': 'EUR ', '€': 'EUR ', 'usd': 'USD ', '$': 'USD '}
HOURS = re.compile(r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per|a|each|/)\s*(day|week)\b', _F)
CAP = re.compile(r'\b(?:up\s*to|upto|max(?:imum)?|capped\s+at|no\s+more\s+than|not\s+exceeding)\s*(?:of\s+)?$', _F)
FLOOR = re.compile(r'\b(?:from|min(?:imum)?|starting\s+(?:at|from)|at\s+least)\s*(?:of\s+)?$', _F)

def _fmt(v): return str(int(v)) if v == int(v) else f'{v:.2f}'.rstrip('0').rstrip('.')

def hours_per_day(text, default=None):
    """Hours in a working day as the JD states it ('8 hours per day', '40 hours a week' -> 8), else the default."""
    for m in HOURS.finditer(text or ''):
        n = float(m.group(1))
        h = n if m.group(2).lower() == 'day' else n / 5
        if 4 <= h <= 12: return h
    return default or HOURS_PER_DAY

def _unit(seg):
    """The pay period a stretch of rate text names: h(our), w(eek), m(onth), d(ay), a(nnum); '' when it names none."""
    hit = [(m.start(), u) for u, rx in UNIT for m in [rx.search(seg)] if m]
    return min(hit)[1] if hit else ''

def _unit_before(seg):
    """The pay period named last before a number ('Annual Salary: Circa £75,000' -> 'a')."""
    seg = re.split(r'[|;]', seg)[-1]          # only this part of a 'a | b' rate line
    hit = [(m.start(), u) for u, rx in UNIT for m in rx.finditer(seg)]
    return max(hit)[1] if hit else ''

def rates(s, permanent=False, text='', hours=None, days=None):
    """(min, max) from free rate text, normalised: a DAY rate for a contract, an ANNUAL salary for a permanent role.
    Hourly x hours per day (stated in the JD `text`, else 7.5), weekly / 5, monthly x 12 / days per year (220),
    annual / 220 for a contract (and a day rate x 220 for a permanent role). Numbers are whole pounds (2 dp kept for an
    exact figure). A currency other than GBP is kept as a prefix ('EUR 863'). 'Up to X' -> ('', X); '' = unknown."""
    s = re.sub(IR, ' ', s or '', flags=_F)
    s = re.sub(r'£\s*market\s+rate|\b(?:24/7|1st|2nd|3rd|\d+(?:st|nd|rd|th))\b', ' ', s, flags=_F)
    hpd, dpy = hours or hours_per_day(text), days or DAYS_PER_YEAR
    seg_unit = _unit(s)
    vals, prev_end = [], 0
    for m in NUM.finditer(s):
        n = float(m.group('n').replace(',', ''))
        if m.group('k'): n *= 1000
        bad = AFTER_BAD.match(s, m.end())
        if bad: prev_end = bad.end(); continue
        if not m.group('cur') and not m.group('k') and 1900 <= n <= 2100 and n == int(n): continue   # a year
        # the unit right after this number (to the next number), else the first one named anywhere, else by size
        nxt = NUM.search(s, m.end())
        u = _unit(s[m.end():nxt.start() if nxt else len(s)]) or _unit_before(s[prev_end:m.start()]) or seg_unit
        prev_end = m.end()
        if not u: u = 'h' if 10 <= n < 100 else 'd' if 100 <= n <= 5000 else 'a' if 15000 <= n <= 500000 else ''
        ok = {'h': 10 <= n <= 300, 'w': 500 <= n <= 25000, 'm': 1500 <= n <= 60000, 'd': 100 <= n <= 5000,
              'a': 15000 <= n <= 500000}.get(u, False)
        if not ok: continue
        day = {'h': n * hpd, 'w': n / 5, 'm': n * 12 / dpy, 'd': n, 'a': n / dpy}[u]
        cur = CUR.get(re.sub(r'\s+', '', (m.group('cur') or '£')).lower(), '')
        vals.append((day * dpy if permanent else day, u, cur, s[max(0, m.start() - 20):m.start()]))
    if not vals: return '', ''
    kinds = {v[1] for v in vals}
    if 'd' in kinds and 'a' in kinds:        # "£682 per day or Annual Salary: £75,000": keep the figure that fits the role
        keep = 'a' if permanent else 'd'
        vals = [v for v in vals if v[1] == keep] or vals
    cur = vals[0][2]
    conv = lambda v, exact: cur + (_fmt(round(v, 2)) if exact else str(int(v + 0.5)))   # noqa: E731
    exact = all(v[1] == ('a' if permanent else 'd') for v in vals)
    if len(vals) == 1:
        n, _, _, before = vals[0]
        if CAP.search(before): return '', conv(n, exact)
        if FLOOR.search(before): return conv(n, exact), ''
        return conv(n, exact), conv(n, exact)
    nums = [v[0] for v in vals]
    return conv(min(nums), exact), conv(max(nums), exact)

# ---------------------------------------------------------------- agent
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
PHONE = re.compile(r'(?:\+44\s?\(?0?\)?\s?|\b0)(?:\d[\s-]?){8,9}\d\b')
GENERIC = re.compile(r'^(?:info|jobs?|careers?|recruit\w*|hr|privacy|dpo|data\w*|gdpr|noreply|no-reply|admin|hello|'
                     r'enquir\w*|contact|apply|applications?|cv|cvs|resourcing|talent|compliance|support|team)\b', _F)
NAME = r"[A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3}"
NOTE_AGENT = re.compile(rf'(?:\bagent\b\s*[:=\-]?\s*)?(?P<name>{NAME})\s*(?P<phone>(?:\+44|0)[\d\s]{{9,14}}\d)?\s*<(?P<email>[^<>\s]+@[^<>\s]+)>')

def _norm_phone(p): return re.sub(r'\s+', ' ', p.strip())

def agent(text, notes=''):
    """'Name <email> phone' for the recruiter: name from the JD 'Contact:' line, email/phone from the member's notes
    ('Agent: Name <email>', '+44 ...') or from the JD (an email that matches the name; a phone next to the name)."""
    m = re.search(r'^Contact:\s*(.+?)\s*$', text or '', re.M)
    name = m.group(1) if m else ''
    email = phone = ''
    n = NOTE_AGENT.search(notes or '')
    if n:   # the member's own record of the agent wins over the JD's Contact line
        name, email, phone = n.group('name'), n.group('email'), _norm_phone(n.group('phone') or '')
    if not phone and notes:
        p = PHONE.search(notes)
        if p: phone = _norm_phone(p.group(0))
    if not email and text:
        toks = [w.lower() for w in re.findall(r'[A-Za-z]{3,}', name)]
        for e in EMAIL.findall(text):
            loc = e.split('@')[0].lower()
            if not GENERIC.match(loc) and (not toks or any(w in loc for w in toks)): email = e; break
    if not phone and text and name:
        i = text.find(name)
        if i >= 0:
            p = PHONE.search(text[i:i + 200])
            if p: phone = _norm_phone(p.group(0))
    out = name
    if email: out += f' <{email}>' if out else email
    if phone: out += f' {phone}' if out else phone
    return out.strip()

# ---------------------------------------------------------------- tracker migration helpers
CONSULTANCIES = re.compile(r'^(?:cgi|capgemini|capgimini|wipro|cognizant|accenture|infosys|tcs|tata consultancy\w*|hcl\w*|'
                           r'tech mahindra|deloitte|pwc|kpmg|ey|ibm|dxc\w*|atos|sopra steria|kainos|bae systems\b.*|leidos|'
                           r'caci|naimuri|made ?tech|hippo digital|ubds\b.*|civica|c3ia|capita|ntt data|mastek|ltimindtree|'
                           r'fujitsu|thoughtworks|equal experts|bjss|version 1|methods|pa consulting|informed solutions|'
                           r'ics\b.*|cloud consulting|tecknuovo|valtech|publicis sapient|slalom|ust|mphasis|hexaware|'
                           r'genpact|virtusa|zensar|birlasoft|coforge|persistent\w*|lti|mindtree|cts)$', _F)

def split_client(v):
    """(consultancy, client) from a 'Consultancy > End client' style value. 'A > B' -> (A, B); 'X / Y' with one known
    consultancy -> (it, the other); a single known consultancy -> (it, ''); anything else -> ('', v) unchanged."""
    v = re.sub(r'\s+', ' ', (v or '').strip())
    if not v: return '', ''
    if '>' in v:
        a, b = (x.strip() for x in v.split('>', 1)); return a, b
    if '/' in v:
        a, b = (x.strip() for x in v.split('/', 1))
        if CONSULTANCIES.match(b) and not CONSULTANCIES.match(a): return b, a
        if CONSULTANCIES.match(a) and not CONSULTANCIES.match(b): return a, b
        return '', v
    return (v, '') if CONSULTANCIES.match(v) else ('', v)

SUBMITTED = re.compile(r'\b(?:rate|day rate|submitted(?: at)?|sent(?: at)?)\s*(?:submitted|sent|agreed|offered|quoted)?\s*'
                       r'[:=\-@]?\s*£?\s*(\d{3,4}(?:\.\d{1,2})?)(?!\d)\s*(?:per day|p/?d\b|/day|a day|daily|pd\b|gbp)?', _F)

def submitted_rate(notes):
    """The rate the member sent, when the notes state it ('Rate: 680 per day' -> '680'); else ''."""
    m = SUBMITTED.search(notes or '')
    if not m: return ''
    n = float(m.group(1))
    return _fmt(n) if 100 <= n <= 5000 else ''

# ---------------------------------------------------------------- selftest / report
CASES = [  # (text, title, rate, expected status) - each from a pattern seen in the stored JDs
    ('AI Engineer\nEngland, UK - Up to £610 per day + inside IR35\nIR35: inside\nType: Contract', '', '', 'Inside'),
    ('Data & Migration Lead\nLondon, UK - £650 outside IR35\nIR35: outside\n\nConfirm in-scope objects.', '', '', 'Outside'),
    ('Data Engineer\nLondon, UK - £500 per day\nType: Contract\n\nWhere the role is marked as outside IR35 in the '
     'advertisement this is subject to receipt of a final determination.', '', '', ''),
    ('X\nUK - £626 per day all inc.(PAYE through FCSA umbrella)\nType: Contract', '', '', 'Inside'),
    ('X\nUK - £550 per day\nType: Contract\n\nIR35 Status: Inside', '', '', 'Inside'),
    ('X\nUK - £550 per day\nType: Contract\n\n- IR35 status: Outside IR35', '', '', 'Outside'),
    ('X\nUK - £550 per day\nType: Contract\n\nIR35: In Scope', '', '', 'Inside'),
    ('X\nUK - £500\nType: Contract\n\nENGAGEMENT TYPE: Via LTD Company/Umbrella', '', '', ''),
    ('X\nUK - £500\nType: Contract\n\nAgency X acts as an employment agency for permanent recruitment and employment '
     'business for the supply of temporary workers.', '', '', ''),
    ('X\nUK - £500\nType: Contract\n\nData may be transferred and processed outside the UK, Switzerland and EEA.', '', '', ''),
    ('Principal Technologist\nUK - £80,000\nType: Permanent\n\nA permanent role.', '', '', 'Permanent'),
    ('Principal Technologist\nLondon, UK - £775 - £800 per day\nType: Contract\n\nContract Type: Permanent\n'
     'Contract Length: 6 months', '', '', ''),
    ('Scala Developer\nUK - £600\nType: Contract/Permanent\n\nUmbrella Day Rate available', '', '', 'Inside'),
    ('', 'AI Engineer - Outside IR35', '', 'Outside'),
    ('', 'Senior Data Scientist (MLOps) - Out IR35', '', 'Outside'),
    ('', 'Data Engineer', '£600 - £675 per day + Inside', 'Inside'),
    ('', 'Data Engineer', '£450 per day | Outside IR35', 'Outside'),
    ('', 'Data Engineer', '450 outside IR-35', 'Outside'),
    ('', 'Data Engineer', '£600 per day', ''),
    ('X\nUK - £500\nType: Contract\n\n- IR35 status: To confirm', '', '', ''),
    ('X\nLondon, UK - 400 Inside IR - 35.\nType: Contract', '', '', 'Inside'),
    ('', 'Commercial Lead/Category Lead, SC London £650 pd Inside, Hybrid', '', 'Inside'),
    ('', 'DevOps Engineer - URGENT - £500 - £525 PER DAY INSIDE', '', 'Inside'),
    ('X\nUK - £550~ Per day\nType: Contract\n\nRole Type: 6 Month Contract - Inside', '', '', 'Inside'),
    ('X\nUK - £500 - £700 per day\nType: Contract\n\nINSIDE - £500-£700 per day | Mainly remote', '', '', 'Inside'),
    ('X\nUK - £450-£550\nType: Contract\n\nour leaders have worked inside the types of institutions we advise.', '', '', ''),
    ('Head of IT\nUK - £600 inside IR35\nType: Contract\n\nThis role is outside IR35 for the right person.', '', '', 'Inside'),
]
RATE_CASES = [  # (rate text, kwargs, expected (min, max)): contract -> per day, permanent -> per year
              ('£600 - £650 per day', {}, ('600', '650')), ('Up to £480 per day', {}, ('', '480')), ('£400/day', {}, ('400', '400')),
              ('£500.0 - £650.0 per day + £market rate (Inside IR35)', {}, ('500', '650')), ('£500 - 580 per day', {}, ('500', '580')),
              ('520.00 Daily - 680.00 Daily GBP', {}, ('520', '680')), ('£671.25/day', {}, ('671.25', '671.25')),
              ('c. £725/day outside IR35 DoE', {}, ('725', '725')), ('450 outside IR-35', {}, ('450', '450')),
              ('£1k - Up To £1100pd Umbrella', {}, ('1000', '1100')), ('Competitive', {}, ('', '')),
              ('£700 per day Competitive Day Rate - Negotiable', {}, ('700', '700')),
              ('£550 - 650 per day, 6 months, 2 days a week on site', {}, ('550', '650')), ('From £500 per day', {}, ('500', '')),
              ('Up to £650 per day via an approved umbrella company', {}, ('', '650')),
              ('£500 - £550 per day + Outside IR35 - onsite 2 days pw', {}, ('500', '550')),
              ('UK - £500 - £550 per day + Outside IR35 - onsite 2 days pw | £500 - £550 per day', {}, ('500', '550')),
              # hourly x 7.5 (or the hours the JD states)
              ('£60 - £63 per hour', {}, ('450', '473')), ('up to £63.50p/h (Inside IR35)', {}, ('', '476')),
              ('£85/hour (Umbrella Inside IR35)', {}, ('638', '638')), ('Up to £89p/h (Inside IR35)', {}, ('', '668')),
              ('£60 - £63 per hour', {'text': '12-Month Contract - up to £63 per hour - 40 hours a week'}, ('480', '504')),
              ('£70 per hour', {'text': 'Full time contract (8 hours per day, 5 days per week)'}, ('560', '560')),
              ('EUR115 per hour (Outside IR35)', {}, ('EUR 863', 'EUR 863')),
              # weekly / monthly / annual for a contract
              ('£3,000 per week', {}, ('600', '600')), ('£10,000 per month', {}, ('545', '545')),
              ('£60,000 - £70,000 per annum', {}, ('273', '318')),
              ('Circa £682 per day (Inside IR35) or Annual Salary: Circa £75,000', {}, ('682', '682')),
              # permanent -> annual
              ('£60,000 - £70,000 per annum', {'permanent': True}, ('60000', '70000')),
              ('£70k - £80k + bonus', {'permanent': True}, ('70000', '80000')),
              ('Circa £682 per day (Inside IR35) or Annual Salary: Circa £75,000', {'permanent': True}, ('75000', '75000')),
              ('£400 per day', {'permanent': True}, ('88000', '88000')), ('£5,000 per month', {'permanent': True}, ('60000', '60000'))]
AGENT_CASES = [  # fictional people; @example.com and the Ofcom drama ranges (07700 900xxx, 020 7946 0xxx)
               ('Contact: Sam\n', 'Agent: Sam <sam@example.com>\n21-Oct-2026', 'Sam <sam@example.com>'),
               ('Contact: Alex Morgan\n', 'Alex Morgan +44 7700 900123 <alex.morgan@example.com>',
                'Alex Morgan <alex.morgan@example.com> +44 7700 900123'),
               ('Contact: Jo Bloggs\n', '', 'Jo Bloggs'),
               ('Contact: Jo Bloggs\n\nEmail jo.bloggs@example.com or info@example.com', '', 'Jo Bloggs <jo.bloggs@example.com>'),
               ('Contact: Sam\n', 'Rate: 680 per day, +447700900456 Tech: x', 'Sam +447700900456'),
               ('Contact: Delivery Team\n', 'Agent = Pat Smith <pat.smith@example.com>', 'Pat Smith <pat.smith@example.com>'),
               ('Contact: Chris Jones\n', 'Agent - Lee Brown <lee.brown@example.com>', 'Lee Brown <lee.brown@example.com>')]
CLIENT_CASES = [('Consultancy Ltd > Home Office - Policing', ('Consultancy Ltd', 'Home Office - Policing')),
                ('Big Bank Group / Wipro', ('Wipro', 'Big Bank Group')), ('CGI', ('CGI', '')),
                ('Cabinet Office', ('', 'Cabinet Office')), ('Internal to consultancy / MoJ', ('', 'Internal to consultancy / MoJ'))]
NOTE_CASES = [('Rate: 680 per day, +447700900456', '680'), ('Agent: Pat Smith <p@example.com>', ''), ('rate 650pd agreed', '650')]

def selftest():
    bad = 0
    for text, title, rate, want in CASES:
        got = ir35(text, title, rate)
        if got[0] != want: bad += 1; print(f'FAIL ir35 {want!r} got {got} for {(title or text)[:70]!r} / {rate!r}')
    for s, kw, want in RATE_CASES:
        if rates(s, **kw) != want: bad += 1; print(f'FAIL rates {s!r} {kw}: {rates(s, **kw)} != {want}')
    for t, n, want in AGENT_CASES:
        if agent(t, n) != want: bad += 1; print(f'FAIL agent: {agent(t, n)!r} != {want!r}')
    for v, want in CLIENT_CASES:
        if split_client(v) != want: bad += 1; print(f'FAIL client {v!r}: {split_client(v)} != {want}')
    for v, want in NOTE_CASES:
        if submitted_rate(v) != want: bad += 1; print(f'FAIL submitted {v!r}: {submitted_rate(v)!r} != {want!r}')
    n = len(CASES) + len(RATE_CASES) + len(AGENT_CASES) + len(CLIENT_CASES) + len(NOTE_CASES)
    print(f'RESULT selftest {n - bad}/{n} passed'); return 1 if bad else 0

def report(paths):
    files = {}
    for p in paths:
        for f in ([p] if os.path.isfile(p) else glob.glob(os.path.join(p, '**', '*.txt'), recursive=True)):
            files.setdefault(os.path.basename(f), f)
    count, rule, hints, rate_blank = {}, {}, [], 0
    for name, f in sorted(files.items()):
        text = open(f, encoding='utf-8', errors='replace').read()
        st, conf, why = ir35(text)
        count[st or '(blank)'] = count.get(st or '(blank)', 0) + 1
        k = (st or '(blank)') + ' <- ' + re.sub(r':.*', '', why) + (f' [{conf}]' if conf else '')
        rule[k] = rule.get(k, 0) + 1
        if not st and re.search(r'ir[\s-]?35|inside|outside|umbrella|paye|permanent', _clean(text), _F): hints.append((name, why))
        t, r, _ = _parts(text, '', '')
        if r and rates(r) == ('', '') and re.search(r'\d{3}', r): rate_blank += 1; print(f'  rate? {name[:50]}: {r[:90]!r}')
    print(f'JDs: {len(files)}'); [print(f'  {k:10} {v}') for k, v in sorted(count.items(), key=lambda x: -x[1])]
    print('by rule:'); [print(f'  {v:4}  {k}') for k, v in sorted(rule.items(), key=lambda x: -x[1])]
    print(f'blank with a hint word ({len(hints)}):'); [print(f'  {n[:60]:60} {w}') for n, w in hints]
    print(f'rate text with digits but no range parsed: {rate_blank}')
    return 0

if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['--selftest']: sys.exit(selftest())
    if a[:1] == ['--report'] and a[1:]: sys.exit(report(a[1:]))
    print(__doc__); sys.exit(2)
