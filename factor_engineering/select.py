# -*- coding: utf-8 -*-
"""Factor screening / quality scorecard."""

from __future__ import annotations

from typing import Mapping, Optional

import numpy as np
import pandas as pd

from .evaluate import FactorEval, evals_to_table
from .factors import FACTOR_META


def screen_factors(
    evals: Mapping[str, FactorEval],
    *,
    min_abs_ic: float = 0.02,
    min_abs_icir: float = 0.3,
    min_ic_pos_ratio: float = 0.52,
    min_monotonicity: float = 0.5,
    max_turnover: float = 1.5,
    corr_matrix: Optional[pd.DataFrame] = None,
    max_pairwise_corr: float = 0.85,
) -> pd.DataFrame:
    """Rank factors by a composite quality score and apply soft filters.

    Quality score (higher better):
      0.35 * |ICIR| + 0.25 * |IC_mean|*20 + 0.15 * IC_pos_ratio
      + 0.15 * q_monotonicity + 0.10 * (1 - clip(turnover,0,2)/2)
    Sign of predictive edge is taken from IC mean (negative IC still usable
    if direction is flipped — here we report absolute strength and a
    ``direction`` column: +1 keep as-is, -1 flip).
    """
    table = evals_to_table(evals).copy()
    table["family"] = [
        FACTOR_META.get(n, {}).get("family", "other") for n in table.index
    ]
    table["desc"] = [FACTOR_META.get(n, {}).get("desc", n) for n in table.index]
    table["direction"] = np.sign(table["ic_mean"]).replace(0, 1)

    abs_ic = table["ic_mean"].abs()
    abs_icir = table["icir"].abs()
    to = table["avg_turnover"].clip(lower=0, upper=2).fillna(1.0)
    mono = table["q_monotonicity"].fillna(0.0)

    table["quality"] = (
        0.35 * abs_icir.fillna(0)
        + 0.25 * (abs_ic.fillna(0) * 20.0)
        + 0.15 * table["ic_pos_ratio"].fillna(0.5)
        + 0.15 * mono
        + 0.10 * (1.0 - to / 2.0)
    )

    table["pass_ic"] = abs_ic >= min_abs_ic
    table["pass_icir"] = abs_icir >= min_abs_icir
    # for direction-aware hit rate: if IC negative, use 1-pos_ratio
    hit = table["ic_pos_ratio"].copy()
    neg = table["ic_mean"] < 0
    hit.loc[neg] = 1.0 - hit.loc[neg]
    table["pass_hit"] = hit >= min_ic_pos_ratio
    table["pass_mono"] = mono >= min_monotonicity
    table["pass_turnover"] = table["avg_turnover"].fillna(0) <= max_turnover

    table["redundant"] = False
    if corr_matrix is not None and len(corr_matrix) > 1:
        # mark lower-quality factor in highly correlated pairs
        ranked = table["quality"].sort_values(ascending=False)
        kept = []
        for name in ranked.index:
            is_red = False
            for k in kept:
                if name in corr_matrix.index and k in corr_matrix.columns:
                    c = abs(float(corr_matrix.loc[name, k]))
                    if c >= max_pairwise_corr:
                        is_red = True
                        break
            table.loc[name, "redundant"] = is_red
            if not is_red:
                kept.append(name)

    table["pass_all"] = (
        table["pass_ic"]
        & table["pass_icir"]
        & table["pass_hit"]
        & table["pass_mono"]
        & table["pass_turnover"]
        & ~table["redundant"]
    )
    table = table.sort_values("quality", ascending=False)
    return table
