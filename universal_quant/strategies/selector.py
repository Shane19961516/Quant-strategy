"""Trend following, mean reversion, shock control, and model matrix A–E."""

from __future__ import annotations

import numpy as np
import pandas as pd

from universal_quant import config as cfg
from universal_quant.regimes.regime_engine import add_regime
from universal_quant.strategies.score import add_score


def _vol_ok(df: pd.DataFrame) -> pd.Series:
    return df["volume_ratio"].fillna(0.0) >= cfg.VOLUME_RATIO_TH


def trend_following(df: pd.DataFrame, use_volume: bool) -> pd.Series:
    up = (df["trend_dir"] > 0) & df["breakout_long"].fillna(False) & (df["momentum_score"].fillna(0.0) > 0)
    dn = (df["trend_dir"] < 0) & df["breakout_short"].fillna(False) & (df["momentum_score"].fillna(0.0) < 0)
    if use_volume:
        ok = _vol_ok(df)
        up = up & ok
        dn = dn & ok
    return pd.Series(np.where(up, 1.0, np.where(dn, -1.0, 0.0)), index=df.index)


def mean_reversion(df: pd.DataFrame) -> pd.Series:
    rp = df["range_position"]
    contract = df["volume_ratio"].fillna(9.0) < 1.0
    lng = (rp <= cfg.RANGE_LONG) & contract
    sht = (rp >= cfg.RANGE_SHORT) & contract
    return pd.Series(np.where(lng, 1.0, np.where(sht, -1.0, 0.0)), index=df.index)


def signal_from_model(df: pd.DataFrame, model: str) -> pd.Series:
    model = model.upper()
    work = add_score(add_regime(df)) if "regime" not in df.columns else df
    tf_nv = trend_following(work, use_volume=False)
    tf_v = trend_following(work, use_volume=True)
    mr = mean_reversion(work)
    regime = work["regime"].astype(str)
    shock = regime == "shock"
    trend = regime == "trend"
    rng = regime == "range"

    if model == "A":
        sig = tf_nv
    elif model == "B":
        sig = tf_v
    elif model == "C":
        sig = tf_nv.where(trend, 0.0)
    elif model == "D":
        sig = tf_v.where(trend, 0.0)
    elif model == "E":
        adaptive = np.where(trend, tf_v, np.where(rng, mr, 0.0))
        sig = pd.Series(adaptive, index=work.index)
        # Shock: no new entries unless |breakout strength| > 1
        strong = work["breakout_strength"].abs().fillna(0.0) > 1.0
        sig = sig.where(~shock | strong, 0.0)
        # Transition: half size encoded as ±0.5 if a raw TF pulse exists
        trans = regime == "transition"
        sig = np.where(trans, 0.5 * tf_v, sig)
        sig = pd.Series(sig, index=work.index)
    elif model == "SCORE":
        sc = work["score"]
        sig = pd.Series(
            np.select(
                [sc >= cfg.SCORE_FULL, sc >= cfg.SCORE_HALF, sc <= -cfg.SCORE_FULL, sc <= -cfg.SCORE_HALF],
                [1.0, 0.5, -1.0, -0.5],
                default=0.0,
            ),
            index=work.index,
        )
    elif model == "MOM":
        sig = pd.Series(np.sign(work["momentum_score"].fillna(0.0)), index=work.index)
        sig = sig.where(sig.abs() > 0, 0.0)
    elif model == "BO":
        sig = pd.Series(
            np.where(work["breakout_long"].fillna(False), 1.0, np.where(work["breakout_short"].fillna(False), -1.0, 0.0)),
            index=work.index,
        )
    else:
        raise ValueError(f"unknown model {model}")

    size = np.where(shock, cfg.SHOCK_SIZE, 1.0)
    out = pd.Series(sig, index=work.index, name="signal").astype(float) * size
    # Pulse models A–E: keep 0 on most bars; engine holds until stop.
    return out.clip(-1.0, 1.0)
