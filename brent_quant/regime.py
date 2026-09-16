"""Trend / range regime from Kaufman efficiency ratio."""

from __future__ import annotations

import numpy as np
import pandas as pd

from brent_quant import config as cfg
from brent_quant.factors.trend import efficiency_ratio, sma


def add_regime(
    df: pd.DataFrame,
    er_n: int = cfg.ER_N,
    er_threshold: float = cfg.ER_THRESHOLD,
    sma_n: int = cfg.SMA_N,
) -> pd.DataFrame:
    out = df.copy()
    out["er"] = efficiency_ratio(out["close"], er_n)
    out["sma"] = sma(out["close"], sma_n)
    out["regime"] = np.where(out["er"] >= er_threshold, "trend", "range")
    out.loc[out["er"].isna(), "regime"] = "unknown"
    direction = np.sign(out["close"] - out["sma"])
    out["trend_direction"] = direction.fillna(0.0)
    return out
