# -*- coding: utf-8 -*-
"""Cross-sectional equity factor library (price-volume, monthly)."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .process import process_factor

FactorFn = Callable[[pd.DataFrame, Optional[pd.DataFrame]], pd.DataFrame]


def factor_mom_12_1(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """12-1 month momentum: compound returns of months t-12..t-1 (at month t raw)."""
    log_ret = np.log1p(returns.clip(lower=-0.999))
    lagged = log_ret.T.shift(1).T
    roll_12 = lagged.T.rolling(12, min_periods=12).sum().T
    return np.expm1(roll_12)


def factor_mom_6(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """6-month momentum (including current month in raw; lag applied in process)."""
    log_ret = np.log1p(returns.clip(lower=-0.999))
    return np.expm1(log_ret.T.rolling(6, min_periods=6).sum().T)


def factor_mom_3(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """3-month momentum."""
    log_ret = np.log1p(returns.clip(lower=-0.999))
    return np.expm1(log_ret.T.rolling(3, min_periods=3).sum().T)


def factor_rev_1(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """1-month reversal: negative of last month return."""
    return -returns


def factor_vol_12(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Low-volatility: negative trailing 12-month std."""
    return -returns.T.rolling(12, min_periods=12).std().T


def factor_vol_6(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Low-volatility (6m)."""
    return -returns.T.rolling(6, min_periods=6).std().T


def factor_max_ret(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """MAX (Bali et al.): negative of max monthly return in past 12 months."""
    return -returns.T.rolling(12, min_periods=12).max().T


def factor_skew_12(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Prefer negative skew (less lottery-like)."""
    return -returns.T.rolling(12, min_periods=12).skew().T


def factor_downside_vol(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Negative downside volatility (prefer lower left-tail risk)."""
    neg = returns.where(returns < 0, 0.0)
    dvol = neg.T.rolling(12, min_periods=12).std().T
    return -dvol


def factor_ind_resid_mom(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Industry-relative residual of 12-1 momentum (raw already demeaned)."""
    from .process import industry_neutralize

    raw = factor_mom_12_1(returns)
    if industry is None:
        return raw
    return industry_neutralize(raw, industry)


def factor_liquidity_proxy(
    returns: pd.DataFrame, industry: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Amihud-like proxy from |ret| / (1+|ret|) smoothed — prefer lower illiquidity.

    Without volume, use trailing mean absolute return as illiquidity proxy
    (high |ret| ↔ less liquid / more jumpy); score = negative of that.
    """
    illiq = returns.abs().T.rolling(6, min_periods=6).mean().T
    return -illiq


FACTOR_REGISTRY: Dict[str, FactorFn] = {
    "mom_12_1": factor_mom_12_1,
    "mom_6": factor_mom_6,
    "mom_3": factor_mom_3,
    "rev_1": factor_rev_1,
    "vol_12": factor_vol_12,
    "vol_6": factor_vol_6,
    "max_ret": factor_max_ret,
    "skew_12": factor_skew_12,
    "downside_vol": factor_downside_vol,
    "ind_resid_mom": factor_ind_resid_mom,
    "liquidity_proxy": factor_liquidity_proxy,
}

FACTOR_META: Dict[str, Dict[str, str]] = {
    "mom_12_1": {"family": "momentum", "desc": "12-1月动量"},
    "mom_6": {"family": "momentum", "desc": "6月动量"},
    "mom_3": {"family": "momentum", "desc": "3月动量"},
    "rev_1": {"family": "reversal", "desc": "1月反转"},
    "vol_12": {"family": "volatility", "desc": "低波动(12月)"},
    "vol_6": {"family": "volatility", "desc": "低波动(6月)"},
    "max_ret": {"family": "lottery", "desc": "MAX因子(取负)"},
    "skew_12": {"family": "lottery", "desc": "负偏度偏好"},
    "downside_vol": {"family": "volatility", "desc": "低下行波动"},
    "ind_resid_mom": {"family": "momentum", "desc": "行业中性残差动量"},
    "liquidity_proxy": {"family": "liquidity", "desc": "流动性代理(低跳跃)"},
}

# A-share monthly sample typically favors short-term reversal / low-vol / lottery
DEFAULT_FACTOR_NAMES = [
    "rev_1",
    "vol_12",
    "max_ret",
    "skew_12",
    "downside_vol",
    "mom_12_1",
    "mom_6",
    "liquidity_proxy",
]


def build_factor_panel(
    returns: pd.DataFrame,
    industry: pd.DataFrame,
    factor_names: Iterable[str] | None = None,
    *,
    lag: int = 1,
    winsor_q: float = 0.01,
    neutralize_industry: bool = True,
    standardize: str = "zscore",
) -> Dict[str, pd.DataFrame]:
    """Compute processed factor matrices (stocks x dates).

    Higher score = more desirable long exposure for every factor.
    """
    names: List[str] = (
        list(factor_names) if factor_names is not None else list(DEFAULT_FACTOR_NAMES)
    )
    panels: Dict[str, pd.DataFrame] = {}
    for name in names:
        if name not in FACTOR_REGISTRY:
            raise KeyError(f"Unknown factor: {name}. Known: {sorted(FACTOR_REGISTRY)}")
        raw = FACTOR_REGISTRY[name](returns, industry)
        # ind_resid_mom already industry-neutralized on raw; skip second pass
        neut = neutralize_industry and name != "ind_resid_mom"
        panels[name] = process_factor(
            raw,
            industry,
            lag=lag,
            winsor_q=winsor_q,
            neutralize=neut,
            standardize=standardize,
        )
    return panels
