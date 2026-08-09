#!/usr/bin/env python3
"""多资产周度轮动策略：一键拉数、回测、归因与出图（最终版）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from backtest import buy_hold_asset, equal_weight_benchmark, run_backtest
from config import PARAMS, SAFE, UNIVERSE
from data import build_panels, load_universe
from metrics import (
    asset_yearly_returns,
    contribution_attribution,
    perf_stats,
    sleeve_attribution,
    yearly_returns,
)
from plotting import (
    plot_asset_yearly_bars,
    plot_asset_yearly_heatmap,
    plot_contribution,
    plot_drawdown,
    plot_month_seasonality,
    plot_monthly_heatmap,
    plot_monthly_timeline,
    plot_nav,
    plot_weights,
    plot_yearly_bars,
)
from strategy import generate_target_weights

OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(parents=True, exist_ok=True)
ART = Path("/opt/cursor/artifacts")


def _copy_artifacts():
    if not ART.exists():
        return
    for p in OUT.glob("*.png"):
        shutil.copy2(p, ART / p.name)


def main(force_download: bool = False):
    print("=" * 64)
    print("多资产轮动策略回测（最终版：非对称权重调解 + YTD油门）")
    print("标的:", {c: UNIVERSE[c]["name"] for c in UNIVERSE})
    print("参数:", PARAMS)
    print("=" * 64)

    raw = load_universe(force=force_download)
    close, open_ = build_panels(raw)
    print(f"[data] calendar {close.index.min().date()} -> {close.index.max().date()}, n={len(close)}")

    signal_w, info = generate_target_weights(close, PARAMS)
    nav, weights_daily, trades = run_backtest(close, signal_w, cost_bps=PARAMS["cost_bps"])

    ew = equal_weight_benchmark(close)
    bond = buy_hold_asset(close, SAFE)
    start = nav.index[0]
    ew = ew / ew.loc[start]
    bond = bond / bond.loc[start]
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
    asset_yearly = asset_yearly_returns(close)

    # 月度
    daily = nav.pct_change().fillna(0.0)
    monthly = daily.groupby([daily.index.year, daily.index.month]).apply(lambda x: (1 + x).prod() - 1)
    monthly.index = pd.MultiIndex.from_tuples(monthly.index, names=["year", "month"])
    monthly_detail = monthly.rename("monthly_return").to_frame()
    monthly_detail["monthly_return_pct"] = monthly_detail["monthly_return"] * 100

    yearly_detail = yearly.rename("annual_return").to_frame()
    yearly_detail["annual_return_pct"] = yearly_detail["annual_return"] * 100
    yearly_analysis = yearly_detail.copy()
    yearly_analysis.attrs["yearly_std"] = float(yearly.std())
    yearly_analysis.attrs["yearly_min"] = float(yearly.min())
    yearly_analysis.attrs["yearly_max"] = float(yearly.max())

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
    yearly_detail.to_csv(OUT / "yearly_returns_detail.csv")
    yearly_analysis.to_csv(OUT / "yearly_analysis.csv")
    monthly_detail.to_csv(OUT / "monthly_returns_detail.csv")
    asset_yearly.to_csv(OUT / "asset_yearly_returns.csv")
    asset_yearly_pct = asset_yearly * 100
    asset_yearly_pct.to_csv(OUT / "asset_yearly_returns_pct.csv")
    # 附带策略列，便于同年对比
    asset_vs_strategy = asset_yearly.copy()
    asset_vs_strategy["Strategy"] = yearly.reindex(asset_vs_strategy.index)
    asset_vs_strategy.to_csv(OUT / "asset_vs_strategy_yearly.csv")
    (asset_vs_strategy * 100).to_csv(OUT / "asset_vs_strategy_yearly_pct.csv")

    summary = {
        "strategy": stats,
        "equal_weight": stats_ew,
        "bond_only": stats_bond,
        "yearly": {
            "std": float(yearly.std()),
            "min": float(yearly.min()),
            "max": float(yearly.max()),
            "mean": float(yearly.mean()),
            "by_year": {str(k): float(v) for k, v in yearly.items()},
        },
        "targets_check": {
            "sharpe_rf0>=2": bool(stats.get("sharpe_rf0", 0) >= 2),
            "ann_return>=15%": bool(stats.get("ann_return", 0) >= 0.15),
            "max_drawdown<=7%": bool(stats.get("max_drawdown", -1) >= -0.07),
            "all_years_nonneg": bool(float(yearly.min()) >= 0),
        },
        "params": PARAMS,
        "version": "final_asymmetric_mediation_ytd_throttle",
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
    plot_yearly_bars(yearly, OUT / "yearly_returns_bar.png")
    plot_asset_yearly_bars(asset_yearly, OUT / "asset_yearly_compare.png", strategy_yearly=yearly)
    plot_asset_yearly_heatmap(asset_yearly, OUT / "asset_yearly_heatmap.png", strategy_yearly=yearly)
    heat = plot_monthly_heatmap(nav, OUT / "monthly_heatmap.png")
    heat.to_csv(OUT / "monthly_heatmap_matrix.csv")
    (heat * 100).to_csv(OUT / "monthly_return_matrix_pct.csv")
    heat.to_csv(OUT / "monthly_return_matrix.csv")
    plot_monthly_timeline(nav, OUT / "monthly_returns_timeline.png")
    season = plot_month_seasonality(nav, OUT / "month_seasonality.png")
    season.to_csv(OUT / "month_seasonality.csv", header=["avg_monthly_return"])

    # 版本对比（v1硬切换 vs 最终版）
    try:
        from compare_versions import main as compare_main

        compare_main()
    except Exception as e:
        print("[warn] version compare skipped:", e)

    _copy_artifacts()

    def _pct(x):
        return f"{x:.2%}" if x is not None else "nan"

    print("\n----- 策略业绩（最终版）-----")
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
    print(
        f"分年统计: mean={yearly.mean():.2%} std={yearly.std():.2%} "
        f"min={yearly.min():.2%} max={yearly.max():.2%}"
    )
    print("\n----- 分年收益 -----")
    print(yearly.apply(lambda x: f"{x:.2%}").to_string())
    print("\n----- 同年各资产收益对比 -----")
    name_map = {c: f"{c}:{UNIVERSE[c]['name']}" for c in asset_yearly.columns}
    show = asset_vs_strategy.rename(columns={**name_map, "Strategy": "Strategy"})
    print(show.map(lambda x: f"{x:.2%}" if pd.notna(x) else "").to_string())
    print("\n----- Sleeve归因 -----")
    print(sleeve.to_string(index=False))
    print("\n----- 资产贡献 -----")
    print(contrib.to_string(index=False))
    print(f"\n结果已输出到: {OUT}")

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
