"""Screener REST endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from sqlmodel import Session, select

from api.schemas import ScreenerCandidateOut, ScreenerRunResponse
from core.screener import ShortStrangleCandidate, run_screener
from data_fetcher.market_data import MarketDataClient
from database.db import get_engine, get_positions, save_screener_results
from database.models import DailyPosition, ScreenerResult

router = APIRouter(prefix="/api/v1/screener", tags=["screener"])

_client = MarketDataClient(use_demo=True)


def _account_margin_used(account_id: str, trade_date: Optional[str] = None) -> float:
    with Session(get_engine()) as session:
        positions = get_positions(session, account_id, trade_date)
        if not positions and trade_date is None:
            # fall back to latest trade_date
            all_pos = session.exec(select(DailyPosition).where(DailyPosition.account_id == account_id)).all()
            return float(sum(p.margin for p in all_pos))
        return float(sum(p.margin for p in positions))


def _persist(candidates: list[ShortStrangleCandidate]) -> None:
    rows = [
        ScreenerResult(
            scan_time=datetime.utcnow(),
            underlying=c.underlying,
            dte=c.dte,
            iv_rank=c.iv_rank,
            iv_percentile=c.iv_percentile,
            iv_hv_spread=c.iv_hv_spread,
            call_symbol=c.call_symbol,
            call_strike=c.call_strike,
            call_delta=c.call_delta,
            put_symbol=c.put_symbol,
            put_strike=c.put_strike,
            put_delta=c.put_delta,
            pop=c.pop,
            max_pairs=c.max_pairs,
            total_margin=c.total_margin,
            total_premium=c.total_premium,
            expected_roi=c.expected_roi,
            F=c.F,
            current_iv=c.current_iv,
            hv30=c.hv30,
        )
        for c in candidates
    ]
    with Session(get_engine()) as session:
        save_screener_results(session, rows)


@router.post("/run", response_model=ScreenerRunResponse)
def run_screen(
    account_id: str = Query(default="MAIN_QUANT_01"),
    refresh: bool = Query(default=False),
) -> ScreenerRunResponse:
    margin_used = _account_margin_used(account_id)
    snaps = _client.fetch_snapshots(refresh=refresh)
    candidates = run_screener(snaps, current_margin_used=margin_used)
    _persist(candidates)
    blocked = any(c.blocked_by_margin_cap for c in candidates)
    return ScreenerRunResponse(
        count=len(candidates),
        candidates=[ScreenerCandidateOut(**c.to_dict()) for c in candidates],
        margin_used=margin_used,
        blocked=blocked,
    )


@router.get("/candidates", response_model=ScreenerRunResponse)
def list_candidates(
    account_id: str = Query(default="MAIN_QUANT_01"),
    limit: int = Query(default=50, ge=1, le=500),
) -> ScreenerRunResponse:
    """Return latest persisted screener rows; re-run if DB empty."""
    with Session(get_engine()) as session:
        rows = session.exec(
            select(ScreenerResult).order_by(ScreenerResult.scan_time.desc()).limit(limit)
        ).all()
    if not rows:
        return run_screen(account_id=account_id, refresh=False)

    # de-dupe by underlying keeping latest scan batch time
    latest_time = rows[0].scan_time
    batch = [r for r in rows if r.scan_time == latest_time]
    margin_used = _account_margin_used(account_id)
    outs = [
        ScreenerCandidateOut(
            underlying=r.underlying,
            dte=r.dte,
            F=r.F,
            iv_rank=r.iv_rank,
            iv_percentile=r.iv_percentile,
            iv_hv_spread=r.iv_hv_spread,
            hv30=r.hv30,
            current_iv=r.current_iv,
            call_symbol=r.call_symbol,
            call_strike=r.call_strike,
            call_delta=r.call_delta,
            call_premium=0.0,
            put_symbol=r.put_symbol,
            put_strike=r.put_strike,
            put_delta=r.put_delta,
            put_premium=0.0,
            pop=r.pop,
            max_pairs=r.max_pairs,
            total_margin=r.total_margin,
            total_premium=r.total_premium,
            expected_roi=r.expected_roi,
            unit_margin=r.total_margin / r.max_pairs if r.max_pairs else 0.0,
        )
        for r in batch
    ]
    return ScreenerRunResponse(
        count=len(outs),
        candidates=outs,
        margin_used=margin_used,
        blocked=False,
    )
