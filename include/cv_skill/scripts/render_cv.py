#!/usr/bin/env python3
"""Render a targeted CV (JSON content file) to an A4 PDF that fits an exact page budget.

Usage:  python3 render_cv.py content.json output.pdf [--pages 2] [--max-size 13] [--min-size 8.2]

Tries font sizes from --max-size downward (0.1pt steps), keeps the largest size that fits
within --pages, then steps back by --safety pt as headroom for other PDF viewers. Role entries
may split across a page break (heading + first bullet stay together), so the pages fill evenly.
Prints how full the last page is; below ~85% the content is thin for the page budget (add
depth the master supports, or accept it and tell the user). Exits with code 2 if even
--min-size overflows: the CONTENT must then be trimmed (never shrink below readability).

HEADER = exactly 3 lines: name, headline, contact. The contact line is built from a dict and
every item is a clickable link: email -> mailto:, phone -> tel:, "LinkedIn" -> profile URL,
"Website" -> site URL, "Book an interview" -> the member's booking calendar (contact.calendar, last
item; contact.calendar_label overrides the label). Nothing else goes in the header (clearance etc.
belong in the Profile).
The headline and contact font are auto-shrunk to stay on ONE line each; a warning is printed
if a line still would not fit, in which case shorten the headline.

No page footer is drawn (PDF readers already show page numbers).

Content JSON (text may use ReportLab mini-markup <b>, <i> and <a href="URL">label</a>; bare
ampersands are auto-escaped; links are auto-coloured):
{
  "name": "Full Name",
  "headline": "Target title | Key JD focus",
  "contact": {"location": "London, UK", "email": "me@example.com", "phone": "+44 7700 900000",
              "linkedin": "https://www.linkedin.com/in/...", "website": "https://example.com",
              "calendar": "https://calendar.app.google/..."},
  "meta": {"title": "PDF title", "author": "Full Name"},
  "sections": [
    {"heading": "Profile", "items": [
        {"type": "paragraph", "text": "...", "space_before": 0},
        {"type": "labelled", "label": "Security", "text": "..."},
        {"type": "subheading", "text": "Earlier roles with directly relevant experience", "space_before": 0},
        {"type": "role", "title": "Role title", "company": "Company", "company_url": "https://...",
         "right": "May 2026 - Present", "bullets": ["..."],
         "para": "optional paragraph (use instead of, or before, bullets)",
         "ref": 3}   # optional master role index; used by arrange_roles.py, ignored here
    ]}
  ]
}
Notes: "company_url" is optional (omit when no confirmed URL). A "role" item with no bullets and
no para is a one-line entry, which is also how Education entries are written (title = qualification,
company = institution, right = years). Legacy "left": "Title | Company" still works but cannot be
linked; prefer title + company. A legacy string "contact" still renders but should not be used.
"""
import argparse, io, json, re, sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table, TableStyle,
                                HRFlowable, KeepTogether, Flowable)
from pypdf import PdfReader

class _EndMarker(Flowable):
    """Zero-height flowable placed last; records how far down the final page the content reaches."""
    def __init__(self, sink): Flowable.__init__(self); self.sink = sink
    def wrap(self, aw, ah): return (0, 0)
    def draw(self): self.sink['y'] = self.canv.absolutePosition(0, 0)[1]

TOP_MARGIN = 11 * mm; BOTTOM_MARGIN = 13 * mm
NAVY = colors.HexColor('#1F3A5F'); GREY = colors.HexColor('#555555')
LINK = '#1F5FA8'
LM = RM = 14 * mm
FRAME_PAD = 6  # SimpleDocTemplate's frame padding (pt) on each side
WARNINGS = []

def esc(s):
    return re.sub(r'&(?!amp;|lt;|gt;|nbsp;|#\d+;)', '&amp;', s)

def mk(s):
    """Escape bare ampersands and colour any <a href="..."> links."""
    s = esc(s)
    return re.sub(r'<a href="([^"]+)">', lambda m: '<a href="%s" color="%s">' % (m.group(1), LINK), s)

