"""Universal signal score in [-100, 100]."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _unit(x: pd.Series) -> pd.Series:
    return x.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-1.0, 1.0)


def add_score(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    direction = np.sign(out["close"] - out["close"].shift(24))
    er_s = _unit(out["er"].fillna(0.0) * direction.fillna(0.0))
    mom = _unit(out["momentum_score"] / 3.0)
    bo = _unit(out["breakout_strength"] / 2.0)
    vol_conf = _unit((out["volume_ratio"].fillna(1.0) - 1.0)) * np.sign(bo + mom + 1e-9)
    # High vol_z in the trade direction is slightly negative (risk), opposite of a trend confirm.
    vol_reg = -_unit(out["vol_z"].fillna(0.0) / 3.0)
    bar = _unit(out["clv"].fillna(0.0))
    # Compression then expansion: low range_ratio with directional CLV.
    comp = _unit((1.0 - out["range_ratio"].fillna(1.0)) * bar)
    out["score"] = (
        20.0 * er_s
        + 20.0 * mom
        + 20.0 * bo
        + 15.0 * vol_conf
        + 10.0 * vol_reg
        + 10.0 * bar
        + 5.0 * comp
    ).clip(-100.0, 100.0)
    return out
