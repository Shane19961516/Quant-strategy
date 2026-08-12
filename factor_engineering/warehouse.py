# -*- coding: utf-8 -*-
"""因子库端到端流程：生成 → 检验 → 入库裁决 → 写库 → 文档。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from .admission import AdmissionCriteria, AdmissionDecision, decisions_to_frame
from .battery import BatteryResult, battery_summary_table, run_universe_battery
from .data import MarketPanel, REPO_ROOT, load_market_panel
from .docs import FORMULAS, build_factor_doc, render_admission_standard_md
from .factors import DEFAULT_FACTOR_NAMES, FACTOR_META, build_factor_panel
from .store import FactorStore


@dataclass
class WarehouseResult:
    store: FactorStore
    factors: Dict[str, pd.DataFrame]
    battery: Dict[str, BatteryResult]
    summary: pd.DataFrame
    admitted: List[str]
    rejected: List[str]
    asof: str
    criteria: AdmissionCriteria
    decisions: Dict[str, AdmissionDecision] = field(default_factory=dict)


def run_warehouse_pipeline(
    root: Path | str | None = None,
    db_root: Path | str | None = None,
    start: str = "2010-01-01",
    end: str | None = "2019-12-31",
    universe: str = "intersect",
    factor_names: Optional[Sequence[str]] = None,
    criteria: Optional[AdmissionCriteria] = None,
    panel: Optional[MarketPanel] = None,
    write_standard_doc: bool = True,
) -> WarehouseResult:
    """完整流程：

    1. 因子生成（build_factor_panel）
    2. 检验套件（有效性 / 稳定性 / 分层 / 多空）
    3. 入库标准裁决（AdmissionCriteria）
    4. 通过者写入 FactorStore，并生成解释文档
    """
    data_root = Path(root) if root is not None else REPO_ROOT
    store = FactorStore(db_root if db_root is not None else data_root / "factor_db")
    crit = criteria or AdmissionCriteria()
    names = list(factor_names) if factor_names else list(DEFAULT_FACTOR_NAMES)

    if panel is None:
        panel = load_market_panel(
            root=data_root, start=start, end=end, universe=universe
        )

    factors = build_factor_panel(panel.returns, panel.industry, factor_names=names)
    battery = run_universe_battery(
        factors,
        panel.returns,
        criteria=crit,
        n_quantiles=crit.n_quantiles,
        cost_bps=crit.cost_bps,
    )
    summary = battery_summary_table(battery)
    asof = str(pd.Timestamp(panel.returns.columns.max()).date())

    admitted: List[str] = []
    rejected: List[str] = []
    decisions: Dict[str, AdmissionDecision] = {}

    if write_standard_doc:
        std = render_admission_standard_md(crit)
        (store.root / "ADMISSION_STANDARD.md").write_text(std, encoding="utf-8")

    for name, br in battery.items():
        decisions[name] = br.decision
        meta = FACTOR_META.get(name, {})
        store.upsert_factor_meta(
            name,
            family=meta.get("family", ""),
            description=meta.get("desc", name),
            formula=FORMULAS.get(name, name),
            direction=br.decision.direction,
            status="candidate",
            process_spec={
                "lag": 1,
                "winsor_q": 0.01,
                "neutralize_industry": True,
                "standardize": "zscore",
            },
        )
        store.save_panel(name, factors[name], asof=asof)
        store.record_admission(name, br.decision, asof=asof, auto_status=True)
        doc = build_factor_doc(name, br.decision, br.metrics, criteria=crit)
        store.save_doc(
            name, doc["body_md"], title=doc["title"], api_example=doc["api_example"]
        )
        if br.decision.admitted:
            admitted.append(name)
        else:
            rejected.append(name)

    # export summary tables next to DB
    summary.to_csv(store.root / "admission_summary.csv", encoding="utf-8-sig")
    decisions_to_frame(decisions).to_csv(
        store.root / "admission_gates.csv", encoding="utf-8-sig"
    )

    return WarehouseResult(
        store=store,
        factors=factors,
        battery=battery,
        summary=summary,
        admitted=admitted,
        rejected=rejected,
        asof=asof,
        criteria=crit,
        decisions=decisions,
    )
