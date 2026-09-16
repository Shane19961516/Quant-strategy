"""Volume factors. Volume itself is never back-adjusted."""

from __future__ import annotations

import numpy as np
import pandas as pd


def volume_ratio(volume: pd.Series, n: int) -> pd.Series:
    ma = volume.rolling(n, min_periods=n).mean()
    return (volume / ma.replace(0.0, np.nan)).rename("volume_ratio")


def volume_zscore(volume: pd.Series, n: int) -> pd.Series:
    mean = volume.rolling(n, min_periods=n).mean()
    std = volume.rolling(n, min_periods=n).std()
    return ((volume - mean) / std.replace(0.0, np.nan)).rename("volume_z")


def signed_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    return (np.sign(close.pct_change()) * volume).rename("signed_volume")


def volume_momentum(close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    return signed_volume(close, volume).rolling(n, min_periods=n).sum().rename("volume_momentum")
