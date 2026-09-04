# -*- coding: utf-8 -*-
"""入库标准（Admission Gate）：决定因子是否可进入因子库。

门禁分四类，**全部通过**方可入库（admit）：
1. 有效性 Validity —— IC / ICIR / t / 胜率
2. 稳定性 Stability —— 子区间同号、滚动 ICIR、半样本
3. 分层 Layered —— 分位单调性与多空价差
4. 多空 LongShort —— 夏普 / 回撤 / 换手

阈值默认面向 A 股月频价量；可通过 AdmissionCriteria 覆盖。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AdmissionCriteria:
    """标准入库阈值。修改需同步更新 docs/ADMISSION_STANDARD.md。"""

    # —— 有效性 ——
    min_abs_ic: float = 0.020
    min_abs_icir: float = 0.30
    min_ic_tstat: float = 2.0
    min_ic_hit_rate: float = 0.55  # 方向校正后的 IC>0 比例

    # —— 稳定性 ——
    min_subperiod_sign_ratio: float = 0.75  # 年度子区间 IC 同号占比
    min_half_sample_sign_match: bool = True  # 前后半样本 IC 同号
    min_rolling_icir_pos_ratio: float = 0.55
    rolling_icir_window: int = 24

    # —— 分层 ——
    min_quantile_monotonicity: float = 0.60
    min_abs_q_spread: float = 0.003  # 月均 Top-Bottom

    # —— 多空 ——
    min_ls_sharpe: float = 0.30
    min_ls_cagr: float = 0.0
    max_ls_drawdown: float = -0.50  # 更差（更负）则拒
    max_avg_turnover: float = 1.80
    cost_bps: float = 20.0
    n_quantiles: int = 5

    # —— 样本 ——
    min_months: int = 36

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# 人类可读说明（写入文档与报告）
CRITERIA_DOCS: Dict[str, str] = {
    "min_abs_ic": "有效性：|IC均值| 下限",
    "min_abs_icir": "有效性：|ICIR| 下限",
    "min_ic_tstat": "有效性：|IC t统计量| 下限",
    "min_ic_hit_rate": "有效性：方向校正后 IC 胜率下限",
    "min_subperiod_sign_ratio": "稳定性：年度子样本 IC 同号占比下限",
    "min_half_sample_sign_match": "稳定性：前后半样本 IC 必须同号",
    "min_rolling_icir_pos_ratio": "稳定性：滚动 ICIR>0 占比下限",
    "min_quantile_monotonicity": "分层：分位收益单调性下限",
    "min_abs_q_spread": "分层：|Top-Bottom 月均价差| 下限",
    "min_ls_sharpe": "多空：净夏普下限（含成本）",
    "min_ls_cagr": "多空：年化收益下限",
    "max_ls_drawdown": "多空：最大回撤下限（更差则拒）",
    "max_avg_turnover": "多空：平均单边换手上限",
    "min_months": "样本：最少有效月份数",
}


@dataclass
class GateResult:
    name: str
    category: str
    passed: bool
    value: Any
    threshold: Any
    detail: str = ""


@dataclass
class AdmissionDecision:
    factor: str
    admitted: bool
    direction: int  # +1 keep, -1 flip before use
    gates: List[GateResult]
    metrics: Dict[str, Any] = field(default_factory=dict)
    reject_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "factor": self.factor,
            "admitted": self.admitted,
            "direction": self.direction,
            "reject_reasons": self.reject_reasons,
            "metrics": self.metrics,
            "gates": [
                {
                    "name": g.name,
                    "category": g.category,
                    "passed": g.passed,
                    "value": _jsonable(g.value),
                    "threshold": _jsonable(g.threshold),
                    "detail": g.detail,
                }
                for g in self.gates
            ],
        }


def _jsonable(v: Any) -> Any:
    if isinstance(v, (np.floating, float)):
        if np.isnan(v):
            return None
        return float(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def decide_admission(
    factor: str,
    metrics: Mapping[str, Any],
    criteria: Optional[AdmissionCriteria] = None,
) -> AdmissionDecision:
    """根据完整检验指标与标准阈值给出入库裁决。"""
    crit = criteria or AdmissionCriteria()
    gates: List[GateResult] = []

    ic_mean = float(metrics.get("ic_mean", np.nan))
    direction = int(np.sign(ic_mean)) if pd.notna(ic_mean) and ic_mean != 0 else 1
    abs_ic = abs(ic_mean) if pd.notna(ic_mean) else np.nan
    abs_icir = abs(float(metrics.get("icir", np.nan))) if pd.notna(metrics.get("icir")) else np.nan
    abs_t = abs(float(metrics.get("ic_tstat", np.nan))) if pd.notna(metrics.get("ic_tstat")) else np.nan

    # direction-adjusted hit rate
    hit = metrics.get("ic_pos_ratio", np.nan)
    if pd.notna(hit):
        hit = float(hit)
        if direction < 0:
            hit = 1.0 - hit
    else:
        hit = np.nan

    def add(name, cat, passed, value, threshold, detail=""):
        gates.append(
            GateResult(
                name=name,
                category=cat,
                passed=bool(passed),
                value=value,
                threshold=threshold,
                detail=detail,
            )
        )

    n_months = float(metrics.get("n", metrics.get("n_months", 0)) or 0)
    add(
        "min_months",
        "sample",
        n_months >= crit.min_months,
        n_months,
        crit.min_months,
        CRITERIA_DOCS["min_months"],
    )
    add(
        "min_abs_ic",
        "validity",
        pd.notna(abs_ic) and abs_ic >= crit.min_abs_ic,
        abs_ic,
        crit.min_abs_ic,
        CRITERIA_DOCS["min_abs_ic"],
    )
    add(
        "min_abs_icir",
        "validity",
        pd.notna(abs_icir) and abs_icir >= crit.min_abs_icir,
        abs_icir,
        crit.min_abs_icir,
        CRITERIA_DOCS["min_abs_icir"],
    )
    add(
        "min_ic_tstat",
        "validity",
        pd.notna(abs_t) and abs_t >= crit.min_ic_tstat,
        abs_t,
        crit.min_ic_tstat,
        CRITERIA_DOCS["min_ic_tstat"],
    )
    add(
        "min_ic_hit_rate",
        "validity",
        pd.notna(hit) and hit >= crit.min_ic_hit_rate,
        hit,
        crit.min_ic_hit_rate,
        CRITERIA_DOCS["min_ic_hit_rate"],
    )

    sub_ratio = metrics.get("subperiod_sign_ratio", np.nan)
    add(
        "min_subperiod_sign_ratio",
        "stability",
        pd.notna(sub_ratio) and float(sub_ratio) >= crit.min_subperiod_sign_ratio,
        sub_ratio,
        crit.min_subperiod_sign_ratio,
        CRITERIA_DOCS["min_subperiod_sign_ratio"],
    )
    half_match = metrics.get("half_sample_sign_match", False)
    if crit.min_half_sample_sign_match:
        add(
            "half_sample_sign_match",
            "stability",
            bool(half_match),
            bool(half_match),
            True,
            CRITERIA_DOCS["min_half_sample_sign_match"],
        )
    roll_pos = metrics.get("rolling_icir_pos_ratio", np.nan)
    add(
        "min_rolling_icir_pos_ratio",
        "stability",
        pd.notna(roll_pos) and float(roll_pos) >= crit.min_rolling_icir_pos_ratio,
        roll_pos,
        crit.min_rolling_icir_pos_ratio,
        CRITERIA_DOCS["min_rolling_icir_pos_ratio"],
    )

    mono = metrics.get("q_monotonicity", np.nan)
    add(
        "min_quantile_monotonicity",
        "layered",
        pd.notna(mono) and float(mono) >= crit.min_quantile_monotonicity,
        mono,
        crit.min_quantile_monotonicity,
        CRITERIA_DOCS["min_quantile_monotonicity"],
    )
    q_spread = metrics.get("q_spread", np.nan)
    abs_qs = abs(float(q_spread)) if pd.notna(q_spread) else np.nan
    add(
        "min_abs_q_spread",
        "layered",
        pd.notna(abs_qs) and abs_qs >= crit.min_abs_q_spread,
        abs_qs,
        crit.min_abs_q_spread,
        CRITERIA_DOCS["min_abs_q_spread"],
    )

    ls_sharpe = metrics.get("ls_sharpe", np.nan)
    add(
        "min_ls_sharpe",
        "long_short",
        pd.notna(ls_sharpe) and float(ls_sharpe) >= crit.min_ls_sharpe,
        ls_sharpe,
        crit.min_ls_sharpe,
        CRITERIA_DOCS["min_ls_sharpe"],
    )
    ls_cagr = metrics.get("ls_cagr", np.nan)
    add(
        "min_ls_cagr",
        "long_short",
        pd.notna(ls_cagr) and float(ls_cagr) >= crit.min_ls_cagr,
        ls_cagr,
        crit.min_ls_cagr,
        CRITERIA_DOCS["min_ls_cagr"],
    )
    ls_dd = metrics.get("ls_max_drawdown", np.nan)
    add(
        "max_ls_drawdown",
        "long_short",
        pd.notna(ls_dd) and float(ls_dd) >= crit.max_ls_drawdown,
        ls_dd,
        crit.max_ls_drawdown,
        CRITERIA_DOCS["max_ls_drawdown"],
    )
    to = metrics.get("avg_turnover", np.nan)
    add(
        "max_avg_turnover",
        "long_short",
        pd.notna(to) and float(to) <= crit.max_avg_turnover,
        to,
        crit.max_avg_turnover,
        CRITERIA_DOCS["max_avg_turnover"],
    )

    failed = [g for g in gates if not g.passed]
    admitted = len(failed) == 0
    reasons = [f"{g.name}: value={g.value} vs {g.threshold} ({g.detail})" for g in failed]

    meta = dict(metrics)
    meta["direction"] = direction
    meta["hit_rate_adj"] = hit
    return AdmissionDecision(
        factor=factor,
        admitted=admitted,
        direction=direction,
        gates=gates,
        metrics=meta,
        reject_reasons=reasons,
    )


def decisions_to_frame(decisions: Mapping[str, AdmissionDecision]) -> pd.DataFrame:
    rows = []
    for name, d in decisions.items():
        row = {
            "factor": name,
            "admitted": d.admitted,
            "direction": d.direction,
            "n_fail": len(d.reject_reasons),
            "reject_summary": "; ".join(d.reject_reasons[:3]),
        }
        for g in d.gates:
            row[f"gate_{g.name}"] = g.passed
            row[f"val_{g.name}"] = g.value
        for k, v in d.metrics.items():
            if k not in row and isinstance(
                v, (int, float, bool, np.floating, np.integer, np.bool_)
            ):
                row[k] = _jsonable(v)
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("factor")
    sort_cols = ["admitted"]
    ascending = [False]
    if "icir" in df.columns:
        sort_cols.append("icir")
        ascending.append(False)
    return df.sort_values(sort_cols, ascending=ascending)
