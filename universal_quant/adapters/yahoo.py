"""Yahoo adapter. Strategy layer only calls get_data(symbol, start, end, interval)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from brent_quant.data_cleaner import clean_ohlcv
from brent_quant.data_loader import download_yahoo, save_parquet, to_utc_index
from universal_quant import config as cfg


def get_data(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    interval: str = cfg.INTERVAL,
    period: str = cfg.PERIOD,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    last_err: Exception | None = None
    df = pd.DataFrame()
    for per in (period, "60d", "1y"):
        try:
            df = download_yahoo(ticker=symbol, period=per, interval=interval, retries=3)
            if not df.empty:
                period = per
                break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    if df.empty:
        raise RuntimeError(f"Yahoo adapter failed for {symbol} {interval}: {last_err}")
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    df = to_utc_index(df)
    clean, qc = clean_ohlcv(df)
    cfg.RAW_DIR.mkdir(parents=True, exist_ok=True)
    cfg.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("=", "").replace("/", "_").replace("-", "")
    save_parquet(df, cfg.RAW_DIR / f"{safe}_{interval}.parquet")
    ohlcv = clean[["open", "high", "low", "close", "volume"]].copy()
    save_parquet(ohlcv, cfg.PROCESSED_DIR / f"{safe}_{interval}.parquet")
    meta = {
        "symbol": symbol,
        "interval": interval,
        "period": period,
        "rows": int(len(ohlcv)),
        "start": str(ohlcv.index.min()) if len(ohlcv) else None,
        "end": str(ohlcv.index.max()) if len(ohlcv) else None,
        "qc": qc,
        "adapter": "yahoo",
    }
    return ohlcv, meta
