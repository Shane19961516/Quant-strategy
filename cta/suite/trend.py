# -*- coding: utf-8 -*-
"""趋势三策略：TSMOM / Donchian / DualMA（文献参数，无寻优）。"""

from __future__ import annotations

from typing import Dict, Tuple

import pandas as pd

from ..signals import donchian_breakout_signal, dual_ma_signal, ts_momentum_signal
from .noleverage import _align_closes, simulate_directional_noleverage


def build_tsmom_signals(panels: Dict[str, pd.DataFrame], lookback: int = 60, skip: int = 1) -> pd.DataFrame:
    cols = {}
    for s, df in panels.items():
        cols[s] = ts_momentum_signal(df["close"], lookback=lookback, skip=skip)
    return pd.DataFrame(cols).sort_index()


def build_donchian_signals(
    panels: Dict[str, pd.DataFrame], entry: int = 20, exit_: int = 10
) -> pd.DataFrame:
    cols = {}
    for s, df in panels.items():
        cols[s] = donchian_breakout_signal(df["high"], df["low"], df["close"], entry=entry, exit_=exit_)
    return pd.DataFrame(cols).sort_index()


def build_dual_ma_signals(panels: Dict[str, pd.DataFrame], fast: int = 20, slow: int = 60) -> pd.DataFrame:
    cols = {}
    for s, df in panels.items():
        cols[s] = dual_ma_signal(df["close"], fast=fast, slow=slow)
    return pd.DataFrame(cols).sort_index()


def run_trend_strategy(
    panels: Dict[str, pd.DataFrame],
    name: str,
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    closes = _align_closes(panels)
    if name == "trend_tsmom":
        sig = build_tsmom_signals(panels)
    elif name == "trend_donchian":
        sig = build_donchian_signals(panels)
    elif name == "trend_dualma":
        sig = build_dual_ma_signals(panels)
    else:
        raise ValueError(name)
    return simulate_directional_noleverage(sig, closes, capital, cost_bps, slip_bps)
