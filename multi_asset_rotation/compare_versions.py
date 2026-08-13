#!/usr/bin/env python3
"""对比旧版硬切换 v1 与最终版（非对称调解+YTD油门）。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backtest import run_backtest
from config import PARAMS, STRATEGY_START
from data import build_panels, load_universe
from metrics import perf_stats, yearly_returns
from plotting import plot_yearly_compare
from strategy import generate_target_weights

OUT = Path(__file__).resolve().parent / "output"

# 旧版硬切换参数（金丝雀100%债券、无中枢/YTD）
V1_PARAMS = {
    **PARAMS,
    "vol_budget": 0.075,
    "active_tilt": 1.0,
    "max_sleeve_dev": 1.0,
    "weight_ema": 1.0,
    "min_bond": 0.0,
    "bond_canary_boost": 1.0,
    "canary_bond_floor": 1.0,
    "defense_skip_center": True,
    "max_single_asset": 0.55,
    "rebalance_thresh": 0.25,
    "use_ytd_throttle": False,
    "neutral_sleeve": {"bond": 0.0, "gold": 0.34, "cn": 0.33, "us": 0.33},
}


def _pack(s, y):
    return {
        "ann_return": float(s["ann_return"]),
        "sharpe_rf0": float(s["sharpe_rf0"]),
        "max_drawdown": float(s["max_drawdown"]),
        "ann_vol": float(s["ann_vol"]),
        "yearly_std": float(y.std()),
        "yearly_min": float(y.min()),
        "yearly_max": float(y.max()),
        "by_year": {str(k): float(v) for k, v in y.items()},
    }


def main():
    raw = load_universe(force=False)
    close, _ = build_panels(raw)
    close = close.loc[STRATEGY_START:]

    rows = {}
    yearly_map = {}
    for name, p in [("v1_hard_switch", V1_PARAMS), ("final_ytd_mediation", PARAMS)]:
        w, _ = generate_target_weights(close, p)
        nav, _, _ = run_backtest(close, w, cost_bps=p["cost_bps"])
        s = perf_stats(nav)
        y = yearly_returns(nav)
        rows[name] = _pack(s, y)
        yearly_map[name] = y
        print(
            f"{name}: ann={s['ann_return']:.2%} sh={s['sharpe_rf0']:.3f} "
            f"mdd={s['max_drawdown']:.2%} ystd={y.std():.2%} ymin={y.min():.2%}"
        )
        print(" ", " | ".join(f"{i}:{v:.1%}" for i, v in y.items()))

    plot_yearly_compare(yearly_map, OUT / "yearly_compare_v1_vs_final.png")
    cmp = pd.DataFrame(rows).T
    cmp.to_csv(OUT / "version_compare.csv")
    with open(OUT / "version_compare.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    v1, fin = rows["v1_hard_switch"], rows["final_ytd_mediation"]
    print("\nDelta final - v1:")
    for k in ["ann_return", "sharpe_rf0", "max_drawdown", "yearly_std", "yearly_min"]:
        print(f"  {k}: {fin[k] - v1[k]:+.4f}")


if __name__ == "__main__":
    main()
