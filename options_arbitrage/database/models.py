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
    """Legacy sync positions (API /positions/sync). Kept for compatibility."""

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
    """Legacy trade sync rows."""

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: str = Field(default="DEFAULT", index=True)
    trade_date: str = Field(index=True)
    trade_id: str = Field(index=True)
    symbol: str
    direction: str
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


# ---------------------------------------------------------------------------
# Settlement / live monitoring tables
# ---------------------------------------------------------------------------


class SettlementImport(SQLModel, table=True):
    """One uploaded broker settlement statement (昨日结算单)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: str = Field(index=True)
    settlement_date: str = Field(index=True)  # 结算单交易日期
    client_name: str = ""
    broker: str = ""
    prev_balance: float = 0.0
    balance: float = 0.0
    client_equity: float = 0.0
    margin_occupied: float = 0.0
    available: float = 0.0
    risk_degree: float = 0.0
    premium_net: float = 0.0
    commission: float = 0.0
    realized_pnl: float = 0.0
    filename: str = ""
    imported_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True, index=True)


class YesterdayOptionPosition(SQLModel, table=True):
    """期权持仓汇总 rows from settlement — 昨日持仓基线（与当日成交分离）。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    import_id: int = Field(index=True)
    account_id: str = Field(index=True)
    settlement_date: str = Field(index=True)
    symbol: str = Field(index=True)
    underlying: str = Field(index=True)
    option_type: str = ""
    strike: float = 0.0
    long_volume: int = 0
    long_avg_price: float = 0.0
    short_volume: int = 0
    short_avg_price: float = 0.0
    prev_settle: float = 0.0
    settle_price: float = 0.0  # becomes today's ref settle
    margin: float = 0.0
    multiplier: float = 10.0
    trade_code: str = ""


class TodayManualTrade(SQLModel, table=True):
    """当日成交（手动录入，与昨日持仓严格分离）。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: str = Field(index=True)
    session_date: str = Field(index=True)  # 监控的交易日
    trade_id: str = Field(index=True)
    symbol: str = Field(index=True)
    underlying: str = ""
    option_type: str = ""
    strike: float = 0.0
    side: str  # BUY / SELL
    offset: str = "OPEN"  # OPEN / CLOSE
    price: float
    volume: int
    fee: float = 0.0
    premium_cash: float = 0.0
    multiplier: float = 10.0
    trade_time: str = ""
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MarkQuote(SQLModel, table=True):
    """Manual / latest mark prices for live MTM."""

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: str = Field(index=True)
    session_date: str = Field(index=True)
    symbol: str = Field(index=True)
    price: float
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FuturesManualTrade(SQLModel, table=True):
    """当日期货对冲成交（与期权分表）。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: str = Field(index=True)
    session_date: str = Field(index=True)
    trade_id: str = Field(index=True)
    symbol: str = Field(index=True)  # JD2610 / V2610
    side: str  # BUY / SELL
    volume: int
    price: float
    last: float = 0.0
    fee: float = 0.0
    multiplier: float = 10.0
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
