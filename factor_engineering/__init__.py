# -*- coding: utf-8 -*-
"""A-share factor engineering research toolkit.

Pipeline: load panel → build factors → process → evaluate (IC / decay /
quantile / corr) → screen → orthogonalize / combine → optional LS backtest.
"""

from .combine import combine_equal, combine_icir, orthogonalize_factors
from .data import MarketPanel, generate_synthetic_panel, load_market_panel
from .evaluate import evaluate_factor, evaluate_factor_universe
from .factors import DEFAULT_FACTOR_NAMES, FACTOR_REGISTRY, build_factor_panel
from .pipeline import FactorEngineeringResult, run_factor_engineering
from .select import screen_factors

__all__ = [
    "DEFAULT_FACTOR_NAMES",
    "FACTOR_REGISTRY",
    "FactorEngineeringResult",
    "MarketPanel",
    "build_factor_panel",
    "combine_equal",
    "combine_icir",
    "evaluate_factor",
    "evaluate_factor_universe",
    "generate_synthetic_panel",
    "load_market_panel",
    "orthogonalize_factors",
    "run_factor_engineering",
    "screen_factors",
]
