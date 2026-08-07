# -*- coding: utf-8 -*-
"""期货量化 CTA 策略框架。

流程：跨品种稳健参数寻优（含 ATR 止损）→ 多策略信号 → 保证金/相关性/VaR 仓位控制。
"""

from .signals import dual_ma_signal, donchian_breakout_signal, ts_momentum_signal
from .stops import StopConfig, apply_atr_stop, trade_stats_from_signal
from .optimize import optimize_strategy_params, optimize_all_methods, build_stopped_signal
from .portfolio_risk import MarginVaRLimits, apply_margin_var_controls, correlation_clusters
from .pipeline import run_cta_pipeline, PipelineResult
from .metrics import performance_summary
from .data import generate_synthetic_futures, load_futures_csv

__all__ = [
    "dual_ma_signal",
    "donchian_breakout_signal",
    "ts_momentum_signal",
    "StopConfig",
    "apply_atr_stop",
    "trade_stats_from_signal",
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
