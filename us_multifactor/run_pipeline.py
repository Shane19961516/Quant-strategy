# -*- coding: utf-8 -*-
"""CLI: S&P 500 multi-factor strategy (yfinance)."""

from __future__ import annotations

import argparse

from us_multifactor.pipeline import run_us_multifactor_pipeline


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--per-category", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--max-names", type=int, default=None, help="Debug: limit universe size")
    p.add_argument("--force-prices", action="store_true")
    p.add_argument("--force-fundamentals", action="store_true")
    p.add_argument("--skip-optimize", action="store_true")
    args = p.parse_args(argv)

    result = run_us_multifactor_pipeline(
        start=args.start,
        end=args.end,
        top_n=args.top_n,
        factors_per_category=args.per_category,
        cost_bps=args.cost_bps,
        max_names=args.max_names,
        force_prices=args.force_prices,
        force_fundamentals=args.force_fundamentals,
        skip_optimize=args.skip_optimize,
    )
    s = result.backtest.summary
    print(
        f"\nDELIVERY CHECK: sharpe>={3}? {s['sharpe']>=3} | "
        f"cagr>={0.3}? {s['cagr']>=0.3} | mdd<=10%? {s['max_drawdown']>=-0.10} | "
        f"feasible={result.best_trial.feasible}"
    )
    return 0 if result.best_trial.feasible else 2


if __name__ == "__main__":
    raise SystemExit(main())
