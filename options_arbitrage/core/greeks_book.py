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


def _nth_trading_day_of_month(year: int, month: int, n: int) -> date:
    from datetime import timedelta

    d = date(year, month, 1)
    found = 0
    while d.month == month:
        if d.weekday() < 5:
            found += 1
            if found == n:
                return d
        d += timedelta(days=1)
    return date(year, month, 28)


def _nth_last_trading_day_on_or_before(end: date, n: int) -> date:
    """倒数第 n 个交易日（含 end 当日，若为交易日）。"""
    from datetime import timedelta

    days: list[date] = []
    d = date(end.year, end.month, 1)
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    if len(days) < n:
        return days[0] if days else end
    return days[-n]


def _trading_days_inclusive(start: date, end: date) -> int:
    from datetime import timedelta

    if end < start:
        return 1
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return max(n, 1)


def estimate_dte_from_underlying(underlying: str, asof: Optional[date] = None) -> int:
    """
    Option DTE aligned with exchange last-trading-day conventions (weekends only)
    and Libra 数据总览 days_to_expiry:

    - DCE/SHFE/INE/GFEX: 合约月份前一个月的第 5 个交易日
      EG2610 on 2026-08-13 → calendar DTE 25 (matches Libra)
    - CZCE 苹果等（交割月前两个月月末倒数第 3 个交易日）:
      AP610 on 2026-08-13 → expiry 2026-08-27 → trading-day DTE 11 (matches Libra)
    - 其他 CZCE: 合约月份前一个月的第 3 个交易日（日历日 DTE）
    """
    from calendar import monthrange

    asof = asof or date.today()
    m = re.search(r"(\d{3,4})$", underlying.strip())
    if not m:
        return 35
    code = m.group(1)
    try:
        if len(code) == 3:
            yy = 2020 + int(code[0])
            mm = int(code[1:])
        else:
            yy = 2000 + int(code[:2])
            mm = int(code[2:])
        if mm < 1 or mm > 12:
            return 35

        prod = product_code_from_underlying(underlying).upper()
        # 郑商所：交割月份前两个月最后一个日历日之前（含）的倒数第 3 个交易日
        # （鲜苹果期权等；Libra AP DTE 用交易日计数）
        czce_two_month_back3 = {"AP", "CJ", "PK"}
        czce_front_month_3 = {
            "SR", "CF", "TA", "MA", "RM", "OI", "FG", "SA", "UR", "PF", "SH", "PX", "PR",
        }

        if prod in czce_two_month_back3:
            # 交割月往前两个月
            em, ey = mm - 2, yy
            while em <= 0:
                em += 12
                ey -= 1
            end = date(ey, em, monthrange(ey, em)[1])
            expiry = _nth_last_trading_day_on_or_before(end, 3)
            return _trading_days_inclusive(asof, expiry)

        if prod in czce_front_month_3:
            if mm == 1:
                ey, em = yy - 1, 12
            else:
                ey, em = yy, mm - 1
            expiry = _nth_trading_day_of_month(ey, em, 3)
            return max((expiry - asof).days, 1)

        # DCE / SHFE / INE / GFEX 等：前月第 5 个交易日，日历日 DTE
        if mm == 1:
            ey, em = yy - 1, 12
        else:
            ey, em = yy, mm - 1
        expiry = _nth_trading_day_of_month(ey, em, 5)
        return max((expiry - asof).days, 1)
    except ValueError:
        return 35


