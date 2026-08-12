# -*- coding: utf-8 -*-
"""Download / cache daily OHLCV for the US equity universe via yfinance."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import yfinance as yf

from .universe import fetch_universe

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "alpha101_data"


@dataclass
class PricePanel:
    """OHLCV panels: index=dates, columns=tickers."""

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    vwap: pd.DataFrame  # proxy: (h+l+c)/3
    returns: pd.DataFrame
    tickers_spx: List[str]
    tickers_ndx: List[str]

    @property
    def tickers(self) -> List[str]:
        return list(self.close.columns)


def _chunked(xs: Sequence[str], n: int) -> List[List[str]]:
    return [list(xs[i : i + n]) for i in range(0, len(xs), n)]


def download_ohlcv(
    tickers: Sequence[str],
    start: str = "2016-08-01",
    end: Optional[str] = None,
    chunk_size: int = 80,
    pause: float = 0.4,
) -> Dict[str, pd.DataFrame]:
    """Batch-download adjusted OHLCV; returns dict of wide frames (dates x tickers)."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    frames = {k: [] for k in ["Open", "High", "Low", "Close", "Volume"]}
    failed: List[str] = []

    for i, chunk in enumerate(_chunked(list(tickers), chunk_size)):
        try:
            raw = yf.download(
                tickers=chunk,
                start=start,
                end=end,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            failed.extend(chunk)
            print(f"[warn] chunk {i} failed: {exc}")
            time.sleep(pause * 2)
            continue

        if raw is None or raw.empty:
            failed.extend(chunk)
            continue

        # yfinance returns MultiIndex columns when multiple tickers
        if isinstance(raw.columns, pd.MultiIndex):
            # level 0 = ticker, level 1 = field  OR reverse depending on version
            lvl0 = raw.columns.get_level_values(0)
            lvl1 = raw.columns.get_level_values(1)
            if set(chunk).intersection(set(lvl0.unique())):
                # ticker on level 0
                for t in chunk:
                    if t not in lvl0:
                        failed.append(t)
                        continue
                    sub = raw[t]
                    for field in frames:
                        if field in sub.columns:
                            s = sub[field].rename(t)
                            frames[field].append(s)
            else:
                # field on level 0
                for field in frames:
                    if field not in raw.columns.get_level_values(0):
                        continue
                    sub = raw[field]
                    for t in chunk:
                        if t in sub.columns:
                            frames[field].append(sub[t].rename(t))
                        else:
                            failed.append(t)
        else:
            # single ticker
            t = chunk[0]
            for field in frames:
                if field in raw.columns:
                    frames[field].append(raw[field].rename(t))

        time.sleep(pause)
        print(f"[download] chunk {i+1}/{(len(tickers)+chunk_size-1)//chunk_size} done")

    out: Dict[str, pd.DataFrame] = {}
    for field, parts in frames.items():
        if not parts:
            out[field] = pd.DataFrame()
            continue
        df = pd.concat(parts, axis=1)
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.sort_index()
        out[field] = df

    if failed:
        print(f"[download] failed/missing count={len(set(failed))}")
    return out


def _save_panel(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, encoding="utf-8", compression="gzip")


def _load_panel(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, compression="gzip")
    df.index = pd.to_datetime(df.index)
    df.columns = df.columns.astype(str)
    return df.sort_index()


def load_or_download_panel(
    data_dir: Path | str | None = None,
    start: str = "2016-08-01",
    end: Optional[str] = None,
    refresh: bool = False,
    max_tickers: Optional[int] = None,
) -> PricePanel:
    data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_path = data_dir / "meta.json"
    uni_path = data_dir / "universe.json"

    uni = fetch_universe(cache_path=uni_path, refresh=refresh)
    tickers = list(uni["union"])
    if max_tickers is not None:
        tickers = tickers[: max_tickers]

    close_path = data_dir / "close.csv.gz"
    if close_path.exists() and not refresh:
        open_ = _load_panel(data_dir / "open.csv.gz")
        high = _load_panel(data_dir / "high.csv.gz")
        low = _load_panel(data_dir / "low.csv.gz")
        close = _load_panel(data_dir / "close.csv.gz")
        volume = _load_panel(data_dir / "volume.csv.gz")
    else:
        raw = download_ohlcv(tickers, start=start, end=end)
        open_, high, low, close, volume = (
            raw["Open"],
            raw["High"],
            raw["Low"],
            raw["Close"],
            raw["Volume"],
        )
        # drop tickers with too few observations
        min_obs = 252 * 3
        keep = close.columns[close.notna().sum() >= min_obs]
        open_, high, low, close, volume = (
            open_[keep],
            high[keep],
            low[keep],
            close[keep],
            volume[keep],
        )
        _save_panel(open_, data_dir / "open.csv.gz")
        _save_panel(high, data_dir / "high.csv.gz")
        _save_panel(low, data_dir / "low.csv.gz")
        _save_panel(close, data_dir / "close.csv.gz")
        _save_panel(volume, data_dir / "volume.csv.gz")
        meta_path.write_text(
            json.dumps(
                {
                    "start": start,
                    "end": end,
                    "n_tickers": int(close.shape[1]),
                    "n_days": int(close.shape[0]),
                    "downloaded_at": pd.Timestamp.utcnow().isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # align
    cols = sorted(set(close.columns) & set(open_.columns) & set(high.columns) & set(low.columns) & set(volume.columns))
    open_, high, low, close, volume = (
        open_[cols],
        high[cols],
        low[cols],
        close[cols],
        volume[cols],
    )
    vwap = (high + low + close) / 3.0
    returns = close.pct_change()

    spx = [t for t in uni["sp500"] if t in cols]
    ndx = [t for t in uni["nasdaq100"] if t in cols]
    return PricePanel(
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        vwap=vwap,
        returns=returns,
        tickers_spx=spx,
        tickers_ndx=ndx,
    )


def make_synthetic_panel(
    n_tickers: int = 40,
    n_days: int = 520,
    seed: int = 0,
) -> PricePanel:
    """Synthetic OHLCV with a planted short-horizon reversal edge for tests."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_days)
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    rets = rng.normal(0, 0.015, size=(n_days, n_tickers))
    # plant: negative of yesterday return predicts next 5d return
    for t in range(1, n_days - 5):
        rets[t + 1 : t + 6] -= 0.15 * rets[t - 1] / 5.0

    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=dates, columns=tickers)
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.001, close.shape))
    high = pd.DataFrame(
        np.maximum(open_.values, close.values) * (1 + rng.uniform(0, 0.005, close.shape)),
        index=dates,
        columns=tickers,
    )
    low = pd.DataFrame(
        np.minimum(open_.values, close.values) * (1 - rng.uniform(0, 0.005, close.shape)),
        index=dates,
        columns=tickers,
    )
    volume = pd.DataFrame(
        rng.uniform(1e6, 5e6, size=close.shape), index=dates, columns=tickers
    )
    vwap = (high + low + close) / 3.0
    return PricePanel(
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        vwap=vwap,
        returns=close.pct_change(),
        tickers_spx=tickers[: n_tickers // 2],
        tickers_ndx=tickers[n_tickers // 2 :],
    )
