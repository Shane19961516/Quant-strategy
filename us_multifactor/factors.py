# -*- coding: utf-8 -*-
"""Six-category factor library for US equities."""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

CATEGORIES = [
    "momentum",
    "profitability",
    "quality",
    "size",
    "stability",
    "valuation",
]


def _cs_rank(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True, method="average")


def _cs_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mu = df.mean(axis=1)
    sd = df.std(axis=1).replace(0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0)


def _winsor(df: pd.DataFrame, q: float = 0.02) -> pd.DataFrame:
    lo = df.quantile(q, axis=1)
    hi = df.quantile(1 - q, axis=1)
    return df.clip(lower=lo, upper=hi, axis=0)


def to_weekly(prices: pd.DataFrame, how: str = "last") -> pd.DataFrame:
    return prices.resample("W-FRI").last() if how == "last" else prices.resample("W-FRI").sum()


def build_price_factors(
    adj_close: pd.DataFrame,
    volume: Optional[pd.DataFrame] = None,
    spy: Optional[pd.Series] = None,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Candidate factors by category from prices (stocks as columns, dates index)."""
    px = adj_close.sort_index().ffill(limit=5)
    rets = px.pct_change()
    out: Dict[str, Dict[str, pd.DataFrame]] = {c: {} for c in CATEGORIES}

    # ----- Momentum -----
    out["momentum"]["mom_1w"] = px / px.shift(5) - 1.0
    out["momentum"]["mom_1m"] = px / px.shift(21) - 1.0
    out["momentum"]["mom_3m"] = px / px.shift(63) - 1.0
    out["momentum"]["mom_6m"] = px / px.shift(126) - 1.0
    out["momentum"]["mom_12m"] = px / px.shift(252) - 1.0
    out["momentum"]["mom_12_1"] = px.shift(21) / px.shift(252) - 1.0
    out["momentum"]["mom_accel"] = (px / px.shift(63) - 1.0) - (px / px.shift(252) - 1.0)
    vol21 = rets.rolling(21).std()
    out["momentum"]["mom_riskadj_3m"] = (px / px.shift(63) - 1.0) / vol21.replace(0, np.nan)
    # 52w high proximity
    roll_max = px.rolling(252).max()
    out["momentum"]["near_52w_high"] = px / roll_max
    if spy is not None:
        spy_ret = spy.pct_change()
        # residual 6m momentum vs SPY
        beta = rets.rolling(126).cov(spy_ret) / spy_ret.rolling(126).var().replace(0, np.nan)
        resid = rets.sub(beta.mul(spy_ret, axis=0), axis=0)
        out["momentum"]["resid_mom_6m"] = resid.rolling(126).sum()

    # ----- Size (small = high score later via sign) -----
    # relative price level + dollar volume as size proxies; mcap filled later
    out["size"]["neg_log_price"] = -np.log(px.replace(0, np.nan))
    if volume is not None:
        vol = volume.reindex_like(px).fillna(0.0)
        dollar = (vol * px).rolling(21).mean()
        out["size"]["neg_log_dollar_vol"] = -np.log(dollar.replace(0, np.nan))
        out["size"]["neg_log_volume"] = -np.log(vol.rolling(21).mean().replace(0, np.nan))
    out["size"]["inv_price_rank"] = 1.0 - _cs_rank(px)

    # ----- Stability (higher = more stable after sign flip in scoring) -----
    out["stability"]["inv_vol_1m"] = -rets.rolling(21).std()
    out["stability"]["inv_vol_3m"] = -rets.rolling(63).std()
    out["stability"]["inv_vol_6m"] = -rets.rolling(126).std()
    downside = rets.clip(upper=0)
    out["stability"]["inv_downside_vol"] = -downside.rolling(63).std()
    roll_max = px.rolling(126).max()
    dd = px / roll_max - 1.0
    out["stability"]["inv_maxdd_6m"] = -dd.rolling(126).min().abs()  # less negative DD → higher after?
    # actually dd.min() is most negative; abs then negate → prefer small DD
    out["stability"]["inv_maxdd_6m"] = dd.rolling(126).min()  # closer to 0 is better (less neg)
    if spy is not None:
        spy_ret = spy.pct_change()
        var = spy_ret.rolling(126).var().replace(0, np.nan)
        beta = rets.rolling(126).cov(spy_ret).div(var, axis=0)
        out["stability"]["inv_beta"] = -beta
        resid = rets.sub(beta.mul(spy_ret, axis=0), axis=0)
        out["stability"]["inv_idio_vol"] = -resid.rolling(63).std()
    out["stability"]["inv_range_vol"] = -(
        (px.rolling(21).max() - px.rolling(21).min()) / px.rolling(21).mean()
    )

    return out


def attach_fundamental_factors(
    factor_map: Dict[str, Dict[str, pd.DataFrame]],
    adj_close: pd.DataFrame,
    fundamentals: pd.DataFrame,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Broadcast latest fundamentals across dates (with caution note in README).

    Yahoo snapshot fundamentals are current. We treat them as slow characteristics
    and only enable them after ``fund_start`` (last 4y) via NaNs before that date
    to avoid pretending we had 2015 knowledge of 2026 ratios. For research we
    still allow full-sample screens when ``broadcast_all=True`` in pipeline.
    """
    tickers = [c for c in adj_close.columns if c in fundamentals.index]
    fund = fundamentals.reindex(tickers)

    def _panel(series: pd.Series) -> pd.DataFrame:
        arr = np.tile(series.to_numpy(), (len(adj_close.index), 1))
        return pd.DataFrame(arr, index=adj_close.index, columns=series.index)

    # Size with market cap
    if "marketCap" in fund.columns:
        mcap = fund["marketCap"]
        # historical mcap proxy: scale by price / last price
        last_px = adj_close[tickers].ffill().iloc[-1]
        shares = mcap / last_px.replace(0, np.nan)
        hist_mcap = adj_close[tickers].mul(shares, axis=1)
        factor_map["size"]["neg_log_mcap"] = -np.log(hist_mcap.replace(0, np.nan))
        factor_map["size"]["neg_mcap_rank"] = 1.0 - _cs_rank(hist_mcap)

    # Profitability
    mapping_prof = {
        "roe": "returnOnEquity",
        "roa": "returnOnAssets",
        "gross_margin": "grossMargins",
        "op_margin": "operatingMargins",
        "profit_margin": "profitMargins",
        "roe_q": "roe_q",
        "roa_q": "roa_q",
        "ni_growth_q": "ni_growth_q",
        "rev_growth": "revenueGrowth",
        "earn_growth": "earningsQuarterlyGrowth",
        "cfo_assets": "cfo_assets",
    }
    for name, col in mapping_prof.items():
        if col in fund.columns:
            factor_map["profitability"][name] = _panel(fund[col]).reindex(columns=adj_close.columns)

    # Quality
    if "debtToEquity" in fund.columns:
        factor_map["quality"]["inv_debt_equity"] = _panel(-fund["debtToEquity"]).reindex(
            columns=adj_close.columns
        )
    if "debt_equity_q" in fund.columns:
        factor_map["quality"]["inv_debt_equity_q"] = _panel(-fund["debt_equity_q"]).reindex(
            columns=adj_close.columns
        )
    if "currentRatio" in fund.columns:
        factor_map["quality"]["current_ratio"] = _panel(fund["currentRatio"]).reindex(
            columns=adj_close.columns
        )
    if "quickRatio" in fund.columns:
        factor_map["quality"]["quick_ratio"] = _panel(fund["quickRatio"]).reindex(
            columns=adj_close.columns
        )
    if "accruals" in fund.columns:
        factor_map["quality"]["inv_accruals"] = _panel(-fund["accruals"]).reindex(
            columns=adj_close.columns
        )
    if "cfo_assets" in fund.columns:
        factor_map["quality"]["cfo_assets"] = _panel(fund["cfo_assets"]).reindex(
            columns=adj_close.columns
        )
    if "heldPercentInsiders" in fund.columns:
        factor_map["quality"]["insider_own"] = _panel(fund["heldPercentInsiders"]).reindex(
            columns=adj_close.columns
        )
    if "shortRatio" in fund.columns:
        factor_map["quality"]["inv_short_ratio"] = _panel(-fund["shortRatio"]).reindex(
            columns=adj_close.columns
        )
    # earnings stability proxy from profitability margins presence
    if "profitMargins" in fund.columns and "returnOnEquity" in fund.columns:
        factor_map["quality"]["roe_margin_blend"] = _panel(
            fund["returnOnEquity"].fillna(0) + fund["profitMargins"].fillna(0)
        ).reindex(columns=adj_close.columns)

    # Valuation (higher yield / lower multiple = better)
    if "trailingPE" in fund.columns:
        pe = fund["trailingPE"].where(fund["trailingPE"] > 0)
        factor_map["valuation"]["earnings_yield"] = _panel(1.0 / pe).reindex(columns=adj_close.columns)
        factor_map["valuation"]["inv_pe"] = _panel(-pe).reindex(columns=adj_close.columns)
    if "priceToBook" in fund.columns:
        pb = fund["priceToBook"].where(fund["priceToBook"] > 0)
        factor_map["valuation"]["book_yield"] = _panel(1.0 / pb).reindex(columns=adj_close.columns)
        factor_map["valuation"]["inv_pb"] = _panel(-pb).reindex(columns=adj_close.columns)
    if "priceToSalesTrailing12Months" in fund.columns:
        ps = fund["priceToSalesTrailing12Months"].where(fund["priceToSalesTrailing12Months"] > 0)
        factor_map["valuation"]["sales_yield"] = _panel(1.0 / ps).reindex(columns=adj_close.columns)
    if "enterpriseToEbitda" in fund.columns:
        eve = fund["enterpriseToEbitda"].where(fund["enterpriseToEbitda"] > 0)
        factor_map["valuation"]["inv_ev_ebitda"] = _panel(-eve).reindex(columns=adj_close.columns)
    if "forwardPE" in fund.columns:
        fpe = fund["forwardPE"].where(fund["forwardPE"] > 0)
        factor_map["valuation"]["forward_earn_yield"] = _panel(1.0 / fpe).reindex(
            columns=adj_close.columns
        )
    # FCF yield
    if "freeCashflow" in fund.columns and "marketCap" in fund.columns:
        fcf_y = fund["freeCashflow"] / fund["marketCap"].replace(0, np.nan)
        factor_map["valuation"]["fcf_yield"] = _panel(fcf_y).reindex(columns=adj_close.columns)

    return factor_map


def resample_factors_weekly(
    factor_map: Dict[str, Dict[str, pd.DataFrame]],
) -> Dict[str, Dict[str, pd.DataFrame]]:
    weekly: Dict[str, Dict[str, pd.DataFrame]] = {c: {} for c in factor_map}
    for cat, fmap in factor_map.items():
        for name, df in fmap.items():
            weekly[cat][name] = df.resample("W-FRI").last()
    return weekly


def lag_factors(
    factor_map: Dict[str, Dict[str, pd.DataFrame]], periods: int = 1
) -> Dict[str, Dict[str, pd.DataFrame]]:
    out: Dict[str, Dict[str, pd.DataFrame]] = {c: {} for c in factor_map}
    for cat, fmap in factor_map.items():
        for name, df in fmap.items():
            out[cat][name] = df.shift(periods)
    return out


def process_factor(
    raw: pd.DataFrame, winsor_q: float = 0.02, zscore: bool = True
) -> pd.DataFrame:
    x = _winsor(raw, winsor_q)
    if zscore:
        x = _cs_zscore(x)
    return x


def rank_ic_series(factor: pd.DataFrame, fwd_ret: pd.DataFrame) -> pd.Series:
    """Vectorized-ish Spearman IC via ranked Pearson."""
    # align
    cols = factor.columns.intersection(fwd_ret.columns)
    f = factor[cols]
    r = fwd_ret[cols].reindex(f.index)
    f_rank = f.rank(axis=1, pct=True)
    r_rank = r.rank(axis=1, pct=True)
    # pearson of ranks per row
    f_demean = f_rank.sub(f_rank.mean(axis=1), axis=0)
    r_demean = r_rank.sub(r_rank.mean(axis=1), axis=0)
    num = (f_demean * r_demean).sum(axis=1)
    den = (f_demean.pow(2).sum(axis=1).pow(0.5) * r_demean.pow(2).sum(axis=1).pow(0.5))
    ic = num / den.replace(0, np.nan)
    valid = f.notna().sum(axis=1)
    ic = ic.where(valid >= 20)
    return ic.rename("rank_ic")


def select_top_factors_per_category(
    weekly_factors: Dict[str, Dict[str, pd.DataFrame]],
    weekly_returns: pd.DataFrame,
    top_n: int = 5,
    min_ic_obs: int = 52,
    end_date: Optional[str] = None,
    already_lagged: bool = True,
) -> Tuple[Dict[str, List[str]], pd.DataFrame, Dict[str, Dict[str, pd.DataFrame]]]:
    """Keep top_n factors per category by |ICIR| with sign aligned to positive IC.

    Parameters
    ----------
    end_date:
        If set, IC/ICIR for ranking uses only observations ``<= end_date``
        (walk-forward hygiene). Signed panels are still built on the full sample.
    already_lagged:
        If True (default), factors are assumed shifted by 1 week already, so IC
        uses same-week forward returns. If False, uses ``returns.shift(-1)``.
    """
    fwd = weekly_returns if already_lagged else weekly_returns.shift(-1)
    rows = []
    selected: Dict[str, List[str]] = {}
    signed_panels: Dict[str, Dict[str, pd.DataFrame]] = {c: {} for c in weekly_factors}
    ic_cut = pd.Timestamp(end_date) if end_date else None

    for cat, fmap in weekly_factors.items():
        cat_rows = []
        for name, raw in fmap.items():
            proc = process_factor(raw)
            ic = rank_ic_series(proc, fwd)
            ic_rank = ic[ic.index <= ic_cut] if ic_cut is not None else ic
            mu = float(ic_rank.mean())
            sd = float(ic_rank.std(ddof=0)) if ic_rank.notna().sum() else np.nan
            icir = mu / sd if sd and sd > 0 else np.nan
            n = float(ic_rank.notna().sum())
            # flip sign if mean IC negative so higher score = higher expected return
            sign = 1.0 if (mu is not None and mu >= 0) else -1.0
            cat_rows.append(
                {
                    "category": cat,
                    "factor": name,
                    "ic_mean": mu,
                    "ic_std": sd,
                    "icir": icir,
                    "abs_icir": abs(icir) if pd.notna(icir) else np.nan,
                    "sign": sign,
                    "n": n,
                }
            )
            signed_panels[cat][name] = proc * sign
        cdf = pd.DataFrame(cat_rows)
        rows.append(cdf)
        cdf = cdf.dropna(subset=["abs_icir"])
        cdf = cdf[cdf["n"] >= min_ic_obs].sort_values("abs_icir", ascending=False)
        selected[cat] = cdf["factor"].head(top_n).tolist()
        # if not enough, take whatever available
        if len(selected[cat]) < top_n:
            selected[cat] = (
                pd.DataFrame(cat_rows)
                .sort_values("abs_icir", ascending=False)["factor"]
                .head(top_n)
                .tolist()
            )

    summary = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return selected, summary, signed_panels
