# -*- coding: utf-8 -*-
"""Weekly top-N portfolio backtest with risk overlays."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd


def performance_summary(equity: pd.Series, returns: pd.Series, freq: int = 52) -> Dict[str, float]:
    equity = equity.dropna()
    returns = returns.dropna()
    if len(returns) < 5:
        return {
            "total_return": np.nan,
            "cagr": np.nan,
            "ann_vol": np.nan,
            "sharpe": np.nan,
            "max_drawdown": np.nan,
            "calmar": np.nan,
            "win_rate": np.nan,
            "n_obs": float(len(returns)),
        }
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    years = max(len(returns) / freq, 1e-9)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0)
    vol = float(returns.std(ddof=0) * np.sqrt(freq))
    sharpe = float(returns.mean() / returns.std(ddof=0) * np.sqrt(freq)) if returns.std(ddof=0) > 0 else 0.0
    dd = equity / equity.cummax() - 1.0
    mdd = float(dd.min())
    calmar = float(cagr / abs(mdd)) if mdd < 0 else np.nan
    return {
        "total_return": total,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "calmar": calmar,
        "win_rate": float((returns > 0).mean()),
        "n_obs": float(len(returns)),
    }


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    exposure: pd.Series
    summary: Dict[str, float]
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    diagnostics: Dict[str, pd.Series] = field(default_factory=dict)


def combine_selected_scores(
    signed_panels: Dict[str, Dict[str, pd.DataFrame]],
    selected: Dict[str, List[str]],
    category_weights: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Equal-weight factors within category, then weight categories."""
    cats = [c for c in selected if selected[c]]
    if category_weights is None:
        category_weights = {c: 1.0 / len(cats) for c in cats}
    cat_scores = {}
    for cat in cats:
        mats = []
        for name in selected[cat]:
            if name not in signed_panels.get(cat, {}):
                continue
            mats.append(signed_panels[cat][name])
        if not mats:
            continue
        # align
        score = sum(m.fillna(0.0) for m in mats) / float(len(mats))
        # mask: require at least half factors present
        avail = sum(m.notna().astype(float) for m in mats) / float(len(mats))
        score = score.where(avail >= 0.5)
        cat_scores[cat] = score

    if not cat_scores:
        raise RuntimeError("No category scores available")

    composite = None
    wsum = None
    for cat, sc in cat_scores.items():
        w = float(category_weights.get(cat, 0.0))
        part = sc * w
        mask = sc.notna().astype(float) * abs(w)
        composite = part.fillna(0.0) if composite is None else composite.add(part.fillna(0.0), fill_value=0.0)
        wsum = mask if wsum is None else wsum.add(mask, fill_value=0.0)
    return composite.div(wsum.replace(0, np.nan))


