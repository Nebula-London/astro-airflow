#!/usr/bin/env bash
# Fail if a folder contains anything personal, before it is pushed, zipped or published. Same file in every skill.
#   bash check_release.sh [DIR] [--master MY-vN.json] [--allow TERM ...]
#     DIR       default: this skill folder (pass the repo root to check everything)
#     --master  also fail on names taken from YOUR master resume: employers, clients, institutions, projects,
#               your own name, email, phone, credential ids (how client names leaked into examples before)
#     --allow   a term from --master that is fine to appear (e.g. a public company used as a generic example)
# Always checked: JobServe search ids (shid; anyone with one can change that search), Google Sheet ids and Apps
# Script web-app URLs and tokens (they lead to a member's tracker), real-looking emails, UK mobile numbers, LinkedIn and
# calendar links. Placeholders are fine: <YOUR_SHID>, @example.com, +44 7700 900xxx (Ofcom drama range), /in/example.
# Exit 0 = clean, 1 = something found. Needs any python3 (standard library only; the newest on PATH is used).
set -u
dir=""; master=""; allow=()
while [ $# -gt 0 ]; do
  case "$1" in
    --master) master="${2:-}"; shift 2;;
    --allow)  allow+=("${2:-}"); shift 2;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) dir="$1"; shift;;
  esac
done
[ -n "$dir" ] || dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="$(command -v python3.15 || command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3 || true)"
[ -n "$py" ] || { echo "FAIL: python3 is required" >&2; exit 1; }
exec "$py" - "$dir" "$master" ${allow[@]+"${allow[@]}"} <<'PY'
import json, os, re, sys
root, master, allow = sys.argv[1], sys.argv[2], {a.lower() for a in sys.argv[3:] if a}
SKIP_DIRS = {'.git', 'dist', '__pycache__', '.cortex', 'node_modules'}
# the team's blank template sheet (team.json "template_sheet_copy_url") is meant to be shared: only that id is allowed
TEMPLATE_IDS = set()
for base, dirs, files in os.walk(root):
    dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
    if 'team.json' in files:
        try: u = json.load(open(os.path.join(base, 'team.json'), encoding='utf-8')).get('template_sheet_copy_url') or ''
        except (OSError, ValueError, AttributeError): u = ''
        m = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]{25,})', u)
        if m: TEMPLATE_IDS.add(m.group(1))
SECRET = {'JobServe search id', 'JobServe search URL', 'Google Sheet id', 'Apps Script web app', 'web-app token'}
PATTERNS = [  # (label, regex, is this match fine?)
    ('JobServe search id', re.compile(r'(?<![0-9A-Za-z])[0-9A-F]{20}(?![0-9A-Za-z])'), None),
    ('JobServe search URL', re.compile(r'shid=[0-9A-Za-z]{6,}'), None),
    ('Google Sheet id', re.compile(r'docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]{25,}'),
     lambda m: m.rsplit('/', 1)[-1] in TEMPLATE_IDS),
    ('Apps Script web app', re.compile(r'script\.google\.com/macros/s/[A-Za-z0-9_-]{20,}'), None),
    ('web-app token', re.compile(r'TOKEN\s*=\s*[\'"][^\'"]{8,}[\'"]'), lambda m: 'PASTE-YOUR-TOKEN-HERE' in m),
    ('email', re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'),
     lambda m: re.search(r'@example\.(com|org|net)$', m, re.I)),
    ('UK mobile number', re.compile(r'(?:\+44\s?\(?0?\)?\s?|(?<!\d)0)7\d{3}\s?\d{3}\s?\d{3}(?!\d)'),
     lambda m: '7700900' in re.sub(r'\D', '', m)),
    ('LinkedIn profile', re.compile(r'linkedin\.com/in/[A-Za-z0-9_-]+'), lambda m: m.lower().endswith('/in/example')),
    ('calendar link', re.compile(r'(?:calendar\.app\.google|calendly\.com|cal\.com)/[A-Za-z0-9_-]+'),
     lambda m: m.lower().endswith('/example')),
]
GENERIC = {'freelance', 'self-employed', 'self employed', 'contract', 'contractor', 'consultant', 'various', 'confidential',
           'independent', 'present', 'remote', 'london', 'united kingdom', 'england', 'full-time', 'part-time', 'microsoft',
           'google', 'amazon', 'amazon web services', 'oracle', 'ibm', 'linkedin', 'coursera', 'udemy', 'example'}

def master_terms(path):
    d = json.load(open(os.path.expanduser(path), encoding='utf-8'))
    terms = set()
    def add(v, minlen=4):
        if isinstance(v, str):
            v = v.strip()
            if minlen <= len(v) <= 80 and v.lower() not in GENERIC and v.lower() not in allow: terms.add(v)
    m = d.get('member') or d.get('basics') or {}
    if isinstance(m, dict):
        for k in ('first_name', 'last_name', 'preferred_name', 'name'): add(m.get(k), 3)
        c = m.get('contact') or {}
        if isinstance(c, dict):
            add(c.get('email')); add(c.get('phone'))
    for k, v in d.items():
        if not isinstance(v, list): continue
        for it in v:
            if not isinstance(it, dict): continue
            for f in ('client', 'organisation', 'organization', 'company', 'employer', 'institution', 'end_client',
                      'credential_id', 'sponsor'):
                add(it.get(f))
            if re.search(r'project|publication|patent|award', k, re.I):
                add(it.get('name')); add(it.get('title'))
    if not terms: sys.exit(f'FAIL: no names found in {path} (is it a master resume JSON?)')
    return sorted(terms, key=len, reverse=True)

terms = master_terms(master) if master else []
hits = []
for base, dirs, files in os.walk(root):
    dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
    for f in files:
        p = os.path.join(base, f)
        if master and os.path.abspath(p) == os.path.abspath(os.path.expanduser(master)): continue
        try: text = open(p, encoding='utf-8').read()
        except (UnicodeDecodeError, OSError): continue
        for n, line in enumerate(text.splitlines(), 1):
            for label, rx, ok in PATTERNS:
                for mt in rx.finditer(line):
                    if ok and ok(mt.group(0)): continue
                    v = mt.group(0)   # secrets are masked so the report itself never leaks them
                    if label in SECRET: v = '<hidden>'
                    hits.append(f'{p}:{n}: {label}: {v}')
            low = line.lower()
            for t in terms:
                if t.lower() in low:
                    hits.append(f'{p}:{n}: from your master: {t}')
if hits:
    print('FAIL: personal data found (replace with placeholders such as <YOUR_SHID>, Example Bank, @example.com):',
          file=sys.stderr)
    for h in sorted(set(hits)): print('  ' + h, file=sys.stderr)
    sys.exit(1)
print(f'check_release: clean ({root}' + (f', {len(terms)} names from your master' if master else '') + ')')
PY
