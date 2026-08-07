# -*- coding: utf-8 -*-
"""用户定义四策略：信号与回测引擎。

资金：100 万
保证金权限：策略一 15 万 / 二 30 万 / 三 15 万 / 四 20 万
单品种杠杆默认 10 倍（保证金 = |名义| / 10）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..metrics import performance_summary


@dataclass
class BookConfig:
    capital: float = 1_000_000.0
    leverage: float = 10.0
    cost_bps: float = 0.5
    slip_bps: float = 0.5
    # 各策略保证金占用上限（元）
    margin_s1: float = 150_000.0
    margin_s2: float = 300_000.0
    margin_s3: float = 150_000.0
    margin_s4: float = 200_000.0


def _jump_mask(ret: pd.Series, thr: float = 0.08) -> pd.Series:
    return ret.abs() > thr


def ma_cross_mid_signal(close: pd.Series, fast: int = 14, slow: int = 16) -> pd.Series:
    """策略一：14/16 均线交叉；回到中轨平仓；不重复开仓。

    - 14 上穿 16 → 做多
    - 14 下穿 16 → 做空
    - 价格回到中轨 (MA14+MA16)/2 → 平仓
    - 平仓后须等待下一次完整交叉才可再开仓
    """
    if fast >= slow:
        raise ValueError("fast must be < slow")
    ma_f = close.rolling(fast, min_periods=fast).mean()
    ma_s = close.rolling(slow, min_periods=slow).mean()
    mid = (ma_f + ma_s) / 2.0
    c = close.to_numpy(dtype=float)
    f = ma_f.to_numpy(dtype=float)
    s = ma_s.to_numpy(dtype=float)
    m = mid.to_numpy(dtype=float)
    n = len(c)
    out = np.zeros(n, dtype=float)
    pos = 0.0
    for i in range(1, n):
        if np.isnan(f[i]) or np.isnan(s[i]) or np.isnan(f[i - 1]) or np.isnan(s[i - 1]):
            out[i] = 0.0
            pos = 0.0
            continue
        cross_up = f[i - 1] <= s[i - 1] and f[i] > s[i]
        cross_dn = f[i - 1] >= s[i - 1] and f[i] < s[i]
        if pos == 0.0:
            if cross_up:
                pos = 1.0
            elif cross_dn:
                pos = -1.0
        elif pos > 0.0:
            # 回到中轨：收盘价重新触及/跌破中轨则平仓
            if (not np.isnan(m[i])) and c[i] <= m[i]:
                pos = 0.0
            elif cross_dn:
                # 反向交叉：先平后反手视为新开仓
                pos = -1.0
        elif pos < 0.0:
            if (not np.isnan(m[i])) and c[i] >= m[i]:
                pos = 0.0
            elif cross_up:
                pos = 1.0
        out[i] = pos
    return pd.Series(out, index=close.index, name="signal")


def bollinger_fade_signal(
    close: pd.Series,
    window: int = 20,
    entry_std: float = 2.0,
    stop_std: float = 4.0,
) -> pd.Series:
    """策略二：20 日布林带。

    - 上穿上轨后下穿上轨 → 做空
    - 下穿下轨后上穿下轨 → 做多
    - 回到中轨平仓
    - |z|>=4 止损
    - 不重复开仓（需重新完成穿轨再回穿）
    """
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
    armed = 0  # +1 已下穿下轨等待上穿；-1 已上穿上轨等待下穿
    for i in range(1, n):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(mid[i]) or np.isnan(z[i]):
            out[i] = 0.0
            pos = 0.0
            armed = 0
            continue
        # 止损优先
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
        # 空仓：等待穿轨武装 / 回穿开仓
        prev_c, prev_up, prev_lo = c[i - 1], up[i - 1], lo[i - 1]
        if np.isnan(prev_up) or np.isnan(prev_lo):
            out[i] = 0.0
            continue
        # 上穿上轨
        if prev_c <= prev_up and c[i] > up[i]:
            armed = -1
        # 下穿下轨
        if prev_c >= prev_lo and c[i] < lo[i]:
            armed = 1
        # 武装后回穿开仓
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
    """单边方向策略：信号 ∈ {-1,0,1}，在保证金预算内等权分配名义。

    返回 (策略权益曲线以1为起点, 日收益相对总资金capital, 权重, 摘要)
    """
    sig = signals.fillna(0.0).sort_index()
    px = closes.reindex(sig.index).ffill()
    rets = px.pct_change().fillna(0.0)
    rets = rets.mask(rets.abs() > 0.08, 0.0)
    symbols = list(sig.columns)
    n = len(sig)
    max_gross = margin_budget * leverage  # 最大总名义

    weight_rows = np.zeros((n, len(symbols)))
    book_pnl = np.zeros(n)  # 绝对金额
    margin_used = np.zeros(n)
    equity_abs = np.zeros(n)
    cash_pnl_cum = 0.0
    prev_w = pd.Series(0.0, index=symbols)

    for i in range(n):
        d = sig.iloc[i]
        active = d[d != 0.0]
        w = pd.Series(0.0, index=symbols)
        if len(active) > 0 and max_gross > 0:
            per = max_gross / float(len(active))
            w = (d * per).reindex(symbols).fillna(0.0)
        # 当日盈亏用昨仓
        day_ret = rets.iloc[i]
        gross = float((prev_w * day_ret).sum())
        turnover = float((w - prev_w).abs().sum())
        cost = turnover * ((cost_bps + slip_bps) / 10000.0)
        net = gross - cost
        cash_pnl_cum += net
        book_pnl[i] = net
        equity_abs[i] = capital + cash_pnl_cum  # 记账用：策略占用预算在总资金内
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
    data = {s: ma_cross_mid_signal(p["close"], 14, 16) for s, p in panels.items()}
    return pd.DataFrame(data).sort_index().fillna(0.0)


def build_s2_signals(panels: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    data = {s: bollinger_fade_signal(p["close"], 20, 2.0, 4.0) for s, p in panels.items()}
    return pd.DataFrame(data).sort_index().fillna(0.0)


def run_s1(panels: Dict[str, pd.DataFrame], cfg: Optional[BookConfig] = None):
    cfg = cfg or BookConfig()
    closes = pd.DataFrame({s: panels[s]["close"] for s in panels}).sort_index().ffill()
    sig = build_s1_signals(panels).reindex(closes.index).fillna(0.0)
    return simulate_directional_book(
        sig, closes, cfg.margin_s1, cfg.capital, cfg.leverage, cfg.cost_bps, cfg.slip_bps
    ) + (sig,)


def run_s2(panels: Dict[str, pd.DataFrame], cfg: Optional[BookConfig] = None):
    cfg = cfg or BookConfig()
    closes = pd.DataFrame({s: panels[s]["close"] for s in panels}).sort_index().ffill()
    sig = build_s2_signals(panels).reindex(closes.index).fillna(0.0)
    return simulate_directional_book(
        sig, closes, cfg.margin_s2, cfg.capital, cfg.leverage, cfg.cost_bps, cfg.slip_bps
    ) + (sig,)
