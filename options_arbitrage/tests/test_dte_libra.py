"""DTE conventions aligned with Libra 数据总览 (2026-08-13 cross-check)."""

from datetime import date

from core.greeks_book import estimate_dte_from_underlying


def test_dce_calendar_dte_matches_libra():
    asof = date(2026, 8, 13)
    assert estimate_dte_from_underlying("EG2610", asof) == 25
    assert estimate_dte_from_underlying("V2610", asof) == 25
    assert estimate_dte_from_underlying("JD2610", asof) == 25


def test_ap_trading_day_dte_matches_libra():
    """
    CZCE 鲜苹果期权：交割月前两个月月末倒数第 3 个交易日。
    AP610 → 2026-08-27；Libra days_to_expiry 用交易日计数 = 11。
    """
    asof = date(2026, 8, 13)
    assert estimate_dte_from_underlying("AP610", asof) == 11
