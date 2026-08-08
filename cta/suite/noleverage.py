# -*- coding: utf-8 -*-
"""可实盘回测引擎：T+1、成本、逆波动配权、杠杆上限=1（全额保证金口径）。"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ..metrics import performance_summary

# re-export typing for slice_period annotations used externally


def _align_closes(panels: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({s: panels[s]["close"].astype(float) for s in panels}).sort_index()


def _rolling_vol(closes: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    return closes.pct_change().rolling(window, min_periods=max(10, window // 2)).std()


def simulate_directional(
    signals: pd.DataFrame,
    closes: pd.DataFrame,
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
    vol_window: int = 20,
    max_leverage: float = 1.0,
    use_inv_vol: bool = True,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """方向策略：活跃品种逆波动配权，总名义 ≤ capital * max_leverage。"""
    sig = signals.reindex(closes.index).fillna(0.0)
    rets = closes.pct_change().fillna(0.0)
    vols = _rolling_vol(closes, vol_window).reindex(closes.index)
    symbols = list(closes.columns)
    n = len(closes)
    w_rows = np.zeros((n, len(symbols)))
    pnl = np.zeros(n)
    prev_w = pd.Series(0.0, index=symbols)
    cost_rate = (cost_bps + slip_bps) / 10000.0
    gross_cap = capital * max_leverage

    for i in range(n):
        d = sig.iloc[i]
        active = [s for s in symbols if float(d[s]) != 0.0]
        w = pd.Series(0.0, index=symbols)
        if active:
            if use_inv_vol:
                inv = []
                for s in active:
                    v = float(vols.iloc[i][s]) if s in vols.columns else np.nan
                    if not np.isfinite(v) or v < 1e-8:
                        inv.append(0.0)
                    else:
                        inv.append(1.0 / v)
                inv_arr = np.asarray(inv, dtype=float)
                if inv_arr.sum() <= 0:
                    inv_arr = np.ones(len(active))
                weights_abs = inv_arr / inv_arr.sum()
            else:
                weights_abs = np.ones(len(active)) / float(len(active))
            for s, wa in zip(active, weights_abs):
                w[s] = np.sign(float(d[s])) * wa * gross_cap
        gross = float((prev_w * rets.iloc[i]).sum())
        turnover = float((w - prev_w).abs().sum())
        pnl[i] = gross - turnover * cost_rate
        w_rows[i, :] = w.to_numpy()
        prev_w = w

    weights = pd.DataFrame(w_rows, index=closes.index, columns=symbols)
    pnl_s = pd.Series(pnl, index=closes.index, name="pnl")
    ret = (pnl_s / capital).rename("ret")
    nav = (1.0 + ret).cumprod().rename("nav")
    return nav, ret, weights


def simulate_pairs(
    leg_signals: pd.DataFrame,
    closes: pd.DataFrame,
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
    max_leverage: float = 1.0,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """配对腿：按 |signal| 归一，总名义 ≤ capital * max_leverage。"""
    sig = leg_signals.reindex(closes.index).fillna(0.0)
    rets = closes.pct_change().fillna(0.0)
    symbols = list(closes.columns)
    n = len(closes)
    w_rows = np.zeros((n, len(symbols)))
    pnl = np.zeros(n)
    prev_w = pd.Series(0.0, index=symbols)
    cost_rate = (cost_bps + slip_bps) / 10000.0
    gross_cap = capital * max_leverage

    for i in range(n):
        d = sig.iloc[i]
        abs_sum = float(d.abs().sum())
        w = pd.Series(0.0, index=symbols)
        if abs_sum > 1e-12:
            w = (d / abs_sum * gross_cap).reindex(symbols).fillna(0.0)
        gross = float((prev_w * rets.iloc[i]).sum())
        turnover = float((w - prev_w).abs().sum())
        pnl[i] = gross - turnover * cost_rate
        w_rows[i, :] = w.to_numpy()
        prev_w = w

    weights = pd.DataFrame(w_rows, index=closes.index, columns=symbols)
    pnl_s = pd.Series(pnl, index=closes.index, name="pnl")
    ret = (pnl_s / capital).rename("ret")
    nav = (1.0 + ret).cumprod().rename("nav")
    return nav, ret, weights


# 兼容旧名
simulate_directional_noleverage = simulate_directional
simulate_pairs_noleverage = simulate_pairs


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
    n = (1.0 + r).cumprod()
    n = n / float(n.iloc[0])
    return n.rename("nav"), r, performance_summary(n, r)


def walk_forward_oos_sharpes(
    ret: pd.Series,
    train_years: int = 3,
    test_years: int = 1,
    min_test_days: int = 120,
) -> Dict[str, float]:
    """滚动 WF：训练 train_years → 测试 test_years，返回 OOS 段夏普均值/中位数/胜率。"""
    r = ret.dropna().sort_index()
    if r.empty:
        return {"wf_mean_sharpe": 0.0, "wf_median_sharpe": 0.0, "wf_pos_frac": 0.0, "wf_n": 0.0}
    start = r.index.min()
    end = r.index.max()
    # 第一个测试窗起点 = start + train_years
    cursor = pd.Timestamp(year=start.year + train_years, month=1, day=1)
    sharpes = []
    while cursor < end:
        test_end = pd.Timestamp(year=cursor.year + test_years, month=1, day=1)
        seg = r[(r.index >= cursor) & (r.index < test_end)]
        if len(seg) >= min_test_days and float(seg.std()) > 0:
            sh = float(seg.mean() / seg.std() * np.sqrt(252.0))
            sharpes.append(sh)
        cursor = test_end
    if not sharpes:
        return {"wf_mean_sharpe": 0.0, "wf_median_sharpe": 0.0, "wf_pos_frac": 0.0, "wf_n": 0.0}
    arr = np.asarray(sharpes, dtype=float)
    return {
        "wf_mean_sharpe": float(np.mean(arr)),
        "wf_median_sharpe": float(np.median(arr)),
        "wf_pos_frac": float((arr > 0).mean()),
        "wf_n": float(len(arr)),
    }
