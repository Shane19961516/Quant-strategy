# -*- coding: utf-8 -*-
"""套利三策略（可实盘加固版）。

1. 跨期价差：固定近远月 + 流动性（沿用 book S4）
2. 产业配对：相关门槛 + 半衰期过滤后再做 z 回归
3. 截面短反转：5 日收益截面多空（替代失效的单品种布林）
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..pairs import DEFAULT_ECONOMIC_PAIRS, available_pairs, pair_leg_signals
from ..book.strategies_4 import (
    CalendarConfig,
    build_calendar_book,
    load_cached_contracts_only,
    simulate_spread_book_realistic,
)
from .noleverage import _align_closes, simulate_directional, simulate_pairs


def _half_life(spread: pd.Series) -> float:
    s = spread.dropna()
    if len(s) < 30:
        return float("nan")
    lag = s.shift(1)
    d = s.diff()
    df = pd.concat([d, lag], axis=1).dropna()
    df.columns = ["d", "lag"]
    if float(df["lag"].var()) < 1e-12:
        return float("nan")
    b = float(np.polyfit(df["lag"].to_numpy(), df["d"].to_numpy(), 1)[0])
    if b >= 0:
        return float("nan")
    return float(-np.log(2.0) / b)


def build_gated_pair_legs(
    panels: Dict[str, pd.DataFrame],
    pairs: Optional[Sequence[Tuple[str, str]]] = None,
    window: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    stop_z: float = 3.5,
    min_corr: float = 0.55,
    corr_window: int = 60,
    min_hl: float = 5.0,
    max_hl: float = 45.0,
    hl_lookback: int = 80,
) -> pd.DataFrame:
    """产业配对：滚动相关不足或半衰期异常时禁止开仓。"""
    use = available_pairs(panels.keys(), pairs or DEFAULT_ECONOMIC_PAIRS)
    closes = _align_closes(panels)
    acc = {s: pd.Series(0.0, index=closes.index) for s in closes.columns}

    for a, b in use:
        sa, sb, z = pair_leg_signals(
            panels[a]["close"],
            panels[b]["close"],
            window=window,
            entry_z=entry_z,
            exit_z=exit_z,
            stop_z=stop_z,
        )
        ra = panels[a]["close"].pct_change()
        rb = panels[b]["close"].pct_change()
        corr = ra.rolling(corr_window, min_periods=corr_window).corr(rb)
        # 半衰期：用对数价差
        spread = np.log(panels[a]["close"].astype(float)) - np.log(panels[b]["close"].astype(float))
        sa = sa.reindex(closes.index).fillna(0.0)
        sb = sb.reindex(closes.index).fillna(0.0)
        corr = corr.reindex(closes.index)
        gated_a = sa.copy()
        gated_b = sb.copy()
        pos = 0.0
        for i, dt in enumerate(closes.index):
            c = corr.iloc[i]
            # 滚动半衰期（因果）
            if i + 1 >= hl_lookback:
                hl = _half_life(spread.iloc[i + 1 - hl_lookback : i + 1])
            else:
                hl = float("nan")
            allow = (
                pd.notna(c)
                and float(c) >= min_corr
                and pd.notna(hl)
                and min_hl <= float(hl) <= max_hl
            )
            raw_a = float(sa.iloc[i])
            raw_b = float(sb.iloc[i])
            # 有仓时允许按原信号平仓；无仓时仅 allow 才开
            if not allow:
                pos = 0.0
            elif pos == 0.0:
                if raw_a != 0.0:
                    pos = float(np.sign(raw_a))
            else:
                if raw_a == 0.0:
                    pos = 0.0
                elif float(np.sign(raw_a)) != pos:
                    pos = float(np.sign(raw_a))
            if pos == 0.0:
                gated_a.iloc[i] = 0.0
                gated_b.iloc[i] = 0.0
            else:
                gated_a.iloc[i] = pos
                gated_b.iloc[i] = -pos
        acc[a] = acc[a].add(gated_a, fill_value=0.0)
        acc[b] = acc[b].add(gated_b, fill_value=0.0)

    legs = pd.DataFrame(acc).clip(-1.0, 1.0)
    return legs


def run_arb_pairs(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    closes = _align_closes({k: v for k, v in panels.items() if k.upper() != "IF"})
    panels_u = {k.upper(): v for k, v in panels.items() if k.upper() != "IF"}
    legs = build_gated_pair_legs(panels_u)
    legs = legs.reindex(index=closes.index, columns=closes.columns).fillna(0.0)
    return simulate_pairs(legs, closes, capital, cost_bps, slip_bps, max_leverage=1.0)


def build_xs_reversal_signals(
    closes: pd.DataFrame,
    lookback: int = 5,
    n_long: int = 3,
    n_short: int = 3,
) -> pd.DataFrame:
    """截面短反转：做多近期最弱、做空最强（经典商品短周期反转）。"""
    mom = closes.pct_change(lookback)
    sig = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    for dt in closes.index:
        row = mom.loc[dt].dropna()
        if len(row) < n_long + n_short:
            continue
        order = row.sort_values()
        longs = order.index[:n_long]
        shorts = order.index[-n_short:]
        for s in longs:
            sig.at[dt, s] = 1.0
        for s in shorts:
            sig.at[dt, s] = -1.0
    return sig


def run_arb_xs_reversal(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    prefer = [s for s in ("RB", "HC", "I", "CU", "TA", "M", "C", "Y", "AU", "MA") if s in panels]
    use = prefer or [s for s in panels if s.upper() != "IF"]
    sub = {s: panels[s] for s in use}
    closes = _align_closes(sub)
    sig = build_xs_reversal_signals(closes)
    return simulate_directional(
        sig, closes, capital=capital, cost_bps=cost_bps, slip_bps=slip_bps, use_inv_vol=True, max_leverage=1.0
    )


def run_arb_calendar(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    cal_cfg: Optional[CalendarConfig] = None,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """跨期：保证金预算 ≈ 0.10*capital，近似两腿名义合计 1×。"""
    cal_cfg = cal_cfg or CalendarConfig()
    store = load_cached_contracts_only(list(panels.keys()), cache_dir=contract_cache)
    book = build_calendar_book(store, cal_cfg)
    if not book:
        idx = _align_closes(panels).index
        z = pd.Series(0.0, index=idx, name="ret")
        return pd.Series(1.0, index=idx, name="nav"), z, pd.DataFrame(0.0, index=idx, columns=[])
    margin_budget = capital * 0.10
    nav, ret, lots, _summary, _ = simulate_spread_book_realistic(
        book, margin_budget=margin_budget, capital=capital, cal_cfg=cal_cfg
    )
    return nav, ret, lots


def run_arb_strategy(
    panels: Dict[str, pd.DataFrame],
    name: str,
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    if name == "arb_pairs":
        return run_arb_pairs(panels, capital=capital, cost_bps=cost_bps, slip_bps=slip_bps)
    if name in ("arb_xs_reversal", "arb_bollinger"):
        # arb_bollinger 保留别名指向截面短反转（旧布林已淘汰）
        return run_arb_xs_reversal(panels, capital=capital, cost_bps=cost_bps, slip_bps=slip_bps)
    if name == "arb_calendar":
        return run_arb_calendar(panels, capital=capital, contract_cache=contract_cache)
    raise ValueError(name)
