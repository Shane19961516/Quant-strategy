"""FastAPI application entrypoint for the short-strangle vol-arb system."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path when launched as `uvicorn api.main:app`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes_charts import router as charts_router
from api.routes_portfolio import router as portfolio_router
from api.routes_screener import router as screener_router
from database.db import init_db

app = FastAPI(
    title="Short Strangle Vol Arbitrage API",
    description="BS76 futures-options screener, portfolio Greeks, and chart data",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(screener_router)
app.include_router(portfolio_router)
app.include_router(charts_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "strangle_vol_arbitrage",
        "docs": "/docs",
        "health": "/health",
    }
