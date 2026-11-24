#!/usr/bin/env python3
"""Minimum-disclosure handling of national security clearances (UK levels by default).

Principle: disclose only the LOWEST level that satisfies the job description, never the level
actually held, and never dates, sponsors or references. A higher clearance satisfies every lower
one (e.g. DV holder + JD asking for SC -> "holds SC"). If the JD asks for no clearance, nothing
is disclosed. Further details are offered "at the appropriate level in a suitable secure setting".

Expiry: only "active today" matters. A clearance that expires during the contract still qualifies the member fully,
because starting a role that requires clearance extends it to at least the end of that contract (e.g. expiry Mar 2027,
12-month role starting Nov 2026 -> extended to Nov 2027 or later). Expired clearances are not disclosed.

Hierarchy (UK Security Vetting, https://www.gov.uk/government/publications/united-kingdom-security-vetting-clearance-levels):
  BPSS (baseline standard) < AC < CTC < SC < DV < eDV. Level 1B is treated as CTC-equivalent.
  Adjust RANK if your organisation's policy differs.

Used by digest_master.py (--jd), arrange_roles.py (inserts the CV line) and lint_content.py (blocks over-disclosure).
Standalone:  clearance.py master.json jd.txt
"""
import datetime as dt, json, re, sys

LEVELS = [  # code, rank, full name, JD patterns (case-insensitive full names; abbreviations need security context)
    ('BPSS', 1, 'Baseline Personnel Security Standard', [r'baseline personnel security', r'\bBPSS\b',
                                                          r'\b(basic|baseline) (level of )?(security )?(clearance|vetting|check)']),
    ('AC', 2, 'Accreditation Check', [r'accreditation check']),
    ('CTC', 3, 'Counter Terrorist Check', [r'counter[- ]terroris[mt] check', r'\bCTC\b']),
    ('Level 1B', 3, 'Level 1B', [r'\blevel 1b\b']),
    ('SC', 4, 'Security Check', [r'\bsecurity check\b', r'\bSC\b']),
    ('DV', 5, 'Developed Vetting', [r'(?<!enhanced )developed vetting', r'(?<!enhanced )\bDV\b']),
    ('eDV', 6, 'Enhanced Developed Vetting', [r'enhanced developed vetting', r'\be-?DV\b']),
]
RANK = {c: r for c, r, _, _ in LEVELS}
NAME = {c: n for c, _, n, _ in LEVELS}
ABBREV_CONTEXT = re.compile(r'clear|vett|security|cleared|eligib|hold|obtain|active|current|valid|transfer', re.I)
DESIRABLE = re.compile(r'desirable|preferred|advantage|nice to have|bonus|beneficial|ideally', re.I)
GENERIC = re.compile(r'security[- ]clear(ed|ance)|clearance (is )?(required|essential|mandatory)|must be (security )?cleared|national security vetting', re.I)
ELIGIBLE = re.compile(r'(eligible|eligibility|able|willing) (for|to (obtain|gain|undergo|achieve))', re.I)
# agency self-promotion ("market leader in the delivery of Security Cleared talent"): not a requirement of the role
AGENCY_BLURB = re.compile(r'\b(deliver(y|ing)?|suppl(y|ier|ying)|provid(er|ing)|provision|placing|placement)\s+(of\s+)?'
                          r'(security[- ])?cleared\s+(talent|candidates|professionals|people|personnel|staff|resources?|workforce)', re.I)
LINE = ('Holds active UK {name} ({code}) clearance. Further details can be provided at the appropriate level '
        'in a suitable secure setting.')

def _code_of(entry):
    txt = ' '.join(str(entry.get(k, '')) for k in ('level_code', 'level', 'name', 'type'))
    for code, _, name, _ in sorted(LEVELS, key=lambda x: -x[1]):
        if re.search(r'\b' + re.escape(code) + r'\b', txt, re.I if len(code) > 3 else 0) or name.lower() in txt.lower():
            return code
    return None

def held(master, today=None):
    """Highest ACTIVE clearance code held, plus notes. Reads master['security_clearances'] (or *clearance*/*vetting* lists)."""
    today = today or dt.date.today()
    lists = [v for k, v in master.items() if re.search('clearance|vetting', k, re.I) and isinstance(v, list)]
    best, notes = None, []
    for e in (x for l in lists for x in l if isinstance(x, dict)):
        code = _code_of(e)
        if not code: notes.append(f'unrecognised clearance level: {e.get("level")!r}'); continue
        exp = e.get('expiry_date') or e.get('expiryDate') or ''
        m = re.match(r'(\d{4})-(\d{2})(?:-(\d{2}))?', exp)
        expd = dt.date(int(m[1]), int(m[2]), int(m[3] or 1)) if m else None
        if expd and expd < today: notes.append(f'{code} expired: not disclosed'); continue
        if not best or RANK[code] > RANK[best[0]]: best = (code, expd)
    return best, notes

