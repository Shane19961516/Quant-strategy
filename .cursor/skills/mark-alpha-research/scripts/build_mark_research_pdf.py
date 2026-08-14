#!/usr/bin/env python3
"""Build a MARK-style A4 research PDF from Markdown."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageTemplate,
    Paragraph,
    Preformatted,
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


def _register_font() -> tuple[str, str]:
    candidates = [
        ("KingHwa OldSong", [
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "KingHwa_OldSong.ttf",
            Path("/mnt/c/Windows/Fonts/KingHwa_OldSong.ttf"),
            Path.home() / "Library/Fonts/KingHwa_OldSong.ttf",
        ]),
        ("SimSun", [
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
            Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
        ]),
    ]
    for family, paths in candidates:
        for path in paths:
            if path.exists():
                try:
                    if path.suffix.lower() == ".ttc":
                        pdfmetrics.registerFont(TTFont("MarkBody", str(path), subfontIndex=0))
                    else:
                        pdfmetrics.registerFont(TTFont("MarkBody", str(path)))
                    return "MarkBody", "MarkBody"
                except Exception:
                    continue
    return "Times-Roman", "Times-Bold"


def _styles(body: str, bold: str) -> dict:
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle(
            "MarkH1", parent=ss["Heading1"], fontName=bold, fontSize=16,
            leading=22, textColor=INK, spaceAfter=8, spaceBefore=0,
        ),
        "h2": ParagraphStyle(
            "MarkH2", parent=ss["Heading2"], fontName=bold, fontSize=12,
            leading=16, textColor=INK, spaceBefore=11, spaceAfter=6,
            borderPadding=0,
        ),
        "h3": ParagraphStyle(
            "MarkH3", parent=ss["Heading3"], fontName=bold, fontSize=10.5,
            leading=14, textColor=ACCENT, spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "MarkBody", parent=ss["BodyText"], fontName=body, fontSize=9.2,
            leading=13.4, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=5,
        ),
        "meta": ParagraphStyle(
            "MarkMeta", parent=ss["BodyText"], fontName=body, fontSize=8.4,
            leading=12, textColor=MUTED, spaceAfter=6,
        ),
        "code": ParagraphStyle(
            "MarkCode", parent=ss["Code"], fontName=body, fontSize=8.0,
            leading=11.2, textColor=INK, backColor=CELL, leftIndent=4,
            rightIndent=4, spaceBefore=3, spaceAfter=7,
        ),
        "th": ParagraphStyle(
            "MarkTh", parent=ss["BodyText"], fontName=bold, fontSize=7.6,
            leading=10.4, textColor=INK, alignment=TA_LEFT,
        ),
        "td": ParagraphStyle(
            "MarkTd", parent=ss["BodyText"], fontName=body, fontSize=7.6,
            leading=10.4, textColor=INK, alignment=TA_LEFT,
        ),
        "li": ParagraphStyle(
            "MarkLi", parent=ss["BodyText"], fontName=body, fontSize=9.0,
            leading=13.0, textColor=INK, leftIndent=2, spaceAfter=2,
        ),
        "footer": ParagraphStyle(
            "MarkFooter", parent=ss["Normal"], fontName=body, fontSize=7.2,
            leading=9, textColor=MUTED, alignment=TA_RIGHT,
        ),
        "header": ParagraphStyle(
            "MarkHeader", parent=ss["Normal"], fontName=body, fontSize=7.2,
            leading=9, textColor=MUTED, alignment=TA_LEFT,
        ),
    }


def _draw_chrome(canvas, doc, header: str, date: str) -> None:
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
    canvas.setFont("MarkBody" if "MarkBody" in pdfmetrics.getRegisteredFontNames() else "Times-Roman", 7)
    canvas.drawString(MARGIN_L + 5 * mm, PAGE_H - 12.4 * mm, header)
    canvas.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 12.4 * mm, date)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN_L, 11 * mm, PAGE_W - MARGIN_R, 11 * mm)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN_L, 7.2 * mm, "INVESTMENT RESEARCH")
    canvas.drawRightString(PAGE_W - MARGIN_R, 7.2 * mm, str(doc.page))
    canvas.restoreState()


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<font color='#5D5A52'>\1</font>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<link href='\2' color='#D9362B'><u>\1</u></link>", text)
    return text


def _split_row(line: str) -> list[str]:
    parts = [c.strip() for c in line.strip().strip("|").split("|")]
    return parts


def _is_sep(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{3,}", line))


def parse_markdown(md: str, styles: dict) -> list:
    lines = md.replace("\r\n", "\n").split("\n")
    story: list = []
    i = 0
    in_code = False
    code_lines: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                story.append(Preformatted("\n".join(code_lines) or " ", styles["code"]))
                code_lines = []
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
            i += 1
            continue
        if line.startswith("## "):
            story.append(Paragraph(_inline(line[3:].strip()), styles["h2"]))
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
            ncols = max(len(r) for r in rows)
            norm = [r + [""] * (ncols - len(r)) for r in rows]
            usable = PAGE_W - MARGIN_L - MARGIN_R
            col_w = usable / ncols
            data = []
            for ri, row in enumerate(norm):
                st = styles["th"] if ri == 0 else styles["td"]
                data.append([Paragraph(_inline(cell), st) for cell in row])
            table = Table(data, colWidths=[col_w] * ncols, repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), CELL),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("FONTNAME", (0, 0), (-1, 0), styles["th"].fontName),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
                ("TOPPADDING", (0, 0), (-1, -1), 3.2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
                ("GRID", (0, 0), (-1, -1), 0.25, RULE),
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, ACCENT),
            ]))
            story.append(Spacer(1, 1.5 * mm))
            story.append(KeepTogether([table]))
            story.append(Spacer(1, 2.5 * mm))
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(ListItem(Paragraph(_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])), styles["li"])))
                i += 1
            story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12, bulletFontName=styles["body"].fontName, bulletFontSize=8, bulletColor=ACCENT))
            story.append(Spacer(1, 1.5 * mm))
            continue
        if not line.strip():
            i += 1
            continue
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("#") and not lines[i].strip().startswith("|") and not lines[i].strip().startswith("```") and not re.match(r"^\s*[-*]\s+", lines[i]) and lines[i].strip() != "---":
            para.append(lines[i].strip())
            i += 1
        text = " ".join(para)
        style = styles["meta"] if story and getattr(story[-1], "style", None) and getattr(story[-1].style, "name", "") == "MarkH1" else styles["body"]
        # First non-heading after title uses meta if it looks like a kicker
        story.append(Paragraph(_inline(text), styles["body"]))
    return story


def build(input_path: Path, output_path: Path, header: str, date: str) -> None:
    body, bold = _register_font()
    styles = _styles(body, bold)
    md = input_path.read_text(encoding="utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def chrome(c, d):
        _draw_chrome(c, d, header, date)

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
    frame = Frame(MARGIN_L, MARGIN_B, PAGE_W - MARGIN_L - MARGIN_R, PAGE_H - MARGIN_T - MARGIN_B, id="body")
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
