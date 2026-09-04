# -*- coding: utf-8 -*-
"""Alpha101 operators (DataFrame: DatetimeIndex x tickers)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def rank(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank in (0, 1]."""
    return df.rank(axis=1, pct=True, method="average")


def delay(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.shift(d)


def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.diff(d)


def ts_sum(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(d, min_periods=max(1, d // 2)).sum()


def ts_mean(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(d, min_periods=max(1, d // 2)).mean()


def ts_std(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(d, min_periods=max(2, d // 2)).std()


def ts_min(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(d, min_periods=max(1, d // 2)).min()


def ts_max(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(d, min_periods=max(1, d // 2)).max()


def ts_rank(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Fast range-rank proxy in [0, 1]: (x - min) / (max - min) over window."""
    mn = df.rolling(d, min_periods=max(2, d // 2)).min()
    mx = df.rolling(d, min_periods=max(2, d // 2)).max()
    return (df - mn) / (mx - mn).replace(0, np.nan)


def ts_argmax(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Days-since-window-high proxy in [1, d] (vectorized)."""
    # For each lag k=0..d-1, check if value equals rolling max ending now and
    # prefer more recent highs via cumulative logic.
    roll_max = df.rolling(d, min_periods=max(1, d // 2)).max()
    out = pd.DataFrame(np.nan, index=df.index, columns=df.columns)
    for k in range(d):
        matched = df.shift(k).eq(roll_max)
        out = out.where(~matched, float(d - k))
    return out


def ts_argmin(df: pd.DataFrame, d: int) -> pd.DataFrame:
    roll_min = df.rolling(d, min_periods=max(1, d // 2)).min()
    out = pd.DataFrame(np.nan, index=df.index, columns=df.columns)
    for k in range(d):
        matched = df.shift(k).eq(roll_min)
        out = out.where(~matched, float(d - k))
    return out


def correlation(x: pd.DataFrame, y: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d, min_periods=max(3, d // 2)).corr(y)


def covariance(x: pd.DataFrame, y: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d, min_periods=max(3, d // 2)).cov(y)


def scale(df: pd.DataFrame) -> pd.DataFrame:
    """Scale so that sum(|x|) == 1 cross-sectionally."""
    s = df.abs().sum(axis=1).replace(0, np.nan)
    return df.div(s, axis=0)


def signedpower(df: pd.DataFrame, a: float) -> pd.DataFrame:
    return np.sign(df) * (df.abs() ** a)


def decay_linear(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Linearly weighted moving average (vectorized via successive adds)."""
    w = np.arange(1, d + 1, dtype=float)
    w = w / w.sum()
    acc = None
    for i, wi in enumerate(w[::-1]):
        part = df.shift(i) * wi
        acc = part if acc is None else acc.add(part, fill_value=0.0)
    # null out until enough history
    need = max(1, d // 2)
    valid = df.notna().rolling(d, min_periods=need).sum() >= need
    return acc.where(valid)


def product(df: pd.DataFrame, d: int) -> pd.DataFrame:
    # rolling product via log-sum-exp of log|x| with sign tracking is fragile;
    # use pandas rolling apply
    def _prod(x: np.ndarray) -> float:
        if np.any(np.isnan(x)):
            x = x[~np.isnan(x)]
        if len(x) == 0:
            return np.nan
        return float(np.prod(x))

    return df.rolling(d, min_periods=d).apply(_prod, raw=True)


def log(df: pd.DataFrame) -> pd.DataFrame:
    return np.log(df.replace(0, np.nan))


def sign(df: pd.DataFrame) -> pd.DataFrame:
    return np.sign(df)


def abs_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.abs()


def cs_demean(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional demean (proxy for IndNeutralize when industry unavailable)."""
    return df.sub(df.mean(axis=1), axis=0)
