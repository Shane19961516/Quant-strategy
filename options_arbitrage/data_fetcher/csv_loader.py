"""Load user-provided CSV/Excel market snapshots when live APIs are insufficient."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from data_fetcher.v2_fetcher import ChainRow, FetchManifest, ProductSnapshotV2

REQUIRED_CHAIN_COLS = [
    "strike",
    "call_bid",
    "call_ask",
    "put_bid",
    "put_ask",
]


def load_chain_csv(path: Path) -> list[ChainRow]:
    df = pd.read_csv(path)
    # normalize headers
    rename = {
        "行权价": "strike",
        "看涨买价": "call_bid",
        "看涨卖价": "call_ask",
        "看跌买价": "put_bid",
        "看跌卖价": "put_ask",
        "看涨合约": "call_symbol",
        "看跌合约": "put_symbol",
        "看涨持仓": "call_oi",
        "看跌持仓": "put_oi",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    missing = [c for c in REQUIRED_CHAIN_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}; do not fill with zeros")
    rows: list[ChainRow] = []
    for _, r in df.iterrows():
        def nz(x):
            if pd.isna(x):
                return None
            v = float(x)
            return v if v > 0 else None

        rows.append(
            ChainRow(
                strike=float(r["strike"]),
                call_symbol=str(r.get("call_symbol") or ""),
                put_symbol=str(r.get("put_symbol") or ""),
                call_bid=nz(r["call_bid"]),
                call_ask=nz(r["call_ask"]),
                call_mid=None,
                call_oi=nz(r["call_oi"]) if "call_oi" in df.columns else None,
                put_bid=nz(r["put_bid"]),
                put_ask=nz(r["put_ask"]),
                put_mid=None,
                put_oi=nz(r["put_oi"]) if "put_oi" in df.columns else None,
            )
        )
    for row in rows:
        if row.call_bid and row.call_ask:
            row.call_mid = 0.5 * (row.call_bid + row.call_ask)
        if row.put_bid and row.put_ask:
            row.put_mid = 0.5 * (row.put_bid + row.put_ask)
    return rows


def load_snapshot_bundle(dir_path: Path) -> tuple[list[ProductSnapshotV2], FetchManifest]:
    """
    Directory layout:
      meta.json
      {PRODUCT}_futures.csv   (date,open,high,low,close[,settle])
      {PRODUCT}_chain.csv     (strike,call_bid,call_ask,put_bid,put_ask,...)
    """
    root = Path(dir_path)
    meta_path = root / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"missing {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    quote_date = date.fromisoformat(meta["quote_date"])
    snaps: list[ProductSnapshotV2] = []
    for prod in meta.get("products", []):
        code = prod["product"]
        fut = pd.read_csv(root / f"{code}_futures.csv")
        fut["date"] = pd.to_datetime(fut["date"])
        fut = fut.sort_values("date")
        chain = load_chain_csv(root / f"{code}_chain.csv")
        last = fut.iloc[-1]
        F = float(last["settle"] if "settle" in fut.columns and pd.notna(last.get("settle")) else last["close"])
        snaps.append(
            ProductSnapshotV2(
                product=code,
                product_name=prod.get("name", code),
                exchange=prod.get("exchange", ""),
                option_month=prod["option_month"],
                underlying_futures=prod["underlying_futures"],
                quote_date=quote_date,
                quote_timestamp=datetime.fromisoformat(meta.get("quote_timestamp", datetime.now().isoformat())),
                data_source=f"user_csv:{root}",
                F=F,
                settle=float(last["settle"]) if "settle" in fut.columns and pd.notna(last.get("settle")) else None,
                multiplier=float(prod["multiplier"]),
                tick_size=float(prod.get("tick_size", 1)),
                expiry_date=date.fromisoformat(prod["expiry_date"]),
                dte=int(prod["dte"]),
                chain=chain,
                futures_ohlc=fut,
            )
        )
    manifest = FetchManifest(
        data_source=f"user_csv:{root}",
        quote_asof=meta.get("quote_timestamp", datetime.now().isoformat()),
        products_ok=[s.product_name for s in snaps],
        products_failed=[],
    )
    return snaps, manifest
