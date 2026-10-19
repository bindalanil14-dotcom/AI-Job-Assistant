"""
Tailored resume PDF generation using ReportLab.

The layout follows a fixed, professional single-column structure (inspired by the
widely used "Jake's Resume" LaTeX template): a centered name + contact header, blue
small-caps section headings underlined by a rule, categorised technical skills,
company -> sub-role work experience, two-column education, and numbered projects and
certifications. Only the STRUCTURE is fixed — all content comes from the candidate's
own data. The output never contains internal system language, scores, or flags.
"""

import io

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, ListFlowable, ListItem,
    Table, TableStyle,
)

DEFAULT_ACCENT = "#0a66c2"   # professional blue (matches the LinkedIn-style theme)
RULE_COLOR = "#222222"
LEFT_MARGIN = RIGHT_MARGIN = 0.5 * inch
CONTENT_WIDTH = letter[0] - LEFT_MARGIN - RIGHT_MARGIN


def _safe_hex(value, fallback):
    try:
        return colors.HexColor(value) if value else colors.HexColor(fallback)
    except Exception:
        return colors.HexColor(fallback)


def _esc(text):
    if text is None:
        return ""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _clean(value):
    """Blank out empty or 'Not Available' placeholders so they never show in the PDF."""
    v = ("" if value is None else str(value)).strip()
    return "" if v.lower() in ("", "not available", "n/a", "na", "none") else v


def _styles(accent):
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle("name", parent=base["Title"], fontName="Helvetica-Bold",
                               fontSize=21, textColor=colors.HexColor("#111111"),
                               alignment=TA_CENTER, spaceAfter=3, leading=24),
        "contact": ParagraphStyle("contact", parent=base["Normal"], fontName="Helvetica",
                                  fontSize=9.5, textColor=colors.HexColor("#333333"),
                                  alignment=TA_CENTER, spaceAfter=2, leading=13),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontName="Helvetica-Bold",
                                  fontSize=11.5, textColor=accent, spaceBefore=10, spaceAfter=1,
                                  leading=13),
        "summary": ParagraphStyle("summary", parent=base["Normal"], fontName="Helvetica",
                                  fontSize=10, textColor=colors.HexColor("#1c1c1c"),
                                  alignment=TA_JUSTIFY, leading=13.5, spaceAfter=2),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName="Helvetica",
                               fontSize=10, textColor=colors.HexColor("#1c1c1c"), leading=13.5,
                               spaceAfter=2),
        "left": ParagraphStyle("left", parent=base["Normal"], fontName="Helvetica",
                               fontSize=10.5, textColor=colors.HexColor("#111111"), leading=13),
        "right": ParagraphStyle("right", parent=base["Normal"], fontName="Helvetica",
                                fontSize=10, textColor=colors.HexColor("#333333"),
                                alignment=TA_RIGHT, leading=13),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontName="Helvetica",
                                 fontSize=9.7, textColor=colors.HexColor("#1c1c1c"), leading=13),
    }


def _section(flow, styles, title):
    flow.append(Paragraph(_esc(title).upper(), styles["section"]))
    flow.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor(RULE_COLOR),
                           spaceBefore=1, spaceAfter=4))


def _lr(left_html, right_html, lstyle, rstyle):
    """A borderless two-column row pinned hard-left: left content, right-aligned content."""
    t = Table([[Paragraph(left_html, lstyle), Paragraph(right_html, rstyle)]],
              colWidths=[CONTENT_WIDTH * 0.66, CONTENT_WIDTH * 0.34])
    t.hAlign = "LEFT"  # never let the table auto-centre within the frame
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return t


# Shared left edge for body text / wrapped bullet lines (bullet glyph sits at the margin).
BULLET_TEXT_INDENT = 12


def _bullets(styles, items):
    # bulletDedent == leftIndent => bullet glyph aligns to the left margin (x=0),
    # level with section headings and company names; text hangs at BULLET_TEXT_INDENT.
    return ListFlowable(
        [ListItem(Paragraph(_esc(i), styles["bullet"]), value=None) for i in items],
        bulletType="bullet", start="•", leftIndent=BULLET_TEXT_INDENT,
        bulletDedent=BULLET_TEXT_INDENT, bulletFontSize=6, spaceBefore=0,
    )


# ---- section renderers ----
def _render_summary(flow, styles, content):
    if content.get("summary"):
        _section(flow, styles, "Professional Summary")
        flow.append(Paragraph(_esc(content["summary"]), styles["summary"]))


def _render_technical_skills(flow, styles, content):
    groups = content.get("technical_skills") or []
    if not groups and content.get("skills"):
        groups = [{"category": "Skills", "items": content["skills"]}]
    groups = [g for g in groups if (g.get("items") if isinstance(g, dict) else False)]
    if not groups:
        return
    _section(flow, styles, "Technical Skills")
    for g in groups:
        cat = _esc(g.get("category", ""))
        items = ", ".join(_esc(i) for i in g.get("items", []) if i)
        flow.append(Paragraph(f"<b>{cat}:</b> {items}" if cat else items, styles["body"]))


