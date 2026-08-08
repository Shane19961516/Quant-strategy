# -*- coding: utf-8 -*-
"""CLI：趋势/套利可落地策略测评。

  python -m cta.suite.run_suite --data-dir cta_data_akshare --plot
"""

from __future__ import annotations

import argparse
import sys

from .breadth_sprint import run_breadth_sprint
from .edge_sprint import run_edge_sprint
from .evaluate import run_research_suite
from .return_target import run_return_target


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="趋势/套利可落地策略测评（无杠杆、防过拟）")
    p.add_argument("--data-dir", default="cta_data_akshare")
    p.add_argument("--save-dir", default="cta_result_suite")
    p.add_argument("--contract-cache", default="cta_data_contracts")
    p.add_argument("--capital", type=float, default=1_000_000.0)
    p.add_argument("--plot", action="store_true", default=True)
    p.add_argument("--no-plot", action="store_true")
    p.add_argument("--breadth", action="store_true", help="额外跑广度冲刺（拆袖层冲 Sharpe≥2）")
    p.add_argument("--skip-core", action="store_true", help="跳过核心六策略测评，只跑冲刺")
    args = p.parse_args(argv)

    if not args.skip_core:
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
        print(f"\nDeployable (Sharpe>=2 gate): {out.get('deploy_keys')}")

    if args.breadth or True:
        # 默认一并跑广度冲刺，回答 Sharpe≥2 是否可达
        b = run_breadth_sprint(
            data_dir=args.data_dir,
            out_dir=args.save_dir,
            capital=args.capital,
            contract_cache=args.contract_cache,
            plot=args.plot and not args.no_plot,
        )
        print(
            f"\nBreadth sprint: best_sleeve_oos={b['best_sleeve_oos_sharpe']:.3f} "
            f"best_book_oos={b['best_oos_sharpe']:.3f} hits>=2? {b['hits_target']}"
        )
        e = run_edge_sprint(
            data_dir=args.data_dir,
            out_dir=args.save_dir,
            capital=args.capital,
            contract_cache=args.contract_cache,
            plot=args.plot and not args.no_plot,
        )
        print(
            f"\nEdge sprint v3: best_edge_oos={e['best_edge_oos_sharpe']:.3f} "
            f"best_book_oos={e['best_oos_sharpe']:.3f} hits>=2? {e['hits_target']}"
        )
        rt = run_return_target(
            data_dir=args.data_dir,
            out_dir=args.save_dir,
            capital=args.capital,
            contract_cache=args.contract_cache,
            plot=args.plot and not args.no_plot,
        )
        rec = rt.get("recommend")
        if rec:
            b = rt["books"][rec]
            print(
                f"\nReturn-target recommend={rec}: "
                f"OOS CAGR={b['oos']['cagr']:.2%} Sharpe={b['oos']['sharpe']:.3f} "
                f"MaxDD={b['oos']['max_drawdown']:.2%}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
