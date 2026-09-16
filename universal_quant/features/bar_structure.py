"""Bar structure, range compression, and range position."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg


def add_bar_structure(df: pd.DataFrame, n: int = cfg.VOL_N) -> pd.DataFrame:
    out = df.copy()
    rng = (out["high"] - out["low"]).replace(0.0, np.nan)
    out["body_ratio"] = ((out["close"] - out["open"]).abs() / rng).fillna(0.0).clip(0.0, 1.0)
    out["clv"] = (((out["close"] - out["low"]) - (out["high"] - out["close"])) / rng).fillna(0.0).clip(-1.0, 1.0)
    out["range_ratio"] = rng / rng.rolling(n, min_periods=n).mean()
    span = (out["rolling_high"] - out["rolling_low"]).replace(0.0, np.nan) if "rolling_high" in out.columns else rng
    if "rolling_low" in out.columns:
        out["range_position"] = (out["close"] - out["rolling_low"]) / span
    else:
        out["range_position"] = np.nan
    return out