def link(text, url):
    return '<a href="%s" color="%s">%s</a>' % (esc(url), LINK, esc(text))

def plain(markup):
    return re.sub(r'<[^>]+>', '', markup).replace('&amp;', '&')

def contact_markup(c):
    if isinstance(c, str):
        return mk(c)
    parts = []
    if c.get('location'): parts.append(esc(c['location']))
    if c.get('email'): parts.append(link(c['email'], 'mailto:' + c['email']))
    if c.get('phone'): parts.append(link(c['phone'], 'tel:' + re.sub(r'[^\d+]', '', c['phone'])))
    if c.get('linkedin'): parts.append(link('LinkedIn', c['linkedin']))
    if c.get('website'): parts.append(link('Website', c['website']))
    if c.get('calendar'): parts.append(link(c.get('calendar_label') or 'Book an interview', c['calendar']))
    return '  |  '.join(parts)

def fit_one_line(markup, font, start, width, floor, label):
    """Largest font size <= start (not below floor) at which the plain text fits on one line."""
    size = start; txt = plain(markup)
    while size > floor and stringWidth(txt, font, size) > width: size = round(size - 0.1, 2)
    if stringWidth(txt, font, size) > width:
        WARNINGS.append('WARNING: header %s is too long for one line even at %.1fpt; shorten it.' % (label, floor))
    return size

def styles(b, head_size, contact_size):
    L = b * 1.22
    S = ParagraphStyle
    return {
        'name': S('name', fontName='Helvetica-Bold', fontSize=19, leading=22, textColor=NAVY),
        'headline': S('headline', fontName='Helvetica', fontSize=head_size, leading=head_size * 1.25, textColor=GREY),
        'contact': S('contact', fontName='Helvetica', fontSize=contact_size, leading=contact_size * 1.3, textColor=GREY),
        'h': S('h', fontName='Helvetica-Bold', fontSize=b + 1.2, leading=b + 3, textColor=NAVY, spaceBefore=5, spaceAfter=1),
        'body': S('body', fontName='Helvetica', fontSize=b, leading=L),
        'role': S('role', fontName='Helvetica-Bold', fontSize=b + 0.4, leading=L + 0.6),
        'date': S('date', fontName='Helvetica', fontSize=b, leading=L + 0.6, alignment=TA_RIGHT, textColor=GREY),
        'bul': S('bul', fontName='Helvetica', fontSize=b, leading=L, leftIndent=9, bulletIndent=1, spaceAfter=0.4),
        'sub': S('sub', fontName='Helvetica-Oblique', fontSize=b - 0.2, leading=L, textColor=GREY, spaceBefore=3),
    }