# Libra-aligned year fraction & rate (night-session cross-check 2026-08-13)
TRADING_DAYS_PER_YEAR = 245.0
DEFAULT_R = 0.0


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
    delta: float  # Libra: 单位Δ × 净手数（不乘乘数）
    gamma: float  # Libra: 单位Γ × 净手数
    vega: float  # Libra: (∂V/∂σ)_小数 × 净手数（不乘乘数）
    theta: float  # Libra: 年化Θ × 净手数（不乘乘数）
    unit_delta: float
    unit_gamma: float
    unit_vega: float  # per 1% IV (engine native)
    unit_theta: float  # daily (engine native)
    multiplier: float
    y_long: int = 0
    y_short: int = 0
    t_long: int = 0
    t_short: int = 0
    cash_vega_1pct: float = 0.0  # 权利金现金 / 1% IV（×乘数）
    cash_theta_daily: float = 0.0  # 权利金现金 / 日（×乘数）

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
    net_delta: float  # 期货张数 = 汇总delta / 乘数
    net_delta_value: float  # 汇总delta点值
    net_gamma: float
    net_vega: float
    net_theta: float
    risk_status: str
    legs: int
    price_basis: str = "close_or_mark"

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
    r: float = DEFAULT_R,
    delta_tilt: float = 0.30,
    year_days: float = TRADING_DAYS_PER_YEAR,
) -> GreeksBookReport:
    """
    Merge 昨仓+今成交 → net book, then BS76 Greeks per leg / underlying / product.

    口径对齐 Libra 数据总览（夜盘核对）:
      T=DTE/245, r=0;
      delta/gamma/vega/theta 均不乘合约乘数；vega 按 σ 小数；theta 为年化。
    """
    underlying_F = underlying_F or {}
    asof_d = datetime.strptime(asof, "%Y-%m-%d").date() if asof else date.today()
    book = build_book(yesterday_positions, today_trades)

    # optional per-underlying DTE override via marks __DTE__:UNDERLYING
    dte_override: dict[str, int] = {}
    for k, v in list(marks.items()):
        if str(k).startswith("__DTE__:"):
            dte_override[k.split(":", 1)[1]] = max(int(float(v)), 1)

    # group legs by underlying for F estimate
    by_u_legs: dict[str, list[PositionLegState]] = {}
    for leg in book.values():
        by_u_legs.setdefault(leg.underlying, []).append(leg)

    F_map: dict[str, float] = {}
    dte_map: dict[str, int] = {}
    for u, legs in by_u_legs.items():
        F_map[u] = float(underlying_F.get(u) or estimate_F_from_book(legs))
        dte_map[u] = int(dte_override.get(u) or estimate_dte_from_underlying(u, asof_d))

    leg_greeks: list[LegGreeks] = []
    for sym, leg in sorted(book.items()):
        if leg.long_volume == 0 and leg.short_volume == 0:
            continue
        mark = float(marks.get(sym, leg.ref_settle or leg.short_cost or leg.long_cost or 0))
        F = F_map.get(leg.underlying, 0.0)
        dte = dte_map.get(leg.underlying, 35)
        T = dte / float(year_days)
        opt = leg.option_type if leg.option_type in ("CALL", "PUT") else "CALL"
        iv = _solve_iv(mark, F, leg.strike, T, r, opt)
        try:
            g = black76_greeks(F, leg.strike, T, r, iv, opt)  # type: ignore[arg-type]
        except Exception:
            continue
        # signed lots: long +, short −
        signed = leg.long_volume - leg.short_volume
        mult = leg.multiplier
        # Libra 数据总览：不乘乘数
        delta_lots = g.delta * signed
        gamma_lots = g.gamma * signed
        vega_libra = (g.vega / 0.01) * signed  # per 1.0 vol
        theta_libra = (g.theta * 365.0) * signed  # annualized
        # cash (with multiplier) for stress / money PnL
        cash_vega = g.vega * signed * mult
        cash_theta = g.theta * signed * mult
        delta_value = g.delta * signed * mult
        gamma_value = g.gamma * signed * mult
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
                delta=round(delta_lots, 6),
                gamma=round(gamma_lots, 8),
                vega=round(vega_libra, 4),
                theta=round(theta_libra, 4),
                unit_delta=round(g.delta, 6),
                unit_gamma=round(g.gamma, 8),
                unit_vega=round(g.vega, 6),
                unit_theta=round(g.theta, 6),
                multiplier=mult,
                y_long=leg.y_long,
                y_short=leg.y_short,
                t_long=leg.t_long,
                t_short=leg.t_short,
                cash_vega_1pct=round(cash_vega, 4),
                cash_theta_daily=round(cash_theta, 4),
            )
        )
        leg_greeks[-1].__dict__["_delta_value"] = delta_value
        leg_greeks[-1].__dict__["_gamma_value"] = gamma_value

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
                "net_delta": 0.0,  # 期货张数
                "net_delta_value": 0.0,  # 汇总delta点值 = 张数×乘数
                "net_gamma": 0.0,
                "net_vega": 0.0,
                "net_theta": 0.0,
                "legs": 0,
                "price_basis": "close_or_mark",
            },
        )
        a["long_volume"] += lg.long_volume
        a["short_volume"] += lg.short_volume
        a["y_long"] += lg.y_long
        a["y_short"] += lg.y_short
        a["t_long"] += lg.t_long
        a["t_short"] += lg.t_short
        a["net_delta"] += lg.delta
        a["net_delta_value"] += float(lg.__dict__.get("_delta_value") or lg.delta * lg.multiplier)
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
                net_delta_value=round(a.get("net_delta_value", nd * 10), 4),
                net_gamma=round(a["net_gamma"], 8),
                net_vega=round(a["net_vega"], 4),
                net_theta=round(a["net_theta"], 4),
                risk_status=_risk_status(nd, delta_tilt),
                legs=a["legs"],
                price_basis=a.get("price_basis", "close_or_mark"),
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
