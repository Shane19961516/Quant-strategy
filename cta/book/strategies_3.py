# -*- coding: utf-8 -*-
"""策略三：滚动 20 日相关 > 0.6 的品种做 1:1 名义配对交易。

入场/出场（与策略四一致的价差回归口径）：
- 对数价差滚动 20 日 z-score
- |z|>=2 开仓回归，回到 0 平仓，|z|>=4 止损
- 每日按相关矩阵动态选对；同一品种多对时名义按信号强度叠加
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..metrics import performance_summary
from .strategies_12 import BookConfig


def _pair_z_matrix(closes: pd.DataFrame, window: int = 20) -> Dict[Tuple[str, str], np.ndarray]:
    symbols = list(closes.columns)
    zmap: Dict[Tuple[str, str], np.ndarray] = {}
    logp = np.log(closes.astype(float))
    for i, a in enumerate(symbols):
        for b in symbols[i + 1 :]:
            spread = logp[a] - logp[b]
            mu = spread.rolling(window, min_periods=window).mean()
            sd = spread.rolling(window, min_periods=window).std()
            zmap[(a, b)] = ((spread - mu) / sd.replace(0.0, np.nan)).to_numpy(dtype=float)
    return zmap


def corr_pairs_positions(
    closes: pd.DataFrame,
    corr_window: int = 20,
    corr_thr: float = 0.6,
    z_window: int = 20,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 4.0,
) -> pd.DataFrame:
    """返回每日各品种目标方向（未归一，配对腿 ±1 叠加）。"""
    px = closes.sort_index().ffill()
    rets = px.pct_change()
    symbols = list(px.columns)
    n = len(px)
    pairs = [(a, b) for i, a in enumerate(symbols) for b in symbols[i + 1 :]]
    zmap = _pair_z_matrix(px, z_window)
    pos_state = {p: 0.0 for p in pairs}
    out = np.zeros((n, len(symbols)), dtype=float)
    sym_ix = {s: j for j, s in enumerate(symbols)}
    ret_df = rets

    for t in range(corr_window, n):
        window_rets = ret_df.iloc[t - corr_window + 1 : t + 1]
        corr = window_rets.corr()
        day = np.zeros(len(symbols), dtype=float)
        for a, b in pairs:
            cval = corr.loc[a, b] if a in corr.index and b in corr.columns else np.nan
            if not (np.isfinite(cval) and cval > corr_thr):
                pos_state[(a, b)] = 0.0
                continue
            z = zmap[(a, b)][t]
            st = pos_state[(a, b)]
            if np.isnan(z):
                pos_state[(a, b)] = 0.0
                continue
            if st == 0.0:
                if z >= entry_z:
                    st = -1.0
                elif z <= -entry_z:
                    st = 1.0
            else:
                if abs(z) >= stop_z:
                    st = 0.0
                elif st > 0 and z >= exit_z:
                    st = 0.0
                elif st < 0 and z <= -exit_z:
                    st = 0.0
            pos_state[(a, b)] = st
            if st != 0.0:
                day[sym_ix[a]] += st
                day[sym_ix[b]] += -st
        out[t, :] = day

    return pd.DataFrame(out, index=px.index, columns=symbols)


def simulate_pairs_book(
    leg_signals: pd.DataFrame,
    closes: pd.DataFrame,
    margin_budget: float,
    capital: float,
    leverage: float = 10.0,
    cost_bps: float = 0.5,
    slip_bps: float = 0.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame, Dict[str, float]]:
    """1:1 名义配对：按当日 |signal| 总和把保证金预算分到各腿。"""
    sig = leg_signals.fillna(0.0).sort_index()
    px = closes.reindex(sig.index).ffill()
    rets = px.pct_change().fillna(0.0).mask(lambda x: x.abs() > 0.08, 0.0)
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
        gross_units = float(d.abs().sum())
        w = pd.Series(0.0, index=symbols)
        if gross_units > 1e-12 and max_gross > 0:
            w = d / gross_units * max_gross
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


def run_s3(panels: Dict[str, pd.DataFrame], cfg: Optional[BookConfig] = None):
    cfg = cfg or BookConfig()
    closes = pd.DataFrame({s: panels[s]["close"] for s in panels}).sort_index().ffill()
    legs = corr_pairs_positions(closes)
    return simulate_pairs_book(
        legs, closes, cfg.margin_s3, cfg.capital, cfg.leverage, cfg.cost_bps, cfg.slip_bps
    ) + (legs,)
