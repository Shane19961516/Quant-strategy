# -*- coding: utf-8 -*-
"""用户定义策略一（改进均线）与遗留布林信号。

资金：100 万
保证金权限：策略一 15 万 / 三 15 万 / 四 20 万（策略二已移除）
单品种杠杆默认 10 倍（保证金 = |名义| / 10）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ..metrics import performance_summary
from ..risk import atr as atr_fn


@dataclass
class BookConfig:
    capital: float = 1_000_000.0
    leverage: float = 10.0
    cost_bps: float = 0.5
    slip_bps: float = 0.5
    # 各策略保证金占用上限（元）；策略二已下线
    margin_s1: float = 150_000.0
    margin_s2: float = 0.0
    margin_s3: float = 150_000.0
    margin_s4: float = 200_000.0


def ma_cross_mid_signal(
    close: pd.Series,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
    fast: int = 14,
    slow: int = 16,
    trend: int = 60,
    atr_window: int = 20,
    exit_atr_buf: float = 1.0,
    trail_mult: float = 2.5,
) -> pd.Series:
    """策略一（改进版）：14/16 均线交叉 + 趋势过滤 + 中轨缓冲出场 + ATR 跟踪止损。

    保留原规则核心：
    - 14 上穿 16 → 做多；下穿 → 做空
    - 回到中轨平仓；平仓后须新交叉再开仓

    改进（原版中轨过近，持仓中位仅约 2 日，噪声过大）：
    - 仅在价格位于 MA60 同向一侧时开仓（趋势过滤）
    - 中轨出场加 1×ATR 缓冲，避免 14/16 贴合反复扫损
    - 叠加 ATR 跟踪止损（2.5×ATR）截断左尾
    """
    if fast >= slow:
        raise ValueError("fast must be < slow")
    high = close if high is None else high
    low = close if low is None else low

    ma_f = close.rolling(fast, min_periods=fast).mean()
    ma_s = close.rolling(slow, min_periods=slow).mean()
    ma_t = close.rolling(trend, min_periods=trend).mean()
    mid = (ma_f + ma_s) / 2.0
    a = atr_fn(high, low, close, window=atr_window).to_numpy(dtype=float)

    c = close.to_numpy(dtype=float)
    hi = high.to_numpy(dtype=float)
    lo = low.to_numpy(dtype=float)
    f = ma_f.to_numpy(dtype=float)
    s = ma_s.to_numpy(dtype=float)
    t = ma_t.to_numpy(dtype=float)
    m = mid.to_numpy(dtype=float)
    n = len(c)

    out = np.zeros(n, dtype=float)
    pos = 0.0
    stop = np.nan
    extreme = np.nan

    for i in range(1, n):
        if (
            np.isnan(f[i])
            or np.isnan(s[i])
            or np.isnan(f[i - 1])
            or np.isnan(s[i - 1])
            or np.isnan(t[i])
            or np.isnan(a[i])
            or a[i] <= 0
        ):
            out[i] = 0.0
            pos = 0.0
            stop = np.nan
            extreme = np.nan
            continue

        cross_up = f[i - 1] <= s[i - 1] and f[i] > s[i]
        cross_dn = f[i - 1] >= s[i - 1] and f[i] < s[i]

        if pos == 0.0:
            # 趋势过滤：多头仅在价>MA60，空头仅在价<MA60
            if cross_up and c[i] > t[i]:
                pos = 1.0
                stop = c[i] - trail_mult * a[i]
                extreme = hi[i]
            elif cross_dn and c[i] < t[i]:
                pos = -1.0
                stop = c[i] + trail_mult * a[i]
                extreme = lo[i]
        elif pos > 0.0:
            extreme = hi[i] if np.isnan(extreme) else max(extreme, hi[i])
            trail = extreme - trail_mult * a[i]
            stop = trail if np.isnan(stop) else max(stop, trail)
            # 止损或中轨-缓冲平仓
            if lo[i] <= stop or c[i] <= m[i] - exit_atr_buf * a[i]:
                pos = 0.0
                stop = np.nan
                extreme = np.nan
            elif cross_dn and c[i] < t[i]:
                # 反向交叉且仍符合空头趋势：反手
                pos = -1.0
                stop = c[i] + trail_mult * a[i]
                extreme = lo[i]
        else:  # pos < 0
            extreme = lo[i] if np.isnan(extreme) else min(extreme, lo[i])
            trail = extreme + trail_mult * a[i]
            stop = trail if np.isnan(stop) else min(stop, trail)
            if hi[i] >= stop or c[i] >= m[i] + exit_atr_buf * a[i]:
                pos = 0.0
                stop = np.nan
                extreme = np.nan
            elif cross_up and c[i] > t[i]:
                pos = 1.0
                stop = c[i] - trail_mult * a[i]
                extreme = hi[i]

        out[i] = pos

    return pd.Series(out, index=close.index, name="signal")


def bollinger_fade_signal(
    close: pd.Series,
    window: int = 20,
    entry_std: float = 2.0,
    stop_std: float = 4.0,
) -> pd.Series:
    """策略二（已下线，保留函数便于对照）。"""
    ma = close.rolling(window, min_periods=window).mean()
    sd = close.rolling(window, min_periods=window).std()
    upper = ma + entry_std * sd
    lower = ma - entry_std * sd
    z = ((close - ma) / sd.replace(0.0, np.nan)).to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    up = upper.to_numpy(dtype=float)
    lo = lower.to_numpy(dtype=float)
    mid = ma.to_numpy(dtype=float)
    n = len(c)
    out = np.zeros(n, dtype=float)
    pos = 0.0
    armed = 0
    for i in range(1, n):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(mid[i]) or np.isnan(z[i]):
            out[i] = 0.0
            pos = 0.0
            armed = 0
            continue
        if pos != 0.0 and abs(z[i]) >= stop_std:
            pos = 0.0
            armed = 0
            out[i] = 0.0
            continue
        if pos > 0.0:
            if c[i] >= mid[i]:
                pos = 0.0
                armed = 0
            out[i] = pos
            continue
        if pos < 0.0:
            if c[i] <= mid[i]:
                pos = 0.0
                armed = 0
            out[i] = pos
            continue
        prev_c, prev_up, prev_lo = c[i - 1], up[i - 1], lo[i - 1]
        if np.isnan(prev_up) or np.isnan(prev_lo):
            out[i] = 0.0
            continue
        if prev_c <= prev_up and c[i] > up[i]:
            armed = -1
        if prev_c >= prev_lo and c[i] < lo[i]:
            armed = 1
        if armed == -1 and prev_c >= prev_up and c[i] < up[i]:
            pos = -1.0
            armed = 0
        elif armed == 1 and prev_c <= prev_lo and c[i] > lo[i]:
            pos = 1.0
            armed = 0
        out[i] = pos
    return pd.Series(out, index=close.index, name="signal")


def simulate_directional_book(
    signals: pd.DataFrame,
    closes: pd.DataFrame,
    margin_budget: float,
    capital: float,
    leverage: float = 10.0,
    cost_bps: float = 0.5,
    slip_bps: float = 0.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame, Dict[str, float]]:
    """单边方向策略：信号 ∈ {-1,0,1}，在保证金预算内等权分配名义。"""
    sig = signals.fillna(0.0).sort_index()
    px = closes.reindex(sig.index).ffill()
    rets = px.pct_change().fillna(0.0)
    rets = rets.mask(rets.abs() > 0.08, 0.0)
    symbols = list(sig.columns)
    n = len(sig)
    max_gross = margin_budget * leverage

    weight_rows = np.zeros((n, len(symbols)))
    book_pnl = np.zeros(n)
    margin_used = np.zeros(n)
    cash_pnl_cum = 0.0
    prev_w = pd.Series(0.0, index=symbols)

    for i in range(n):
        d = sig.iloc[i]
        active = d[d != 0.0]
        w = pd.Series(0.0, index=symbols)
        if len(active) > 0 and max_gross > 0:
            per = max_gross / float(len(active))
            w = (d * per).reindex(symbols).fillna(0.0)
        day_ret = rets.iloc[i]
        gross = float((prev_w * day_ret).sum())
        turnover = float((w - prev_w).abs().sum())
        cost = turnover * ((cost_bps + slip_bps) / 10000.0)
        net = gross - cost
        cash_pnl_cum += net
        book_pnl[i] = net
        margin_used[i] = float(w.abs().sum() / leverage)
        weight_rows[i, :] = w.to_numpy()
        prev_w = w

    weights = pd.DataFrame(weight_rows, index=sig.index, columns=symbols)
    pnl = pd.Series(book_pnl, index=sig.index, name="pnl")
    ret_on_capital = (pnl / capital).rename("ret")
    nav = (1.0 + ret_on_capital).cumprod().rename("nav")
    summary = performance_summary(nav, ret_on_capital)
    summary["max_margin"] = float(margin_used.max()) if n else 0.0
    summary["avg_margin"] = float(margin_used.mean()) if n else 0.0
    summary["margin_budget"] = float(margin_budget)
    summary["margin_ok"] = float(summary["max_margin"] <= margin_budget + 1e-6)
    return nav, ret_on_capital, weights, summary


def build_s1_signals(panels: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    data = {
        s: ma_cross_mid_signal(p["close"], p["high"], p["low"])
        for s, p in panels.items()
    }
    return pd.DataFrame(data).sort_index().fillna(0.0)


def run_s1(panels: Dict[str, pd.DataFrame], cfg: Optional[BookConfig] = None):
    cfg = cfg or BookConfig()
    closes = pd.DataFrame({s: panels[s]["close"] for s in panels}).sort_index().ffill()
    sig = build_s1_signals(panels).reindex(closes.index).fillna(0.0)
    return simulate_directional_book(
        sig, closes, cfg.margin_s1, cfg.capital, cfg.leverage, cfg.cost_bps, cfg.slip_bps
    ) + (sig,)
