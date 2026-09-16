"""Stops and filters. ATR is price information, allowed by the spec."""

from __future__ import annotations

import math


def initial_stop(entry: float, side: float, atr: float, mult: float) -> float:
    if side == 0 or atr <= 0:
        return math.nan
    return entry - math.copysign(1.0, side) * mult * atr


def trailing_stop(extreme: float, side: float, atr: float, mult: float) -> float:
    if side == 0 or atr <= 0 or math.isnan(extreme):
        return math.nan
    return extreme - math.copysign(1.0, side) * mult * atr


def stop_hit(side: float, low: float, high: float, stop: float) -> bool:
    if side == 0 or math.isnan(stop):
        return False
    if side > 0:
        return low <= stop
    return high >= stop


def extreme_bar(z: float, threshold: float) -> bool:
    if z is None or (isinstance(z, float) and math.isnan(z)):
        return False
    return abs(float(z)) >= threshold
