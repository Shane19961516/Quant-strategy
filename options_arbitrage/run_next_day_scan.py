#!/usr/bin/env python3
"""
Next-session short strangle scan (v2.0.0).

Reads:
  - docs/方法与口径.md
  - docs/交易所规则管理.md
  - docs/报告规范.md

Usage:
  python run_next_day_scan.py
  python run_next_day_scan.py --client-margin-addon 0.05 --equity 500000
  python run_next_day_scan.py --csv-dir ./user_snapshots   # future: user CSV
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.next_day_screener import run_next_session_scan
from report.next_day_report import build_next_session_report


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Next-session short strangle scan v2")
    p.add_argument("--equity", type=float, default=None, help="Account equity for sizing")
    p.add_argument("--client-margin-addon", type=float, default=None, help="Broker margin addon ratio")
    p.add_argument("--as-of", type=str, default=None, help="Scan as-of date YYYY-MM-DD")
    p.add_argument("--output-dir", type=str, default=str(ROOT / "output"))
    p.add_argument("--dte-min", type=int, default=30)
    p.add_argument("--dte-max", type=int, default=60)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results, meta = run_next_session_scan(
        as_of=as_of,
        account_equity=args.equity,
        client_margin_addon=args.client_margin_addon,
        dte_min=args.dte_min,
        dte_max=args.dte_max,
    )
    rows = [r.to_dict() for r in results]
    report = build_next_session_report(meta, rows)

    md_path = out_dir / "next_session_report.md"
    json_path = out_dir / "next_session_scan.json"
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps({"meta": meta, "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(report)
    print(f"\n[saved] {md_path}")
    print(f"[saved] {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
