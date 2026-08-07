# -*- coding: utf-8 -*-
"""套利三策略：跨期 / 产业配对 / 布林均值回归（文献参数，无寻优）。"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd

from ..pairs import available_pairs, build_pairs_symbol_signals
from ..signals import bollinger_reversion_signal
from ..book.strategies_4 import (
    CalendarConfig,
    build_calendar_book,
    load_cached_contracts_only,
    simulate_spread_book_realistic,
)
from .noleverage import _align_closes, simulate_directional_noleverage, simulate_pairs_noleverage


def run_arb_pairs(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
    window: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 3.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    closes = _align_closes(panels)
    pairs = available_pairs(closes.columns)
    # build_pairs_symbol_signals 返回各腿累加信号
    legs = build_pairs_symbol_signals(
        {s: panels[s] for s in closes.columns if s in panels},
        params={"window": window, "entry_z": entry_z, "exit_z": exit_z, "stop_z": stop_z},
        pairs=pairs,
    )
    legs = legs.reindex(index=closes.index, columns=closes.columns).fillna(0.0)
    return simulate_pairs_noleverage(legs, closes, capital, cost_bps, slip_bps)


def run_arb_bollinger(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
    window: int = 20,
    n_std: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 3.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    closes = _align_closes(panels)
    # 仅流动工业品，降低噪声
    prefer = [s for s in ("RB", "HC", "I", "CU", "TA", "M", "C", "Y") if s in panels]
    use = prefer or list(panels.keys())
    cols = {}
    for s in use:
        cols[s] = bollinger_reversion_signal(
            panels[s]["close"], window=window, n_std=n_std, exit_z=exit_z, stop_z=stop_z
        )
    sig = pd.DataFrame(cols).reindex(closes.index)
    return simulate_directional_noleverage(sig, closes[use], capital, cost_bps, slip_bps)


def run_arb_calendar(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    cal_cfg: Optional[CalendarConfig] = None,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """跨期无杠杆：保证金预算 ≈ capital × 平均保证金率，使两腿名义合计约 1× 资金。"""
    cal_cfg = cal_cfg or CalendarConfig()
    store = load_cached_contracts_only(list(panels.keys()), cache_dir=contract_cache)
    book = build_calendar_book(store, cal_cfg)
    if not book:
        idx = _align_closes(panels).index
        z = pd.Series(0.0, index=idx, name="ret")
        return pd.Series(1.0, index=idx, name="nav"), z, pd.DataFrame(0.0, index=idx, columns=[])
    # 保证金率约 8–11%，跨期折扣 0.7 → 用 0.10 近似「名义≈资金」
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
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    if name == "arb_pairs":
        return run_arb_pairs(panels, capital=capital)
    if name == "arb_bollinger":
        return run_arb_bollinger(panels, capital=capital)
    if name == "arb_calendar":
        return run_arb_calendar(panels, capital=capital, contract_cache=contract_cache)
    raise ValueError(name)
