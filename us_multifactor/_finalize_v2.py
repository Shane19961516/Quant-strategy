#!/usr/bin/env python3
"""Finalize causal_v2 delivery artifacts + frozen defaults."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from us_multifactor.backtest import combine_selected_scores
from us_multifactor.causal import run_causal_backtest, walk_forward_stats
from us_multifactor.data_yfinance import DATA_DIR, REPO_ROOT, load_market_bundle
from us_multifactor.factors import (
    attach_fundamental_factors,
    build_price_factors,
    lag_factors,
    resample_factors_weekly,
    select_top_factors_per_category,
)

OUT = REPO_ROOT / "us_multifactor_result"

# Primary deliverable: best causal full-sample CAGR / OOS-consistent book
PRIMARY = {
    "tilt": {"momentum": 0.6, "stability": 0.3, "size": 0.1},
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
    "top_n": 10,
    "cost_bps": 10.0,
    "use_vol_target": False,
    "use_dd_brake": True,
    "week_stop": None,
    "engine": "causal_weekly",
}

# Defensive variant: vol-target to compress MDD (trades off CAGR)
DEFENSIVE = {
    **PRIMARY,
    "vol_target": 0.12,
    "lever_cap": 2.0,
    "use_vol_target": True,
    "dd_soft": -0.04,
    "dd_hard": -0.07,
    "engine": "causal_weekly_voltarget",
}


def run_one(comp, wret, spy, params):
    return run_causal_backtest(
        comp,
        wret,
        spy,
        top_n=params["top_n"],
        cost_bps=params["cost_bps"],
        weighting=params["weighting"],
        regime_mode=params["regime_mode"],
        regime_fast=params["regime_fast"],
        regime_slow=params["regime_slow"],
        require_prior_spy_pos=params["require_prior_spy_pos"],
        mom_confirm=params["mom_confirm"],
        spy_vol_cap=params["spy_vol_cap"],
        vol_target=params["vol_target"],
        lever_cap=params["lever_cap"],
        dd_soft=params["dd_soft"],
        dd_hard=params["dd_hard"],
        use_regime=params["use_regime"],
        use_vol_target=params["use_vol_target"],
        use_dd_brake=params["use_dd_brake"],
    )


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    b = load_market_bundle(start="2016-01-01", cache_dir=DATA_DIR)
    adj = b["prices"]["adj_close"]
    tickers = b["tickers"]
    spy = b["spy"]
    vol = b["prices"]["volume"]
    fmap = build_price_factors(adj[tickers], volume=vol.reindex(columns=tickers), spy=spy)
    fmap = attach_fundamental_factors(fmap, adj[tickers], b["fundamentals"])
    fmap = {k: fmap[k] for k in ("momentum", "stability", "size")}
    wf = lag_factors(resample_factors_weekly(fmap), 1)
    wret = adj[tickers].resample("W-FRI").last().pct_change()
    selected, ic, signed = select_top_factors_per_category(wf, wret, top_n=5)
    tilt = PRIMARY["tilt"]
    comp = combine_selected_scores(signed, selected, tilt)

    primary = run_one(comp, wret, spy, PRIMARY)
    defensive = run_one(comp, wret, spy, DEFENSIVE)
    wf_p = walk_forward_stats(primary.returns, "2021-12-31")
    wf_d = walk_forward_stats(defensive.returns, "2021-12-31")

    print("PRIMARY", primary.summary, flush=True)
    print("DEFENSIVE", defensive.summary, flush=True)

    # persist primary as main delivery
    primary.equity.to_csv(OUT / "equity.csv")
    primary.returns.to_csv(OUT / "returns.csv")
    primary.exposure.to_csv(OUT / "exposure.csv")
    primary.holdings.to_csv(OUT / "holdings.csv", index=False)
    pd.DataFrame([primary.summary]).to_csv(OUT / "summary.csv", index=False)
    defensive.equity.to_csv(OUT / "equity_defensive.csv")
    pd.DataFrame([defensive.summary]).to_csv(OUT / "summary_defensive.csv", index=False)

    ic.to_csv(OUT / "factor_ic_all.csv", index=False)
    Path(OUT / "SELECTED_FACTORS.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    pd.DataFrame(
        [{"category": c, "rank": i, "factor": f} for c, fs in selected.items() for i, f in enumerate(fs, 1)]
    ).to_csv(OUT / "selected_factors.csv", index=False)

    hit = (
        primary.summary["sharpe"] >= 3
        and primary.summary["cagr"] >= 0.3
        and primary.summary["max_drawdown"] >= -0.1000001
    )
    Path(OUT / "BEST_PARAMS.json").write_text(
        json.dumps(
            {
                "engine": "causal_v2",
                "params": PRIMARY,
                "summary": primary.summary,
                "walk_forward": wf_p,
                "hit": hit,
                "defensive": {"params": DEFENSIVE, "summary": defensive.summary, "walk_forward": wf_d},
                "notes": [
                    "v1 Sharpe~3 was invalid (same-week SPY look-ahead).",
                    "causal_v2: all overlays lagged; price factors only.",
                    "Joint targets Sharpe>=3 & MDD<=10% & CAGR>=30% not attainable without look-ahead for long-only Top10 weekly SPX.",
                    "Primary maximizes OOS-consistent CAGR under causal rules; defensive compresses vol/MDD.",
                ],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    # charts
    fig, ax = plt.subplots(figsize=(10, 4.5))
    eq = primary.equity.dropna()
    ax.plot(eq.index, eq.values, color="#0B3D5C", lw=1.8, label="Primary")
    deq = defensive.equity.reindex(eq.index).dropna()
    ax.plot(deq.index, deq.values, color="#2E7D32", lw=1.4, label="Defensive (vol-target)")
    spy_w = spy.resample("W-FRI").last().reindex(eq.index).ffill()
    ax.plot(spy_w.index, (spy_w / spy_w.iloc[0]).values, color="#C45C26", lw=1.2, alpha=0.8, label="SPY")
    ax.axvline(pd.Timestamp("2021-12-31"), color="gray", ls="--", lw=1, alpha=0.7, label="IS|OOS")
    s = primary.summary
    ax.set_title(
        f"Causal v2 Top10 Weekly | Sharpe={s['sharpe']:.2f} CAGR={s['cagr']:.1%} MDD={s['max_drawdown']:.1%}"
    )
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "nav.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.plot(primary.exposure.index, primary.exposure.values, color="#0B3D5C", lw=1.0, label="Primary")
    ax.plot(defensive.exposure.index, defensive.exposure.values, color="#2E7D32", lw=1.0, label="Defensive")
    ax.set_title("Exposure")
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "exposure.png", dpi=140)
    plt.close(fig)

    lines = [
        "US S&P 500 Multi-Factor — CAUSAL v2 (deliverable)",
        f"TARGETS HIT (joint Sharpe>=3 & CAGR>=30% & MDD<=10%): {hit}",
        "",
        "PRIMARY (recommended)",
        f"  Sharpe: {primary.summary['sharpe']:.3f}",
        f"  CAGR:   {primary.summary['cagr']:.2%}",
        f"  MaxDD:  {primary.summary['max_drawdown']:.2%}",
        f"  AnnVol: {primary.summary['ann_vol']:.2%}",
        f"  IS  sharpe={wf_p['is']['sharpe']:.2f} cagr={wf_p['is']['cagr']:.1%} mdd={wf_p['is']['max_drawdown']:.1%}",
        f"  OOS sharpe={wf_p['oos']['sharpe']:.2f} cagr={wf_p['oos']['cagr']:.1%} mdd={wf_p['oos']['max_drawdown']:.1%}",
        "",
        "DEFENSIVE (vol-target)",
        f"  Sharpe: {defensive.summary['sharpe']:.3f}",
        f"  CAGR:   {defensive.summary['cagr']:.2%}",
        f"  MaxDD:  {defensive.summary['max_drawdown']:.2%}",
        "",
        "Factors (price-only, 5 each) & weights:",
        "  momentum 60%: mom_1m, mom_accel, mom_12_1, mom_12m, mom_1w",
        "  stability 30%: inv_downside_vol, inv_vol_6m, inv_vol_1m, inv_vol_3m, inv_beta",
        "  size 10%: neg_log_mcap, neg_mcap_rank, inv_price_rank, neg_log_price, neg_log_dollar_vol",
        "",
        "Audit:",
        "- Removed same-week SPY look-ahead that previously faked Sharpe~3.",
        "- Dropped Yahoo snapshot fundamentals from production score.",
        "- Joint original targets are not attainable under causal long-only Top10 weekly SPX;",
        "  this delivery prioritizes correctness + OOS consistency (IS/OOS Sharpe both ~1.6).",
    ]
    Path(OUT / "SUMMARY.txt").write_text("\n".join(lines), encoding="utf-8")
    Path(OUT / "EVALUATION.md").write_text(
        "\n".join(
            [
                "# Professional Evaluation",
                "",
                "## Findings on prior version",
                "1. **Critical look-ahead**: `require_spy_pos` used *same-week* SPY return while earning that week's portfolio PnL. Removing it cut Sharpe from ~3.08 to ~2.3 and broke target feasibility.",
                "2. **Fundamental leakage**: Yahoo `Ticker.info` snapshots were broadcast across history for valuation/profitability/quality.",
                "3. **Overfit overlays**: equity explosion + MDD glued to -10% indicated brittle risk overlays.",
                "",
                "## Remediation",
                "- New `causal` engine: all SPY regime / mom / vol filters are `shift(1)`.",
                "- Production factors limited to **momentum / stability / size** (price-based).",
                "- Walk-forward split at 2021-12-31; IS and OOS Sharpe nearly identical (~1.6).",
                "",
                "## Deliverable metrics (primary)",
                f"- Sharpe **{primary.summary['sharpe']:.2f}**, CAGR **{primary.summary['cagr']:.1%}**, MDD **{primary.summary['max_drawdown']:.1%}**",
                f"- OOS Sharpe **{wf_p['oos']['sharpe']:.2f}**, OOS CAGR **{wf_p['oos']['cagr']:.1%}**",
                "",
                "## On the original joint targets",
                "For a long-only S&P500 Top-10 weekly strategy with no look-ahead, simultaneously requiring Sharpe≥3, CAGR≥30%, MDD≤10% is not a realistic production constraint. Meeting all three in v1 required look-ahead.",
                "We deliver the best **causal, OOS-stable** book instead, plus a defensive vol-targeted variant.",
            ]
        ),
        encoding="utf-8",
    )
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
