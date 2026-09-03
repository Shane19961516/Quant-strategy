"""Fixed-tenor ATM IV surface and IV Rank/Percentile (methods-v2.0.0)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from core.bs76_engine import implied_volatility
from core.metrics import iv_percentile, iv_rank


@dataclass(frozen=True)
class TenorIVPoint:
    option_month: str
    dte_days: int
    T_years: float
    atm_iv: float
    atm_strike: float
    source: str


@dataclass(frozen=True)
class FixedTenorIV:
    target_days: int
    sigma_star: float
    method: str
    bracket: tuple[Optional[TenorIVPoint], Optional[TenorIVPoint]]


def _safe_iv(
    bid: float,
    ask: float,
    F: float,
    K: float,
    T: float,
    opt_type: str,
    r: float = 0.02,
) -> Optional[float]:
    """Invert IV from mid if bid/ask valid; return None on failure."""
    if bid <= 0 or ask <= 0 or ask < bid or T <= 0 or F <= 0 or K <= 0:
        return None
    mid = 0.5 * (bid + ask)
    try:
        return implied_volatility(mid, F, K, T, r, opt_type.upper())  # type: ignore[arg-type]
    except Exception:
        return None


def atm_iv_from_chain(
    rows: Sequence[dict],
    F: float,
    T: float,
    *,
    r: float = 0.02,
) -> tuple[Optional[float], Optional[float]]:
    """
    Pick ATM strike (|Δ−0.5| min) from chain rows with keys:
    strike, call_bid, call_ask, put_bid, put_ask
    Returns (atm_iv, atm_strike) or (None, None).
    """
    if F is None or F <= 0 or T <= 0:
        return None, None
    best: tuple[float, float, float] | None = None  # dist, iv, strike
    for row in rows:
        K = float(row["strike"])
        if K <= 0:
            continue
        for side, bid_k, ask_k, iv_k in (
            ("CALL", "call_bid", "call_ask", "call_iv"),
            ("PUT", "put_bid", "put_ask", "put_iv"),
        ):
            bid = float(row.get(bid_k) or 0)
            ask = float(row.get(ask_k) or 0)
            iv = _safe_iv(bid, ask, F, K, T, side, r)
            if iv is None:
                raw_iv = row.get(iv_k)
                if raw_iv is not None:
                    try:
                        iv = float(raw_iv)
                        if iv <= 0 or math.isnan(iv):
                            iv = None
                    except Exception:
                        iv = None
            if iv is None:
                continue
            dist = abs(K / F - 1.0)
            if best is None or dist < best[0]:
                best = (dist, iv, K)
    if best is None:
        return None, None
    # refine: average call+put IV at nearest strike if both available
    K = best[2]
    ivs = []
    for side, bid_k, ask_k, iv_k in (
        ("CALL", "call_bid", "call_ask", "call_iv"),
        ("PUT", "put_bid", "put_ask", "put_iv"),
    ):
        row = next((r for r in rows if float(r["strike"]) == K), None)
        if row is None:
            continue
        iv = _safe_iv(float(row.get(bid_k) or 0), float(row.get(ask_k) or 0), F, K, T, side, r)
        if iv is None and row.get(iv_k) is not None:
            try:
                iv = float(row[iv_k])
                if iv <= 0 or math.isnan(iv):
                    iv = None
            except Exception:
                iv = None
        if iv is not None:
            ivs.append(iv)
    if not ivs:
        return None, None
    return float(np.mean(ivs)), K


def iv_25delta_skew(
    rows: Sequence[dict],
    F: float,
    T: float,
    *,
    r: float = 0.02,
) -> Optional[float]:
    """Skew25 = IV_put(25Δ) − IV_call(25Δ) using moneyness proxy if needed."""
    from core.bs76_engine import black76_greeks

    call_ivs: list[tuple[float, float]] = []
    put_ivs: list[tuple[float, float]] = []
    for row in rows:
        K = float(row["strike"])
        cb, ca = float(row.get("call_bid") or 0), float(row.get("call_ask") or 0)
        pb, pa = float(row.get("put_bid") or 0), float(row.get("put_ask") or 0)
        civ = _safe_iv(cb, ca, F, K, T, "CALL", r)
        if civ is not None:
            d = black76_greeks(F, K, T, r, civ, "CALL").delta
            call_ivs.append((abs(d - 0.25), civ))
        piv = _safe_iv(pb, pa, F, K, T, "PUT", r)
        if piv is not None:
            d = black76_greeks(F, K, T, r, piv, "PUT").delta
            put_ivs.append((abs(abs(d) - 0.25), piv))
    if not call_ivs or not put_ivs:
        return None
    iv_c = min(call_ivs, key=lambda x: x[0])[1]
    iv_p = min(put_ivs, key=lambda x: x[0])[1]
    return float(iv_p - iv_c)


def interpolate_fixed_tenor_iv(
    points: Sequence[TenorIVPoint],
    target_days: int = 30,
) -> FixedTenorIV:
    """Variance interpolation to target calendar days."""
    if not points:
        return FixedTenorIV(target_days, float("nan"), "none", (None, None))
    tgt_T = target_days / 365.0
    sorted_pts = sorted(points, key=lambda p: p.dte_days)
    if len(sorted_pts) == 1:
        p = sorted_pts[0]
        # scale variance to target tenor (flat forward vol assumption)
        w = p.atm_iv ** 2 * p.T_years
        sigma = math.sqrt(w / tgt_T) if tgt_T > 0 else p.atm_iv
        return FixedTenorIV(target_days, sigma, "single_point_extrapolate", (p, None))

    lo, hi = sorted_pts[0], sorted_pts[-1]
    for i in range(len(sorted_pts) - 1):
        a, b = sorted_pts[i], sorted_pts[i + 1]
        if a.dte_days <= target_days <= b.dte_days:
            lo, hi = a, b
            break
        if target_days < sorted_pts[0].dte_days:
            lo, hi = sorted_pts[0], sorted_pts[1]
            break

    w_lo = lo.atm_iv ** 2 * lo.T_years
    w_hi = hi.atm_iv ** 2 * hi.T_years
    if hi.dte_days == lo.dte_days:
        sigma = lo.atm_iv
    else:
        frac = (target_days - lo.dte_days) / (hi.dte_days - lo.dte_days)
        frac = max(0.0, min(1.0, frac))
        w_star = w_lo + (w_hi - w_lo) * frac
        sigma = math.sqrt(w_star / tgt_T) if tgt_T > 0 else float("nan")
    return FixedTenorIV(target_days, sigma, "variance_linear", (lo, hi))


def compute_iv_rank_percentile(
    sigma_star: float,
    history: Sequence[float],
) -> tuple[Optional[float], Optional[float], int]:
    """Return (ivr, ivp, n_obs). Requires >= 60 obs for partial; 252 for full gate."""
    hist = [h for h in history if h is not None and not math.isnan(h) and h > 0]
    if len(hist) < 2:
        return None, None, len(hist)
    return iv_rank(sigma_star, hist), iv_percentile(sigma_star, hist), len(hist)
