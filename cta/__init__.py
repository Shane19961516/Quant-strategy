# -*- coding: utf-8 -*-
"""期货量化策略框架：套利 / 反转为主，趋势可选。

流程：跨品种（或跨经济配对）稳健参数寻优 → 信号 → 保证金/相关性/VaR 仓位控制。
"""

from .signals import (
    bollinger_reversion_signal,
    dual_ma_signal,
    donchian_breakout_signal,
    short_term_reversal_signal,
    ts_momentum_signal,
)
from .stops import StopConfig, apply_atr_stop, trade_stats_from_signal
from .pairs import DEFAULT_ECONOMIC_PAIRS, build_pairs_symbol_signals, unit_pair_returns
from .optimize import (
    DEFAULT_METHODS,
    optimize_strategy_params,
    optimize_all_methods,
    build_stopped_signal,
)
from .portfolio_risk import MarginVaRLimits, apply_margin_var_controls, correlation_clusters
from .pipeline import run_cta_pipeline, PipelineResult
from .metrics import performance_summary
from .data import generate_synthetic_futures, load_futures_csv

__all__ = [
    "bollinger_reversion_signal",
    "dual_ma_signal",
    "donchian_breakout_signal",
    "short_term_reversal_signal",
    "ts_momentum_signal",
    "StopConfig",
    "apply_atr_stop",
    "trade_stats_from_signal",
    "DEFAULT_ECONOMIC_PAIRS",
    "build_pairs_symbol_signals",
    "unit_pair_returns",
    "DEFAULT_METHODS",
    "build_stopped_signal",
    "optimize_strategy_params",
    "optimize_all_methods",
    "MarginVaRLimits",
    "apply_margin_var_controls",
    "correlation_clusters",
    "run_cta_pipeline",
    "PipelineResult",
    "performance_summary",
    "generate_synthetic_futures",
    "load_futures_csv",
]
