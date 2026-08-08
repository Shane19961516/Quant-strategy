# -*- coding: utf-8 -*-
"""Causal weekly selection + optional daily stop overlay."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .backtest import (
    BacktestResult,
    apply_drawdown_brake,
    apply_vol_target,
    performance_summary,
    top_n_weights,
)
from .causal import causal_regime_exposure
from .enhanced import score_weighted_top_n


def run_causal_daily_stop_backtest(
    scores: pd.DataFrame,
    weekly_returns: pd.DataFrame,
    daily_returns: pd.DataFrame,
    spy: pd.Series,
    top_n: int = 10,
    cost_bps: float = 10.0,
    weighting: str = "equal",
    regime_mode: str = "ma",
    regime_fast: int = 8,
    regime_slow: int = 26,
    require_prior_spy_pos: bool = False,
    mom_confirm: int = 4,
    spy_vol_cap: Optional[float] = 0.18,
    vol_target: Optional[float] = 0.12,
    lever_cap: float = 3.0,
    dd_soft: float = -0.04,
    dd_hard: float = -0.07,
    week_stop: float = -0.02,
    use_daily_stop: bool = True,
    use_vol_target: bool = True,
    use_dd_brake: bool = True,
    use_regime: bool = True,
) -> BacktestResult:
    """Weekly Top-N with lagged overlays; optional causal intra-week daily stop."""
    scores = scores.reindex(weekly_returns.index).copy()
    weights_w = (
        score_weighted_top_n(scores, n=top_n)
        if weighting == "score"
        else top_n_weights(scores, n=top_n)
    )

    daily_ret = daily_returns.reindex(columns=weights_w.columns).sort_index()
    periods = daily_ret.index.to_period("W-FRI")

    w_daily = pd.DataFrame(0.0, index=daily_ret.index, columns=daily_ret.columns)
    for dt, wrow in weights_w.iterrows():
        per = pd.Timestamp(dt).to_period("W-FRI")
        mask = periods == per
        if mask.any():
            w_daily.loc[mask, :] = wrow.values

    # pre-stop daily portfolio returns
    port_raw = (w_daily * daily_ret.fillna(0.0)).sum(axis=1)

    if use_daily_stop and week_stop is not None:
        exposure_d = pd.Series(1.0, index=daily_ret.index)
        for _, idx in daily_ret.groupby(periods).groups.items():
            idx = list(pd.DatetimeIndex(idx))
            cum = 0.0
            stopped = False
            for j, d in enumerate(idx):
                if stopped:
                    exposure_d.loc[d] = 0.0
                    continue
                r = float(port_raw.loc[d])
                cum = (1.0 + cum) * (1.0 + r) - 1.0
                # breach → flatten remaining days (not today; today already earned)
                if cum <= week_stop and j < len(idx) - 1:
                    stopped = True
        port_daily = port_raw * exposure_d
        # also zero weights after stop for turnover realism (approx)
        w_daily = w_daily.mul(exposure_d, axis=0)
    else:
        port_daily = port_raw

    to = 0.5 * weights_w.fillna(0.0).diff().abs().sum(axis=1)
    cost_daily = pd.Series(0.0, index=daily_ret.index)
    for dt, c in (to * (cost_bps / 10000.0)).items():
        per = pd.Timestamp(dt).to_period("W-FRI")
        days = daily_ret.index[periods == per]
        if len(days):
            cost_daily.loc[days[0]] += float(c)

    net_daily = port_daily - cost_daily
    net_weekly = (1.0 + net_daily).groupby(periods).prod() - 1.0
    net_weekly.index = net_weekly.index.to_timestamp(how="end").normalize()
    # align to weights index
    net_weekly = net_weekly.reindex(weights_w.index).fillna(0.0)

    spy_w = spy.resample("W-FRI").last().reindex(net_weekly.index).ffill()
    if use_regime:
        exposure = causal_regime_exposure(
            net_weekly.index, spy, fast=regime_fast, slow=regime_slow, mode=regime_mode
        )
        if require_prior_spy_pos:
            exposure = exposure * (spy_w.pct_change().shift(1).fillna(0) > 0).astype(float)
        if mom_confirm and mom_confirm > 0:
            exposure = exposure * (spy_w.pct_change(mom_confirm).shift(1).fillna(0) > 0).astype(float)
        if spy_vol_cap is not None:
            spy_vol = spy_w.pct_change().rolling(8).std() * np.sqrt(52)
            exposure = exposure * (spy_vol.shift(1) <= spy_vol_cap).astype(float).fillna(0.0)
    else:
        exposure = pd.Series(1.0, index=net_weekly.index)

    filtered_w = net_weekly * exposure
    if use_dd_brake:
        eq0 = (1.0 + filtered_w.fillna(0.0)).cumprod()
        exposure = exposure * apply_drawdown_brake(eq0, soft=dd_soft, hard=dd_hard)
        filtered_w = net_weekly * exposure
    if use_vol_target and vol_target is not None:
        vt = apply_vol_target(filtered_w, target_vol=vol_target, lever_cap=lever_cap, lookback=10)
        exposure = exposure * vt
        filtered_w = net_weekly * exposure

    equity = (1.0 + filtered_w.fillna(0.0)).cumprod()
    summary = performance_summary(equity, filtered_w.fillna(0.0))
    summary["avg_turnover"] = float(to.mean())
    summary["avg_exposure"] = float(exposure.mean())
    summary["max_leverage"] = float(exposure.max())

    hold_rows = [
        {"date": dt, "tickers": ",".join(row[row > 0].sort_values(ascending=False).index.astype(str))}
        for dt, row in weights_w.iterrows()
        if (row > 0).any()
    ]

    return BacktestResult(
        equity=equity.rename("equity"),
        returns=filtered_w.rename("ret"),
        weights=weights_w,
        exposure=exposure.rename("exposure"),
        summary=summary,
        holdings=pd.DataFrame(hold_rows),
        diagnostics={"turnover": to, "net_weekly_pre": net_weekly},
    )