def _render_experience(flow, styles, content):
    exp = content.get("experience") or []
    if not exp:
        return
    _section(flow, styles, "Work Experience")
    for e in exp:
        if isinstance(e, str):
            flow.append(Paragraph(_esc(e), styles["body"]))
            continue
        company = _clean(e.get("company", ""))
        location = _clean(e.get("location", ""))
        role = _clean(e.get("role", "") or e.get("title", ""))
        left = f"<b>{_esc(company)}</b>" + (f", {_esc(location)}" if location else "")
        if company or role:
            flow.append(_lr(left or f"<b>{_esc(role)}</b>",
                            f"<i>{_esc(role)}</i>" if (company and role) else "",
                            styles["left"], styles["right"]))
        positions = e.get("positions") or e.get("roles") or []
        if not positions and e.get("highlights"):
            positions = [{"title": "", "dates": e.get("dates", ""),
                          "highlights": e.get("highlights", [])}]
        for pos in positions:
            ptitle = _clean(pos.get("title", ""))
            pdates = _clean(pos.get("dates", ""))
            # Avoid printing the role twice: if this sub-role just repeats the overall
            # role (single-role job), show only the dates line.
            duplicate = ptitle.lower() == role.lower()
            if duplicate:
                ptitle = ""
            if ptitle or pdates:
                flow.append(_lr(f"<b>{_esc(ptitle)}</b>" if ptitle else "",
                                f"<b>{_esc(pdates)}</b>" if pdates else "",
                                styles["left"], styles["right"]))
            hl = [h for h in pos.get("highlights", []) if h]
            if hl:
                flow.append(_bullets(styles, hl))
            flow.append(Spacer(1, 3))


def _render_education(flow, styles, content):
    edu = content.get("education") or []
    if not edu:
        return
    _section(flow, styles, "Education")
    for ed in edu:
        if isinstance(ed, str):
            flow.append(Paragraph(_esc(ed), styles["body"]))
            continue
        inst = _clean(ed.get("institution", ""))
        dates = _clean(ed.get("dates", "") or ed.get("year", ""))
        degree = _clean(ed.get("degree", ""))
        loc = _clean(ed.get("location", ""))
        if inst or dates:
            flow.append(_lr(f"<b>{_esc(inst)}</b>", f"<b>{_esc(dates)}</b>" if dates else "",
                            styles["left"], styles["right"]))
        if degree or loc:
            flow.append(_lr(f"<i>{_esc(degree)}</i>" if degree else "",
                            f"<i>{_esc(loc)}</i>" if loc else "",
                            styles["left"], styles["right"]))
        flow.append(Spacer(1, 2))


def _render_projects(flow, styles, content):
    projects = content.get("projects") or []
    if not projects:
        return
    _section(flow, styles, "Projects")
    for i, p in enumerate(projects, 1):
        if isinstance(p, str):
            flow.append(Paragraph(f"<b>{i}. {_esc(p)}</b>", styles["body"]))
            continue
        title = _esc(p.get("title", ""))
        tag = _esc(p.get("tag", ""))
        head = f"<b>{i}. {title}</b>" + (f' <font size="9" color="#555555">({tag})</font>' if tag else "")
        flow.append(Paragraph(head, styles["body"]))
        hl = [h for h in p.get("highlights", []) if h]
        if not hl and p.get("description"):
            hl = [p["description"]]
        if hl:
            flow.append(_bullets(styles, hl))
        flow.append(Spacer(1, 3))


def _render_certifications(flow, styles, content):
    certs = [c for c in (content.get("certifications") or []) if c]
    if not certs:
        return
    _section(flow, styles, "Certifications and Relevant Courses")
    for i, c in enumerate(certs, 1):
        if isinstance(c, dict):
            c = c.get("name") or c.get("title") or ""
        flow.append(Paragraph(f"{i}. {_esc(c)}", styles["body"]))


def _contact_line(content):
    """Build a centered ' | '-separated contact line from contact object or contact_line."""
    c = content.get("contact")
    if isinstance(c, dict):
        parts = [_clean(c.get("location")), _clean(c.get("phone")),
                 _clean(c.get("email")), _clean(c.get("linkedin"))]
        parts = [_esc(p) for p in parts if p]
        if parts:
            return "  |  ".join(parts)
    return _esc(_clean(content.get("contact_line", "")))


def generate_resume_pdf(content, ref_colors=None, header_align="center"):
    """
    Render the resume content into PDF bytes using the fixed professional structure.

    ref_colors: optional {"accent",...} parsed from the reference resume — section
    headings adopt that colour (default professional blue otherwise).
    Content is never altered by the colour.
    """
    accent = _safe_hex((ref_colors or {}).get("accent"), DEFAULT_ACCENT)
    styles = _styles(accent)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title="Resume",
    )

    flow = []
    # Header: centered name + contact
    flow.append(Paragraph(_esc(content.get("name_line", "")) or "Resume", styles["name"]))
    contact = _contact_line(content)
    if contact:
        flow.append(Paragraph(contact, styles["contact"]))
    flow.append(Spacer(1, 4))

    default_order = ["summary", "technical_skills", "experience", "education",
                     "projects", "certifications"]
    # Map any legacy "skills" name to technical_skills.
    requested = content.get("section_order") or default_order
    order = []
    for s in list(requested) + default_order:
        s = (s or "").lower().strip()
        s = "technical_skills" if s == "skills" else s
        if s in default_order and s not in order:
            order.append(s)

    renderers = {
        "summary": _render_summary,
        "technical_skills": _render_technical_skills,
        "experience": _render_experience,
        "education": _render_education,
        "projects": _render_projects,
        "certifications": _render_certifications,
    }
    for section in order:
        renderers[section](flow, styles, content)

    doc.build(flow)
    buf.seek(0)
    return buf.read()
