"""Yahoo Finance downloader with incremental Parquet persistence."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from brent_quant import config as cfg


OHLCV = ("open", "high", "low", "close", "volume")


def _flatten_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(OHLCV))

    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        level0 = [str(x).lower() for x in out.columns.get_level_values(0)]
        if any(name in level0 for name in ("open", "close", "high", "low")):
            out.columns = [str(c[0]).lower() for c in out.columns]
        else:
            out.columns = ["_".join(str(x) for x in c).lower() for c in out.columns]

    rename: dict[str, str] = {}
    seen: set[str] = set()
    for col in out.columns:
        key = str(col).lower().replace(" ", "_")
        if key in {"adj_close", "adjclose", "dividends", "stock_splits"}:
            continue
        mapped = None
        if key in OHLCV:
            mapped = key
        elif key.endswith("_open"):
            mapped = "open"
        elif key.endswith("_high"):
            mapped = "high"
        elif key.endswith("_low"):
            mapped = "low"
        elif key.endswith("_close") and "adj" not in key:
            mapped = "close"
        elif key.endswith("_volume"):
            mapped = "volume"
        if mapped and mapped not in seen:
            rename[col] = mapped
            seen.add(mapped)
    out = out.rename(columns=rename)
    keep = [c for c in OHLCV if c in out.columns]
    out = out.loc[:, ~out.columns.duplicated()].copy()
    out = out[keep].copy()
    for col in OHLCV:
        if col not in out.columns:
            out[col] = pd.NA
    return out[list(OHLCV)]


def to_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize(cfg.SOURCE_TIMEZONE, ambiguous="NaT", nonexistent="shift_forward")
    out.index = idx
    out = out[~out.index.isna()]
    out = out.tz_convert(cfg.TIMEZONE)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def _yahoo_chart_api(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """Direct chart API fallback when yfinance is unavailable or empty."""
    params = {
        "interval": interval,
        "range": period,
        "includePrePost": "false",
        "events": "div,splits",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?{urlencode(params)}"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; BrentQuant/0.1)",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=30) as resp:
        payload: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    result = payload.get("chart", {}).get("result")
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(f"Yahoo chart API empty: {error}")
    node = result[0]
    ts = node.get("timestamp") or []
    quote = (node.get("indicators") or {}).get("quote") or [{}]
    q0 = quote[0]
    if not ts:
        raise RuntimeError("Yahoo chart API returned no timestamps")
    idx = pd.to_datetime(ts, unit="s", utc=True)
    df = pd.DataFrame(
        {
            "open": q0.get("open"),
            "high": q0.get("high"),
            "low": q0.get("low"),
            "close": q0.get("close"),
            "volume": q0.get("volume"),
        },
        index=idx,
    )
    return df.dropna(how="all")


def download_yahoo(
    ticker: str = cfg.TICKER,
    period: str = cfg.PERIOD,
    interval: str = cfg.INTERVAL,
    retries: int = 4,
) -> pd.DataFrame:
    last_err: Exception | None = None
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        yf = None
        last_err = exc

    if yf is not None:
        for i in range(retries):
            try:
                raw = yf.download(
                    ticker,
                    period=period,
                    interval=interval,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
                df = _flatten_ohlcv(raw)
                df = to_utc_index(df)
                df = df.dropna(subset=["open", "high", "low", "close"], how="any")
                if not df.empty:
                    return df
                last_err = RuntimeError("yfinance returned empty frame")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
            time.sleep(1.5 * (i + 1))

        for i in range(retries):
            try:
                hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
                df = _flatten_ohlcv(hist)
                df = to_utc_index(df)
                df = df.dropna(subset=["open", "high", "low", "close"], how="any")
                if not df.empty:
                    return df
            except Exception as exc:  # noqa: BLE001
                last_err = exc
            time.sleep(1.5 * (i + 1))

    for i in range(retries):
        try:
            df = _yahoo_chart_api(ticker, interval, period)
            df = _flatten_ohlcv(df)
            df = to_utc_index(df)
            df = df.dropna(subset=["open", "high", "low", "close"], how="any")
            if not df.empty:
                return df
            last_err = RuntimeError("chart API empty after flatten")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(1.5 * (i + 1))

    raise RuntimeError(f"Failed to download {ticker} {interval} {period}: {last_err}")


def processed_path(ticker: str = cfg.TICKER, interval: str = cfg.INTERVAL) -> Path:
    safe = ticker.replace("=", "").replace("/", "_")
    return cfg.PROCESSED_DIR / f"{safe}_{interval}_clean.parquet"


def raw_snapshot_path(ticker: str, interval: str, when: datetime | None = None) -> Path:
    when = when or datetime.now(timezone.utc)
    safe = ticker.replace("=", "").replace("/", "_")
    return cfg.RAW_DIR / f"{safe}_{when.strftime('%Y_%m_%d')}_{interval}.parquet"


def save_parquet(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def load_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=list(OHLCV))
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")
        else:
            df.index = pd.to_datetime(df.index, utc=True)
    return to_utc_index(_flatten_ohlcv(df))


def merge_incremental(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        out = new.copy()
    elif new is None or new.empty:
        out = old.copy()
    else:
        out = pd.concat([old, new], axis=0)
    out = _flatten_ohlcv(out)
    out = to_utc_index(out)
    out = out.dropna(subset=["open", "high", "low", "close"], how="any")
    return out


def incremental_update(
    ticker: str = cfg.TICKER,
    period: str = cfg.PERIOD,
    interval: str = cfg.INTERVAL,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cfg.RAW_DIR.mkdir(parents=True, exist_ok=True)
    cfg.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dest = processed_path(ticker, interval)
    old = load_parquet(dest)
    new = download_yahoo(ticker=ticker, period=period, interval=interval)
    save_parquet(new, raw_snapshot_path(ticker, interval))
    merged = merge_incremental(old, new)
    save_parquet(merged, dest)
    meta = {
        "ticker": ticker,
        "interval": interval,
        "period_requested": period,
        "old_rows": int(len(old)),
        "new_rows": int(len(new)),
        "merged_rows": int(len(merged)),
        "start": str(merged.index.min()) if len(merged) else None,
        "end": str(merged.index.max()) if len(merged) else None,
        "processed_path": str(dest),
    }
    return merged, meta
