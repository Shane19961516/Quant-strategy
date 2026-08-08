# -*- coding: utf-8 -*-
"""Frozen best-config runner (causal v2, reproducible)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .backtest import combine_selected_scores
from .causal import run_causal_backtest, walk_forward_stats
from .data_yfinance import DATA_DIR, REPO_ROOT, load_market_bundle
from .factors import (
    attach_fundamental_factors,
    build_price_factors,
    lag_factors,
    resample_factors_weekly,
    select_top_factors_per_category,
)

# Causal v2 primary defaults (no look-ahead)
DEFAULT_PARAMS: Dict[str, Any] = {
    "start": "2016-01-01",
    "top_n": 10,
    "cost_bps": 10.0,
    "tilt": {"momentum": 0.60, "stability": 0.30, "size": 0.10},
    "weighting": "equal",
    "use_regime": False,
    "regime_mode": "ma",
    "regime_fast": 8,
    "regime_slow": 26,
    "require_prior_spy_pos": False,
    "mom_confirm": 0,
    "spy_vol_cap": None,
    "vol_target": None,
    "lever_cap": 1.0,
    "dd_soft": -0.06,
    "dd_hard": -0.10,
    "use_vol_target": False,
    "use_dd_brake": True,
}


def run_frozen(
    out_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    params: Optional[Dict[str, Any]] = None,
    reselect_factors: bool = True,
) -> Dict[str, Any]:
    """Reproduce the causal v2 weekly Top-10 multi-factor book."""
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
    fmap = {k: fmap[k] for k in ("momentum", "stability", "size")}
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

    bt = run_causal_backtest(
        comp,
        wret,
        spy,
        top_n=int(params["top_n"]),
        cost_bps=float(params["cost_bps"]),
        weighting=params["weighting"],
        regime_mode=params["regime_mode"],
        regime_fast=int(params["regime_fast"]),
        regime_slow=int(params["regime_slow"]),
        require_prior_spy_pos=bool(params["require_prior_spy_pos"]),
        mom_confirm=int(params["mom_confirm"]),
        spy_vol_cap=params["spy_vol_cap"],
        vol_target=params["vol_target"],
        lever_cap=float(params["lever_cap"]),
        dd_soft=float(params["dd_soft"]),
        dd_hard=float(params["dd_hard"]),
        use_regime=bool(params["use_regime"]),
        use_vol_target=bool(params["use_vol_target"]),
        use_dd_brake=bool(params["use_dd_brake"]),
    )
    s = bt.summary
    wfstats = walk_forward_stats(bt.returns, "2021-12-31")
    hit = s["sharpe"] >= 3 and s["cagr"] >= 0.30 and s["max_drawdown"] >= -0.1000001

    bt.equity.to_csv(out_dir / "equity.csv")
    bt.returns.to_csv(out_dir / "returns.csv")
    bt.exposure.to_csv(out_dir / "exposure.csv")
    bt.holdings.to_csv(out_dir / "holdings.csv", index=False)
    pd.DataFrame([s]).to_csv(out_dir / "summary.csv", index=False)
    Path(out_dir / "BEST_PARAMS.json").write_text(
        json.dumps(
            {
                "engine": "causal_v2",
                "params": {**params, "tilt": tilt},
                "summary": s,
                "walk_forward": wfstats,
                "hit": hit,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    fig, ax = plt.subplots(figsize=(10, 4.5))
    eq = bt.equity.dropna()
    ax.plot(eq.index, eq.values, color="#0B3D5C", lw=1.8, label="Strategy")
    spy_w = spy.resample("W-FRI").last().reindex(eq.index).ffill()
    ax.plot(spy_w.index, (spy_w / spy_w.iloc[0]).values, color="#C45C26", lw=1.2, alpha=0.8, label="SPY")
    ax.axvline(pd.Timestamp("2021-12-31"), color="gray", ls="--", lw=1, alpha=0.7, label="IS|OOS")
    ax.set_title(
        f"Causal v2 Top10 Weekly | Sharpe={s['sharpe']:.2f} CAGR={s['cagr']:.1%} MDD={s['max_drawdown']:.1%}"
    )
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "nav.png", dpi=140)
    plt.close(fig)

    lines = [
        "US S&P 500 Multi-Factor — CAUSAL v2",
        f"joint_targets_hit={hit}",
        f"Sharpe={s['sharpe']:.3f} CAGR={s['cagr']:.2%} MaxDD={s['max_drawdown']:.2%}",
        f"OOS Sharpe={wfstats.get('oos', {}).get('sharpe', float('nan')):.3f} "
        f"CAGR={wfstats.get('oos', {}).get('cagr', float('nan')):.2%}",
        f"tilt={tilt}",
    ]
    Path(out_dir / "SUMMARY.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return {
        "summary": s,
        "hit": hit,
        "selected": selected,
        "params": {**params, "tilt": tilt},
        "backtest": bt,
        "walk_forward": wfstats,
    }
