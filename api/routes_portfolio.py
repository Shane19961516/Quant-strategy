"""Portfolio sync and Greeks aggregation endpoints."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Query
from sqlmodel import Session

from api.schemas import (
    GreeksSummaryResponse,
    PositionSyncRequest,
    PositionSyncResponse,
    UnderlyingGreeks,
)
from core.bs76_engine import black76_greeks
from core.screener import check_delta_tilt
from database.db import get_engine, get_positions, replace_positions, upsert_trades
from database.models import DailyPosition, DailyTrade

router = APIRouter(prefix="/api/v1", tags=["portfolio"])


def _infer_option_meta(symbol: str) -> tuple[Optional[str], Optional[float]]:
    """Parse symbols like m2609-C-3200 or AG2609-P-7100."""
    parts = symbol.replace("_", "-").split("-")
    if len(parts) >= 3:
        opt = parts[-2].upper()
        opt_type = "CALL" if opt.startswith("C") else ("PUT" if opt.startswith("P") else None)
        try:
            strike = float(parts[-1])
        except ValueError:
            strike = None
        return opt_type, strike
    return None, None


def _enrich_greeks(pos_in, settle: float) -> tuple[float, float, float, float]:
    """Compute signed per-lot greeks if enough fields provided; else use payload."""
    if pos_in.delta or pos_in.gamma or pos_in.vega or pos_in.theta:
        # treat provided values as per-lot already
        sign = -1.0 if pos_in.direction == "SHORT" else 1.0
        return (
            sign * pos_in.delta * pos_in.volume,
            sign * pos_in.gamma * pos_in.volume,
            sign * pos_in.vega * pos_in.volume,
            sign * pos_in.theta * pos_in.volume,
        )

    opt_type = pos_in.option_type
    strike = pos_in.strike
    if opt_type is None or strike is None:
        opt_type, strike = _infer_option_meta(pos_in.symbol)
    F = pos_in.underlying_price
    iv = pos_in.iv
    dte = pos_in.dte
    if opt_type and strike and F and iv and dte is not None and dte > 0:
        g = black76_greeks(F, strike, dte / 365.0, 0.02, iv, opt_type)
        sign = -1.0 if pos_in.direction == "SHORT" else 1.0
        return (
            sign * g.delta * pos_in.volume,
            sign * g.gamma * pos_in.volume,
            sign * g.vega * pos_in.volume,
            sign * g.theta * pos_in.volume,
        )
    return 0.0, 0.0, 0.0, 0.0


def _position_pnl(direction: str, volume: int, cost: float, settle: float, mult: float) -> float:
    """SHORT options: profit when settle < cost."""
    if direction == "SHORT":
        return (cost - settle) * volume * mult
    return (settle - cost) * volume * mult


@router.post("/positions/sync", response_model=PositionSyncResponse)
def sync_positions(payload: PositionSyncRequest) -> PositionSyncResponse:
    positions: list[DailyPosition] = []
    for p in payload.positions:
        pnl = _position_pnl(p.direction, p.volume, p.avg_cost, p.current_price, p.multiplier)
        d, g, v, t = _enrich_greeks(p, p.current_price)
        positions.append(
            DailyPosition(
                account_id=payload.account_id,
                trade_date=payload.trade_date,
                underlying=p.underlying,
                symbol=p.symbol,
                direction=p.direction,
                volume=p.volume,
                cost_price=p.avg_cost,
                settle_price=p.current_price,
                margin=p.margin,
                pnl=pnl,
                multiplier=p.multiplier,
                delta=d,
                gamma=g,
                vega=v,
                theta=t,
            )
        )

    trades = [
        DailyTrade(
            account_id=payload.account_id,
            trade_date=payload.trade_date,
            trade_id=t.trade_id,
            symbol=t.symbol,
            direction=t.direction,
            volume=t.volume,
            price=t.price,
            fee=t.fee,
        )
        for t in payload.trades_today
    ]

    with Session(get_engine()) as session:
        n_pos = replace_positions(session, payload.account_id, payload.trade_date, positions)
        n_trd = upsert_trades(session, trades) if trades else 0

    return PositionSyncResponse(
        account_id=payload.account_id,
        trade_date=payload.trade_date,
        positions_upserted=n_pos,
        trades_upserted=n_trd,
    )


@router.get("/portfolio/greeks-summary", response_model=GreeksSummaryResponse)
def greeks_summary(
    account_id: str = Query(default="MAIN_QUANT_01"),
    trade_date: Optional[str] = Query(default=None),
) -> GreeksSummaryResponse:
    with Session(get_engine()) as session:
        positions = get_positions(session, account_id, trade_date)
        if not positions:
            return GreeksSummaryResponse(
                trade_date=trade_date or "",
                account_id=account_id,
                total_unrealized_pnl=0.0,
                total_margin_used=0.0,
                by_underlying=[],
            )
        if trade_date is None:
            trade_date = max(p.trade_date for p in positions)
            positions = [p for p in positions if p.trade_date == trade_date]

    buckets: dict[str, list[DailyPosition]] = defaultdict(list)
    for p in positions:
        buckets[p.underlying].append(p)

    by_underlying: list[UnderlyingGreeks] = []
    total_pnl = 0.0
    total_margin = 0.0

    for underlying, legs in buckets.items():
        net_delta = sum(p.delta for p in legs)
        net_gamma = sum(p.gamma for p in legs)
        net_vega = sum(p.vega for p in legs)
        net_theta = sum(p.theta for p in legs)
        daily_pnl = sum(p.pnl for p in legs)
        margin = sum(p.margin for p in legs)
        total_pnl += daily_pnl
        total_margin += margin

        alerts: list[str] = []
        tilt = check_delta_tilt(net_delta, underlying)
        if tilt:
            alerts.append(tilt.message)

        # Gamma squeeze heuristic on each short leg if symbol parseable
        for p in legs:
            opt_type, strike = _infer_option_meta(p.symbol)
            if strike is None:
                continue
            # without F we skip; try estimate from ATM midpoint of strikes later
            # use settle of option as weak signal — skip unless LONG/SHORT near expiry unknown
            # DTE unknown in DailyPosition — skip unless alert already from tilt
            pass

        risk_status = "SAFE"
        if any("Gamma" in a or "DELTA" in a.upper() or "delta" in a for a in alerts):
            risk_status = "WARN"
        if abs(net_delta) > 0.50:
            risk_status = "CRITICAL"
            alerts.append(f"|net_delta|={abs(net_delta):.3f} critically high")

        has_call = any("-C-" in p.symbol.upper() or "-CALL-" in p.symbol.upper() for p in legs)
        has_put = any("-P-" in p.symbol.upper() or "-PUT-" in p.symbol.upper() for p in legs)
        status = "ACTIVE" if (has_call and has_put) else "PARTIAL"

        by_underlying.append(
            UnderlyingGreeks(
                underlying=underlying,
                strangle_status=status,
                net_delta=round(net_delta, 6),
                net_gamma=round(net_gamma, 6),
                net_vega=round(net_vega, 4),
                net_theta=round(net_theta, 4),
                daily_pnl=round(daily_pnl, 2),
                margin_occupation=round(margin, 2),
                risk_status=risk_status,
                alerts=alerts,
            )
        )

    by_underlying.sort(key=lambda x: x.underlying)
    return GreeksSummaryResponse(
        trade_date=trade_date or "",
        account_id=account_id,
        total_unrealized_pnl=round(total_pnl, 2),
        total_margin_used=round(total_margin, 2),
        by_underlying=by_underlying,
    )
