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


def asset_yearly_returns(close: pd.DataFrame) -> pd.DataFrame:
    """各资产同年收益对比（按自然年，缺数年份为 NaN）。"""
    cols = {}
    for c in close.columns:
        s = close[c].dropna()
        if len(s) < 5:
            continue
        ret = s.pct_change().fillna(0.0)
        cols[c] = ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
    return pd.DataFrame(cols).sort_index()


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


def daily_asset_contribution(close: pd.DataFrame, weights_daily: pd.DataFrame) -> pd.DataFrame:
    """每日资产贡献：权重_{t-1} * 收益_t。"""
    ret = close.pct_change().fillna(0.0)
    w_lag = weights_daily.shift(1).fillna(0.0)
    return (w_lag * ret).reindex(columns=CODES).fillna(0.0)


def yearly_contribution_by_asset(
    close: pd.DataFrame, weights_daily: pd.DataFrame
) -> pd.DataFrame:
    """
    策略持仓下，各资产对组合的分年贡献（算术加总日贡献）。
    注意：分年贡献之和近似该年组合收益，但因复合效应不完全相等。
    """
    contrib = daily_asset_contribution(close, weights_daily)
    by_year = contrib.groupby(contrib.index.year).sum()
    by_year["StrategyApprox"] = by_year.sum(axis=1)
    return by_year


def latest_rebalance_instruction(
    signal_weights: pd.DataFrame,
    weights_daily: pd.DataFrame,
    calendar: pd.DatetimeIndex,
) -> dict:
    """
    最新周五信号 -> 下周一执行的差额调仓指令。
    current ≈ 信号日前一持仓（最近已执行权重）。
    """
    from config import UNIVERSE

    if signal_weights.empty:
        return {}
    sig_dt = signal_weights.index.max()
    target = signal_weights.loc[sig_dt].reindex(CODES).fillna(0.0)
    # 找信号日及之前最近一次已执行持仓
    hist = weights_daily.loc[:sig_dt]
    if len(hist) == 0:
        current = pd.Series(0.0, index=CODES)
    else:
        current = hist.iloc[-1].reindex(CODES).fillna(0.0)

    # 下一交易日
    pos = {d: i for i, d in enumerate(calendar)}
    exec_date = None
    if sig_dt in pos and pos[sig_dt] + 1 < len(calendar):
        exec_date = calendar[pos[sig_dt] + 1]

    delta = target - current
    rows = []
    for c in CODES:
        rows.append(
            {
                "code": c,
                "name": UNIVERSE[c]["name"],
                "current_weight": float(current[c]),
                "target_weight": float(target[c]),
                "delta_weight": float(delta[c]),
                "action": (
                    "BUY" if delta[c] > 1e-4 else ("SELL" if delta[c] < -1e-4 else "HOLD")
                ),
            }
        )
    return {
        "signal_date": str(sig_dt.date()),
        "exec_date": str(exec_date.date()) if exec_date is not None else "next_trading_day_after_signal",
        "note": "Friday close signal -> next trading day open execution (approx). If sample ends on signal Friday, exec_date is the next session in live trading.",
        "orders": rows,
        "turnover": float(delta.abs().sum()) / 2.0,
    }


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
