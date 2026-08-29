"""Margin calculation and max-pairs capital allocation for short strangles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import floor
from pathlib import Path
from typing import Any, Optional


def _load_margin_rules(path: Optional[str | Path] = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "margin_rules.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_product_rules(
    product: str,
    exchange: Optional[str] = None,
    rules: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Merge default → exchange → product-specific margin coefficients."""
    rules = rules or _load_margin_rules()
    merged = dict(rules.get("default", {}))
    exchanges = rules.get("exchanges", {})

    if exchange and exchange in exchanges:
        ex = exchanges[exchange]
        for k, v in ex.items():
            if k != "products":
                merged[k] = v
        products = ex.get("products", {})
        key = product if product in products else product.lower()
        if key in products:
            merged.update(products[key])
        elif product.upper() in products:
            merged.update(products[product.upper()])
    else:
        # search all exchanges for product code
        for ex in exchanges.values():
            products = ex.get("products", {})
            for cand in (product, product.lower(), product.upper()):
                if cand in products:
                    for k, v in ex.items():
                        if k != "products":
                            merged[k] = v
                    merged.update(products[cand])
                    return merged
    return merged


def underlying_margin(F: float, multiplier: float, margin_rate: float) -> float:
    """标的合约保证金 = F × multiplier × margin_rate."""
    return float(F * multiplier * margin_rate)


def otm_amount(F: float, K: float, option_type: str, multiplier: float) -> float:
    """虚值额 (OTM amount) in cash terms."""
    if option_type.upper() == "CALL":
        otm = max(K - F, 0.0)
    else:
        otm = max(F - K, 0.0)
    return float(otm * multiplier)


def short_option_margin(
    premium: float,
    F: float,
    K: float,
    option_type: str,
    *,
    multiplier: float = 10.0,
    underlying_margin_rate: float = 0.12,
    otm_haircut: float = 0.5,
    min_margin_ratio: float = 0.5,
) -> float:
    """
    Domestic short option margin (per lot):

    Margin = premium_settle + max(
        underlying_margin - 0.5 * OTM_amount,
        0.5 * underlying_margin
    )
    where premium_settle is cash premium = premium_price * multiplier.
    """
    prem_cash = float(premium) * multiplier
    u_margin = underlying_margin(F, multiplier, underlying_margin_rate)
    otm = otm_amount(F, K, option_type, multiplier)
    core = max(u_margin - otm_haircut * otm, min_margin_ratio * u_margin)
    return prem_cash + core


@dataclass(frozen=True)
class StrangleMargin:
    call_margin: float
    put_margin: float
    unit_margin: float  # 组合保证金（1对）
    call_premium_cash: float
    put_premium_cash: float
    total_premium_cash: float
    otm_leg: str  # which leg is more OTM (rights premium kept)


def short_strangle_margin(
    F: float,
    call_strike: float,
    put_strike: float,
    call_premium: float,
    put_premium: float,
    *,
    multiplier: float = 10.0,
    underlying_margin_rate: float = 0.12,
    otm_haircut: float = 0.5,
    min_margin_ratio: float = 0.5,
) -> StrangleMargin:
    """
    Combination margin estimate: max(Call_margin, Put_margin) + OTM-leg premium.

    Spec: 组合保证金采用「取大值 + 虚值端权利金」的优惠估计规则。
    """
    call_m = short_option_margin(
        call_premium,
        F,
        call_strike,
        "CALL",
        multiplier=multiplier,
        underlying_margin_rate=underlying_margin_rate,
        otm_haircut=otm_haircut,
        min_margin_ratio=min_margin_ratio,
    )
    put_m = short_option_margin(
        put_premium,
        F,
        put_strike,
        "PUT",
        multiplier=multiplier,
        underlying_margin_rate=underlying_margin_rate,
        otm_haircut=otm_haircut,
        min_margin_ratio=min_margin_ratio,
    )
    call_prem_cash = call_premium * multiplier
    put_prem_cash = put_premium * multiplier

    # Identify the more OTM leg by cash OTM amount
    call_otm = otm_amount(F, call_strike, "CALL", multiplier)
    put_otm = otm_amount(F, put_strike, "PUT", multiplier)
    if call_otm >= put_otm:
        otm_leg = "CALL"
        otm_prem = call_prem_cash
    else:
        otm_leg = "PUT"
        otm_prem = put_prem_cash

    unit = max(call_m, put_m) + otm_prem
    return StrangleMargin(
        call_margin=call_m,
        put_margin=put_m,
        unit_margin=unit,
        call_premium_cash=call_prem_cash,
        put_premium_cash=put_prem_cash,
        total_premium_cash=call_prem_cash + put_prem_cash,
        otm_leg=otm_leg,
    )


