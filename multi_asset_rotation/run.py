#!/usr/bin/env python3
"""多资产周度轮动策略：一键拉数、回测、归因与出图（校准版 15.86%）。"""

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
    latest_rebalance_instruction,
    perf_stats,
    sleeve_attribution,
    yearly_contribution_by_asset,
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
    plot_yearly_contribution_heatmap,
    plot_yearly_contribution_stacked,
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


def _write_report(summary: dict, yearly: pd.Series, asset_vs_strategy: pd.DataFrame, order: dict, sleeve: pd.DataFrame):
    lines = []
    s = summary["strategy"]
    lines.append("# Multi-Asset Rotation Report (Calibrated 15.86%)")
    lines.append("")
    lines.append(f"- Period: `{s['start']}` → `{s['end']}`")
    lines.append(f"- Ann return: **{s['ann_return']:.2%}**")
    lines.append(f"- Sharpe(rf=0): **{s['sharpe_rf0']:.3f}**")
    lines.append(f"- Max drawdown: **{s['max_drawdown']:.2%}**")
    lines.append(f"- Targets: `{summary['targets_check']}`")
    lines.append("")
    lines.append("## Yearly Strategy Returns")
    lines.append("")
    lines.append("| Year | Strategy |")
    lines.append("|---|---:|")
    for y, v in yearly.items():
        lines.append(f"| {y} | {v:.2%} |")
    lines.append("")
    lines.append("## Same-Year Asset Buy&Hold vs Strategy")
    lines.append("")
    cols = list(asset_vs_strategy.columns)
    header = "| Year | " + " | ".join(cols) + " |"
    sep = "|---|" + "|".join(["---:" ] * len(cols)) + "|"
    lines.append(header)
    lines.append(sep)
    for yr, row in asset_vs_strategy.iterrows():
        cells = []
        for c in cols:
            val = row[c]
            cells.append("" if pd.isna(val) else f"{val:.2%}")
        lines.append(f"| {yr} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Latest Rebalance Instruction")
    lines.append("")
    lines.append(f"- Signal date: `{order.get('signal_date')}`")
    lines.append(f"- Exec date: `{order.get('exec_date')}`")
    lines.append(f"- Turnover: **{order.get('turnover', 0):.2%}**")
    lines.append("")
    lines.append("| Code | Name | Current | Target | Delta | Action |")
    lines.append("|---|---|---:|---:|---:|---|")
    for r in order.get("orders", []):
        if abs(r["target_weight"]) < 1e-6 and abs(r["delta_weight"]) < 1e-6:
            continue
        lines.append(
            f"| {r['code']} | {r['name']} | {r['current_weight']:.2%} | "
            f"{r['target_weight']:.2%} | {r['delta_weight']:+.2%} | {r['action']} |"
        )
    lines.append("")
    lines.append("## Sleeve Attribution")
    lines.append("")
    lines.append(sleeve.to_string(index=False))
    lines.append("")
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main(force_download: bool = False):
    print("=" * 64)
    print("多资产轮动策略回测（校准版：双动量 + 波动目标 + 金丝雀）")
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
    yearly_contrib = yearly_contribution_by_asset(close, weights_daily)
    order = latest_rebalance_instruction(signal_w, weights_daily, close.index)

    # 月度
    daily = nav.pct_change().fillna(0.0)
    monthly = daily.groupby([daily.index.year, daily.index.month]).apply(lambda x: (1 + x).prod() - 1)
    monthly.index = pd.MultiIndex.from_tuples(monthly.index, names=["year", "month"])
    monthly_detail = monthly.rename("monthly_return").to_frame()
    monthly_detail["monthly_return_pct"] = monthly_detail["monthly_return"] * 100

    yearly_detail = yearly.rename("annual_return").to_frame()
    yearly_detail["annual_return_pct"] = yearly_detail["annual_return"] * 100
    yearly_analysis = yearly_detail.copy()

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
    (asset_yearly * 100).to_csv(OUT / "asset_yearly_returns_pct.csv")
    asset_vs_strategy = asset_yearly.copy()
    asset_vs_strategy["Strategy"] = yearly.reindex(asset_vs_strategy.index)
    asset_vs_strategy.to_csv(OUT / "asset_vs_strategy_yearly.csv")
    (asset_vs_strategy * 100).to_csv(OUT / "asset_vs_strategy_yearly_pct.csv")
    yearly_contrib.to_csv(OUT / "yearly_contribution_by_asset.csv")
    (yearly_contrib * 100).to_csv(OUT / "yearly_contribution_by_asset_pct.csv")

    with open(OUT / "latest_signal.json", "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=2)
    pd.DataFrame(order.get("orders", [])).to_csv(OUT / "latest_orders.csv", index=False)

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
        "latest_signal": {
            "signal_date": order.get("signal_date"),
            "exec_date": order.get("exec_date"),
            "turnover": order.get("turnover"),
        },
        "targets_check": {
            "sharpe_rf0>=2": bool(stats.get("sharpe_rf0", 0) >= 2),
            "ann_return>=15%": bool(stats.get("ann_return", 0) >= 0.15),
            "max_drawdown<=7%": bool(stats.get("max_drawdown", -1) >= -0.07),
            "all_years_nonneg": bool(float(yearly.min()) >= 0),
        },
        "params": PARAMS,
        "version": "v1_dual_momentum_vol_target_canary",
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
    plot_yearly_contribution_stacked(yearly_contrib, OUT / "yearly_contribution_stacked.png")
    plot_yearly_contribution_heatmap(yearly_contrib, OUT / "yearly_contribution_heatmap.png")
    heat = plot_monthly_heatmap(nav, OUT / "monthly_heatmap.png")
    heat.to_csv(OUT / "monthly_heatmap_matrix.csv")
    (heat * 100).to_csv(OUT / "monthly_return_matrix_pct.csv")
    heat.to_csv(OUT / "monthly_return_matrix.csv")
    plot_monthly_timeline(nav, OUT / "monthly_returns_timeline.png")
    season = plot_month_seasonality(nav, OUT / "month_seasonality.png")
    season.to_csv(OUT / "month_seasonality.csv", header=["avg_monthly_return"])

    _write_report(summary, yearly, asset_vs_strategy, order, sleeve)
    _copy_artifacts()

    def _pct(x):
        return f"{x:.2%}" if x is not None else "nan"

    print("\n----- 策略业绩（含 HK0005）-----")
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
    print("\n----- 同年各资产收益对比（买入持有）-----")
    name_map = {c: f"{c}:{UNIVERSE[c]['name']}" for c in asset_yearly.columns}
    show = asset_vs_strategy.rename(columns={**name_map, "Strategy": "Strategy"})
    print(show.map(lambda x: f"{x:.2%}" if pd.notna(x) else "").to_string())
    print("\n----- 策略分年资产贡献（持仓加权，百分点近似）-----")
    yc_show = yearly_contrib.drop(columns=["StrategyApprox"], errors="ignore").copy()
    yc_show = yc_show.rename(columns=name_map)
    print((yc_show * 100).map(lambda x: f"{x:.2f}").to_string())
    print("\n----- Sleeve归因 -----")
    print(sleeve.to_string(index=False))
    print("\n----- 资产累计贡献 -----")
    print(contrib.to_string(index=False))

    print("\n----- 最新调仓指令 -----")
    print("signal_date:", order.get("signal_date"), "exec_date:", order.get("exec_date"))
    print(f"turnover: {order.get('turnover', 0):.2%}")
    for r in order.get("orders", []):
        if abs(r["target_weight"]) < 1e-6 and abs(r["delta_weight"]) < 1e-6:
            continue
        print(
            f"  {r['action']:4s} {r['code']} {r['name']}: "
            f"{r['current_weight']:.2%} -> {r['target_weight']:.2%} ({r['delta_weight']:+.2%})"
        )
    print(f"\n结果已输出到: {OUT}")
    print(f"报告: {OUT / 'REPORT.md'}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force-download", action="store_true", help="强制重新下载行情")
    args = ap.parse_args()
    main(force_download=args.force_download)
