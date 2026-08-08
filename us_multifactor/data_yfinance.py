# -*- coding: utf-8 -*-
"""S&P 500 universe + OHLCV/fundamental panels via yfinance."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "us_multifactor_data"
SPX_LIST_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
)


def ensure_data_dir(path: Path | None = None) -> Path:
    d = Path(path) if path is not None else DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_sp500_tickers(cache_dir: Path | None = None) -> pd.DataFrame:
    cache_dir = ensure_data_dir(cache_dir)
    cache = cache_dir / "sp500_constituents.csv"
    if cache.exists():
        df = pd.read_csv(cache)
    else:
        df = pd.read_csv(SPX_LIST_URL)
        df.to_csv(cache, index=False)
    df = df.copy()
    df["Symbol"] = df["Symbol"].astype(str).str.replace(".", "-", regex=False)
    return df


def _normalize_yf_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance multi-ticker: level 0 = field
        if "Adj Close" in df.columns.get_level_values(0):
            out = df["Adj Close"].copy()
        elif "Close" in df.columns.get_level_values(0):
            out = df["Close"].copy()
        else:
            out = df.droplevel(0, axis=1)
    else:
        out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out.sort_index()


def download_price_panel(
    tickers: Iterable[str],
    start: str = "2015-01-01",
    end: Optional[str] = None,
    cache_dir: Path | None = None,
    force: bool = False,
) -> Dict[str, pd.DataFrame]:
    """Download OHLCV for tickers + SPY; cache as parquet panels."""
    cache_dir = ensure_data_dir(cache_dir)
    tag = f"{start}_{end or 'latest'}"
    paths = {
        "adj_close": cache_dir / f"adj_close_{tag}.parquet",
        "close": cache_dir / f"close_{tag}.parquet",
        "high": cache_dir / f"high_{tag}.parquet",
        "low": cache_dir / f"low_{tag}.parquet",
        "volume": cache_dir / f"volume_{tag}.parquet",
    }
    if not force and all(p.exists() for p in paths.values()):
        return {k: pd.read_parquet(v) for k, v in paths.items()}

    tickers = sorted(set(list(tickers) + ["SPY"]))
    # chunk to avoid Yahoo throttling
    chunks = [tickers[i : i + 50] for i in range(0, len(tickers), 50)]
    frames: Dict[str, List[pd.DataFrame]] = {
        "Adj Close": [],
        "Close": [],
        "High": [],
        "Low": [],
        "Volume": [],
    }
    for i, chunk in enumerate(chunks):
        print(f"[prices] chunk {i+1}/{len(chunks)} ({len(chunk)} names)")
        raw = yf.download(
            chunk,
            start=start,
            end=end,
            auto_adjust=False,
            threads=True,
            group_by="column",
            progress=False,
        )
        if raw.empty:
            time.sleep(1.0)
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            for field in frames:
                if field in raw.columns.get_level_values(0):
                    part = raw[field].copy()
                    part.index = pd.to_datetime(part.index).tz_localize(None)
                    frames[field].append(part)
        else:
            # single ticker edge case
            t = chunk[0]
            tmp = raw.copy()
            tmp.index = pd.to_datetime(tmp.index).tz_localize(None)
            for field in frames:
                if field in tmp.columns:
                    frames[field].append(tmp[[field]].rename(columns={field: t}))
        time.sleep(0.4)

    def _join(parts: List[pd.DataFrame]) -> pd.DataFrame:
        if not parts:
            return pd.DataFrame()
        out = pd.concat(parts, axis=1)
        out = out.loc[:, ~out.columns.duplicated()].sort_index()
        return out

    panels = {
        "adj_close": _join(frames["Adj Close"]),
        "close": _join(frames["Close"]),
        "high": _join(frames["High"]),
        "low": _join(frames["Low"]),
        "volume": _join(frames["Volume"]),
    }
    for k, df in panels.items():
        df.to_parquet(paths[k])
    return panels


def _safe_first(df: pd.DataFrame, keys: List[str]) -> Optional[pd.Series]:
    if df is None or df.empty:
        return None
    for k in keys:
        for idx in df.index.astype(str):
            if k.lower() == idx.lower() or k.lower() in idx.lower():
                s = df.loc[idx]
                s.index = pd.to_datetime(s.index)
                return pd.to_numeric(s, errors="coerce").sort_index()
    return None


def _fetch_one_fundamentals(ticker: str) -> Dict[str, float]:
    """Latest trailing fundamentals snapshot (cross-section)."""
    out = {"ticker": ticker}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        keys = [
            "marketCap",
            "enterpriseValue",
            "trailingPE",
            "forwardPE",
            "priceToBook",
            "priceToSalesTrailing12Months",
            "enterpriseToEbitda",
            "returnOnEquity",
            "returnOnAssets",
            "grossMargins",
            "operatingMargins",
            "profitMargins",
            "debtToEquity",
            "currentRatio",
            "quickRatio",
            "earningsQuarterlyGrowth",
            "revenueGrowth",
            "freeCashflow",
            "operatingCashflow",
            "totalRevenue",
            "ebitda",
            "bookValue",
            "trailingEps",
            "forwardEps",
            "sharesOutstanding",
            "beta",
            "heldPercentInsiders",
            "shortRatio",
        ]
        for k in keys:
            v = info.get(k)
            try:
                out[k] = float(v) if v is not None else np.nan
            except Exception:
                out[k] = np.nan

        # quarterly statements for quality/accruals proxies
        try:
            bs = t.quarterly_balance_sheet
            is_ = t.quarterly_financials
            cf = t.quarterly_cashflow
            ni = _safe_first(is_, ["Net Income"])
            revenue = _safe_first(is_, ["Total Revenue", "Operating Revenue"])
            assets = _safe_first(bs, ["Total Assets"])
            equity = _safe_first(bs, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
            debt = _safe_first(bs, ["Total Debt", "Long Term Debt"])
            cfo = _safe_first(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            if ni is not None and len(ni) >= 2 and assets is not None:
                out["roe_q"] = float(ni.iloc[0] / equity.iloc[0]) if equity is not None and equity.iloc[0] else np.nan
                out["roa_q"] = float(ni.iloc[0] / assets.iloc[0]) if assets.iloc[0] else np.nan
                out["ni_growth_q"] = float(ni.iloc[0] / abs(ni.iloc[1]) - 1.0) if ni.iloc[1] else np.nan
            if revenue is not None and len(revenue) >= 2:
                out["rev_growth_q"] = float(revenue.iloc[0] / abs(revenue.iloc[1]) - 1.0) if revenue.iloc[1] else np.nan
            if cfo is not None and ni is not None:
                out["accruals"] = float((ni.iloc[0] - cfo.iloc[0]) / assets.iloc[0]) if assets is not None and assets.iloc[0] else np.nan
            if debt is not None and equity is not None and equity.iloc[0]:
                out["debt_equity_q"] = float(debt.iloc[0] / equity.iloc[0])
            if cfo is not None and assets is not None and assets.iloc[0]:
                out["cfo_assets"] = float(cfo.iloc[0] / assets.iloc[0])
        except Exception:
            pass
    except Exception:
        pass
    return out


def download_fundamentals_snapshot(
    tickers: Iterable[str],
    cache_dir: Path | None = None,
    force: bool = False,
    max_workers: int = 12,
) -> pd.DataFrame:
    cache_dir = ensure_data_dir(cache_dir)
    cache = cache_dir / "fundamentals_snapshot.csv"
    if cache.exists() and not force:
        return pd.read_csv(cache, index_col=0)

    tickers = list(tickers)
    rows = []
    print(f"[fundamentals] fetching {len(tickers)} tickers with {max_workers} workers")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_fetch_one_fundamentals, t): t for t in tickers}
        done = 0
        for fut in as_completed(futs):
            rows.append(fut.result())
            done += 1
            if done % 50 == 0:
                print(f"  fundamentals {done}/{len(tickers)}")
    df = pd.DataFrame(rows).set_index("ticker")
    df.to_csv(cache)
    return df


def load_market_bundle(
    start: str = "2015-01-01",
    end: Optional[str] = None,
    cache_dir: Path | None = None,
    force_prices: bool = False,
    force_fundamentals: bool = False,
    max_names: Optional[int] = None,
) -> Dict:
    """Return prices panels, SPY, fundamentals snapshot, ticker meta."""
    cache_dir = ensure_data_dir(cache_dir)
    meta = fetch_sp500_tickers(cache_dir)
    tickers = meta["Symbol"].tolist()
    if max_names is not None:
        tickers = tickers[:max_names]
    prices = download_price_panel(tickers, start=start, end=end, cache_dir=cache_dir, force=force_prices)
    # drop columns with too few observations
    ac = prices["adj_close"]
    keep = [c for c in ac.columns if c != "SPY" and ac[c].notna().sum() >= 252]
    ac = ac[keep + (["SPY"] if "SPY" in ac.columns else [])]
    for k in prices:
        prices[k] = prices[k].reindex(columns=[c for c in ac.columns if c in prices[k].columns])

    funds = download_fundamentals_snapshot(
        [c for c in ac.columns if c != "SPY"],
        cache_dir=cache_dir,
        force=force_fundamentals,
    )
    return {
        "meta": meta,
        "prices": prices,
        "fundamentals": funds,
        "tickers": [c for c in ac.columns if c != "SPY"],
        "spy": ac["SPY"] if "SPY" in ac.columns else None,
    }
