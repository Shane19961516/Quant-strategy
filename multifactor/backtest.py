# -*- coding: utf-8 -*-
"""Monthly rebalance backtest with transaction costs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .metrics import performance_summary
from .portfolio import turnover


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    gross_returns: pd.Series
    cost: pd.Series
    turnover: pd.Series
    summary: Dict[str, float]
    benchmark: Optional[pd.Series] = None
    excess_returns: Optional[pd.Series] = None
    diagnostics: Dict[str, pd.Series] = field(default_factory=dict)

    def to_frames(self) -> Dict[str, pd.DataFrame]:
        nav = self.equity.rename("NAV").to_frame()
        frames = {
            "equity": nav,
            "returns": self.returns.to_frame("ret"),
            "gross_returns": self.gross_returns.to_frame("gross_ret"),
            "cost": self.cost.to_frame("cost"),
            "turnover": self.turnover.to_frame("turnover"),
            "weights": self.weights,
            "summary": pd.DataFrame([self.summary]),
        }
        if self.benchmark is not None:
            frames["benchmark"] = self.benchmark.to_frame("benchmark")
        if self.excess_returns is not None:
            frames["excess_returns"] = self.excess_returns.to_frame("excess")
        for k, v in self.diagnostics.items():
            frames[k] = v.to_frame(k)
        return frames


def run_backtest(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    cost_bps: float = 20.0,
    benchmark: Optional[pd.Series] = None,
    starting_nav: float = 1.0,
) -> BacktestResult:
    """Apply month-t weights to month-t returns (weights must be lagged signals).

    ``cost_bps`` is one-way cost in basis points applied to turnover.
    """
    w = weights.reindex(index=returns.index, columns=returns.columns).fillna(0.0)
    r = returns.reindex_like(w)
    # portfolio gross return
    gross = (w * r.fillna(0.0)).sum(axis=0)
    to = turnover(w)
    cost = to * (cost_bps / 10000.0)
    net = gross - cost
    equity = (1.0 + net.fillna(0.0)).cumprod() * starting_nav
    equity.name = "equity"
    net = net.rename("ret")
    summary = performance_summary(equity, returns=net, freq="M")
    summary["avg_turnover"] = float(to.replace([np.inf, -np.inf], np.nan).dropna().mean())
    summary["avg_cost"] = float(cost.replace([np.inf, -np.inf], np.nan).dropna().mean())
    summary["avg_n_long"] = float((w > 0).sum(axis=0).replace(0, np.nan).mean())
    summary["avg_n_short"] = float((w < 0).sum(axis=0).replace(0, np.nan).mean())

    excess = None
    if benchmark is not None:
        bm = benchmark.reindex(net.index)
        excess = (net - bm).rename("excess")
        # long-only active stats
        active_eq = (1.0 + excess.fillna(0.0)).cumprod()
        active = performance_summary(active_eq, returns=excess.dropna(), freq="M")
        summary["excess_cagr"] = active.get("cagr", np.nan)
        summary["excess_sharpe"] = active.get("sharpe", np.nan)
        summary["info_ratio"] = active.get("sharpe", np.nan)

    return BacktestResult(
        equity=equity,
        returns=net,
        weights=w,
        gross_returns=gross.rename("gross_ret"),
        cost=cost.rename("cost"),
        turnover=to,
        summary=summary,
        benchmark=benchmark,
        excess_returns=excess,
    )
