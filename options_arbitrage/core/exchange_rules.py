"""Exchange margin tiers with rules metadata (docs/交易所规则管理.md)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from core.capital_allocator import (
    resolve_product_rules,
    short_option_margin,
    short_strangle_margin,
)


def load_rules_meta(path: Optional[Path] = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config" / "rules_meta.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class MarginBreakdown:
    call_exchange: float
    put_exchange: float
    no_combo_total: float
    combo_theoretical: float
    combo_status: str
    client_estimated: Optional[float]
    stress_iv_up50: float
    stress_no_combo: float
    premium_bid_cash: float
    rules_version: str
    verified_date: str


def compute_margin_breakdown(
    F: float,
    call_strike: float,
    put_strike: float,
    call_bid: float,
    put_bid: float,
    *,
    product: str,
    exchange: str,
    multiplier: float,
    client_margin_addon: Optional[float] = None,
    stress_iv_mult: float = 1.5,
) -> MarginBreakdown:
    meta = load_rules_meta()
    rules = resolve_product_rules(product, exchange)
    rate = float(rules.get("underlying_margin_rate", 0.12))
    mult = float(multiplier)

    call_m = short_option_margin(
        call_bid, F, call_strike, "CALL",
        multiplier=mult, underlying_margin_rate=rate,
        otm_haircut=float(rules.get("otm_haircut", 0.5)),
        min_margin_ratio=float(rules.get("min_margin_ratio", 0.5)),
    )
    put_m = short_option_margin(
        put_bid, F, put_strike, "PUT",
        multiplier=mult, underlying_margin_rate=rate,
        otm_haircut=float(rules.get("otm_haircut", 0.5)),
        min_margin_ratio=float(rules.get("min_margin_ratio", 0.5)),
    )
    combo = short_strangle_margin(
        F, call_strike, put_strike, call_bid, put_bid,
        multiplier=mult, underlying_margin_rate=rate,
    )
    no_combo = call_m + put_m
    combo_status = str(meta.get("combo_margin_status", "unclear"))

    client_est: Optional[float] = None
    if client_margin_addon is not None:
        base = combo.unit_margin if combo_status == "confirmed" else no_combo
        client_est = base * (1.0 + client_margin_addon)

    # Stress: scale premium ~50% IV increase (rough: premium * 1.3 for short)
    stress_call_bid = call_bid * 1.3
    stress_put_bid = put_bid * 1.3
    stress_combo = short_strangle_margin(
        F, call_strike, put_strike, stress_call_bid, stress_put_bid,
        multiplier=mult, underlying_margin_rate=rate * stress_iv_mult,
    )

    return MarginBreakdown(
        call_exchange=call_m,
        put_exchange=put_m,
        no_combo_total=no_combo,
        combo_theoretical=combo.unit_margin,
        combo_status=combo_status,
        client_estimated=client_est,
        stress_iv_up50=stress_combo.unit_margin,
        stress_no_combo=call_m * 1.15 + put_m * 1.15,
        premium_bid_cash=(call_bid + put_bid) * mult,
        rules_version=str(meta.get("rules_version", "unknown")),
        verified_date=str(meta.get("verified_date", "unknown")),
    )
