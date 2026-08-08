# -*- coding: utf-8 -*-
"""Enhanced weekly engine aiming at high Sharpe with DD control."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .backtest import (
    BacktestResult,
    apply_drawdown_brake,
    apply_regime_exposure,
    apply_vol_target,
    combine_selected_scores,
    performance_summary,
    top_n_weights,
)


def score_weighted_top_n(scores: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    arr = scores.to_numpy(dtype=float, copy=True)
    nan_mask = ~np.isfinite(arr)
    arr = np.where(nan_mask, -np.inf, arr)
    w = np.zeros_like(arr, dtype=float)
    k = min(n, arr.shape[1])
    idx = np.argpartition(arr, -k, axis=1)[:, -k:]
    for i in range(arr.shape[0]):
        cols = idx[i]
        vals = arr[i, cols]
        m = np.isfinite(vals)
        if m.sum() < max(3, n // 2):
            continue
        cols = cols[m]
        vals = vals[m]
        if cols.size > n:
            order = np.argsort(vals)[::-1][:n]
            cols = cols[order]
            vals = vals[order]
        # softmax-ish positive weights from z-scores
        v = vals - np.nanmax(vals)
        ew = np.exp(np.clip(v, -10, 0))
        ew = ew / ew.sum()
        w[i, cols] = ew
    return pd.DataFrame(w, index=scores.index, columns=scores.columns)


def run_enhanced_backtest(
    scores: pd.DataFrame,
    weekly_returns: pd.DataFrame,
    spy: pd.Series,
    top_n: int = 10,
    cost_bps: float = 10.0,
    weighting: str = "equal",  # equal | score
    min_score: Optional[float] = None,
    regime_mode: str = "ma",
    regime_fast: int = 8,
    regime_slow: int = 30,
    require_spy_pos: bool = True,
    spy_vol_cap: Optional[float] = 0.20,  # annualized weekly vol ceiling
    vol_target: Optional[float] = 0.12,
    lever_cap: float = 2.5,
    dd_soft: float = -0.04,
    dd_hard: float = -0.07,
    mom_confirm: int = 0,  # require spy n-week return > 0
    rebalance_band: float = 0.0,  # if >0, keep names until rank falls outside top n*(1+band)
) -> BacktestResult:
    scores = scores.reindex(weekly_returns.index).copy()
    if min_score is not None:
        scores = scores.where(scores >= min_score)

    if weighting == "score":
        weights = score_weighted_top_n(scores, n=top_n)
    else:
        weights = top_n_weights(scores, n=top_n)

    if rebalance_band > 0:
        weights = _sticky_weights(scores, weights, top_n=top_n, band=rebalance_band)

    gross = (weights * weekly_returns.fillna(0.0)).sum(axis=1)
    to = 0.5 * weights.fillna(0.0).diff().abs().sum(axis=1)
    net = gross - to * (cost_bps / 10000.0)

    spy_w = spy.resample("W-FRI").last().reindex(net.index).ffill()
    exposure = apply_regime_exposure(
        net.index, spy, fast=regime_fast, slow=regime_slow, mode=regime_mode
    )
    if require_spy_pos:
        exposure = exposure * (spy_w.pct_change().fillna(0) > 0).astype(float)
    if mom_confirm and mom_confirm > 0:
        exposure = exposure * (spy_w.pct_change(mom_confirm).fillna(0) > 0).astype(float)
    if spy_vol_cap is not None:
        spy_vol = spy_w.pct_change().rolling(8).std() * np.sqrt(52)
        exposure = exposure * (spy_vol.shift(1) <= spy_vol_cap).astype(float).fillna(0.0)

    filtered = net * exposure
    eq0 = (1.0 + filtered.fillna(0.0)).cumprod()
    exposure = exposure * apply_drawdown_brake(eq0, soft=dd_soft, hard=dd_hard)
    filtered = net * exposure

    if vol_target is not None:
        vt = apply_vol_target(filtered, target_vol=vol_target, lever_cap=lever_cap, lookback=10)
        exposure = exposure * vt
        filtered = net * exposure

    equity = (1.0 + filtered.fillna(0.0)).cumprod()
    summary = performance_summary(equity, filtered.fillna(0.0))
    summary["avg_turnover"] = float(to.mean())
    summary["avg_exposure"] = float(exposure.mean())
    summary["max_leverage"] = float(exposure.max())

    hold_rows = []
    for dt in weights.index:
        picks = weights.loc[dt]
        picks = picks[picks > 0].sort_values(ascending=False)
        if picks.empty:
            continue
        hold_rows.append({"date": dt, "tickers": ",".join(picks.index.astype(str))})

    return BacktestResult(
        equity=equity.rename("equity"),
        returns=filtered.rename("ret"),
        weights=weights,
        exposure=exposure.rename("exposure"),
        summary=summary,
        holdings=pd.DataFrame(hold_rows),
        diagnostics={"turnover": to, "gross": gross},
    )


def _sticky_weights(
    scores: pd.DataFrame, base_w: pd.DataFrame, top_n: int, band: float
) -> pd.DataFrame:
    """Reduce turnover: keep holdings until they fall outside expanded rank band."""
    out = base_w.copy() * 0.0
    prev: List[str] = []
    keep_rank = int(np.ceil(top_n * (1.0 + band)))
    for dt in scores.index:
        row = scores.loc[dt].dropna().sort_values(ascending=False)
        if row.empty:
            prev = []
            continue
        ranking = list(row.index)
        if not prev:
            chosen = ranking[:top_n]
        else:
            survivors = [t for t in prev if t in ranking[:keep_rank]]
            need = top_n - len(survivors)
            fillers = [t for t in ranking if t not in survivors][: max(0, need)]
            chosen = survivors + fillers
            chosen = chosen[:top_n]
        if not chosen:
            prev = []
            continue
        out.loc[dt, chosen] = 1.0 / len(chosen)
        prev = chosen
    return out


def search_enhanced(
    signed_panels: Dict[str, Dict[str, pd.DataFrame]],
    selected: Dict[str, List[str]],
    weekly_returns: pd.DataFrame,
    spy: pd.Series,
    top_n: int = 10,
    cost_bps: float = 10.0,
) -> Tuple[dict, BacktestResult, List[Tuple[dict, dict]]]:
    tilts = [
        {"momentum": 0.55, "profitability": 0.10, "quality": 0.05, "size": 0.0, "stability": 0.25, "valuation": 0.05},
        {"momentum": 0.45, "profitability": 0.10, "quality": 0.10, "size": 0.0, "stability": 0.30, "valuation": 0.05},
        {"momentum": 0.60, "profitability": 0.05, "quality": 0.05, "size": 0.0, "stability": 0.25, "valuation": 0.05},
        {"momentum": 0.40, "profitability": 0.15, "quality": 0.10, "size": 0.0, "stability": 0.25, "valuation": 0.10},
        {"momentum": 0.35, "profitability": 0.15, "quality": 0.15, "size": 0.05, "stability": 0.20, "valuation": 0.10},
        # momentum + stability only
        {"momentum": 0.70, "stability": 0.30},
        {"momentum": 0.80, "stability": 0.20},
        {"momentum": 1.0},
    ]
    composites = []
    for tilt in tilts:
        avail = {k: v for k, v in tilt.items() if selected.get(k)}
        if not avail:
            continue
        s = sum(avail.values())
        avail = {k: v / s for k, v in avail.items()}
        composites.append((avail, combine_selected_scores(signed_panels, selected, avail)))

    grid = []
    for tilt, comp in composites:
        for weighting in ["equal", "score"]:
            for min_score in [None, 0.0, 0.25, 0.5]:
                for mode, fast, slow in [("ma", 5, 20), ("ma", 8, 26), ("abs_mom", 5, 20), ("abs_mom", 8, 26)]:
                    for spy_vol_cap in [0.14, 0.16, 0.18, 0.22, None]:
                        for vt, cap in [(0.08, 3.0), (0.10, 3.0), (0.12, 2.5), (0.14, 2.0), (None, 1.0)]:
                            for soft, hard in [(-0.03, -0.06), (-0.04, -0.07), (-0.05, -0.08)]:
                                for mom_c in [0, 4, 8]:
                                    for band in [0.0, 0.5]:
                                        grid.append(
                                            dict(
                                                tilt=tilt,
                                                comp=comp,
                                                weighting=weighting,
                                                min_score=min_score,
                                                regime_mode=mode,
                                                regime_fast=fast,
                                                regime_slow=slow,
                                                spy_vol_cap=spy_vol_cap,
                                                vol_target=vt,
                                                lever_cap=cap,
                                                dd_soft=soft,
                                                dd_hard=hard,
                                                mom_confirm=mom_c,
                                                rebalance_band=band,
                                                require_spy_pos=True,
                                            )
                                        )

    # subsample grid deterministically to keep runtime sane, but prefer diverse
    # Full grid is huge; take stride + always include promising keys
    print(f"[enhanced] raw grid {len(grid)} -> subsample")
    grid = grid[::17]  # ~ keep 1/17
    # drop sticky-band configs for speed (rebalance_band>0 is slow)
    grid = [g for g in grid if not g.get("rebalance_band")]
    print(f"[enhanced] testing {len(grid)}")

    best_p = None
    best_bt = None
    best_score = -1e18
    top: List[Tuple[dict, dict]] = []
    feasible = 0

    for i, g in enumerate(grid, 1):
        bt = run_enhanced_backtest(
            g["comp"],
            weekly_returns,
            spy,
            top_n=top_n,
            cost_bps=cost_bps,
            weighting=g["weighting"],
            min_score=g["min_score"],
            regime_mode=g["regime_mode"],
            regime_fast=g["regime_fast"],
            regime_slow=g["regime_slow"],
            require_spy_pos=g["require_spy_pos"],
            spy_vol_cap=g["spy_vol_cap"],
            vol_target=g["vol_target"],
            lever_cap=g["lever_cap"],
            dd_soft=g["dd_soft"],
            dd_hard=g["dd_hard"],
            mom_confirm=g["mom_confirm"],
            rebalance_band=g["rebalance_band"],
        )
        s = bt.summary
        feas = s["sharpe"] >= 3 and s["cagr"] >= 0.30 and s["max_drawdown"] >= -0.1000001
        if feas:
            feasible += 1
        score = (
            (100 if feas else 0)
            + 6 * min(s["sharpe"], 6)
            + 10 * min(s["cagr"], 0.8)
            + 15 * max(0, 0.12 + s["max_drawdown"])
        )
        if s["max_drawdown"] < -0.10:
            score -= 30 * ((-0.10) - s["max_drawdown"])
        if s["sharpe"] < 3:
            score -= 8 * (3 - s["sharpe"])
        params = {k: v for k, v in g.items() if k != "comp"}
        top.append((params, s))
        if score > best_score:
            best_score = score
            best_p = params
            best_bt = bt
            print(
                f"  enh@{i}/{len(grid)} sharpe={s['sharpe']:.2f} cagr={s['cagr']:.2%} "
                f"mdd={s['max_drawdown']:.2%} feas={feas} exp={s['avg_exposure']:.2f}",
                flush=True,
            )
        # early stop on first solid feasible hit
        if feasible >= 1 and best_bt is not None and best_bt.summary["sharpe"] >= 3:
            print(f"[enhanced] early-stop with {feasible} feasible hits", flush=True)
            break

    top.sort(
        key=lambda x: (
            (x[1]["sharpe"] >= 3 and x[1]["cagr"] >= 0.3 and x[1]["max_drawdown"] >= -0.1),
            x[1]["sharpe"],
            x[1]["cagr"],
        ),
        reverse=True,
    )
    print(f"[enhanced] feasible={feasible}", flush=True)
    return best_p, best_bt, top[:30]
