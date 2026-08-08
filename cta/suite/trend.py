# -*- coding: utf-8 -*-
"""趋势三策略（可实盘加固版）。

1. TSMOM 60d + 逆波动（Baltas 波动缩放思想，杠杆上限 1）
2. Donchian 55/20（海龟长周期，降低 20/10 假突破）
3. Dual MA 20/60 + ATR 跟踪止损（截断亏损）
"""

from __future__ import annotations

from typing import Dict, Tuple

import pandas as pd

from ..signals import donchian_breakout_signal, dual_ma_signal, ts_momentum_signal
from ..stops import StopConfig, apply_atr_stop
from .noleverage import _align_closes, simulate_directional


LIQUID_TREND = ("RB", "HC", "I", "CU", "AU", "M", "Y", "C", "TA", "MA", "SC", "RU")


def _filter_panels(panels: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    out = {}
    for s, df in panels.items():
        su = s.upper()
        if su == "IF":
            continue
        if su in LIQUID_TREND or su in panels:
            # 流动性：近一年日均成交若可得则过滤
            if "volume" in df.columns and len(df) > 60:
                vol = pd.to_numeric(df["volume"], errors="coerce").tail(252).mean()
                if pd.notna(vol) and vol < 1000:
                    continue
            out[su] = df
    return out or panels


def build_tsmom_signals(panels: Dict[str, pd.DataFrame], lookback: int = 60, skip: int = 1) -> pd.DataFrame:
    cols = {}
    for s, df in panels.items():
        cols[s] = ts_momentum_signal(df["close"], lookback=lookback, skip=skip)
    return pd.DataFrame(cols).sort_index()


def build_donchian_signals(
    panels: Dict[str, pd.DataFrame], entry: int = 55, exit_: int = 20
) -> pd.DataFrame:
    cols = {}
    for s, df in panels.items():
        cols[s] = donchian_breakout_signal(df["high"], df["low"], df["close"], entry=entry, exit_=exit_)
    return pd.DataFrame(cols).sort_index()


def build_dual_ma_atr_signals(
    panels: Dict[str, pd.DataFrame],
    fast: int = 20,
    slow: int = 60,
    atr_mult: float = 2.5,
    trail_mult: float = 3.0,
) -> pd.DataFrame:
    cfg = StopConfig(atr_window=20, atr_mult=atr_mult, trail_mult=trail_mult, use_trailing=True)
    cols = {}
    for s, df in panels.items():
        raw = dual_ma_signal(df["close"], fast=fast, slow=slow)
        stopped, _, _ = apply_atr_stop(raw, df["high"], df["low"], df["close"], config=cfg)
        cols[s] = stopped
    return pd.DataFrame(cols).sort_index()


def run_trend_strategy(
    panels: Dict[str, pd.DataFrame],
    name: str,
    capital: float = 1_000_000.0,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    panels = _filter_panels(panels)
    closes = _align_closes(panels)
    if name == "trend_tsmom":
        sig = build_tsmom_signals(panels)
    elif name == "trend_donchian":
        sig = build_donchian_signals(panels, entry=55, exit_=20)
    elif name == "trend_dualma":
        sig = build_dual_ma_atr_signals(panels)
    else:
        raise ValueError(name)
    sig = sig.reindex(closes.index).fillna(0.0)
    return simulate_directional(
        sig, closes, capital=capital, cost_bps=cost_bps, slip_bps=slip_bps, use_inv_vol=True, max_leverage=1.0
    )
