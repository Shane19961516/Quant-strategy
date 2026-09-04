# -*- coding: utf-8 -*-
"""Parameter search to hit Sharpe / CAGR / MDD targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .backtest import BacktestResult, combine_selected_scores, run_weekly_backtest


TARGETS = {
    "sharpe": 3.0,
    "cagr": 0.30,
    "max_drawdown": -0.10,
}


@dataclass
class OptTrial:
    params: Dict
    summary: Dict[str, float]
    score: float
    feasible: bool


def _trial_score(summary: Dict[str, float]) -> Tuple[float, bool]:
    sharpe = summary.get("sharpe", np.nan)
    cagr = summary.get("cagr", np.nan)
    mdd = summary.get("max_drawdown", np.nan)
    if not all(np.isfinite(x) for x in [sharpe, cagr, mdd]):
        return -1e9, False
    feasible = (
        sharpe >= TARGETS["sharpe"]
        and cagr >= TARGETS["cagr"]
        and mdd >= TARGETS["max_drawdown"]
    )
    score = (
        5.0 * min(sharpe, 6.0)
        + 8.0 * min(cagr, 0.8)
        + 12.0 * max(0.0, 0.12 + mdd)
        + (80.0 if feasible else 0.0)
    )
    if mdd < -0.10:
        score -= 25.0 * ((-0.10) - mdd)
    if sharpe < 3:
        score -= 6.0 * (3.0 - sharpe)
    if cagr < 0.30:
        score -= 10.0 * (0.30 - cagr)
    return float(score), bool(feasible)


def optimize_to_targets(
    signed_panels: Dict[str, Dict[str, pd.DataFrame]],
    selected: Dict[str, List[str]],
    weekly_returns: pd.DataFrame,
    spy: pd.Series,
    top_n: int = 10,
    cost_bps: float = 10.0,
) -> Tuple[OptTrial, BacktestResult, List[OptTrial]]:
    """Compact grid focused on high Sharpe with drawdown control."""
    tilt_sets = [
        {"momentum": 0.35, "profitability": 0.15, "quality": 0.10, "size": 0.05, "stability": 0.25, "valuation": 0.10},
        {"momentum": 0.45, "profitability": 0.10, "quality": 0.10, "size": 0.00, "stability": 0.25, "valuation": 0.10},
        {"momentum": 0.30, "profitability": 0.20, "quality": 0.15, "size": 0.05, "stability": 0.15, "valuation": 0.15},
        {"momentum": 0.50, "profitability": 0.10, "quality": 0.05, "size": 0.00, "stability": 0.30, "valuation": 0.05},
        {"momentum": 0.25, "profitability": 0.20, "quality": 0.20, "size": 0.05, "stability": 0.15, "valuation": 0.15},
        {"momentum": 0.40, "profitability": 0.05, "quality": 0.05, "size": 0.00, "stability": 0.40, "valuation": 0.10},
        {"momentum": 0.55, "profitability": 0.10, "quality": 0.05, "size": 0.00, "stability": 0.20, "valuation": 0.10},
    ]

    configs = []
    for tilt in tilt_sets:
        for mode, fast, slow in [("ma", 5, 20), ("ma", 8, 30), ("ma", 10, 40), ("abs_mom", 10, 26), ("abs_mom", 5, 20)]:
            for vt in [0.08, 0.10, 0.12, 0.14, 0.16, 0.20]:
                for cap in [1.5, 2.0, 2.5, 3.0]:
                    for soft, hard in [(-0.03, -0.06), (-0.04, -0.07), (-0.05, -0.08), (-0.06, -0.09)]:
                        configs.append(
                            dict(
                                category_weights=tilt,
                                regime_mode=mode,
                                regime_fast=fast,
                                regime_slow=slow,
                                vol_target=vt,
                                lever_cap=cap,
                                dd_soft=soft,
                                dd_hard=hard,
                                use_regime=True,
                                use_vol_target=True,
                                use_dd_brake=True,
                                top_n=top_n,
                                cost_bps=cost_bps,
                            )
                        )
            # also no vol target / no dd variants for diversity
            configs.append(
                dict(
                    category_weights=tilt,
                    regime_mode=mode,
                    regime_fast=fast,
                    regime_slow=slow,
                    vol_target=None,
                    lever_cap=1.0,
                    dd_soft=-0.05,
                    dd_hard=-0.08,
                    use_regime=True,
                    use_vol_target=False,
                    use_dd_brake=True,
                    top_n=top_n,
                    cost_bps=cost_bps,
                )
            )

    # precompute composites
    comp_cache = {}
    for tilt in tilt_sets:
        key = tuple(sorted((k, round(v, 6)) for k, v in tilt.items()))
        avail = {k: v for k, v in tilt.items() if selected.get(k)}
        s = sum(avail.values()) or 1.0
        avail = {k: v / s for k, v in avail.items()}
        comp_cache[key] = (avail, combine_selected_scores(signed_panels, selected, avail))

    print(f"[optimize] configs={len(configs)}")
    trials: List[OptTrial] = []
    best: Optional[OptTrial] = None
    best_bt: Optional[BacktestResult] = None
    feasible_count = 0

    for i, params in enumerate(configs, 1):
        tilt = params["category_weights"]
        key = tuple(sorted((k, round(v, 6)) for k, v in tilt.items()))
        avail, comp = comp_cache[key]
        p = dict(params)
        p["category_weights"] = avail
        bt = run_weekly_backtest(
            comp,
            weekly_returns,
            spy=spy,
            top_n=p["top_n"],
            cost_bps=p["cost_bps"],
            regime_mode=p["regime_mode"],
            regime_fast=p["regime_fast"],
            regime_slow=p["regime_slow"],
            vol_target=p["vol_target"],
            lever_cap=p["lever_cap"],
            dd_soft=p["dd_soft"],
            dd_hard=p["dd_hard"],
            use_regime=p["use_regime"],
            use_vol_target=p["use_vol_target"],
            use_dd_brake=p["use_dd_brake"],
        )
        score, feasible = _trial_score(bt.summary)
        trial = OptTrial(params=p, summary=bt.summary, score=score, feasible=feasible)
        trials.append(trial)
        if feasible:
            feasible_count += 1
        if best is None or trial.score > best.score:
            best = trial
            best_bt = bt
            print(
                f"  best@{i}/{len(configs)}: sharpe={bt.summary['sharpe']:.2f} "
                f"cagr={bt.summary['cagr']:.2%} mdd={bt.summary['max_drawdown']:.2%} "
                f"feas={feasible} exp={bt.summary.get('avg_exposure', float('nan')):.2f}"
            )

    assert best is not None and best_bt is not None
    trials.sort(key=lambda t: t.score, reverse=True)
    print(f"[optimize] done; feasible={feasible_count}; best_feasible={best.feasible}")
    return best, best_bt, trials[:40]
