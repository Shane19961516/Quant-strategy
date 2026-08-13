"""Portfolio net-position and BS76 Greeks aggregation (昨仓+今成交)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from core.bs76_engine import black76_greeks, implied_volatility
from core.pnl_engine import PositionLegState, build_book
from core.settlement_parser import product_code_from_underlying


def estimate_F_from_book(legs: list[PositionLegState]) -> float:
    """
    Estimate futures level from short strangle geometry:
    midpoint between highest short-put strike and lowest short-call strike;
    fallback to volume-weighted average strike.
    """
    short_puts = [lg.strike for lg in legs if lg.option_type == "PUT" and lg.short_volume > 0]
    short_calls = [lg.strike for lg in legs if lg.option_type == "CALL" and lg.short_volume > 0]
    if short_puts and short_calls:
        return 0.5 * (max(short_puts) + min(short_calls))

    long_puts = [lg.strike for lg in legs if lg.option_type == "PUT" and lg.long_volume > 0]
    long_calls = [lg.strike for lg in legs if lg.option_type == "CALL" and lg.long_volume > 0]
    if long_puts and long_calls:
        return 0.5 * (max(long_puts) + min(long_calls))

    wsum = 0.0
    w = 0.0
    for lg in legs:
        v = lg.long_volume + lg.short_volume
        if v > 0 and lg.strike > 0:
            wsum += lg.strike * v
            w += v
    if w > 0:
        return wsum / w
    strikes = [lg.strike for lg in legs if lg.strike > 0]
    return float(sum(strikes) / len(strikes)) if strikes else 0.0


def estimate_dte_from_underlying(underlying: str, asof: Optional[date] = None) -> int:
    """Rough DTE from contract month code: AP610 / EG2610 / SR611."""
    asof = asof or date.today()
    m = re.search(r"(\d{3,4})$", underlying.strip())
    if not m:
        return 35
    code = m.group(1)
    try:
        if len(code) == 3:
            # YMM e.g. 610 -> 2026-10
            yy = 2020 + int(code[0])
            mm = int(code[1:])
        else:
            # YYMM e.g. 2610
            yy = 2000 + int(code[:2])
            mm = int(code[2:])
        if mm < 1 or mm > 12:
            return 35
        # commodity options often expire ~ month before / early in delivery month — use mid-month
        expiry = date(yy, mm, 15)
        dte = (expiry - asof).days
        return max(dte, 1)
    except ValueError:
        return 35


def _solve_iv(mark: float, F: float, K: float, T: float, r: float, opt: str) -> float:
    if mark <= 0 or F <= 0 or K <= 0 or T <= 0:
        return 0.25
    try:
        return float(implied_volatility(mark, F, K, T, r, opt if opt in ("CALL", "PUT") else "CALL"))
    except Exception:
        return 0.25


@dataclass
class LegGreeks:
    symbol: str
    underlying: str
    product: str
    option_type: str
    strike: float
    net_volume: int  # long - short
    long_volume: int
    short_volume: int
    mark: float
    F: float
    iv: float
    dte: int
    delta: float
    gamma: float
    vega: float  # cash per 1% (already × mult × signed lots)
    theta: float  # cash per day
    unit_delta: float
    unit_gamma: float
    unit_vega: float
    unit_theta: float
    multiplier: float
    y_long: int = 0
    y_short: int = 0
    t_long: int = 0
    t_short: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnderlyingNetSummary:
    underlying: str
    product: str
    F_est: float
    dte: int
    long_volume: int
    short_volume: int
    net_volume: int
    call_long: int
    call_short: int
    put_long: int
    put_short: int
    y_long: int
    y_short: int
    t_long: int
    t_short: int
    margin: float
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    risk_status: str
    legs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductNetSummary:
    product: str
    underlyings: list[str]
    long_volume: int
    short_volume: int
    net_volume: int
    margin: float
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    risk_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GreeksBookReport:
    asof: str
    r: float
    leg_greeks: list[LegGreeks] = field(default_factory=list)
    by_underlying: list[UnderlyingNetSummary] = field(default_factory=list)
    by_product: list[ProductNetSummary] = field(default_factory=list)
    total_net_delta: float = 0.0
    total_net_gamma: float = 0.0
    total_net_vega: float = 0.0
    total_net_theta: float = 0.0
    total_long_volume: int = 0
    total_short_volume: int = 0
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asof": self.asof,
            "r": self.r,
            "leg_greeks": [x.to_dict() for x in self.leg_greeks],
            "by_underlying": [x.to_dict() for x in self.by_underlying],
            "by_product": [x.to_dict() for x in self.by_product],
            "total_net_delta": self.total_net_delta,
            "total_net_gamma": self.total_net_gamma,
            "total_net_vega": self.total_net_vega,
            "total_net_theta": self.total_net_theta,
            "total_long_volume": self.total_long_volume,
            "total_short_volume": self.total_short_volume,
            "alerts": self.alerts,
        }


def _risk_status(net_delta: float, delta_tilt: float = 0.30) -> str:
    ad = abs(net_delta)
    if ad > 0.50:
        return "CRITICAL"
    if ad > delta_tilt:
        return "WARN"
    return "SAFE"


def compute_net_positions_and_greeks(
    *,
    yesterday_positions: list[dict[str, Any]],
    today_trades: list[dict[str, Any]],
    marks: dict[str, float],
    underlying_F: Optional[dict[str, float]] = None,
    asof: Optional[str] = None,
    r: float = 0.02,
    delta_tilt: float = 0.30,
) -> GreeksBookReport:
    """
    Merge 昨仓+今成交 → net book, then BS76 Greeks per leg / underlying / product.
    """
    underlying_F = underlying_F or {}
    asof_d = datetime.strptime(asof, "%Y-%m-%d").date() if asof else date.today()
    book = build_book(yesterday_positions, today_trades)

    # group legs by underlying for F estimate
    by_u_legs: dict[str, list[PositionLegState]] = {}
    for leg in book.values():
        by_u_legs.setdefault(leg.underlying, []).append(leg)

    F_map: dict[str, float] = {}
    dte_map: dict[str, int] = {}
    for u, legs in by_u_legs.items():
        F_map[u] = float(underlying_F.get(u) or estimate_F_from_book(legs))
        dte_map[u] = estimate_dte_from_underlying(u, asof_d)

    leg_greeks: list[LegGreeks] = []
    for sym, leg in sorted(book.items()):
        if leg.long_volume == 0 and leg.short_volume == 0:
            continue
        mark = float(marks.get(sym, leg.ref_settle or leg.short_cost or leg.long_cost or 0))
        F = F_map.get(leg.underlying, 0.0)
        dte = dte_map.get(leg.underlying, 35)
        T = dte / 365.0
        opt = leg.option_type if leg.option_type in ("CALL", "PUT") else "CALL"
        iv = _solve_iv(mark, F, leg.strike, T, r, opt)
        try:
            g = black76_greeks(F, leg.strike, T, r, iv, opt)  # type: ignore[arg-type]
        except Exception:
            continue
        # signed lots: long +, short −
        signed = leg.long_volume - leg.short_volume
        mult = leg.multiplier
        leg_greeks.append(
            LegGreeks(
                symbol=sym,
                underlying=leg.underlying,
                product=product_code_from_underlying(leg.underlying),
                option_type=opt,
                strike=leg.strike,
                net_volume=signed,
                long_volume=leg.long_volume,
                short_volume=leg.short_volume,
                mark=mark,
                F=round(F, 4),
                iv=round(iv, 6),
                dte=dte,
                delta=round(g.delta * signed, 6),
                gamma=round(g.gamma * signed, 8),
                vega=round(g.vega * signed * mult, 4),
                theta=round(g.theta * signed * mult, 4),
                unit_delta=round(g.delta, 6),
                unit_gamma=round(g.gamma, 8),
                unit_vega=round(g.vega, 6),
                unit_theta=round(g.theta, 6),
                multiplier=mult,
                y_long=leg.y_long,
                y_short=leg.y_short,
                t_long=leg.t_long,
                t_short=leg.t_short,
            )
        )

    # underlying rollup
    u_acc: dict[str, dict[str, Any]] = {}
    for lg in leg_greeks:
        a = u_acc.setdefault(
            lg.underlying,
            {
                "underlying": lg.underlying,
                "product": lg.product,
                "F_est": lg.F,
                "dte": lg.dte,
                "long_volume": 0,
                "short_volume": 0,
                "call_long": 0,
                "call_short": 0,
                "put_long": 0,
                "put_short": 0,
                "y_long": 0,
                "y_short": 0,
                "t_long": 0,
                "t_short": 0,
                "margin": 0.0,
                "net_delta": 0.0,
                "net_gamma": 0.0,
                "net_vega": 0.0,
                "net_theta": 0.0,
                "legs": 0,
            },
        )
        a["long_volume"] += lg.long_volume
        a["short_volume"] += lg.short_volume
        a["y_long"] += lg.y_long
        a["y_short"] += lg.y_short
        a["t_long"] += lg.t_long
        a["t_short"] += lg.t_short
        a["net_delta"] += lg.delta
        a["net_gamma"] += lg.gamma
        a["net_vega"] += lg.vega
        a["net_theta"] += lg.theta
        a["legs"] += 1
        if lg.option_type == "CALL":
            a["call_long"] += lg.long_volume
            a["call_short"] += lg.short_volume
        else:
            a["put_long"] += lg.long_volume
            a["put_short"] += lg.short_volume

    # attach margin from book
    for u, legs in by_u_legs.items():
        if u in u_acc:
            u_acc[u]["margin"] = round(sum(lg.margin for lg in legs), 2)

    by_underlying: list[UnderlyingNetSummary] = []
    for u, a in sorted(u_acc.items()):
        net_vol = a["long_volume"] - a["short_volume"]
        nd = round(a["net_delta"], 6)
        by_underlying.append(
            UnderlyingNetSummary(
                underlying=a["underlying"],
                product=a["product"],
                F_est=a["F_est"],
                dte=a["dte"],
                long_volume=a["long_volume"],
                short_volume=a["short_volume"],
                net_volume=net_vol,
                call_long=a["call_long"],
                call_short=a["call_short"],
                put_long=a["put_long"],
                put_short=a["put_short"],
                y_long=a["y_long"],
                y_short=a["y_short"],
                t_long=a["t_long"],
                t_short=a["t_short"],
                margin=a["margin"],
                net_delta=nd,
                net_gamma=round(a["net_gamma"], 8),
                net_vega=round(a["net_vega"], 4),
                net_theta=round(a["net_theta"], 4),
                risk_status=_risk_status(nd, delta_tilt),
                legs=a["legs"],
            )
        )

    # product rollup
    p_acc: dict[str, dict[str, Any]] = {}
    for u in by_underlying:
        p = p_acc.setdefault(
            u.product,
            {
                "product": u.product,
                "underlyings": [],
                "long_volume": 0,
                "short_volume": 0,
                "margin": 0.0,
                "net_delta": 0.0,
                "net_gamma": 0.0,
                "net_vega": 0.0,
                "net_theta": 0.0,
            },
        )
        p["underlyings"].append(u.underlying)
        p["long_volume"] += u.long_volume
        p["short_volume"] += u.short_volume
        p["margin"] += u.margin
        p["net_delta"] += u.net_delta
        p["net_gamma"] += u.net_gamma
        p["net_vega"] += u.net_vega
        p["net_theta"] += u.net_theta

    by_product: list[ProductNetSummary] = []
    for prod, p in sorted(p_acc.items()):
        nd = round(p["net_delta"], 6)
        by_product.append(
            ProductNetSummary(
                product=prod,
                underlyings=p["underlyings"],
                long_volume=p["long_volume"],
                short_volume=p["short_volume"],
                net_volume=p["long_volume"] - p["short_volume"],
                margin=round(p["margin"], 2),
                net_delta=nd,
                net_gamma=round(p["net_gamma"], 8),
                net_vega=round(p["net_vega"], 4),
                net_theta=round(p["net_theta"], 4),
                risk_status=_risk_status(nd, delta_tilt),
            )
        )

    alerts: list[str] = []
    for u in by_underlying:
        if u.risk_status != "SAFE":
            alerts.append(
                f"{u.underlying} 净Δ={u.net_delta:.3f} ({u.risk_status}) — 昨仓短{u.y_short}/长{u.y_long} "
                f"+ 今开短{u.t_short}/长{u.t_long}"
            )

    return GreeksBookReport(
        asof=asof_d.isoformat(),
        r=r,
        leg_greeks=leg_greeks,
        by_underlying=by_underlying,
        by_product=by_product,
        total_net_delta=round(sum(u.net_delta for u in by_underlying), 6),
        total_net_gamma=round(sum(u.net_gamma for u in by_underlying), 8),
        total_net_vega=round(sum(u.net_vega for u in by_underlying), 4),
        total_net_theta=round(sum(u.net_theta for u in by_underlying), 4),
        total_long_volume=sum(u.long_volume for u in by_underlying),
        total_short_volume=sum(u.short_volume for u in by_underlying),
        alerts=alerts,
    )
