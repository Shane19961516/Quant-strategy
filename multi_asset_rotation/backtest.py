"""
回测引擎：周五收盘信号 -> 下一交易日（周一）调仓。

持仓收益按日度 close-to-close 计，调仓发生在信号日后的首个交易日开盘附近；
回测中以该日开盘前完成权重切换、并从当日收盘收益开始计入新权重
（等价于“周一开盘成交、当日纳入新组合”的可实盘近似，避免用失真开盘价制造虚假滑点）。
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from config import CODES, PARAMS, SAFE


def map_signal_to_exec(
    signal_weights: pd.DataFrame, calendar: pd.DatetimeIndex
) -> Dict[pd.Timestamp, pd.Series]:
    pos = {d: i for i, d in enumerate(calendar)}
    exec_w: Dict[pd.Timestamp, pd.Series] = {}
    for sig_dt, row in signal_weights.iterrows():
        if sig_dt not in pos:
            continue
        i = pos[sig_dt]
        if i + 1 >= len(calendar):
            continue
        exec_dt = calendar[i + 1]
        w = row.reindex(CODES).fillna(0.0)
        s = float(w.sum())
        exec_w[exec_dt] = (w / s) if s > 0 else pd.Series({SAFE: 1.0}, index=CODES).fillna(0.0)
    return exec_w


def run_backtest(
    close: pd.DataFrame,
    signal_weights: pd.DataFrame,
    cost_bps: float | None = None,
) -> Tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    """
    返回：
      nav: 组合净值
      weights_daily: 每日持仓权重（收盘后）
      trades: 调仓记录
    """
    cost_bps = float(PARAMS["cost_bps"] if cost_bps is None else cost_bps)
    calendar = close.index
    daily_ret = close.pct_change().fillna(0.0)
    exec_w = map_signal_to_exec(signal_weights, calendar)

    cur = pd.Series(0.0, index=CODES)
    cur[SAFE] = 1.0
    equity = 1.0
    nav_list = []
    w_rows = []
    trades = []

    for i, dt in enumerate(calendar):
        if dt in exec_w:
            tw = exec_w[dt].reindex(CODES).fillna(0.0)
            s = float(tw.sum())
            tw = tw / s if s > 0 else cur * 0
            if float(tw.sum()) <= 0:
                tw[SAFE] = 1.0
            turn = float((tw - cur).abs().sum()) / 2.0
            equity *= 1.0 - turn * cost_bps / 10000.0
            trades.append(
                {
                    "exec_date": dt,
                    "turnover": turn,
                    "cost": turn * cost_bps / 10000.0,
                    **{c: float(tw[c]) for c in CODES},
                }
            )
            cur = tw

        if i > 0:
            # 仅对当日有价格的资产计收益，权重再归一
            r = daily_ret.loc[dt]
            avail = close.loc[dt].notna()
            w = cur.copy()
            w[~avail] = 0.0
            if float(w.sum()) > 0:
                w = w / w.sum()
            else:
                w = cur * 0
                w[SAFE] = 1.0
            equity *= 1.0 + float((w * r.fillna(0.0)).sum())
            cur = w

        nav_list.append(equity)
        w_rows.append(cur.copy())

    nav = pd.Series(nav_list, index=calendar, name="nav")
    weights_daily = pd.DataFrame(w_rows, index=calendar)
    trade_df = pd.DataFrame(trades).set_index("exec_date") if trades else pd.DataFrame()
    return nav, weights_daily, trade_df


def equal_weight_benchmark(close: pd.DataFrame) -> pd.Series:
    ret = close.pct_change()
    mask = ret.notna()
    w = mask.astype(float)
    w = w.div(w.sum(axis=1), axis=0)
    port = (w.shift(1) * ret).sum(axis=1).fillna(0.0)
    return (1.0 + port).cumprod().rename("equal_weight")


def buy_hold_asset(close: pd.DataFrame, code: str) -> pd.Series:
    s = close[code].dropna()
    return (s / s.iloc[0]).rename(code)
