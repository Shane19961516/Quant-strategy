"""Pydantic request/response schemas for the FastAPI layer."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class PositionIn(BaseModel):
    symbol: str
    underlying: str
    direction: Literal["LONG", "SHORT"]
    volume: int
    avg_cost: float
    current_price: float
    multiplier: float = 10.0
    margin: float = 0.0
    # optional greeks override; otherwise API may leave zeros
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    dte: Optional[int] = None
    strike: Optional[float] = None
    option_type: Optional[Literal["CALL", "PUT"]] = None
    underlying_price: Optional[float] = None
    iv: Optional[float] = None


class TradeIn(BaseModel):
    trade_id: str
    symbol: str
    direction: str
    volume: int
    price: float
    fee: float = 0.0


class PositionSyncRequest(BaseModel):
    account_id: str = "MAIN_QUANT_01"
    trade_date: str
    positions: list[PositionIn] = Field(default_factory=list)
    trades_today: list[TradeIn] = Field(default_factory=list)


class PositionSyncResponse(BaseModel):
    account_id: str
    trade_date: str
    positions_upserted: int
    trades_upserted: int
    message: str = "ok"


class UnderlyingGreeks(BaseModel):
    underlying: str
    strangle_status: str
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    daily_pnl: float
    margin_occupation: float
    risk_status: str
    alerts: list[str] = Field(default_factory=list)


class GreeksSummaryResponse(BaseModel):
    trade_date: str
    account_id: str
    total_unrealized_pnl: float
    total_margin_used: float
    by_underlying: list[UnderlyingGreeks]


class ScreenerCandidateOut(BaseModel):
    model_config = {"extra": "ignore"}

    underlying: str
    dte: int
    F: float
    iv_rank: float
    iv_percentile: float
    iv_hv_spread: float
    hv30: float
    current_iv: float
    call_symbol: str
    call_strike: float
    call_delta: float
    call_premium: float
    put_symbol: str
    put_strike: float
    put_delta: float
    put_premium: float
    pop: float
    max_pairs: int
    total_margin: float
    total_premium: float
    expected_roi: float
    unit_margin: float
    blocked_by_margin_cap: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    product: str = ""
    product_name: str = ""
    premium_cash: float = 0.0
    premium_margin_ratio: float = 0.0
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    notes: str = ""


class ScreenerRunResponse(BaseModel):
    count: int
    candidates: list[ScreenerCandidateOut]
    margin_used: float
    blocked: bool = False
