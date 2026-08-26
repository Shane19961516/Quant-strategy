# -*- coding: utf-8 -*-
"""通过 akshare 拉取国内期货主力连续日线。

优先使用新浪 `futures_zh_daily_sina`（symbol 形如 RB0），
东方财富 `futures_hist_em` 作为备选。
"""

from __future__ import annotations

import os
import time
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

# 展示代码 -> 新浪主力连续代码 / 东财中文名
AKSHARE_SINA_SYMBOLS: Dict[str, str] = {
    "RB": "RB0",
    "HC": "HC0",
    "CU": "CU0",
    "AU": "AU0",
    "AG": "AG0",
    "RU": "RU0",
    "I": "I0",
    "JM": "JM0",
    "J": "J0",
    "M": "M0",
    "Y": "Y0",
    "C": "C0",
    "P": "P0",
    "TA": "TA0",
    "MA": "MA0",
    "SC": "SC0",
    "IF": "IF0",
    "IC": "IC0",
    "IH": "IH0",
}

AKSHARE_EM_SYMBOLS: Dict[str, str] = {
    "RB": "螺纹钢主连",
    "HC": "热卷主连",
    "CU": "沪铜主连",
    "AU": "沪金主连",
    "AG": "沪银主连",
    "RU": "橡胶主连",
    "I": "铁矿石主连",
    "JM": "焦煤主连",
    "J": "焦炭主连",
    "M": "豆粕主连",
    "Y": "豆油主连",
    "C": "玉米主连",
    "P": "棕榈油主连",
    "TA": "PTA主连",
    "MA": "甲醇主连",
    "SC": "原油主连",
    "IF": "沪深主连",
    "IC": "中证500股指主连",
    "IH": "上证主连",
}

DEFAULT_AKSHARE_UNIVERSE = ["RB", "HC", "CU", "AU", "RU", "I", "M", "Y", "C", "TA", "MA", "SC", "IF"]


def _normalize_sina(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if "date" not in df.columns:
        raise ValueError(f"missing date in {list(raw.columns)}")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    rename = {"hold": "oi", "settle": "settle"}
    df = df.rename(columns=rename)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    keep = [c for c in ("open", "high", "low", "close", "volume", "oi", "settle") if c in df.columns]
    return df[keep]


def _normalize_em(raw: pd.DataFrame) -> pd.DataFrame:
    colmap = {
        "时间": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "持仓量": "oi",
        "成交额": "amount",
    }
    df = raw.rename(columns={k: v for k, v in colmap.items() if k in raw.columns}).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    keep = [c for c in ("open", "high", "low", "close", "volume", "oi", "amount") if c in df.columns]
    return df[keep]


def fetch_main_continuous_sina(symbol: str, retries: int = 5, sleep: float = 1.5) -> pd.DataFrame:
    """新浪主力连续日线。"""
    import akshare as ak

    sina = AKSHARE_SINA_SYMBOLS.get(symbol.upper(), symbol.upper() if symbol[-1] == "0" else f"{symbol.upper()}0")
    last_err: Optional[Exception] = None
    for i in range(retries):
        try:
            raw = ak.futures_zh_daily_sina(symbol=sina)
            if raw is None or len(raw) == 0:
                raise RuntimeError(f"empty data for {sina}")
            return _normalize_sina(raw)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(sleep * (i + 1))
    raise RuntimeError(f"sina failed {symbol}/{sina}: {last_err}")


def fetch_main_continuous_em(
    symbol: str,
    start_date: str = "20180101",
    end_date: str = "20500101",
    retries: int = 3,
    sleep: float = 2.0,
) -> pd.DataFrame:
    """东方财富主力连续日线（备选）。"""
    import akshare as ak

    em_name = AKSHARE_EM_SYMBOLS.get(symbol.upper(), symbol)
    last_err: Optional[Exception] = None
    for i in range(retries):
        try:
            raw = ak.futures_hist_em(
                symbol=em_name,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if raw is None or len(raw) == 0:
                raise RuntimeError(f"empty data for {em_name}")
            return _normalize_em(raw)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(sleep * (i + 1))
    raise RuntimeError(f"em failed {symbol}/{em_name}: {last_err}")


def fetch_main_continuous(
    symbol: str,
    start_date: str = "20180101",
    end_date: str = "20500101",
    source: str = "sina",
    retries: int = 5,
    sleep: float = 1.5,
) -> pd.DataFrame:
    """拉取单个主力连续，默认新浪，失败可回退东财。"""
    errors = []
    if source in ("sina", "auto"):
        try:
            df = fetch_main_continuous_sina(symbol, retries=retries, sleep=sleep)
            return _slice(df, start_date, end_date)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            if source == "sina":
                raise
    try:
        df = fetch_main_continuous_em(symbol, start_date=start_date, end_date=end_date, retries=retries, sleep=sleep)
        return _slice(df, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
        raise RuntimeError(" | ".join(errors)) from exc


def _slice(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    start = pd.to_datetime(start_date)
    out = df.loc[df.index >= start]
    if end_date and end_date < "20500101":
        end = pd.to_datetime(end_date)
        out = out.loc[out.index <= end]
    return out


def fetch_akshare_panels(
    symbols: Optional[Iterable[str]] = None,
    start_date: str = "20180101",
    end_date: str = "20500101",
    cache_dir: Optional[str] = "cta_data_akshare",
    force_refresh: bool = False,
    min_bars: int = 252,
    pause: float = 1.0,
    source: str = "sina",
) -> Tuple[Dict[str, pd.DataFrame], List[str]]:
    """批量拉取主力连续，可选落盘缓存。

    返回 (panels, errors)。部分品种失败不影响其余品种。
    """
    symbols = list(symbols or DEFAULT_AKSHARE_UNIVERSE)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    panels: Dict[str, pd.DataFrame] = {}
    errors: List[str] = []
    for sym in symbols:
        cache_path = os.path.join(cache_dir, f"{sym}.csv") if cache_dir else ""
        try:
            if cache_path and os.path.exists(cache_path) and not force_refresh:
                df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                df = _slice(df, start_date, end_date)
            else:
                df = fetch_main_continuous(
                    sym,
                    start_date=start_date,
                    end_date=end_date,
                    source=source,
                )
                if cache_path:
                    out = df.copy()
                    out.index.name = "date"
                    out.to_csv(cache_path)
                time.sleep(pause)
            if len(df) < min_bars:
                errors.append(f"{sym}: only {len(df)} bars (<{min_bars})")
                continue
            panels[sym] = df
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sym}: {exc}")
    return panels, errors
