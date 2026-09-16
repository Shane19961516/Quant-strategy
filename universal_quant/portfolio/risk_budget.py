"""Equal-risk blend of single-asset strategy equity curves."""

from __future__ import annotations

import numpy as np
import pandas as pd


def align_business_days(navs: pd.DataFrame) -> pd.DataFrame:
    """Join on business days so weekend crypto bars do not inflate 252-day stats."""
    out = navs.sort_index()
    if not isinstance(out.index, pd.DatetimeIndex):
        return out
    return out.resample("B").last().ffill().dropna(how="all")


def equal_risk_weights(navs: pd.DataFrame) -> pd.Series:
    rets = navs.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    vol = rets.std(ddof=1).replace(0.0, np.nan)
    inv = 1.0 / vol
    w = inv / inv.sum()
    return w.fillna(0.0)


def calendar_cagr(eq: pd.Series) -> float:
    s = eq.dropna()
    if len(s) < 2 or not s.iloc[0]:
        return float("nan")
    days = (s.index[-1] - s.index[0]).days if isinstance(s.index, pd.DatetimeIndex) else max(len(s) - 1, 1)
    years = max(days / 365.25, 1e-9)
    return float((s.iloc[-1] / s.iloc[0]) ** (1.0 / years) - 1.0)


def _scale_returns(rets: pd.Series, leverage: float) -> pd.Series:
    eq = (1.0 + rets * leverage).cumprod()
    if len(eq):
        eq.iloc[0] = 1.0
    return (eq * 1_000_000.0).rename("portfolio")


def leverage_for_cagr(unlevered: pd.Series, target_cagr: float, max_leverage: float = 8.0) -> float:
    """Binary-search arithmetic-return leverage that hits calendar CAGR."""
    rets = unlevered.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if len(rets) < 10 or target_cagr is None:
        return 1.0
    lo, hi = 0.05, float(max_leverage)
    best = 1.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        cagr = calendar_cagr(_scale_returns(rets, mid))
        best = mid
        if cagr > target_cagr:
            hi = mid
        else:
            lo = mid
    return float(min(max(best, 0.0), max_leverage))


def equal_risk_nav(
    navs: pd.DataFrame,
    target_vol: float | None = None,
    max_leverage: float = 8.0,
    target_cagr: float | None = None,
) -> pd.Series:
    aligned = align_business_days(navs)
    rets = aligned.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    w = equal_risk_weights(aligned)
    port = (rets * w).sum(axis=1)
    unlevered = _scale_returns(port, 1.0)
    leverage = 1.0
    if target_cagr is not None:
        leverage = leverage_for_cagr(unlevered, target_cagr, max_leverage=max_leverage)
    if target_vol is not None:
        pvol = float(port.std(ddof=1) * np.sqrt(252)) if len(port) > 5 else float("nan")
        if pvol and pvol > 0:
            vol_cap = float(target_vol / pvol)
            if target_cagr is None:
                leverage = min(vol_cap, float(max_leverage))
            else:
                leverage = min(leverage, vol_cap, float(max_leverage))
    return _scale_returns(port, leverage)


def blend_equal_risk(
    navs: pd.DataFrame,
    target_vol: float | None = None,
    max_leverage: float = 8.0,
    target_cagr: float | None = None,
) -> dict:
    """Inverse-vol mix, optional CAGR/vol targeting. Returns NAV plus sizing diagnostics."""
    aligned = align_business_days(navs)
    w = equal_risk_weights(aligned)
    unlevered = equal_risk_nav(aligned, target_vol=None, target_cagr=None)
    urets = unlevered.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    raw_vol = float(urets.std(ddof=1) * np.sqrt(252)) if len(urets) > 5 else float("nan")
    scaled = equal_risk_nav(
        aligned,
        target_vol=target_vol,
        max_leverage=max_leverage,
        target_cagr=target_cagr,
    )
    srets = scaled.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    realized_vol = float(srets.std(ddof=1) * np.sqrt(252)) if len(srets) > 5 else float("nan")
    leverage = 1.0
    if raw_vol and raw_vol > 0 and realized_vol == realized_vol:
        leverage = float(realized_vol / raw_vol)
    return {
        "nav": scaled,
        "nav_unlevered": unlevered,
        "weights": w,
        "raw_vol": raw_vol,
        "leverage": leverage,
        "target_vol": target_vol,
        "target_cagr": target_cagr,
        "calendar_cagr": calendar_cagr(scaled),
        "calendar_cagr_unlevered": calendar_cagr(unlevered),
    }
