# -*- coding: utf-8 -*-
"""A-share factor engineering + factor warehouse toolkit.

完整流程：
  因子生成 → 有效性/稳定性/分层/多空检验 → 入库标准裁决
  → FactorStore 录入 → 解释文档 → 固定时点更新 → 读取调用 API
"""

from .admission import AdmissionCriteria, AdmissionDecision, decide_admission
from .battery import BatteryResult, run_factor_battery, run_universe_battery
from .combine import combine_equal, combine_icir, orthogonalize_factors
from .data import MarketPanel, generate_synthetic_panel, load_market_panel
from .evaluate import evaluate_factor, evaluate_factor_universe
from .factors import DEFAULT_FACTOR_NAMES, FACTOR_REGISTRY, build_factor_panel
from .pipeline import FactorEngineeringResult, run_factor_engineering
from .select import screen_factors
from .store import FactorStore
from .update import UpdateConfig, run_scheduled_update
from .warehouse import WarehouseResult, run_warehouse_pipeline

__all__ = [
    "AdmissionCriteria",
    "AdmissionDecision",
    "BatteryResult",
    "DEFAULT_FACTOR_NAMES",
    "FACTOR_REGISTRY",
    "FactorEngineeringResult",
    "FactorStore",
    "MarketPanel",
    "UpdateConfig",
    "WarehouseResult",
    "build_factor_panel",
    "combine_equal",
    "combine_icir",
    "decide_admission",
    "evaluate_factor",
    "evaluate_factor_universe",
    "generate_synthetic_panel",
    "load_market_panel",
    "orthogonalize_factors",
    "run_factor_battery",
    "run_factor_engineering",
    "run_scheduled_update",
    "run_universe_battery",
    "run_warehouse_pipeline",
    "screen_factors",
]