@dataclass(frozen=True)
class AllocationResult:
    max_pairs: int
    unit_margin: float
    total_margin: float
    total_premium: float
    expected_roi: float  # percent
    capital_budget: float
    blocked_by_margin_cap: bool


def max_pairs(
    unit_margin: float,
    total_equity: float = 100_000.0,
    max_allocation_per_symbol: float = 0.30,
    *,
    current_margin_used: float = 0.0,
    max_margin_usage: float = 0.60,
) -> AllocationResult:
    """
    Max Pairs = floor(W * R_max / M_unit)

    If account margin usage already exceeds max_margin_usage, refuse new pairs (0).
    """
    if unit_margin <= 0:
        raise ValueError("unit_margin must be positive")

    usage_ratio = current_margin_used / total_equity if total_equity > 0 else 1.0
    blocked = usage_ratio > max_margin_usage
    budget = total_equity * max_allocation_per_symbol
    pairs = 0 if blocked else int(floor(budget / unit_margin))
    total_margin = pairs * unit_margin
    # premium scaling happens outside; ROI uses premium per pair if provided separately
    return AllocationResult(
        max_pairs=pairs,
        unit_margin=unit_margin,
        total_margin=total_margin,
        total_premium=0.0,
        expected_roi=0.0,
        capital_budget=budget,
        blocked_by_margin_cap=blocked,
    )


def allocate_strangle(
    F: float,
    call_strike: float,
    put_strike: float,
    call_premium: float,
    put_premium: float,
    *,
    total_equity: float = 100_000.0,
    max_allocation_per_symbol: float = 0.30,
    current_margin_used: float = 0.0,
    max_margin_usage: float = 0.60,
    product: str = "default",
    exchange: Optional[str] = None,
    multiplier: Optional[float] = None,
    underlying_margin_rate: Optional[float] = None,
) -> AllocationResult:
    """End-to-end sizing: margin rules → unit margin → max pairs → ROI."""
    rules = resolve_product_rules(product, exchange)
    mult = float(multiplier if multiplier is not None else rules.get("multiplier", 10))
    rate = float(
        underlying_margin_rate
        if underlying_margin_rate is not None
        else rules.get("underlying_margin_rate", 0.12)
    )
    margin = short_strangle_margin(
        F,
        call_strike,
        put_strike,
        call_premium,
        put_premium,
        multiplier=mult,
        underlying_margin_rate=rate,
        otm_haircut=float(rules.get("otm_haircut", 0.5)),
        min_margin_ratio=float(rules.get("min_margin_ratio", 0.5)),
    )
    base = max_pairs(
        margin.unit_margin,
        total_equity=total_equity,
        max_allocation_per_symbol=max_allocation_per_symbol,
        current_margin_used=current_margin_used,
        max_margin_usage=max_margin_usage,
    )
    total_premium = margin.total_premium_cash * base.max_pairs
    total_margin = margin.unit_margin * base.max_pairs
    roi = (total_premium / total_margin * 100.0) if total_margin > 0 else 0.0
    return AllocationResult(
        max_pairs=base.max_pairs,
        unit_margin=margin.unit_margin,
        total_margin=total_margin,
        total_premium=total_premium,
        expected_roi=roi,
        capital_budget=base.capital_budget,
        blocked_by_margin_cap=base.blocked_by_margin_cap,
    )
