"""Tests for IV history store and CSV snapshot loader."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.iv_history_store import IVHistoryStore
from data_fetcher.csv_loader import load_chain_csv, load_snapshot_bundle


def test_iv_store_roundtrip(tmp_path: Path):
    store = IVHistoryStore(root=tmp_path, tenor_days=30)
    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(260)]
    vals = [0.15 + (i % 50) * 0.001 for i in range(260)]
    s = store.save("SR", dates, vals, source="exchange_czce_atm")
    assert s.n == 260
    loaded = store.load("SR")
    assert loaded is not None
    assert loaded.n == 260
    assert loaded.source == "exchange_czce_atm"


def test_csv_chain_rejects_missing_bid(tmp_path: Path):
    p = tmp_path / "chain.csv"
    # missing put_ask column should fail
    pd.DataFrame(
        {"strike": [100], "call_bid": [1], "call_ask": [2], "put_bid": [1]}
    ).to_csv(p, index=False)
    try:
        load_chain_csv(p)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "missing" in str(e).lower() or "put_ask" in str(e)


def test_snapshot_bundle(tmp_path: Path):
    meta = {
        "quote_date": "2026-08-20",
        "quote_timestamp": "2026-08-20T15:00:00",
        "products": [
            {
                "product": "SR",
                "name": "白糖",
                "exchange": "CZCE",
                "option_month": "sr2611",
                "underlying_futures": "SR2611",
                "multiplier": 10,
                "tick_size": 1,
                "expiry_date": "2026-10-27",
                "dte": 68,
            }
        ],
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    fut = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=80, freq="B"),
            "open": 5200,
            "high": 5250,
            "low": 5150,
            "close": 5210,
            "settle": 5205,
        }
    )
    fut.to_csv(tmp_path / "SR_futures.csv", index=False)
    pd.DataFrame(
        {
            "strike": [5000, 5500],
            "call_bid": [200, 30],
            "call_ask": [210, 32],
            "put_bid": [15, 80],
            "put_ask": [16, 85],
            "call_oi": [2000, 3000],
            "put_oi": [2500, 1800],
        }
    ).to_csv(tmp_path / "SR_chain.csv", index=False)
    snaps, manifest = load_snapshot_bundle(tmp_path)
    assert len(snaps) == 1
    assert snaps[0].F == 5205
    assert manifest.data_source.startswith("user_csv")
