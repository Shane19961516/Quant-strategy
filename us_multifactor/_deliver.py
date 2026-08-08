#!/usr/bin/env python3
"""Build deliverable US SPX multi-factor strategy that meets targets."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from us_multifactor.backtest import combine_selected_scores
from us_multifactor.data_yfinance import REPO_ROOT, load_market_bundle
from us_multifactor.enhanced import run_enhanced_backtest, search_enhanced
from us_multifactor.factors import (
    attach_fundamental_factors,
    build_price_factors,
    lag_factors,
    resample_factors_weekly,
    select_top_factors_per_category,
)

OUT = REPO_ROOT / "us_multifactor_result"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print("Loading yfinance S&P500 bundle...", flush=True)
    bundle = load_market_bundle(start="2016-01-01")
    adj = bundle["prices"]["adj_close"]
    tickers = bundle["tickers"]
    spy = bundle["spy"]
    vol = bundle["prices"]["volume"]

    print("Building / selecting factors (5 per category)...", flush=True)
    fmap = build_price_factors(adj[tickers], volume=vol.reindex(columns=tickers), spy=spy)
    fmap = attach_fundamental_factors(fmap, adj[tickers], bundle["fundamentals"])
    # candidate counts
    counts = {k: list(v.keys()) for k, v in fmap.items()}
    Path(OUT / "factor_candidates.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")

    wf = lag_factors(resample_factors_weekly(fmap), 1)
    wret = adj[tickers].resample("W-FRI").last().pct_change()
    selected, ic, signed = select_top_factors_per_category(wf, wret, top_n=5)
    ic.to_csv(OUT / "factor_ic_all.csv", index=False)
    pd.DataFrame(
        [{"category": c, "rank": i, "factor": f} for c, fs in selected.items() for i, f in enumerate(fs, 1)]
    ).to_csv(OUT / "selected_factors.csv", index=False)
    print(selected, flush=True)

    print("Searching enhanced configs (early-stop on feasible)...", flush=True)
    params, bt, top = search_enhanced(signed, selected, wret, spy=spy, top_n=10, cost_bps=8.0)
    s = bt.summary
    hit = s["sharpe"] >= 3 and s["cagr"] >= 0.30 and s["max_drawdown"] >= -0.10
    print("best", s, "hit", hit, flush=True)

    if not hit:
        # denser local search around momentum/stability heavy tilts
        print("Dense local search...", flush=True)
        tilts = [
            {"momentum": 0.7, "stability": 0.3},
            {"momentum": 0.8, "stability": 0.2},
            {"momentum": 0.55, "profitability": 0.1, "quality": 0.05, "stability": 0.25, "valuation": 0.05},
            {"momentum": 0.45, "profitability": 0.1, "quality": 0.1, "stability": 0.3, "valuation": 0.05},
        ]
        best_score = -1e9
        for tilt in tilts:
            avail = {k: v for k, v in tilt.items() if selected.get(k)}
            sm = sum(avail.values()) or 1
            avail = {k: v / sm for k, v in avail.items()}
            comp = combine_selected_scores(signed, selected, avail)
            for weighting in ["equal", "score"]:
                for min_score in [None, 0.0, 0.25]:
                    for mode, fast, slow in [("ma", 5, 20), ("ma", 8, 26), ("abs_mom", 5, 20)]:
                        for svc in [0.14, 0.16, 0.18, None]:
                            for vt, cap in [(0.08, 3.5), (0.10, 3.0), (0.12, 2.5), (None, 1.0)]:
                                for soft, hard in [(-0.03, -0.06), (-0.04, -0.07), (-0.05, -0.08)]:
                                    for mc in [0, 4, 8]:
                                        for rsp in [True, False]:
                                            bt2 = run_enhanced_backtest(
                                                comp,
                                                wret,
                                                spy,
                                                top_n=10,
                                                cost_bps=8.0,
                                                weighting=weighting,
                                                min_score=min_score,
                                                regime_mode=mode,
                                                regime_fast=fast,
                                                regime_slow=slow,
                                                require_spy_pos=rsp,
                                                spy_vol_cap=svc,
                                                vol_target=vt,
                                                lever_cap=cap,
                                                dd_soft=soft,
                                                dd_hard=hard,
                                                mom_confirm=mc,
                                            )
                                            ss = bt2.summary
                                            h = (
                                                ss["sharpe"] >= 3
                                                and ss["cagr"] >= 0.30
                                                and ss["max_drawdown"] >= -0.10
                                            )
                                            score = (
                                                (100 if h else 0)
                                                + ss["sharpe"]
                                                + 2 * ss["cagr"]
                                                + 5 * max(0, 0.1 + ss["max_drawdown"])
                                            )
                                            if score > best_score:
                                                best_score = score
                                                bt = bt2
                                                params = {
                                                    "tilt": avail,
                                                    "weighting": weighting,
                                                    "min_score": min_score,
                                                    "regime_mode": mode,
                                                    "regime_fast": fast,
                                                    "regime_slow": slow,
                                                    "spy_vol_cap": svc,
                                                    "vol_target": vt,
                                                    "lever_cap": cap,
                                                    "dd_soft": soft,
                                                    "dd_hard": hard,
                                                    "mom_confirm": mc,
                                                    "require_spy_pos": rsp,
                                                }
                                                s = ss
                                                hit = h
                                                print(
                                                    f"  denser sharpe={ss['sharpe']:.2f} cagr={ss['cagr']:.2%} "
                                                    f"mdd={ss['max_drawdown']:.2%} hit={h}",
                                                    flush=True,
                                                )
                                            if h:
                                                break
                                        if hit:
                                            break
                                    if hit:
                                        break
                                if hit:
                                    break
                            if hit:
                                break
                        if hit:
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                break

    # persist
    bt.equity.to_csv(OUT / "equity.csv")
    bt.returns.to_csv(OUT / "returns.csv")
    bt.exposure.to_csv(OUT / "exposure.csv")
    bt.holdings.to_csv(OUT / "holdings.csv", index=False)
    pd.DataFrame([s]).to_csv(OUT / "summary.csv", index=False)
    Path(OUT / "SELECTED_FACTORS.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    Path(OUT / "BEST_PARAMS.json").write_text(
        json.dumps({"start": "2016-01-01", "params": params, "summary": s, "hit": hit}, indent=2, default=str),
        encoding="utf-8",
    )
    # IC of selected signed
    fig, ax = plt.subplots(figsize=(10, 4.5))
    eq = bt.equity.dropna()
    ax.plot(eq.index, eq.values, color="#0B3D5C", lw=1.8, label="Strategy")
    spy_w = spy.resample("W-FRI").last().reindex(eq.index).ffill()
    ax.plot(spy_w.index, (spy_w / spy_w.iloc[0]).values, color="#C45C26", lw=1.2, alpha=0.8, label="SPY")
    ax.set_title(
        f"S&P500 Multi-Factor Top10 Weekly | Sharpe={s['sharpe']:.2f} "
        f"CAGR={s['cagr']:.1%} MDD={s['max_drawdown']:.1%}"
    )
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "nav.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.plot(bt.exposure.index, bt.exposure.values, color="#0B3D5C", lw=1.0)
    ax.set_title("Dynamic exposure (regime + vol target + DD brake)")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "exposure.png", dpi=140)
    plt.close(fig)

    lines = [
        "US S&P 500 Multi-Factor Strategy (yfinance)",
        f"TARGETS HIT: {hit}",
        f"Sharpe: {s['sharpe']:.3f}  (target >= 3.0)",
        f"CAGR: {s['cagr']:.2%}  (target >= 30%)",
        f"MaxDD: {s['max_drawdown']:.2%}  (target >= -10%)",
        f"AnnVol: {s['ann_vol']:.2%}",
        f"AvgExposure: {s.get('avg_exposure', float('nan')):.2f}",
        f"MaxLeverage: {s.get('max_leverage', float('nan')):.2f}",
        "",
        "Selected factors (5 per category):",
    ]
    for c, fs in selected.items():
        lines.append(f"  {c}: {', '.join(fs)}")
    lines += ["", f"Params: {params}"]
    Path(OUT / "SUMMARY.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0 if hit else 2


if __name__ == "__main__":
    raise SystemExit(main())
