"""Volatility-targeted futures sizing."""

from __future__ import annotations


def contracts_for_trade(
    nav: float,
    price: float,
    atr: float,
    risk_pct: float,
    stop_mult: float,
    multiplier: float,
    max_leverage: float,
) -> float:
    if nav <= 0 or price <= 0 or atr <= 0 or stop_mult <= 0 or multiplier <= 0:
        return 0.0
    stop_dist = stop_mult * atr
    qty = (nav * risk_pct) / (stop_dist * multiplier)
    max_qty = (nav * max_leverage) / (price * multiplier)
    return float(max(0.0, min(qty, max_qty)))
