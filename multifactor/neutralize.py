# -*- coding: utf-8 -*-
"""Cross-sectional transforms: winsorize, z-score, industry neutralize."""

from __future__ import annotations

import numpy as np
import pandas as pd


def winsorize(panel: pd.DataFrame, q: float = 0.01) -> pd.DataFrame:
    """Column-wise winsorize at [q, 1-q]."""
    if q <= 0:
        return panel
    lo = panel.quantile(q, axis=0)
    hi = panel.quantile(1.0 - q, axis=0)
    return panel.clip(lower=lo, upper=hi, axis=1)


def cs_zscore(panel: pd.DataFrame) -> pd.DataFrame:
    """Column-wise cross-sectional z-score."""
    mu = panel.mean(axis=0)
    sd = panel.std(axis=0, ddof=0).replace(0, np.nan)
    return panel.sub(mu, axis=1).div(sd, axis=1)


def industry_neutralize(
    panel: pd.DataFrame, industry: pd.DataFrame
) -> pd.DataFrame:
    """Subtract industry equal-weight mean within each date."""
    out = panel.copy()
    # ensure aligned
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
        # stocks without industry stay NaN
        missing = s.index.difference(demeaned.index)
        out.loc[missing, dt] = np.nan
    return out


def rank_pct(panel: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank in (0, 1]."""
    return panel.rank(axis=0, pct=True, method="average")
