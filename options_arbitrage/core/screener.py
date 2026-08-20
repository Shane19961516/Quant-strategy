"""Market-wide short-strangle screener and contract pairing engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

from .bs76_engine import black76_greeks
from .capital_allocator import allocate_strangle, short_strangle_margin
from .events import filter_product_events
from .metrics import hv30, iv_hv_spread, iv_percentile, iv_rank, strangle_pop
from .technicals import TechnicalSnapshot, evaluate_ranging_regime


def _load_settings(path: Optional[str | Path] = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    option_type: str  # CALL / PUT
    strike: float
    dte: int
    expire_date: str
    iv: float
    premium: float
    F: float
    multiplier: float = 10.0
    exchange: str = ""
    product: str = ""
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    volume: float = 0.0
    open_interest: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0

    def ensure_greeks(self, r: float = 0.02) -> None:
        if self.delta is not None:
            return
        T = self.dte / 365.0
        g = black76_greeks(self.F, self.strike, T, r, self.iv, self.option_type.upper())  # type: ignore[arg-type]
        self.delta = g.delta
        self.gamma = g.gamma
        self.vega = g.vega
        self.theta = g.theta


@dataclass
class UnderlyingSnapshot:
    underlying: str
    F: float
    prices: Sequence[float]  # historical closes for HV
    iv_history: Sequence[float]  # historical ATM IV for IVR/IVP
    current_iv: float
    contracts: list[OptionContract] = field(default_factory=list)
    product: str = ""
    exchange: str = ""
    multiplier: float = 10.0
    highs: Optional[Sequence[float]] = None
    lows: Optional[Sequence[float]] = None
    product_name: str = ""
    option_month: str = ""
    iv_history_source: str = "provided"
    month_volume: float = 0.0  # aggregate option volume for month (if known)


@dataclass
class ShortStrangleCandidate:
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
    risk_flags: list[str] = field(default_factory=list)
    # Extended fields for skill report
    product: str = ""
    product_name: str = ""
    exchange: str = ""
    option_month: str = ""
    premium_cash: float = 0.0
    premium_margin_ratio: float = 0.0
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    call_gamma: float = 0.0
    put_gamma: float = 0.0
    call_theta: float = 0.0
    put_theta: float = 0.0
    call_vega: float = 0.0
    put_vega: float = 0.0
    adx: float = float("nan")
    bb_pct_b: float = float("nan")
    range_low_30: float = float("nan")
    range_high_30: float = float("nan")
    notes: str = ""
    light_size_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScreenReject:
    underlying: str
    product: str
    product_name: str
    stage: str
    reject_reason: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def pair_delta_strikes(
    contracts: Sequence[OptionContract],
    *,
    target_call_delta: float = 0.15,
    target_put_delta: float = -0.15,
    delta_min: float = 0.12,
    delta_max: float = 0.20,
    r: float = 0.02,
    prefer_liquid: bool = True,
) -> Optional[tuple[OptionContract, OptionContract]]:
    """Pick Call/Put with |Δ| in [delta_min, delta_max], preferring target / liquidity."""
    calls: list[OptionContract] = []
    puts: list[OptionContract] = []
    for c in contracts:
        c.ensure_greeks(r)
        if c.option_type.upper() == "CALL":
            calls.append(c)
        elif c.option_type.upper() == "PUT":
            puts.append(c)

    if not calls or not puts:
        return None

    def in_band(c: OptionContract, lo: float, hi: float) -> bool:
        d = abs(c.delta or 0.0)
        return lo - 1e-6 <= d <= hi + 1e-6

    call_band = [c for c in calls if in_band(c, delta_min, delta_max)]
    put_band = [c for c in puts if in_band(c, delta_min, delta_max)]
    call_pool = call_band or calls
    put_pool = put_band or puts

    def score(c: OptionContract, target: float) -> tuple[float, float, float]:
        # prefer closer to target; then farther OTM (lower |delta|); then liquidity
        liq = -((c.open_interest or 0.0) + (c.volume or 0.0)) if prefer_liquid else 0.0
        return (abs((c.delta or 0.0) - target), abs(c.delta or 0.0), liq)

    best_call = min(call_pool, key=lambda x: score(x, target_call_delta))
    best_put = min(put_pool, key=lambda x: score(x, target_put_delta))
    return best_call, best_put


def passes_hard_filters(
    dte: int,
    ivr: float,
    ivp: float,
    spread: float,
    *,
    dte_min: int = 30,
    dte_max: int = 60,
    ivr_min: float = 60.0,
    ivp_min: float = 70.0,
    iv_hv_spread_min: float = 0.05,
) -> bool:
    # Skill: IV Rank ≥ 60 OR IV Percentile ≥ 70, plus DTE / optional IV-HV
    iv_ok = ivr >= ivr_min or ivp >= ivp_min
    return dte_min <= dte <= dte_max and iv_ok and spread > iv_hv_spread_min


def check_liquidity(
    call: OptionContract,
    put: OptionContract,
    *,
    min_volume: float = 500.0,
    min_open_interest: float = 1000.0,
    month_volume: float = 0.0,
    max_spread_pct: float = 0.02,
    max_spread_abs: float = 5.0,
) -> tuple[bool, str]:
    """
    Skill step 3 liquidity gates.

    When per-contract volume is unavailable (common on Sina chain), fall back to
    open interest and optional month_volume aggregate.
    """
    call_oi = call.open_interest or 0.0
    put_oi = put.open_interest or 0.0
    call_vol = call.volume or 0.0
    put_vol = put.volume or 0.0

    if month_volume > 0 and month_volume < min_volume:
        return False, f"主力月份成交量 {month_volume:.0f} < {min_volume:.0f}"

    # Per-leg OI
    if call_oi < min_open_interest or put_oi < min_open_interest:
        # If demo/synthetic has zero OI, allow when both volume and OI are unset (legacy demo)
        if max(call_oi, put_oi, call_vol, put_vol, month_volume) > 0:
            return False, f"持仓量不足 Call OI={call_oi:.0f} Put OI={put_oi:.0f}"

    for leg, name in ((call, "Call"), (put, "Put")):
        if leg.spread > 0 and leg.premium > 0:
            pct = leg.spread / leg.premium
            if pct > max_spread_pct and leg.spread > max_spread_abs:
                return False, f"{name} 买卖价差过大 spread={leg.spread:.2f} ({pct:.1%})"
    return True, ""


def screen_underlying(
    snap: UnderlyingSnapshot,
    *,
    settings: Optional[dict[str, Any]] = None,
    current_margin_used: float = 0.0,
    require_ranging: bool = True,
    require_liquidity: bool = True,
    exclude_events: bool = True,
) -> tuple[Optional[ShortStrangleCandidate], Optional[ScreenReject]]:
    """Run full skill pipeline filters for one underlying."""
    cfg = settings or _load_settings()
    sc = cfg.get("screener", {})
    cap = cfg.get("capital", {})
    risk = cfg.get("risk", {})
    r = float(risk.get("risk_free_rate", 0.02))

    product = snap.product or snap.underlying.rstrip("0123456789")
    product_name = snap.product_name or product

    dte_min = int(sc.get("dte_min", 30))
    dte_max = int(sc.get("dte_max", 60))
    in_window = [c for c in snap.contracts if dte_min <= c.dte <= dte_max]
    effective_dte_max = dte_max
    if not in_window:
        # Skill step 6: fall back to 60–90 DTE when primary window empty
        in_window = [c for c in snap.contracts if dte_min <= c.dte <= 90]
        effective_dte_max = 90
    if not in_window:
        return None, ScreenReject(
            snap.underlying, product, product_name, "dte", "无落在 DTE 窗口的合约"
        )

    try:
        hv = hv30(snap.prices)
    except ValueError:
        return None, ScreenReject(snap.underlying, product, product_name, "hv", "历史价格不足以计算 HV30")

    ivr = iv_rank(snap.current_iv, snap.iv_history)
    ivp = iv_percentile(snap.current_iv, snap.iv_history)
    spread = iv_hv_spread(snap.current_iv, hv)
    dte = int(sorted({c.dte for c in in_window})[0])

    ivr_min = float(sc.get("ivr_min", sc.get("iv_rank_min", 60.0)))
    ivp_min = float(sc.get("ivp_min", sc.get("iv_percentile_min", 70.0)))
    if not passes_hard_filters(
        dte,
        ivr,
        ivp,
        spread,
        dte_min=dte_min,
        dte_max=effective_dte_max,
        ivr_min=ivr_min,
        ivp_min=ivp_min,
        iv_hv_spread_min=float(sc.get("iv_hv_spread_min", 0.05)),
    ):
        return None, ScreenReject(
            snap.underlying,
            product,
            product_name,
            "iv",
            f"IV/DTE 条件未满足 DTE={dte} IVR={ivr:.1f} IVP={ivp:.1f} IV-HV={spread:.3f}",
            {"iv_rank": ivr, "iv_percentile": ivp, "iv_hv_spread": spread, "hv30": hv, "current_iv": snap.current_iv},
        )

    # Technical regime
    tech: TechnicalSnapshot = evaluate_ranging_regime(
        snap.prices,
        highs=snap.highs,
        lows=snap.lows,
        adx_max=float(sc.get("adx_max", 20.0)),
        di_max=float(sc.get("di_max", 25.0)),
        bb_mid_tol=float(sc.get("bb_mid_tol", 0.35)),
        ma_slope_max=float(sc.get("ma_slope_max", 0.5)),
    )
    if require_ranging and not tech.is_ranging:
        return None, ScreenReject(
            snap.underlying,
            product,
            product_name,
            "technical",
            "；".join(tech.reasons) or "非震荡格局",
            {"iv_rank": ivr, "iv_percentile": ivp, "adx": tech.adx, "bb_pct_b": tech.bb_pct_b},
        )

    # Events
    ev = filter_product_events(
        product,
        exclude_events=exclude_events if sc.get("exclude_events", True) else False,
        horizon_days=int(sc.get("event_horizon_days", 5)),
    )
    if ev.blocked:
        return None, ScreenReject(
            snap.underlying,
            product,
            product_name,
            "event",
            "事件窗口: " + "；".join(ev.notes),
            {"iv_rank": ivr, "iv_percentile": ivp},
        )

    delta_min, delta_max = 0.12, 0.20
    dt = sc.get("delta_target", [0.12, 0.20])
    if isinstance(dt, (list, tuple)) and len(dt) == 2:
        delta_min, delta_max = float(dt[0]), float(dt[1])
    target_call = float(sc.get("target_call_delta", 0.15))
    target_put = float(sc.get("target_put_delta", -0.15))

    paired = pair_delta_strikes(
        in_window,
        target_call_delta=target_call,
        target_put_delta=target_put,
        delta_min=delta_min,
        delta_max=delta_max,
        r=r,
    )
    if paired is None:
        return None, ScreenReject(snap.underlying, product, product_name, "strike", "无法匹配 Delta 目标的 Call/Put")
    call, put = paired
    assert call.delta is not None and put.delta is not None
    call.ensure_greeks(r)
    put.ensure_greeks(r)

    if require_liquidity:
        ok_liq, liq_msg = check_liquidity(
            call,
            put,
            min_volume=float(sc.get("min_volume", 500)),
            min_open_interest=float(sc.get("min_open_interest", 1000)),
            month_volume=float(snap.month_volume or 0.0),
        )
        if not ok_liq:
            return None, ScreenReject(
                snap.underlying, product, product_name, "liquidity", liq_msg,
                {"iv_rank": ivr, "iv_percentile": ivp},
            )

    T = dte / 365.0
    # Skill step 8: estimate win rate under HV (lognormal), not ATM IV
    pop_sigma = max(hv, 1e-6)
    pop = strangle_pop(
        snap.F,
        put.strike,
        call.strike,
        T,
        pop_sigma,
        call.delta,
        put.delta,
        method="exact",
    )
    min_pop = float(sc.get("min_pop", 0.70))
    if pop < min_pop:
        return None, ScreenReject(
            snap.underlying, product, product_name, "pop",
            f"到期胜率 {pop:.1%} < {min_pop:.0%}",
            {"iv_rank": ivr, "pop": pop},
        )

    margin = short_strangle_margin(
        snap.F,
        call.strike,
        put.strike,
        call.premium,
        put.premium,
        multiplier=snap.multiplier,
    )
    prem_ratio = margin.total_premium_cash / margin.unit_margin if margin.unit_margin > 0 else 0.0
    min_ratio = float(sc.get("min_premium_margin_ratio", 0.08))
    if prem_ratio < min_ratio:
        return None, ScreenReject(
            snap.underlying, product, product_name, "premium_ratio",
            f"权利金/保证金比 {prem_ratio:.1%} < {min_ratio:.0%}",
            {"iv_rank": ivr, "premium_margin_ratio": prem_ratio},
        )

    alloc = allocate_strangle(
        snap.F,
        call.strike,
        put.strike,
        call.premium,
        put.premium,
        total_equity=float(cap.get("total_equity", 100_000)),
        max_allocation_per_symbol=float(cap.get("max_allocation_per_symbol", 0.30)),
        current_margin_used=current_margin_used,
        max_margin_usage=float(cap.get("max_margin_usage", 0.60)),
        product=product,
        exchange=snap.exchange or None,
        multiplier=snap.multiplier,
    )

    # Short strangle greeks: short both legs
    net_delta = -(call.delta or 0.0) - (put.delta or 0.0)
    net_gamma = -(call.gamma or 0.0) - (put.gamma or 0.0)
    # Short theta = - (long theta); long theta is typically negative => short theta positive
    net_theta = -(call.theta or 0.0) - (put.theta or 0.0)
    net_vega = -(call.vega or 0.0) - (put.vega or 0.0)

    flags: list[str] = []
    if alloc.blocked_by_margin_cap:
        flags.append("MARGIN_CAP_BLOCKED")
    if ev.light_size_only:
        flags.append("EVENT_LIGHT_SIZE")
    if abs(net_delta) > 0.10:
        flags.append("DELTA_NOT_NEUTRAL")
    if snap.iv_history_source == "hv_scaled_proxy":
        flags.append("IV_HISTORY_PROXY")

    notes_parts = list(flags)
    if ev.notes:
        notes_parts.extend(ev.notes)
    if not tech.is_ranging:
        notes_parts.append("技术面部分放宽")

    cand = ShortStrangleCandidate(
        underlying=snap.underlying,
        dte=dte,
        F=snap.F,
        iv_rank=ivr,
        iv_percentile=ivp,
        iv_hv_spread=spread,
        hv30=hv,
        current_iv=snap.current_iv,
        call_symbol=call.symbol,
        call_strike=call.strike,
        call_delta=call.delta,
        call_premium=call.premium,
        put_symbol=put.symbol,
        put_strike=put.strike,
        put_delta=put.delta,
        put_premium=put.premium,
        pop=pop,
        max_pairs=alloc.max_pairs,
        total_margin=alloc.total_margin,
        total_premium=alloc.total_premium,
        expected_roi=alloc.expected_roi,
        unit_margin=alloc.unit_margin,
        blocked_by_margin_cap=alloc.blocked_by_margin_cap,
        risk_flags=flags,
        product=product,
        product_name=product_name,
        exchange=snap.exchange,
        option_month=snap.option_month or snap.underlying,
        premium_cash=margin.total_premium_cash,
        premium_margin_ratio=prem_ratio,
        net_delta=net_delta,
        net_gamma=net_gamma,
        net_theta=net_theta,
        net_vega=net_vega,
        call_gamma=call.gamma or 0.0,
        put_gamma=put.gamma or 0.0,
        call_theta=call.theta or 0.0,
        put_theta=put.theta or 0.0,
        call_vega=call.vega or 0.0,
        put_vega=put.vega or 0.0,
        adx=tech.adx,
        bb_pct_b=tech.bb_pct_b,
        range_low_30=tech.range_low_30,
        range_high_30=tech.range_high_30,
        notes="；".join(notes_parts),
        light_size_only=ev.light_size_only,
    )
    return cand, None


def run_screener(
    snapshots: Sequence[UnderlyingSnapshot],
    *,
    settings: Optional[dict[str, Any]] = None,
    current_margin_used: float = 0.0,
    require_ranging: bool = True,
    require_liquidity: bool = True,
    exclude_events: Optional[bool] = None,
) -> list[ShortStrangleCandidate]:
    """Screen all underlyings; skip those that fail filters."""
    cfg = settings or _load_settings()
    if exclude_events is None:
        exclude_events = bool(cfg.get("screener", {}).get("exclude_events", True))
    results: list[ShortStrangleCandidate] = []
    for snap in snapshots:
        cand, _rej = screen_underlying(
            snap,
            settings=cfg,
            current_margin_used=current_margin_used,
            require_ranging=require_ranging,
            require_liquidity=require_liquidity,
            exclude_events=exclude_events,
        )
        if cand is not None:
            results.append(cand)
    results.sort(key=lambda c: (c.premium_margin_ratio, c.iv_hv_spread, c.iv_rank), reverse=True)
    return results


def run_screener_with_rejects(
    snapshots: Sequence[UnderlyingSnapshot],
    *,
    settings: Optional[dict[str, Any]] = None,
    current_margin_used: float = 0.0,
    require_ranging: bool = True,
    require_liquidity: bool = True,
    exclude_events: Optional[bool] = None,
) -> tuple[list[ShortStrangleCandidate], list[ScreenReject], list[dict[str, Any]]]:
    """Full scan returning recommendations, rejects, and IV-passed summary rows."""
    cfg = settings or _load_settings()
    if exclude_events is None:
        exclude_events = bool(cfg.get("screener", {}).get("exclude_events", True))
    sc = cfg.get("screener", {})
    ivr_min = float(sc.get("ivr_min", sc.get("iv_rank_min", 60.0)))
    ivp_min = float(sc.get("ivp_min", sc.get("iv_percentile_min", 70.0)))

    results: list[ShortStrangleCandidate] = []
    rejects: list[ScreenReject] = []
    iv_passed: list[dict[str, Any]] = []

    for snap in snapshots:
        try:
            hv = hv30(snap.prices)
            ivr = iv_rank(snap.current_iv, snap.iv_history)
            ivp = iv_percentile(snap.current_iv, snap.iv_history)
            spread = iv_hv_spread(snap.current_iv, hv)
        except ValueError:
            rejects.append(
                ScreenReject(snap.underlying, snap.product, snap.product_name or snap.product, "hv", "HV 计算失败")
            )
            continue

        if ivr >= ivr_min or ivp >= ivp_min:
            iv_passed.append(
                {
                    "product": snap.product,
                    "product_name": snap.product_name or snap.product,
                    "underlying": snap.underlying,
                    "current_iv": snap.current_iv,
                    "iv_rank": ivr,
                    "iv_percentile": ivp,
                    "iv_hv_spread": spread,
                    "hv30": hv,
                }
            )

        cand, rej = screen_underlying(
            snap,
            settings=cfg,
            current_margin_used=current_margin_used,
            require_ranging=require_ranging,
            require_liquidity=require_liquidity,
            exclude_events=exclude_events,
        )
        if cand is not None:
            results.append(cand)
        elif rej is not None:
            rejects.append(rej)

    results.sort(key=lambda c: (c.premium_margin_ratio, c.iv_hv_spread, c.iv_rank), reverse=True)
    iv_passed.sort(key=lambda x: x["iv_rank"], reverse=True)
    return results, rejects, iv_passed


# ---------------------------------------------------------------------------
# Risk secondary checks (Module 4)
# ---------------------------------------------------------------------------

@dataclass
class RiskAlert:
    underlying: str
    alert_type: str
    message: str
    severity: str  # INFO / WARN / CRITICAL


def check_delta_tilt(net_delta: float, underlying: str, threshold: float = 0.30) -> Optional[RiskAlert]:
    if abs(net_delta) > threshold:
        return RiskAlert(
            underlying=underlying,
            alert_type="DELTA_TILT",
            message=f"|net_delta|={abs(net_delta):.3f} > {threshold}; consider hedge/roll",
            severity="WARN",
        )
    return None


def check_gamma_squeeze(
    F: float,
    strike: float,
    dte: int,
    underlying: str,
    *,
    dte_threshold: int = 10,
    moneyness: float = 0.03,
) -> Optional[RiskAlert]:
    if dte < dte_threshold and F > 0 and abs(F - strike) / F <= moneyness:
        return RiskAlert(
            underlying=underlying,
            alert_type="GAMMA_SQUEEZE",
            message=(
                f"DTE={dte} < {dte_threshold} and |F-K|/F={abs(F - strike) / F:.2%} "
                f"within {moneyness:.0%}; Gamma storm — recommend close"
            ),
            severity="CRITICAL",
        )
    return None
