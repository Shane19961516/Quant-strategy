# -*- coding: utf-8 -*-
"""End-to-end US multi-factor pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .backtest import BacktestResult, combine_selected_scores, run_weekly_backtest
from .data_yfinance import DATA_DIR, REPO_ROOT, load_market_bundle
from .factors import (
    CATEGORIES,
    attach_fundamental_factors,
    build_price_factors,
    lag_factors,
    resample_factors_weekly,
    select_top_factors_per_category,
)
from .optimize import OptTrial, optimize_to_targets


@dataclass
class USPipelineResult:
    selected: Dict[str, List[str]]
    ic_summary: pd.DataFrame
    best_trial: OptTrial
    backtest: BacktestResult
    top_trials: List[OptTrial]
    weekly_returns: pd.DataFrame
    composite: pd.DataFrame


def run_us_multifactor_pipeline(
    start: str = "2016-01-01",
    end: Optional[str] = None,
    top_n: int = 10,
    factors_per_category: int = 5,
    cost_bps: float = 10.0,
    cache_dir: Path | None = None,
    out_dir: Path | None = None,
    max_names: Optional[int] = None,
    force_prices: bool = False,
    force_fundamentals: bool = False,
    skip_optimize: bool = False,
) -> USPipelineResult:
    cache_dir = Path(cache_dir) if cache_dir else DATA_DIR
    out_dir = Path(out_dir) if out_dir else REPO_ROOT / "us_multifactor_result"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== Loading market bundle (yfinance) ===")
    bundle = load_market_bundle(
        start=start,
        end=end,
        cache_dir=cache_dir,
        force_prices=force_prices,
        force_fundamentals=force_fundamentals,
        max_names=max_names,
    )
    prices = bundle["prices"]
    adj = prices["adj_close"]
    volume = prices["volume"]
    spy = bundle["spy"]
    funds = bundle["fundamentals"]
    tickers = bundle["tickers"]
    print(f"Universe: {len(tickers)} names, dates {adj.index[0].date()} → {adj.index[-1].date()}")

    print("=== Building factors ===")
    fmap = build_price_factors(adj[tickers], volume=volume.reindex(columns=tickers), spy=spy)
    fmap = attach_fundamental_factors(fmap, adj[tickers], funds)
    for cat in CATEGORIES:
        print(f"  {cat}: {len(fmap[cat])} candidates")

    weekly_px = adj[tickers].resample("W-FRI").last()
    weekly_ret = weekly_px.pct_change()
    weekly_factors = resample_factors_weekly(fmap)
    weekly_factors = lag_factors(weekly_factors, periods=1)

    print("=== Selecting top factors per category ===")
    selected, ic_summary, signed = select_top_factors_per_category(
        weekly_factors, weekly_ret, top_n=factors_per_category
    )
    ic_summary.to_csv(out_dir / "factor_ic_all.csv", index=False)
    sel_rows = []
    for cat, names in selected.items():
        for i, n in enumerate(names, 1):
            sel_rows.append({"category": cat, "rank": i, "factor": n})
    pd.DataFrame(sel_rows).to_csv(out_dir / "selected_factors.csv", index=False)
    print(pd.DataFrame(sel_rows).groupby("category")["factor"].apply(list))

    if skip_optimize:
        tilt = {c: 1.0 / len(CATEGORIES) for c in CATEGORIES}
        comp = combine_selected_scores(signed, selected, tilt)
        bt = run_weekly_backtest(comp, weekly_ret, top_n=top_n, cost_bps=cost_bps, spy=spy)
        best = OptTrial(params={"category_weights": tilt}, summary=bt.summary, score=0.0, feasible=False)
        top_trials = [best]
    else:
        print("=== Optimizing to targets (Sharpe≥3, CAGR≥30%, MDD≤10%) ===")
        best, bt, top_trials = optimize_to_targets(
            signed, selected, weekly_ret, spy=spy, top_n=top_n, cost_bps=cost_bps
        )
        comp = combine_selected_scores(signed, selected, best.params["category_weights"])

    _save_outputs(out_dir, selected, ic_summary, best, bt, top_trials, spy)
    return USPipelineResult(
        selected=selected,
        ic_summary=ic_summary,
        best_trial=best,
        backtest=bt,
        top_trials=top_trials,
        weekly_returns=weekly_ret,
        composite=comp,
    )


def _save_outputs(
    out_dir: Path,
    selected: Dict[str, List[str]],
    ic_summary: pd.DataFrame,
    best: OptTrial,
    bt: BacktestResult,
    top_trials: List[OptTrial],
    spy: pd.Series,
) -> None:
    bt.equity.to_csv(out_dir / "equity.csv")
    bt.returns.to_csv(out_dir / "returns.csv")
    bt.exposure.to_csv(out_dir / "exposure.csv")
    bt.holdings.to_csv(out_dir / "holdings.csv", index=False)
    pd.DataFrame([bt.summary]).to_csv(out_dir / "summary.csv", index=False)
    pd.DataFrame([{"feasible": best.feasible, **{f"p_{k}": str(v) for k, v in best.params.items()}}]).to_csv(
        out_dir / "best_params.csv", index=False
    )
    trial_rows = []
    for t in top_trials:
        trial_rows.append({**t.summary, "score": t.score, "feasible": t.feasible, "params": str(t.params)})
    pd.DataFrame(trial_rows).to_csv(out_dir / "top_trials.csv", index=False)

    # plots
    fig, ax = plt.subplots(figsize=(10, 4.5))
    eq = bt.equity.dropna()
    ax.plot(eq.index, eq.values, color="#0B3D5C", lw=1.8, label="Strategy")
    if spy is not None:
        spy_w = spy.resample("W-FRI").last().reindex(eq.index).ffill()
        spy_nav = spy_w / spy_w.iloc[0]
        ax.plot(spy_nav.index, spy_nav.values, color="#C45C26", lw=1.2, alpha=0.8, label="SPY")
    ax.set_title(
        f"US Multi-Factor Top10 Weekly  |  Sharpe={bt.summary['sharpe']:.2f}  "
        f"CAGR={bt.summary['cagr']:.1%}  MDD={bt.summary['max_drawdown']:.1%}"
    )
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "nav.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.plot(bt.exposure.index, bt.exposure.values, color="#0B3D5C", lw=1.0)
    ax.set_title("Dynamic Exposure")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "exposure.png", dpi=140)
    plt.close(fig)

    # IC bars for selected
    sel_set = {(c, f) for c, fs in selected.items() for f in fs}
    sub = ic_summary[ic_summary.apply(lambda r: (r["category"], r["factor"]) in sel_set, axis=1)]
    if not sub.empty:
        fig, ax = plt.subplots(figsize=(11, 4.5))
        labels = [f"{r.category[:3]}:{r.factor}" for r in sub.itertuples()]
        ax.bar(range(len(sub)), sub["ic_mean"].values * sub["sign"].values, color="#0B3D5C", alpha=0.85)
        ax.set_xticks(range(len(sub)))
        ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_title("Selected factors signed mean Rank IC")
        fig.tight_layout()
        fig.savefig(out_dir / "selected_ic.png", dpi=140)
        plt.close(fig)

    lines = [
        "US S&P500 Multi-Factor Strategy",
        f"feasible={best.feasible}",
        f"sharpe={bt.summary['sharpe']:.3f}",
        f"cagr={bt.summary['cagr']:.2%}",
        f"max_drawdown={bt.summary['max_drawdown']:.2%}",
        f"ann_vol={bt.summary['ann_vol']:.2%}",
        f"avg_exposure={bt.summary.get('avg_exposure', float('nan')):.2f}",
        f"params={best.params}",
        "",
        "Selected factors:",
    ]
    for cat, names in selected.items():
        lines.append(f"  {cat}: {', '.join(names)}")
    (out_dir / "SUMMARY.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12]))
