# -*- coding: utf-8 -*-
"""Portfolio construction from composite factor scores."""

from __future__ import annotations

from typing import Literal, Optional

import numpy as np
import pandas as pd

Weighting = Literal["long_short", "long_only", "long_short_equal"]


def scores_to_weights(
    scores: pd.DataFrame,
    method: Weighting = "long_short",
    n_quantiles: int = 5,
    top_n: Optional[int] = None,
    long_only_top_pct: float = 0.2,
    max_name_weight: float = 0.05,
    industry: Optional[pd.DataFrame] = None,
    max_industry_weight: Optional[float] = None,
) -> pd.DataFrame:
    """Map composite scores to portfolio weights (stocks x dates).

    - ``long_short``: dollar-neutral long top quantile / short bottom quantile,
      equal weight within each side (gross ≈ 1.0 long + 1.0 short).
    - ``long_only``: equal weight top ``long_only_top_pct`` (or top_n).
    - ``long_short_equal``: same as long_short (alias).
    """
    if method == "long_short_equal":
        method = "long_short"

    weights = pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    for dt in scores.columns:
        s = scores[dt].dropna()
        if len(s) < max(n_quantiles * 2, 10):
            continue
        if method == "long_only":
            if top_n is not None:
                picked = s.nlargest(min(top_n, len(s)))
            else:
                k = max(1, int(np.ceil(len(s) * long_only_top_pct)))
                picked = s.nlargest(k)
            w = pd.Series(1.0 / len(picked), index=picked.index)
            if max_name_weight is not None:
                w = w.clip(upper=max_name_weight)
                w = w / w.sum()
            weights.loc[w.index, dt] = w
        else:
            # quantile long-short
            try:
                q = pd.qcut(s, n_quantiles, labels=False, duplicates="drop")
            except ValueError:
                continue
            qmax, qmin = int(q.max()), int(q.min())
            if qmax == qmin:
                continue
            long = s[q == qmax]
            short = s[q == qmin]
            if long.empty or short.empty:
                continue
            w = pd.Series(0.0, index=s.index)
            w.loc[long.index] = 1.0 / len(long)
            w.loc[short.index] = -1.0 / len(short)
            weights.loc[w.index, dt] = w

    if industry is not None and max_industry_weight is not None:
        weights = _cap_industry(weights, industry, max_industry_weight)
    return weights


def _cap_industry(
    weights: pd.DataFrame,
    industry: pd.DataFrame,
    max_industry_weight: float,
) -> pd.DataFrame:
    """Cap absolute industry exposure on the long side; renormalize longs."""
    out = weights.copy()
    industry = industry.reindex(index=weights.index, columns=weights.columns)
    for dt in weights.columns:
        w = weights[dt]
        ind = industry[dt]
        long = w[w > 0]
        if long.empty:
            continue
        df = pd.DataFrame({"w": long, "ind": ind.reindex(long.index)}).dropna()
        if df.empty:
            continue
        ind_sum = df.groupby("ind")["w"].sum()
        scale = (max_industry_weight / ind_sum).clip(upper=1.0)
        df["w2"] = df.apply(lambda r: r["w"] * float(scale.get(r["ind"], 1.0)), axis=1)
        if df["w2"].sum() > 0:
            df["w2"] *= long.sum() / df["w2"].sum()
        out.loc[df.index, dt] = df["w2"]
        # keep shorts unchanged
    return out


def turnover(weights: pd.DataFrame) -> pd.Series:
    """One-way turnover = 0.5 * sum |w_t - w_{t-1}|."""
    diff = weights.fillna(0.0).diff(axis=1).abs().sum(axis=0)
    return (0.5 * diff).rename("turnover")
