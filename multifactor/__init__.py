# -*- coding: utf-8 -*-
"""A-share monthly multi-factor equity strategy framework.

Pipeline: load returns → build factors → neutralize/z-score → combine →
portfolio → backtest → IC / performance metrics.
"""

from .backtest import BacktestResult, run_backtest
from .combine import combine_factors, rolling_icir_weights
from .data import load_market_panel
from .factors import DEFAULT_FACTORS, DEFAULT_FACTOR_NAMES, build_factor_panel
from .metrics import factor_ic_summary, performance_summary
from .pipeline import PipelineResult, run_multifactor_pipeline

__all__ = [
    "DEFAULT_FACTORS",
    "DEFAULT_FACTOR_NAMES",
    "BacktestResult",
    "PipelineResult",
    "build_factor_panel",
    "combine_factors",
    "factor_ic_summary",
    "load_market_panel",
    "performance_summary",
    "rolling_icir_weights",
    "run_backtest",
    "run_multifactor_pipeline",
]
