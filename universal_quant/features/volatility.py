"""ATR / NATR / realized vol / z-scores."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg


def add_volatility(df: pd.DataFrame, n: int = cfg.VOL_N) -> pd.DataFrame:
    out = df.copy()
    prev = out["close"].shift(1)
    tr = pd.concat(
        [
            (out["high"] - out["low"]).abs(),
            (out["high"] - prev).abs(),
            (out["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(n, min_periods=n).mean()
    out["natr"] = out["atr"] / out["close"].replace(0.0, np.nan)
    ret = out["close"].pct_change()
    out["rv"] = ret.rolling(n, min_periods=n).std()
    out["vol_z"] = (out["rv"] - out["rv"].rolling(n).mean()) / out["rv"].rolling(n).std().replace(0.0, np.nan)
    out["ret_z"] = (ret - ret.rolling(n).mean()) / ret.rolling(n).std().replace(0.0, np.nan)
    out["normalized_return"] = ret / out["rv"].replace(0.0, np.nan)
    out["normalized_slope"] = out.get("log_slope", pd.Series(index=out.index, dtype=float)) / out["rv"].replace(0.0, np.nan)
    return out
