#!/usr/bin/env python3
"""Build a MARK / Nonlinear Alpha A4 research PDF from Markdown.

Visual identity: Modern Investment Editorial
  - warm paper, black ink, muted gray, restrained red
  - de-gridded tables, hairline rules
  - small footer watermark: Written by 林凯 (Ricardo Lin)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

INK = HexColor("#1B1B18")
MUTED = HexColor("#5D5A52")
ACCENT = HexColor("#D9362B")
PAPER = HexColor("#F4EDDE")
RULE = HexColor("#C9C1B0")
CELL = HexColor("#EFE7D6")
BASE_FILL = HexColor("#F7F1E4")
WATERMARK = "Written by 林凯 (Ricardo Lin)"

PAGE_W, PAGE_H = A4
MARGIN_L = 18 * mm
MARGIN_R = 18 * mm
MARGIN_T = 22 * mm
MARGIN_B = 18 * mm
USABLE = PAGE_W - MARGIN_L - MARGIN_R


def _try_register(name: str, path: Path) -> bool:
    if not path.exists():
        return False
    try:
        kwargs = {"subfontIndex": 0} if path.suffix.lower() == ".ttc" else {}
        pdfmetrics.registerFont(TTFont(name, str(path), **kwargs))
        return True
    except Exception:
        return False


def _register_fonts() -> tuple[str, str]:
    """Return (body_font, meta_font). Prefer 京华老宋体 + a clean sans."""
    skill_dir = Path(__file__).resolve().parent.parent
    serif_candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "KingHwa_OldSong.ttf",
        Path("/mnt/c/Windows/Fonts/KingHwa_OldSong.ttf"),
        Path.home() / "Library/Fonts/KingHwa_OldSong.ttf",
        Path.home() / "AppData/Local/Microsoft/Windows/Fonts/KingHwa_OldSong.ttf",
        skill_dir / "assets" / "KingHwa_OldSong.ttf",
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf"),
    ]
    sans_candidates = [
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
        Path("/mnt/c/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]

    body = "Times-Roman"
    for path in serif_candidates:
        if _try_register("MarkBody", path):
            pdfmetrics.registerFontFamily(
                "MarkBody",
                normal="MarkBody",
                bold="MarkBody",
                italic="MarkBody",
                boldItalic="MarkBody",
            )
            body = "MarkBody"
            break
    if body == "Times-Roman":
        # Last-resort CJK-capable face so Chinese reports still render.
        for path in sans_candidates:
            if _try_register("MarkBody", path):
                pdfmetrics.registerFontFamily(
                    "MarkBody",
                    normal="MarkBody",
                    bold="MarkBody",
                    italic="MarkBody",
                    boldItalic="MarkBody",
                )
                body = "MarkBody"
                break

    meta = body
    for path in sans_candidates:
        if _try_register("MarkMeta", path):
            meta = "MarkMeta"
            break
    return body, meta


def _base_kwargs(font: str) -> dict:
    return {
        "fontName": font,
        "wordWrap": "CJK",
        "splitLongWords": True,
        "alignment": TA_LEFT,
        "encoding": "utf-8",
    }


def _styles(font: str) -> dict:
    ss = getSampleStyleSheet()
    kw = _base_kwargs(font)
    return {
        "h1": ParagraphStyle(
            "MarkH1", parent=ss["Normal"], **kw, fontSize=18, leading=24,
            spaceAfter=6, spaceBefore=0, textColor=ACCENT,
        ),
        "h2": ParagraphStyle(
            "MarkH2", parent=ss["Normal"], **kw, fontSize=12, leading=16.5,
            spaceBefore=11, spaceAfter=5, textColor=INK,
        ),
        "h3": ParagraphStyle(
            "MarkH3", parent=ss["Normal"], **kw, fontSize=10.5, leading=14.5,
            spaceBefore=7, spaceAfter=3, textColor=INK,
        ),
        "body": ParagraphStyle(
            "MarkBodyP", parent=ss["Normal"], **kw, fontSize=9.2, leading=13.8,
            spaceAfter=5, textColor=INK,
        ),
        "kicker": ParagraphStyle(
            "MarkKicker", parent=ss["Normal"], **kw, fontSize=8.6, leading=12.4,
            textColor=MUTED, spaceAfter=7,
        ),
        "rating": ParagraphStyle(
            "MarkRating", parent=ss["Normal"], **kw, fontSize=13, leading=18,
            textColor=INK, spaceBefore=2, spaceAfter=6,
        ),
        "code": ParagraphStyle(
            "MarkCode", parent=ss["Normal"], **kw, fontSize=8.0, leading=11.6,
            textColor=INK, backColor=CELL, leftIndent=6, rightIndent=6,
            spaceBefore=3, spaceAfter=7, borderPadding=4,
        ),
        "th": ParagraphStyle(
            "MarkTh", parent=ss["Normal"], **kw, fontSize=7.5, leading=10.8,
            textColor=INK,
        ),
        "td": ParagraphStyle(
            "MarkTd", parent=ss["Normal"], **kw, fontSize=7.5, leading=10.8,
            textColor=INK,
        ),
        "li": ParagraphStyle(
            "MarkLi", parent=ss["Normal"], **kw, fontSize=9.0, leading=13.2,
            spaceAfter=2, textColor=INK,
        ),
        "source": ParagraphStyle(
            "MarkSource", parent=ss["Normal"], **kw, fontSize=8.0, leading=11.6,
            spaceAfter=3, leftIndent=12, firstLineIndent=-12, textColor=MUTED,
        ),
    }


def _draw_chrome(canvas, doc, header: str, date: str, meta_font: str) -> None:
    canvas.saveState()
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    # Small red arrow motif, top-left.
    canvas.setFillColor(ACCENT)
    path = canvas.beginPath()
    ax, ay = MARGIN_L, PAGE_H - 11 * mm
    path.moveTo(ax, ay)
    path.lineTo(ax + 3.2 * mm, ay + 1.5 * mm)
    path.lineTo(ax, ay + 3.0 * mm)
    path.close()
    canvas.drawPath(path, fill=1, stroke=0)

    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(0.55)
    canvas.line(MARGIN_L, PAGE_H - 14 * mm, PAGE_W - MARGIN_R, PAGE_H - 14 * mm)

    canvas.setFillColor(MUTED)
    canvas.setFont(meta_font, 7)
    canvas.drawString(MARGIN_L + 5 * mm, PAGE_H - 12.4 * mm, header)
    canvas.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 12.4 * mm, date)

    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.35)
    canvas.line(MARGIN_L, 12.2 * mm, PAGE_W - MARGIN_R, 12.2 * mm)

    canvas.setFillColor(MUTED)
    canvas.setFont(meta_font, 7)
    canvas.drawString(MARGIN_L, 8.0 * mm, "INVESTMENT RESEARCH")
    canvas.drawCentredString(PAGE_W / 2, 8.0 * mm, str(doc.page))

    # Subordinate, horizontal, low-contrast authorship watermark.
    canvas.setFillColor(Color(0.36, 0.35, 0.32, alpha=0.38))
    canvas.setFont(meta_font, 6)
    canvas.drawRightString(PAGE_W - MARGIN_R, 8.0 * mm, WATERMARK)
    canvas.restoreState()


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<font color='#5D5A52'>\1</font>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r"<link href='\2' color='#D9362B'><u>\1</u></link>",
        text,
    )
    text = re.sub(
        r"(?<!href=')(https?://[^\s<]+)",
        r"<font size='7' color='#5D5A52'>\1</font>",
        text,
    )
    return text


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-[-:]{2,}", line))


def _col_widths(ncols: int, rows: list[list[str]]) -> list[float]:
    if ncols <= 1:
        return [USABLE]
    if ncols == 3:
        fracs = [0.22, 0.48, 0.30]
    elif ncols == 4:
        last_numeric = all(
            re.match(r"^[\d$+\-–—%.x×至到 ]+$", (r[3] if len(r) > 3 else ""), re.I)
            for r in rows[1:]
        )
        fracs = [0.16, 0.34, 0.28, 0.22] if last_numeric else [0.16, 0.28, 0.28, 0.28]
    else:
        fracs = [1 / ncols] * ncols
    return [USABLE * f for f in fracs]


def _make_table(rows: list[list[str]], styles: dict) -> Table:
    ncols = max(len(r) for r in rows)
    norm = [r + [""] * (ncols - len(r)) for r in rows]
    widths = _col_widths(ncols, norm)
    data = []
    for ri, row in enumerate(norm):
        st = styles["th"] if ri == 0 else styles["td"]
        data.append([Paragraph(_inline(cell), st) for cell in row])
    table = Table(data, colWidths=widths, repeatRows=1, splitByRow=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), CELL),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4.2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4.2),
        ("TOPPADDING", (0, 0), (-1, -1), 3.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
        ("LINEABOVE", (0, 0), (-1, 0), 0.55, ACCENT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.25, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.22, RULE),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]
    # Light fill on Base-case rows in scenario tables.
    for ri, row in enumerate(norm):
        if ri == 0:
            continue
        label = re.sub(r"<[^>]+>", "", row[0]).lower()
        if "base" in label or "基准" in row[0]:
            cmds.append(("BACKGROUND", (0, ri), (-1, ri), BASE_FILL))
    table.setStyle(TableStyle(cmds))
    return table


def parse_markdown(md: str, styles: dict) -> list:
    lines = md.replace("\r\n", "\n").split("\n")
    story: list = []
    i = 0
    in_code = False
    code_lines: list[str] = []
    saw_h1 = False

    def flush_code() -> None:
        nonlocal code_lines
        if not code_lines:
            return
        html = "<br/>".join(_inline(x) if x.strip() else "&nbsp;" for x in code_lines)
        story.append(Paragraph(html, styles["code"]))
        code_lines = []

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        if line.strip() in ("---",):
            story.append(Spacer(1, 2 * mm))
            i += 1
            continue
        if line.strip().lower() in ("<!-- pagebreak -->", "\\newpage"):
            story.append(PageBreak())
            i += 1
            continue
        if line.startswith("# "):
            story.append(Paragraph(_inline(line[2:].strip()), styles["h1"]))
            saw_h1 = True
            i += 1
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            # Red section number if the heading starts with a digit.
            m = re.match(r"^(\d+)\.\s+(.*)$", heading)
            if m:
                html = f"<font color='#D9362B'>{m.group(1)}.</font> {_inline(m.group(2))}"
                story.append(Paragraph(html, styles["h2"]))
            else:
                story.append(Paragraph(_inline(heading), styles["h2"]))
            i += 1
            continue
        if line.startswith("### "):
            story.append(Paragraph(_inline(line[4:].strip()), styles["h3"]))
            i += 1
            continue
        if line.strip().startswith("|") and i + 1 < len(lines) and _is_sep(lines[i + 1]):
            headers = _split_row(line)
            i += 2
            rows = [headers]
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            story.append(Spacer(1, 1.2 * mm))
            story.append(_make_table(rows, styles))
            story.append(Spacer(1, 2.2 * mm))
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(
                    ListItem(
                        Paragraph(_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])), styles["li"]),
                        leftIndent=8,
                        bulletColor=ACCENT,
                    )
                )
                i += 1
            story.append(
                ListFlowable(
                    items,
                    bulletType="bullet",
                    start="•",
                    leftIndent=14,
                    bulletFontName=styles["body"].fontName,
                    bulletFontSize=8,
                    bulletColor=ACCENT,
                    spaceAfter=2,
                )
            )
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                text = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                items.append(
                    ListItem(
                        Paragraph(_inline(text), styles["source"] if "http" in text else styles["li"]),
                        leftIndent=10,
                    )
                )
                i += 1
            story.append(
                ListFlowable(
                    items,
                    bulletType="1",
                    start="1",
                    leftIndent=16,
                    bulletFontName=styles["body"].fontName,
                    bulletFontSize=8,
                    bulletColor=INK,
                    spaceAfter=2,
                )
            )
            continue
        if not line.strip():
            i += 1
            continue
        para = [line.strip()]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not lines[i].startswith("#")
            and not lines[i].strip().startswith("|")
            and not lines[i].strip().startswith("```")
            and not re.match(r"^\s*[-*]\s+", lines[i])
            and not re.match(r"^\s*\d+\.\s+", lines[i])
            and lines[i].strip() != "---"
            and lines[i].strip().lower() not in ("<!-- pagebreak -->", "\\newpage")
        ):
            para.append(lines[i].strip())
            i += 1
        text = " ".join(para)
        style = styles["kicker"] if saw_h1 and len(story) <= 2 else styles["body"]
        story.append(Paragraph(_inline(text), style))
        saw_h1 = False
    return story


def build(input_path: Path, output_path: Path, header: str, date: str) -> None:
    body_font, meta_font = _register_fonts()
    styles = _styles(body_font)
    md = input_path.read_text(encoding="utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def chrome(c, d):
        _draw_chrome(c, d, header, date, meta_font)

    doc = BaseDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=header,
        author="林凯 (Ricardo Lin)",
    )
    frame = Frame(
        MARGIN_L,
        MARGIN_B,
        USABLE,
        PAGE_H - MARGIN_T - MARGIN_B,
        id="body",
        showBoundary=0,
    )
    doc.addPageTemplates([PageTemplate(id="mark", frames=[frame], onPage=chrome)])
    story = parse_markdown(md, styles)
    doc.build(story)


def main() -> int:
    p = argparse.ArgumentParser(description="Build MARK / Nonlinear Alpha research PDF")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--header", default="INVESTMENT RESEARCH")
    p.add_argument("--date", required=True)
    args = p.parse_args()
    build(Path(args.input), Path(args.output), args.header, args.date)
    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