def required(jd_text):
    """(code or 'ANY' or None, eligibility_only, notes) for the lowest level the JD requires."""
    if not jd_text: return None, False, []
    sents = re.split(r'(?<=[.!?\n])\s+', jd_text)
    req, des, notes, elig, both = [], [], [], False, []
    for s in sents:
        if AGENCY_BLURB.search(s): continue
        for code, rank, _, pats in LEVELS:
            for p in pats:
                flags = 0 if re.search(r'\\b[A-Z]', p) else re.I
                for m in re.finditer(p, s, flags):
                    if flags == 0 and not ABBREV_CONTEXT.search(s[max(0, m.start() - 40):m.end() + 40]): continue
                    (des if DESIRABLE.search(s) else req).append(code)
                    if ELIGIBLE.search(s): elig = True
        if GENERIC.search(s) and not DESIRABLE.search(s): req.append('ANY')
        # "BPSS + SC", "SC and NPPV", "BPSS plus SC": every joined level is required, so the highest one is the floor
        if not DESIRABLE.search(s):
            codes = '|'.join(c for c, _, _, _ in LEVELS)
            for m in re.finditer(rf'\b({codes})\b\s*(?:\+|&|\band\b|\bplus\b)\s*({codes})\b', s):
                both.extend([m.group(1), m.group(2)])
        if ELIGIBLE.search(s) and GENERIC.search(s): elig = True
    named = [c for c in req if c != 'ANY']
    if named:
        low = min(named, key=lambda c: RANK[c])
        if both:
            top = max(both, key=lambda c: RANK[c])
            if RANK[top] > RANK[low]:
                notes.append(f'JD requires levels together ({" + ".join(dict.fromkeys(both))}): {top} is the minimum'); low = top
        if len(set(named)) > 1: notes.append(f'JD names several levels ({", ".join(sorted(set(named), key=RANK.get))}); lowest taken: {low}')
        return low, elig, notes
    if 'ANY' in req: return 'ANY', elig, ['JD asks for security clearance without a level: minimum national security level (CTC) assumed; confirm with the member']
    if des:
        notes.append(f'clearance only desirable ({", ".join(sorted(set(des)))}); disclosed only if held')
        return min(des, key=lambda c: RANK[c]), elig, notes
    return None, False, []

def decide(master, jd_text, today=None):
    """Dict: disclose (code|None), line (CV text|None), notes [str]. Never contains dates or the held level."""
    today = today or dt.date.today()
    h, notes = held(master, today)
    req, elig, rnotes = required(jd_text)
    notes += rnotes
    if re.search(r'\bNPPV\b|non[- ]police personnel vetting', jd_text or '', re.I):
        notes.append('JD also asks for NPPV (police vetting, outside the national clearance hierarchy): not inferred from national clearances; ask the member')
    out = {'required': req, 'disclose': None, 'line': None, 'notes': notes}
    if not req:
        out['notes'].append('JD asks for no clearance: nothing disclosed on the CV')
        return out
    need = 'CTC' if req == 'ANY' else req
    if not h:
        out['notes'].append(f'JD requires {need}{" (eligibility)" if elig else ""}; member holds no active clearance: ask (do not claim)')
        return out
    code, _ = h
    if RANK[code] < RANK[need]:
        out['notes'].append(f'JD requires {need}; member\'s active clearance is lower: ask (do not claim {need})')
        return out
    out['disclose'] = need
    out['line'] = LINE.format(name=NAME[need], code=need)
    # No expiry note: a clearance active today fully qualifies the member, and starting a role that needs clearance
    # extends it to (at least) the end of that contract. Never raise renewal or expiry with the member or on the CV.
    return out

def mentions(text):
    """Clearance level codes mentioned in CV text (for lint)."""
    found = set()
    for code, _, name, pats in LEVELS:
        if re.search(re.escape(name), text, re.I): found.add(code)
        for p in pats:
            if re.search(r'\\b[A-Z]', p) and re.search(p, text): found.add(code)
    return found

if __name__ == '__main__':
    m = json.load(open(sys.argv[1], encoding='utf-8')); jd = open(sys.argv[2], encoding='utf-8').read() if len(sys.argv) > 2 else ''
    print(json.dumps(decide(m, jd), indent=1))
