"""Trend, breakout, ATR, and efficiency ratio factors."""

from __future__ import annotations

import numpy as np
import pandas as pd


def donchian_channel(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Prior-n highs/lows so the current bar is not in the breakout window."""
    out = pd.DataFrame(index=df.index)
    out["rolling_high"] = df["high"].rolling(n, min_periods=n).max().shift(1)
    out["rolling_low"] = df["low"].rolling(n, min_periods=n).min().shift(1)
    return out


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    er = net / path.replace(0.0, np.nan)
    return er.clip(lower=0.0, upper=1.0).rename("er")


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean().rename("atr")


def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean().rename(f"sma_{n}")
