# -*- coding: utf-8 -*-
"""风险与仓位：波动率目标、ATR 仓位。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 20,
) -> pd.Series:
    return true_range(high, low, close).rolling(window, min_periods=window).mean()


def rolling_volatility(close: pd.Series, window: int = 20, ann_factor: int = 252) -> pd.Series:
    """收盘价对数收益的滚动年化波动率。"""
    logret = np.log(close / close.shift(1))
    return logret.rolling(window, min_periods=window).std() * np.sqrt(ann_factor)


def atr_position_size(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    risk_frac: float = 0.01,
    atr_window: int = 20,
    atr_mult: float = 2.0,
) -> pd.Series:
    """海龟式 ATR 仓位：单位风险 = risk_frac * 名义本金 / (atr_mult * ATR)。

    返回的是相对名义本金的仓位权重（绝对值），方向由信号另行决定。
    """
    a = atr(high, low, close, atr_window)
    dollar_risk_per_unit = atr_mult * a / close
    size = risk_frac / dollar_risk_per_unit.replace(0, np.nan)
    return size.clip(upper=1.0).rename("size")


def volatility_target_weights(
    close: pd.Series,
    signal: pd.Series,
    target_vol: float = 0.10,
    vol_window: int = 20,
    max_leverage: float = 2.0,
) -> pd.Series:
    """单品种波动率目标仓位：weight = signal * target_vol / realized_vol。

    target_vol / max_leverage 为组合层约束前的单品种杠杆上限。
    """
    vol = rolling_volatility(close, vol_window)
    raw = signal * (target_vol / vol.replace(0, np.nan))
    return raw.clip(-max_leverage, max_leverage).rename("weight")


def portfolio_vol_scale(
    asset_returns: pd.DataFrame,
    weights: pd.DataFrame,
    target_vol: float = 0.12,
    vol_window: int = 60,
    max_leverage: float = 3.0,
) -> pd.DataFrame:
    """按组合实现波动率再缩放，使组合接近目标年化波动。"""
    port_ret = (weights.shift(1) * asset_returns).sum(axis=1)
    port_vol = port_ret.rolling(vol_window, min_periods=max(20, vol_window // 2)).std() * np.sqrt(252)
    scale = (target_vol / port_vol.replace(0, np.nan)).clip(upper=max_leverage)
    scale = scale.fillna(1.0)
    return weights.mul(scale, axis=0)
