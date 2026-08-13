"""FastAPI application entrypoint for the short-strangle vol-arb system."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes_charts import router as charts_router
from api.routes_portfolio import router as portfolio_router
from api.routes_screener import router as screener_router
from api.routes_settlement import router as settlement_router
from database.db import init_db, get_engine, clear_all_session_trades
from sqlmodel import Session
import logging

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Short Strangle Vol Arbitrage & Settlement Monitor",
    description=(
        "昨日结算单导入 · 当日成交手录 · 实时盈亏监控 · BS76 筛选与组合风控"
    ),
    version="0.2.0",
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
    # 每次启动清空「当日成交录入」（期权 + 期货对冲），避免沿用上一次手录
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.2.0"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "strangle_vol_arbitrage",
        "docs": "/docs",
        "health": "/health",
        "settlement_upload": "POST /api/v1/settlement/upload",
        "live_pnl": "GET /api/v1/settlement/live-pnl",
    }
