# -*- coding: utf-8 -*-
"""End-to-end Alpha101 US validation → admission → FactorStore → report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from factor_engineering.admission import AdmissionCriteria
from factor_engineering.docs import build_factor_doc, render_admission_standard_md
from factor_engineering.store import FactorStore

from .alphas import ALPHA_DOCS, ALPHA_REGISTRY, compute_alphas
from .data import DEFAULT_DATA_DIR, PricePanel, load_or_download_panel
from .evaluate_5d import (
    US_ALPHA101_CRITERIA,
    US_ALPHA101_CRITERIA_STRICT,
    Alpha5DResult,
    evaluate_universe_5d,
    summary_table,
)
from factor_engineering.admission import decide_admission
from .report import save_alpha101_report

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Alpha101PipelineResult:
    panel: PricePanel
    factors: Dict[str, pd.DataFrame]
    results: Dict[str, Alpha5DResult]
    summary: pd.DataFrame
    admitted: List[str]
    rejected: List[str]
    store: FactorStore
    asof: str
    criteria: AdmissionCriteria
    out_dir: Path
    strict_admitted: List[str] = field(default_factory=list)


def run_alpha101_pipeline(
    *,
    data_dir: Path | str | None = None,
    db_root: Path | str | None = None,
    out_dir: Path | str | None = None,
    start: str = "2016-08-01",
    end: Optional[str] = None,
    refresh_data: bool = False,
    factor_names: Optional[Sequence[str]] = None,
    criteria: Optional[AdmissionCriteria] = None,
    horizon: int = 5,
    cost_bps: float = 5.0,
    max_tickers: Optional[int] = None,
    panel: Optional[PricePanel] = None,
) -> Alpha101PipelineResult:
    data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    db_root = Path(db_root) if db_root else (REPO_ROOT / "factor_db_alpha101")
    out_dir = Path(out_dir) if out_dir else (REPO_ROOT / "alpha101_result")
    out_dir.mkdir(parents=True, exist_ok=True)
    crit = criteria or US_ALPHA101_CRITERIA

    if panel is None:
        print("[data] loading / downloading OHLCV …")
        panel = load_or_download_panel(
            data_dir=data_dir,
            start=start,
            end=end,
            refresh=refresh_data,
            max_tickers=max_tickers,
        )
    print(
        f"[data] tickers={panel.close.shape[1]} days={panel.close.shape[0]} "
        f"spx={len(panel.tickers_spx)} ndx={len(panel.tickers_ndx)}"
    )

    names = list(factor_names) if factor_names else list(ALPHA_REGISTRY.keys())
    print(f"[alpha] computing {len(names)} factors …")
    factors = compute_alphas(panel, names=names, lag=1)

    print(f"[eval] 5-day forward IC / layered / stability / LS …")
    results = evaluate_universe_5d(
        factors, panel.close, horizon=horizon, criteria=crit, cost_bps=cost_bps
    )
    summary = summary_table(results)
    asof = str(pd.Timestamp(panel.close.index.max()).date())

    # Strict-tier shadow decisions (documentation only)
    strict_admitted: List[str] = []
    for name, br in results.items():
        d_strict = decide_admission(name, br.metrics, criteria=US_ALPHA101_CRITERIA_STRICT)
        if d_strict.admitted:
            strict_admitted.append(name)

    store = FactorStore(db_root)
    # write US-specific admission standard
    std = render_admission_standard_md(crit)
    std = (
        "# Alpha101 US (SPX∪NDX) 入库标准\n\n"
        f"预测目标：未来 **{horizon}** 个交易日收益率（非重叠再平衡）。\n"
        f"股票池：S&P500 ∪ Nasdaq-100；样本起点 {start}。\n"
        "IC / 分层 / 多空均在非重叠 5 日网格上评估，以避免重叠收益虚高。\n\n"
        "## 研究级门槛（实际入库采用）\n\n"
        "Alpha101 在美股大盘上的典型 |IC| 仅约 0.5%–1.5%。研究级门槛要求：\n"
        "显著的 IC t 统计、半样本/年度稳定性、分层单调，且多空净夏普不低于 0。\n\n"
        + std
        + "\n\n## 机构机构级门槛（对照，通常更严）\n\n"
        + render_admission_standard_md(US_ALPHA101_CRITERIA_STRICT)
    )
    (store.root / "ADMISSION_STANDARD.md").write_text(std, encoding="utf-8")
    (out_dir / "ADMISSION_STANDARD.md").write_text(std, encoding="utf-8")

    admitted: List[str] = []
    rejected: List[str] = []
    for name, br in results.items():
        store.upsert_factor_meta(
            name,
            family="alpha101",
            description=ALPHA_DOCS.get(name, name),
            formula=name,
            direction=br.decision.direction,
            status="candidate",
            source_module="alpha101.alphas",
            process_spec={
                "lag": 1,
                "winsor_q": 0.01,
                "zscore": True,
                "horizon_days": horizon,
                "universe": "SPX_NDX",
                "admission_tier": "research",
            },
        )
        # store full panels only for admitted factors (keeps repo/DB usable)
        if br.decision.admitted:
            panel_sx = factors[name].T
            store.save_panel(name, panel_sx, asof=asof)
        store.record_admission(name, br.decision, asof=asof, auto_status=True)
        doc = build_factor_doc(name, br.decision, br.metrics, criteria=crit)
        # enrich API example for US store path
        doc["body_md"] = doc["body_md"].replace(
            "FactorStore()", f'FactorStore("{store.root.as_posix()}")'
        )
        doc["body_md"] += (
            f"\n\n## US Alpha101 备注\n"
            f"- 股票池：S&P500 ∪ Nasdaq-100\n"
            f"- 预测窗口：未来 {horizon} 日收益\n"
            f"- 数据源：yfinance（auto_adjust OHLCV）\n"
            f"- 评估网格：非重叠 {horizon} 日\n"
            f"- 入库层级：research（机构级对照另见 ADMISSION_STANDARD.md）\n"
        )
        store.save_doc(
            name, doc["body_md"], title=doc["title"], api_example=doc["api_example"]
        )
        if br.decision.admitted:
            admitted.append(name)
        else:
            rejected.append(name)

    summary.to_csv(store.root / "admission_summary.csv", encoding="utf-8-sig")
    summary.to_csv(out_dir / "admission_summary.csv", encoding="utf-8-sig")

    save_alpha101_report(
        summary=summary,
        results=results,
        admitted=admitted,
        rejected=rejected,
        out_dir=out_dir,
        asof=asof,
        n_tickers=panel.close.shape[1],
        n_days=panel.close.shape[0],
        start=str(pd.Timestamp(panel.close.index.min()).date()),
        horizon=horizon,
        criteria=crit,
        store_root=store.root,
        strict_admitted=strict_admitted,
    )

    print(f"[done] admitted(research)={admitted}")
    print(f"[done] admitted(strict)={strict_admitted}")
    print(f"[done] rejected={len(rejected)}  db={store.root}  report={out_dir}")
    return Alpha101PipelineResult(
        panel=panel,
        factors=factors,
        results=results,
        summary=summary,
        admitted=admitted,
        rejected=rejected,
        store=store,
        asof=asof,
        criteria=crit,
        out_dir=out_dir,
        strict_admitted=strict_admitted,
    )
