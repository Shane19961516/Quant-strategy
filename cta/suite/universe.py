# -*- coding: utf-8 -*-
"""全品种宇宙（本仓库 akshare 面板）。

商品全做；股指 IF 机制不同，默认只进趋势、不进跨期/配对。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd

from ..book.strategies_4 import CONTRACT_SPEC, CalendarConfig

# 数据目录全部商品（不含 IF）
ALL_COMMODITIES: Tuple[str, ...] = (
    "RB",
    "HC",
    "I",
    "CU",
    "AU",
    "RU",
    "M",
    "Y",
    "C",
    "TA",
    "MA",
    "SC",
)

# 产业逻辑配对（全链条，仍非无脑全组合）
FULL_ECONOMIC_PAIRS: Tuple[Tuple[str, str], ...] = (
    # 黑色
    ("RB", "HC"),
    ("I", "RB"),
    ("I", "HC"),
    # 油脂/饲料
    ("Y", "M"),
    ("C", "M"),
    ("Y", "C"),
    # 化工
    ("MA", "TA"),
    ("RU", "TA"),
    # 有色/贵金属（弱相关，靠门控）
    ("CU", "AU"),
    # 能源-化工弱链
    ("SC", "TA"),
    ("SC", "MA"),
)

# 跨期：CONTRACT_SPEC 内除 IF
ALL_CALENDAR_SYMBOLS: Tuple[str, ...] = tuple(
    s for s in CONTRACT_SPEC if s.upper() != "IF"
)

# 趋势：商品 + 可选 IF
TREND_SYMBOLS: Tuple[str, ...] = ALL_COMMODITIES + ("IF",)

# carry 截面
CARRY_SYMBOLS: Tuple[str, ...] = ALL_CALENDAR_SYMBOLS


def commodity_panels(panels: Dict[str, pd.DataFrame], include_if: bool = False) -> Dict[str, pd.DataFrame]:
    out = {}
    for k, v in panels.items():
        su = k.upper()
        if su == "IF" and not include_if:
            continue
        if su == "IF" or su in ALL_COMMODITIES:
            out[su] = v
    return out


def available_full_pairs(symbols: Iterable[str]) -> List[Tuple[str, str]]:
    syms = {s.upper() for s in symbols}
    return [(a, b) for a, b in FULL_ECONOMIC_PAIRS if a in syms and b in syms]


def full_calendar_config(
    allow: Sequence[str] | None = None,
    min_volume: float = 3000.0,
    min_oi: float = 5000.0,
) -> CalendarConfig:
    """全品种跨期配置：放宽流动性门槛，排除 IF。"""
    allow_t = tuple(allow) if allow is not None else ALL_CALENDAR_SYMBOLS
    return CalendarConfig(
        allow=allow_t,
        exclude=("IF",),
        min_volume=min_volume,
        min_oi=min_oi,
    )
