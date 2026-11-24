#!/usr/bin/env python3
"""Check a member's master resume JSON before the first CV (read-only: never modifies the file).

Usage:
  check_master.py [MASTER.json | DIR]      # DIR (default: cwd) -> the highest *-vN.json in it

FAIL = the skill cannot build a correct CV from it (fix first); WARN = it works, but the CV will be weaker or
a step will need a question. Exit 1 on any FAIL. The template is references/example-master.json.
"""
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.dont_write_bytecode = True
import digest_master as dm

VER = re.compile(r'-v(\d+)\.json$', re.I)
DATE = re.compile(r'^\d{4}(-\d{2}(-\d{2})?)?$')

def pick(where):
    if os.path.isfile(where): return where
    found = [f for f in glob.glob(os.path.join(where, '*-v*.json')) if VER.search(f)]
    if not found: sys.exit(f'FAIL: no *-vN.json master in {where} (pass the file, e.g. check_master.py me-v1.json)')
    return max(found, key=lambda f: int(VER.search(f).group(1)))

def main(argv):
    if argv and argv[0] in ('-h', '--help'): print(__doc__); return 0
    path = os.path.abspath(os.path.expanduser(pick(argv[0] if argv else '.')))
    fails, warns = [], []
    try:
        d = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError) as e:
        print(f'FAIL: cannot read {path} as JSON: {e}'); return 1
    if not isinstance(d, dict):
        print('FAIL: the master must be a JSON object ({...}), see references/example-master.json'); return 1

    if not VER.search(path):
        warns.append(f'file name should end in -vN.json (e.g. {os.path.basename(path)[:-5]}-v1.json): '
                     'the skill picks the highest version and saves approved changes as vN+1')

    # contact block: the CV header comes from here
    flat = {}
    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if isinstance(x, (dict, list)): walk(x)
                elif x not in (None, ''): flat.setdefault(k, x)
    walk(dm.first_dict(d, ('member', 'personal', 'basics', 'contact', 'profile_info')))
    name = ' '.join(str(x) for x in (flat.get('preferred_name') or flat.get('first_name'), flat.get('last_name')) if x) \
        or str(flat.get('name', ''))
    if not name: fails.append('no name: add member.first_name and member.last_name (or preferred_name)')
    if d.get('_readme') and name == 'Alex Morgan':
        fails.append('this is still the example master: replace Alex Morgan\'s details with your own')
    if not flat.get('email'): warns.append('no email in member.contact: the CV header will have no email')
    elif str(flat['email']).endswith('@example.com'): warns.append(f'email is still the example ({flat["email"]})')
    if not flat.get('phone'): warns.append('no phone in member.contact')

    roles = dm.load_roles(d)
    if not roles:
        fails.append('no roles found: add a "career_history" list (one object per role)')
    for r in roles:
        who = f"{dm.g(r, 'role_title') or '?'} @ {dm.g(r, 'client', 'organisation') or '?'} ({r['start_date'] or 'no start'})"
        if not dm.g(r, 'role_title'): fails.append(f'{who}: no role_title')
        if not dm.g(r, 'client', 'organisation'): fails.append(f'{who}: no client or organisation')
        if not r['start_date']: fails.append(f'{who}: no start_date (YYYY-MM-DD or YYYY-MM)')
        elif not DATE.match(r['start_date']): fails.append(f'{who}: start_date "{r["start_date"]}" is not YYYY-MM-DD or YYYY-MM')
        if r['end_date'] and not DATE.match(r['end_date']):
            fails.append(f'{who}: end_date "{r["end_date"]}" is not YYYY-MM-DD, YYYY-MM or "" (current role)')
        elif r['end_date'] and DATE.match(r['start_date'] or '') and r['end_date'][:len(r['start_date'])] < r['start_date'][:len(r['end_date'])]:
            fails.append(f'{who}: end_date {r["end_date"]} is before start_date {r["start_date"]}')
        if not (r.get('responsibilities') or r.get('achievements')):
            warns.append(f'{who}: no responsibilities or achievements, so there is nothing to write bullets from')
        if not r.get('technologies_used'):
            warns.append(f'{who}: no technologies_used, so tools used in this role cannot be evidenced')
    current = [r for r in roles if not r['end_date']]
    if len(current) > 1:
        warns.append(f'{len(current)} roles have no end_date (current): fine if they really run in parallel, '
                     'otherwise add the end dates')

    if not dm.first_list_of_dicts(d, ('skill',)): warns.append('no skills list: Core Capabilities rely on it')
    if not dm.first_list_of_dicts(d, ('education',)): warns.append('no education list')
    for c in d.get('security_clearances') or []:
        if isinstance(c, dict) and not (c.get('level_code') or c.get('level')):
            fails.append('security_clearances entry without level_code (BPSS, CTC, SC, DV, eDV) or level')

    for f in fails: print('FAIL:', f)
    for w in warns: print('WARN:', w)
    print(f'RESULT: {os.path.basename(path)}: {len(roles)} role(s), {len(fails)} FAIL, {len(warns)} WARN'
          + ('' if fails else ' - ready to use'))
    return 1 if fails else 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