def build(content, size):
    W = A4[0] - LM - RM - 2 * FRAME_PAD   # exact inner frame width, so tables align with paragraphs
    WARNINGS.clear()
    head = esc(content.get('headline', ''))
    cont = contact_markup(content.get('contact', ''))
    hs = fit_one_line(head, 'Helvetica', 10, W, 8.5, 'headline') if head else 10
    cs = fit_one_line(cont, 'Helvetica', min(size, 10), W, 8.5, 'contact line') if cont else 10
    st = styles(size, hs, cs)
    story = [Paragraph(esc(content['name']), st['name'])]
    if head: story.append(Paragraph(head, st['headline']))
    if cont: story.append(Paragraph(cont, st['contact']))
    for sec in content['sections']:
        story.append(Paragraph(esc(sec['heading']).upper(), st['h']))
        story.append(HRFlowable(width='100%', thickness=0.6, color=NAVY, spaceAfter=2.5))
        for it in sec['items']:
            t = it['type']
            if t == 'paragraph':
                style = st['body']
                if it.get('space_before'):
                    style = ParagraphStyle('bodysb', parent=st['body'], spaceBefore=it['space_before'])
                story.append(Paragraph(mk(it['text']), style))
            elif t == 'labelled':
                story.append(Paragraph('<b>%s:</b> %s' % (esc(it['label']), mk(it['text'])), st['body']))
            elif t == 'subheading':
                style = st['sub']
                if it.get('space_before'):
                    style = ParagraphStyle('subsb', parent=st['sub'], spaceBefore=it['space_before'])
                story.append(Paragraph(esc(it['text']), style))
            elif t == 'bullet':
                story.append(Paragraph(mk(it['text']), st['bul'], bulletText='•'))
            elif t == 'role':
                if 'title' in it:
                    comp = it.get('company', '')
                    comp_m = link(comp, it['company_url']) if it.get('company_url') and comp else esc(comp)
                    left = esc(it['title']) + (' | ' + comp_m if comp else '')
                else:
                    left = mk(it['left'])
                date_txt = esc(it.get('right', '')).strip()
                if date_txt:
                    dw = min(W * 0.28, stringWidth(plain(date_txt), 'Helvetica', size) + 10)
                    row = Table([[Paragraph(left, st['role']), Paragraph(date_txt, st['date'])]],
                                colWidths=[W - dw, dw], hAlign='LEFT')
                else:
                    row = Table([[Paragraph(left, st['role'])]], colWidths=[W], hAlign='LEFT')
                row.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                                         ('TOPPADDING', (0, 0), (-1, -1), 2.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 0.2),
                                         ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
                block = [row]
                if it.get('para'): block.append(Paragraph(mk(it['para']), st['body']))
                block += [Paragraph(mk(b), st['bul'], bulletText='\u2022') for b in it.get('bullets', [])]
                story.append(KeepTogether(block))
            else:
                raise ValueError('unknown item type: %s' % t)
    sink = {}; story.append(_EndMarker(sink))
    buf = io.BytesIO(); name = content['name']
    meta = content.get('meta', {})
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=LM, rightMargin=RM, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
                            title=meta.get('title', name + ' - CV'), author=meta.get('author', name))
    doc.build(story)   # no footer: PDF readers already show the page count
    data = buf.getvalue()
    usable = A4[1] - TOP_MARGIN - BOTTOM_MARGIN
    fill = max(0.0, min(1.0, (A4[1] - TOP_MARGIN - sink['y']) / usable)) if 'y' in sink else None
    return data, len(PdfReader(io.BytesIO(data)).pages), fill

SAMPLE_PROSE = 'Implemented Python and Databricks data pipelines across Azure Data Factory, SQL and REST APIs for regulated clients.'

def inner_width():
    return A4[0] - LM - RM - 2 * FRAME_PAD

def line_budgets(sizes=(9.5, 10.0, 10.5, 11.0)):
    """Deterministic Helvetica line capacities (characters) at common CV font sizes."""
    W = inner_width(); out = []
    for sz in sizes:
        cw = stringWidth(SAMPLE_PROSE, 'Helvetica', sz) / len(SAMPLE_PROSE)
        bc, bulc = W / cw, (W - 9) / cw
        out.append({
            'size': sz,
            'body_1l': (round(bc * 0.82), round(bc * 0.98)),
            'body_2l': (round(bc * 1.72), round(bc * 1.96)),
            'bullet_1l': (round(bulc * 0.82), round(bulc * 0.98)),
            'bullet_2l': (round(bulc * 1.72), round(bulc * 1.96)),
        })
    return out

def _line_extra(line):
    return line[0] if isinstance(line, tuple) else line.extraSpace

