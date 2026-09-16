"""OKX public swap candles. No key required."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from universal_quant import config as cfg

OKX_HISTORY = "https://www.okx.com/api/v5/market/history-candles"


def _get(params: dict[str, Any]) -> list[list[str]]:
    url = f"{OKX_HISTORY}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; UPV/1.0)", "Accept": "application/json"})
    last: Exception | None = None
    for i in range(5):
        try:
            with urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if str(payload.get("code")) != "0":
                raise RuntimeError(payload.get("msg") or payload)
            return payload.get("data") or []
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"OKX candles failed: {last}")


def download_swap_1m(inst_id: str = "ETH-USDT-SWAP", days: int = 60) -> pd.DataFrame:
    """Newest-first pages. `after` = older than this ts (ms)."""
    need = int(days * 24 * 60) + 10
    rows: list[list[str]] = []
    after: str | None = None
    while len(rows) < need:
        params: dict[str, Any] = {"instId": inst_id, "bar": "1m", "limit": "300"}
        if after:
            params["after"] = after
        chunk = _get(params)
        if not chunk:
            break
        rows.extend(chunk)
        after = chunk[-1][0]
        time.sleep(0.07)
        if len(rows) % 3000 < 300:
            print(f"  OKX {inst_id} 1m {len(rows)} bars...")
    if not rows:
        raise RuntimeError(f"OKX returned no candles for {inst_id}")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "vol_ccy", "vol_quote", "confirm"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    out = df[["ts", "open", "high", "low", "close", "volume"]].drop_duplicates("ts").set_index("ts").sort_index()
    out = out.dropna(subset=["open", "high", "low", "close"], how="any")
    out = out[~out.index.duplicated(keep="last")]
    return out.iloc[-need:]


def get_okx_1m(inst_id: str = "ETH-USDT-SWAP", days: int = 60, force: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    safe = inst_id.replace("-", "")
    path = cfg.PROCESSED_DIR / f"{safe}_1m.parquet"
    cfg.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        age_h = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 3600.0
        if age_h < 6:
            df = pd.read_parquet(path)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, utc=True)
            elif df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            meta = {"symbol": inst_id, "interval": "1m", "rows": int(len(df)), "cached": True, "adapter": "okx"}
            if len(df):
                meta["start"] = str(df.index.min())
                meta["end"] = str(df.index.max())
            return df, meta
    df = download_swap_1m(inst_id, days=days)
    df.to_parquet(path)
    meta = {
        "symbol": inst_id,
        "interval": "1m",
        "rows": int(len(df)),
        "start": str(df.index.min()) if len(df) else None,
        "end": str(df.index.max()) if len(df) else None,
        "cached": False,
        "adapter": "okx",
        "days_requested": days,
    }
    return df, meta
