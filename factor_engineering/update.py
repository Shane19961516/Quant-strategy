# -*- coding: utf-8 -*-
"""固定时点更新机制（默认月度月末）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from .admission import AdmissionCriteria
from .battery import run_factor_battery
from .data import REPO_ROOT, load_market_panel
from .docs import build_factor_doc
from .factors import FACTOR_META, FACTOR_REGISTRY, build_factor_panel
from .store import FactorStore


@dataclass
class UpdateConfig:
    """固定时点更新配置。"""

    schedule: str = "month_end"  # month_end | manual
    lookback_start: str = "2010-01-01"
    universe: str = "intersect"
    retest: bool = True  # 更新后是否重跑入库检验
    only_admitted: bool = True  # 仅更新已入库因子；False=更新库中全部
    cost_bps: float = 20.0


def resolve_asof(panel_dates: pd.DatetimeIndex, schedule: str = "month_end") -> pd.Timestamp:
    """根据日程解析本次更新时点（默认取面板最新月度截面）。"""
    if len(panel_dates) == 0:
        raise ValueError("Empty panel dates")
    asof = pd.Timestamp(panel_dates.max())
    if schedule == "month_end":
        # 已是月度数据，直接用最新列
        return asof
    if schedule == "manual":
        return asof
    raise ValueError(f"Unsupported schedule: {schedule}")


def next_month_end_after(ts: pd.Timestamp) -> pd.Timestamp:
    return (ts + pd.offsets.MonthEnd(1))


def run_scheduled_update(
    store: Optional[FactorStore] = None,
    *,
    config: Optional[UpdateConfig] = None,
    root: Path | str | None = None,
    factor_names: Optional[Sequence[str]] = None,
    criteria: Optional[AdmissionCriteria] = None,
    end: Optional[str] = None,
) -> Dict:
    """执行一次固定时点更新：重算面板 →（可选）复检 → 写库。

    典型用法（月末批处理）::

        python3 run_factor_warehouse.py update --schedule month_end
    """
    cfg = config or UpdateConfig()
    store = store or FactorStore(root=Path(root) / "factor_db" if root else None)
    data_root = Path(root) if root is not None else REPO_ROOT
    crit = criteria or AdmissionCriteria()

    # which factors to refresh
    if factor_names is not None:
        names = list(factor_names)
    else:
        status = "admitted" if cfg.only_admitted else None
        catalog = store.list_factors(status=status)
        if catalog.empty:
            # bootstrap: nothing in DB yet
            names = []
        else:
            names = catalog["name"].tolist()

    if not names:
        run_id = store.begin_update_run(cfg.schedule, asof=None)
        store.finish_update_run(
            run_id,
            status="skipped",
            n_updated=0,
            detail={"reason": "no factors to update"},
        )
        return {"run_id": run_id, "status": "skipped", "n_updated": 0, "asof": None}

    panel = load_market_panel(
        root=data_root,
        start=cfg.lookback_start,
        end=end,
        universe=cfg.universe,
    )
    asof = resolve_asof(panel.returns.columns, cfg.schedule)
    run_id = store.begin_update_run(cfg.schedule, asof=str(asof.date()))

    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=names,
    )

    updated: List[str] = []
    decisions: Dict[str, bool] = {}
    errors: Dict[str, str] = {}

    for name in names:
        try:
            store.upsert_factor_meta(
                name,
                family=FACTOR_META.get(name, {}).get("family", ""),
                description=FACTOR_META.get(name, {}).get("desc", name),
                formula=name,
                source_module="factor_engineering.factors",
                process_spec={
                    "lag": 1,
                    "winsor_q": 0.01,
                    "neutralize_industry": True,
                    "standardize": "zscore",
                    "schedule": cfg.schedule,
                },
            )
            store.save_panel(name, factors[name], asof=str(asof.date()))

            if cfg.retest:
                br = run_factor_battery(
                    name,
                    factors[name],
                    panel.returns,
                    criteria=crit,
                    cost_bps=cfg.cost_bps,
                )
                store.record_admission(name, br.decision, asof=str(asof.date()))
                doc = build_factor_doc(name, br.decision, br.metrics, criteria=crit)
                store.save_doc(
                    name,
                    doc["body_md"],
                    title=doc["title"],
                    api_example=doc["api_example"],
                )
                decisions[name] = br.decision.admitted
            updated.append(name)
        except Exception as exc:  # noqa: BLE001 — batch job continues
            errors[name] = repr(exc)

    status = "ok" if not errors else ("partial" if updated else "error")
    detail = {
        "updated": updated,
        "decisions": decisions,
        "errors": errors,
        "asof": str(asof.date()),
        "schedule": cfg.schedule,
        "finished_wall": datetime.utcnow().isoformat() + "Z",
    }
    store.finish_update_run(
        run_id, status=status, n_updated=len(updated), detail=detail
    )
    return {
        "run_id": run_id,
        "status": status,
        "n_updated": len(updated),
        "asof": str(asof.date()),
        "updated": updated,
        "decisions": decisions,
        "errors": errors,
    }


def describe_schedule() -> str:
    return """固定时点更新机制
================
日程: month_end（默认）
触发: 每个自然月度数据就绪后，对因子库中 status=admitted 的因子：
  1) 拉取/对齐最新月度收益与行业面板
  2) 按注册公式重算全历史面板并覆盖写入 panels/
  3) 可选复检（有效性/稳定性/分层/多空）并刷新入库状态与文档
  4) 写入 update_runs 审计表

CLI:
  python3 run_factor_warehouse.py update --schedule month_end
  python3 run_factor_warehouse.py update --schedule month_end --no-retest
  python3 run_factor_warehouse.py update --all-status   # 含 candidate/rejected

Cron 示例（每月最后一个自然日 20:00）:
  0 20 28-31 * * [ "$(date +\\%d -d tomorrow)" = "01" ] && python3 /path/run_factor_warehouse.py update
"""
