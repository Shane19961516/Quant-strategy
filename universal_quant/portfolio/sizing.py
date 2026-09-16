"""Risk-based weight: risk_budget / stop_distance, then vol cap."""

from __future__ import annotations


def target_weight(
    natr: float,
    risk_pct: float,
    stop_mult: float,
    max_weight: float,
    size_mult: float = 1.0,
) -> float:
    if natr is None or natr <= 0 or stop_mult <= 0:
        return 0.0
    stop_pct = stop_mult * natr
    w = (risk_pct / stop_pct) * abs(size_mult)
    return float(max(0.0, min(w, max_weight)))
