# -*- coding: utf-8 -*-
"""S&P 500 多因子策略入口（yfinance, causal v3）。"""

from __future__ import annotations

import argparse

from us_multifactor.frozen import run_frozen


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="S&P500 weekly multi-factor Top-15 (causal v3)")
    p.add_argument("--search", action="store_true", help="Re-run causal finalize (v3)")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--defensive", action="store_true", help="Use vol-targeted defensive params (10%)")
    args = p.parse_args(argv)

    if args.search:
        from us_multifactor._finalize_v3 import main as finalize_main

        return int(finalize_main())

    params = {"start": args.start}
    if args.defensive:
        params.update(
            {
                "vol_target": 0.10,
                "lever_cap": 2.0,
                "use_vol_target": True,
                "dd_soft": -0.04,
                "dd_hard": -0.07,
            }
        )
    result = run_frozen(params=params)
    s = result["summary"]
    print(
        f"\nMETRICS: sharpe={s['sharpe']:.2f} cagr={s['cagr']:.1%} mdd={s['max_drawdown']:.1%} "
        f"| OOS sharpe={result['walk_forward'].get('oos', {}).get('sharpe', float('nan')):.2f} "
        f"| soft_hit={result.get('soft_hit')}"
    )
    # exit 0 for deliverable causal book (soft production targets)
    return 0 if result.get("soft_hit") else 2


if __name__ == "__main__":
    raise SystemExit(main())
