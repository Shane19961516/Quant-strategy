"""Chart data endpoints for price + IV/HV dual-panel visualization."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import Session, select

from core.screener import run_screener
from data_fetcher.market_data import (
    MarketDataClient,
    snapshot_to_ohlc,
    snapshot_vol_series,
)
from database.db import get_engine
from database.models import ScreenerResult, WatchlistItem

router = APIRouter(prefix="/api/v1/charts", tags=["charts"])

_client = MarketDataClient(use_demo=True)


def _find_snapshot(underlying: str, refresh: bool = False):
    for snap in _client.fetch_snapshots(refresh=refresh):
        if snap.underlying.upper() == underlying.upper():
            return snap
    return None


@router.get("/underlyings")
def list_chart_underlyings() -> dict[str, Any]:
    snaps = _client.fetch_snapshots()
    return {"underlyings": [s.underlying for s in snaps]}


@router.get("/price/{underlying}")
def price_chart(
    underlying: str,
    lookback: int = Query(default=120, ge=30, le=500),
    call_strike: Optional[float] = None,
    put_strike: Optional[float] = None,
) -> dict[str, Any]:
    snap = _find_snapshot(underlying)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"underlying not found: {underlying}")

    # Prefer strikes from latest screener result if not provided
    if call_strike is None or put_strike is None:
        with Session(get_engine()) as session:
            row = session.exec(
                select(ScreenerResult)
                .where(ScreenerResult.underlying == snap.underlying)
                .order_by(ScreenerResult.scan_time.desc())
            ).first()
            if row:
                call_strike = call_strike if call_strike is not None else row.call_strike
                put_strike = put_strike if put_strike is not None else row.put_strike

    if call_strike is None or put_strike is None:
        # derive from live screen
        cands = run_screener([snap])
        if cands:
            call_strike = cands[0].call_strike
            put_strike = cands[0].put_strike

    ohlc = snapshot_to_ohlc(list(snap.prices), lookback=lookback)
    records = []
    for _, r in ohlc.iterrows():
        records.append(
            {
                "date": r["date"].strftime("%Y-%m-%d"),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "donchian_high": float(r["donchian_high"]),
                "donchian_low": float(r["donchian_low"]),
                "bb_upper": float(r["bb_upper"]),
                "bb_mid": float(r["bb_mid"]),
                "bb_lower": float(r["bb_lower"]),
            }
        )
    return {
        "underlying": snap.underlying,
        "F": snap.F,
        "call_strike": call_strike,
        "put_strike": put_strike,
        "bars": records,
    }


@router.get("/vol/{underlying}")
def vol_chart(
    underlying: str,
    lookback: int = Query(default=120, ge=30, le=500),
) -> dict[str, Any]:
    snap = _find_snapshot(underlying)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"underlying not found: {underlying}")
    df = snapshot_vol_series(snap, lookback=lookback)
    series = [
        {
            "date": r["date"].strftime("%Y-%m-%d"),
            "iv": float(r["iv"]),
            "hv30": float(r["hv30"]) if r["hv30"] == r["hv30"] else None,
        }
        for _, r in df.iterrows()
    ]
    return {
        "underlying": snap.underlying,
        "current_iv": snap.current_iv,
        "series": series,
    }


@router.post("/watchlist")
def add_to_watchlist(underlying: str, call_symbol: str, put_symbol: str, note: str = "") -> dict[str, Any]:
    item = WatchlistItem(
        underlying=underlying,
        call_symbol=call_symbol,
        put_symbol=put_symbol,
        note=note,
    )
    with Session(get_engine()) as session:
        session.add(item)
        session.commit()
        session.refresh(item)
        return {
            "id": item.id,
            "underlying": item.underlying,
            "call_symbol": item.call_symbol,
            "put_symbol": item.put_symbol,
            "note": item.note,
        }


@router.get("/order-ticket/{underlying}")
def order_ticket(underlying: str) -> dict[str, Any]:
    """Generate a human-readable short-strangle order ticket text."""
    snap = _find_snapshot(underlying)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"underlying not found: {underlying}")
    cands = run_screener([snap])
    if not cands:
        raise HTTPException(status_code=404, detail="no screener candidate for underlying")
    c = cands[0]
    text = (
        f"【宽跨式卖出报单】\n"
        f"标的: {c.underlying}  现价: {c.F:.2f}\n"
        f"卖出 Call: {c.call_symbol}  K={c.call_strike:.0f}  Δ={c.call_delta:.3f}  权利金≈{c.call_premium:.2f}\n"
        f"卖出 Put : {c.put_symbol}  K={c.put_strike:.0f}  Δ={c.put_delta:.3f}  权利金≈{c.put_premium:.2f}\n"
        f"DTE={c.dte}  POP={c.pop:.1%}  IVR={c.iv_rank:.1f}%  IVP={c.iv_percentile:.1f}%\n"
        f"建议手数: {c.max_pairs} 对  保证金≈{c.total_margin:.0f}  ROI≈{c.expected_roi:.1f}%\n"
        f"风控: 净Δ倾斜阈=1.0 | 账户保证金占用上限 60%\n"
    )
    return {"underlying": c.underlying, "ticket": text, "candidate": c.to_dict()}
