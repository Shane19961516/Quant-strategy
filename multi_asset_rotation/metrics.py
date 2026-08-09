"""业绩指标与业绩归因。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import CODES, RF_ANNUAL, UNIVERSE


def perf_stats(nav: pd.Series, rf: float = RF_ANNUAL) -> dict:
    ret = nav.pct_change().dropna()
    n = len(ret)
    if n < 5:
        return {}
    total = float(nav.iloc[-1] / nav.iloc[0] - 1)
    ann = float(nav.iloc[-1] ** (252 / n) - 1)
    vol = float(ret.std() * np.sqrt(252))
    downside = ret[ret < 0]
    dvol = float(downside.std() * np.sqrt(252)) if len(downside) > 1 else np.nan
    mdd = float((nav / nav.cummax() - 1).min())
    sharpe = (ann - rf) / vol if vol > 0 else np.nan
    sharpe0 = ann / vol if vol > 0 else np.nan
    sortino = (ann - rf) / dvol if dvol and dvol > 0 else np.nan
    calmar = ann / abs(mdd) if mdd < 0 else np.nan
    win = float((ret > 0).mean())
    return {
        "start": str(nav.index[0].date()),
        "end": str(nav.index[-1].date()),
        "total_return": total,
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe_rf0": sharpe0,
        "sharpe_rf2pct": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": calmar,
        "win_rate": win,
        "n_days": n,
    }


def yearly_returns(nav: pd.Series) -> pd.Series:
    ret = nav.pct_change().fillna(0.0)
    return ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)


def contribution_attribution(
    close: pd.DataFrame, weights_daily: pd.DataFrame
) -> pd.DataFrame:
    """
    简单贡献归因：每日 权重_{t-1} * 收益_t 累加。
    返回各资产累计贡献与占比。
    """
    ret = close.pct_change().fillna(0.0)
    w_lag = weights_daily.shift(1).fillna(0.0)
    contrib = w_lag * ret
    # 对齐列
    contrib = contrib.reindex(columns=CODES).fillna(0.0)
    cum = contrib.sum(axis=0)
    out = pd.DataFrame(
        {
            "code": cum.index,
            "name": [UNIVERSE[c]["name"] for c in cum.index],
            "cum_contribution": cum.values,
        }
    )
    s = out["cum_contribution"].abs().sum()
    out["contrib_share"] = out["cum_contribution"] / s if s > 0 else 0.0
    return out.sort_values("cum_contribution", ascending=False)


def sleeve_attribution(weights_daily: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    from config import CN, GOLD, SAFE, US_CANDIDATES

    ret = close.pct_change().fillna(0.0)
    w = weights_daily.shift(1).fillna(0.0)
    sleeves = {
        "债券": [SAFE],
        "黄金": [GOLD],
        "A股红利低波": [CN],
        "美股": US_CANDIDATES,
    }
    rows = []
    for name, cols in sleeves.items():
        cols = [c for c in cols if c in w.columns]
        c = (w[cols] * ret[cols]).sum(axis=1)
        rows.append({"sleeve": name, "cum_contribution": float(c.sum()), "avg_weight": float(w[cols].sum(axis=1).mean())})
    df = pd.DataFrame(rows)
    s = df["cum_contribution"].abs().sum()
    df["contrib_share"] = df["cum_contribution"] / s if s else 0.0
    return df


def rolling_sharpe(nav: pd.Series, window: int = 63) -> pd.Series:
    ret = nav.pct_change()
    mu = ret.rolling(window).mean() * 252
    sig = ret.rolling(window).std() * np.sqrt(252)
    return (mu / sig).rename("rolling_sharpe")
