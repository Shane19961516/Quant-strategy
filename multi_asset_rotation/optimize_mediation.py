#!/usr/bin/env python3
"""权重调解层两阶段搜索：粗搜达标邻域 -> 精搜平滑。"""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import run_backtest
from config import PARAMS, STRATEGY_START
from data import build_panels, load_universe
from metrics import perf_stats, yearly_returns
from strategy import generate_target_weights

OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(parents=True, exist_ok=True)
RNG = random.Random(42)


def score_row(s: dict, y: pd.Series) -> float:
    ann = float(s.get("ann_return", 0))
    sh = float(s.get("sharpe_rf0", 0))
    mdd = float(s.get("max_drawdown", -1))
    ystd = float(y.std())
    ymin = float(y.min())

    pen = 0.0
    if sh < 2.0:
        pen += (2.0 - sh) * 4.0
    if ann < 0.15:
        pen += (0.15 - ann) * 25.0
    if mdd < -0.07:
        pen += (-0.07 - mdd) * 30.0
    if ymin < 0:
        pen += (-ymin) * 20.0

    hit = (sh >= 2.0) and (ann >= 0.15) and (mdd >= -0.07) and (ymin >= 0)
    if hit:
        return sh * 0.4 + ann * 3.0 + ymin * 10.0 - ystd * 12.0 + (mdd + 0.07) * 2.0
    return sh * 1.5 + ann * 10.0 + ymin * 5.0 - ystd * 2.0 - pen


def eval_params(close, p):
    w, _ = generate_target_weights(close, p)
    nav, _, _ = run_backtest(close, w, cost_bps=p["cost_bps"])
    s = perf_stats(nav)
    y = yearly_returns(nav)
    return s, y, score_row(s, y)


def row_from(s, y, sc, p, center):
    row = {
        "score": sc,
        "ann": s["ann_return"],
        "sharpe": s["sharpe_rf0"],
        "mdd": s["max_drawdown"],
        "vol": s["ann_vol"],
        "ystd": float(y.std()),
        "ymin": float(y.min()),
        "tilt": p["active_tilt"],
        "max_dev": p["max_sleeve_dev"],
        "vol_budget": p["vol_budget"],
        "ema": p["weight_ema"],
        "canary_floor": p["canary_bond_floor"],
        "min_bond": p["min_bond"],
        "thresh": p["rebalance_thresh"],
        "max_single": p["max_single_asset"],
        "center": json.dumps(center),
    }
    for yr, val in y.items():
        row[f"y{yr}"] = float(val)
    return row


