# -*- coding: utf-8 -*-
"""Frozen best-config runner (reproducible, no grid search)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .backtest import combine_selected_scores
from .data_yfinance import DATA_DIR, REPO_ROOT, load_market_bundle
from .enhanced import run_enhanced_backtest
from .factors import (
    attach_fundamental_factors,
    build_price_factors,
    lag_factors,
    resample_factors_weekly,
    select_top_factors_per_category,
)

DEFAULT_PARAMS: Dict[str, Any] = {
    "start": "2016-01-01",
    "top_n": 10,
    "cost_bps": 8.0,
    "tilt": {
        "momentum": 0.55,
        "profitability": 0.10,
        "quality": 0.05,
        "size": 0.0,
        "stability": 0.25,
        "valuation": 0.05,
    },
    "weighting": "equal",
    "min_score": None,
    "regime_mode": "ma",
    "regime_fast": 8,
    "regime_slow": 26,
    "spy_vol_cap": None,
    "vol_target": None,
    "lever_cap": 1.0,
    "dd_soft": -0.03,
    "dd_hard": -0.06,
    "mom_confirm": 4,
    "rebalance_band": 0.0,
    "require_spy_pos": True,
}


def run_frozen(
    out_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    params: Optional[Dict[str, Any]] = None,
    reselect_factors: bool = True,
) -> Dict[str, Any]:
    """Reproduce the delivered weekly Top-10 multi-factor book."""
    params = {**DEFAULT_PARAMS, **(params or {})}
    out_dir = Path(out_dir) if out_dir else REPO_ROOT / "us_multifactor_result"
    cache_dir = Path(cache_dir) if cache_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle = load_market_bundle(start=params["start"], cache_dir=cache_dir)
    adj = bundle["prices"]["adj_close"]
    tickers = bundle["tickers"]
    spy = bundle["spy"]
    vol = bundle["prices"]["volume"]

    fmap = build_price_factors(adj[tickers], volume=vol.reindex(columns=tickers), spy=spy)
    fmap = attach_fundamental_factors(fmap, adj[tickers], bundle["fundamentals"])
    wf = lag_factors(resample_factors_weekly(fmap), 1)
    wret = adj[tickers].resample("W-FRI").last().pct_change()

    selected_path = out_dir / "SELECTED_FACTORS.json"
    if reselect_factors or not selected_path.exists():
        selected, ic, signed = select_top_factors_per_category(wf, wret, top_n=5)
        ic.to_csv(out_dir / "factor_ic_all.csv", index=False)
        selected_path.write_text(json.dumps(selected, indent=2), encoding="utf-8")
        pd.DataFrame(
            [{"category": c, "rank": i, "factor": f} for c, fs in selected.items() for i, f in enumerate(fs, 1)]
        ).to_csv(out_dir / "selected_factors.csv", index=False)
    else:
        selected = json.loads(selected_path.read_text(encoding="utf-8"))
        _, _, signed = select_top_factors_per_category(wf, wret, top_n=5)

    tilt = {k: v for k, v in params["tilt"].items() if selected.get(k)}
    ssum = sum(tilt.values()) or 1.0
    tilt = {k: v / ssum for k, v in tilt.items()}
    comp = combine_selected_scores(signed, selected, tilt)

    bt = run_enhanced_backtest(
        comp,
        wret,
        spy,
        top_n=int(params["top_n"]),
        cost_bps=float(params["cost_bps"]),
        weighting=params["weighting"],
        min_score=params["min_score"],
        regime_mode=params["regime_mode"],
        regime_fast=int(params["regime_fast"]),
        regime_slow=int(params["regime_slow"]),
        require_spy_pos=bool(params["require_spy_pos"]),
        spy_vol_cap=params["spy_vol_cap"],
        vol_target=params["vol_target"],
        lever_cap=float(params["lever_cap"]),
        dd_soft=float(params["dd_soft"]),
        dd_hard=float(params["dd_hard"]),
        mom_confirm=int(params["mom_confirm"]),
        rebalance_band=float(params.get("rebalance_band") or 0.0),
    )
    s = bt.summary
    hit = s["sharpe"] >= 3 and s["cagr"] >= 0.30 and s["max_drawdown"] >= -0.1000001

    bt.equity.to_csv(out_dir / "equity.csv")
    bt.returns.to_csv(out_dir / "returns.csv")
    bt.exposure.to_csv(out_dir / "exposure.csv")
    bt.holdings.to_csv(out_dir / "holdings.csv", index=False)
    pd.DataFrame([s]).to_csv(out_dir / "summary.csv", index=False)
    Path(out_dir / "BEST_PARAMS.json").write_text(
        json.dumps({"params": {**params, "tilt": tilt}, "summary": s, "hit": hit}, indent=2, default=str),
        encoding="utf-8",
    )

    fig, ax = plt.subplots(figsize=(10, 4.5))
    eq = bt.equity.dropna()
    ax.plot(eq.index, eq.values, color="#0B3D5C", lw=1.8, label="Strategy")
    spy_w = spy.resample("W-FRI").last().reindex(eq.index).ffill()
    ax.plot(spy_w.index, (spy_w / spy_w.iloc[0]).values, color="#C45C26", lw=1.2, alpha=0.8, label="SPY")
    ax.set_title(
        f"S&P500 MF Top10 Weekly | Sharpe={s['sharpe']:.2f} CAGR={s['cagr']:.1%} MDD={s['max_drawdown']:.1%}"
    )
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "nav.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.plot(bt.exposure.index, bt.exposure.values, color="#0B3D5C", lw=1.0)
    ax.set_title("Dynamic exposure")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "exposure.png", dpi=140)
    plt.close(fig)

    lines = [
        "US S&P 500 Multi-Factor Strategy (yfinance)",
        f"TARGETS HIT: {hit}",
        f"Sharpe: {s['sharpe']:.3f}  (target >= 3.0)",
        f"CAGR: {s['cagr']:.2%}  (target >= 30%)",
        f"MaxDD: {s['max_drawdown']:.2%}  (target >= -10%)",
        f"AnnVol: {s['ann_vol']:.2%}",
        f"AvgExposure: {s.get('avg_exposure', float('nan')):.2f}",
        "",
        "Selected factors (5 per category):",
    ]
    for c, fs in selected.items():
        lines.append(f"  {c}: {', '.join(fs)}")
    lines += [
        "",
        f"Params: { {**params, 'tilt': tilt} }",
        "",
        "Notes:",
        "- Universe: current S&P 500 list via public constituents CSV; prices/fundamentals from yfinance.",
        "- Weekly Friday rebalance; hold 10 names equal-weight.",
        "- Risk overlays: SPY MA regime, weekly SPY>0 filter, 4-week SPY momentum confirm, drawdown brake.",
        "- Yahoo `.info` fundamentals are point-in-time snapshots (slow characteristics); momentum/stability dominate the tilt.",
    ]
    Path(out_dir / "SUMMARY.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:10]))
    return {"summary": s, "hit": hit, "selected": selected, "params": {**params, "tilt": tilt}, "backtest": bt}
