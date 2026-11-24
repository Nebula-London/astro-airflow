#!/usr/bin/env python3
"""Check a rendered CV PDF with text-based tests, so page images are rarely needed.

Usage:  python3 check_pdf.py cv.pdf [--pages 2] [--content content.json] [--images DIR] [--dpi 60]

Checks (exit 1 on any FAIL):
- page count equals --pages
- header: page 1 starts with name, headline, contact line (3 lines) and the next line is a section heading
- left edge: no text starts left of the page margin
- orphans: no page ends with a section heading or a role heading without its first line
- glyphs: characters outside Helvetica's WinAnsi set (render as boxes) in --content or the PDF text
- links: lists every URI; mailto: and tel: must be present; with --content, contact.calendar must be a link
Page images are written only with --images DIR (pymupdf, else pdftoppm): use them for a final
glance, not for routine checking.
"""
import argparse, json, os, re, shutil, subprocess, sys
from pypdf import PdfReader
try:
    import pymupdf as fitz  # optional; enables layout checks (PyMuPDF 1.24+; 'fitz' is its legacy name)
except ImportError:
    fitz = None

LEFT_MARGIN_PT = 14 * 72 / 25.4 + 6  # render_cv.py LM + frame padding

def winansi_bad(text):
    bad = set()
    for ch in text:
        try: ch.encode('cp1252')
        except UnicodeEncodeError: bad.add(ch)
    return bad

def lines_of(page):
    out = []
    for b in page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            spans = [s for s in l['spans'] if s['text'].strip()]
            if spans:
                out.append({'text': ''.join(s['text'] for s in spans).strip(), 'x': l['bbox'][0], 'y': l['bbox'][1],
                            'bold': 'Bold' in spans[0]['font'], 'size': spans[0]['size']})
    return sorted(out, key=lambda l: (round(l['y']), l['x']))

def main():
    a = argparse.ArgumentParser()
    a.add_argument('pdf'); a.add_argument('--pages', type=int, default=2)
    a.add_argument('--content'); a.add_argument('--images'); a.add_argument('--dpi', type=int, default=60)
    a = a.parse_args()
    fails, warns = [], []
    reader = PdfReader(a.pdf); n = len(reader.pages)
    if n != a.pages: fails.append(f'{n} pages, expected {a.pages}')

    uris = []
    for i, p in enumerate(reader.pages, 1):
        for an in p.get('/Annots') or []:
            act = an.get_object().get('/A') or {}
            if act.get('/URI'): uris.append((i, act['/URI']))
    for scheme in ('mailto:', 'tel:'):
        if not any(u.startswith(scheme) for _, u in uris): fails.append(f'no {scheme} link in header')

    src = ''
    if a.content:
        cj = json.load(open(a.content, encoding='utf-8')); src = json.dumps(cj, ensure_ascii=False)
        cal = (cj.get('contact') or {}).get('calendar') if isinstance(cj.get('contact'), dict) else None
        if cal and not any(u == cal for _, u in uris): fails.append(f'calendar booking link missing from header: {cal}')
    bad = winansi_bad(src + ''.join(p.extract_text() or '' for p in reader.pages))
    if bad: fails.append('characters Helvetica cannot render: ' + ' '.join(sorted(repr(c) for c in bad)))

    if fitz:
        doc = fitz.open(a.pdf)
        p1 = lines_of(doc[0])
        if len(p1) >= 4:
            if not (p1[3]['text'].isupper() and p1[3]['bold']):
                fails.append(f'header is not exactly 3 lines (4th line: "{p1[3]["text"][:50]}")')
        for pi, page in enumerate(doc, 1):
            ls = lines_of(page)
            minx = min((l['x'] for l in ls), default=LEFT_MARGIN_PT)
            if minx < LEFT_MARGIN_PT - 1.5: fails.append(f'page {pi}: text starts left of margin (x={minx:.1f})')
            if pi < len(doc) and ls:
                last = ls[-1]
                if last['bold'] and last['text'].isupper():
                    fails.append(f'page {pi}: ends with orphaned section heading "{last["text"]}"')
                elif last['bold'] and '|' in last['text']:
                    fails.append(f'page {pi}: role heading "{last["text"][:50]}" separated from its content')
    else:
        warns.append('pymupdf missing: header/margin/orphan checks skipped (use --images and look once)')

    if a.images:
        os.makedirs(a.images, exist_ok=True)
        if fitz:
            for i, page in enumerate(fitz.open(a.pdf), 1):
                path = os.path.join(a.images, f'pg{i}.png'); page.get_pixmap(dpi=a.dpi).save(path); print('image:', path)
        elif shutil.which('pdftoppm'):
            subprocess.run(['pdftoppm', '-r', str(a.dpi), '-png', a.pdf, os.path.join(a.images, 'pg')], check=True)
            print('images in', a.images)
        else:
            warns.append('no pymupdf or pdftoppm: images skipped')

    hosts = {}
    for _, u in uris: hosts[u] = hosts.get(u, 0) + 1
    print('links:', '; '.join(f'{u} x{c}' if c > 1 else u for u, c in hosts.items()))
    for w in warns: print('WARNING:', w)
    for f in fails: print('FAIL:', f)
    print(('OK' if not fails else 'FAIL') + f': {n} page(s), {len(uris)} link(s)')
    return 1 if fails else 0

if __name__ == '__main__':
    sys.exit(main())
