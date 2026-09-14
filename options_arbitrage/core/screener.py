"""Market-wide short-strangle screener and contract pairing engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

import yaml
from pathlib import Path

from .bs76_engine import black76_greeks
from .capital_allocator import allocate_strangle
from .metrics import hv30, iv_hv_spread, iv_percentile, iv_rank, strangle_pop


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def pair_delta_strikes(
    contracts: Sequence[OptionContract],
    *,
    target_call_delta: float = 0.20,
    target_put_delta: float = -0.20,
    r: float = 0.02,
) -> Optional[tuple[OptionContract, OptionContract]]:
    """Pick Call/Put with |Δ| closest to target 0.20 / -0.20."""
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

    best_call = min(calls, key=lambda x: abs((x.delta or 0.0) - target_call_delta))
    best_put = min(puts, key=lambda x: abs((x.delta or 0.0) - target_put_delta))
    return best_call, best_put


def passes_hard_filters(
    dte: int,
    ivr: float,
    ivp: float,
    spread: float,
    *,
    dte_min: int = 30,
    dte_max: int = 45,
    ivr_min: float = 50.0,
    ivp_min: float = 70.0,
    iv_hv_spread_min: float = 0.05,
) -> bool:
    return (
        dte_min <= dte <= dte_max
        and ivr > ivr_min
        and ivp > ivp_min
        and spread > iv_hv_spread_min
    )


def screen_underlying(
    snap: UnderlyingSnapshot,
    *,
    settings: Optional[dict[str, Any]] = None,
    current_margin_used: float = 0.0,
) -> Optional[ShortStrangleCandidate]:
    """Run hard filters + delta pairing + capital allocation for one underlying."""
    cfg = settings or _load_settings()
    sc = cfg.get("screener", {})
    cap = cfg.get("capital", {})
    risk = cfg.get("risk", {})
    r = float(risk.get("risk_free_rate", 0.02))

    # Prefer contracts in DTE window; use median DTE among filtered for metrics
    dte_min = int(sc.get("dte_min", 30))
    dte_max = int(sc.get("dte_max", 45))
    in_window = [c for c in snap.contracts if dte_min <= c.dte <= dte_max]
    if not in_window:
        return None

    try:
        hv = hv30(snap.prices)
    except ValueError:
        return None

    ivr = iv_rank(snap.current_iv, snap.iv_history)
    ivp = iv_percentile(snap.current_iv, snap.iv_history)
    spread = iv_hv_spread(snap.current_iv, hv)

    # Use representative DTE (mode / first contract's dte in window)
    dte = int(sorted({c.dte for c in in_window})[0])
    if not passes_hard_filters(
        dte,
        ivr,
        ivp,
        spread,
        dte_min=dte_min,
        dte_max=dte_max,
        ivr_min=float(sc.get("ivr_min", 50.0)),
        ivp_min=float(sc.get("ivp_min", 70.0)),
        iv_hv_spread_min=float(sc.get("iv_hv_spread_min", 0.05)),
    ):
        return None

    paired = pair_delta_strikes(
        in_window,
        target_call_delta=float(sc.get("target_call_delta", 0.20)),
        target_put_delta=float(sc.get("target_put_delta", -0.20)),
        r=r,
    )
    if paired is None:
        return None
    call, put = paired
    assert call.delta is not None and put.delta is not None

    T = dte / 365.0
    pop = strangle_pop(
        snap.F,
        put.strike,
        call.strike,
        T,
        snap.current_iv,
        call.delta,
        put.delta,
        method="exact",
    )

    product = snap.product or snap.underlying.rstrip("0123456789").lower()
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

    flags: list[str] = []
    if alloc.blocked_by_margin_cap:
        flags.append("MARGIN_CAP_BLOCKED")

    return ShortStrangleCandidate(
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
    )


def run_screener(
    snapshots: Sequence[UnderlyingSnapshot],
    *,
    settings: Optional[dict[str, Any]] = None,
    current_margin_used: float = 0.0,
) -> list[ShortStrangleCandidate]:
    """Screen all underlyings; skip those that fail filters."""
    cfg = settings or _load_settings()
    results: list[ShortStrangleCandidate] = []
    for snap in snapshots:
        cand = screen_underlying(snap, settings=cfg, current_margin_used=current_margin_used)
        if cand is not None:
            results.append(cand)
    results.sort(key=lambda c: (c.iv_hv_spread, c.iv_rank), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Risk secondary checks (Module 4)
# ---------------------------------------------------------------------------

@dataclass
class RiskAlert:
    underlying: str
    alert_type: str
    message: str
    severity: str  # INFO / WARN / CRITICAL


def check_delta_tilt(net_delta: float, underlying: str, threshold: float = 1.0) -> Optional[RiskAlert]:
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
