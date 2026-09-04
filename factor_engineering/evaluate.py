# -*- coding: utf-8 -*-
"""Factor evaluation: IC, decay, quantile, turnover, correlation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

import numpy as np
import pandas as pd


def rank_ic_series(factor: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """Spearman IC each date (factor already lagged vs same-column return)."""
    ics = []
    for dt in returns.columns:
        if dt not in factor.columns:
            ics.append(np.nan)
            continue
        df = pd.concat([factor[dt], returns[dt]], axis=1, keys=["f", "r"]).dropna()
        if len(df) < 5:
            ics.append(np.nan)
        else:
            ics.append(float(df["f"].corr(df["r"], method="spearman")))
    return pd.Series(ics, index=returns.columns, name="rank_ic")


def pearson_ic_series(factor: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    ics = []
    for dt in returns.columns:
        if dt not in factor.columns:
            ics.append(np.nan)
            continue
        df = pd.concat([factor[dt], returns[dt]], axis=1, keys=["f", "r"]).dropna()
        if len(df) < 5:
            ics.append(np.nan)
        else:
            ics.append(float(df["f"].corr(df["r"], method="pearson")))
    return pd.Series(ics, index=returns.columns, name="pearson_ic")


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
    tstat = mu / (sd / np.sqrt(n)) if sd > 0 and n > 0 else np.nan
    return {
        "ic_mean": mu,
        "ic_std": sd,
        "icir": mu / sd if sd > 0 else np.nan,
        "ic_pos_ratio": float((ic > 0).mean()),
        "ic_tstat": float(tstat),
        "n": n,
    }


def ic_decay(
    factor: pd.DataFrame,
    returns: pd.DataFrame,
    horizons: tuple[int, ...] = (0, 1, 2, 3, 6, 12),
) -> pd.Series:
    """Mean rank-IC of factor[t] vs return[t+h] for horizons h (0 = same month).

    Factors are already lagged once in build; horizon 0 is the tradable IC.
    Horizon h>0 shifts returns forward (factor vs future months).
    """
    out = {}
    for h in horizons:
        if h == 0:
            ic = rank_ic_series(factor, returns)
        else:
            fut = returns.T.shift(-h).T
            ic = rank_ic_series(factor, fut)
        out[h] = float(ic.mean())
    return pd.Series(out, name="ic_decay")


def factor_autocorr(factor: pd.DataFrame, lags: tuple[int, ...] = (1, 3, 6, 12)) -> pd.Series:
    """Mean cross-sectional Spearman autocorr of factor ranks across lags."""
    out = {}
    for lag in lags:
        cors = []
        cols = list(factor.columns)
        for i in range(lag, len(cols)):
            a = factor[cols[i - lag]]
            b = factor[cols[i]]
            df = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
            if len(df) < 10:
                continue
            cors.append(float(df["a"].corr(df["b"], method="spearman")))
        out[lag] = float(np.nanmean(cors)) if cors else np.nan
    return pd.Series(out, name="autocorr")


def quantile_returns(
    scores: pd.DataFrame,
    returns: pd.DataFrame,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Equal-weight return of each score quantile by date. Q1=low, Qn=high."""
    labels = [f"Q{i+1}" for i in range(n_quantiles)]
    out = pd.DataFrame(index=returns.columns, columns=labels, dtype=float)
    for dt in returns.columns:
        if dt not in scores.columns:
            continue
        df = pd.concat([scores[dt], returns[dt]], axis=1, keys=["s", "r"]).dropna()
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


def quantile_summary(qrets: pd.DataFrame) -> Dict[str, float]:
    """Monotonicity / long-short spread from quantile return panel."""
    means = qrets.mean()
    if means.isna().all():
        return {
            "q_spread": np.nan,
            "q_monotonicity": np.nan,
            "top_minus_bottom": np.nan,
        }
    vals = means.dropna().values.astype(float)
    if len(vals) < 2:
        return {"q_spread": np.nan, "q_monotonicity": np.nan, "top_minus_bottom": np.nan}
    spread = float(vals[-1] - vals[0])
    # fraction of adjacent steps that move in the same direction as overall spread
    diffs = np.diff(vals)
    if abs(spread) < 1e-12:
        mono = np.nan
    else:
        mono = float((np.sign(diffs) == np.sign(spread)).mean())
    return {
        "q_spread": spread,
        "q_monotonicity": mono,
        "top_minus_bottom": spread,
        **{f"mean_{c}": float(means[c]) for c in means.index if pd.notna(means[c])},
    }


