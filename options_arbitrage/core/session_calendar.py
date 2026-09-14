"""CN futures session calendar helpers (Asia/Shanghai).

Trading-day conventions (commodities with night session):
  - Night of calendar day D 21:00 → next calendar morning ~02:30 belongs to
    trading day D+1 (or next trade date).
  - Day session D 09:00–15:00 belongs to trading day D.

Libra / desk 浮动盈亏 昨收口径（有夜盘）:
  夜盘 21:00 开盘后，上一日收盘价 = **当天下午 15:00 日盘收盘价**
  （不是结算单「今结算价」，也不是再往前推一天的收盘价）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")

# Products that commonly have a night session (options follow underlying).
NIGHT_SESSION_PRODUCTS = {
    "V",
    "EG",
    "M",
    "C",
    "I",
    "L",
    "PP",
    "P",
    "Y",
    "A",
    "LH",
    "PG",
    "EB",
    "SR",
    "CF",
    "TA",
    "MA",
    "RM",
    "OI",
    "FG",
    "SA",
    "UR",
    "AU",
    "AG",
    "CU",
    "RB",
    "RU",
    "SC",
    "LC",
    "SI",
}

# No night for these (desk: AP apple / JD egg options often flat at night)
NO_NIGHT_PRODUCTS = {"AP", "JD", "LH"}  # LH sometimes night; keep JD/AP strict


def cn_now(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(SH_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=SH_TZ)
    return now.astimezone(SH_TZ)


def is_night_clock(now: Optional[datetime] = None) -> bool:
    """True during 20:55–24:00 or 00:00–02:35 Asia/Shanghai (weekday rules loose)."""
    now = cn_now(now)
    t = now.time()
    return t >= time(20, 55) or t <= time(2, 35)


def is_day_session_clock(now: Optional[datetime] = None) -> bool:
    now = cn_now(now)
    t = now.time()
    return (time(8, 55) <= t <= time(11, 30)) or (time(13, 25) <= t <= time(15, 15))


def most_recent_day_close_date(now: Optional[datetime] = None) -> date:
    """
    Calendar date whose **15:00 day-session close** is the current 昨收 reference.

    - Night (20:55+): today's date (afternoon just finished).
    - Early morning night remnant (≤02:35): yesterday's date.
    - Day session / other: previous calendar weekday as proxy for prior day close
      (callers should still resolve via actual trade bars).
    """
    now = cn_now(now)
    t = now.time()
    if t >= time(20, 55):
        return now.date()
    if t <= time(2, 35):
        return now.date() - timedelta(days=1)
    # Day session or midday gap: 昨收 = previous trade day's close
    d = now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def suggested_session_date(now: Optional[datetime] = None) -> date:
    """
    Futures trading date label for the current clock.
    Night from D 20:55 → trading date D+1 (skip weekend).
    """
    now = cn_now(now)
    t = now.time()
    d = now.date()
    if t >= time(20, 55):
        d = d + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d
    if t <= time(2, 35):
        # still previous night's trading date = calendar today (Mon morning = Mon)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    return d


def product_has_night_session(product_code: str) -> bool:
    p = str(product_code or "").upper()
    if p in NO_NIGHT_PRODUCTS:
        return False
    return p in NIGHT_SESSION_PRODUCTS or p not in NO_NIGHT_PRODUCTS


def price_basis_note(now: Optional[datetime] = None) -> str:
    now = cn_now(now)
    close_d = most_recent_day_close_date(now)
    sess = suggested_session_date(now)
    phase = "夜盘" if is_night_clock(now) else ("日盘" if is_day_session_clock(now) else "休市/间隙")
    return (
        f"{phase} | 交易日={sess.isoformat()} | "
        f"昨收基准日(日盘收盘)={close_d.isoformat()} | "
        f"口径=日盘15:00收盘价(非结算价)"
    )
