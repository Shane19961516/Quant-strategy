"""Live P&L engine: yesterday settlement positions + today's manual trades."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class MarkPrice:
    symbol: str
    price: float


@dataclass
class PositionLegState:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    multiplier: float
    long_volume: int = 0
    short_volume: int = 0
    # reference settle from yesterday EOD (今结算 of settlement sheet)
    ref_settle: float = 0.0
    # volume-weighted open prices for today-opened lots (approximate)
    long_cost: float = 0.0
    short_cost: float = 0.0
    margin: float = 0.0
    # lots that came from yesterday (for carry MTM vs ref_settle)
    y_long: int = 0
    y_short: int = 0
    # lots opened today (for MTM vs trade price)
    t_long: int = 0
    t_short: int = 0
    t_long_cost: float = 0.0
    t_short_cost: float = 0.0

    @property
    def net_volume(self) -> int:
        return self.long_volume - self.short_volume


@dataclass
class LegPnL:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    long_volume: int
    short_volume: int
    mark: float
    ref_settle: float
    carry_pnl: float
    today_trade_pnl: float
    fee: float
    total_pnl: float
    margin: float
    multiplier: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LivePnLReport:
    account_id: str
    settlement_date: str  # yesterday settlement date
    session_date: str  # today trading date being monitored
    opening_equity: float
    margin_occupied_settlement: float
    available_settlement: float
    risk_degree_settlement: float
    total_carry_pnl: float
    total_today_trade_pnl: float
    total_fees: float
    total_pnl: float
    estimated_equity: float
    by_leg: list[LegPnL] = field(default_factory=list)
    by_underlying: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _apply_close(long_v: int, short_v: int, side: str, volume: int) -> tuple[int, int, int]:
    """
    Apply a close trade. Returns (new_long, new_short, closed_volume_applied).
    BUY close reduces short; SELL close reduces long.
    """
    if side == "BUY":
        closed = min(short_v, volume)
        return long_v, short_v - closed, closed
    closed = min(long_v, volume)
    return long_v - closed, short_v, closed


def build_book(
    yesterday_positions: list[dict[str, Any]],
    today_trades: list[dict[str, Any]],
) -> dict[str, PositionLegState]:
    """Merge yesterday option positions with today's trades into a live book."""
    book: dict[str, PositionLegState] = {}

    for p in yesterday_positions:
        sym = p["symbol"]
        # 优先 close，其次显式 ref_price，最后才 settle（与 Excel 口径对齐）
        ref = float(
            p.get("close_price")
            or p.get("ref_close")
            or p.get("ref_price")
            or p.get("settle_price")
            or p.get("ref_settle")
            or 0
        )
        leg = PositionLegState(
            symbol=sym,
            underlying=p.get("underlying") or sym,
            option_type=p.get("option_type") or "",
            strike=float(p.get("strike") or 0),
            multiplier=float(p.get("multiplier") or 10),
            long_volume=int(p.get("long_volume") or 0),
            short_volume=int(p.get("short_volume") or 0),
            ref_settle=ref,
            long_cost=float(p.get("long_avg_price") or 0),
            short_cost=float(p.get("short_avg_price") or 0),
            margin=float(p.get("margin") or 0),
            y_long=int(p.get("long_volume") or 0),
            y_short=int(p.get("short_volume") or 0),
        )
        book[sym] = leg

    # sort trades by time if present
    ordered = sorted(
        today_trades,
        key=lambda t: (str(t.get("trade_date") or ""), str(t.get("trade_time") or ""), str(t.get("trade_id") or "")),
    )

    for t in ordered:
        sym = t["symbol"]
        side = str(t.get("side") or "").upper()
        offset = str(t.get("offset") or "OPEN").upper()
        vol = int(t.get("volume") or 0)
        px = float(t.get("price") or 0)
        if vol <= 0:
            continue
        if sym not in book:
            book[sym] = PositionLegState(
                symbol=sym,
                underlying=t.get("underlying") or sym,
                option_type=t.get("option_type") or "",
                strike=float(t.get("strike") or 0),
                multiplier=float(t.get("multiplier") or 10),
                ref_settle=px,  # no yesterday ref — use trade as anchor for residual
            )
        leg = book[sym]
        if t.get("multiplier"):
            leg.multiplier = float(t["multiplier"])

        if offset == "CLOSE":
            # close against yesterday first, then today
            if side == "BUY":
                # covering short
                close_y = min(leg.y_short, vol)
                leg.y_short -= close_y
                rem = vol - close_y
                close_t = min(leg.t_short, rem)
                leg.t_short -= close_t
                # adjust short cost for today lots remaining — keep simple
                leg.short_volume = leg.y_short + leg.t_short
            else:
                close_y = min(leg.y_long, vol)
                leg.y_long -= close_y
                rem = vol - close_y
                close_t = min(leg.t_long, rem)
                leg.t_long -= close_t
                leg.long_volume = leg.y_long + leg.t_long
        else:
            # OPEN
            if side == "SELL":
                # weighted avg for today short
                new_v = leg.t_short + vol
                if new_v > 0:
                    leg.t_short_cost = (leg.t_short_cost * leg.t_short + px * vol) / new_v
                leg.t_short = new_v
                leg.short_volume = leg.y_short + leg.t_short
                if leg.short_volume > 0:
                    # overall short cost approx
                    y_part = leg.short_cost * leg.y_short
                    # if y_short originally had short_cost from settlement avg
                    leg.short_cost = (y_part + leg.t_short_cost * leg.t_short) / leg.short_volume
            else:
                new_v = leg.t_long + vol
                if new_v > 0:
                    leg.t_long_cost = (leg.t_long_cost * leg.t_long + px * vol) / new_v
                leg.t_long = new_v
                leg.long_volume = leg.y_long + leg.t_long
                if leg.long_volume > 0:
                    y_part = leg.long_cost * leg.y_long
                    leg.long_cost = (y_part + leg.t_long_cost * leg.t_long) / leg.long_volume

    return book


