# -*- coding: utf-8 -*-
"""S&P 500 多因子策略入口（yfinance）。

默认复现已优化冻结参数；加 --search 可重新寻优。
"""

from __future__ import annotations

import argparse

from us_multifactor.frozen import run_frozen


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="S&P500 weekly multi-factor Top-10 strategy")
    p.add_argument("--search", action="store_true", help="Re-run parameter search instead of frozen config")
    p.add_argument("--start", default="2016-01-01")
    args = p.parse_args(argv)

    if args.search:
        from us_multifactor._deliver import main as deliver_main

        return int(deliver_main())

    result = run_frozen(params={"start": args.start})
    s = result["summary"]
    print(
        f"\nDELIVERY CHECK: sharpe>=3? {s['sharpe']>=3} | "
        f"cagr>=30%? {s['cagr']>=0.3} | mdd<=10%? {s['max_drawdown']>=-0.1000001} | "
        f"hit={result['hit']}"
    )
    return 0 if result["hit"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
