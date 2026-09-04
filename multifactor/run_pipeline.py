# -*- coding: utf-8 -*-
"""CLI entry for the A-share multi-factor strategy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .factors import DEFAULT_FACTOR_NAMES
from .data import REPO_ROOT
from .metrics import format_summary
from .pipeline import run_multifactor_pipeline, save_results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A-share monthly multi-factor strategy")
    p.add_argument("--root", type=str, default=str(REPO_ROOT))
    p.add_argument("--start", type=str, default="2010-01-01")
    p.add_argument("--end", type=str, default=None)
    p.add_argument("--universe", choices=["intersect", "csi300"], default="intersect")
    p.add_argument(
        "--combine",
        choices=["equal", "icir"],
        default="icir",
        help="Factor blend method",
    )
    p.add_argument(
        "--portfolio",
        choices=["long_short", "long_only"],
        default="long_short",
    )
    p.add_argument("--top-pct", type=float, default=0.2, help="Long-only top fraction")
    p.add_argument("--top-n", type=int, default=None)
    p.add_argument("--quantiles", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=20.0)
    p.add_argument("--no-industry-neutral", action="store_true")
    p.add_argument(
        "--out",
        type=str,
        default=str(REPO_ROOT / "multifactor_result"),
    )
    p.add_argument(
        "--factors",
        type=str,
        default=",".join(DEFAULT_FACTOR_NAMES),
        help="Comma-separated factor names",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    names = [x.strip() for x in args.factors.split(",") if x.strip()]
    print("Running multi-factor pipeline...")
    print(f"  universe={args.universe} combine={args.combine} portfolio={args.portfolio}")
    result = run_multifactor_pipeline(
        root=args.root,
        start=args.start,
        end=args.end,
        universe=args.universe,
        factor_names=names,
        combine_method=args.combine,
        portfolio_method=args.portfolio,
        n_quantiles=args.quantiles,
        long_only_top_pct=args.top_pct,
        top_n=args.top_n,
        cost_bps=args.cost_bps,
        neutralize_industry=not args.no_industry_neutral,
    )
    out = save_results(result, args.out)
    print(format_summary(result.backtest.summary))
    print("\nIC table:")
    print(result.ic_table.to_string(float_format=lambda x: f"{x:.4f}"))
    print(f"\nResults written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