def main():
    raw = load_universe(force=False)
    close, _ = build_panels(raw)
    close = close.loc[STRATEGY_START:]

    centers = [
        {"bond": 0.20, "gold": 0.25, "cn": 0.15, "us": 0.40},
        {"bond": 0.25, "gold": 0.25, "cn": 0.15, "us": 0.35},
        {"bond": 0.28, "gold": 0.24, "cn": 0.16, "us": 0.32},
        {"bond": 0.30, "gold": 0.20, "cn": 0.15, "us": 0.35},
    ]

    # ---- Stage 1: 随机搜索找达标 ----
    print("=== Stage 1: random search ===")
    rows = []
    for i in range(220):
        center = RNG.choice(centers)
        p = {
            **PARAMS,
            "neutral_sleeve": center,
            "active_tilt": RNG.choice([0.85, 0.90, 0.93, 0.96, 0.98, 1.00]),
            "max_sleeve_dev": RNG.choice([0.25, 0.35, 0.45, 0.60, 0.80, 1.00]),
            "vol_budget": RNG.choice([0.072, 0.075, 0.078, 0.082, 0.085, 0.090]),
            "weight_ema": RNG.choice([0.50, 0.65, 0.80, 0.90, 1.00]),
            "canary_bond_floor": RNG.choice([0.88, 0.92, 0.96, 1.00]),
            "bond_canary_boost": RNG.choice([0.40, 0.55, 0.70]),
            "min_bond": RNG.choice([0.0, 0.05, 0.08, 0.12]),
            "max_single_asset": RNG.choice([0.50, 0.55]),
            "rebalance_thresh": RNG.choice([0.10, 0.15, 0.20, 0.25]),
            "defense_skip_center": True,
        }
        s, y, sc = eval_params(close, p)
        if not s:
            continue
        rows.append(row_from(s, y, sc, p, center))
        if (i + 1) % 40 == 0:
            best = max(rows, key=lambda r: r["score"])
            print(
                f"[{i+1}] best ann={best['ann']:.2%} sh={best['sharpe']:.2f} "
                f"mdd={best['mdd']:.2%} ystd={best['ystd']:.2%} ymin={best['ymin']:.2%}"
            )

    df1 = pd.DataFrame(rows).sort_values("score", ascending=False)
    ok1 = df1[(df1["ann"] >= 0.15) & (df1["sharpe"] >= 2.0) & (df1["mdd"] >= -0.07)]
    print(f"Stage1 达标: {len(ok1)} / {len(df1)}")

    seeds = (ok1 if len(ok1) else df1).head(8)

    # ---- Stage 2: 围绕种子精搜 ----
    print("\n=== Stage 2: local refine ===")
    rows2 = list(rows)
    for _, seed in seeds.iterrows():
        base_center = json.loads(seed["center"])
        for _ in range(35):
            center = {
                "bond": float(np.clip(base_center["bond"] + RNG.uniform(-0.04, 0.04), 0.15, 0.35)),
                "gold": 0.0,
                "cn": 0.0,
                "us": 0.0,
            }
            center["gold"] = float(np.clip(base_center["gold"] + RNG.uniform(-0.04, 0.04), 0.15, 0.35))
            center["cn"] = float(np.clip(base_center["cn"] + RNG.uniform(-0.04, 0.04), 0.08, 0.25))
            rem = 1.0 - center["bond"] - center["gold"] - center["cn"]
            center["us"] = float(max(0.15, rem))
            ssum = sum(center.values())
            center = {k: center[k] / ssum for k in center}

            p = {
                **PARAMS,
                "neutral_sleeve": center,
                "active_tilt": float(np.clip(seed["tilt"] + RNG.uniform(-0.06, 0.06), 0.80, 1.0)),
                "max_sleeve_dev": float(np.clip(seed["max_dev"] + RNG.uniform(-0.15, 0.15), 0.20, 1.0)),
                "vol_budget": float(np.clip(seed["vol_budget"] + RNG.uniform(-0.008, 0.008), 0.068, 0.095)),
                "weight_ema": float(np.clip(seed["ema"] + RNG.uniform(-0.15, 0.15), 0.45, 1.0)),
                "canary_bond_floor": float(np.clip(seed["canary_floor"] + RNG.uniform(-0.05, 0.05), 0.85, 1.0)),
                "bond_canary_boost": 0.60,
                "min_bond": float(np.clip(seed["min_bond"] + RNG.uniform(-0.04, 0.04), 0.0, 0.18)),
                "max_single_asset": float(seed["max_single"]),
                "rebalance_thresh": float(seed["thresh"]),
                "defense_skip_center": True,
            }
            s, y, sc = eval_params(close, p)
            if not s:
                continue
            rows2.append(row_from(s, y, sc, p, center))

    df = pd.DataFrame(rows2).sort_values("score", ascending=False)
    df.to_csv(OUT / "opt_results.csv", index=False)

    cols = [
        "score", "ann", "sharpe", "mdd", "ystd", "ymin",
        "tilt", "max_dev", "vol_budget", "ema", "canary_floor", "min_bond", "thresh", "center",
    ]
    print("\n===== TOP 15 =====")
    print(df[cols].head(15).to_string(index=False))

    ok = df[(df["ann"] >= 0.15) & (df["sharpe"] >= 2.0) & (df["mdd"] >= -0.07) & (df["ymin"] >= 0)]
    print(f"\n完全达标(含年份非负): {len(ok)} / {len(df)}")
    if len(ok):
        # 在达标集合中，优先平滑，但要求 ann/sharpe 不要掉太多
        ok = ok.copy()
        ok["smooth_rank"] = ok["ystd"].rank(ascending=True) + ok["ymin"].rank(ascending=False) * 0.5
        # 也考虑相对最优夏普的折损
        ok["quality"] = ok["sharpe"] / ok["sharpe"].max() + ok["ann"] / ok["ann"].max() - ok["ystd"] / ok["ystd"].max()
        pick = ok.sort_values(["quality", "ystd"], ascending=[False, True]).iloc[0].to_dict()
        print("\n===== 达标优选 TOP 10 (by quality) =====")
        print(ok.sort_values("quality", ascending=False)[cols].head(10).to_string(index=False))
    else:
        ok2 = df[(df["ann"] >= 0.15) & (df["sharpe"] >= 2.0) & (df["mdd"] >= -0.07)]
        pick = (ok2 if len(ok2) else df).sort_values(["ymin", "ystd", "score"], ascending=[False, True, False]).iloc[0].to_dict()

    best_params = {
        **PARAMS,
        "active_tilt": float(pick["tilt"]),
        "max_sleeve_dev": float(pick["max_dev"]),
        "vol_budget": float(pick["vol_budget"]),
        "weight_ema": float(pick["ema"]),
        "canary_bond_floor": float(pick["canary_floor"]),
        "min_bond": float(pick["min_bond"]),
        "neutral_sleeve": json.loads(pick["center"]),
        "bond_canary_boost": 0.60,
        "defense_skip_center": True,
        "max_single_asset": float(pick["max_single"]),
        "rebalance_thresh": float(pick["thresh"]),
    }
    with open(OUT / "best_params.json", "w", encoding="utf-8") as f:
        json.dump({"pick": pick, "params": best_params}, f, ensure_ascii=False, indent=2, default=str)

    print("\nSELECTED METRICS:")
    print({k: pick[k] for k in ["ann", "sharpe", "mdd", "ystd", "ymin", "tilt", "max_dev", "vol_budget", "ema", "canary_floor", "min_bond"]})
    print("\nBEST PARAMS:")
    print(json.dumps(best_params, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
