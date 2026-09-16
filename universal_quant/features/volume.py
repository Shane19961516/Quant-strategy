"""Volume ratio / z / signed momentum. Never use raw volume across assets."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg


def add_volume(df: pd.DataFrame, n: int = cfg.VOLUME_MA) -> pd.DataFrame:
    out = df.copy()
    vol = out["volume"].astype(float)
    ma = vol.rolling(n, min_periods=n).mean()
    sd = vol.rolling(n, min_periods=n).std()
    out["volume_ratio"] = vol / ma.replace(0.0, np.nan)
    out["volume_z"] = (vol - ma) / sd.replace(0.0, np.nan)
    signed = np.sign(out["close"].pct_change()) * vol
    out["signed_volume"] = signed
    out["volume_momentum"] = signed.rolling(n, min_periods=n).sum()
    return out
