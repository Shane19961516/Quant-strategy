# -*- coding: utf-8 -*-
"""Performance and factor-quality metrics."""

from __future__ import annotations

from typing import Dict, Mapping, Optional

import numpy as np
import pandas as pd


def _ann_factor(freq: str = "M") -> float:
    return {"D": 252.0, "W": 52.0, "M": 12.0}.get(freq.upper(), 12.0)


def performance_summary(
    equity: pd.Series,
    returns: pd.Series | None = None,
    freq: str = "M",
    risk_free: float = 0.0,
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

    af = _ann_factor(freq)
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n_years = max(len(returns) / af, 1e-9)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / n_years) - 1.0)
    vol = float(returns.std(ddof=0) * np.sqrt(af))
    excess = returns - risk_free / af
    sharpe = (
        float(excess.mean() / returns.std(ddof=0) * np.sqrt(af))
        if returns.std(ddof=0) > 0
        else 0.0
    )
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    max_dd = float(dd.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else np.nan
    win_rate = float((returns > 0).mean()) if len(returns) else np.nan
    return {
        "total_return": total_ret,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "n_obs": float(len(returns)),
    }


def format_summary(summary: Dict[str, float]) -> str:
    labels = {
        "total_return": "累计收益",
        "cagr": "年化收益",
        "ann_vol": "年化波动",
        "sharpe": "夏普比率",
        "max_drawdown": "最大回撤",
        "calmar": "卡玛比率",
        "win_rate": "月胜率",
        "avg_turnover": "平均换手(单边)",
        "excess_cagr": "超额年化",
        "info_ratio": "信息比率",
        "n_obs": "样本数",
    }
    pct = {
        "total_return",
        "cagr",
        "ann_vol",
        "max_drawdown",
        "win_rate",
        "avg_turnover",
        "excess_cagr",
    }
    lines = ["绩效摘要"]
    for k, label in labels.items():
        if k not in summary:
            continue
        v = summary[k]
        if k == "n_obs":
            lines.append(f"  {label}: {int(v)}")
        elif k in pct:
            lines.append(f"  {label}: {v:.2%}")
        else:
            lines.append(f"  {label}: {v:.3f}")
    return "\n".join(lines)


def rank_ic_series(factor: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """Spearman IC each date (factor already lagged vs same-column return)."""
    ics = []
    for dt in returns.columns:
        df = pd.concat([factor[dt], returns[dt]], axis=1, keys=["f", "r"]).dropna()
        if len(df) < 5:
            ics.append(np.nan)
        else:
            ics.append(float(df["f"].corr(df["r"], method="spearman")))
    return pd.Series(ics, index=returns.columns, name="rank_ic")


def factor_ic_summary(
    factor_panels: Mapping[str, pd.DataFrame],
    returns: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for name, panel in factor_panels.items():
        ic = rank_ic_series(panel, returns)
        mu = float(ic.mean())
        sd = float(ic.std(ddof=0))
        rows.append(
            {
                "factor": name,
                "ic_mean": mu,
                "ic_std": sd,
                "icir": mu / sd if sd > 0 else np.nan,
                "ic_pos_ratio": float((ic > 0).mean()),
                "n": float(ic.notna().sum()),
            }
        )
    return pd.DataFrame(rows).set_index("factor")


def quantile_returns(
    scores: pd.DataFrame,
    returns: pd.DataFrame,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Equal-weight return of each score quantile by date."""
    out = pd.DataFrame(index=returns.columns, columns=[f"Q{i+1}" for i in range(n_quantiles)], dtype=float)
    for dt in returns.columns:
        s = scores[dt]
        r = returns[dt]
        df = pd.concat([s, r], axis=1, keys=["s", "r"]).dropna()
        if len(df) < n_quantiles * 3:
            continue
        try:
            df["q"] = pd.qcut(df["s"], n_quantiles, labels=False, duplicates="drop")
        except ValueError:
            continue
        g = df.groupby("q")["r"].mean()
        for q, val in g.items():
            out.loc[dt, f"Q{int(q)+1}"] = float(val)
    return out
