"""SQLModel table definitions for the short-strangle system."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class OptionContractCache(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    underlying: str = Field(index=True)
    option_type: str  # CALL or PUT
    strike: float
    dte: int
    expire_date: str
    iv: float
    delta: float
    gamma: float
    vega: float
    theta: float
    premium: float = 0.0
    F: float = 0.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ScreenerResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scan_time: datetime = Field(default_factory=datetime.utcnow)
    underlying: str = Field(index=True)
    dte: int
    iv_rank: float
    iv_percentile: float
    iv_hv_spread: float
    call_symbol: str
    call_strike: float
    call_delta: float
    put_symbol: str
    put_strike: float
    put_delta: float
    pop: float
    max_pairs: int
    total_margin: float
    total_premium: float
    expected_roi: float
    F: float = 0.0
    current_iv: float = 0.0
    hv30: float = 0.0


class DailyPosition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: str = Field(default="DEFAULT", index=True)
    trade_date: str = Field(index=True)
    underlying: str = Field(index=True)
    symbol: str
    direction: str  # LONG or SHORT
    volume: int
    cost_price: float
    settle_price: float
    margin: float = 0.0
    pnl: float = 0.0
    multiplier: float = 10.0
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0


class DailyTrade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: str = Field(default="DEFAULT", index=True)
    trade_date: str = Field(index=True)
    trade_id: str = Field(index=True)
    symbol: str
    direction: str  # BUY_OPEN / SELL_OPEN / BUY_CLOSE / SELL_CLOSE
    volume: int
    price: float
    fee: float = 0.0


class WatchlistItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    underlying: str = Field(index=True)
    call_symbol: str
    put_symbol: str
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
