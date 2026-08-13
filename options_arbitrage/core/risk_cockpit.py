"""Risk cockpit aggregations matching the user's Excel 风控台 layout.

套利策略损益 = 昨日持仓损益 + 今日成交损益（只按方向计，不冲抵）。
底层希腊值 / IV / 理论价一律用 BS76。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.greeks_book import GreeksBookReport, compute_net_positions_and_greeks
from core.pnl_engine import compute_live_pnl
from core.settlement_parser import lookup_multiplier, product_code_from_underlying


@dataclass
class FuturesTrade:
    symbol: str  # e.g. JD2610
    side: str  # BUY / SELL
    volume: int
    price: float
    last: float
    multiplier: float
    fee: float = 0.0

    @property
    def signed_lots(self) -> int:
        return self.volume if self.side == "BUY" else -self.volume

    @property
    def pnl(self) -> float:
        # 方向计：买 +(last-px)*q*m；卖 -(last-px)*q*m
        sign = 1 if self.side == "BUY" else -1
        return sign * (self.last - self.price) * self.volume * self.multiplier - self.fee


def futures_pnl(trades: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    total = 0.0
    for t in trades:
        sym = str(t["symbol"])
        product = product_code_from_underlying(sym)
        mult = float(t.get("multiplier") or lookup_multiplier(sym))
        side = str(t.get("side") or "").upper()
        if side in {"买", "BUY", "B", "LONG"} or "买" in str(t.get("side")):
            side = "BUY"
        else:
            side = "SELL"
        ft = FuturesTrade(
            symbol=sym,
            side=side,
            volume=int(t.get("volume") or 0),
            price=float(t.get("price") or 0),
            last=float(t.get("last") or t.get("mark") or t.get("price") or 0),
            multiplier=mult,
            fee=float(t.get("fee") or 0),
        )
        pnl = ft.pnl
        total += pnl
        rows.append(
            {
                "symbol": sym,
                "product": product,
                "side": side,
                "volume": ft.volume,
                "price": ft.price,
                "last": ft.last,
                "multiplier": mult,
                "pnl": round(pnl, 2),
            }
        )
    return round(total, 2), rows


def _stress_components(greeks: GreeksBookReport, shock: float = 0.05, iv_shock_pts: float = 5.0) -> dict[str, float]:
    """
    Stress test on ±shock underlying move (take worse) + IV shock in vega points.
    Cash PnL approximations:
      delta_cash(dF) = net_delta * dF * mult_equiv
    We store per-underlying net_delta as sum(unit_delta*lots); cash = delta * dF * avg_mult
    Better: use leg-level.
    """
    up = 0.0
    down = 0.0
    gamma_cash = 0.0
    vega_cash = 0.0
    theta_cash = greeks.total_net_theta

    # rebuild from legs for accuracy
    for lg in greeks.leg_greeks:
        dF_up = shock * lg.F
        dF_dn = -shock * lg.F
        up += lg.delta * dF_up * lg.multiplier
        down += lg.delta * dF_dn * lg.multiplier
        # gamma same for ± (quadratic)
        gamma_cash += 0.5 * lg.gamma * (dF_up**2) * lg.multiplier
        vega_cash += lg.vega * iv_shock_pts  # lg.vega already cash per 1%

    # short gamma book: gamma_cash typically negative for short options when using signed gamma
    delta_worse = min(up, down)
    # For stress "loss" presentation: show component contributions at the worse delta scenario
    # Gamma/vega/theta as structural risk
    total = delta_worse + gamma_cash + theta_cash + vega_cash
    return {
        "delta": round(delta_worse, 2),
        "gamma": round(gamma_cash, 2),
        "theta": round(theta_cash, 2),
        "vega": round(vega_cash, 2),
        "alpha": 0.0,
        "total": round(total, 2),
        "shock_pct": shock * 100,
        "iv_shock_pts": iv_shock_pts,
    }


def _pnl_attribution(
    greeks: GreeksBookReport,
    option_pnl: float,
    underlying_dF: Optional[dict[str, float]] = None,
) -> dict[str, float]:
    """
    Approximate day's PnL attribution.
    If dF provided per underlying, delta/gamma use it; else scale delta/gamma to residual after theta.
    """
    underlying_dF = underlying_dF or {}
    theta_attr = greeks.total_net_theta
    delta_attr = 0.0
    gamma_attr = 0.0
    vega_attr = 0.0

    # group legs by underlying
    from collections import defaultdict

    by_u: dict[str, list] = defaultdict(list)
    for lg in greeks.leg_greeks:
        by_u[lg.underlying].append(lg)

    have_df = False
    for u, legs in by_u.items():
        dF = underlying_dF.get(u)
        if dF is None:
            continue
        have_df = True
        for lg in legs:
            delta_attr += lg.delta * dF * lg.multiplier
            gamma_attr += 0.5 * lg.gamma * (dF**2) * lg.multiplier

    if not have_df:
        # residual split: assign theta first, leftover to delta (proxy), small gamma/vega placeholders
        residual = option_pnl - theta_attr
        delta_attr = residual * 0.55
        gamma_attr = residual * 0.25
        vega_attr = residual * 0.20
    else:
        explained = delta_attr + gamma_attr + theta_attr
        vega_attr = option_pnl - explained

    total = delta_attr + gamma_attr + theta_attr + vega_attr
    return {
        "delta": round(delta_attr, 2),
        "gamma": round(gamma_attr, 2),
        "theta": round(theta_attr, 2),
        "vega": round(vega_attr, 2),
        "total": round(total, 2),
    }


def build_risk_cockpit(
    *,
    account_id: str,
    settlement_date: str,
    session_date: str,
    yesterday_positions: list[dict[str, Any]],
    today_option_trades: list[dict[str, Any]],
    marks: dict[str, float],
    opening_equity: float,
    margin_occupied: float,
    available: float,
    risk_degree: float,
    underlying_F: Optional[dict[str, float]] = None,
    futures_trades: Optional[list[dict[str, Any]]] = None,
    daily_profit_target: float = 660.0,
    max_product_margin_ratio: float = 0.12,
    stress_shock: float = 0.05,
) -> dict[str, Any]:
    """Assemble Excel-like 风控概览 payload."""
    pnl_report = compute_live_pnl(
        account_id=account_id,
        settlement_date=settlement_date,
        session_date=session_date,
        yesterday_positions=yesterday_positions,
        today_trades=today_option_trades,
        marks=marks,
        opening_equity=opening_equity,
        margin_occupied_settlement=margin_occupied,
        available_settlement=available,
        risk_degree_settlement=risk_degree,
    )
    greeks = compute_net_positions_and_greeks(
        yesterday_positions=yesterday_positions,
        today_trades=today_option_trades,
        marks=marks,
        underlying_F=underlying_F,
        asof=session_date,
    )

    yday_pnl = float(pnl_report.total_carry_pnl)
    today_pnl = float(pnl_report.total_today_trade_pnl)
    fees = float(pnl_report.total_fees)
    arb_pnl = float(pnl_report.total_pnl)  # 昨仓 + 今成交 - 费
    hedge_pnl, hedge_rows = futures_pnl(futures_trades or [])

    stress = _stress_components(greeks, shock=stress_shock)
    attribution = _pnl_attribution(greeks, arb_pnl)

    # delta notional: sum |net_delta| * F * representative multiplier
    delta_notional = 0.0
    pnl_by_u = {r["underlying"]: r for r in pnl_report.by_underlying}
    variety_rows: list[dict[str, Any]] = []
    for u in greeks.by_underlying:
        mult = 10.0
        for lg in greeks.leg_greeks:
            if lg.underlying == u.underlying:
                mult = lg.multiplier
                break
        delta_lots = u.net_delta  # 期货张数 = 汇总delta / 乘数
        delta_value = getattr(u, "net_delta_value", delta_lots * mult)
        delta_notional += abs(delta_lots) * u.F_est * mult
        u_pnl = pnl_by_u.get(u.underlying, {})
        variety_pnl = float(u_pnl.get("total_pnl") or 0.0)
        carry_u = float(u_pnl.get("carry_pnl") or 0.0)
        today_u = float(u_pnl.get("today_trade_pnl") or 0.0)
        est_pnl = round(u.net_theta, 2)
        variety_rows.append(
            {
                "合约": u.underlying,
                "品种": u.product,
                "汇总delta": round(delta_value, 2),
                "套利策略delta(张数)": round(delta_lots, 4),
                "持仓delta张数汇总": round(delta_lots, 4),
                "品种盈亏": round(variety_pnl, 2),
                "昨仓损益": round(carry_u, 2),
                "今成交损益": round(today_u, 2),
                "套利策略": round(variety_pnl, 2),
                "预估损益": est_pnl,
                "保证金": round(u.margin, 2),
                "净卖持仓": u.short_volume,
                "净买持仓": u.long_volume,
                "昨仓短": u.y_short,
                "今开短": u.t_short,
                "net_vega": u.net_vega,
                "net_theta": u.net_theta,
                "risk_status": u.risk_status,
                "F": u.F_est,
                "乘数": mult,
            }
        )

    margin_total = float(margin_occupied)
    margin_wan = round(margin_total / 10000.0, 2)
    prod_margins = {p.product: p.margin for p in greeks.by_product}
    max_ratio = 0.0
    max_prod = ""
    if margin_total > 0:
        for prod, m in prod_margins.items():
            ratio = m / margin_total
            if ratio > max_ratio:
                max_ratio = ratio
                max_prod = prod

    return {
        "account_id": account_id,
        "settlement_date": settlement_date,
        "session_date": session_date,
        "opening_equity": opening_equity,
        "available": available,
        "risk_degree": risk_degree,
        "model": "BS76",
        "methodology": {
            "pnl": (
                "套利策略损益=昨日持仓损益+今日成交损益（方向计，不冲抵）；"
                "昨仓: 方向×数量×(最新-昨收)×乘数；"
                "今成交: 方向×数量×(最新-成交价)×乘数"
            ),
            "price_basis": "昨仓基准优先昨收/close（无 close 时回退 settle）；盯市与希腊值用最新价",
            "delta_lots": "期货张数 = 汇总delta / 品种乘数；汇总delta = Σ(单位Δ × 净手数 × 乘数)",
            "delta_value": "汇总delta（点值）= Σ(单位Δ × 净手数 × 乘数)，即标的变动1点的权利金现金敏感度",
            "greeks_model": "BS76（Black-76）；IV 由期权最新价反推",
        },
        "风控概览": {
            "昨日持仓损益": round(yday_pnl, 2),
            "今日成交损益": round(today_pnl, 2),
            "手续费": round(fees, 2),
            "套利策略损益": round(arb_pnl, 2),
            "总盈亏": round(arb_pnl, 2),
            "对冲盈亏": round(hedge_pnl, 2),
            "综合盈亏_含对冲": round(arb_pnl + hedge_pnl, 2),
            "保证金合计": round(margin_total, 2),
            "保证金合计_万": margin_wan,
            "日均盈利目标": daily_profit_target,
            "距目标": round(arb_pnl - daily_profit_target, 2),
            "delta名义价值总额": round(delta_notional, 2),
            "品种保证金最大占比": round(max_ratio * 100, 2),
            "最大占比品种": max_prod,
            "配置上限占比": round(max_product_margin_ratio * 100, 2),
        },
        "希腊值风控": {
            "模型": "BS76",
            "组合净Δ_张数": greeks.total_net_delta,
            "组合净Δ": greeks.total_net_delta,
            "组合净Γ": greeks.total_net_gamma,
            "日Theta": greeks.total_net_theta,
            "Vega": greeks.total_net_vega,
        },
        "压力测试": stress,
        "盈亏归因": attribution,
        "分品种明细": variety_rows,
        "分品种净持仓": [p.to_dict() for p in greeks.by_product],
        "对冲明细": hedge_rows,
        "今日成交逐笔": pnl_report.by_trade,
        "greeks_summary": greeks.to_dict(),
        "pnl_report": pnl_report.to_dict(),
        "alerts": list(pnl_report.alerts) + list(greeks.alerts),
    }
