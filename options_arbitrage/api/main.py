"""FastAPI application entrypoint for the short-strangle vol-arb system."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load optional .env next to package (CFMMC / quote settings)
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from api.routes_charts import router as charts_router
from api.routes_portfolio import router as portfolio_router
from api.routes_screener import router as screener_router
from api.routes_settlement import router as settlement_router
from database.db import clear_all_session_trades, get_engine, init_db

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Short Strangle Vol Arbitrage & Settlement Monitor",
    description=(
        "自动行情(akshare/CTP) · 结算单手动/CFMMC · 实时盈亏 · BS76 希腊值风控"
    ),
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settlement_router)
app.include_router(screener_router)
app.include_router(portfolio_router)
app.include_router(charts_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    with Session(get_engine()) as session:
        cleared = clear_all_session_trades(session)
    logger.info(
        "startup cleared session trades: options=%s futures=%s",
        cleared["today_option_trades"],
        cleared["futures_trades"],
    )
    print(
        f"[startup] cleared today trades: "
        f"options={cleared['today_option_trades']} futures={cleared['futures_trades']}",
        flush=True,
    )

    # Zero-touch feeds: CFMMC (if creds) + quotes in background; schedule refresh
    try:
        from data_fetcher.auto_feed import run_auto_feed_background
        from data_fetcher.scheduler import start_scheduler

        run_auto_feed_background(
            include_cfmmc=os.getenv("AUTO_CFMMC", "1") not in {"0", "false", "False"},
            include_quotes=os.getenv("AUTO_QUOTES", "1") not in {"0", "false", "False"},
        )
        if os.getenv("AUTO_SCHEDULER", "1") not in {"0", "false", "False"}:
            start_scheduler()
            print("[startup] auto feed + scheduler started", flush=True)
        else:
            print("[startup] auto feed kicked (scheduler disabled)", flush=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto feed startup failed: %s", exc)
        print(f"[startup] auto feed failed: {exc}", flush=True)


@app.get("/health")
def health() -> dict:
    from data_fetcher.auto_feed import feed_status
    from core.session_calendar import price_basis_note

    return {
        "status": "ok",
        "version": "0.3.0",
        "price_basis": price_basis_note(),
        "feed": feed_status(),
    }


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "strangle_vol_arbitrage",
        "docs": "/docs",
        "health": "/health",
        "settlement_upload": "POST /api/v1/settlement/upload",
        "cfmmc_sync": "POST /api/v1/settlement/cfmmc-sync",
        "auto_feed": "POST /api/v1/settlement/auto-feed",
        "live_pnl": "GET /api/v1/settlement/live-pnl",
    }
