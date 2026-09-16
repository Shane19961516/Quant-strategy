"""Prior-N Donchian breakout and ATR-normalized strength."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg


def add_breakout(df: pd.DataFrame, n: int = cfg.BREAKOUT_N) -> pd.DataFrame:
    out = df.copy()
    out["rolling_high"] = df["high"].shift(1).rolling(n, min_periods=n).max()
    out["rolling_low"] = df["low"].shift(1).rolling(n, min_periods=n).min()
    atr = out["atr"] if "atr" in out.columns else (out["high"] - out["low"]).rolling(n).mean()
    out["breakout_long"] = out["close"] > out["rolling_high"]
    out["breakout_short"] = out["close"] < out["rolling_low"]
    out["breakout_strength_long"] = (out["close"] - out["rolling_high"]) / atr.replace(0.0, np.nan)
    out["breakout_strength_short"] = (out["close"] - out["rolling_low"]) / atr.replace(0.0, np.nan)
    out["breakout_strength"] = np.where(
        out["breakout_long"],
        out["breakout_strength_long"],
        np.where(out["breakout_short"], out["breakout_strength_short"], 0.0),
    )
    return out
