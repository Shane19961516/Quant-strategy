#!/usr/bin/env python3
"""Search upgraded strategy for Sharpe>2.3, ann>25%, MDD<=8%."""
from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

from backtest import run_backtest
from config import PARAMS, STRATEGY_START
from data import build_panels, load_universe
from metrics import perf_stats, yearly_returns
from strategy import generate_target_weights

OUT = Path(__file__).resolve().parent / "output"
RNG = random.Random(20260812)


def eval_p(close, ov):
    p = {**PARAMS, **ov}
    w, _ = generate_target_weights(close, p)
    nav, _, _ = run_backtest(close, w, cost_bps=p["cost_bps"], borrow_rate=p.get("borrow_rate", 0.03))
    s = perf_stats(nav)
    y = yearly_returns(nav)
    return {
        "ann": float(s["ann_return"]),
        "sharpe": float(s["sharpe_rf0"]),
        "mdd": float(s["max_drawdown"]),
        "vol": float(s["ann_vol"]),
        "ymin": float(y.min()),
        **{f"y{yr}": float(v) for yr, v in y.items()},
    }


def sample():
    return {
        "mom_lb": RNG.choice([4, 6, 8, 10, 12]),
        "abs_lb": RNG.choice([2, 3, 4, 5, 6, 8]),
        "vol_lb": RNG.choice([15, 20, 30]),
        "sma_lb": RNG.choice([20, 25, 30, 35, 40, 50]),
        "abs_margin": RNG.choice([0.0, 0.002, 0.005, 0.01]),
        "require_abs_pos": RNG.choice([False, True]),
        "sma_buffer": RNG.choice([0.0, 0.002, 0.005, 0.008, 0.01, 0.015]),
        "sma_slope_lb": RNG.choice([0, 5, 10]),
        "vol_target": RNG.choice([0.08, 0.09, 0.10, 0.11, 0.12, 0.14, 0.16]),
        "top_k": RNG.choice([1, 2, 3, 4]),
        "max_single": RNG.choice([0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80, 1.0]),
        "canary_k": RNG.choice([2, 3, 4]),
        "rebalance_thresh": RNG.choice([0.0, 0.05, 0.10, 0.15, 0.20, 0.25]),
        "max_gross": RNG.choice([1.0, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0]),
        "boost_mom": RNG.choice([0.02, 0.04, 0.06, 0.08, 0.10, 0.12]),
        "boost_breadth": RNG.choice([0.25, 0.4, 0.5, 0.6, 0.75]),
        "dd_stop": RNG.choice([0.04, 0.05, 0.055, 0.06, 0.07, 0.08]),
        "dd_resume": RNG.choice([0.01, 0.015, 0.02, 0.03]),
        "risk_off_scale": RNG.choice([0.0, 0.1, 0.2]),
        "neutral_scale": RNG.choice([0.3, 0.4, 0.5, 0.6]),
        "mom_strength": RNG.choice([0.0, 0.01, 0.02, 0.03, 0.05]),
        "borrow_rate": 0.03,
        "cost_bps": 2.0,
    }


def score(m):
    # hard preference for target region
    pen = 0.0
    if m["sharpe"] < 2.3:
        pen += (2.3 - m["sharpe"]) * 8
    if m["ann"] < 0.25:
        pen += (0.25 - m["ann"]) * 20
    if m["mdd"] < -0.08:
        pen += (-0.08 - m["mdd"]) * 30
    if m["ymin"] < 0:
        pen += (-m["ymin"]) * 10
    hit = (m["sharpe"] > 2.3) and (m["ann"] > 0.25) and (m["mdd"] >= -0.08)
    if hit:
        return 100 + m["sharpe"] * 2 + m["ann"] * 5 + (m["mdd"] + 0.08) * 3
    return m["sharpe"] * 2 + m["ann"] * 6 + (m["mdd"] + 0.08) * 4 + m["ymin"] * 2 - pen


def main():
    raw = load_universe(False)
    close, _ = build_panels(raw)
    close = close.loc[STRATEGY_START:]

    base = eval_p(close, {})
    print("BASE", {k: round(base[k], 4) for k in ["ann", "sharpe", "mdd", "ymin"]}, flush=True)

    rows = []
    best = None
    feasible = []
    N = 1600
    for i in range(1, N + 1):
        ov = sample()
        m = eval_p(close, ov)
        row = {**ov, **m, "score": score(m)}
        rows.append(row)
        if (m["sharpe"] > 2.3) and (m["ann"] > 0.25) and (m["mdd"] >= -0.08):
            feasible.append(row)
        if best is None or row["score"] > best["score"]:
            best = row
        if i % 50 == 0:
            print(
                f"iter {i} best ann={best['ann']:.3f} sh={best['sharpe']:.3f} mdd={best['mdd']:.3f} ymin={best['ymin']:.3f} feas={len(feasible)}",
                flush=True,
            )

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "opt_upgrade_search.csv", index=False)
    print("feasible", len(feasible), flush=True)
    if feasible:
        fdf = pd.DataFrame(feasible).sort_values(["score", "sharpe", "ann"], ascending=False)
        fdf.to_csv(OUT / "opt_upgrade_feasible.csv", index=False)
        print(fdf.head(15).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    else:
        # near targets
        near = df.copy()
        near["gap"] = (
            (2.3 - near["sharpe"]).clip(lower=0) * 2
            + (0.25 - near["ann"]).clip(lower=0) * 5
            + ((-0.08) - near["mdd"]).clip(lower=0) * 8
        )
        print("\nClosest by gap:")
        print(
            near.sort_values("gap")
            .head(20)[
                [
                    "ann",
                    "sharpe",
                    "mdd",
                    "ymin",
                    "gap",
                    "max_gross",
                    "vol_target",
                    "dd_stop",
                    "top_k",
                    "max_single",
                    "canary_k",
                    "sma_buffer",
                    "mom_lb",
                    "abs_lb",
                ]
            ]
            .to_string(index=False, float_format=lambda x: f"{x:.4f}")
        )
        print("\nBest score:")
        print(
            df.sort_values("score", ascending=False)
            .head(15)[
                [
                    "ann",
                    "sharpe",
                    "mdd",
                    "ymin",
                    "score",
                    "max_gross",
                    "vol_target",
                    "dd_stop",
                    "top_k",
                    "max_single",
                    "canary_k",
                    "sma_buffer",
                ]
            ]
            .to_string(index=False, float_format=lambda x: f"{x:.4f}")
        )


if __name__ == "__main__":
    main()