def analyze_layout(content, size):
    """Exact ReportLab line-wrap & trailing-whitespace diagnostics for a content.json at font size."""
    W = inner_width()
    st = styles(size, 10, min(size, 10))
    items, issues = [], []

    def check_para(sec_h, tag, markup, style, text_len_hint=None):
        p = Paragraph(markup, style, bulletText='\u2022' if style is st['bul'] else None)
        p.wrap(W, 10000)
        ls = p.blPara.lines
        avail = W - style.leftIndent
        last_extra = _line_extra(ls[-1]) if ls else avail
        fill_pct = max(0, min(100, round(100 * (avail - last_extra) / avail)))
        ptxt = plain(markup)
        cw = max(3.6, stringWidth(ptxt, style.fontName, style.fontSize) / max(1, len(ptxt)))
        nlines = len(ls)
        msg = ''
        if nlines >= 3:
            trim_ch = max(1, round((avail - last_extra + 14) / cw))
            msg = f'{nlines} lines (line {nlines} {fill_pct}%): trim ~{trim_ch} chars to fit {nlines - 1} lines'
        elif nlines == 2 and fill_pct < 74:
            add_ch = max(1, round((0.90 * avail - (avail - last_extra)) / cw))
            if fill_pct <= 35:
                trim_ch = max(1, round((avail - last_extra + 14) / cw))
                msg = f'2 lines (line 2 {fill_pct}%): add ~{add_ch} chars to fill, or trim ~{trim_ch} chars for 1 line'
            else:
                msg = f'2 lines (line 2 {fill_pct}%): add ~{add_ch} chars from master to fill line 2'
        elif nlines == 1 and fill_pct < 76:
            add_ch = max(1, round((0.90 * avail - (avail - last_extra)) / cw))
            msg = f'1 line ({fill_pct}%): add ~{add_ch} chars from master'
        rec = {'section': sec_h, 'item': tag, 'chars': text_len_hint if text_len_hint is not None else len(ptxt),
               'lines': nlines, 'last_fill': fill_pct, 'issue': msg}
        items.append(rec)
        if msg: issues.append(rec)

    for sec in content.get('sections', []):
        sh = sec.get('heading', '')
        if re.search(r'certif|education', sh, re.I): continue
        for idx, it in enumerate(sec.get('items', []), 1):
            t = it.get('type')
            if t == 'paragraph':
                txt = it.get('text', '')
                if txt.startswith('Also:') or sh.lower().startswith(('profile', 'summary')): continue
                check_para(sh, f'para[{idx}]', mk(txt), st['body'], len(plain(txt)))
            elif t == 'labelled':
                lab = it.get('label', '')
                if lab.lower().startswith('security clearance'): continue
                markup = '<b>%s:</b> %s' % (esc(lab), mk(it.get('text', '')))
                check_para(sh, lab or f'labelled[{idx}]', markup, st['body'], len(plain(markup)))
            elif t == 'role':
                if 'title' in it:
                    comp = it.get('company', '')
                    comp_m = link(comp, it['company_url']) if it.get('company_url') and comp else esc(comp)
                    left = esc(it['title']) + (' | ' + comp_m if comp else '')
                else:
                    left = mk(it['left'])
                date_txt = esc(it.get('right', '')).strip()
                dw = min(W * 0.28, stringWidth(plain(date_txt), 'Helvetica', size) + 10) if date_txt else 0
                cw_left = W - dw
                lp = Paragraph(left, st['role'])
                lp.wrap(cw_left, 10000)
                if len(lp.blPara.lines) > 1:
                    ptxt = plain(left)
                    over_pt = max(0.0, stringWidth(ptxt, 'Helvetica-Bold', size + 0.4) - cw_left)
                    cw_b = max(4.0, stringWidth(ptxt, 'Helvetica-Bold', size + 0.4) / max(1, len(ptxt)))
                    msg = f'role heading wraps to {len(lp.blPara.lines)} lines (+{over_pt:.0f}pt; shorten title/company by ~{max(1, round(over_pt / cw_b) + 2)} chars)'
                    rec = {'section': sh, 'item': f"role[{it.get('ref', idx)}] {it.get('company', '')}", 'chars': len(ptxt),
                           'lines': len(lp.blPara.lines), 'last_fill': 0, 'issue': msg}
                    items.append(rec); issues.append(rec)
                who = it.get('company') or it.get('title') or f'role[{idx}]'
                if it.get('para'):
                    check_para(sh, f'{who} (para)', mk(it['para']), st['body'], len(plain(it['para'])))
                for bi, b in enumerate(it.get('bullets', []), 1):
                    check_para(sh, f'{who} b{bi}', mk(b), st['bul'], len(plain(b)))
    return {'size': size, 'inner_width_pt': round(W, 1), 'budgets': line_budgets((size,)), 'items': items, 'issues': issues}

