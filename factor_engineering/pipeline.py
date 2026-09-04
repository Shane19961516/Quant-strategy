# -*- coding: utf-8 -*-
"""End-to-end factor engineering pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestResult, run_backtest, scores_to_ls_weights
from .combine import combine_equal, combine_icir, orthogonalize_factors
from .data import MarketPanel, REPO_ROOT, load_market_panel
from .evaluate import (
    FactorEval,
    evaluate_factor,
    evaluate_factor_universe,
    pairwise_factor_corr,
)
from .factors import DEFAULT_FACTOR_NAMES, build_factor_panel
from .select import screen_factors


@dataclass
class FactorEngineeringResult:
    panel: MarketPanel
    factors: Dict[str, pd.DataFrame]
    evals: Dict[str, FactorEval]
    scorecard: pd.DataFrame
    corr_matrix: pd.DataFrame
    selected_factors: List[str]
    composite: Optional[pd.DataFrame] = None
    blend_weights: Optional[pd.DataFrame] = None
    backtest: Optional[BacktestResult] = None
    single_factor_bt: pd.DataFrame = field(default_factory=pd.DataFrame)
    start: str = ""
    end: str = ""
    universe: str = "intersect"
    combine_method: str = "icir"
    cost_bps: float = 20.0
    n_stocks: int = 0
    n_months: int = 0


def run_factor_engineering(
    root: Path | str | None = None,
    start: str = "2010-01-01",
    end: str | None = "2019-12-31",
    universe: str = "intersect",
    factor_names: Optional[List[str]] = None,
    combine_method: str = "icir",  # equal | icir
    orthogonalize: bool = True,
    n_quantiles: int = 5,
    cost_bps: float = 20.0,
    icir_window: int = 24,
    neutralize_industry: bool = True,
    run_validation_bt: bool = True,
    panel: Optional[MarketPanel] = None,
    # screening thresholds (A-share monthly: softer than classic)
    min_abs_ic: float = 0.015,
    min_abs_icir: float = 0.25,
    min_ic_pos_ratio: float = 0.50,
    min_monotonicity: float = 0.40,
    max_turnover: float = 1.8,
    max_pairwise_corr: float = 0.85,
) -> FactorEngineeringResult:
    root = Path(root) if root is not None else REPO_ROOT
    if panel is None:
        panel = load_market_panel(root=root, start=start, end=end, universe=universe)

    names = list(factor_names) if factor_names else list(DEFAULT_FACTOR_NAMES)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=names,
        neutralize_industry=neutralize_industry,
    )

    evals = evaluate_factor_universe(factors, panel.returns, n_quantiles=n_quantiles)
    corr = pairwise_factor_corr(factors)
    scorecard = screen_factors(
        evals,
        min_abs_ic=min_abs_ic,
        min_abs_icir=min_abs_icir,
        min_ic_pos_ratio=min_ic_pos_ratio,
        min_monotonicity=min_monotonicity,
        max_turnover=max_turnover,
        corr_matrix=corr,
        max_pairwise_corr=max_pairwise_corr,
    )

    selected = scorecard.index[scorecard["pass_all"]].tolist()
    # fallback: take top-4 by quality if filters too strict
    if len(selected) < 2:
        selected = scorecard.head(4).index.tolist()

    directions = {
        n: float(scorecard.loc[n, "direction"]) if n in scorecard.index else 1.0
        for n in selected
    }

    # optional orthogonalization in quality order
    combo_panels = {n: factors[n] for n in selected}
    if orthogonalize and len(selected) >= 2:
        combo_panels = orthogonalize_factors(combo_panels, order=selected)

    blend_weights = None
    composite = None
    if combine_method == "icir":
        blend = combine_icir(
            combo_panels,
            panel.returns,
            window=icir_window,
            names=selected,
            directions=directions,
        )
        composite = blend["combined"]
        blend_weights = blend["weights"]
    else:
        # apply direction then equal weight
        signed = {n: combo_panels[n] * directions[n] for n in selected}
        composite = combine_equal(signed, names=selected)

    # single-factor LS diagnostics (direction-adjusted)
    rows = []
    for name in names:
        direction = float(scorecard.loc[name, "direction"]) if name in scorecard.index else 1.0
        w = scores_to_ls_weights(factors[name] * direction, n_quantiles=n_quantiles)
        bt = run_backtest(w, panel.returns, cost_bps=cost_bps)
        rows.append({"factor": name, **bt.summary})
    single = pd.DataFrame(rows).set_index("factor")

    bt_result = None
    if run_validation_bt and composite is not None:
        w = scores_to_ls_weights(composite, n_quantiles=n_quantiles)
        bt_result = run_backtest(w, panel.returns, cost_bps=cost_bps)
        # also evaluate composite as a factor
        evals["composite"] = evaluate_factor(
            "composite", composite, panel.returns, n_quantiles=n_quantiles
        )

    dates = panel.returns.columns
    return FactorEngineeringResult(
        panel=panel,
        factors=factors,
        evals=evals,
        scorecard=scorecard,
        corr_matrix=corr,
        selected_factors=selected,
        composite=composite,
        blend_weights=blend_weights,
        backtest=bt_result,
        single_factor_bt=single,
        start=str(pd.Timestamp(dates[0]).date()) if len(dates) else start,
        end=str(pd.Timestamp(dates[-1]).date()) if len(dates) else (end or ""),
        universe=universe,
        combine_method=combine_method,
        cost_bps=cost_bps,
        n_stocks=int(panel.returns.shape[0]),
        n_months=int(panel.returns.shape[1]),
    )
