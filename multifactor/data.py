# -*- coding: utf-8 -*-
"""Market panel loaders for the multi-factor strategy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_wide_csv(path: Path, encoding: str = "gbk") -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, encoding=encoding)
    df.columns = pd.to_datetime(df.columns)
    df.index = df.index.astype(str)
    return df


def is_a_share(code: str) -> bool:
    """Keep main-board / SME / ChiNext A-shares; drop index codes."""
    code = str(code)
    if code.endswith(".SZ"):
        return code[:2] in {"00", "30"}
    if code.endswith(".SH"):
        return code.startswith("6")
    return False


@dataclass
class MarketPanel:
    """Aligned monthly returns, industry labels, optional CSI300 weights."""

    returns: pd.DataFrame  # stocks x dates, decimal monthly returns
    industry: pd.DataFrame  # stocks x dates
    index_weight: Optional[pd.DataFrame] = None  # stocks x dates, percent or weight
    benchmark: Optional[pd.Series] = None  # monthly benchmark return


def load_market_panel(
    root: Path | str | None = None,
    start: str | None = "2009-01-01",
    end: str | None = None,
    universe: str = "intersect",
) -> MarketPanel:
    """Load monthly pct_chg + Zhongxin industry (+ CSI300 weight if present).

    Parameters
    ----------
    universe:
        ``intersect`` keeps stocks present in both returns and industry panels.
        ``csi300`` keeps stocks that appear in CSI300 weight file at least once.
    """
    root = Path(root) if root is not None else REPO_ROOT
    returns = _read_wide_csv(root / "quote_data" / "pct_chg_M.csv")
    industry = _read_wide_csv(root / "quote_data" / "industry_zx.csv")

    a_mask = [is_a_share(c) for c in returns.index]
    returns = returns.loc[a_mask]

    # Align date range
    if start is not None:
        returns = returns.loc[:, returns.columns >= pd.Timestamp(start)]
        industry = industry.loc[:, industry.columns >= pd.Timestamp(start)]
    if end is not None:
        returns = returns.loc[:, returns.columns <= pd.Timestamp(end)]
        industry = industry.loc[:, industry.columns <= pd.Timestamp(end)]

    # Map industry columns to month-end of returns by nearest month label
    industry = _align_industry_to_returns(industry, returns.columns)

    common_stocks = returns.index.intersection(industry.index)
    returns = returns.loc[common_stocks]
    industry = industry.loc[common_stocks]

    index_weight = None
    weight_path = root / "index_weight" / "000300.csv"
    if weight_path.exists():
        w = pd.read_csv(weight_path, encoding="gbk")
        w = w.rename(columns={w.columns[0]: "code"}).set_index("code")
        w.index = w.index.astype(str)
        w.columns = pd.to_datetime(w.columns)
        w = w.reindex(columns=returns.columns)
        # weights are in percent in source file
        w = w.apply(pd.to_numeric, errors="coerce") / 100.0
        index_weight = w.reindex(index=returns.index)

    if universe == "csi300" and index_weight is not None:
        keep = index_weight.notna().any(axis=1)
        returns = returns.loc[keep]
        industry = industry.loc[keep]
        index_weight = index_weight.loc[keep]

    benchmark = _monthly_benchmark(root, returns.columns)
    return MarketPanel(
        returns=returns,
        industry=industry,
        index_weight=index_weight,
        benchmark=benchmark,
    )


def _align_industry_to_returns(
    industry: pd.DataFrame, ret_dates: pd.DatetimeIndex
) -> pd.DataFrame:
    """Match industry snapshot dates to return month-ends (same calendar month)."""
    ind = industry.copy()
    # Build month-key map: YYYY-MM -> industry column
    month_to_col = {pd.Timestamp(c).strftime("%Y-%m"): c for c in ind.columns}
    aligned = {}
    for d in ret_dates:
        key = pd.Timestamp(d).strftime("%Y-%m")
        if key in month_to_col:
            aligned[d] = ind[month_to_col[key]]
        else:
            # fallback: last industry col on or before d
            prior = [c for c in ind.columns if c <= d]
            aligned[d] = ind[prior[-1]] if prior else pd.Series(np.nan, index=ind.index)
    out = pd.DataFrame(aligned)
    out.index = ind.index
    return out


def _monthly_benchmark(root: Path, dates: pd.DatetimeIndex) -> pd.Series:
    path = root / "quote_data" / "000300.SH.csv"
    if not path.exists():
        return pd.Series(dtype=float)
    bm = pd.read_csv(path, encoding="gbk")
    bm["date"] = pd.to_datetime(bm["date"])
    bm = bm.set_index("date").sort_index()
    # compound daily pct_change into calendar-month returns
    if "pct_change" in bm.columns:
        daily = bm["pct_change"].astype(float)
        monthly = (1.0 + daily).resample("ME").prod() - 1.0
    else:
        monthly = bm["close"].resample("ME").last().pct_change()
    # align to panel month labels (may be month-end trade dates)
    mapped = []
    for d in dates:
        key = pd.Timestamp(d).strftime("%Y-%m")
        hit = monthly[monthly.index.strftime("%Y-%m") == key]
        mapped.append(float(hit.iloc[-1]) if len(hit) else np.nan)
    return pd.Series(mapped, index=dates, name="benchmark")


def generate_synthetic_panel(
    n_stocks: int = 120,
    n_months: int = 96,
    n_industries: int = 10,
    seed: int = 42,
) -> MarketPanel:
    """Synthetic panel with planted momentum / low-vol edges for unit tests."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2015-01-31", periods=n_months, freq="ME")
    codes = [f"{i:06d}.SZ" for i in range(1, n_stocks + 1)]
    inds = [f"IND{i % n_industries:02d}" for i in range(n_stocks)]

    # latent factor exposures
    mom_true = rng.normal(0, 1, size=n_stocks)
    vol_true = rng.uniform(0.03, 0.12, size=n_stocks)

    rets = np.zeros((n_stocks, n_months))
    for t in range(n_months):
        noise = rng.normal(0, 1, size=n_stocks) * vol_true
        # low-vol premium in contemporaneous return
        edge = -0.20 * (vol_true - vol_true.mean())
        # slow latent momentum state also pays
        edge = edge + 0.010 * mom_true
        rets[:, t] = edge + noise
        mom_true = 0.92 * mom_true + 0.08 * rng.normal(0, 1, size=n_stocks)

    # inject tradable 12-1 momentum edge into subsequent months
    # raw mom at t uses returns[t-12..t-1]; after pipeline lag, score[t]≈mom[t-1]
    # so plant edge in return[t] using mom known at t-1 (returns[t-13..t-2])
    log_rets = np.log1p(np.clip(rets, -0.99, None))
    for t in range(14, n_months):
        window = log_rets[:, t - 13 : t - 1].sum(axis=1)  # t-13..t-2
        mom = np.expm1(window)
        mu, sd = mom.mean(), mom.std() + 1e-12
        rets[:, t] = rets[:, t] + 0.03 * ((mom - mu) / sd)
        log_rets[:, t] = np.log1p(np.clip(rets[:, t], -0.99, None))

    returns = pd.DataFrame(rets, index=codes, columns=dates)
    industry = pd.DataFrame(
        np.tile(np.array(inds)[:, None], (1, n_months)),
        index=codes,
        columns=dates,
    )
    # fake equal index weights for first 60 names
    w = pd.DataFrame(np.nan, index=codes, columns=dates)
    w.iloc[:60] = 1.0 / 60.0
    bm = returns.mean(axis=0)
    bm.name = "benchmark"
    return MarketPanel(returns=returns, industry=industry, index_weight=w, benchmark=bm)
