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
    print("\n=== Scorecard (OOS / WF / stress) ===")
    cols = [
        "category",
        "name",
        "oos_sharpe",
        "oos_maxdd",
        "wf_mean_sharpe",
        "stress_oos_sharpe",
        "deployable",
    ]
    print(sc[cols].to_string(index=False))
    print(f"\nDeployable: {out.get('deploy_keys')}")
    de = out.get("deploy_invvol") or out["portfolio_summary"]
    print(
        f"Deploy inv-vol: ret={de['total_return']:.2%} CAGR={de['cagr']:.2%} "
        f"Sharpe={de['sharpe']:.3f} MaxDD={de['max_drawdown']:.2%}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
