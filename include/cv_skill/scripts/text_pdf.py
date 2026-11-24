#!/usr/bin/env python3
"""Plain text -> a simple A4 PDF (Helvetica, wrapped, multi-page), standard library only. Used to put job
descriptions into Google Drive next to the CVs (the tracker web app accepts PDFs only). Shared by jobserve-new-jobs and
targeted-cv-from-master (each skill ships an identical copy).

  text_pdf.py IN.txt OUT.pdf         # first non-blank line = title (bold), the header lines up to the first blank line
                                     # in grey, the rest as body text
  text_pdf.py --selftest
"""
import os, sys, zlib

PAGE_W, PAGE_H, MARGIN = 595.28, 841.89, 51.0          # A4 in points, 18 mm margins
# Helvetica advance widths (1/1000 em) for ASCII 32..126, from the standard AFM; other characters use 556
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
      556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
      722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
      556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
      260, 334, 584]
_SUB = {'\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"', '\u2013': '-', '\u2014': '-', '\u2022': '-',
        '\u00a0': ' ', '\t': '    ', '\u2026': '...', '\u200b': ''}

def _width(s, size, bold=False):
    w = sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in s) * size / 1000
    return w * 1.06 if bold else w

def _wrap(line, size, width, bold=False):
    words, out, cur = line.split(' '), [], ''
    for w in words:
        t = f'{cur} {w}' if cur else w
        if _width(t, size, bold) <= width: cur = t; continue
        if cur: out.append(cur)
        while _width(w, size, bold) > width:            # a very long token (URL): break it
            k = max(1, int(len(w) * width / _width(w, size, bold)))
            out.append(w[:k]); w = w[k:]
        cur = w
    out.append(cur)
    return out

def _pdfstr(s):
    """A PDF literal string in WinAnsi (cp1252): £, €, accented letters print; anything else becomes '?'."""
    s = ''.join(_SUB.get(c, c) for c in s)
    b = s.encode('cp1252', errors='replace')
    out = []
    for ch in b:
        if ch in (0x28, 0x29, 0x5c): out.append('\\' + chr(ch))
        elif 32 <= ch < 127: out.append(chr(ch))
        else: out.append('\\%03o' % ch)
    return '(' + ''.join(out) + ')'

def render(text, out_pdf, title=None):
    """Write `text` to `out_pdf`. Returns the number of pages."""
    lines = (text or '').replace('\r', '').split('\n')
    while lines and not lines[0].strip(): lines.pop(0)
    head = lines.pop(0).strip() if lines else ''
    width = PAGE_W - 2 * MARGIN
    items = []                                           # (font, size, grey, text) or None for a paragraph gap
    for l in _wrap(head, 15, width, True): items.append(('F2', 15, 0, l))
    items.append(None)
    meta = True
    for ln in lines:
        s = ' '.join(ln.split()) if ln.strip() else ''
        if not s: meta = False; items.append(None); continue
        size, grey = (9, 0.3) if meta else (10, 0)
        for l in _wrap(s, size, width): items.append(('F1', size, grey, l))
    pages, cur, y = [], [], PAGE_H - MARGIN
    for it in items:
        lead = 5 if it is None else it[1] * 1.35
        if y - lead < MARGIN and cur: pages.append(cur); cur, y = [], PAGE_H - MARGIN
        y -= lead
        if it is not None: cur.append((it, y))
    if cur or not pages: pages.append(cur)
    objs = []                                            # bytes of objects 1..n
    def add(b): objs.append(b); return len(objs)
    cat, pages_id = add(b''), add(b'')
    f1 = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    f2 = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>')
    kids = []
    for pg in pages:
        ops = []
        for (font, size, grey, s), y in pg:
            ops.append(f'BT /{font} {size} Tf {grey:.2f} g {MARGIN:.2f} {y:.2f} Td {_pdfstr(s)} Tj ET')
        data = zlib.compress('\n'.join(ops).encode('latin-1'))
        content = add(b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(data) + data + b'\nendstream')
        kids.append(add(('<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 %d 0 R '
                         '/F2 %d 0 R >> >> /Contents %d 0 R >>' % (pages_id, PAGE_W, PAGE_H, f1, f2, content)).encode()))
    objs[cat - 1] = b'<< /Type /Catalog /Pages %d 0 R >>' % pages_id
    objs[pages_id - 1] = ('<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join(f'{k} 0 R' for k in kids), len(kids))).encode()
    info = add(('<< /Title %s /Producer (text_pdf.py) >>' % _pdfstr(title or head or 'Job description')).encode('latin-1'))
    out, offs = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'), []
    for i, b in enumerate(objs, 1):
        offs.append(len(out)); out += b'%d 0 obj\n' % i + b + b'\nendobj\n'
    x = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % o for o in offs)
    out += b'trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, cat, info, x)
    tmp = out_pdf + '.tmp'
    with open(tmp, 'wb') as f: f.write(out)
    os.replace(tmp, out_pdf)
    return len(kids)

def render_file(txt, out_pdf):
    with open(txt, encoding='utf-8', errors='replace') as f: return render(f.read(), out_pdf)

def selftest():
    import re, tempfile
    bad = 0
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 't.pdf')
        body = 'AI Engineer (Outside IR35)\nLondon, UK - £600 - £650 per day\nType: Contract\n\n' + \
               '\n'.join(f'Line {i}: build “agentic” workflows – {"x" * (i % 90)} €5 https://example.com/' + 'a' * 120
                         for i in range(160))
        n = render(body, p)
        with open(p, 'rb') as f: b = f.read()
        ok = b.startswith(b'%PDF-1.4') and b.rstrip().endswith(b'%%EOF') and n >= 3
        x = int(re.search(rb'startxref\n(\d+)', b).group(1))
        ok = ok and b[x:x + 4] == b'xref'
        for i, off in enumerate(re.findall(rb'(\d{10}) 00000 n ', b), 1):   # every xref offset points at "i 0 obj"
            ok = ok and b[int(off):int(off) + len(b'%d 0 obj' % i)] == b'%d 0 obj' % i
        if not ok: bad += 1; print('FAIL text_pdf: malformed PDF')
        if render('', p) != 1: bad += 1; print('FAIL text_pdf: empty text')
    print(f'RESULT text_pdf selftest {"ok" if not bad else "FAILED"}'); return 1 if bad else 0

if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['--selftest']: sys.exit(selftest())
    if len(a) == 2: print(f'{a[1]}: {render_file(a[0], a[1])} page(s)'); sys.exit(0)
    print(__doc__); sys.exit(2)
