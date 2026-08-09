"""通过 akshare 拉取ETF日行情，并做对齐/缓存。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict

import akshare as ak
import numpy as np
import pandas as pd

from config import CODES, END_DATE, START_DATE, STRATEGY_START, UNIVERSE

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _sina_symbol(code: str) -> str:
    mkt = UNIVERSE[code]["market"]
    return f"{mkt}{code}"


def download_one(code: str, retries: int = 5) -> pd.DataFrame:
    """优先腾讯前复权（akshare），失败回退新浪。"""
    symbol = _sina_symbol(code)
    last_err = None
    for i in range(retries):
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=symbol,
                start_date=START_DATE,
                end_date=END_DATE,
                adjust="qfq",
            )
            df = df.rename(
                columns={
                    "date": "date",
                    "open": "open",
                    "close": "close",
                    "high": "high",
                    "low": "low",
                    "volume": "volume",
                }
            )
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").drop_duplicates("date")
            keep = ["date", "open", "high", "low", "close", "volume"]
            df = df[keep].copy()
            return df
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0 + i)
    # fallback sina
    for i in range(retries):
        try:
            df = ak.fund_etf_hist_sina(symbol=symbol)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            df = df[(df["date"] >= START_DATE[:4] + "-" + START_DATE[4:6] + "-" + START_DATE[6:])
                    & (df["date"] <= END_DATE[:4] + "-" + END_DATE[4:6] + "-" + END_DATE[6:])]
            # 粗暴拆分修正：隔夜跳空>28%且开盘同步跳空
            df = _fix_splits(df)
            return df[["date", "open", "high", "low", "close", "volume"]].copy()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0 + i)
    raise RuntimeError(f"下载失败 {code}: {last_err}")


def _fix_splits(df: pd.DataFrame, thr: float = 0.28) -> pd.DataFrame:
    df = df.sort_values("date").reset_index(drop=True).copy()
    factor = np.ones(len(df))
    closes = df["close"].values
    opens = df["open"].values
    for i in range(1, len(df)):
        r = closes[i] / closes[i - 1] - 1
        gap = opens[i] / closes[i - 1] - 1
        if abs(r) > thr and abs(gap) > thr * 0.8:
            f = closes[i] / closes[i - 1]
            factor[:i] *= f
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].values * factor
    return df


def load_universe(force: bool = False) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for code in CODES:
        cache = DATA_DIR / f"{code}.csv"
        if cache.exists() and not force:
            df = pd.read_csv(cache, parse_dates=["date"])
        else:
            print(f"[data] downloading {code} ...")
            df = download_one(code)
            df.to_csv(cache, index=False)
        out[code] = df.sort_values("date").reset_index(drop=True)
    return out


def build_panels(raw: Dict[str, pd.DataFrame]):
    """对齐交易日历，返回 close/open DataFrame。"""
    all_idx = sorted(set().union(*[set(df["date"]) for df in raw.values()]))
    all_idx = pd.DatetimeIndex(all_idx)
    all_idx = all_idx[(all_idx >= STRATEGY_START) & (all_idx <= pd.Timestamp(END_DATE))]

    close = pd.DataFrame(index=all_idx)
    open_ = pd.DataFrame(index=all_idx)
    for code, df in raw.items():
        s = df.set_index("date").sort_index()
        first = s.index.min()
        close[code] = s["close"].reindex(all_idx)
        open_[code] = s["open"].reindex(all_idx)
        close.loc[close.index < first, code] = np.nan
        open_.loc[open_.index < first, code] = np.nan
        close[code] = close[code].ffill()
        open_[code] = open_[code].ffill()
    return close, open_
