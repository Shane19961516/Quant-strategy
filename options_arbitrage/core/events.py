"""Event calendar filter for short-strangle risk windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class MarketEvent:
    event_date: date
    title: str
    severity: str  # HIGH / MEDIUM / LOW
    products: tuple[str, ...]  # product codes affected; empty = broad macro
    source: str = "calendar"


# Static near-term calendar (extend as needed). Dates are trading-impact dates.
# Keep product codes lowercase commodity roots: m, c, i, sr, cf, ag, au, ...
_STATIC_EVENTS: tuple[MarketEvent, ...] = (
    MarketEvent(date(2026, 8, 21), "美国初请失业金 / 周度宏观", "MEDIUM", ()),
    MarketEvent(date(2026, 8, 22), "美国制造业/服务业 PMI 初值窗口", "MEDIUM", ()),
    MarketEvent(date(2026, 8, 26), "USDA 作物进度/出口检验（农产品敏感）", "HIGH", ("m", "c", "y", "p", "a", "rm", "oi", "cf", "sr", "pk")),
    MarketEvent(date(2026, 8, 27), "美联储官员讲话密集窗口（外盘金融属性）", "MEDIUM", ("au", "ag", "cu", "sc")),
    MarketEvent(date(2026, 9, 1), "中国官方制造业 PMI", "HIGH", ()),
    MarketEvent(date(2026, 9, 9), "OPEC+ 产量政策会议窗口", "HIGH", ("sc", "lu", "fu", "bu", "pg")),
    MarketEvent(date(2026, 9, 11), "美国 CPI 发布窗口", "HIGH", ("au", "ag", "cu")),
    MarketEvent(date(2026, 9, 17), "美联储 FOMC 议息", "HIGH", ("au", "ag", "cu", "sc")),
)


@dataclass(frozen=True)
class EventFilterResult:
    blocked: bool
    light_size_only: bool
    events: tuple[MarketEvent, ...]
    notes: tuple[str, ...]


def _normalize_product(product: str) -> str:
    p = product.strip().lower()
    # strip trailing option suffix markers
    if p.endswith("_o"):
        p = p[:-2]
    return p


def upcoming_events(
    *,
    as_of: Optional[date] = None,
    horizon_days: int = 5,
    extra_events: Sequence[MarketEvent] = (),
) -> list[MarketEvent]:
    today = as_of or date.today()
    end = today + timedelta(days=horizon_days)
    pool = list(_STATIC_EVENTS) + list(extra_events)
    return [e for e in pool if today <= e.event_date <= end]


def filter_product_events(
    product: str,
    *,
    as_of: Optional[date] = None,
    horizon_days: int = 5,
    exclude_events: bool = True,
    extra_events: Sequence[MarketEvent] = (),
) -> EventFilterResult:
    """
    Skill step 5: exclude or downsize names with high-risk event windows.

    - HIGH severity matching product (or broad macro) → blocked if exclude_events
    - MEDIUM → light_size_only recommendation
    """
    if not exclude_events:
        return EventFilterResult(False, False, (), ("事件过滤已关闭",))

    prod = _normalize_product(product)
    events = upcoming_events(as_of=as_of, horizon_days=horizon_days, extra_events=extra_events)
    relevant: list[MarketEvent] = []
    for e in events:
        if not e.products or prod in e.products:
            relevant.append(e)

    notes: list[str] = []
    blocked = False
    light = False
    for e in relevant:
        tag = f"{e.event_date.isoformat()} {e.title} [{e.severity}]"
        notes.append(tag)
        if e.severity == "HIGH":
            blocked = True
        elif e.severity == "MEDIUM":
            light = True

    return EventFilterResult(
        blocked=blocked,
        light_size_only=light and not blocked,
        events=tuple(relevant),
        notes=tuple(notes),
    )


def parse_event_row(event_date: str, title: str, severity: str = "HIGH", products: Iterable[str] = ()) -> MarketEvent:
    d = datetime.strptime(event_date[:10], "%Y-%m-%d").date()
    return MarketEvent(d, title, severity.upper(), tuple(_normalize_product(p) for p in products))
