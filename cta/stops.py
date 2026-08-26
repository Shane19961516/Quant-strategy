# -*- coding: utf-8 -*-
"""趋势策略止损：ATR 初始止损 + 跟踪止损。

趋势 CTA 典型特征是高赔率、低胜率：必须截断亏损、让利润奔跑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .risk import atr


@dataclass
class StopConfig:
    atr_window: int = 20
    atr_mult: float = 2.0  # 初始止损距离 = atr_mult * ATR
    trail_mult: float = 3.0  # 跟踪止损距离（通常宽于初始，避免过早出场）
    use_trailing: bool = True
    cooldown_bars: int = 0  # 止损后冷却期（0=信号仍同向可次日再入）


def apply_atr_stop(
    raw_signal: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    config: Optional[StopConfig] = None,
    open_: Optional[pd.Series] = None,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """将原始方向信号叠加 ATR 止损。

    返回:
      stopped_signal: 止损后的持仓方向
      stop_price: 当日有效止损价
      stop_exit_ret: 若当日因止损离场，则为相对昨收的止损收益；否则 NaN
                    （回测应用该收益替换全天 close-to-close，避免穿仓高估亏损）
    """
    cfg = config or StopConfig()
    a = atr(high, low, close, window=cfg.atr_window).to_numpy()
    sig = raw_signal.fillna(0.0).to_numpy(dtype=float)
    hi = high.to_numpy(dtype=float)
    lo = low.to_numpy(dtype=float)
    cl = close.to_numpy(dtype=float)
    if open_ is not None:
        open_arr = open_.reindex(close.index).to_numpy(dtype=float)
    else:
        open_arr = None
    n = len(cl)

    out = np.zeros(n, dtype=float)
    stop_px = np.full(n, np.nan)
    stop_exit_ret = np.full(n, np.nan)
    pos = 0.0
    entry = np.nan
    stop = np.nan
    extreme = np.nan
    cool = 0
    prev_close = np.nan

    for i in range(n):
        if np.isnan(a[i]) or a[i] <= 0:
            out[i] = 0.0
            prev_close = cl[i]
            continue

        if cool > 0:
            cool -= 1

        desired = float(np.sign(sig[i]))
        # 无 open 时用昨收近似；有 open 则检测跳空破位
        if open_arr is not None and not np.isnan(open_arr[i]):
            day_open = float(open_arr[i])
        else:
            day_open = float(prev_close) if not np.isnan(prev_close) else float(cl[i])

        if pos != 0:
            # 先检查跳空穿越昨收止损（开盘已破位），再更新跟踪止损
            hit = False
            exit_px = stop
            if not np.isnan(stop):
                if pos > 0 and day_open <= stop:
                    hit = True
                    exit_px = day_open
                elif pos < 0 and day_open >= stop:
                    hit = True
                    exit_px = day_open

            if not hit:
                if pos > 0:
                    extreme = hi[i] if np.isnan(extreme) else max(extreme, hi[i])
                    if cfg.use_trailing:
                        trail = extreme - cfg.trail_mult * a[i]
                        stop = trail if np.isnan(stop) else max(stop, trail)
                    hit = (not np.isnan(stop)) and lo[i] <= stop
                    exit_px = stop
                else:
                    extreme = lo[i] if np.isnan(extreme) else min(extreme, lo[i])
                    if cfg.use_trailing:
                        trail = extreme + cfg.trail_mult * a[i]
                        stop = trail if np.isnan(stop) else min(stop, trail)
                    hit = (not np.isnan(stop)) and hi[i] >= stop
                    exit_px = stop

            if hit:
                # 连续主力合约开盘含换月伪跳，统一按止损价结算（截断亏损、避免伪穿仓）
                fill = stop if not np.isnan(stop) else exit_px
                if not np.isnan(prev_close) and prev_close != 0 and not np.isnan(fill):
                    if pos > 0:
                        stop_exit_ret[i] = fill / prev_close - 1.0
                    else:
                        stop_exit_ret[i] = 1.0 - fill / prev_close
                pos = 0.0
                entry = np.nan
                stop = np.nan
                extreme = np.nan
                cool = cfg.cooldown_bars
                out[i] = 0.0
                stop_px[i] = np.nan
                prev_close = cl[i]
                continue

            if desired != 0 and desired != pos:
                pos = 0.0
                entry = np.nan
                stop = np.nan
                extreme = np.nan
            elif desired == 0:
                pos = 0.0
                entry = np.nan
                stop = np.nan
                extreme = np.nan
                out[i] = 0.0
                stop_px[i] = np.nan
                prev_close = cl[i]
                continue
            else:
                out[i] = pos
                stop_px[i] = stop
                prev_close = cl[i]
                continue

        if pos == 0 and desired != 0 and cool <= 0:
            pos = desired
            entry = cl[i]
            extreme = hi[i] if pos > 0 else lo[i]
            if pos > 0:
                stop = entry - cfg.atr_mult * a[i]
            else:
                stop = entry + cfg.atr_mult * a[i]
            out[i] = pos
            stop_px[i] = stop
        else:
            out[i] = pos
            stop_px[i] = stop if pos != 0 else np.nan

        prev_close = cl[i]

    return (
        pd.Series(out, index=raw_signal.index, name="signal"),
        pd.Series(stop_px, index=raw_signal.index, name="stop"),
        pd.Series(stop_exit_ret, index=raw_signal.index, name="stop_exit_ret"),
    )


def trade_stats_from_signal(
    signal: pd.Series,
    close: pd.Series,
) -> Dict[str, float]:
    """按交易回合统计胜率/赔率（高赔率低胜率的正确口径）。"""
    df = pd.concat([signal.rename("sig"), close.rename("close")], axis=1).dropna(how="any")
    if df.empty:
        return {
            "n_trades": 0.0,
            "trade_win_rate": np.nan,
            "trade_payoff": np.nan,
            "avg_win": np.nan,
            "avg_loss": np.nan,
            "expectancy": np.nan,
        }
    sig = df["sig"].fillna(0.0).to_numpy(dtype=float)
    px = df["close"].to_numpy(dtype=float)
    trades: List[float] = []
    pos = 0.0
    entry = np.nan

    for i in range(len(sig)):
        s = sig[i]
        if pos == 0 and s != 0:
            pos = s
            entry = px[i]
        elif pos != 0 and s != pos:
            ret = (px[i] / entry - 1.0) * pos
            trades.append(float(ret))
            if s != 0:
                pos = s
                entry = px[i]
            else:
                pos = 0.0
                entry = np.nan
    if pos != 0 and not np.isnan(entry):
        trades.append(float((px[-1] / entry - 1.0) * pos))

    if not trades:
        return {
            "n_trades": 0.0,
            "trade_win_rate": np.nan,
            "trade_payoff": np.nan,
            "avg_win": np.nan,
            "avg_loss": np.nan,
            "expectancy": np.nan,
        }

    arr = np.asarray(trades, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    win_rate = float((arr > 0).mean())
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff = float(avg_win / abs(avg_loss)) if avg_loss < 0 else np.nan
    expectancy = float(arr.mean())
    return {
        "n_trades": float(len(arr)),
        "trade_win_rate": win_rate,
        "trade_payoff": payoff,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
    }


def aggregate_trade_stats(
    panels: Dict[str, pd.DataFrame],
    signals: Dict[str, pd.Series],
) -> Dict[str, float]:
    """多品种交易统计汇总。"""
    all_rets: List[float] = []
    for sym, sig in signals.items():
        if sym not in panels:
            continue
        st = trade_stats_from_signal(sig, panels[sym]["close"])
        # 用单品种函数已经汇总；这里再拆不够直接，改为复用对齐后的逻辑
        df = pd.concat([sig.rename("sig"), panels[sym]["close"].rename("close")], axis=1).dropna()
        s = df["sig"].fillna(0.0).to_numpy(dtype=float)
        px = df["close"].to_numpy(dtype=float)
        pos = 0.0
        entry = np.nan
        for i in range(len(s)):
            if pos == 0 and s[i] != 0:
                pos = s[i]
                entry = px[i]
            elif pos != 0 and s[i] != pos:
                all_rets.append(float((px[i] / entry - 1.0) * pos))
                if s[i] != 0:
                    pos = s[i]
                    entry = px[i]
                else:
                    pos = 0.0
                    entry = np.nan
        if pos != 0 and not np.isnan(entry):
            all_rets.append(float((px[-1] / entry - 1.0) * pos))

    n_trades = len(all_rets)
    if n_trades == 0:
        return {
            "n_trades": 0.0,
            "trade_win_rate": np.nan,
            "trade_payoff": np.nan,
            "avg_win": np.nan,
            "avg_loss": np.nan,
            "expectancy": np.nan,
        }
    arr = np.asarray(all_rets, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff = float(avg_win / abs(avg_loss)) if avg_loss < 0 else np.nan
    return {
        "n_trades": float(n_trades),
        "trade_win_rate": float((arr > 0).mean()),
        "trade_payoff": payoff,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": float(arr.mean()),
    }
