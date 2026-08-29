"""通过 akshare 拉取ETF/港股日行情，并做对齐/缓存。"""

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


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for c in df.columns:
        cl = str(c).lower()
        if cl in ("date", "日期"):
            rename[c] = "date"
        elif cl in ("open", "开盘"):
            rename[c] = "open"
        elif cl in ("high", "最高"):
            rename[c] = "high"
        elif cl in ("low", "最低"):
            rename[c] = "low"
        elif cl in ("close", "收盘"):
            rename[c] = "close"
        elif cl in ("volume", "成交量"):
            rename[c] = "volume"
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date")
    keep = ["date", "open", "high", "low", "close", "volume"]
    for col in keep:
        if col not in df.columns:
            df[col] = np.nan
    start = pd.Timestamp(f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}")
    end = pd.Timestamp(f"{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}")
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df[keep].copy()


def download_hk(code: str, retries: int = 5) -> pd.DataFrame:
    """港股：akshare stock_hk_daily（前复权）。"""
    symbol = UNIVERSE[code].get("ak_symbol", code.replace("HK", "").zfill(5))
    last_err = None
    for i in range(retries):
        try:
            df = ak.stock_hk_daily(symbol=symbol, adjust="qfq")
            return _normalize_ohlcv(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0 + i)
    # fallback eastmoney hist
    for i in range(retries):
        try:
            df = ak.stock_hk_hist(
                symbol=symbol,
                period="daily",
                start_date=START_DATE,
                end_date=END_DATE,
                adjust="qfq",
            )
            return _normalize_ohlcv(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0 + i)
    raise RuntimeError(f"下载失败 {code}: {last_err}")


def download_one(code: str, retries: int = 5) -> pd.DataFrame:
    """A股ETF优先腾讯前复权；港股走 HK 接口。"""
    if UNIVERSE[code]["market"] == "hk":
        return download_hk(code, retries=retries)

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
            return _normalize_ohlcv(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0 + i)
    # fallback sina
    for i in range(retries):
        try:
            df = ak.fund_etf_hist_sina(symbol=symbol)
            df = _normalize_ohlcv(df)
            df = _fix_splits(df)
            return df
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


def download_us_etf(code: str, retries: int = 5) -> pd.DataFrame:
    """美股 ETF（如 VIG）：akshare stock_us_daily 前复权。"""
    symbol = UNIVERSE[code].get("ak_symbol", code)
    last_err = None
    for i in range(retries):
        try:
            df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
            return _normalize_ohlcv(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0 + i)
    raise RuntimeError(f"下载美股失败 {code}/{symbol}: {last_err}")


# 指数回填映射：ETF 代码 -> (新浪指数符号, 补齐起始)
_INDEX_BACKFILL = {
    "513400": ".DJI",   # 道琼斯工业指数
    "513110": ".NDX",   # 纳斯达克 100 指数
}


def _backfill_with_index(code: str, etf_df: pd.DataFrame) -> pd.DataFrame:
    """用美股指数日线补全 ETF 上市前的缺失区间。"""
    idx_sym = _INDEX_BACKFILL.get(code)
    if idx_sym is None:
        return etf_df
    etf_start = etf_df["date"].min()
    bt_start = pd.Timestamp(f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}")
    if etf_start <= bt_start:
        return etf_df
    try:
        idx = ak.index_us_stock_sina(symbol=idx_sym)
        idx = _normalize_ohlcv(idx)
    except Exception:  # noqa: BLE001
        print(f"[data] warning: index backfill for {code} ({idx_sym}) failed")
        return etf_df
    idx = idx[idx["date"] < etf_start].copy()
    if idx.empty:
        return etf_df
    ratio = float(etf_df.iloc[0]["close"]) / float(idx.iloc[-1]["close"])
    for col in ["open", "high", "low", "close"]:
        idx[col] = idx[col] * ratio
    idx["volume"] = 0.0
    combined = pd.concat([idx, etf_df], ignore_index=True).sort_values("date").drop_duplicates("date")
    print(f"[data] backfilled {code} with {idx_sym}: {idx['date'].min().date()}~{idx['date'].max().date()} ({len(idx)} rows)")
    return combined.reset_index(drop=True)


