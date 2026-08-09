#!/usr/bin/env python3
"""多资产周度轮动策略：一键拉数、回测、归因与出图。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backtest import buy_hold_asset, equal_weight_benchmark, run_backtest
from config import PARAMS, SAFE, UNIVERSE
from data import build_panels, load_universe
from metrics import (
    contribution_attribution,
    perf_stats,
    sleeve_attribution,
    yearly_returns,
)
from plotting import plot_contribution, plot_drawdown, plot_nav, plot_weights
from strategy import generate_target_weights

OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(parents=True, exist_ok=True)


def main(force_download: bool = False):
    print("=" * 64)
    print("多资产轮动策略回测")
    print("标的:", {c: UNIVERSE[c]["name"] for c in UNIVERSE})
    print("参数:", PARAMS)
    print("=" * 64)

    raw = load_universe(force=force_download)
    close, open_ = build_panels(raw)
    print(f"[data] calendar {close.index.min().date()} -> {close.index.max().date()}, n={len(close)}")

    signal_w, info = generate_target_weights(close, PARAMS)
    nav, weights_daily, trades = run_backtest(close, signal_w, cost_bps=PARAMS["cost_bps"])

    # 基准
    ew = equal_weight_benchmark(close)
    bond = buy_hold_asset(close, SAFE)
    # 对齐起点
    start = nav.index[0]
    ew = ew / ew.loc[start]
    bond = bond / bond.loc[start]
    # 用黄金+美股代表风险资产
    gold = buy_hold_asset(close, "159934")
    gold = gold / gold.loc[start]
    spy = buy_hold_asset(close, "513500")
    spy = spy / spy.loc[start]

    stats = perf_stats(nav)
    stats_ew = perf_stats(ew.reindex(nav.index).ffill())
    stats_bond = perf_stats(bond.reindex(nav.index).ffill())

    contrib = contribution_attribution(close, weights_daily)
    sleeve = sleeve_attribution(weights_daily, close)
    yearly = yearly_returns(nav)

    # 保存表
    nav.to_csv(OUT / "nav.csv", header=True)
    weights_daily.to_csv(OUT / "weights_daily.csv")
    signal_w.to_csv(OUT / "weights_signal_friday.csv")
    if len(trades):
        trades.to_csv(OUT / "trades.csv")
    info["meta"].to_csv(OUT / "signal_meta.csv")
    contrib.to_csv(OUT / "attribution_asset.csv", index=False)
    sleeve.to_csv(OUT / "attribution_sleeve.csv", index=False)
    yearly.to_csv(OUT / "yearly_returns.csv", header=["return"])

    summary = {
        "strategy": stats,
        "equal_weight": stats_ew,
        "bond_only": stats_bond,
        "targets_check": {
            "sharpe_rf0>=2": bool(stats.get("sharpe_rf0", 0) >= 2),
            "ann_return>=15%": bool(stats.get("ann_return", 0) >= 0.15),
            "max_drawdown<=7%": bool(stats.get("max_drawdown", -1) >= -0.07),
        },
        "params": PARAMS,
    }
    with open(OUT / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    # 图
    plot_nav(
        nav,
        {
            "Equal Weight": ew,
            "Bond 159816": bond,
            "Gold 159934": gold,
            "S&P500 513500": spy,
        },
        OUT / "nav_curve.png",
    )
    plot_drawdown(nav, OUT / "drawdown.png")
    plot_weights(weights_daily, OUT / "weights.png")
    plot_contribution(contrib, OUT / "attribution.png")

    # 打印报告
    def _pct(x):
        return f"{x:.2%}" if x is not None else "nan"

    print("\n----- 策略业绩 -----")
    pct_keys = {"total_return", "ann_return", "ann_vol", "max_drawdown", "win_rate"}
    for k in [
        "start",
        "end",
        "total_return",
        "ann_return",
        "ann_vol",
        "sharpe_rf0",
        "sharpe_rf2pct",
        "max_drawdown",
        "calmar",
        "win_rate",
    ]:
        v = stats.get(k)
        if isinstance(v, float) and k in pct_keys:
            print(f"{k:16s}: {_pct(v)}")
        elif isinstance(v, float):
            print(f"{k:16s}: {v:.3f}")
        else:
            print(f"{k:16s}: {v}")

    print("\n目标达成:", summary["targets_check"])
    print("\n----- 分年收益 -----")
    print(yearly.apply(lambda x: f"{x:.2%}").to_string())
    print("\n----- Sleeve归因 -----")
    print(sleeve.to_string(index=False))
    print("\n----- 资产贡献 -----")
    print(contrib.to_string(index=False))
    print(f"\n结果已输出到: {OUT}")

    # 最新调仓建议
    last_sig = signal_w.index.max()
    last_w = signal_w.loc[last_sig]
    print("\n----- 最新周五信号权重（下周一开盘执行）-----")
    print("signal_date:", last_sig.date())
    for c, w in last_w.items():
        if w > 1e-6:
            print(f"  {c} {UNIVERSE[c]['name']}: {w:.2%}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force-download", action="store_true", help="强制重新下载行情")
    args = ap.parse_args()
    main(force_download=args.force_download)
