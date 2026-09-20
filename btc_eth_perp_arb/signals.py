"""Residual z-score of ETH vs BTC. All rolling stats use t-1 and earlier."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BacktestConfig


def _mark_return(close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    r = close / prev - 1.0
    return r.replace([np.inf, -np.inf], np.nan)


def add_signals(panel: pd.DataFrame, cfg: BacktestConfig | None = None) -> pd.DataFrame:
    cfg = cfg or BacktestConfig()
    df = panel.copy()
    complete = df["pair_incomplete"] == 0

    # Returns are NaN on incomplete bars (no ffill of close).
    btc_px = df["btc_mark_close"].where(complete)
    eth_px = df["eth_mark_close"].where(complete)
    df["btc_mark_ret"] = _mark_return(btc_px)
    df["eth_mark_ret"] = _mark_return(eth_px)
    df["micro_event"] = (
        (df["btc_mark_ret"].abs() > 0.03) | (df["eth_mark_ret"].abs() > 0.03)
    ).astype("int8")

    r_btc = df["btc_mark_ret"]
    r_eth = df["eth_mark_ret"]
    # Hedge β from past window only.
    cov = r_eth.shift(1).rolling(cfg.beta_window, min_periods=cfg.beta_window).cov(r_btc.shift(1))
    var = r_btc.shift(1).rolling(cfg.beta_window, min_periods=cfg.beta_window).var()
    df["beta"] = cov / var.replace(0.0, np.nan)

    df["log_spread"] = np.log(eth_px) - np.log(btc_px)
    mu = df["log_spread"].shift(1).rolling(cfg.z_window, min_periods=cfg.z_window).mean()
    sd = df["log_spread"].shift(1).rolling(cfg.z_window, min_periods=cfg.z_window).std(ddof=0)
    df["z"] = (df["log_spread"] - mu) / sd.replace(0.0, np.nan)

    df["corr"] = (
        r_eth.shift(1)
        .rolling(cfg.corr_window, min_periods=cfg.corr_window)
        .corr(r_btc.shift(1))
    )
    df.loc[~complete, ["beta", "z", "corr", "log_spread"]] = np.nan
    return df
