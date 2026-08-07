# -*- coding: utf-8 -*-
"""四策略资金簿整合。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import pandas as pd

from ..metrics import format_summary, performance_summary
from .strategies_12 import BookConfig, run_s1, run_s2
from .strategies_3 import run_s3
from .strategies_4 import load_or_fetch_contracts, run_s4


@dataclass
class BookResult:
    cfg: BookConfig
    nav_total: pd.Series
    ret_total: pd.Series
    nav_s1: pd.Series
    nav_s2: pd.Series
    nav_s3: pd.Series
    nav_s4: pd.Series
    ret_s1: pd.Series
    ret_s2: pd.Series
    ret_s3: pd.Series
    ret_s4: pd.Series
    summary_total: Dict
    summary_s1: Dict
    summary_s2: Dict
    summary_s3: Dict
    summary_s4: Dict
    extras: Dict = field(default_factory=dict)


def run_defined_book(
    panels: Dict[str, pd.DataFrame],
    cfg: Optional[BookConfig] = None,
    fetch_contracts: bool = True,
    contract_cache: str = "cta_data_contracts",
) -> BookResult:
    cfg = cfg or BookConfig()

    nav1, ret1, w1, sum1, sig1 = run_s1(panels, cfg)
    nav2, ret2, w2, sum2, sig2 = run_s2(panels, cfg)
    nav3, ret3, w3, sum3, sig3 = run_s3(panels, cfg)

    contract_store = None
    if fetch_contracts:
        contract_store = load_or_fetch_contracts(list(panels.keys()), cache_dir=contract_cache)
    else:
        from .strategies_4 import load_cached_contracts_only

        contract_store = load_cached_contracts_only(list(panels.keys()), cache_dir=contract_cache)
    nav4, ret4, w4, sum4, sig4 = run_s4(
        panels,
        cfg,
        contract_store=contract_store,
        cache_dir=contract_cache,
        allow_fetch=False,
    )

    # 对齐日期后相加（各策略收益已是相对总资金 100 万）
    idx = nav1.index.union(nav2.index).union(nav3.index).union(nav4.index).sort_values()
    r1 = ret1.reindex(idx).fillna(0.0)
    r2 = ret2.reindex(idx).fillna(0.0)
    r3 = ret3.reindex(idx).fillna(0.0)
    r4 = ret4.reindex(idx).fillna(0.0)
    ret_total = (r1 + r2 + r3 + r4).rename("ret")
    nav_total = (1.0 + ret_total).cumprod().rename("NAV")
    sum_total = performance_summary(nav_total, ret_total)
    sum_total["capital"] = cfg.capital
    sum_total["margin_budget_total"] = (
        cfg.margin_s1 + cfg.margin_s2 + cfg.margin_s3 + cfg.margin_s4
    )
    sum_total["max_margin_s1"] = sum1.get("max_margin", 0.0)
    sum_total["max_margin_s2"] = sum2.get("max_margin", 0.0)
    sum_total["max_margin_s3"] = sum3.get("max_margin", 0.0)
    sum_total["max_margin_s4"] = sum4.get("max_margin", 0.0)
    sum_total["max_margin"] = (
        sum_total["max_margin_s1"]
        + sum_total["max_margin_s2"]
        + sum_total["max_margin_s3"]
        + sum_total["max_margin_s4"]
    )
    sum_total["margin_budget"] = sum_total["margin_budget_total"]
    sum_total["margin_ok"] = float(
        sum_total["max_margin_s1"] <= cfg.margin_s1 + 1.0
        and sum_total["max_margin_s2"] <= cfg.margin_s2 + 1.0
        and sum_total["max_margin_s3"] <= cfg.margin_s3 + 1.0
        and sum_total["max_margin_s4"] <= cfg.margin_s4 + 1.0
    )
    sum_total["ending_equity"] = float(cfg.capital * nav_total.iloc[-1]) if len(nav_total) else cfg.capital
    sum_total["pnl_abs"] = sum_total["ending_equity"] - cfg.capital

    return BookResult(
        cfg=cfg,
        nav_total=nav_total,
        ret_total=ret_total,
        nav_s1=nav1.reindex(idx).ffill().fillna(1.0),
        nav_s2=nav2.reindex(idx).ffill().fillna(1.0),
        nav_s3=nav3.reindex(idx).ffill().fillna(1.0),
        nav_s4=nav4.reindex(idx).ffill().fillna(1.0),
        ret_s1=r1,
        ret_s2=r2,
        ret_s3=r3,
        ret_s4=r4,
        summary_total=sum_total,
        summary_s1=sum1,
        summary_s2=sum2,
        summary_s3=sum3,
        summary_s4=sum4,
        extras={
            "sig_s1": sig1,
            "sig_s2": sig2,
            "sig_s3": sig3,
            "sig_s4": sig4,
            "w_s1": w1,
            "w_s2": w2,
            "w_s3": w3,
            "w_s4": w4,
        },
    )
