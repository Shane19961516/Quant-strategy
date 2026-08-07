# -*- coding: utf-8 -*-
"""期货量化 CTA 策略框架。

经典趋势跟踪（CTA）组合：信号生成、波动率目标仓位、仓位管理风控、多品种回测与绩效评估。
"""

from .signals import dual_ma_signal, donchian_breakout_signal, ts_momentum_signal
from .risk import volatility_target_weights, atr_position_size
from .backtest import CTABacktester, BacktestResult
from .metrics import performance_summary
from .data import generate_synthetic_futures, load_futures_csv
from .position_manager import RiskLimits, apply_position_manager

__all__ = [
    "dual_ma_signal",
    "donchian_breakout_signal",
    "ts_momentum_signal",
    "volatility_target_weights",
    "atr_position_size",
    "CTABacktester",
    "BacktestResult",
    "performance_summary",
    "generate_synthetic_futures",
    "load_futures_csv",
    "RiskLimits",
    "apply_position_manager",
]
