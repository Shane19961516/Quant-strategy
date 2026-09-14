"""Unit tests for quote provider symbol mapping and session rules."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data_fetcher.quote_provider import (
    AkshareQuoteProvider,
    is_cn_futures_trading_session,
    to_sina_futures_symbol,
    to_sina_option_symbol,
)

SH = ZoneInfo("Asia/Shanghai")


def test_sina_futures_symbol_czce_expand():
    assert to_sina_futures_symbol("AP610") == "AP2610"
    assert to_sina_futures_symbol("EG2610") == "EG2610"
    assert to_sina_futures_symbol("jd2610") == "JD2610"


def test_sina_option_symbol():
    assert to_sina_option_symbol("EG2610-C-5100") == "eg2610C5100"
    assert to_sina_option_symbol("AP610C8200") == "ap2610C8200"
    assert to_sina_option_symbol("V2610-P-4350") == "v2610P4350"


def test_session_weekend_closed():
    sat = datetime(2026, 8, 15, 10, 0, tzinfo=SH)  # Saturday
    assert is_cn_futures_trading_session(sat) is False


def test_session_day_open():
    mon = datetime(2026, 8, 10, 10, 0, tzinfo=SH)  # Monday
    assert is_cn_futures_trading_session(mon) is True
    night = datetime(2026, 8, 10, 22, 0, tzinfo=SH)
    assert is_cn_futures_trading_session(night) is True
    midday = datetime(2026, 8, 10, 12, 0, tzinfo=SH)
    assert is_cn_futures_trading_session(midday) is False


def test_fetch_underlying_uses_prev_and_last(monkeypatch):
    prov = AkshareQuoteProvider()
    daily = pd.DataFrame(
        [
            {"date": "2026-08-11", "close": 4500.0},
            {"date": "2026-08-12", "close": 4569.0},
            {"date": "2026-08-13", "close": 4555.0},
        ]
    )

    class _AK:
        @staticmethod
        def futures_zh_daily_sina(symbol: str):
            assert symbol == "V2610"
            return daily.copy()

    monkeypatch.setattr("data_fetcher.quote_provider.is_cn_futures_trading_session", lambda now=None: False)
    monkeypatch.setitem(__import__("sys").modules, "akshare", _AK())
    # ensure import inside method sees our stub
    import sys

    sys.modules["akshare"] = _AK()  # type: ignore

    q = prov.fetch_underlying("V2610", date(2026, 8, 13))
    assert q.prev_close == pytest.approx(4569.0)
    assert q.last == pytest.approx(4555.0)  # off-session → session close
    assert q.sina_symbol == "V2610"


def test_fetch_option_hist(monkeypatch):
    prov = AkshareQuoteProvider()
    hist = pd.DataFrame(
        [
            {"date": "2026-08-11", "close": 61.0},
            {"date": "2026-08-12", "close": 61.5},
            {"date": "2026-08-13", "close": 56.0},
        ]
    )

    class _AK:
        @staticmethod
        def option_commodity_hist_sina(symbol: str):
            assert symbol == "eg2610C5100"
            return hist.copy()

    import sys

    sys.modules["akshare"] = _AK()  # type: ignore
    monkeypatch.setattr("data_fetcher.quote_provider.is_cn_futures_trading_session", lambda now=None: False)

    q = prov.fetch_option("EG2610-C-5100", date(2026, 8, 13))
    assert q.prev_close == pytest.approx(61.5)
    assert q.last == pytest.approx(56.0)
