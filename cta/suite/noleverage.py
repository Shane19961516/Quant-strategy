# -*- coding: utf-8 -*-
"""无杠杆回测引擎：总名义敞口 ≤ 资金，收盘信号 T+1 成交。"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ..metrics import performance_summary


def _align_closes(panels: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({s: panels[s]["close"].astype(float) for s in panels}).sort_index()


def simulate_directional_noleverage(
    signals: pd.DataFrame,
    closes: pd.DataFrame,
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """方向策略：活跃品种等权，|名义|合计 = capital。"""
    sig = signals.reindex(closes.index).fillna(0.0)
    rets = closes.pct_change().fillna(0.0)
    symbols = list(closes.columns)
    n = len(closes)
    w_rows = np.zeros((n, len(symbols)))
    pnl = np.zeros(n)
    prev_w = pd.Series(0.0, index=symbols)
    cost_rate = (cost_bps + slip_bps) / 10000.0

    for i in range(n):
        d = sig.iloc[i]
        active = d[d != 0.0]
        w = pd.Series(0.0, index=symbols)
        if len(active) > 0:
            leg = capital / float(len(active))
            w = (np.sign(active) * leg).reindex(symbols).fillna(0.0)
        # T+1：用昨仓吃今日收益
        gross = float((prev_w * rets.iloc[i]).sum())
        turnover = float((w - prev_w).abs().sum())
        cost = turnover * cost_rate
        pnl[i] = gross - cost
        w_rows[i, :] = w.to_numpy()
        prev_w = w

    weights = pd.DataFrame(w_rows, index=closes.index, columns=symbols)
    pnl_s = pd.Series(pnl, index=closes.index, name="pnl")
    ret = (pnl_s / capital).rename("ret")
    nav = (1.0 + ret).cumprod().rename("nav")
    return nav, ret, weights


def simulate_pairs_noleverage(
    leg_signals: pd.DataFrame,
    closes: pd.DataFrame,
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """配对腿信号：|signal| 归一后总名义 = capital（多空名义各约一半）。"""
    sig = leg_signals.reindex(closes.index).fillna(0.0)
    rets = closes.pct_change().fillna(0.0)
    symbols = list(closes.columns)
    n = len(closes)
    w_rows = np.zeros((n, len(symbols)))
    pnl = np.zeros(n)
    prev_w = pd.Series(0.0, index=symbols)
    cost_rate = (cost_bps + slip_bps) / 10000.0

    for i in range(n):
        d = sig.iloc[i]
        abs_sum = float(d.abs().sum())
        w = pd.Series(0.0, index=symbols)
        if abs_sum > 1e-12:
            w = (d / abs_sum * capital).reindex(symbols).fillna(0.0)
        gross = float((prev_w * rets.iloc[i]).sum())
        turnover = float((w - prev_w).abs().sum())
        cost = turnover * cost_rate
        pnl[i] = gross - cost
        w_rows[i, :] = w.to_numpy()
        prev_w = w

    weights = pd.DataFrame(w_rows, index=closes.index, columns=symbols)
    pnl_s = pd.Series(pnl, index=closes.index, name="pnl")
    ret = (pnl_s / capital).rename("ret")
    nav = (1.0 + ret).cumprod().rename("nav")
    return nav, ret, weights


def slice_period(
    nav: pd.Series,
    ret: pd.Series,
    start: Optional[str],
    end: Optional[str],
) -> Tuple[pd.Series, pd.Series, Dict]:
    r = ret.copy()
    if start:
        r = r[r.index >= pd.Timestamp(start)]
    if end:
        r = r[r.index <= pd.Timestamp(end)]
    if r.empty:
        empty = pd.Series(dtype=float)
        return empty, empty, performance_summary(pd.Series([1.0]), pd.Series([0.0]))
    # 区间净值从 1 复利
    n = (1.0 + r).cumprod()
    n = n / n.iloc[0]
    n.iloc[0] = 1.0
    # 更干净：用 cumprod 直接
    n = (1.0 + r).cumprod()
    # 归一到区间起点
    n = n / float(n.iloc[0])
    return n.rename("nav"), r, performance_summary(n, r)
