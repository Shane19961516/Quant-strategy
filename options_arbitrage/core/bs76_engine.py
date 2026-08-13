"""Black-76 pricing and Greeks engine for futures options."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log, sqrt
from typing import Literal

from scipy.stats import norm

OptionType = Literal["CALL", "PUT"]


@dataclass(frozen=True)
class BS76Result:
    """Black-76 price and first-order Greeks."""

    price: float
    delta: float
    gamma: float
    vega: float  # per 1% IV move
    theta: float  # daily theta
    d1: float
    d2: float


def _validate_inputs(F: float, K: float, T: float, sigma: float) -> None:
    if F <= 0 or K <= 0:
        raise ValueError("F and K must be positive")
    if T < 0:
        raise ValueError("T (time to expiry in years) must be non-negative")
    if sigma < 0:
        raise ValueError("sigma must be non-negative")


def _d1_d2(F: float, K: float, T: float, sigma: float) -> tuple[float, float]:
    if T == 0 or sigma == 0:
        # Degenerate: intrinsic / digital-like delta at expiry
        moneyness = log(F / K) if F != K else 0.0
        d1 = float("inf") if moneyness > 0 else (float("-inf") if moneyness < 0 else 0.0)
        return d1, d1
    sqrt_t = sqrt(T)
    d1 = (log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return d1, d2


def black76_price(
    F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "CALL",
) -> float:
    """Black-76 undiscounted futures-option price discounted by e^{-rT}."""
    _validate_inputs(F, K, T, sigma)
    df = exp(-r * T)
    if T == 0 or sigma == 0:
        intrinsic = max(F - K, 0.0) if option_type == "CALL" else max(K - F, 0.0)
        return df * intrinsic

    d1, d2 = _d1_d2(F, K, T, sigma)
    if option_type == "CALL":
        return df * (F * norm.cdf(d1) - K * norm.cdf(d2))
    return df * (K * norm.cdf(-d2) - F * norm.cdf(-d1))


def black76_greeks(
    F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "CALL",
) -> BS76Result:
    """
    Compute Black-76 price, Delta, Gamma, Vega (per 1% IV), and daily Theta.

    Formulas follow CURSOR_SPEC.md §3.1.
    """
    _validate_inputs(F, K, T, sigma)
    df = exp(-r * T)

    if T == 0 or sigma == 0:
        if option_type == "CALL":
            price = df * max(F - K, 0.0)
            delta = df * (1.0 if F > K else (0.5 if F == K else 0.0))
        else:
            price = df * max(K - F, 0.0)
            delta = -df * (1.0 if F < K else (0.5 if F == K else 0.0))
        return BS76Result(price=price, delta=delta, gamma=0.0, vega=0.0, theta=0.0, d1=0.0, d2=0.0)

    d1, d2 = _d1_d2(F, K, T, sigma)
    n_d1 = norm.pdf(d1)
    sqrt_t = sqrt(T)

    if option_type == "CALL":
        price = df * (F * norm.cdf(d1) - K * norm.cdf(d2))
        delta = df * norm.cdf(d1)
        # Call theta (annual) then /365 for daily
        theta_annual = (
            -F * df * n_d1 * sigma / (2.0 * sqrt_t)
            + r * F * df * norm.cdf(d1)
            - r * K * df * norm.cdf(d2)
        )
    else:
        price = df * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
        delta = -df * norm.cdf(-d1)
        # Put theta via put-call parity / direct formula
        theta_annual = (
            -F * df * n_d1 * sigma / (2.0 * sqrt_t)
            - r * F * df * norm.cdf(-d1)
            + r * K * df * norm.cdf(-d2)
        )

    gamma = df * n_d1 / (F * sigma * sqrt_t)
    vega = F * df * n_d1 * sqrt_t * 0.01  # per 1% IV
    theta = theta_annual / 365.0

    return BS76Result(
        price=price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        d1=d1,
        d2=d2,
    )


def implied_volatility(
    market_price: float,
    F: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType = "CALL",
    *,
    low: float = 1e-6,
    high: float = 5.0,
    tol: float = 1e-8,
    max_iter: int = 100,
) -> float:
    """Binary-search implied volatility under Black-76."""
    if market_price <= 0:
        raise ValueError("market_price must be positive")
    df = exp(-r * T)
    intrinsic = df * (max(F - K, 0.0) if option_type == "CALL" else max(K - F, 0.0))
    if market_price < intrinsic - 1e-10:
        raise ValueError("market_price below intrinsic value")

    lo, hi = low, high
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        price = black76_price(F, K, T, r, mid, option_type)
        if abs(price - market_price) < tol:
            return mid
        if price > market_price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)
