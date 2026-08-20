"""Underlying technical filters for short-strangle regime screening."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class TechnicalSnapshot:
    adx: float
    plus_di: float
    minus_di: float
    bb_pct_b: float  # 0 = lower band, 0.5 = mid, 1 = upper
    ma20_slope_pct: float  # daily % slope of MA20
    ma60_slope_pct: float
    inside_30d_range: bool
    price: float
    bb_mid: float
    range_high_30: float
    range_low_30: float
    is_ranging: bool
    reasons: tuple[str, ...]


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    return np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))


def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    out[period - 1] = np.mean(values[:period])
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def adx_di(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> tuple[float, float, float]:
    """Return (ADX, +DI, -DI) for the latest bar."""
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    if len(c) < period + 2:
        return float("nan"), float("nan"), float("nan")

    up = np.diff(h, prepend=h[0])
    down = -np.diff(l, prepend=l[0])
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _true_range(h, l, c)

    atr = _wilder_smooth(tr, period)
    plus_sm = _wilder_smooth(plus_dm, period)
    minus_sm = _wilder_smooth(minus_dm, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_sm / atr
        minus_di = 100.0 * minus_sm / atr
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)

    adx = _wilder_smooth(np.nan_to_num(dx, nan=0.0), period)
    return float(adx[-1]), float(plus_di[-1]), float(minus_di[-1])


def bollinger_pct_b(closes: Sequence[float], window: int = 20, n_std: float = 2.0) -> tuple[float, float]:
    """Return (%B, mid) for latest close."""
    arr = np.asarray(closes, dtype=float)
    if len(arr) < window:
        return float("nan"), float("nan")
    window_slice = arr[-window:]
    mid = float(window_slice.mean())
    std = float(window_slice.std(ddof=1)) if window > 1 else 0.0
    if std <= 1e-12:
        return 0.5, mid
    upper = mid + n_std * std
    lower = mid - n_std * std
    pct_b = (arr[-1] - lower) / (upper - lower)
    return float(pct_b), mid


def ma_slope_pct(closes: Sequence[float], window: int, lookback: int = 5) -> float:
    """Approximate daily % slope of SMA over `lookback` bars."""
    arr = np.asarray(closes, dtype=float)
    if len(arr) < window + lookback:
        return float("nan")
    ma = np.convolve(arr, np.ones(window) / window, mode="valid")
    if len(ma) < lookback + 1:
        return float("nan")
    start, end = ma[-(lookback + 1)], ma[-1]
    if abs(start) < 1e-12:
        return 0.0
    return float((end / start - 1.0) / lookback * 100.0)


def evaluate_ranging_regime(
    closes: Sequence[float],
    *,
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    adx_max: float = 20.0,
    di_max: float = 25.0,
    bb_mid_tol: float = 0.35,  # |pct_b - 0.5| <= 0.35 => near mid
    ma_slope_max: float = 0.5,  # %/day
) -> TechnicalSnapshot:
    """
    Skill step 4: ranging / mean-reverting regime checks.

    If highs/lows are omitted, approximate with closes (conservative TR≈0).
    """
    c = np.asarray(closes, dtype=float)
    if highs is None:
        h = c
    else:
        h = np.asarray(highs, dtype=float)
    if lows is None:
        l = c
    else:
        l = np.asarray(lows, dtype=float)

    adx, pdi, mdi = adx_di(h, l, c)
    pct_b, mid = bollinger_pct_b(c)
    slope20 = ma_slope_pct(c, 20)
    slope60 = ma_slope_pct(c, 60)

    range_high = float(np.max(h[-30:])) if len(h) >= 30 else float(np.max(h))
    range_low = float(np.min(l[-30:])) if len(l) >= 30 else float(np.min(l))
    px = float(c[-1])
    # soft inside: not closing outside the prior 30d extremes by >0.5%
    inside = range_low * 0.995 <= px <= range_high * 1.005

    reasons: list[str] = []
    # ADX < threshold is the primary ranging signal. DI: either both muted,
    # or roughly balanced (chop with frequent +/-DI crosses still keeps ADX low).
    di_balanced = (not np.isnan(pdi)) and abs(pdi - mdi) <= di_max
    di_muted = (not np.isnan(pdi)) and pdi < di_max and mdi < di_max
    ok_adx = (not np.isnan(adx)) and adx < adx_max and (di_muted or di_balanced)
    if not ok_adx:
        reasons.append(f"ADX/DI 偏强 (ADX={adx:.1f}, +DI={pdi:.1f}, -DI={mdi:.1f})")

    ok_bb = (not np.isnan(pct_b)) and abs(pct_b - 0.5) <= bb_mid_tol
    if not ok_bb:
        reasons.append(f"偏离布林中轨 (%B={pct_b:.2f})")

    ok_ma = (not np.isnan(slope20)) and abs(slope20) < ma_slope_max and (
        np.isnan(slope60) or abs(slope60) < ma_slope_max
    )
    if not ok_ma:
        reasons.append(f"均线斜率偏大 (MA20={slope20:.2f}%/日, MA60={slope60:.2f}%/日)")

    if not inside:
        reasons.append("价格突破近30日高低点区间")

    return TechnicalSnapshot(
        adx=adx,
        plus_di=pdi,
        minus_di=mdi,
        bb_pct_b=pct_b,
        ma20_slope_pct=slope20,
        ma60_slope_pct=slope60,
        inside_30d_range=inside,
        price=px,
        bb_mid=mid,
        range_high_30=range_high,
        range_low_30=range_low,
        is_ranging=ok_adx and ok_bb and ok_ma and inside,
        reasons=tuple(reasons),
    )
