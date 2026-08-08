# -*- coding: utf-8 -*-
"""Cross-sectional equity factors from monthly returns."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List

import numpy as np
import pandas as pd

from .neutralize import cs_zscore, industry_neutralize, winsorize


FactorFn = Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame]


def _shift_safe(df: pd.DataFrame, periods: int) -> pd.DataFrame:
    return df.T.shift(periods).T


def factor_mom_12_1(returns: pd.DataFrame, industry: pd.DataFrame | None = None) -> pd.DataFrame:
    """12-1 month momentum at month t: compound returns of months t-12..t-1."""
    log_ret = np.log1p(returns)
    # exclude month t, keep prior 12 months
    lagged = log_ret.T.shift(1).T
    roll_12 = lagged.T.rolling(12, min_periods=12).sum().T
    return np.expm1(roll_12)


def factor_rev_1(returns: pd.DataFrame, industry: pd.DataFrame | None = None) -> pd.DataFrame:
    """1-month reversal: negative of last month return."""
    return -returns


def factor_vol_12(returns: pd.DataFrame, industry: pd.DataFrame | None = None) -> pd.DataFrame:
    """Low-volatility: negative trailing 12-month std."""
    vol = returns.T.rolling(12, min_periods=12).std().T
    return -vol


def factor_max_ret(returns: pd.DataFrame, industry: pd.DataFrame | None = None) -> pd.DataFrame:
    """MAX (Bali et al.): negative of max monthly return in past 12 months."""
    mx = returns.T.rolling(12, min_periods=12).max().T
    return -mx


def factor_skew_12(returns: pd.DataFrame, industry: pd.DataFrame | None = None) -> pd.DataFrame:
    """Prefer negative skew (less lottery-like): negative of trailing skew."""
    sk = returns.T.rolling(12, min_periods=12).skew().T
    return -sk


def factor_ind_resid_mom(
    returns: pd.DataFrame, industry: pd.DataFrame
) -> pd.DataFrame:
    """Industry-relative residual of 12-1 momentum."""
    raw = factor_mom_12_1(returns)
    return industry_neutralize(raw, industry)


def factor_ind_mom(
    returns: pd.DataFrame, industry: pd.DataFrame
) -> pd.DataFrame:
    """Own industry equal-weight 6-month momentum (same value within industry)."""
    log_ret = np.log1p(returns)
    roll_6 = np.expm1(log_ret.T.rolling(6, min_periods=6).sum().T)
    # industry average each date
    out = pd.DataFrame(np.nan, index=returns.index, columns=returns.columns)
    for dt in returns.columns:
        ind = industry[dt]
        val = roll_6[dt]
        tmp = pd.DataFrame({"ind": ind, "v": val}).dropna()
        if tmp.empty:
            continue
        means = tmp.groupby("ind")["v"].transform("mean")
        out.loc[means.index, dt] = means.values
    return out


DEFAULT_FACTORS: Dict[str, FactorFn] = {
    "mom_12_1": factor_mom_12_1,
    "rev_1": factor_rev_1,
    "vol_12": factor_vol_12,
    "max_ret": factor_max_ret,
    "skew_12": factor_skew_12,
    "ind_resid_mom": factor_ind_resid_mom,
}

# A-share sample (2010–2019) favors reversal / low-vol / MAX / skew;
# classic 12-1 momentum is weak or negative — kept available but not default.
DEFAULT_FACTOR_NAMES = ["rev_1", "vol_12", "max_ret", "skew_12"]


def build_factor_panel(
    returns: pd.DataFrame,
    industry: pd.DataFrame,
    factor_names: Iterable[str] | None = None,
    winsor_q: float = 0.01,
    neutralize_industry: bool = True,
    zscore: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Compute processed factor matrices (stocks x dates).

    Higher score = more desirable long exposure for every factor.
    """
    names: List[str] = list(factor_names) if factor_names is not None else list(DEFAULT_FACTORS)
    panels: Dict[str, pd.DataFrame] = {}
    for name in names:
        if name not in DEFAULT_FACTORS:
            raise KeyError(f"Unknown factor: {name}")
        raw = DEFAULT_FACTORS[name](returns, industry)
        # signal at month t uses data through t; trade next month → shift +1
        # so backtest can use weight[t] * return[t] with no look-ahead
        sig = _shift_safe(raw, 1)
        if winsor_q and winsor_q > 0:
            sig = winsorize(sig, winsor_q)
        if neutralize_industry and name != "ind_resid_mom":
            # ind_resid_mom already neutralized on raw; still z-score after shift
            sig = industry_neutralize(sig, industry)
        if zscore:
            sig = cs_zscore(sig)
        panels[name] = sig
    return panels
