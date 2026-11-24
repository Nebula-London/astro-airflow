#!/usr/bin/env python3
"""Convert a PDF file (including targeted CV PDFs) into an editable Word .docx preserving text, fonts,
bold/italic styling, colours, horizontal rules, right-aligned date columns, bullets, and clickable hyperlinks.

Usage:
  python3 pdf_to_docx.py INPUT.pdf [OUTPUT.docx] [--font Arial]

Requires only PyMuPDF (already installed by the skill launcher) and Python's standard library.
"""
import argparse, os, re, sys, zipfile
from xml.sax.saxutils import escape
import pymupdf

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL_HLINK = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink'

CONTENT_TYPES_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

PKG_RELS_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

STYLES_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr>
      <w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>
      <w:sz w:val="21"/><w:szCs w:val="21"/>
    </w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:before="0" w:after="0" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
</w:styles>'''


def _tw(pt):
    return max(0, int(round(pt * 20)))


def _hex_rgb(val):
    if isinstance(val, int):
        return f'{val & 0xFFFFFF:06X}'
    if isinstance(val, (tuple, list)) and len(val) >= 3:
        r, g, b = (max(0, min(255, int(round(c * 255)))) for c in val[:3])
        return f'{r:02X}{g:02X}{b:02X}'
    return '000000'


def _match_link(bbox, links):
    sr = pymupdf.Rect(bbox)
    sa = max(1e-6, sr.get_area())
    cx, cy = (sr.x0 + sr.x1) * 0.5, (sr.y0 + sr.y1) * 0.5
    best, best_score = '', 0.0
    for lr, uri in links:
        inter = sr & lr
        score = inter.get_area() / sa if not inter.is_empty else (0.5 if lr.contains((cx, cy)) else 0.0)
        if score > 0.25 and score > best_score:
            best, best_score = uri, score
    return best


def _extract_page(page):
    w_pt, h_pt = float(page.rect.width), float(page.rect.height)
    links = [(pymupdf.Rect(l['from']), l['uri']) for l in page.get_links() if l.get('uri') and l.get('from')]
    rules = []
    for d in page.get_drawings():
        r = pymupdf.Rect(d['rect'])
        if r.height <= 3.5 and r.width >= w_pt * 0.35:
            col = _hex_rgb(d.get('color') or d.get('fill') or (0.12, 0.23, 0.37))
            rules.append({'y': (r.y0 + r.y1) * 0.5, 'color': col, 'sz': max(4, min(12, int(round(max(r.height, 0.6) * 8))))})
    raw_lines = []
    for b in page.get_text('dict').get('blocks', []):
        for l in b.get('lines', []):
            spans = []
            for s in l.get('spans', []):
                if not s.get('text'):
                    continue
                if not s['text'].strip() and not spans:
                    continue
                fn = s.get('font', '')
                spans.append({
                    'x0': float(s['bbox'][0]), 'y0': float(s['bbox'][1]),
                    'x1': float(s['bbox'][2]), 'y1': float(s['bbox'][3]),
                    'text': s['text'],
                    'size': float(s.get('size', 10.5)),
                    'bold': 'Bold' in fn or 'Black' in fn,
                    'italic': 'Oblique' in fn or 'Italic' in fn,
                    'color': _hex_rgb(s.get('color', 0)),
                    'url': _match_link(s['bbox'], links),
                })
            if spans and ''.join(x['text'] for x in spans).strip():
                raw_lines.append({
                    'x0': min(x['x0'] for x in spans), 'y0': min(x['y0'] for x in spans),
                    'x1': max(x['x1'] for x in spans), 'y1': max(x['y1'] for x in spans),
                    'spans': spans,
                })
    raw_lines.sort(key=lambda r: (r['y0'], r['x0']))
    # Cluster lines sharing the same horizontal baseline (within 3.5pt), sort left-to-right within each band
    bands = []
    for ln in raw_lines:
        if bands and abs(ln['y0'] - bands[-1][0]['y0']) <= 3.5:
            bands[-1].append(ln)
        else:
            bands.append([ln])
    rows = []
    for band in bands:
        band.sort(key=lambda r: r['x0'])
        row = {'x0': band[0]['x0'], 'y0': min(x['y0'] for x in band),
               'x1': max(x['x1'] for x in band), 'y1': max(x['y1'] for x in band),
               'left': list(band[0]['spans']), 'right': [], 'rule': None}
        for nxt in band[1:]:
            if nxt['x0'] > band[0]['x1'] + 15.0:
                row['right'].extend(nxt['spans'])
            else:
                row['left'].extend(nxt['spans'])
        row['left_x1'] = max(s['x1'] for s in row['left'])
        rows.append(row)
    # Attach horizontal rules immediately below a row
    for rule in rules:
        above = [r for r in rows if r['y0'] < rule['y'] <= r['y1'] + 10.0]
        if above:
            target = min(above, key=lambda r: abs(rule['y'] - r['y1']))
            target['rule'] = rule
    return w_pt, h_pt, rows


def _is_bullet_row(row):
    txt = ''.join(s['text'] for s in row['left']).lstrip()
    return txt.startswith('\u2022')


def _strip_bullet(spans):
    out = [dict(s) for s in spans]
    while out and not out[0]['text'].strip():
        out.pop(0)
    if out and out[0]['text'].lstrip().startswith('\u2022'):
        t = out[0]['text'].lstrip()[1:].lstrip()
        if t:
            out[0]['text'] = t
        else:
            out.pop(0)
            if out and out[0]['text'].startswith(' '):
                out[0]['text'] = out[0]['text'].lstrip()
    # Record where actual text (after bullet glyph) starts
    text_x0 = out[0]['x0'] if out else spans[0]['x0']
    return out, text_x0


def _starts_labelled_or_heading(row):
    if not row['left']:
        return False
    s0 = row['left'][0]
    full = ''.join(s['text'] for s in row['left']).strip()
    if s0['bold'] and (':' in s0['text'] or len(row['left']) == 1 or full.isupper()):
        return True
    if row['right'] or row['rule']:
        return True
    return False


def _first_word_w(spans):
    txt = ''.join(s['text'] for s in spans).lstrip()
    w = (txt.split() or [''])[0]
    sz = spans[0]['size'] if spans else 10.5
    return len(w) * sz * 0.52


def _merge_page_paragraphs(rows, right_edge_pt):
    """Merge wrapped continuation lines into native Word paragraphs."""
    paras = []
    for row in rows:
        is_bul = _is_bullet_row(row)
        left_spans, text_x0 = _strip_bullet(row['left']) if is_bul else ([dict(s) for s in row['left']], row['x0'])
        if not paras:
            paras.append({
                'spans': left_spans, 'right': row['right'], 'rule': row['rule'],
                'bullet': is_bul, 'x0': row['x0'], 'text_x0': text_x0, 'left_x1': row['left_x1'],
                'y0': row['y0'], 'y1': row['y1'], 'size': left_spans[0]['size'] if left_spans else 10.5,
                'space_before': 0.0,
            })
            continue
        prev = paras[-1]
        gap = row['y0'] - prev['y1']
        line_h = max(8.0, prev['size'] * 1.25)
        wrapped = prev['left_x1'] + _first_word_w(left_spans) >= right_edge_pt - 14.0
        same_indent = (
            (prev['x0'] - 2.0 <= row['x0'] <= prev['x0'] + 16.0) if prev['bullet']
            else abs(row['x0'] - prev['text_x0']) <= 3.5
        )
        can_merge = (
            wrapped
            and not is_bul
            and not row['right']
            and not prev['right']
            and not prev['rule']
            and not _starts_labelled_or_heading(row)
            and not prev['spans'][0]['italic']
            and gap <= max(5.5, prev['size'] * 0.55)
            and same_indent
            and abs((left_spans[0]['size'] if left_spans else prev['size']) - prev['size']) <= 0.6
        )
        if can_merge:
            last_t = prev['spans'][-1]['text'] if prev['spans'] else ''
            first_t = left_spans[0]['text'] if left_spans else ''
            if last_t and first_t and not last_t.endswith((' ', '-', '/')) and not first_t.startswith((' ', ',', '.', ';', ':')):
                prev['spans'].append({**prev['spans'][-1], 'text': ' ', 'url': ''})
            prev['spans'].extend(left_spans)
            prev['y1'] = row['y1']
            prev['left_x1'] = row['left_x1']
            prev['rule'] = row['rule'] or prev['rule']
        else:
            sb = max(0.0, min(14.0, gap - 1.5)) if gap > 3.5 else 0.0
            paras.append({
                'spans': left_spans, 'right': row['right'], 'rule': row['rule'],
                'bullet': is_bul, 'x0': row['x0'], 'text_x0': text_x0, 'left_x1': row['left_x1'],
                'y0': row['y0'], 'y1': row['y1'], 'size': left_spans[0]['size'] if left_spans else 10.5,
                'space_before': sb, 'line_h': line_h,
            })
    return paras


def _run_xml(span, font):
    rpr = [f'<w:rFonts w:ascii="{escape(font)}" w:hAnsi="{escape(font)}" w:cs="{escape(font)}"/>']
    if span['bold']:
        rpr.append('<w:b/>')
    if span['italic']:
        rpr.append('<w:i/>')
    sz = max(12, int(round(span['size'] * 2)))
    rpr.append(f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>')
    col = span['color']
    if col and col != '000000':
        rpr.append(f'<w:color w:val="{col}"/>')
    if span.get('url'):
        rpr.append('<w:u w:val="single"/>')
    txt = escape(span['text'])
    return f'<w:r><w:rPr>{"".join(rpr)}</w:rPr><w:t xml:space="preserve">{txt}</w:t></w:r>'


def _coalesce_spans(spans):
    out = []
    for s in spans:
        if out and all(out[-1][k] == s[k] for k in ('bold', 'italic', 'size', 'color', 'url')):
            out[-1]['text'] += s['text']
        else:
            out.append(dict(s))
    return out


def convert_pdf_to_docx(pdf_path, docx_path=None, font='Arial'):
    doc = pymupdf.open(pdf_path)
    if not len(doc):
        raise ValueError(f'Empty PDF: {pdf_path}')
    pages = [_extract_page(p) for p in doc]
    w_pt, h_pt, _ = pages[0]
    all_rows = [r for _, _, rows in pages for r in rows]
    min_x = min((r['x0'] for r in all_rows), default=42.0)
    max_x = max((r['x1'] for r in all_rows), default=w_pt - 42.0)
    min_y = min((r['y0'] for r in pages[0][2]), default=36.0)
    max_y = max((r['y1'] for r in pages[-1][2]), default=h_pt - 36.0)
    lm_pt = max(18.0, min(min_x, 72.0))
    rm_pt = max(18.0, min(w_pt - max_x, 72.0))
    tm_pt = max(18.0, min(min_y, 60.0))
    bm_pt = max(18.0, min(h_pt - max_y, 60.0))
    content_w_pt = max(200.0, w_pt - lm_pt - rm_pt)

    rels = {}
    def rid_for(url):
        if url not in rels:
            rels[url] = f'rId{len(rels) + 2}'
        return rels[url]

    def emit_spans(spans):
        parts = []
        for sp in _coalesce_spans(spans):
            rx = _run_xml(sp, font)
            if sp['url']:
                parts.append(f'<w:hyperlink r:id="{rid_for(sp["url"])}" w:history="1">{rx}</w:hyperlink>')
            else:
                parts.append(rx)
        return ''.join(parts)

    body_xml = []
    right_edge_pt = w_pt - rm_pt
    for pi, (_, _, rows) in enumerate(pages):
        paras = _merge_page_paragraphs(rows, right_edge_pt)
        for p in paras:
            ppr = []
            sb_tw = _tw(p['space_before'])
            sa_tw = 40 if p['rule'] else 10
            lh_tw = _tw(max(10.0, p['size'] * 1.22))
            ppr.append(f'<w:spacing w:before="{sb_tw}" w:after="{sa_tw}" w:line="{lh_tw}" w:lineRule="exact"/>')
            if p['rule']:
                ru = p['rule']
                ppr.append(f'<w:pBdr><w:bottom w:val="single" w:sz="{ru["sz"]}" w:space="2" w:color="{ru["color"]}"/></w:pBdr>')
            if p['bullet']:
                ind_left = _tw(max(9.0, p['text_x0'] - lm_pt))
                hang = max(120, _tw(max(6.0, p['text_x0'] - p['x0'])))
                ppr.append(f'<w:ind w:left="{ind_left}" w:hanging="{hang}"/>')
            elif p['x0'] > lm_pt + 2.0:
                ppr.append(f'<w:ind w:left="{_tw(p["x0"] - lm_pt)}"/>')
            if p['right']:
                ppr.append(f'<w:tabs><w:tab w:val="right" w:pos="{_tw(content_w_pt)}"/></w:tabs>')
            inner = []
            if p['bullet']:
                bsz = int(round(p['size'] * 2))
                inner.append(f'<w:r><w:rPr><w:rFonts w:ascii="{escape(font)}" w:hAnsi="{escape(font)}" w:cs="{escape(font)}"/><w:sz w:val="{bsz}"/><w:szCs w:val="{bsz}"/></w:rPr><w:t>\u2022</w:t><w:tab/></w:r>')
            inner.append(emit_spans(p['spans']))
            if p['right']:
                inner.append('<w:r><w:tab/></w:r>' + emit_spans(p['right']))
            body_xml.append(f'<w:p><w:pPr>{"".join(ppr)}</w:pPr>{"".join(inner)}</w:p>')

    sect_pr = (
        f'<w:sectPr>'
        f'<w:pgSz w:w="{_tw(w_pt)}" w:h="{_tw(h_pt)}"/>'
        f'<w:pgMar w:top="{_tw(tm_pt)}" w:right="{_tw(rm_pt)}" w:bottom="{_tw(bm_pt)}" w:left="{_tw(lm_pt)}" w:header="360" w:footer="360" w:gutter="0"/>'
        f'</w:sectPr>'
    )
    doc_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
        f'<w:body>{"".join(body_xml)}{sect_pr}</w:body></w:document>'
    )
    rel_items = ['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
    for url, rid in rels.items():
        rel_items.append(f'<Relationship Id="{rid}" Type="{REL_HLINK}" Target="{escape(url)}" TargetMode="External"/>')
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rel_items)}</Relationships>'
    )
    out = docx_path or (os.path.splitext(pdf_path)[0] + '.docx')
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', CONTENT_TYPES_XML)
        zf.writestr('_rels/.rels', PKG_RELS_XML)
        zf.writestr('word/styles.xml', STYLES_XML.format(font=escape(font)))
        zf.writestr('word/_rels/document.xml.rels', rels_xml)
        zf.writestr('word/document.xml', doc_xml)
    return out, len(body_xml), len(rels), len(doc)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Convert a PDF file to an editable Word (.docx) document.')
    ap.add_argument('pdf', help='Input PDF path')
    ap.add_argument('output', nargs='?', help='Output DOCX path (default: <pdf_stem>.docx)')
    ap.add_argument('--font', default='Arial', help='Word font family (default: Arial, metrically compatible with Helvetica)')
    a = ap.parse_args(argv)
    out, np, nh, npages = convert_pdf_to_docx(a.pdf, a.output, a.font)
    print(f'OK: {npages} page(s), {np} paragraph(s), {nh} unique link(s) -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
