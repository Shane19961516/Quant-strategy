"""APScheduler jobs for market-data refresh and screener re-runs."""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def create_scheduler(
    *,
    market_cron: str = "*/5 * * * *",
    screener_cron: str = "0 * * * *",
) -> BackgroundScheduler:
    """Build a background scheduler (not started)."""
    scheduler = BackgroundScheduler()

    def refresh_market() -> None:
        from data_fetcher.market_data import MarketDataClient

        client = MarketDataClient(use_demo=True)
        snaps = client.fetch_snapshots(refresh=True)
        logger.info("market refresh: %d underlyings", len(snaps))

    def run_screen_job() -> None:
        from core.screener import run_screener
        from data_fetcher.market_data import MarketDataClient

        snaps = MarketDataClient(use_demo=True).fetch_snapshots()
        results = run_screener(snaps)
        logger.info("screener job: %d candidates", len(results))

    scheduler.add_job(refresh_market, "cron", **_cron_kwargs(market_cron), id="market_refresh")
    scheduler.add_job(run_screen_job, "cron", **_cron_kwargs(screener_cron), id="screener_run")
    return scheduler


def _cron_kwargs(expr: str) -> dict:
    """Parse a 5-field cron expression into APScheduler kwargs."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"expected 5-field cron, got: {expr}")
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


_SCHEDULER: Optional[BackgroundScheduler] = None


def start_scheduler() -> BackgroundScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = create_scheduler()
        _SCHEDULER.start()
    return _SCHEDULER
