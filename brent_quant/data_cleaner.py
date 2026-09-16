"""Minute-bar quality control. Raw files are never rewritten."""

from __future__ import annotations

from typing import Any

import pandas as pd

from brent_quant import config as cfg
from brent_quant.data_loader import OHLCV, to_utc_index


def _expected_delta() -> pd.Timedelta:
    return pd.Timedelta(minutes=cfg.BAR_MINUTES)


def detect_missing_bars(df: pd.DataFrame) -> pd.DatetimeIndex:
    if df.empty or len(df) < 2:
        tz = df.index.tz if getattr(df.index, "tz", None) else cfg.TIMEZONE
        return pd.DatetimeIndex([], tz=tz)
    expected = _expected_delta()
    missing: list[pd.Timestamp] = []
    prev = df.index[0]
    for ts in df.index[1:]:
        delta = ts - prev
        # Session/weekend gaps (>= 1h) are not treated as missing bars.
        if expected < delta < pd.Timedelta(hours=1):
            n_miss = int(round(delta / expected)) - 1
            for k in range(1, n_miss + 1):
                missing.append(prev + k * expected)
        prev = ts
    tz = df.index.tz
    if not missing:
        return pd.DatetimeIndex([], tz=tz)
    return pd.DatetimeIndex(missing, tz=tz)


def clean_ohlcv(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Deduplicate, mark anomalies, keep zero-volume bars, never forward-fill OHLC."""
    out = to_utc_index(df)
    n_raw = len(out)
    out = out.dropna(subset=["open", "high", "low", "close"], how="any")
    n_ohlc_drop = n_raw - len(out)
    dup = int(out.index.duplicated().sum())
    out = out[~out.index.duplicated(keep="last")]
    for col in OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"], how="any")
    out["volume"] = out["volume"].fillna(0.0)

    ret = out["close"].pct_change()
    out["bar_return"] = ret
    out["abnormal_return"] = ret.abs() > cfg.ABNORMAL_RETURN
    out["zero_volume"] = out["volume"] <= 0
    out["range"] = (out["high"] - out["low"]).clip(lower=0.0)
    invalid = (
        (out["high"] < out["low"])
        | (out["high"] < out["open"])
        | (out["high"] < out["close"])
        | (out["low"] > out["open"])
        | (out["low"] > out["close"])
        | (out["open"] <= 0)
        | (out["close"] <= 0)
    )
    out["invalid_bar"] = invalid.fillna(False)
    missing = detect_missing_bars(out)

    qc = {
        "rows": int(len(out)),
        "dropped_ohlc_na": int(n_ohlc_drop),
        "duplicate_timestamps_removed": dup,
        "abnormal_return_bars": int(out["abnormal_return"].sum()),
        "zero_volume_bars": int(out["zero_volume"].sum()),
        "invalid_bars": int(out["invalid_bar"].sum()),
        "missing_bars": int(len(missing)),
        "missing_bar_sample": [str(x) for x in missing[:20]],
        "start": str(out.index.min()) if len(out) else None,
        "end": str(out.index.max()) if len(out) else None,
        "timezone": str(out.index.tz),
    }
    return out, qc
