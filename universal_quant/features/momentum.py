"""Momentum, volatility-normalized for cross-asset comparison."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg


def add_momentum(df: pd.DataFrame, n: int = cfg.MOM_N, vol_n: int = cfg.VOL_N) -> pd.DataFrame:
    out = df.copy()
    ret = out["close"].pct_change()
    out["momentum"] = out["close"] / out["close"].shift(n) - 1.0
    rv = ret.rolling(vol_n, min_periods=vol_n).std()
    out["momentum_score"] = out["momentum"] / rv.replace(0.0, np.nan)
    return out
