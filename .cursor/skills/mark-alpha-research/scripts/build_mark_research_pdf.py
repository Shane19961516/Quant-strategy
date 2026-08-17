#!/usr/bin/env python3
"""Build a MARK-style A4 research PDF from Markdown."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
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

PAGE_W, PAGE_H = A4
MARGIN_L = 18 * mm
MARGIN_R = 18 * mm
MARGIN_T = 22 * mm
MARGIN_B = 16 * mm
USABLE = PAGE_W - MARGIN_L - MARGIN_R


def _register_font() -> str:
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "KingHwa_OldSong.ttf",
        Path("/mnt/c/Windows/Fonts/KingHwa_OldSong.ttf"),
        Path.home() / "Library/Fonts/KingHwa_OldSong.ttf",
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            kwargs = {"subfontIndex": 0} if path.suffix.lower() == ".ttc" else {}
            pdfmetrics.registerFont(TTFont("MarkBody", str(path), **kwargs))
            pdfmetrics.registerFont(TTFont("MarkBody-Bold", str(path), **kwargs))
            pdfmetrics.registerFontFamily(
                "MarkBody",
                normal="MarkBody",
                bold="MarkBody-Bold",
                italic="MarkBody",
                boldItalic="MarkBody-Bold",
            )
            return "MarkBody"
        except Exception:
            continue
    return "Times-Roman"


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
            "MarkH1", parent=ss["Normal"], **kw, fontSize=16, leading=22,
            spaceAfter=8, spaceBefore=0, textColor=INK,
        ),
        "h2": ParagraphStyle(
            "MarkH2", parent=ss["Normal"], **kw, fontSize=12, leading=16.5,
            spaceBefore=10, spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "MarkH3", parent=ss["Normal"], **kw, fontSize=10.5, leading=14.5,
            spaceBefore=7, spaceAfter=3, textColor=ACCENT,
        ),
        "body": ParagraphStyle(
            "MarkBodyP", parent=ss["Normal"], **kw, fontSize=9.2, leading=13.8,
            spaceAfter=5,
        ),
        "kicker": ParagraphStyle(
            "MarkKicker", parent=ss["Normal"], **kw, fontSize=8.6, leading=12.4,
            textColor=MUTED, spaceAfter=7,
        ),
        "code": ParagraphStyle(
            "MarkCode", parent=ss["Normal"], **kw, fontSize=8.0, leading=11.6,
            textColor=INK, backColor=CELL, leftIndent=6, rightIndent=6,
            spaceBefore=3, spaceAfter=7, borderPadding=3,
        ),
        "th": ParagraphStyle(
            "MarkTh", parent=ss["Normal"], **kw, fontSize=7.5, leading=10.6,
        ),
        "td": ParagraphStyle(
            "MarkTd", parent=ss["Normal"], **kw, fontSize=7.5, leading=10.6,
        ),
        "li": ParagraphStyle(
            "MarkLi", parent=ss["Normal"], **kw, fontSize=9.0, leading=13.2,
            spaceAfter=2,
        ),
        "source": ParagraphStyle(
            "MarkSource", parent=ss["Normal"], **kw, fontSize=8.0, leading=11.6,
            spaceAfter=3, leftIndent=12, firstLineIndent=-12,
        ),
    }


def _draw_chrome(canvas, doc, header: str, date: str, font: str) -> None:
    canvas.saveState()
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setFillColor(ACCENT)
    path = canvas.beginPath()
    ax, ay = MARGIN_L, PAGE_H - 11 * mm
    path.moveTo(ax, ay)
    path.lineTo(ax + 3.2 * mm, ay + 1.5 * mm)
    path.lineTo(ax, ay + 3.0 * mm)
    path.close()
    canvas.drawPath(path, fill=1, stroke=0)
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN_L, PAGE_H - 14 * mm, PAGE_W - MARGIN_R, PAGE_H - 14 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont(font, 7)
    canvas.drawString(MARGIN_L + 5 * mm, PAGE_H - 12.4 * mm, header)
    canvas.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 12.4 * mm, date)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN_L, 11 * mm, PAGE_W - MARGIN_R, 11 * mm)
    canvas.drawString(MARGIN_L, 7.2 * mm, "INVESTMENT RESEARCH")
    canvas.drawRightString(PAGE_W - MARGIN_R, 7.2 * mm, str(doc.page))
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
    # Bare URLs: wrap so they do not blow a line
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
    # First column is usually a short label; give remaining columns more wrap room.
    if ncols == 3:
        fracs = [0.22, 0.48, 0.30]
    elif ncols == 4:
        # numeric last columns slightly narrower
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
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), CELL),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.2),
                ("TOPPADDING", (0, 0), (-1, -1), 3.0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.0),
                ("GRID", (0, 0), (-1, -1), 0.25, RULE),
                ("LINEABOVE", (0, 0), (-1, 0), 0.55, ACCENT),
            ]
        )
    )
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
        if line.strip() == "---":
            story.append(Spacer(1, 2 * mm))
            i += 1
            continue
        if line.startswith("# "):
            story.append(Paragraph(_inline(line[2:].strip()), styles["h1"]))
            saw_h1 = True
            i += 1
            continue
        if line.startswith("## "):
            heading = Paragraph(_inline(line[3:].strip()), styles["h2"])
            story.append(heading)
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
            n = 1
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                text = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                items.append(
                    ListItem(
                        Paragraph(_inline(text), styles["source"] if "http" in text else styles["li"]),
                        leftIndent=10,
                    )
                )
                n += 1
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
        ):
            para.append(lines[i].strip())
            i += 1
        text = " ".join(para)
        style = styles["kicker"] if saw_h1 and len(story) <= 2 else styles["body"]
        story.append(Paragraph(_inline(text), style))
        saw_h1 = False
    return story


def build(input_path: Path, output_path: Path, header: str, date: str) -> None:
    font = _register_font()
    styles = _styles(font)
    md = input_path.read_text(encoding="utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def chrome(c, d):
        _draw_chrome(c, d, header, date, font)

    doc = BaseDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=header,
        author="Investment Research",
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
    p = argparse.ArgumentParser(description="Build MARK research PDF")
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
