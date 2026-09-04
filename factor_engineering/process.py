# -*- coding: utf-8 -*-
"""Cross-sectional transforms for factor processing."""

from __future__ import annotations

import numpy as np
import pandas as pd


def winsorize(panel: pd.DataFrame, q: float = 0.01) -> pd.DataFrame:
    """Column-wise winsorize at [q, 1-q]."""
    if q is None or q <= 0:
        return panel
    lo = panel.quantile(q, axis=0)
    hi = panel.quantile(1.0 - q, axis=0)
    return panel.clip(lower=lo, upper=hi, axis=1)


def cs_zscore(panel: pd.DataFrame) -> pd.DataFrame:
    """Column-wise cross-sectional z-score."""
    mu = panel.mean(axis=0)
    sd = panel.std(axis=0, ddof=0).replace(0, np.nan)
    return panel.sub(mu, axis=1).div(sd, axis=1)


def rank_pct(panel: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank in (0, 1]."""
    return panel.rank(axis=0, pct=True, method="average")


def industry_neutralize(panel: pd.DataFrame, industry: pd.DataFrame) -> pd.DataFrame:
    """Subtract industry equal-weight mean within each date."""
    out = panel.copy()
    industry = industry.reindex(index=panel.index, columns=panel.columns)
    for dt in panel.columns:
        s = panel[dt]
        ind = industry[dt]
        df = pd.DataFrame({"v": s, "ind": ind}).dropna()
        if df.empty:
            out[dt] = np.nan
            continue
        demeaned = df["v"] - df.groupby("ind")["v"].transform("mean")
        out.loc[demeaned.index, dt] = demeaned
        missing = s.index.difference(demeaned.index)
        out.loc[missing, dt] = np.nan
    return out


def process_factor(
    raw: pd.DataFrame,
    industry: pd.DataFrame | None = None,
    *,
    lag: int = 1,
    winsor_q: float = 0.01,
    neutralize: bool = True,
    standardize: str = "zscore",
) -> pd.DataFrame:
    """Standard factor engineering chain: lag → winsor → neutralize → standardize.

    ``lag=1`` ensures signal at month t uses data through t-1 / raw_t shifted,
    so weight[t] * return[t] has no look-ahead when raw is computed with data ≤ t.
    """
    sig = raw.T.shift(lag).T if lag else raw
    if winsor_q and winsor_q > 0:
        sig = winsorize(sig, winsor_q)
    if neutralize and industry is not None:
        sig = industry_neutralize(sig, industry)
    if standardize == "zscore":
        sig = cs_zscore(sig)
    elif standardize == "rank":
        sig = rank_pct(sig)
        sig = cs_zscore(sig)  # center ranks for combine
    elif standardize in (None, "none", ""):
        pass
    else:
        raise ValueError(f"Unknown standardize mode: {standardize}")
    return sig
