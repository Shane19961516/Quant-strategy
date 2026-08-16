#!/usr/bin/env python3
"""Local desktop web app: ticker -> K-line + MARK-style fundamental memo + export."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.market_data import load_market_bundle  # noqa: E402
from services.report import build_mark_report  # noqa: E402
from services.ticker import resolve_ticker  # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")
OUTPUT = ROOT / "output"
OUTPUT.mkdir(parents=True, exist_ok=True)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "mark-fundamentals-web"})


@app.post("/api/analyze")
def analyze():
    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or request.args.get("code") or "").strip()
    range_ = (payload.get("range") or "1y").strip() or "1y"
    try:
        resolved = resolve_ticker(code)
        bundle = load_market_bundle(resolved.yahoo, range_=range_)
        report = build_mark_report(resolved.to_dict(), bundle["snapshot"], bundle["chart"]["candles"])
        return jsonify(
            {
                "ok": True,
                "resolved": resolved.to_dict(),
                "snapshot": bundle["snapshot"],
                "candles": bundle["chart"]["candles"],
                "report": report,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/export/markdown")
def export_markdown():
    payload = request.get_json(silent=True) or {}
    md = payload.get("markdown") or ""
    code = (payload.get("code") or "report").strip().replace("/", "_")
    if not md:
        return jsonify({"ok": False, "error": "没有可导出的报告内容"}), 400
    data = md.encode("utf-8")
    return send_file(
        io.BytesIO(data),
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=f"{code}_fundamental_report.md",
    )


@app.post("/api/export/pdf")
def export_pdf():
    import subprocess
    from datetime import date as date_cls

    payload = request.get_json(silent=True) or {}
    md = payload.get("markdown") or ""
    code = (payload.get("code") or "report").strip().replace("/", "_")
    title = payload.get("title") or f"{code} INVESTMENT RESEARCH"
    report_date = (payload.get("date") or "").strip() or date_cls.today().isoformat()
    if not md:
        return jsonify({"ok": False, "error": "没有可导出的报告内容"}), 400

    # Prefer project PDF builder if present; else minimal reportlab fallback.
    skill_pdf = (
        ROOT.parents[2] / ".cursor" / "skills" / "mark-alpha-research" / "scripts" / "build_mark_research_pdf.py"
    )
    local_pdf = ROOT / "scripts" / "build_mark_research_pdf.py"
    builder = local_pdf if local_pdf.exists() else skill_pdf

    out_path = OUTPUT / f"{code}_fundamental_report.pdf"
    md_path = OUTPUT / f"{code}_fundamental_report.md"
    md_path.write_text(md, encoding="utf-8")

    if builder.exists():
        subprocess.run(
            [
                sys.executable,
                str(builder),
                "--input",
                str(md_path),
                "--output",
                str(out_path),
                "--header",
                str(title)[:80],
                "--date",
                report_date,
            ],
            check=True,
        )
    else:
        _write_simple_pdf(md, out_path, title)

    return send_file(out_path, mimetype="application/pdf", as_attachment=True, download_name=out_path.name)


def _write_simple_pdf(markdown: str, out_path: Path, title: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font = "Helvetica"
    for candidate in (
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
    ):
        if candidate.exists():
            try:
                pdfmetrics.registerFont(TTFont("CN", str(candidate), subfontIndex=0 if candidate.suffix.lower() == ".ttc" else 0))
                font = "CN"
                break
            except Exception:  # noqa: BLE001
                continue

    styles = getSampleStyleSheet()
    body = ParagraphStyle("BodyCN", parent=styles["Normal"], fontName=font, fontSize=10, leading=14)
    h = ParagraphStyle("HeadCN", parent=styles["Heading1"], fontName=font, fontSize=14, leading=18)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, title=title)
    story = [Paragraph(title.replace("<", "&lt;"), h), Spacer(1, 12)]
    for line in markdown.splitlines():
        safe = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("|", "｜")
        )
        if not safe.strip():
            story.append(Spacer(1, 6))
            continue
        story.append(Paragraph(safe, body))
    doc.build(story)


def main() -> None:
    # Copy PDF builder next to app for offline desktop installs when available.
    src = ROOT.parents[2] / ".cursor" / "skills" / "mark-alpha-research" / "scripts" / "build_mark_research_pdf.py"
    dst_dir = ROOT / "scripts"
    dst_dir.mkdir(exist_ok=True)
    dst = dst_dir / "build_mark_research_pdf.py"
    if src.exists() and not dst.exists():
        dst.write_bytes(src.read_bytes())

    print("基本面分析 Web: http://127.0.0.1:8765")
    print("健康检查: http://127.0.0.1:8765/api/health")
    print("请保持此窗口开启；关闭窗口即停止服务。")
    try:
        app.run(host="127.0.0.1", port=8765, debug=False, use_reloader=False)
    except OSError as exc:
        print(f"启动失败: {exc}")
        print("若提示端口被占用，请关闭占用 8765 的旧进程后重试。")
        raise


if __name__ == "__main__":
    main()