def compute_live_pnl(
    *,
    account_id: str,
    settlement_date: str,
    session_date: str,
    yesterday_positions: list[dict[str, Any]],
    today_trades: list[dict[str, Any]],
    marks: dict[str, float],
    opening_equity: float = 0.0,
    margin_occupied_settlement: float = 0.0,
    available_settlement: float = 0.0,
    risk_degree_settlement: float = 0.0,
) -> LivePnLReport:
    """
    Carry PnL (yesterday lots): short (ref - mark)*y_short*mult ; long (mark - ref)*y_long*mult
    Today open PnL: short (trade_cost - mark)*t_short*mult ; long (mark - trade_cost)*t_long*mult
    Fees: sum of today trade fees
    """
    book = build_book(yesterday_positions, today_trades)
    fee_by_symbol: dict[str, float] = {}
    for t in today_trades:
        fee_by_symbol[t["symbol"]] = fee_by_symbol.get(t["symbol"], 0.0) + float(t.get("fee") or 0)

    legs: list[LegPnL] = []
    total_carry = 0.0
    total_today = 0.0
    total_fees = sum(fee_by_symbol.values())

    for sym, leg in sorted(book.items()):
        mark = float(marks.get(sym, leg.ref_settle or leg.short_cost or leg.long_cost or 0))
        mult = leg.multiplier
        carry = (leg.ref_settle - mark) * leg.y_short * mult + (mark - leg.ref_settle) * leg.y_long * mult
        today_pnl = (leg.t_short_cost - mark) * leg.t_short * mult + (mark - leg.t_long_cost) * leg.t_long * mult
        fee = fee_by_symbol.get(sym, 0.0)
        total = carry + today_pnl - fee
        total_carry += carry
        total_today += today_pnl
        legs.append(
            LegPnL(
                symbol=sym,
                underlying=leg.underlying,
                option_type=leg.option_type,
                strike=leg.strike,
                long_volume=leg.long_volume,
                short_volume=leg.short_volume,
                mark=mark,
                ref_settle=leg.ref_settle,
                carry_pnl=round(carry, 2),
                today_trade_pnl=round(today_pnl, 2),
                fee=round(fee, 2),
                total_pnl=round(total, 2),
                margin=round(leg.margin, 2),
                multiplier=mult,
            )
        )

    # aggregate by underlying
    by_u: dict[str, dict[str, Any]] = {}
    for leg in legs:
        u = by_u.setdefault(
            leg.underlying,
            {
                "underlying": leg.underlying,
                "total_pnl": 0.0,
                "carry_pnl": 0.0,
                "today_trade_pnl": 0.0,
                "fee": 0.0,
                "margin": 0.0,
                "short_volume": 0,
                "long_volume": 0,
                "legs": 0,
            },
        )
        u["total_pnl"] += leg.total_pnl
        u["carry_pnl"] += leg.carry_pnl
        u["today_trade_pnl"] += leg.today_trade_pnl
        u["fee"] += leg.fee
        u["margin"] += leg.margin
        u["short_volume"] += leg.short_volume
        u["long_volume"] += leg.long_volume
        u["legs"] += 1

    by_underlying = sorted(by_u.values(), key=lambda x: x["total_pnl"])
    for u in by_underlying:
        for k in ("total_pnl", "carry_pnl", "today_trade_pnl", "fee", "margin"):
            u[k] = round(u[k], 2)

    total_pnl = total_carry + total_today - total_fees
    alerts: list[str] = []
    if opening_equity > 0 and margin_occupied_settlement / opening_equity > 0.60:
        alerts.append("结算保证金占用率已超过 60%，Screener 将拒绝新开仓建议")

    return LivePnLReport(
        account_id=account_id,
        settlement_date=settlement_date,
        session_date=session_date,
        opening_equity=opening_equity,
        margin_occupied_settlement=margin_occupied_settlement,
        available_settlement=available_settlement,
        risk_degree_settlement=risk_degree_settlement,
        total_carry_pnl=round(total_carry, 2),
        total_today_trade_pnl=round(total_today, 2),
        total_fees=round(total_fees, 2),
        total_pnl=round(total_pnl, 2),
        estimated_equity=round(opening_equity + total_pnl, 2) if opening_equity else round(total_pnl, 2),
        by_leg=legs,
        by_underlying=by_underlying,
        alerts=alerts,
    )
