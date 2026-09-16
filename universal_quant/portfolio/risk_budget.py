"""Equal-risk blend of single-asset strategy equity curves."""

from __future__ import annotations

import numpy as np
import pandas as pd


def equal_risk_nav(navs: pd.DataFrame, target_vol: float | None = None) -> pd.Series:
    rets = navs.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    vol = rets.std(ddof=1).replace(0.0, np.nan)
    inv = 1.0 / vol
    w = inv / inv.sum()
    w = w.fillna(0.0)
    port = (rets * w).sum(axis=1)
    if target_vol is not None:
        pvol = float(port.std(ddof=1) * np.sqrt(252)) if len(port) > 5 else float("nan")
        if pvol and pvol > 0:
            port = port * (target_vol / pvol)
    eq = (1.0 + port).cumprod()
    if len(eq):
        eq.iloc[0] = 1.0
    return (eq * 1_000_000.0).rename("portfolio")
