"""Pure volume-price signals. Generated at bar close; executed next bar."""

from __future__ import annotations

import numpy as np
import pandas as pd

from brent_quant import config as cfg
from brent_quant.factors.price_action import body_ratio, close_location_value
from brent_quant.factors.trend import atr, donchian_channel
from brent_quant.factors.volume import volume_momentum, volume_ratio, volume_zscore
from brent_quant.regime import add_regime


def add_factors(
    df: pd.DataFrame,
    breakout_n: int = cfg.BREAKOUT_N,
    er_n: int = cfg.ER_N,
    er_threshold: float = cfg.ER_THRESHOLD,
    volume_ma: int = cfg.VOLUME_MA,
    volume_z_n: int = cfg.VOLUME_Z_N,
    volume_mom_n: int = cfg.VOLUME_MOM_N,
) -> pd.DataFrame:
    out = add_regime(df, er_n=er_n, er_threshold=er_threshold)
    ch = donchian_channel(out, breakout_n)
    out["rolling_high"] = ch["rolling_high"]
    out["rolling_low"] = ch["rolling_low"]
    out["breakout_long"] = out["close"] > out["rolling_high"]
    out["breakout_short"] = out["close"] < out["rolling_low"]
    out["fade_long"] = out["close"] < out["rolling_low"]
    out["fade_short"] = out["close"] > out["rolling_high"]
    out["atr"] = atr(out, cfg.ATR_PERIOD)
    out["volume_ratio"] = volume_ratio(out["volume"], volume_ma)
    out["volume_z"] = volume_zscore(out["volume"], volume_z_n)
    out["volume_momentum"] = volume_momentum(out["close"], out["volume"], volume_mom_n)
    out["clv"] = close_location_value(out)
    out["body_ratio"] = body_ratio(out)
    ret = out["close"].pct_change()
    z_win = max(volume_z_n, 60)
    out["bar_return_z"] = (ret - ret.rolling(z_win).mean()) / ret.rolling(z_win).std().replace(0.0, np.nan)
    return out


def _clip_unit(x: pd.Series) -> pd.Series:
    return x.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-1.0, 1.0)


def add_score(df: pd.DataFrame) -> pd.DataFrame:
    """Weighted score in [-100, 100] using only current-and-past bars."""
    out = df.copy()
    direction = np.sign(out["close"] - out["close"].shift(cfg.ER_N))
    er_signed = _clip_unit(out["er"].fillna(0.0) * direction.fillna(0.0))
    bo = np.where(out["breakout_long"], 1.0, np.where(out["breakout_short"], -1.0, 0.0))
    vr = _clip_unit((out["volume_ratio"].fillna(1.0) - 1.0) / 1.0)
    vr_signed = vr * np.sign(bo + direction.fillna(0.0))
    vm = _clip_unit(out["volume_momentum"] / out["volume"].rolling(cfg.VOLUME_MOM_N).sum().replace(0.0, np.nan))
    shock = _clip_unit(out["volume_z"].fillna(0.0) / 3.0) * np.sign(bo + direction.fillna(0.0))
    out["score"] = (
        25.0 * er_signed
        + 25.0 * pd.Series(bo, index=out.index)
        + 20.0 * vr_signed
        + 15.0 * vm
        + 15.0 * shock
    )
    out["score"] = out["score"].clip(-100.0, 100.0)
    return out


def _volume_ok(df: pd.DataFrame, threshold: float) -> pd.Series:
    return df["volume_ratio"].fillna(0.0) >= threshold


def signal_from_model(
    df: pd.DataFrame,
    model: str,
    er_threshold: float = cfg.ER_THRESHOLD,
    volume_threshold: float = cfg.VOLUME_RATIO_THRESHOLD,
) -> pd.Series:
    model = model.upper()
    bo_long = df["breakout_long"].fillna(False)
    bo_short = df["breakout_short"].fillna(False)
    vol_ok = _volume_ok(df, volume_threshold)
    trend = df["regime"] == "trend"
    rng = df["regime"] == "range"
    vol_contract = df["volume_ratio"].fillna(9.0) < 1.0

    if model == "A":
        sig = np.where(bo_long, 1.0, np.where(bo_short, -1.0, 0.0))
    elif model == "B":
        sig = np.where(bo_long & vol_ok, 1.0, np.where(bo_short & vol_ok, -1.0, 0.0))
    elif model == "C":
        sig = np.where(trend & bo_long, 1.0, np.where(trend & bo_short, -1.0, 0.0))
    elif model == "D":
        sig = np.where(
            trend & bo_long & vol_ok,
            1.0,
            np.where(trend & bo_short & vol_ok, -1.0, 0.0),
        )
    elif model == "E":
        long = (trend & bo_long & vol_ok) | (rng & df["fade_long"].fillna(False) & vol_contract)
        short = (trend & bo_short & vol_ok) | (rng & df["fade_short"].fillna(False) & vol_contract)
        sig = np.where(long, 1.0, np.where(short, -1.0, 0.0))
    elif model == "SCORE":
        score = df["score"]
        sig = np.select(
            [
                score >= cfg.SCORE_FULL,
                score >= cfg.SCORE_HALF,
                score <= -cfg.SCORE_FULL,
                score <= -cfg.SCORE_HALF,
            ],
            [1.0, 0.5, -1.0, -0.5],
            default=0.0,
        )
    elif model == "DONCHIAN20":
        n = 20
        prev_high = df["high"].rolling(n, min_periods=n).max().shift(1)
        prev_low = df["low"].rolling(n, min_periods=n).min().shift(1)
        sig = np.where(df["close"] > prev_high, 1.0, np.where(df["close"] < prev_low, -1.0, 0.0))
    elif model == "BHS":
        sig = np.ones(len(df), dtype=float)
    else:
        raise ValueError(f"unknown model {model}")

    out = pd.Series(sig, index=df.index, name="signal").astype(float)
    # Hold last non-zero until an opposite or explicit flatten. Pulse entries
    # (single-bar breakout flags) stay as target until reversed.
    out = _sticky_target(out, model)
    return out


def _sticky_target(signal: pd.Series, model: str) -> pd.Series:
    if model in {"BHS", "SCORE"}:
        return signal
    # Breakout systems: keep the last directional target until opposite fires.
    sticky = signal.copy()
    last = 0.0
    vals = sticky.to_numpy(copy=True)
    for i, v in enumerate(vals):
        if v != 0.0:
            last = v
        vals[i] = last
    return pd.Series(vals, index=signal.index, name="signal")


def build_signal_frame(df: pd.DataFrame, model: str, **kwargs) -> pd.DataFrame:
    out = add_factors(df, **{k: v for k, v in kwargs.items() if k in {
        "breakout_n", "er_n", "er_threshold", "volume_ma", "volume_z_n", "volume_mom_n"
    }})
    out = add_score(out)
    out["signal"] = signal_from_model(
        out,
        model,
        er_threshold=kwargs.get("er_threshold", cfg.ER_THRESHOLD),
        volume_threshold=kwargs.get("volume_threshold", cfg.VOLUME_RATIO_THRESHOLD),
    )
    return out