def top_n_weights(scores: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Fast top-n equal weights without iterrows."""
    arr = scores.to_numpy(dtype=float, copy=True)
    # nan -> -inf so they won't be selected
    nan_mask = ~np.isfinite(arr)
    arr = np.where(nan_mask, -np.inf, arr)
    w = np.zeros_like(arr, dtype=float)
    n_cols = arr.shape[1]
    k = min(n, n_cols)
    # argpartition for top-k
    idx = np.argpartition(arr, -k, axis=1)[:, -k:]
    rows = np.arange(arr.shape[0])[:, None]
    top_vals = arr[rows, idx]
    # valid if finite
    valid = np.isfinite(top_vals)
    # need enough finite
    enough = valid.sum(axis=1) >= max(3, n // 2)
    # set equal weight among valid top-k
    for i in range(arr.shape[0]):
        if not enough[i]:
            continue
        cols = idx[i][valid[i]]
        if cols.size == 0:
            continue
        # if more than n finite in partition, take largest n
        if cols.size > n:
            order = np.argsort(arr[i, cols])[::-1][:n]
            cols = cols[order]
        w[i, cols] = 1.0 / cols.size
    return pd.DataFrame(w, index=scores.index, columns=scores.columns)


def apply_regime_exposure(
    dates: pd.DatetimeIndex,
    spy: pd.Series,
    fast: int = 10,
    slow: int = 40,
    mode: str = "ma",
) -> pd.Series:
    """Weekly SPY regime known at prior close (shifted)."""
    spy_w = spy.resample("W-FRI").last().reindex(dates).ffill()
    if mode == "ma":
        on = (spy_w > spy_w.rolling(slow).mean()) & (
            spy_w.pct_change(fast).fillna(0) > -0.05
        )
    elif mode == "abs_mom":
        on = spy_w.pct_change(slow) > 0
    else:
        on = pd.Series(True, index=dates)
    return on.astype(float).shift(1).fillna(0.0)


def apply_vol_target(
    raw_returns: pd.Series,
    target_vol: float = 0.12,
    lever_cap: float = 2.0,
    lookback: int = 12,
) -> pd.Series:
    """Compute exposure series from rolling vol of raw strategy returns."""
    vol = raw_returns.rolling(lookback, min_periods=max(4, lookback // 2)).std(ddof=0) * np.sqrt(52)
    exp = (target_vol / vol.replace(0, np.nan)).clip(upper=lever_cap).shift(1).fillna(0.0)
    return exp


def apply_drawdown_brake(
    equity: pd.Series,
    soft: float = -0.05,
    hard: float = -0.08,
) -> pd.Series:
    """Scale exposure based on current drawdown (causal via shift)."""
    dd = equity / equity.cummax() - 1.0
    scale = pd.Series(1.0, index=equity.index)
    scale[dd <= soft] = 0.5
    scale[dd <= hard] = 0.0
    return scale.shift(1).fillna(1.0)


def run_weekly_backtest(
    scores: pd.DataFrame,
    weekly_returns: pd.DataFrame,
    top_n: int = 10,
    cost_bps: float = 10.0,
    spy: Optional[pd.Series] = None,
    regime_mode: str = "ma",
    regime_fast: int = 10,
    regime_slow: int = 40,
    vol_target: Optional[float] = 0.12,
    lever_cap: float = 2.0,
    dd_soft: float = -0.05,
    dd_hard: float = -0.08,
    use_regime: bool = True,
    use_vol_target: bool = True,
    use_dd_brake: bool = True,
) -> BacktestResult:
    scores = scores.reindex(weekly_returns.index).copy()
    weights = top_n_weights(scores, n=top_n)
    # gross portfolio return before overlays
    gross = (weights.shift(0) * weekly_returns.fillna(0.0)).sum(axis=1)
    # turnover costs: weights already for this week's holding earning this week's return
    # signal at t-1 → we use scores lagged already; weights[t] * ret[t]
    to = 0.5 * weights.fillna(0.0).diff().abs().sum(axis=1)
    cost = to * (cost_bps / 10000.0)
    net = gross - cost

    exposure = pd.Series(1.0, index=net.index)
    if use_regime and spy is not None:
        exposure = exposure * apply_regime_exposure(
            net.index, spy, fast=regime_fast, slow=regime_slow, mode=regime_mode
        )

    # preliminary equity for DD brake on unlevered regime-filtered book
    base = net * exposure
    eq0 = (1.0 + base.fillna(0.0)).cumprod()
    if use_dd_brake:
        exposure = exposure * apply_drawdown_brake(eq0, soft=dd_soft, hard=dd_hard)

    filtered = net * exposure
    if use_vol_target and vol_target is not None:
        vt = apply_vol_target(filtered, target_vol=vol_target, lever_cap=lever_cap)
        exposure = exposure * vt
        filtered = net * exposure

    equity = (1.0 + filtered.fillna(0.0)).cumprod()
    summary = performance_summary(equity, filtered.fillna(0.0))
    summary["avg_turnover"] = float(to.replace([np.inf], np.nan).dropna().mean())
    summary["avg_exposure"] = float(exposure.mean())
    summary["max_leverage"] = float(exposure.max())

    # holdings log
    hold_rows = []
    for dt, row in weights.iterrows():
        picks = row[row > 0].sort_values(ascending=False)
        if picks.empty:
            continue
        hold_rows.append({"date": dt, "tickers": ",".join(picks.index.astype(str))})
    holdings = pd.DataFrame(hold_rows)

    return BacktestResult(
        equity=equity.rename("equity"),
        returns=filtered.rename("ret"),
        weights=weights,
        exposure=exposure.rename("exposure"),
        summary=summary,
        holdings=holdings,
        diagnostics={"turnover": to, "gross": gross, "cost": cost},
    )