def factor_turnover(factor: pd.DataFrame, n_quantiles: int = 5) -> pd.Series:
    """One-way turnover of long top-quantile / short bottom-quantile book."""
    dates = list(factor.columns)
    to = []
    prev = None
    for dt in dates:
        s = factor[dt].dropna()
        w = pd.Series(0.0, index=factor.index)
        if len(s) >= n_quantiles * 3:
            try:
                q = pd.qcut(s, n_quantiles, labels=False, duplicates="drop")
                qmax, qmin = int(q.max()), int(q.min())
                if qmax != qmin:
                    long = s[q == qmax]
                    short = s[q == qmin]
                    w.loc[long.index] = 1.0 / len(long)
                    w.loc[short.index] = -1.0 / len(short)
            except ValueError:
                pass
        if prev is None:
            to.append(np.nan)
        else:
            to.append(0.5 * float((w - prev).abs().sum()))
        prev = w
    return pd.Series(to, index=dates, name="turnover")


def pairwise_factor_corr(
    factor_panels: Mapping[str, pd.DataFrame],
    method: str = "spearman",
) -> pd.DataFrame:
    """Average cross-sectional correlation between factors across dates."""
    names = list(factor_panels.keys())
    mat = pd.DataFrame(np.eye(len(names)), index=names, columns=names, dtype=float)
    if len(names) < 2:
        return mat
    # use common dates
    dates = None
    for f in factor_panels.values():
        dates = f.columns if dates is None else dates.intersection(f.columns)
    cors = { (a, b): [] for i, a in enumerate(names) for b in names[i + 1 :] }
    for dt in dates:
        frame = pd.DataFrame({n: factor_panels[n][dt] for n in names})
        c = frame.corr(method=method)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                v = c.loc[a, b]
                if pd.notna(v):
                    cors[(a, b)].append(float(v))
    for (a, b), vals in cors.items():
        m = float(np.mean(vals)) if vals else np.nan
        mat.loc[a, b] = m
        mat.loc[b, a] = m
    return mat


@dataclass
class FactorEval:
    name: str
    ic: pd.Series
    pearson_ic: pd.Series
    summary: Dict[str, float]
    decay: pd.Series
    autocorr: pd.Series
    quantile_rets: pd.DataFrame
    quantile_stats: Dict[str, float]
    turnover: pd.Series
    extras: Dict[str, float] = field(default_factory=dict)

    def scorecard_row(self) -> Dict[str, float]:
        row = dict(self.summary)
        row.update(
            {
                "avg_turnover": float(self.turnover.dropna().mean())
                if self.turnover.notna().any()
                else np.nan,
                "ic_decay_1": float(self.decay.get(1, np.nan)),
                "ic_decay_3": float(self.decay.get(3, np.nan)),
                "autocorr_1": float(self.autocorr.get(1, np.nan)),
                "q_spread": self.quantile_stats.get("q_spread", np.nan),
                "q_monotonicity": self.quantile_stats.get("q_monotonicity", np.nan),
            }
        )
        row.update(self.extras)
        return row


def evaluate_factor(
    name: str,
    factor: pd.DataFrame,
    returns: pd.DataFrame,
    n_quantiles: int = 5,
) -> FactorEval:
    ic = rank_ic_series(factor, returns)
    pic = pearson_ic_series(factor, returns)
    summary = ic_summary(ic)
    decay = ic_decay(factor, returns)
    ac = factor_autocorr(factor)
    qrets = quantile_returns(factor, returns, n_quantiles=n_quantiles)
    qstats = quantile_summary(qrets)
    to = factor_turnover(factor, n_quantiles=n_quantiles)
    return FactorEval(
        name=name,
        ic=ic,
        pearson_ic=pic,
        summary=summary,
        decay=decay,
        autocorr=ac,
        quantile_rets=qrets,
        quantile_stats=qstats,
        turnover=to,
    )


def evaluate_factor_universe(
    factor_panels: Mapping[str, pd.DataFrame],
    returns: pd.DataFrame,
    n_quantiles: int = 5,
) -> Dict[str, FactorEval]:
    return {
        name: evaluate_factor(name, panel, returns, n_quantiles=n_quantiles)
        for name, panel in factor_panels.items()
    }


def evals_to_table(evals: Mapping[str, FactorEval]) -> pd.DataFrame:
    rows = []
    for name, ev in evals.items():
        row = {"factor": name, **ev.scorecard_row()}
        rows.append(row)
    return pd.DataFrame(rows).set_index("factor")
