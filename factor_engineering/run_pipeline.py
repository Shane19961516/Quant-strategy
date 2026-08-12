# -*- coding: utf-8 -*-
"""CLI entry for factor engineering research pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from .data import REPO_ROOT
from .factors import DEFAULT_FACTOR_NAMES, FACTOR_REGISTRY
from .pipeline import run_factor_engineering
from .report import save_report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A-share factor engineering research")
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2019-12-31")
    p.add_argument("--universe", default="intersect", choices=["intersect", "csi300"])
    p.add_argument(
        "--factors",
        default=",".join(DEFAULT_FACTOR_NAMES),
        help="Comma-separated factor names",
    )
    p.add_argument("--combine", default="icir", choices=["equal", "icir"])
    p.add_argument("--no-orthogonalize", action="store_true")
    p.add_argument("--cost-bps", type=float, default=20.0)
    p.add_argument(
        "--out",
        default=str(REPO_ROOT / "factor_engineering_result"),
        help="Output directory",
    )
    p.add_argument("--list-factors", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_factors:
        for name in sorted(FACTOR_REGISTRY):
            print(name)
        return 0

    names = [x.strip() for x in args.factors.split(",") if x.strip()]
    print(f"Running factor engineering: {len(names)} factors, {args.start}~{args.end}")
    result = run_factor_engineering(
        start=args.start,
        end=args.end,
        universe=args.universe,
        factor_names=names,
        combine_method=args.combine,
        orthogonalize=not args.no_orthogonalize,
        cost_bps=args.cost_bps,
    )
    out = save_report(result, Path(args.out))
    print(f"Selected: {result.selected_factors}")
    if result.backtest is not None:
        s = result.backtest.summary
        print(
            f"Composite LS  CAGR={s['cagr']:.2%}  Sharpe={s['sharpe']:.3f}  "
            f"MaxDD={s['max_drawdown']:.2%}"
        )
    print(f"Report written to {out}")
    print((out / "SUMMARY.txt").read_text(encoding="utf-8")[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
