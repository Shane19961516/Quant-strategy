"""Equal-risk blend of single-asset strategy equity curves."""

from __future__ import annotations

import numpy as np
import pandas as pd


def equal_risk_weights(navs: pd.DataFrame) -> pd.Series:
    rets = navs.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    vol = rets.std(ddof=1).replace(0.0, np.nan)
    inv = 1.0 / vol
    w = inv / inv.sum()
    return w.fillna(0.0)


def equal_risk_nav(
    navs: pd.DataFrame,
    target_vol: float | None = None,
    max_leverage: float = 8.0,
) -> pd.Series:
    rets = navs.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    w = equal_risk_weights(navs)
    port = (rets * w).sum(axis=1)
    if target_vol is not None:
        pvol = float(port.std(ddof=1) * np.sqrt(252)) if len(port) > 5 else float("nan")
        if pvol and pvol > 0:
            leverage = min(float(target_vol / pvol), float(max_leverage))
            port = port * leverage
    eq = (1.0 + port).cumprod()
    if len(eq):
        eq.iloc[0] = 1.0
    return (eq * 1_000_000.0).rename("portfolio")


def blend_equal_risk(
    navs: pd.DataFrame,
    target_vol: float | None = None,
    max_leverage: float = 8.0,
) -> dict:
    """Inverse-vol mix, optional vol targeting. Returns NAV plus sizing diagnostics."""
    w = equal_risk_weights(navs)
    unlevered = equal_risk_nav(navs, target_vol=None)
    urets = unlevered.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    raw_vol = float(urets.std(ddof=1) * np.sqrt(252)) if len(urets) > 5 else float("nan")
    leverage = 1.0
    if target_vol is not None and raw_vol and raw_vol > 0:
        leverage = min(float(target_vol / raw_vol), float(max_leverage))
    scaled = equal_risk_nav(navs, target_vol=target_vol, max_leverage=max_leverage)
    return {
        "nav": scaled,
        "nav_unlevered": unlevered,
        "weights": w,
        "raw_vol": raw_vol,
        "leverage": float(leverage),
        "target_vol": target_vol,
    }
