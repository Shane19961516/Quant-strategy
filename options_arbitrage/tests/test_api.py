"""API integration tests using FastAPI TestClient."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app
from database.db import get_sqlite_url, init_db


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("db")
    url = f"sqlite:///{db_dir / 'test.db'}"
    # reset global engine
    import database.db as dbmod

    dbmod._ENGINE = None
    init_db(url)
    with TestClient(app) as c:
        yield c
    dbmod._ENGINE = None


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_screener_run(client):
    r = client.post("/api/v1/screener/run")
    assert r.status_code == 200
    body = r.json()
    assert "candidates" in body
    assert body["count"] == len(body["candidates"])


def test_positions_sync_and_greeks(client):
    payload = {
        "account_id": "MAIN_QUANT_01",
        "trade_date": "2026-08-12",
        "positions": [
            {
                "symbol": "m2609-C-3200",
                "underlying": "M2609",
                "direction": "SHORT",
                "volume": 5,
                "avg_cost": 45.5,
                "current_price": 32.0,
                "multiplier": 10,
                "margin": 18000,
                "delta": 0.20,
                "gamma": 0.001,
                "vega": 12.0,
                "theta": -8.0,
                "option_type": "CALL",
                "strike": 3200,
                "underlying_price": 3000,
                "iv": 0.28,
                "dte": 35,
            },
            {
                "symbol": "m2609-P-2800",
                "underlying": "M2609",
                "direction": "SHORT",
                "volume": 5,
                "avg_cost": 38.0,
                "current_price": 25.0,
                "multiplier": 10,
                "margin": 16500,
                "delta": -0.20,
                "gamma": 0.001,
                "vega": 12.0,
                "theta": -7.0,
                "option_type": "PUT",
                "strike": 2800,
                "underlying_price": 3000,
                "iv": 0.28,
                "dte": 35,
            },
        ],
        "trades_today": [
            {
                "trade_id": "T100293",
                "symbol": "m2609-P-2800",
                "direction": "SELL_OPEN",
                "volume": 5,
                "price": 38.0,
                "fee": 15.0,
            }
        ],
    }
    r = client.post("/api/v1/positions/sync", json=payload)
    assert r.status_code == 200
    assert r.json()["positions_upserted"] == 2

    g = client.get(
        "/api/v1/portfolio/greeks-summary",
        params={"account_id": "MAIN_QUANT_01", "trade_date": "2026-08-12"},
    )
    assert g.status_code == 200
    body = g.json()
    assert body["total_margin_used"] == 34500.0
    assert len(body["by_underlying"]) == 1
    u = body["by_underlying"][0]
    assert u["underlying"] == "M2609"
    assert u["strangle_status"] == "ACTIVE"
    # SHORT: sign -1, deltas 0.20 and -0.20 * 5 => net_delta = -1 + 1 = 0
    assert abs(u["net_delta"]) < 1e-6


def test_charts_price_and_vol(client):
    # ensure screener ran so strikes exist
    client.post("/api/v1/screener/run")
    unders = client.get("/api/v1/charts/underlyings").json()["underlyings"]
    assert unders
    u = unders[0]
    px = client.get(f"/api/v1/charts/price/{u}")
    assert px.status_code == 200
    assert len(px.json()["bars"]) > 10
    vol = client.get(f"/api/v1/charts/vol/{u}")
    assert vol.status_code == 200
    assert len(vol.json()["series"]) > 10
