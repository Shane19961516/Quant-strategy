#!/usr/bin/env python3
"""
End-to-end next-session short-strangle pipeline.

Steps:
  1) Ensure IV history (>=252d) via seed / CSV
  2) Fetch live chains (or --csv-dir snapshots)
  3) Screen with account params
  4) Write next_session_report.md / json

Usage:
  python run_e2e.py --equity 500000 --client-margin-addon 0.05
  python run_e2e.py --equity 500000 --client-margin-addon 0.05 --seed-days 260
  python run_e2e.py --csv-dir ./data/snapshots/20260820 --equity 500000 --client-margin-addon 0.05
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.iv_history_store import IVHistoryStore
from core.next_day_screener import evaluate_product, run_next_session_scan
from data_fetcher.csv_loader import load_snapshot_bundle
from data_fetcher.option_universe import load_universe, universe_product_codes
from report.next_day_report import build_next_session_report
from core.next_day_screener import next_trading_day
from core.exchange_rules import load_rules_meta
from dataclasses import asdict


def ensure_iv_history(products: list[str], days: int, seed: bool) -> dict[str, int]:
    store = IVHistoryStore()
    status = {}
    missing = []
    for p in products:
        s = store.load(p)
        n = s.n if s else 0
        status[p] = n
        if n < 252:
            missing.append(p)
    if missing and seed:
        czce = [p for p in missing if p.upper() in {
            "SR", "CF", "TA", "MA", "RM", "OI", "PK", "ZC", "AP", "CJ", "FG", "SA", "UR",
            "SF", "SM", "PF", "PR", "PL", "PX", "SH",
        }]
        if czce:
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "seed_iv_history.py"),
                "--days",
                str(days),
                "--products",
                ",".join(czce),
                "--workers",
                "16",
            ]
            print("Seeding IV history:", " ".join(cmd), flush=True)
            subprocess.check_call(cmd, cwd=str(ROOT))
        dce = [p for p in missing if p.lower() in {"m", "c", "i", "pg"}]
        if dce:
            # need current ATM — seed script uses defaults; refresh after live fetch ideally
            subprocess.check_call(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "seed_iv_history.py"),
                    "--seed-dce-csv",
                    "--days",
                    str(days),
                    "--products",
                    "",
                ],
                cwd=str(ROOT),
            )
        for p in products:
            s = store.load(p)
            status[p] = s.n if s else 0
    return status


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="E2E short-strangle next-session pipeline")
    p.add_argument("--equity", type=float, required=True)
    p.add_argument("--client-margin-addon", type=float, required=True)
    p.add_argument("--seed-days", type=int, default=260)
    p.add_argument("--no-seed", action="store_true")
    p.add_argument("--csv-dir", type=str, default=None)
    p.add_argument("--as-of", type=str, default=None)
    p.add_argument("--output-dir", type=str, default=str(ROOT / "output"))
    p.add_argument(
        "--products-seed",
        type=str,
        default="all",
        help="Comma-separated product codes, or 'all' for full universe",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    if args.products_seed.strip().lower() == "all":
        products = universe_product_codes()
    else:
        products = [x.strip() for x in args.products_seed.split(",") if x.strip()]

    iv_status = ensure_iv_history(products, args.seed_days, seed=not args.no_seed)
    print("IV history status:", iv_status, flush=True)
    print("[e2e] starting live/csv scan (full universe may take 5–10 minutes) …", flush=True)

    if args.csv_dir:
        snaps, manifest = load_snapshot_bundle(Path(args.csv_dir))
        results = [
            evaluate_product(
                s,
                as_of=as_of,
                account_equity=args.equity,
                client_margin_addon=args.client_margin_addon,
            )
            for s in snaps
        ]
        meta = {
            "report_version": "report-v2.0.0",
            "methods_version": "methods-v2.0.0",
            "rules_version": load_rules_meta().get("rules_version"),
            "quote_asof": manifest.quote_asof,
            "target_session": next_trading_day(as_of).isoformat(),
            "data_source": manifest.data_source,
            "model": "Black-76 (American risk flagged)",
            "account_equity": args.equity,
            "client_margin_addon": args.client_margin_addon,
            "iv_history_status": iv_status,
            "fetch": asdict(manifest),
            "counts": {
                "scanned": len(results),
                "推荐": sum(1 for r in results if r.classification == "推荐"),
                "观察": sum(1 for r in results if r.classification == "观察"),
                "排除": sum(1 for r in results if r.classification == "排除"),
            },
        }
        order = {"推荐": 0, "观察": 1, "排除": 2}
        results.sort(key=lambda r: (order[r.classification], -(r.vrp or -999), -(r.technical_score or 0)))
    else:
        results, meta = run_next_session_scan(
            as_of=as_of,
            account_equity=args.equity,
            client_margin_addon=args.client_margin_addon,
        )
        meta["client_margin_addon"] = args.client_margin_addon
        meta["iv_history_status"] = iv_status

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r.to_dict() for r in results]
    report = build_next_session_report(meta, rows)
    (out_dir / "next_session_report.md").write_text(report, encoding="utf-8")
    (out_dir / "next_session_scan.json").write_text(
        json.dumps({"meta": meta, "results": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(report)
    print(f"\n[saved] {out_dir / 'next_session_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