def print_layout_report(rep):
    b = rep['budgets'][0]
    print(f"LAYOUT ({rep['size']:.1f}pt, width {rep['inner_width_pt']}pt): body 1L={b['body_1l'][0]}-{b['body_1l'][1]}ch / 2L={b['body_2l'][0]}-{b['body_2l'][1]}ch"
          f" | bullet 1L={b['bullet_1l'][0]}-{b['bullet_1l'][1]}ch / 2L={b['bullet_2l'][0]}-{b['bullet_2l'][1]}ch")
    if not rep['issues']:
        print('  no wrapped headings or short trailing lines')
        return
    for r in rep['issues']:
        print(f"  - [{r['section']}] {r['item']} ({r['chars']}ch): {r['issue']}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('content'); ap.add_argument('output', nargs='?')
    ap.add_argument('--pages', type=int, default=2)
    ap.add_argument('--max-size', type=float, default=13.0)
    ap.add_argument('--min-size', type=float, default=8.2)
    ap.add_argument('--safety', type=float, default=0.1)
    ap.add_argument('--size', type=float, help='measure layout at this exact font size with --layout')
    ap.add_argument('--layout', action='store_true', help='print deterministic per-item line fill & char budgets')
    ap.add_argument('--layout-json', help='write layout diagnostics JSON here')
    a = ap.parse_args()
    if not a.output and not a.layout: ap.error('output PDF path is required unless --layout is passed')
    content = json.load(open(a.content, encoding='utf-8'))
    if a.layout and not a.output:
        sz = a.size
        if sz is None:
            s = a.max_size
            while s >= a.min_size - 1e-9:
                _, n, _ = build(content, s)
                if n <= a.pages:
                    cand = max(round(s - a.safety, 2), a.min_size)
                    _, n2, _ = build(content, cand)
                    sz = cand if n2 <= a.pages else s
                    break
                s = round(s - 0.1, 2)
            if sz is None: sz = a.min_size
        rep = analyze_layout(content, sz)
        if a.layout_json: json.dump(rep, open(a.layout_json, 'w', encoding='utf-8'), indent=1)
        print_layout_report(rep)
        return 0
    size = a.max_size
    while size >= a.min_size - 1e-9:
        data, n, fill = build(content, size)
        if n <= a.pages:
            final = max(round(size - a.safety, 2), a.min_size)
            d2, n2, f2 = build(content, final)
            if n2 <= a.pages: data, n, fill = d2, n2, f2
            else: final = size
            open(a.output, 'wb').write(data)
            lrep = analyze_layout(content, final)
            if a.layout_json: json.dump(lrep, open(a.layout_json, 'w', encoding='utf-8'), indent=1)
            if a.layout:
                print_layout_report(lrep)
            elif lrep['issues']:
                wraps = sum(1 for r in lrep['issues'] if 'role heading wraps' in r['issue'])
                shorts = len(lrep['issues']) - wraps
                print(f'LAYOUT: {len(lrep["issues"])} line-fill item(s) at {final:.1f}pt (role_wraps={wraps}, short_or_spill={shorts}; run --layout / cv layout for +N/-M chars)')
            msg = 'OK: %d page(s) at %.1fpt -> %s' % (n, final, a.output)
            if fill is not None:
                msg += ' | last page %d%% full' % round(fill * 100)
                if n == a.pages and fill < 0.85: msg += ' (below 85%: content is thin for this page budget)'
            print(msg)
            for w in WARNINGS: print(w)
            return 0
        size = round(size - 0.1, 2)
    print('TOO LONG: does not fit %d page(s) even at %.1fpt. Trim the content (drop bullets or roles); do not shrink further.' % (a.pages, a.min_size))
    return 2

if __name__ == '__main__':
    sys.exit(main())
