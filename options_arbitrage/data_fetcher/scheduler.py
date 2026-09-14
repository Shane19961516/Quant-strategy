"""APScheduler jobs: auto quotes + optional CFMMC settlement."""

from __future__ import annotations

import logging
import os
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_SCHEDULER: Optional[BackgroundScheduler] = None


def create_scheduler(
    *,
    quote_cron: str = "*/2 * * * *",
    cfmmc_cron: str = "30 16 * * 1-5",
) -> BackgroundScheduler:
    """Build background scheduler for product feeds (not started)."""
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    def quote_job() -> None:
        from data_fetcher.auto_feed import sync_quotes_for_account

        try:
            sync_quotes_for_account()
        except Exception:  # noqa: BLE001
            logger.exception("scheduled quote sync failed")

    def cfmmc_job() -> None:
        from data_fetcher.auto_feed import sync_cfmmc_settlement

        try:
            sync_cfmmc_settlement()
        except Exception:  # noqa: BLE001
            logger.exception("scheduled CFMMC sync failed")

    scheduler.add_job(quote_job, "cron", **_cron_kwargs(quote_cron), id="auto_quotes")
    # CFMMC after afternoon settlement window (default 16:30 CN weekdays)
    scheduler.add_job(cfmmc_job, "cron", **_cron_kwargs(cfmmc_cron), id="auto_cfmmc")
    return scheduler


def _cron_kwargs(expr: str) -> dict:
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


def start_scheduler() -> BackgroundScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        quote_cron = os.getenv("QUOTE_CRON") or "*/2 * * * *"
        cfmmc_cron = os.getenv("CFMMC_CRON") or "30 16 * * 1-5"
        _SCHEDULER = create_scheduler(quote_cron=quote_cron, cfmmc_cron=cfmmc_cron)
        _SCHEDULER.start()
        logger.info("scheduler started quote_cron=%s cfmmc_cron=%s", quote_cron, cfmmc_cron)
    return _SCHEDULER
