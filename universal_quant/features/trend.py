"""Trend efficiency and log-price slope (scale invariant)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return (net / path.replace(0.0, np.nan)).clip(0.0, 1.0).rename("er")


def _rolling_log_slope(close: pd.Series, n: int) -> pd.Series:
    y = np.log(close.replace(0, np.nan).to_numpy(dtype=float))
    t = np.arange(n, dtype=float)
    t = t - t.mean()
    denom = float((t ** 2).sum())
    out = np.full(len(y), np.nan)
    for i in range(n - 1, len(y)):
        w = y[i - n + 1 : i + 1]
        if np.isnan(w).any():
            continue
        w = w - w.mean()
        out[i] = float((t * w).sum() / denom) if denom else np.nan
    return pd.Series(out, index=close.index, name="log_slope")


def add_trend(df: pd.DataFrame, n: int = cfg.ER_N, slope_n: int = cfg.SLOPE_N) -> pd.DataFrame:
    out = df.copy()
    out["er"] = efficiency_ratio(out["close"], n)
    out["log_slope"] = _rolling_log_slope(out["close"], slope_n)
    return out
