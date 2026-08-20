"""Next-session short strangle screener with data gates (v2.0.0)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd

from core.bs76_engine import black76_greeks, implied_volatility
from core.data_gates import (
    GateSummary,
    check_account_equity,
    check_bid_ask_leg,
    check_client_margin_known,
    check_events_loaded,
    check_iv_history,
    check_iv_solved,
    check_mapping,
    check_quote_freshness,
    check_rules_meta,
)
from core.events import filter_product_events, upcoming_events
from core.exchange_rules import compute_margin_breakdown, load_rules_meta
from core.iv_surface import (
    TenorIVPoint,
    atm_iv_from_chain,
    compute_iv_rank_percentile,
    interpolate_fixed_tenor_iv,
    iv_25delta_skew,
)
from core.metrics import hv30, pop_lognormal, pop_approx
from core.technicals import evaluate_ranging_regime
from data_fetcher.v2_fetcher import ProductSnapshotV2, V2MarketFetcher

Classification = Literal["推荐", "观察", "排除"]
METHODS_VERSION = "methods-v2.0.0"


@dataclass
class LegQuote:
    symbol: str
    strike: float
    bid: float
    ask: float
    mid: float
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    oi: Optional[float]
    slippage: float


@dataclass
class CandidateResult:
    product: str
    product_name: str
    exchange: str
    option_month: str
    underlying_futures: str
    quote_date: str
    quote_timestamp: str
    target_session: str
    dte: int
    F: float
    classification: Classification
    classification_reasons: list[str]
    gates: list[dict[str, Any]]
    # vol
    sigma_star: Optional[float]
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    iv_history_n: int
    hv30: Optional[float]
    vrp: Optional[float]
    skew25: Optional[float]
    # legs
    call: Optional[LegQuote]
    put: Optional[LegQuote]
    net_delta: Optional[float]
    # margin
    margin: Optional[dict[str, Any]]
    # prob
    breakeven_low: Optional[float]
    breakeven_high: Optional[float]
    pop_risk_neutral: Optional[float]
    pop_delta_approx: Optional[float]
    hist_breach_rate: Optional[float]
    # technical
    technical_score: Optional[float]
    technical_detail: Optional[dict[str, Any]]
    # events
    events: list[str] = field(default_factory=list)
    stress: dict[str, Any] = field(default_factory=dict)
    suggested_lots: Optional[int] = None
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def next_trading_day(d: date) -> date:
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


def _chain_dicts(snap: ProductSnapshotV2) -> list[dict[str, Any]]:
    return [
        {
            "strike": r.strike,
            "call_bid": r.call_bid,
            "call_ask": r.call_ask,
            "put_bid": r.put_bid,
            "put_ask": r.put_ask,
        }
        for r in snap.chain
    ]


def _invert_iv(bid: float, ask: float, F: float, K: float, T: float, opt: str, r: float = 0.02) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = 0.5 * (bid + ask)
    try:
        return implied_volatility(mid, F, K, T, r, opt.upper())  # type: ignore[arg-type]
    except Exception:
        return None


def _pick_delta_legs(
    snap: ProductSnapshotV2,
    *,
    T: float,
    delta_min: float = 0.15,
    delta_max: float = 0.20,
    r: float = 0.02,
) -> tuple[Optional[LegQuote], Optional[LegQuote]]:
    calls: list[LegQuote] = []
    puts: list[LegQuote] = []
    for row in snap.chain:
        K = row.strike
        if row.call_bid and row.call_ask:
            iv = _invert_iv(row.call_bid, row.call_ask, snap.F, K, T, "CALL", r)
            if iv is None:
                continue
            g = black76_greeks(snap.F, K, T, r, iv, "CALL")
            mid = 0.5 * (row.call_bid + row.call_ask)
            calls.append(
                LegQuote(
                    row.call_symbol, K, row.call_bid, row.call_ask, mid, iv,
                    g.delta, g.gamma, g.theta, g.vega, row.call_oi, mid - row.call_bid,
                )
            )
        if row.put_bid and row.put_ask:
            iv = _invert_iv(row.put_bid, row.put_ask, snap.F, K, T, "PUT", r)
            if iv is None:
                continue
            g = black76_greeks(snap.F, K, T, r, iv, "PUT")
            mid = 0.5 * (row.put_bid + row.put_ask)
            puts.append(
                LegQuote(
                    row.put_symbol, K, row.put_bid, row.put_ask, mid, iv,
                    g.delta, g.gamma, g.theta, g.vega, row.put_oi, mid - row.put_bid,
                )
            )
    if not calls or not puts:
        return None, None

    def pick(pool: list[LegQuote], target: float) -> LegQuote:
        band = [x for x in pool if delta_min <= abs(x.delta or 0) <= delta_max]
        use = band or pool
        return min(use, key=lambda x: (abs(abs(x.delta or 0) - abs(target)), -((x.oi or 0))))

    return pick(calls, 0.175), pick(puts, 0.175)


def _hist_breach_rate(closes: pd.Series, k_low: float, k_high: float, window: int = 60) -> Optional[float]:
    if len(closes) < window:
        return None
    sample = closes.iloc[-window:]
    outside = ((sample < k_low) | (sample > k_high)).mean()
    return float(outside)


def _build_iv_history_proxy_from_hv(closes: list[float], sigma_star: float) -> tuple[list[float], str]:
    """Fallback history — NOT valid for 推荐 gate; marked as proxy."""
    arr = np.asarray(closes, dtype=float)
    if len(arr) < 40:
        return [], "unavailable"
    rets = np.diff(np.log(arr))
    series: list[float] = []
    w = 30
    for i in range(w, len(rets) + 1):
        s = rets[i - w : i]
        var = float(np.sum((s - s.mean()) ** 2) / (w - 1))
        series.append(math.sqrt(252 * var))
    scale = sigma_star / max(series[-1], 1e-6)
    return [v * scale for v in series[-252:]], "hv_scaled_proxy_not_for_ivr_gate"


def evaluate_product(
    snap: ProductSnapshotV2,
    *,
    as_of: date,
    target_tenor_days: int = 30,
    account_equity: Optional[float] = None,
    client_margin_addon: Optional[float] = None,
    min_volume: float = 500,
    min_oi: float = 1000,
    iv_rank_min: float = 60,
    ivp_min: float = 70,
    dte_min: int = 30,
    dte_max: int = 60,
    tech_exclude_below: float = 35,
    tech_watch_below: float = 55,
) -> CandidateResult:
    reasons: list[str] = []
    gates = GateSummary()
    meta = load_rules_meta()
    rules_version = meta.get("rules_version")
    verified_date = meta.get("verified_date")

    gates.results.append(check_quote_freshness(snap.quote_date, as_of, snap.quote_timestamp))
    gates.results.append(check_mapping(snap.option_month, snap.underlying_futures, snap.multiplier, snap.tick_size))
    gates.results.append(check_rules_meta(rules_version, verified_date))
    ev_all = upcoming_events(as_of=as_of)
    gates.results.append(
        check_events_loaded("static_calendar_v2", verified_date or "unknown", len(ev_all))
    )
    gates.results.append(check_client_margin_known(client_margin_addon))
    gates.results.append(check_account_equity(account_equity))

    T = max(snap.dte, 1) / 365.0
    chain_rows = _chain_dicts(snap)

    # Fixed tenor IV (current)
    atm_iv, _ = atm_iv_from_chain(chain_rows, snap.F, T)
    tenor_pt = TenorIVPoint(snap.option_month, snap.dte, T, atm_iv or float("nan"), snap.F, "chain_atm")
    fixed = interpolate_fixed_tenor_iv([tenor_pt], target_tenor_days)
    sigma_star = fixed.sigma_star if atm_iv is not None else None

    # IV history — proxy only; gate fails for 推荐
    hist, hist_src = _build_iv_history_proxy_from_hv(snap.futures_ohlc["close"].tolist(), sigma_star or 0.2)
    ivr, ivp, n_hist = (None, None, 0)
    if sigma_star is not None and hist:
        ivr, ivp, n_hist = compute_iv_rank_percentile(sigma_star, hist)
    gates.results.append(check_iv_history(n_hist, 252))

    try:
        hv = hv30(snap.futures_ohlc["close"].tolist())
    except ValueError:
        hv = None
    vrp = (sigma_star - hv) if sigma_star is not None and hv is not None else None
    skew = iv_25delta_skew(chain_rows, snap.F, T) if chain_rows else None

    highs = snap.futures_ohlc["high"].tolist()
    lows = snap.futures_ohlc["low"].tolist()
    tech = evaluate_ranging_regime(snap.futures_ohlc["close"].tolist(), highs=highs, lows=lows)

    call, put = _pick_delta_legs(snap, T=T)
    if call is None or put is None:
        gates.results.append(check_bid_ask_leg("call", None, None))
        gates.results.append(check_bid_ask_leg("put", None, None))
        return CandidateResult(
            product=snap.product,
            product_name=snap.product_name,
            exchange=snap.exchange,
            option_month=snap.option_month,
            underlying_futures=snap.underlying_futures,
            quote_date=snap.quote_date.isoformat(),
            quote_timestamp=snap.quote_timestamp.isoformat(),
            target_session=next_trading_day(as_of).isoformat(),
            dte=snap.dte,
            F=snap.F,
            classification="排除",
            classification_reasons=["无法匹配 Delta 0.15–0.20 且 IV 可反解的双边报价腿"],
            gates=gates.to_dict(),
            sigma_star=sigma_star,
            iv_rank=ivr,
            iv_percentile=ivp,
            iv_history_n=n_hist,
            hv30=hv,
            vrp=vrp,
            skew25=skew,
            call=call,
            put=put,
            net_delta=None,
            margin=None,
            breakeven_low=None,
            breakeven_high=None,
            pop_risk_neutral=None,
            pop_delta_approx=None,
            hist_breach_rate=None,
            technical_score=tech.score,
            technical_detail={"adx": tech.adx, "bb_pct_b": tech.bb_pct_b, "reasons": list(tech.reasons)},
            trace={"methods_version": METHODS_VERSION, "iv_history_source": hist_src, "data_source": snap.data_source},
        )

    gates.results.append(check_bid_ask_leg("call", call.bid, call.ask))
    gates.results.append(check_bid_ask_leg("put", put.bid, put.ask))
    gates.results.append(check_iv_solved(call.iv, "call"))
    gates.results.append(check_iv_solved(put.iv, "put"))

    net_delta = -(call.delta or 0) - (put.delta or 0)
    margin_bd = compute_margin_breakdown(
        snap.F, call.strike, put.strike, call.bid, put.bid,
        product=snap.product, exchange=snap.exchange, multiplier=snap.multiplier,
        client_margin_addon=client_margin_addon,
    )
    be_low = put.strike - (call.bid + put.bid)
    be_high = call.strike + (call.bid + put.bid)
    pop_rn = pop_lognormal(snap.F, put.strike, call.strike, T, hv or sigma_star or 0.2) if hv or sigma_star else None
    pop_d = pop_approx(call.delta or 0, put.delta or 0)
    breach = _hist_breach_rate(snap.futures_ohlc["close"], put.strike, call.strike)

    ev = filter_product_events(snap.product, as_of=as_of, exclude_events=False)
    event_notes = list(ev.notes)

    # Hard excludes
    classification: Classification = "观察"
    if snap.dte < dte_min or snap.dte > 90:
        classification = "排除"
        reasons.append(f"DTE={snap.dte} 不在 {dte_min}–{dte_max} 优选窗口")
    if tech.score <= tech_exclude_below:
        classification = "排除"
        reasons.append(f"技术面评分 {tech.score:.0f} ≤ {tech_exclude_below}")
    if call.iv is None or put.iv is None:
        classification = "排除"
        reasons.append("IV 反解失败")
    if ev.blocked:
        classification = "排除"
        reasons.append("事件窗口高风险")

    # Strategy thresholds
    iv_ok = (ivr is not None and ivr >= iv_rank_min) or (ivp is not None and ivp >= ivp_min)
    if not iv_ok and sigma_star is not None:
        reasons.append(f"IVR/IVP 未达标 (IVR={ivr}, IVP={ivp})")
    if vrp is not None and vrp <= 0:
        reasons.append(f"VRP≤0 ({vrp:.3f})")
    if (call.oi or 0) < min_oi or (put.oi or 0) < min_oi:
        reasons.append(f"单腿持仓量不足 Call={call.oi} Put={put.oi}")

    prem_ratio = margin_bd.premium_bid_cash / margin_bd.no_combo_total if margin_bd.no_combo_total > 0 else 0
    if prem_ratio < 0.08:
        reasons.append(f"权/保比(无优惠) {prem_ratio:.1%} < 8%")

    stress = {
        "margin_iv_up50_no_combo": margin_bd.stress_no_combo,
        "margin_iv_up50_combo": margin_bd.stress_iv_up50,
        "underlying_up3pct_note": "需入场前重算",
        "underlying_down3pct_note": "需入场前重算",
        "combo_fail_use_no_combo": margin_bd.no_combo_total,
    }

    # Final classification
    hard_fails = [g for g in gates.results if not g.passed and g.severity == "hard"]
    if classification != "排除":
        if not hard_fails and iv_ok and (vrp or 0) > 0 and tech.score >= tech_watch_below and not ev.blocked:
            if client_margin_addon is not None and account_equity is not None and n_hist >= 252:
                classification = "推荐"
                reasons.append("全部硬闸门通过且评分达标")
            else:
                classification = "观察"
                if client_margin_addon is None:
                    reasons.append("缺少客户保证金加收比例")
                if account_equity is None:
                    reasons.append("缺少账户权益")
                if n_hist < 252:
                    reasons.append(f"IV 历史不足({n_hist}/252)，IVR/IVP 不可用于推荐")
        else:
            classification = "观察"
            if hard_fails:
                reasons.append("数据闸门未全部通过: " + "; ".join(g.name for g in hard_fails))

    suggested = None
    if account_equity and classification == "推荐":
        budget = account_equity * 0.30
        unit = margin_bd.client_estimated or margin_bd.no_combo_total
        suggested = int(budget // unit) if unit > 0 else 0

    return CandidateResult(
        product=snap.product,
        product_name=snap.product_name,
        exchange=snap.exchange,
        option_month=snap.option_month,
        underlying_futures=snap.underlying_futures,
        quote_date=snap.quote_date.isoformat(),
        quote_timestamp=snap.quote_timestamp.isoformat(),
        target_session=next_trading_day(as_of).isoformat(),
        dte=snap.dte,
        F=snap.F,
        classification=classification,
        classification_reasons=reasons,
        gates=gates.to_dict(),
        sigma_star=sigma_star,
        iv_rank=ivr,
        iv_percentile=ivp,
        iv_history_n=n_hist,
        hv30=hv,
        vrp=vrp,
        skew25=skew,
        call=call,
        put=put,
        net_delta=net_delta,
        margin={
            "call_exchange": margin_bd.call_exchange,
            "put_exchange": margin_bd.put_exchange,
            "no_combo_total": margin_bd.no_combo_total,
            "combo_theoretical": margin_bd.combo_theoretical,
            "combo_status": margin_bd.combo_status,
            "client_estimated": margin_bd.client_estimated,
            "premium_bid_cash": margin_bd.premium_bid_cash,
            "premium_margin_ratio_no_combo": prem_ratio,
            "rules_version": margin_bd.rules_version,
            "verified_date": margin_bd.verified_date,
        },
        breakeven_low=be_low,
        breakeven_high=be_high,
        pop_risk_neutral=pop_rn,
        pop_delta_approx=pop_d,
        hist_breach_rate=breach,
        technical_score=tech.score,
        technical_detail={"adx": tech.adx, "bb_pct_b": tech.bb_pct_b, "reasons": list(tech.reasons)},
        events=event_notes,
        stress=stress,
        suggested_lots=suggested,
        trace={
            "methods_version": METHODS_VERSION,
            "iv_history_source": hist_src,
            "data_source": snap.data_source,
            "american_risk_flag": True,
            "premium_uses_bid": True,
        },
    )


def run_next_session_scan(
    *,
    as_of: Optional[date] = None,
    account_equity: Optional[float] = None,
    client_margin_addon: Optional[float] = None,
    dte_min: int = 30,
    dte_max: int = 60,
) -> tuple[list[CandidateResult], dict[str, Any]]:
    as_of = as_of or date.today()
    fetcher = V2MarketFetcher()
    snaps, manifest = fetcher.fetch_all(dte_min=dte_min, dte_max=dte_max, as_of=as_of)
    results = [
        evaluate_product(
            s,
            as_of=as_of,
            account_equity=account_equity,
            client_margin_addon=client_margin_addon,
            dte_min=dte_min,
            dte_max=dte_max,
        )
        for s in snaps
    ]
    meta = {
        "report_version": "report-v2.0.0",
        "methods_version": METHODS_VERSION,
        "rules_version": load_rules_meta().get("rules_version"),
        "quote_asof": manifest.quote_asof,
        "target_session": next_trading_day(as_of).isoformat(),
        "data_source": manifest.data_source,
        "model": "Black-76 (American risk flagged)",
        "account_equity": account_equity,
        "fetch": asdict(manifest),
        "counts": {
            "scanned": len(results),
            "推荐": sum(1 for r in results if r.classification == "推荐"),
            "观察": sum(1 for r in results if r.classification == "观察"),
            "排除": sum(1 for r in results if r.classification == "排除"),
        },
    }
    order = {"推荐": 0, "观察": 1, "排除": 2}
    results.sort(key=lambda r: (order[r.classification], -(r.vrp or -999), -(r.technical_score or 0)))
    return results, meta
