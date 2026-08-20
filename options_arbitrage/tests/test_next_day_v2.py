"""Tests for v2 next-session screener gates and classification."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data_gates import GateSummary, check_bid_ask_leg, check_iv_history
from core.next_day_screener import next_trading_day
from report.next_day_report import build_next_session_report


def test_iv_history_gate_fails_under_252():
    g = check_iv_history(60, 252)
    assert not g.passed


def test_bid_ask_gate_requires_positive():
    assert not check_bid_ask_leg("call", 0, 1).passed
    assert check_bid_ask_leg("call", 1.0, 1.5).passed


def test_next_trading_day_skips_weekend():
    # 2026-08-20 is Thursday
    assert next_trading_day(date(2026, 8, 20)) == date(2026, 8, 21)


def test_report_no_recommendation_wording():
    meta = {"quote_asof": "t", "target_session": "2026-08-21", "counts": {"scanned": 0, "推荐": 0, "观察": 0, "排除": 0}}
    md = build_next_session_report(meta, [])
    assert "今日无推荐" in md or "无观察" in md
