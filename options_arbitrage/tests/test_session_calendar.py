"""Unit tests: CN session calendar & night-session 昨收口径."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.session_calendar import (
    is_night_clock,
    most_recent_day_close_date,
    suggested_session_date,
)
from core.pnl_engine import compute_live_pnl

SH = ZoneInfo("Asia/Shanghai")


def test_night_after_2100_uses_same_day_close_date():
    now = datetime(2026, 8, 12, 21, 5, tzinfo=SH)
    assert is_night_clock(now)
    assert most_recent_day_close_date(now).isoformat() == "2026-08-12"
    assert suggested_session_date(now).isoformat() == "2026-08-13"


def test_early_morning_night_uses_yesterday_close_date():
    now = datetime(2026, 8, 13, 1, 0, tzinfo=SH)
    assert is_night_clock(now)
    assert most_recent_day_close_date(now).isoformat() == "2026-08-12"


def test_day_session_prev_close_is_prior_weekday():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=SH)
    assert not is_night_clock(now)
    assert most_recent_day_close_date(now).isoformat() == "2026-08-12"


def test_pnl_prefers_day_close_over_settle():
    """夜盘：昨收用日盘收盘价，不用结算价。"""
    y = [
        {
            "symbol": "EG2610-C-5200",
            "underlying": "EG2610",
            "option_type": "CALL",
            "strike": 5200,
            "long_volume": 0,
            "short_volume": 5,
            "settle_price": 44.0,  # 结算
            "prev_close": 40.0,  # 日盘收盘
            "multiplier": 10,
        }
    ]
    report = compute_live_pnl(
        account_id="166308",
        settlement_date="2026-08-12",
        session_date="2026-08-13",
        yesterday_positions=y,
        today_trades=[],
        marks={"EG2610-C-5200": 34.0},
    )
    # -5*(34-40)*10 = 300  （若误用 settle44 → 500）
    assert report.total_carry_pnl == 300.0
