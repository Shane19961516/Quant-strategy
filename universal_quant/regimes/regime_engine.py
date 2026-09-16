"""Four-state regime: TREND / RANGE / SHOCK / TRANSITION."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg
from universal_quant.features import add_all_features


def add_regime(df: pd.DataFrame) -> pd.DataFrame:
    out = add_all_features(df) if "er" not in df.columns else df.copy()
    er = out["er"].fillna(0.0)
    mom = out["momentum_score"].fillna(0.0)
    shock = (
        (out["volume_z"].abs() >= cfg.VOLUME_Z_SHOCK)
        | (out["ret_z"].abs() >= cfg.RET_Z_SHOCK)
        | (out["vol_z"].abs() >= cfg.VOL_Z_SHOCK)
    )
    trend = (er >= cfg.ER_TREND) & (mom.abs() >= cfg.MOM_TREND)
    rng = (er <= cfg.ER_RANGE) & (mom.abs() < cfg.MOM_TREND)
    regime = np.where(shock.fillna(False), "shock", np.where(trend.fillna(False), "trend", np.where(rng.fillna(False), "range", "transition")))
    out["regime"] = regime
    out["trend_dir"] = np.sign(mom).fillna(0.0)
    return out
