# -*- coding: utf-8 -*-
"""Factor combination: equal weight and rolling ICIR weights."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional

import numpy as np
import pandas as pd


def combine_factors(
    factor_panels: Mapping[str, pd.DataFrame],
    weights: Optional[Mapping[str, float]] = None,
    names: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Weighted average of factor z-scores (equal weight by default)."""
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
        acc = contrib.fillna(0.0) if acc is None else acc.add(contrib.fillna(0.0), fill_value=0.0)
        cnt = mask * abs(w[n]) if cnt is None else cnt.add(mask * abs(w[n]), fill_value=0.0)
    # renormalize by available weight mass
    combined = acc.div(cnt.replace(0, np.nan))
    return combined


def _rank_ic(factor: pd.Series, forward_ret: pd.Series) -> float:
    df = pd.concat([factor, forward_ret], axis=1, keys=["f", "r"]).dropna()
    if len(df) < 5:
        return np.nan
    return float(df["f"].corr(df["r"], method="spearman"))


def rolling_icir_weights(
    factor_panels: Mapping[str, pd.DataFrame],
    returns: pd.DataFrame,
    window: int = 24,
    min_periods: int = 12,
    names: Optional[Iterable[str]] = None,
) -> Dict[str, pd.DataFrame]:
    """Expanding/rolling ICIR blend weights per date (for diagnostics + combine).

    Returns
    -------
    dict with:
      - ``weights``: DataFrame factors x dates
      - ``ic``: DataFrame factors x dates (contemporaneous factor vs *same* month
        return — only valid because factors were already lagged in build_factor_panel)
      - ``combined``: combined score panel
    """
    names = list(names) if names is not None else list(factor_panels.keys())
    dates = returns.columns
    ic = pd.DataFrame(index=names, columns=dates, dtype=float)
    for n in names:
        f = factor_panels[n]
        for dt in dates:
            ic.loc[n, dt] = _rank_ic(f[dt], returns[dt])

    # rolling ICIR = mean(IC) / std(IC)
    ic_num = ic.astype(float)
    roll_mean = ic_num.T.rolling(window, min_periods=min_periods).mean()
    roll_std = ic_num.T.rolling(window, min_periods=min_periods).std(ddof=0).replace(0, np.nan)
    icir = (roll_mean / roll_std).T
    # shift weights by 1 so we don't use month-t IC to weight month-t portfolio
    w_raw = icir.T.shift(1).T.clip(lower=0.0)
    # if all zero/NaN, fall back to equal
    w = w_raw.copy()
    for dt in dates:
        col = w[dt]
        if col.isna().all() or float(col.fillna(0).sum()) <= 0:
            w[dt] = 1.0 / len(names)
        else:
            s = col.fillna(0.0)
            w[dt] = s / s.sum()

    # combine date-by-date
    combined = pd.DataFrame(np.nan, index=returns.index, columns=dates)
    for dt in dates:
        parts = []
        ww = []
        for n in names:
            parts.append(factor_panels[n][dt] * float(w.loc[n, dt]))
            ww.append(float(w.loc[n, dt]) if pd.notna(w.loc[n, dt]) else 0.0)
        stacked = pd.concat(parts, axis=1)
        mass = stacked.notna().astype(float).mul(ww, axis=1).sum(axis=1)
        combined[dt] = stacked.fillna(0.0).sum(axis=1) / mass.replace(0, np.nan)

    return {"weights": w, "ic": ic_num, "combined": combined, "icir": icir}
