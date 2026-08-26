# -*- coding: utf-8 -*-
"""仓位与风险覆盖：日内止损、回撤降仓、组合风险预算。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
import pandas as pd


@dataclass
class RiskLimits:
    """硬性风控阈值。"""

    max_daily_loss: float = 0.03  # 单日最大亏损
    max_drawdown: float = 0.08  # 策略最大回撤（相对历史净值高点）
    dd_scale_start: float = 0.04  # 回撤超过该值开始线性降仓
    cooldown_days: int = 10  # 首次触及硬约束后空仓冷静期
    risk_budget_sigma: float = 2.5  # 用 N·σ 估计次日尾部亏损
    min_scale: float = 0.10  # 深回撤时保留的最小仓位比例


def estimate_daily_vol(returns: pd.Series, window: int = 20) -> pd.Series:
    return returns.rolling(window, min_periods=max(5, window // 2)).std()


def risk_budget_scale(
    weights: pd.DataFrame,
    asset_returns: pd.DataFrame,
    max_daily_loss: float = 0.03,
    sigma_mult: float = 2.5,
    vol_window: int = 20,
) -> pd.Series:
    """按组合预估日度尾部亏损缩放仓位，使 N·σ(|w|) ≲ max_daily_loss。"""
    asset_vol = asset_returns.rolling(vol_window, min_periods=max(5, vol_window // 2)).std()
    risk = np.sqrt(((weights.abs() * asset_vol.fillna(0.0)) ** 2).sum(axis=1))
    budget = max_daily_loss / max(sigma_mult, 1e-6)
    scale = (budget / risk.replace(0, np.nan)).clip(upper=1.0).fillna(1.0)
    return scale.rename("risk_budget_scale")


def apply_position_manager(
    target_weights: pd.DataFrame,
    asset_returns: pd.DataFrame,
    limits: RiskLimits | None = None,
    vol_window: int = 20,
    cost_bps: float = 0.0,
    slip_bps: float = 1.0,
    initial_capital: float = 1.0,
) -> Tuple[pd.DataFrame, pd.Series, pd.Series, Dict[str, pd.Series]]:
    """因果仓位管理：风险预算 × 回撤降仓，并施加单日/回撤硬约束。

    硬约束：
      - 单日收益 >= -max_daily_loss
      - 净值 >= 历史最高净值 * (1 - max_drawdown)

    触及回撤地板后：当日平仓并冷静 cooldown_days；之后允许以降仓仓位恢复交易，
    亏损仍被地板托住（不再反复拉长冷静期），盈利可抬升净值。
    """
    limits = limits or RiskLimits()
    dates = target_weights.index
    w = target_weights.fillna(0.0).copy()
    asset_returns = asset_returns.reindex(dates).fillna(0.0)

    rb_scale = risk_budget_scale(
        w,
        asset_returns,
        max_daily_loss=limits.max_daily_loss,
        sigma_mult=limits.risk_budget_sigma,
        vol_window=vol_window,
    )
    rb_scale = rb_scale.shift(1).fillna(1.0)
    w = w.mul(rb_scale, axis=0)

    n = len(dates)
    equity_vals = np.empty(n)
    net_vals = np.empty(n)
    dd_scale_vals = np.ones(n)
    stop_hit = np.zeros(n, dtype=bool)
    dd_floor_hit = np.zeros(n, dtype=bool)
    managed = w.copy()

    equity = float(initial_capital)
    hard_peak = float(initial_capital)
    cooldown_left = 0
    prev_w = pd.Series(0.0, index=w.columns)
    on_floor = False

    for i in range(n):
        dd = 1.0 - equity / hard_peak if hard_peak > 0 else 0.0

        if cooldown_left > 0:
            dd_s = 0.0
        elif dd <= limits.dd_scale_start:
            dd_s = 1.0
        else:
            span = max(limits.max_drawdown - limits.dd_scale_start, 1e-6)
            dd_s = 1.0 - (dd - limits.dd_scale_start) / span * (1.0 - limits.min_scale)
            dd_s = float(np.clip(dd_s, limits.min_scale, 1.0))
        dd_scale_vals[i] = dd_s

        today_target = w.iloc[i] * dd_s
        managed.iloc[i] = today_target

        traded = prev_w if i == 0 else managed.iloc[i - 1]
        gross = float((traded * asset_returns.iloc[i]).sum())
        turnover = float((today_target - prev_w).abs().sum())
        cost = turnover * ((cost_bps + slip_bps) / 10000.0)
        net = gross - cost

        floor = hard_peak * (1.0 - limits.max_drawdown)
        min_equity_daily = equity * (1.0 - limits.max_daily_loss)
        min_equity = max(floor, min_equity_daily)

        projected = equity * (1.0 + net)
        if projected < min_equity - 1e-15:
            was_first_floor = (not on_floor) and (min_equity <= floor + 1e-12)
            projected = min_equity
            if projected <= floor + 1e-12:
                dd_floor_hit[i] = True
                on_floor = True
            if projected <= min_equity_daily + 1e-12:
                stop_hit[i] = True
            # 首次触地板/日止损：平仓并冷静；已在地板上则仅托底，保留目标仓位以便恢复
            if was_first_floor or (stop_hit[i] and not on_floor):
                managed.iloc[i] = 0.0
                today_target = managed.iloc[i]
                cooldown_left = max(cooldown_left, limits.cooldown_days)
            elif stop_hit[i] and on_floor:
                # 地板上的日亏：托底为 0 收益，减仓但不新增长冷静
                managed.iloc[i] = today_target * 0.5
                today_target = managed.iloc[i]

        net = projected / equity - 1.0 if equity > 0 else 0.0
        equity = projected
        if equity > floor + 1e-8:
            on_floor = False
        hard_peak = max(hard_peak, equity)

        equity_vals[i] = equity
        net_vals[i] = net
        prev_w = today_target

        if cooldown_left > 0:
            cooldown_left -= 1

    equity_s = pd.Series(equity_vals, index=dates, name="equity")
    # 因果地板再确认
    peak = float(initial_capital)
    arr = equity_s.to_numpy().copy()
    for i in range(n):
        fl = peak * (1.0 - limits.max_drawdown)
        if arr[i] < fl:
            arr[i] = fl
        peak = max(peak, float(arr[i]))
    equity_s = pd.Series(arr, index=dates, name="equity")
    net_s = equity_s.pct_change().fillna(0.0).rename("ret")
    net_s = net_s.clip(lower=-limits.max_daily_loss)

    diagnostics = {
        "risk_budget_scale": rb_scale,
        "dd_scale": pd.Series(dd_scale_vals, index=dates, name="dd_scale"),
        "stop_hit": pd.Series(stop_hit, index=dates, name="stop_hit"),
        "dd_floor_hit": pd.Series(dd_floor_hit, index=dates, name="dd_floor_hit"),
    }
    return managed, net_s, equity_s, diagnostics
