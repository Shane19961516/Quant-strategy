# -*- coding: utf-8 -*-
"""Alpha101 US equity validation toolkit (SPX ∪ NDX, 5-day forward returns)."""

from .alphas import ALPHA_REGISTRY, compute_alphas
from .data import PricePanel, load_or_download_panel, make_synthetic_panel
from .evaluate_5d import (
    US_ALPHA101_CRITERIA,
    US_ALPHA101_CRITERIA_STRICT,
    evaluate_alpha_5d,
    evaluate_universe_5d,
)
from .pipeline import Alpha101PipelineResult, run_alpha101_pipeline
from .universe import fetch_universe

__all__ = [
    "ALPHA_REGISTRY",
    "Alpha101PipelineResult",
    "PricePanel",
    "US_ALPHA101_CRITERIA",
    "compute_alphas",
    "evaluate_alpha_5d",
    "evaluate_universe_5d",
    "fetch_universe",
    "load_or_download_panel",
    "make_synthetic_panel",
    "run_alpha101_pipeline",
]
