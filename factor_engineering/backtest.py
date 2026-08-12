# -*- coding: utf-8 -*-
"""Lightweight long-short backtest for factor validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd


def scores_to_ls_weights(
    scores: pd.DataFrame,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Dollar-neutral equal-weight long top / short bottom quantile."""
    weights = pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    for dt in scores.columns:
        s = scores[dt].dropna()
        if len(s) < n_quantiles * 3:
            continue
        try:
            q = pd.qcut(s, n_quantiles, labels=False, duplicates="drop")
        except ValueError:
            continue
        qmax, qmin = int(q.max()), int(q.min())
        if qmax == qmin:
            continue
        long = s[q == qmax]
        short = s[q == qmin]
        w = pd.Series(0.0, index=s.index)
        w.loc[long.index] = 1.0 / len(long)
        w.loc[short.index] = -1.0 / len(short)
        weights.loc[w.index, dt] = w
    return weights


def turnover(weights: pd.DataFrame) -> pd.Series:
    w = weights.fillna(0.0)
    diff = w.diff(axis=1).abs().sum(axis=0) * 0.5
    diff.iloc[0] = np.nan
    return diff.rename("turnover")


def performance_summary(
    equity: pd.Series,
    returns: pd.Series | None = None,
    freq: str = "M",
) -> Dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {
            "total_return": np.nan,
            "cagr": np.nan,
            "ann_vol": np.nan,
            "sharpe": np.nan,
            "max_drawdown": np.nan,
            "calmar": np.nan,
            "win_rate": np.nan,
            "n_obs": float(len(equity)),
        }
    if returns is None:
        returns = equity.pct_change().dropna()
    else:
        returns = returns.dropna()
    af = {"D": 252.0, "W": 52.0, "M": 12.0}.get(freq.upper(), 12.0)
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n_years = max(len(returns) / af, 1e-9)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / n_years) - 1.0)
    vol = float(returns.std(ddof=0) * np.sqrt(af))
    sharpe = (
        float(returns.mean() / returns.std(ddof=0) * np.sqrt(af))
        if returns.std(ddof=0) > 0
        else 0.0
    )
    dd = equity / equity.cummax() - 1.0
    max_dd = float(dd.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else np.nan
    return {
        "total_return": total_ret,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "win_rate": float((returns > 0).mean()) if len(returns) else np.nan,
        "n_obs": float(len(returns)),
    }


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    gross_returns: pd.Series
    cost: pd.Series
    turnover: pd.Series
    summary: Dict[str, float]


def run_backtest(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    cost_bps: float = 20.0,
) -> BacktestResult:
    w = weights.reindex(index=returns.index, columns=returns.columns).fillna(0.0)
    r = returns.reindex_like(w)
    gross = (w * r.fillna(0.0)).sum(axis=0)
    to = turnover(w)
    cost = to * (cost_bps / 10000.0)
    net = gross - cost
    equity = (1.0 + net.fillna(0.0)).cumprod()
    equity.name = "equity"
    summary = performance_summary(equity, returns=net, freq="M")
    summary["avg_turnover"] = float(to.replace([np.inf, -np.inf], np.nan).dropna().mean())
    summary["avg_cost"] = float(cost.replace([np.inf, -np.inf], np.nan).dropna().mean())
    return BacktestResult(
        equity=equity,
        returns=net.rename("ret"),
        weights=w,
        gross_returns=gross.rename("gross_ret"),
        cost=cost.rename("cost"),
        turnover=to,
        summary=summary,
    )
