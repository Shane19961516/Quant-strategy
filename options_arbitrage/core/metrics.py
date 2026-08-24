"""Volatility statistics and short-strangle POP metrics."""

from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy.stats import norm

from .bs76_engine import _d1_d2


def log_returns(prices: Sequence[float]) -> np.ndarray:
    """Compute log returns from a price series."""
    arr = np.asarray(prices, dtype=float)
    if arr.ndim != 1 or len(arr) < 2:
        raise ValueError("prices must be a 1-D series with at least 2 points")
    if np.any(arr <= 0):
        raise ValueError("prices must be positive")
    return np.diff(np.log(arr))


def hv30(prices: Sequence[float], window: int = 30, trading_days: int = 252) -> float:
    """
    Annualized historical volatility over the last `window` trading days.

    Spec: HV_30 = sqrt(252/29 * sum_{i=1}^{30} (r_i - mean)^2)
    which is the sample std (ddof=1) of 30 log returns, annualized by sqrt(252).
    """
    rets = log_returns(prices)
    if len(rets) < window:
        raise ValueError(f"need at least {window + 1} prices for HV{window}")
    sample = rets[-window:]
    # sample variance with ddof=1 => divisor = window - 1 (=29 for window=30)
    var = float(np.sum((sample - sample.mean()) ** 2) / (window - 1))
    return float(np.sqrt(trading_days * var))


def iv_rank(current_iv: float, iv_history: Sequence[float]) -> float:
    """
    IV Rank over the provided history window (typically 252 trading days).

    IVR = (IV_current - IV_min) / (IV_max - IV_min) * 100%
    """
    hist = np.asarray(iv_history, dtype=float)
    if len(hist) == 0:
        raise ValueError("iv_history must be non-empty")
    lo, hi = float(hist.min()), float(hist.max())
    if hi <= lo:
        return 50.0  # flat history — neutral rank
    return float((current_iv - lo) / (hi - lo) * 100.0)


def iv_percentile(current_iv: float, iv_history: Sequence[float]) -> float:
    """
    IV Percentile: share of history days with IV strictly below current IV.

    IVP = count(IV_hist < IV_current) / N * 100%
    """
    hist = np.asarray(iv_history, dtype=float)
    if len(hist) == 0:
        raise ValueError("iv_history must be non-empty")
    return float(np.sum(hist < current_iv) / len(hist) * 100.0)


def iv_hv_spread(current_iv: float, hv: float) -> float:
    """IV - HV spread (absolute, same units as IV/HV, e.g. 0.32 - 0.22 = 0.10)."""
    return float(current_iv - hv)


def pop_approx(call_delta: float, put_delta: float) -> float:
    """Approximate POP ≈ 1 - |Δ_call| - |Δ_put| for a short strangle."""
    return float(1.0 - abs(call_delta) - abs(put_delta))


def pop_lognormal(
    F: float,
    K_put: float,
    K_call: float,
    T: float,
    sigma: float,
    r: float = 0.0,
) -> float:
    """
    Exact-ish POP under lognormal futures dynamics: P(K_put < F_T < K_call).

    Uses risk-neutral Black-76 distribution of F_T (drift ≈ 0 in futures measure,
    discounted measure uses d2-style terms). With futures measure, ln(F_T/F) ~ N(-0.5 σ²T, σ²T).
    """
    if K_put >= K_call:
        # inverted / crossed strikes → zero RN POP (caller should treat as invalid pair)
        return 0.0
    if T <= 0 or sigma <= 0:
        return 1.0 if K_put < F < K_call else 0.0

    # P(F_T > K) = N(d2) in Black-76 (futures measure, r does not enter forward dynamics)
    _, d2_put = _d1_d2(F, K_put, T, sigma)
    _, d2_call = _d1_d2(F, K_call, T, sigma)
    # P(F_T > K_put) - P(F_T > K_call) = N(d2_put) - N(d2_call)
    return float(norm.cdf(d2_put) - norm.cdf(d2_call))


def strangle_pop(
    F: float,
    K_put: float,
    K_call: float,
    T: float,
    sigma: float,
    call_delta: float,
    put_delta: float,
    *,
    method: str = "exact",
) -> float:
    """Unified POP helper. method: 'exact' (lognormal) or 'approx' (delta-based)."""
    if method == "approx":
        return pop_approx(call_delta, put_delta)
    return pop_lognormal(F, K_put, K_call, T, sigma)
