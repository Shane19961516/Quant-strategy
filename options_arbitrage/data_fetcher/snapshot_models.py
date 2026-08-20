"""Shared V2 snapshot dataclasses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd


def norm_underlying_symbol(contract: str, exchange: str) -> str:
    c = contract.strip()
    if exchange == "CZCE":
        m = re.match(r"^([A-Za-z]+)(\d+)$", c)
        if m:
            letters, digits = m.group(1), m.group(2)
            # CZCE option months often use 3-digit YYM (e.g. SR611); futures need 4-digit (SR2611)
            if len(digits) == 3:
                digits = "2" + digits
            return letters.upper() + digits
    return c.lower()


@dataclass
class ChainRow:
    strike: float
    call_symbol: str
    put_symbol: str
    call_bid: Optional[float]
    call_ask: Optional[float]
    call_mid: Optional[float]
    call_oi: Optional[float]
    put_bid: Optional[float]
    put_ask: Optional[float]
    put_mid: Optional[float]
    put_oi: Optional[float]
    call_iv: Optional[float] = None
    put_iv: Optional[float] = None


@dataclass
class ProductSnapshotV2:
    product: str
    product_name: str
    exchange: str
    option_month: str
    underlying_futures: str
    quote_date: date
    quote_timestamp: datetime
    data_source: str
    F: float
    settle: Optional[float]
    multiplier: float
    tick_size: float
    expiry_date: date
    dte: int
    chain: list[ChainRow]
    futures_ohlc: pd.DataFrame
    all_months: list[str] = field(default_factory=list)
    tenor_points: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FetchManifest:
    data_source: str
    quote_asof: str
    products_ok: list[str]
    products_failed: list[dict[str, str]]
    methods_version: str = "methods-v2.0.0"
