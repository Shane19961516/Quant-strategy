# -*- coding: utf-8 -*-
"""完整因子检验套件：生成后的有效性 / 稳定性 / 分层 / 多空。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from .admission import AdmissionCriteria, AdmissionDecision, decide_admission
from .backtest import run_backtest, scores_to_ls_weights
from .evaluate import (
    FactorEval,
    evaluate_factor,
    factor_autocorr,
    ic_decay,
    ic_summary,
    pairwise_factor_corr,
    quantile_returns,
    quantile_summary,
    rank_ic_series,
)


@dataclass
class BatteryResult:
    """单因子完整检验结果。"""

    factor: str
    eval: FactorEval
    metrics: Dict[str, Any]
    decision: AdmissionDecision
    quantile_rets: pd.DataFrame
    ic_series: pd.Series
    ls_equity: Optional[pd.Series] = None
    extras: Dict[str, Any] = field(default_factory=dict)


def _subperiod_sign_ratio(ic: pd.Series, freq: str = "Y") -> float:
    """年度（或指定频率）子区间平均 IC 与全样本同号的占比。"""
    ic = ic.dropna()
    if ic.empty:
        return np.nan
    overall = np.sign(ic.mean())
    if overall == 0:
        return np.nan
    # Group by calendar year
    years = ic.index.to_period("Y")
    ok = 0
    n = 0
    for _, g in ic.groupby(years):
        if len(g) < 3:
            continue
        n += 1
        if np.sign(g.mean()) == overall:
            ok += 1
    return float(ok / n) if n else np.nan


def _half_sample_sign_match(ic: pd.Series) -> bool:
    ic = ic.dropna()
    if len(ic) < 24:
        return False
    mid = len(ic) // 2
    a, b = ic.iloc[:mid].mean(), ic.iloc[mid:].mean()
    if not np.isfinite(a) or not np.isfinite(b):
        return False
    if a == 0 or b == 0:
        return np.sign(a) == np.sign(b)
    return bool(np.sign(a) == np.sign(b))


def _rolling_icir_pos_ratio(ic: pd.Series, window: int = 24) -> float:
    ic = ic.astype(float)
    if ic.dropna().shape[0] < window:
        return np.nan
    roll_mean = ic.rolling(window, min_periods=window).mean()
    roll_std = ic.rolling(window, min_periods=window).std(ddof=0).replace(0, np.nan)
    icir = (roll_mean / roll_std).dropna()
    if icir.empty:
        return np.nan
    # direction-aware: use overall IC sign
    overall = np.sign(ic.dropna().mean())
    if overall == 0:
        return float((icir > 0).mean())
    return float(((icir * overall) > 0).mean())


def run_factor_battery(
    name: str,
    factor: pd.DataFrame,
    returns: pd.DataFrame,
    *,
    criteria: Optional[AdmissionCriteria] = None,
    n_quantiles: int = 5,
    cost_bps: float = 20.0,
) -> BatteryResult:
    """对单个已处理因子跑完整检验，并给出入库裁决。"""
    crit = criteria or AdmissionCriteria()
    ev = evaluate_factor(name, factor, returns, n_quantiles=n_quantiles)
    ic = ev.ic

    # direction from raw IC; LS uses signed factor
    direction = int(np.sign(ic.dropna().mean())) if ic.dropna().shape[0] else 1
    if direction == 0:
        direction = 1
    signed = factor * direction

    w = scores_to_ls_weights(signed, n_quantiles=n_quantiles)
    bt = run_backtest(w, returns, cost_bps=cost_bps)

    metrics: Dict[str, Any] = {}
    metrics.update(ev.scorecard_row())
    metrics["subperiod_sign_ratio"] = _subperiod_sign_ratio(ic)
    metrics["half_sample_sign_match"] = _half_sample_sign_match(ic)
    metrics["rolling_icir_pos_ratio"] = _rolling_icir_pos_ratio(
        ic, window=crit.rolling_icir_window
    )
    metrics["autocorr_1"] = float(ev.autocorr.get(1, np.nan))
    metrics["ic_decay_0"] = float(ev.decay.get(0, np.nan))
    metrics["ic_decay_3"] = float(ev.decay.get(3, np.nan))
    metrics["ls_sharpe"] = bt.summary.get("sharpe", np.nan)
    metrics["ls_cagr"] = bt.summary.get("cagr", np.nan)
    metrics["ls_max_drawdown"] = bt.summary.get("max_drawdown", np.nan)
    metrics["ls_total_return"] = bt.summary.get("total_return", np.nan)
    metrics["ls_win_rate"] = bt.summary.get("win_rate", np.nan)
    metrics["avg_turnover"] = bt.summary.get("avg_turnover", np.nan)
    metrics["n_months"] = metrics.get("n", np.nan)

    decision = decide_admission(name, metrics, criteria=crit)
    # merge direction-adjusted fields from decision
    metrics.update(
        {
            "direction": decision.direction,
            "hit_rate_adj": decision.metrics.get("hit_rate_adj"),
        }
    )
    return BatteryResult(
        factor=name,
        eval=ev,
        metrics=metrics,
        decision=decision,
        quantile_rets=ev.quantile_rets,
        ic_series=ic,
        ls_equity=bt.equity,
        extras={"backtest": bt, "signed_direction": direction},
    )


def run_universe_battery(
    factor_panels: Mapping[str, pd.DataFrame],
    returns: pd.DataFrame,
    *,
    criteria: Optional[AdmissionCriteria] = None,
    n_quantiles: int = 5,
    cost_bps: float = 20.0,
) -> Dict[str, BatteryResult]:
    return {
        name: run_factor_battery(
            name,
            panel,
            returns,
            criteria=criteria,
            n_quantiles=n_quantiles,
            cost_bps=cost_bps,
        )
        for name, panel in factor_panels.items()
    }


def battery_summary_table(results: Mapping[str, BatteryResult]) -> pd.DataFrame:
    rows = []
    for name, br in results.items():
        d = br.decision
        rows.append(
            {
                "factor": name,
                "admitted": d.admitted,
                "direction": d.direction,
                "ic_mean": br.metrics.get("ic_mean"),
                "icir": br.metrics.get("icir"),
                "ic_tstat": br.metrics.get("ic_tstat"),
                "hit_rate_adj": br.metrics.get("hit_rate_adj"),
                "subperiod_sign_ratio": br.metrics.get("subperiod_sign_ratio"),
                "half_sample_sign_match": br.metrics.get("half_sample_sign_match"),
                "rolling_icir_pos_ratio": br.metrics.get("rolling_icir_pos_ratio"),
                "q_monotonicity": br.metrics.get("q_monotonicity"),
                "q_spread": br.metrics.get("q_spread"),
                "ls_sharpe": br.metrics.get("ls_sharpe"),
                "ls_cagr": br.metrics.get("ls_cagr"),
                "ls_max_drawdown": br.metrics.get("ls_max_drawdown"),
                "avg_turnover": br.metrics.get("avg_turnover"),
                "n_fail": len(d.reject_reasons),
                "reject_summary": "; ".join(d.reject_reasons[:2]),
            }
        )
    df = pd.DataFrame(rows).set_index("factor")
    return df.sort_values(["admitted", "icir"], ascending=[False, False])
