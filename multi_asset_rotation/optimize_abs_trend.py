#!/usr/bin/env python3
"""Search absolute-momentum / trend-filter thresholds."""
from __future__ import annotations

import itertools
from pathlib import Path

import pandas as pd

from backtest import run_backtest
from config import PARAMS, STRATEGY_START
from data import build_panels, load_universe
from metrics import perf_stats, yearly_returns
from strategy import generate_target_weights

OUT = Path(__file__).resolve().parent / "output"


def main():
    raw = load_universe(force=False)
    close, _ = build_panels(raw)
    close = close.loc[STRATEGY_START:]

    def eval_p(ov):
        p = {**PARAMS, **ov}
        w, _ = generate_target_weights(close, p)
        nav, _, _ = run_backtest(close, w, cost_bps=p["cost_bps"])
        s = perf_stats(nav)
        y = yearly_returns(nav)
        out = {
            "ann": float(s["ann_return"]),
            "sharpe": float(s["sharpe_rf0"]),
            "mdd": float(s["max_drawdown"]),
            "vol": float(s["ann_vol"]),
            "ymin": float(y.min()),
        }
        for yr, v in y.items():
            out[f"y{yr}"] = float(v)
        return out

    base = eval_p({})
    ba, bs, bm = base["ann"], base["sharpe"], base["mdd"]
    print("BASE", {k: round(base[k], 4) for k in ["ann", "sharpe", "mdd", "ymin"]}, flush=True)

    cands = []
    # 1) pure sma_buffer fine scan
    for sma_buffer in [i / 10000 for i in range(0, 81, 5)]:  # 0 to 0.8% step 5bp
        cands.append(
            dict(
                abs_lb=4,
                sma_lb=40,
                abs_margin=0.0,
                require_abs_pos=False,
                sma_buffer=sma_buffer,
                sma_slope_lb=0,
            )
        )
    # 2) abs_margin fine scan
    for abs_margin in [i / 10000 for i in range(0, 301, 10)]:
        cands.append(
            dict(
                abs_lb=4,
                sma_lb=40,
                abs_margin=abs_margin,
                require_abs_pos=False,
                sma_buffer=0.0,
                sma_slope_lb=0,
            )
        )
        cands.append(
            dict(
                abs_lb=4,
                sma_lb=40,
                abs_margin=abs_margin,
                require_abs_pos=True,
                sma_buffer=0.0,
                sma_slope_lb=0,
            )
        )
    # 3) combine best region: sma_buffer x abs_margin x sma_lb
    for sma_lb, sma_buffer, abs_margin in itertools.product(
        [35, 38, 40, 42, 45],
        [0.0, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004],
        [0.0, 0.001, 0.002, 0.003, 0.005],
    ):
        cands.append(
            dict(
                abs_lb=4,
                sma_lb=sma_lb,
                abs_margin=abs_margin,
                require_abs_pos=False,
                sma_buffer=sma_buffer,
                sma_slope_lb=0,
            )
        )
    # 4) slope variants on top of good buffers
    for sma_buffer, sma_slope_lb in itertools.product([0.0, 0.002, 0.003], [0, 3, 5, 10]):
        cands.append(
            dict(
                abs_lb=4,
                sma_lb=40,
                abs_margin=0.0,
                require_abs_pos=False,
                sma_buffer=sma_buffer,
                sma_slope_lb=sma_slope_lb,
            )
        )
    # 5) abs_lb tweak with best buffers
    for abs_lb, sma_buffer in itertools.product([3, 4, 5], [0.0, 0.002, 0.003]):
        cands.append(
            dict(
                abs_lb=abs_lb,
                sma_lb=40,
                abs_margin=0.0,
                require_abs_pos=False,
                sma_buffer=sma_buffer,
                sma_slope_lb=0,
            )
        )

    seen = set()
    uniq = []
    for c in cands:
        key = tuple(sorted((k, float(v) if isinstance(v, float) else v) for k, v in c.items()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    print("candidates", len(uniq), flush=True)

    rows = []
    for i, ov in enumerate(uniq, 1):
        m = eval_p(ov)
        rows.append({**ov, **m})
        if i % 50 == 0 or i == len(uniq):
            print(f"evaluated {i}/{len(uniq)}", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "opt_abs_trend_fine.csv", index=False)
    df["d_ann"] = df.ann - ba
    df["d_sh"] = df.sharpe - bs
    df["d_mdd"] = df.mdd - bm
    df["score"] = df.d_sh * 3 + df.d_ann * 8 + df.d_mdd * 10

    strict = df[
        (df.sharpe + 1e-12 >= bs)
        & (df.ann + 1e-12 >= ba)
        & (df.mdd + 1e-12 >= bm)
        & (df.ymin >= -1e-12)
    ].sort_values("score", ascending=False)
    print("\nSTRICT dominate", len(strict), flush=True)
    print(strict.head(20).to_string(index=False, float_format=lambda x: f"{x:.4f}"), flush=True)

    win = df[
        (df.sharpe + 1e-12 >= bs)
        & (df.ann > ba + 1e-4)
        & (df.mdd > bm + 1e-4)
        & (df.ymin >= -1e-12)
    ].sort_values("score", ascending=False)
    print("\nWIN ann↑ AND mdd↑ AND sharpe≥", len(win), flush=True)
    print(win.head(15).to_string(index=False, float_format=lambda x: f"{x:.4f}"), flush=True)

    prac = df[
        (df.sharpe > bs + 1e-4)
        & (df.ann > ba + 1e-4)
        & (df.mdd + 1e-12 >= bm)
        & (df.ymin >= -1e-12)
    ].sort_values("score", ascending=False)
    print("\nPRAC sharpe↑ ann↑ mdd not worse", len(prac), flush=True)
    print(prac.head(15).to_string(index=False, float_format=lambda x: f"{x:.4f}"), flush=True)

    print("\nBest MDD sharpe>=base ann>=base", flush=True)
    print(
        df[(df.sharpe + 1e-12 >= bs) & (df.ann + 1e-12 >= ba)]
        .sort_values("mdd", ascending=False)
        .head(10)
        .to_string(index=False, float_format=lambda x: f"{x:.4f}"),
        flush=True,
    )

    top = strict if len(strict) else prac
    (OUT / "opt_abs_trend_top.json").write_text(
        top.head(15).to_json(orient="records", force_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
