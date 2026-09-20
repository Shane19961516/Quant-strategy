"""Residual z-score of ETH vs BTC, and the 8h funding-carry alternative.

All rolling stats use t-1 and earlier. Last-settled funding is lagged one bar
before being treated as known.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import FUNDING_BP_SCALE, BacktestConfig


def _mark_return(close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    r = close / prev - 1.0
    return r.replace([np.inf, -np.inf], np.nan)


def _last_settled_rate(rate: pd.Series, is_funding: pd.Series) -> pd.Series:
    """Funding known only after the settlement bar (no same-bar lookahead)."""
    settled = rate.where(is_funding.fillna(False))
    return settled.shift(1).ffill()


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
    df["z_residual"] = (df["log_spread"] - mu) / sd.replace(0.0, np.nan)
    df["spread_dev_bps"] = (df["log_spread"] - mu) * 1e4
    # Hedge residual: one-bar P&L of long ETH / short lagged-β BTC, then z of
    # the cumulative level so the signal is the same object as the hedge.
    df["ret_resid"] = r_eth - df["beta"] * r_btc
    df["cum_resid"] = df["ret_resid"].cumsum()
    mu_h = df["cum_resid"].shift(1).rolling(cfg.z_window, min_periods=cfg.z_window).mean()
    sd_h = df["cum_resid"].shift(1).rolling(cfg.z_window, min_periods=cfg.z_window).std(ddof=0)
    df["z_hedge"] = (df["cum_resid"] - mu_h) / sd_h.replace(0.0, np.nan)
    df["hedge_dev_bps"] = (df["cum_resid"] - mu_h) * 1e4
    dt_ms = df["bar_open_ts"].diff().median()
    if pd.notna(dt_ms) and float(dt_ms) > 0:
        lag_1d = max(1, int(round(86_400_000 / float(dt_ms))))
    else:
        lag_1d = 1440

    df["px_ratio"] = btc_px / eth_px.replace(0.0, np.nan)
    mu_r = df["px_ratio"].shift(1).rolling(cfg.z_window, min_periods=cfg.z_window).mean()
    sd_r = df["px_ratio"].shift(1).rolling(cfg.z_window, min_periods=cfg.z_window).std(ddof=0)
    df["z_ratio"] = (df["px_ratio"] - mu_r) / sd_r.replace(0.0, np.nan)

    s_hist = df["log_spread"].shift(1)
    min_p = int(cfg.raw_spread_min_periods)
    mu_exp = s_hist.expanding(min_periods=min_p).mean()
    sd_exp = s_hist.expanding(min_periods=min_p).std(ddof=0)
    df["z_raw"] = (df["log_spread"] - mu_exp) / sd_exp.replace(0.0, np.nan)
    df["raw_dev_bps"] = (df["log_spread"] - mu_exp) * 1e4

    df["corr"] = (
        r_eth.shift(1)
        .rolling(cfg.corr_window, min_periods=cfg.corr_window)
        .corr(r_btc.shift(1))
    )

    btc_fund = _last_settled_rate(df["btc_funding_rate"], df["btc_is_funding"])
    eth_fund = _last_settled_rate(df["eth_funding_rate"], df["eth_is_funding"])
    df["fund_diff"] = eth_fund - btc_fund
    df["z_funding"] = df["fund_diff"] * FUNDING_BP_SCALE

    if cfg.signal_mode == "funding_carry":
        df["z"] = df["z_funding"]
        df["spread_dev_bps"] = np.nan
    elif cfg.signal_mode == "raw_spread":
        df["z"] = df["z_raw"]
        df["spread_dev_bps"] = df["raw_dev_bps"]
    elif cfg.signal_mode == "ratio_btc_eth":
        df["z"] = df["z_ratio"]
        df["spread_dev_bps"] = (df["px_ratio"] - mu_r) / mu_r.replace(0.0, np.nan) * 1e4
    elif cfg.signal_mode == "hedge_residual":
        df["z"] = df["z_hedge"]
        df["spread_dev_bps"] = df["hedge_dev_bps"]
    else:
        df["z"] = df["z_residual"]
    df["z_lag_1d"] = df["z"].shift(lag_1d)

    nan_cols = [
        "beta",
        "z",
        "z_residual",
        "z_hedge",
        "z_funding",
        "z_raw",
        "z_ratio",
        "px_ratio",
        "corr",
        "log_spread",
        "spread_dev_bps",
        "raw_dev_bps",
        "hedge_dev_bps",
        "ret_resid",
        "cum_resid",
        "z_lag_1d",
        "fund_diff",
    ]
    df.loc[~complete, nan_cols] = np.nan
    return df
