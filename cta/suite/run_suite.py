# -*- coding: utf-8 -*-
"""CLI：趋势/套利可落地策略测评。

  python -m cta.suite.run_suite --data-dir cta_data_akshare --plot
"""

from __future__ import annotations

import argparse
import sys

from .evaluate import run_research_suite


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="趋势/套利可落地策略测评（无杠杆、防过拟）")
    p.add_argument("--data-dir", default="cta_data_akshare")
    p.add_argument("--save-dir", default="cta_result_suite")
    p.add_argument("--contract-cache", default="cta_data_contracts")
    p.add_argument("--capital", type=float, default=1_000_000.0)
    p.add_argument("--plot", action="store_true", default=True)
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args(argv)

    out = run_research_suite(
        data_dir=args.data_dir,
        out_dir=args.save_dir,
        capital=args.capital,
        contract_cache=args.contract_cache,
        plot=args.plot and not args.no_plot,
    )
    sc = out["scorecard"]
    print("\n=== Scorecard (OOS headline) ===")
    cols = ["category", "name", "oos_sharpe", "oos_cagr", "oos_maxdd", "avg_corr_others", "stability_0_1"]
    print(sc[cols].to_string(index=False))
    ps = out["portfolio_summary"]
    print(
        f"\nPortfolio: ret={ps['total_return']:.2%} CAGR={ps['cagr']:.2%} "
        f"Sharpe={ps['sharpe']:.3f} MaxDD={ps['max_drawdown']:.2%}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
