# -*- coding: utf-8 -*-
"""CTA 信号：趋势、布林反转、短周期反转。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def dual_ma_signal(
    close: pd.Series,
    fast: int = 20,
    slow: int = 60,
) -> pd.Series:
    """双均线交叉：快线上穿慢线做多，下穿做空，否则空仓。

    返回取值 {-1, 0, 1}，信号在当日收盘确认，回测中应滞后 1 日交易。
    """
    if fast >= slow:
        raise ValueError("fast period must be < slow period")
    ma_fast = close.rolling(fast, min_periods=fast).mean()
    ma_slow = close.rolling(slow, min_periods=slow).mean()
    raw = pd.Series(0, index=close.index, dtype=float)
    raw = raw.mask(ma_fast > ma_slow, 1.0)
    raw = raw.mask(ma_fast < ma_slow, -1.0)
    raw = raw.where(ma_fast.notna() & ma_slow.notna(), np.nan)
    return raw.rename("signal")


def donchian_breakout_signal(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    entry: int = 20,
    exit_: int = 10,
) -> pd.Series:
    """唐奇安通道突破（海龟风格）。

    - 收盘价突破过去 entry 日最高价 -> 做多
    - 收盘价跌破过去 entry 日最低价 -> 做空
    - 反向触及 exit_ 日通道则平仓（回到 0）
    """
    upper_entry = high.shift(1).rolling(entry, min_periods=entry).max().to_numpy()
    lower_entry = low.shift(1).rolling(entry, min_periods=entry).min().to_numpy()
    upper_exit = high.shift(1).rolling(exit_, min_periods=exit_).max().to_numpy()
    lower_exit = low.shift(1).rolling(exit_, min_periods=exit_).min().to_numpy()
    c = close.to_numpy()
    n = len(c)
    out = np.full(n, np.nan)
    pos = 0.0
    for i in range(n):
        ue, le = upper_entry[i], lower_entry[i]
        if np.isnan(ue) or np.isnan(le):
            continue
        ux, lx = upper_exit[i], lower_exit[i]
        ci = c[i]
        if pos == 0:
            if ci > ue:
                pos = 1.0
            elif ci < le:
                pos = -1.0
        elif pos > 0:
            if (not np.isnan(lx)) and ci < lx:
                pos = 0.0
            elif ci < le:
                pos = -1.0
        elif pos < 0:
            if (not np.isnan(ux)) and ci > ux:
                pos = 0.0
            elif ci > ue:
                pos = 1.0
        out[i] = pos
    return pd.Series(out, index=close.index, name="signal")


def ts_momentum_signal(
    close: pd.Series,
    lookback: int = 60,
    skip: int = 1,
) -> pd.Series:
    """时间序列动量：过去 lookback 日收益符号作为方向，默认跳过最近 skip 日。

    经典 CTA / 学术 TSMOM 设定：r_{t-L-skip : t-skip} 的符号。
    """
    lagged = close.shift(skip)
    past = close.shift(skip + lookback)
    mom = lagged / past - 1.0
    signal = np.sign(mom).astype(float)
    signal = signal.where(mom.notna(), np.nan)
    signal = signal.replace(0.0, 0.0)
    return signal.rename("signal")


def bollinger_reversion_signal(
    close: pd.Series,
    window: int = 20,
    n_std: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 3.5,
) -> pd.Series:
    """布林带均值回归：上轨外做空、下轨外做多，回到均值平仓；过远止损。"""
    if stop_z <= n_std:
        raise ValueError("stop_z must be > n_std")
    ma = close.rolling(window, min_periods=window).mean()
    sd = close.rolling(window, min_periods=window).std()
    z = ((close - ma) / sd.replace(0.0, np.nan)).to_numpy(dtype=float)
    n = len(close)
    out = np.zeros(n, dtype=float)
    pos = 0.0
    for i in range(n):
        zi = z[i]
        if np.isnan(zi):
            out[i] = 0.0
            continue
        if pos == 0.0:
            if zi >= n_std:
                pos = -1.0
            elif zi <= -n_std:
                pos = 1.0
        else:
            if abs(zi) >= stop_z:
                pos = 0.0
            elif pos > 0 and zi >= exit_z:
                pos = 0.0
            elif pos < 0 and zi <= -exit_z:
                pos = 0.0
        out[i] = pos
    return pd.Series(out, index=close.index, name="signal")


def short_term_reversal_signal(
    close: pd.Series,
    lookback: int = 5,
    entry_z: float = 1.5,
    exit_z: float = 0.0,
    stop_z: float = 3.0,
) -> pd.Series:
    """短周期收益 z-score 反转：大涨后做空、大跌后做多。"""
    if stop_z <= entry_z:
        raise ValueError("stop_z must be > entry_z")
    ret = close.pct_change(lookback)
    mu = ret.rolling(60, min_periods=30).mean()
    sd = ret.rolling(60, min_periods=30).std()
    z = ((ret - mu) / sd.replace(0.0, np.nan)).to_numpy(dtype=float)
    n = len(close)
    out = np.zeros(n, dtype=float)
    pos = 0.0
    for i in range(n):
        zi = z[i]
        if np.isnan(zi):
            out[i] = 0.0
            continue
        if pos == 0.0:
            if zi >= entry_z:
                pos = -1.0
            elif zi <= -entry_z:
                pos = 1.0
        else:
            if abs(zi) >= stop_z:
                pos = 0.0
            elif pos > 0 and zi >= exit_z:
                pos = 0.0
            elif pos < 0 and zi <= -exit_z:
                pos = 0.0
        out[i] = pos
    return pd.Series(out, index=close.index, name="signal")


def combine_signals(
    signals: dict[str, pd.Series],
    weights: dict[str, float] | None = None,
) -> pd.Series:
    """多信号等权/加权合成后取符号，得到最终方向 {-1,0,1}。"""
    names = list(signals.keys())
    if not names:
        raise ValueError("empty signals")
    weights = weights or {n: 1.0 / len(names) for n in names}
    aligned = pd.concat([signals[n].rename(n) for n in names], axis=1)
    w = pd.Series({n: weights.get(n, 0.0) for n in names})
    score = aligned.mul(w, axis=1).sum(axis=1, min_count=1)
    out = np.sign(score).astype(float)
    out = out.where(score.notna(), np.nan)
    # 弱信号阈值：|score| 过小则空仓，降低抖动
    thr = 0.25 * sum(abs(weights[n]) for n in names) / len(names)
    out = out.where(score.abs() >= thr, 0.0)
    return out.rename("signal")
