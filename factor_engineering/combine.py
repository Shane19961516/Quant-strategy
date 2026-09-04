# -*- coding: utf-8 -*-
"""Factor combination and orthogonalization."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .evaluate import rank_ic_series


def combine_equal(
    factor_panels: Mapping[str, pd.DataFrame],
    names: Optional[Iterable[str]] = None,
    weights: Optional[Mapping[str, float]] = None,
) -> pd.DataFrame:
    """Weighted average of factor z-scores (equal by default)."""
    names = list(names) if names is not None else list(factor_panels.keys())
    if not names:
        raise ValueError("No factors to combine")
    if weights is None:
        w = {n: 1.0 / len(names) for n in names}
    else:
        w = {n: float(weights[n]) for n in names}
        s = sum(abs(v) for v in w.values()) or 1.0
        w = {n: v / s for n, v in w.items()}

    acc = None
    cnt = None
    for n in names:
        f = factor_panels[n]
        contrib = f * w[n]
        mask = f.notna().astype(float)
        acc = (
            contrib.fillna(0.0)
            if acc is None
            else acc.add(contrib.fillna(0.0), fill_value=0.0)
        )
        cnt = (
            mask * abs(w[n])
            if cnt is None
            else cnt.add(mask * abs(w[n]), fill_value=0.0)
        )
    return acc.div(cnt.replace(0, np.nan))


def combine_icir(
    factor_panels: Mapping[str, pd.DataFrame],
    returns: pd.DataFrame,
    window: int = 24,
    min_periods: int = 12,
    names: Optional[Iterable[str]] = None,
    directions: Optional[Mapping[str, float]] = None,
) -> Dict[str, pd.DataFrame]:
    """Rolling ICIR blend; weights shifted by 1 to avoid look-ahead."""
    names = list(names) if names is not None else list(factor_panels.keys())
    directions = directions or {n: 1.0 for n in names}
    dates = returns.columns
    ic = pd.DataFrame(index=names, columns=dates, dtype=float)
    signed = {}
    for n in names:
        signed[n] = factor_panels[n] * float(directions.get(n, 1.0))
        ic.loc[n] = rank_ic_series(signed[n], returns)

    ic_num = ic.astype(float)
    roll_mean = ic_num.T.rolling(window, min_periods=min_periods).mean()
    roll_std = (
        ic_num.T.rolling(window, min_periods=min_periods).std(ddof=0).replace(0, np.nan)
    )
    icir = (roll_mean / roll_std).T
    w_raw = icir.T.shift(1).T.clip(lower=0.0)
    w = w_raw.copy()
    for dt in dates:
        col = w[dt]
        if col.isna().all() or float(col.fillna(0).sum()) <= 0:
            w[dt] = 1.0 / len(names)
        else:
            s = col.fillna(0.0)
            w[dt] = s / s.sum()

    combined = pd.DataFrame(np.nan, index=returns.index, columns=dates)
    for dt in dates:
        parts = []
        ww = []
        for n in names:
            parts.append(signed[n][dt] * float(w.loc[n, dt]))
            ww.append(float(w.loc[n, dt]) if pd.notna(w.loc[n, dt]) else 0.0)
        stacked = pd.concat(parts, axis=1)
        mass = stacked.notna().astype(float).mul(ww, axis=1).sum(axis=1)
        combined[dt] = stacked.fillna(0.0).sum(axis=1) / mass.replace(0, np.nan)

    return {"weights": w, "ic": ic_num, "combined": combined, "icir": icir}


def orthogonalize_factors(
    factor_panels: Mapping[str, pd.DataFrame],
    order: Sequence[str],
) -> Dict[str, pd.DataFrame]:
    """Sequential cross-sectional residualization (Gram-Schmidt style).

    Factor at position k is residualized against factors 0..k-1 within each date.
    """
    order = list(order)
    out: Dict[str, pd.DataFrame] = {}
    if not order:
        return out
    # seed
    out[order[0]] = factor_panels[order[0]].copy()
    dates = factor_panels[order[0]].columns
    index = factor_panels[order[0]].index

    for k, name in enumerate(order[1:], start=1):
        residual = pd.DataFrame(np.nan, index=index, columns=dates)
        bases = [out[order[j]] for j in range(k)]
        target = factor_panels[name]
        for dt in dates:
            y = target[dt]
            Xcols = [b[dt] for b in bases]
            frame = pd.concat([y] + Xcols, axis=1, keys=["y"] + [f"x{j}" for j in range(k)])
            frame = frame.dropna()
            if len(frame) < k + 5:
                continue
            yv = frame["y"].values
            X = frame.drop(columns=["y"]).values
            # add intercept
            Xd = np.column_stack([np.ones(len(frame)), X])
            try:
                beta, *_ = np.linalg.lstsq(Xd, yv, rcond=None)
                resid = yv - Xd @ beta
                residual.loc[frame.index, dt] = resid
            except np.linalg.LinAlgError:
                continue
        # re-zscore residual
        mu = residual.mean(axis=0)
        sd = residual.std(axis=0, ddof=0).replace(0, np.nan)
        out[name] = residual.sub(mu, axis=1).div(sd, axis=1)
    return out
