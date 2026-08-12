# -*- coding: utf-8 -*-
"""5-day forward return evaluation: IC, layered, stability, long-short."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from factor_engineering.admission import (
    AdmissionCriteria,
    AdmissionDecision,
    decide_admission,
)


def forward_return(close: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    """Return from close[t] to close[t+horizon]."""
    return close.shift(-horizon) / close - 1.0


def rank_ic_series(factor: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    ics = []
    for dt in factor.index:
        if dt not in fwd.index:
            ics.append(np.nan)
            continue
        df = pd.concat([factor.loc[dt], fwd.loc[dt]], axis=1, keys=["f", "r"]).dropna()
        if len(df) < 20:
            ics.append(np.nan)
        else:
            ics.append(float(df["f"].corr(df["r"], method="spearman")))
    return pd.Series(ics, index=factor.index, name="rank_ic")


def nonoverlapping_index(index: pd.DatetimeIndex, step: int = 5) -> pd.DatetimeIndex:
    return index[::step]


def ic_summary(ic: pd.Series) -> Dict[str, float]:
    ic = ic.dropna()
    if ic.empty:
        return {
            "ic_mean": np.nan,
            "ic_std": np.nan,
            "icir": np.nan,
            "ic_pos_ratio": np.nan,
            "ic_tstat": np.nan,
            "n": 0.0,
        }
    mu = float(ic.mean())
    sd = float(ic.std(ddof=0))
    n = float(len(ic))
    tstat = mu / (sd / np.sqrt(n)) if sd > 0 else np.nan
    return {
        "ic_mean": mu,
        "ic_std": sd,
        "icir": mu / sd if sd > 0 else np.nan,
        "ic_pos_ratio": float((ic > 0).mean()),
        "ic_tstat": float(tstat),
        "n": n,
    }


def quantile_returns(
    factor: pd.DataFrame,
    fwd: pd.DataFrame,
    dates: pd.DatetimeIndex,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    labels = [f"Q{i+1}" for i in range(n_quantiles)]
    rows = []
    for dt in dates:
        if dt not in factor.index or dt not in fwd.index:
            continue
        df = pd.concat([factor.loc[dt], fwd.loc[dt]], axis=1, keys=["s", "r"]).dropna()
        if len(df) < n_quantiles * 5:
            continue
        try:
            df["q"] = pd.qcut(df["s"], n_quantiles, labels=False, duplicates="drop")
        except ValueError:
            continue
        g = df.groupby("q")["r"].mean()
        row = {f"Q{int(q)+1}": float(v) for q, v in g.items()}
        row["date"] = dt
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=labels)
    out = pd.DataFrame(rows).set_index("date")
    for c in labels:
        if c not in out.columns:
            out[c] = np.nan
    return out[labels]


def quantile_stats(qrets: pd.DataFrame) -> Dict[str, float]:
    means = qrets.mean()
    if means.isna().all() or len(means.dropna()) < 2:
        return {"q_spread": np.nan, "q_monotonicity": np.nan}
    vals = means.dropna().values.astype(float)
    spread = float(vals[-1] - vals[0])
    diffs = np.diff(vals)
    mono = (
        float((np.sign(diffs) == np.sign(spread)).mean())
        if abs(spread) > 1e-12
        else np.nan
    )
    return {"q_spread": spread, "q_monotonicity": mono, "top_minus_bottom": spread}


def subperiod_sign_ratio(ic: pd.Series) -> float:
    ic = ic.dropna()
    if ic.empty:
        return np.nan
    overall = np.sign(ic.mean())
    if overall == 0:
        return np.nan
    years = ic.index.to_period("Y")
    ok = n = 0
    for _, g in ic.groupby(years):
        if len(g) < 5:
            continue
        n += 1
        if np.sign(g.mean()) == overall:
            ok += 1
    return float(ok / n) if n else np.nan


def half_sample_sign_match(ic: pd.Series) -> bool:
    ic = ic.dropna()
    if len(ic) < 40:
        return False
    mid = len(ic) // 2
    a, b = ic.iloc[:mid].mean(), ic.iloc[mid:].mean()
    if not np.isfinite(a) or not np.isfinite(b):
        return False
    return bool(np.sign(a) == np.sign(b) or a == 0 or b == 0)


def rolling_icir_pos_ratio(ic: pd.Series, window: int = 24) -> float:
    """window in number of non-overlapping observations (~24 ≈ 2y of 5d steps)."""
    ic = ic.dropna().astype(float)
    if len(ic) < window:
        return np.nan
    roll_mean = ic.rolling(window, min_periods=window).mean()
    roll_std = ic.rolling(window, min_periods=window).std(ddof=0).replace(0, np.nan)
    icir = (roll_mean / roll_std).dropna()
    overall = np.sign(ic.mean())
    if overall == 0:
        return float((icir > 0).mean())
    return float(((icir * overall) > 0).mean())


def long_short_backtest(
    factor: pd.DataFrame,
    fwd: pd.DataFrame,
    dates: pd.DatetimeIndex,
    n_quantiles: int = 5,
    cost_bps: float = 5.0,
) -> Dict[str, Any]:
    """Non-overlapping 5-day long-short using forward returns (cost on turnover)."""
    rets = []
    turnovers = []
    prev_w = None
    equities = []
    nav = 1.0
    for dt in dates:
        if dt not in factor.index or dt not in fwd.index:
            continue
        s = factor.loc[dt].dropna()
        r = fwd.loc[dt].reindex(s.index)
        df = pd.concat([s, r], axis=1, keys=["s", "r"]).dropna()
        if len(df) < n_quantiles * 5:
            continue
        try:
            q = pd.qcut(df["s"], n_quantiles, labels=False, duplicates="drop")
        except ValueError:
            continue
        qmax, qmin = int(q.max()), int(q.min())
        if qmax == qmin:
            continue
        w = pd.Series(0.0, index=df.index)
        long = df.index[q == qmax]
        short = df.index[q == qmin]
        w.loc[long] = 1.0 / len(long)
        w.loc[short] = -1.0 / len(short)
        gross = float((w * df["r"]).sum())
        if prev_w is None:
            to = 1.0  # initial full gross
        else:
            aligned = pd.concat([w, prev_w], axis=1, keys=["n", "o"]).fillna(0.0)
            to = 0.5 * float((aligned["n"] - aligned["o"]).abs().sum())
        cost = to * (cost_bps / 10000.0)
        net = gross - cost
        rets.append(net)
        turnovers.append(to)
        nav *= 1.0 + net
        equities.append((dt, nav))
        prev_w = w

    if not rets:
        return {
            "ls_sharpe": np.nan,
            "ls_cagr": np.nan,
            "ls_max_drawdown": np.nan,
            "ls_total_return": np.nan,
            "ls_win_rate": np.nan,
            "avg_turnover": np.nan,
            "equity": pd.Series(dtype=float),
        }

    ser = pd.Series(rets)
    # ~50.4 non-overlapping 5d periods per year
    ann = 252.0 / 5.0
    mu, sd = float(ser.mean()), float(ser.std(ddof=0))
    sharpe = mu / sd * np.sqrt(ann) if sd > 0 else np.nan
    eq = pd.Series({d: v for d, v in equities})
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0) if len(eq) > 1 else np.nan
    n_years = max(len(ser) / ann, 1e-9)
    cagr = float((eq.iloc[-1]) ** (1 / n_years) - 1.0) if len(eq) else np.nan
    dd = float((eq / eq.cummax() - 1.0).min())
    return {
        "ls_sharpe": sharpe,
        "ls_cagr": cagr,
        "ls_max_drawdown": dd,
        "ls_total_return": total,
        "ls_win_rate": float((ser > 0).mean()),
        "avg_turnover": float(np.mean(turnovers)),
        "equity": eq,
    }


@dataclass
class Alpha5DResult:
    name: str
    metrics: Dict[str, Any]
    decision: AdmissionDecision
    ic_daily: pd.Series
    ic_nonoverlap: pd.Series
    quantile_rets: pd.DataFrame
    equity: pd.Series = field(default_factory=pd.Series)


# Strict institutional bar (often admits none for Alpha101 on US large-cap 5d)
US_ALPHA101_CRITERIA_STRICT = AdmissionCriteria(
    min_abs_ic=0.015,
    min_abs_icir=0.25,
    min_ic_tstat=1.80,
    min_ic_hit_rate=0.52,
    min_subperiod_sign_ratio=0.60,
    min_half_sample_sign_match=True,
    min_rolling_icir_pos_ratio=0.50,
    rolling_icir_window=24,
    min_quantile_monotonicity=0.50,
    min_abs_q_spread=0.002,
    min_ls_sharpe=0.25,
    min_ls_cagr=0.0,
    max_ls_drawdown=-0.55,
    max_avg_turnover=2.0,
    cost_bps=5.0,
    n_quantiles=5,
    min_months=36,
)

# Research-grade bar aligned with Alpha101 literature (weak but reproducible)
# Requires: statistically meaningful IC, stability, layered mono; LS after cost not deeply negative.
US_ALPHA101_CRITERIA = AdmissionCriteria(
    min_abs_ic=0.005,
    min_abs_icir=0.06,
    min_ic_tstat=1.50,
    min_ic_hit_rate=0.51,
    min_subperiod_sign_ratio=0.55,
    min_half_sample_sign_match=True,
    min_rolling_icir_pos_ratio=0.50,
    rolling_icir_window=24,
    min_quantile_monotonicity=0.50,
    min_abs_q_spread=0.0003,
    min_ls_sharpe=0.0,
    min_ls_cagr=-0.01,
    max_ls_drawdown=-0.60,
    max_avg_turnover=2.0,
    cost_bps=5.0,
    n_quantiles=5,
    min_months=36,
)


def evaluate_alpha_5d(
    name: str,
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    horizon: int = 5,
    criteria: Optional[AdmissionCriteria] = None,
    cost_bps: float = 5.0,
) -> Alpha5DResult:
    crit = criteria or US_ALPHA101_CRITERIA
    # align
    common_cols = factor.columns.intersection(close.columns)
    factor = factor[common_cols]
    close = close[common_cols]
    fwd = forward_return(close, horizon=horizon)

    # drop last horizon days with all-nan fwd
    valid_idx = factor.index.intersection(fwd.dropna(how="all").index)
    factor = factor.loc[valid_idx]
    fwd = fwd.loc[valid_idx]

    ic_daily = rank_ic_series(factor, fwd)
    no_idx = nonoverlapping_index(factor.index, step=horizon)
    ic_no = rank_ic_series(factor.loc[no_idx], fwd.loc[no_idx])

    summ = ic_summary(ic_no)
    direction_pre = int(np.sign(summ["ic_mean"])) if pd.notna(summ["ic_mean"]) and summ["ic_mean"] != 0 else 1
    signed = factor * direction_pre

    qrets = quantile_returns(signed, fwd, no_idx, n_quantiles=crit.n_quantiles)
    qstats = quantile_stats(qrets)
    ls = long_short_backtest(
        signed, fwd, no_idx, n_quantiles=crit.n_quantiles, cost_bps=cost_bps
    )

    metrics: Dict[str, Any] = {}
    metrics.update(summ)
    metrics.update(qstats)
    metrics["ic_mean_daily"] = float(ic_daily.dropna().mean()) if ic_daily.notna().any() else np.nan
    metrics["subperiod_sign_ratio"] = subperiod_sign_ratio(ic_no)
    metrics["half_sample_sign_match"] = half_sample_sign_match(ic_no)
    metrics["rolling_icir_pos_ratio"] = rolling_icir_pos_ratio(
        ic_no, window=crit.rolling_icir_window
    )
    metrics["ls_sharpe"] = ls["ls_sharpe"]
    metrics["ls_cagr"] = ls["ls_cagr"]
    metrics["ls_max_drawdown"] = ls["ls_max_drawdown"]
    metrics["ls_total_return"] = ls["ls_total_return"]
    metrics["ls_win_rate"] = ls["ls_win_rate"]
    metrics["avg_turnover"] = ls["avg_turnover"]
    # map n obs to min_months gate: require enough non-overlap points (~36 ≈ 1.5y)
    metrics["n"] = summ["n"]
    metrics["n_months"] = summ["n"]

    decision = decide_admission(name, metrics, criteria=crit)
    metrics["direction"] = decision.direction
    metrics["hit_rate_adj"] = decision.metrics.get("hit_rate_adj")

    return Alpha5DResult(
        name=name,
        metrics=metrics,
        decision=decision,
        ic_daily=ic_daily,
        ic_nonoverlap=ic_no,
        quantile_rets=qrets,
        equity=ls["equity"],
    )


def evaluate_universe_5d(
    factors: Mapping[str, pd.DataFrame],
    close: pd.DataFrame,
    *,
    horizon: int = 5,
    criteria: Optional[AdmissionCriteria] = None,
    cost_bps: float = 5.0,
) -> Dict[str, Alpha5DResult]:
    return {
        name: evaluate_alpha_5d(
            name, fac, close, horizon=horizon, criteria=criteria, cost_bps=cost_bps
        )
        for name, fac in factors.items()
    }


def summary_table(results: Mapping[str, Alpha5DResult]) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        rows.append(
            {
                "factor": name,
                "admitted": r.decision.admitted,
                "direction": r.decision.direction,
                "ic_mean": r.metrics.get("ic_mean"),
                "icir": r.metrics.get("icir"),
                "ic_tstat": r.metrics.get("ic_tstat"),
                "hit_rate_adj": r.metrics.get("hit_rate_adj"),
                "ic_mean_daily": r.metrics.get("ic_mean_daily"),
                "subperiod_sign_ratio": r.metrics.get("subperiod_sign_ratio"),
                "half_sample_sign_match": r.metrics.get("half_sample_sign_match"),
                "rolling_icir_pos_ratio": r.metrics.get("rolling_icir_pos_ratio"),
                "q_monotonicity": r.metrics.get("q_monotonicity"),
                "q_spread": r.metrics.get("q_spread"),
                "ls_sharpe": r.metrics.get("ls_sharpe"),
                "ls_cagr": r.metrics.get("ls_cagr"),
                "ls_max_drawdown": r.metrics.get("ls_max_drawdown"),
                "avg_turnover": r.metrics.get("avg_turnover"),
                "n": r.metrics.get("n"),
                "n_fail": len(r.decision.reject_reasons),
                "reject_summary": "; ".join(r.decision.reject_reasons[:2]),
            }
        )
    df = pd.DataFrame(rows).set_index("factor")
    return df.sort_values(["admitted", "icir"], ascending=[False, False])
