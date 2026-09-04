# -*- coding: utf-8 -*-
"""Production-grade causal weekly engine (no same-week look-ahead)."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .backtest import (
    BacktestResult,
    apply_drawdown_brake,
    apply_vol_target,
    combine_selected_scores,
    performance_summary,
    top_n_weights,
)
from .enhanced import score_weighted_top_n, _sticky_weights


def causal_regime_exposure(
    dates: pd.DatetimeIndex,
    spy: pd.Series,
    fast: int = 10,
    slow: int = 40,
    mode: str = "ma",
) -> pd.Series:
    """Risk-on flag known at prior week close (shift 1)."""
    spy_w = spy.resample("W-FRI").last().reindex(dates).ffill()
    if mode in ("off", "always", "none"):
        on = pd.Series(True, index=dates)
    elif mode == "ma":
        on = (spy_w > spy_w.rolling(slow).mean()) & (spy_w.pct_change(fast).fillna(0) > -0.05)
    elif mode == "abs_mom":
        on = spy_w.pct_change(slow) > 0
    else:
        on = pd.Series(True, index=dates)
    return on.astype(float).shift(1).fillna(0.0)


def run_causal_backtest(
    scores: pd.DataFrame,
    weekly_returns: pd.DataFrame,
    spy: pd.Series,
    top_n: int = 10,
    cost_bps: float = 10.0,
    weighting: str = "equal",
    min_score: Optional[float] = None,
    regime_mode: str = "ma",
    regime_fast: int = 8,
    regime_slow: int = 30,
    require_prior_spy_pos: bool = True,
    mom_confirm: int = 4,
    spy_vol_cap: Optional[float] = 0.18,
    vol_target: Optional[float] = 0.12,
    lever_cap: float = 2.5,
    dd_soft: float = -0.04,
    dd_hard: float = -0.07,
    rebalance_band: float = 0.0,
    use_regime: bool = True,
    use_dd_brake: bool = True,
    use_vol_target: bool = True,
) -> BacktestResult:
    """Weekly Top-N book with strictly lagged overlays.

    Timing convention
    -----------------
    - ``scores`` must already be lagged (known at prior close).
    - ``weights[t] * weekly_returns[t]`` earns the week ending at t.
    - All SPY regime / momentum / vol filters are ``shift(1)`` so only
      information through week t-1 is used.
    """
    scores = scores.reindex(weekly_returns.index).copy()
    if min_score is not None:
        scores = scores.where(scores >= min_score)

    if weighting == "score":
        weights = score_weighted_top_n(scores, n=top_n)
    else:
        weights = top_n_weights(scores, n=top_n)

    if rebalance_band and rebalance_band > 0:
        weights = _sticky_weights(scores, weights, top_n=top_n, band=rebalance_band)

    gross = (weights * weekly_returns.fillna(0.0)).sum(axis=1)
    to = 0.5 * weights.fillna(0.0).diff().abs().sum(axis=1)
    net = gross - to * (cost_bps / 10000.0)

    spy_w = spy.resample("W-FRI").last().reindex(net.index).ffill()
    exposure = pd.Series(1.0, index=net.index)

    if use_regime:
        exposure = exposure * causal_regime_exposure(
            net.index, spy, fast=regime_fast, slow=regime_slow, mode=regime_mode
        )
    if require_prior_spy_pos:
        # prior week SPY return > 0
        exposure = exposure * (spy_w.pct_change().shift(1).fillna(0) > 0).astype(float)
    if mom_confirm and mom_confirm > 0:
        # trailing mom_confirm weeks ending at t-1
        exposure = exposure * (spy_w.pct_change(mom_confirm).shift(1).fillna(0) > 0).astype(float)
    if spy_vol_cap is not None:
        spy_vol = spy_w.pct_change().rolling(8).std() * np.sqrt(52)
        exposure = exposure * (spy_vol.shift(1) <= spy_vol_cap).astype(float).fillna(0.0)

    filtered = net * exposure
    if use_dd_brake:
        eq0 = (1.0 + filtered.fillna(0.0)).cumprod()
        exposure = exposure * apply_drawdown_brake(eq0, soft=dd_soft, hard=dd_hard)
        filtered = net * exposure

    if use_vol_target and vol_target is not None:
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
        if not picks.empty:
            hold_rows.append({"date": dt, "tickers": ",".join(picks.index.astype(str))})

    return BacktestResult(
        equity=equity.rename("equity"),
        returns=filtered.rename("ret"),
        weights=weights,
        exposure=exposure.rename("exposure"),
        summary=summary,
        holdings=pd.DataFrame(hold_rows),
        diagnostics={"turnover": to, "gross": gross, "net_pre_overlay": net},
    )


def walk_forward_stats(
    returns: pd.Series,
    is_end: str,
) -> Dict[str, Dict[str, float]]:
    """Split summary into IS / OOS around ``is_end``."""
    eq = (1.0 + returns.fillna(0.0)).cumprod()
    is_mask = returns.index <= pd.Timestamp(is_end)
    oos_mask = returns.index > pd.Timestamp(is_end)
    out = {}
    for name, mask in [("is", is_mask), ("oos", oos_mask), ("full", pd.Series(True, index=returns.index))]:
        r = returns[mask]
        if r.empty:
            out[name] = {}
            continue
        e = (1.0 + r.fillna(0.0)).cumprod()
        out[name] = performance_summary(e, r.fillna(0.0))
    return out