def load_universe(force: bool = False) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for code in CODES:
        cache = DATA_DIR / f"{code}.csv"
        if cache.exists() and not force:
            df = pd.read_csv(cache, parse_dates=["date"])
        else:
            print(f"[data] downloading {code} ...")
            if UNIVERSE[code].get("market") == "us":
                df = download_us_etf(code)
            else:
                df = download_one(code)
            df = _backfill_with_index(code, df)
            df.to_csv(cache, index=False)
        out[code] = df.sort_values("date").reset_index(drop=True)
    return out


def last_raw_dates(raw: Dict[str, pd.DataFrame]) -> Dict[str, pd.Timestamp]:
    """各标的原始行情最后交易日（ffill 之前），用于周五推送时判断数据是否齐全。"""
    out: Dict[str, pd.Timestamp] = {}
    for code, df in raw.items():
        if df is None or len(df) == 0 or "date" not in df.columns:
            continue
        out[code] = pd.Timestamp(pd.to_datetime(df["date"]).max()).normalize()
    return out


def stale_codes_on(
    raw: Dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    markets: tuple[str, ...] = ("hk", "us"),
) -> list[str]:
    """返回在 asof 日尚无真实收盘价（仅可能被 ffill）的标的。"""
    asof = pd.Timestamp(asof).normalize()
    last = last_raw_dates(raw)
    stale: list[str] = []
    for code in CODES:
        mkt = UNIVERSE.get(code, {}).get("market")
        if markets and mkt not in markets:
            continue
        d = last.get(code)
        if d is None or d < asof:
            stale.append(code)
    return stale


def stale_rebalance_codes(
    raw: Dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    weight_codes: list[str] | tuple[str, ...],
) -> list[str]:
    """调仓目标新鲜度：只检查目标仓位里、且为境外原生市场(hk/us)的标的。

    513500/513110/513400 等 QDII 在 UNIVERSE 中为 sh/sz，跟 A 股日历入库，不在此列。
    VIG 等未进入目标权重的候选不会误触发「沿用上周持仓」。
    """
    asof = pd.Timestamp(asof).normalize()
    last = last_raw_dates(raw)
    stale: list[str] = []
    for code in weight_codes:
        if code not in CODES:
            continue
        mkt = UNIVERSE.get(code, {}).get("market")
        if mkt not in ("hk", "us"):
            continue
        d = last.get(code)
        if d is None or d < asof:
            stale.append(code)
    return stale


def build_panels(raw: Dict[str, pd.DataFrame]):
    """对齐交易日历，返回 close/open DataFrame。

    主日历使用 A 股/ETF 交易日，避免港股单独开市日把 A 股前值当成“交易日收益=0”。
    港股价格映射到 A 股日历后 ffill，在 A 股交易日上计港股收益（含隔夜/假期跳空）。
    """
    a_codes = [c for c, m in UNIVERSE.items() if m.get("market") not in ("hk", "us") and c in raw]
    if not a_codes:
        a_codes = list(raw.keys())
    all_idx = sorted(set().union(*[set(raw[c]["date"]) for c in a_codes]))
    all_idx = pd.DatetimeIndex(all_idx)
    all_idx = all_idx[(all_idx >= STRATEGY_START) & (all_idx <= pd.Timestamp(END_DATE))]

    close = pd.DataFrame(index=all_idx)
    open_ = pd.DataFrame(index=all_idx)
    for code, df in raw.items():
        s = df.set_index("date").sort_index()
        first = s.index.min()
        close[code] = s["close"].reindex(all_idx)
        open_[code] = s["open"].reindex(all_idx)
        # 港股/美股：先按完整历史 ffill 再裁到 A 股日历，保留假期跳空
        if UNIVERSE.get(code, {}).get("market") in ("hk", "us"):
            full = s["close"].copy()
            full_open = s["open"].copy()
            close[code] = full.reindex(all_idx, method="ffill")
            open_[code] = full_open.reindex(all_idx, method="ffill")
        close.loc[close.index < first, code] = np.nan
        open_.loc[open_.index < first, code] = np.nan
        close[code] = close[code].ffill()
        open_[code] = open_[code].ffill()
    return close, open_
