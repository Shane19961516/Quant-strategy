# -*- coding: utf-8 -*-
"""绩效指标。"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def _ann_factor(freq: str = "D") -> float:
    return {"D": 252.0, "W": 52.0, "M": 12.0}.get(freq.upper(), 252.0)


def performance_summary(
    equity: pd.Series,
    returns: pd.Series | None = None,
    freq: str = "D",
    risk_free: float = 0.0,
) -> Dict[str, float]:
    """从净值曲线计算 CTA 常用绩效指标。"""
    equity = equity.dropna()
    if returns is None:
        returns = equity.pct_change().dropna()
    else:
        returns = returns.dropna()

    af = _ann_factor(freq)
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n_years = len(returns) / af
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / max(n_years, 1e-9)) - 1.0)

    vol = float(returns.std() * np.sqrt(af))
    excess = returns - risk_free / af
    sharpe = float(excess.mean() / returns.std() * np.sqrt(af)) if returns.std() > 0 else 0.0

    downside = returns.copy()
    downside[downside > 0] = 0.0
    downside_std = float(downside.std() * np.sqrt(af))
    sortino = float(excess.mean() / downside.std() * np.sqrt(af)) if downside.std() > 0 else 0.0

    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    max_dd = float(dd.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else np.nan

    win_rate = float((returns > 0).mean())
    avg_win = float(returns[returns > 0].mean()) if (returns > 0).any() else 0.0
    avg_loss = float(returns[returns < 0].mean()) if (returns < 0).any() else 0.0
    payoff = float(avg_win / abs(avg_loss)) if avg_loss != 0 else np.nan

    return {
        "total_return": total_ret,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "n_obs": float(len(returns)),
    }


def format_summary(summary: Dict[str, float]) -> str:
    lines = ["绩效摘要"]
    labels = {
        "total_return": "累计收益",
        "cagr": "年化收益",
        "ann_vol": "年化波动",
        "sharpe": "夏普比率",
        "sortino": "索提诺",
        "max_drawdown": "最大回撤",
        "calmar": "卡玛比率",
        "win_rate": "日胜率",
        "payoff_ratio": "盈亏比",
        "n_obs": "样本数",
    }
    pct_keys = {"total_return", "cagr", "ann_vol", "max_drawdown", "win_rate"}
    for k, label in labels.items():
        v = summary.get(k, np.nan)
        if k == "n_obs":
            lines.append(f"  {label}: {int(v)}")
        elif k in pct_keys:
            lines.append(f"  {label}: {v:.2%}")
        else:
            lines.append(f"  {label}: {v:.3f}")
    return "\n".join(lines)
