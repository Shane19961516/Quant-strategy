# -*- coding: utf-8 -*-
"""CLI: validate Alpha101 on SPX∪NDX for 5-day forward returns."""

from __future__ import annotations

import argparse

from alpha101.alphas import ALPHA_REGISTRY
from alpha101.pipeline import run_alpha101_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Validate Alpha101 on S&P500∪Nasdaq100 for 5-day forward returns"
    )
    p.add_argument("--start", default="2016-08-01")
    p.add_argument("--end", default=None)
    p.add_argument("--refresh", action="store_true", help="Re-download market data")
    p.add_argument(
        "--factors",
        default=",".join(ALPHA_REGISTRY.keys()),
        help="Comma-separated alpha names",
    )
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=5.0)
    p.add_argument("--max-tickers", type=int, default=None, help="Debug: limit universe")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--list", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for n in ALPHA_REGISTRY:
            print(n)
        return 0
    names = [x.strip() for x in args.factors.split(",") if x.strip()]
    result = run_alpha101_pipeline(
        start=args.start,
        end=args.end,
        refresh_data=args.refresh,
        factor_names=names,
        horizon=args.horizon,
        cost_bps=args.cost_bps,
        max_tickers=args.max_tickers,
        data_dir=args.data_dir,
        db_root=args.db,
        out_dir=args.out,
    )
    print(result.summary.to_string(float_format=lambda x: f"{x:.4f}"))
    print(f"\nReport: {result.out_dir / 'FACTOR_REPORT.md'}")
    print(f"DB: {result.store.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
