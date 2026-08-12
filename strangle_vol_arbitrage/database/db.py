"""Database engine, session helpers, and CRUD utilities."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterable, Optional

from sqlmodel import Session, SQLModel, create_engine, select

from .models import (
    DailyPosition,
    DailyTrade,
    OptionContractCache,
    ScreenerResult,
    WatchlistItem,
)

_ENGINE = None


def get_sqlite_url(url: Optional[str] = None) -> str:
    if url:
        return url
    data_dir = Path(__file__).resolve().parents[1] / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'strangle.db'}"


def get_engine(url: Optional[str] = None, *, echo: bool = False):
    global _ENGINE
    if _ENGINE is None or url is not None:
        connect_args = {"check_same_thread": False}
        _ENGINE = create_engine(get_sqlite_url(url), echo=echo, connect_args=connect_args)
    return _ENGINE


def init_db(url: Optional[str] = None) -> None:
    engine = get_engine(url)
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(url: Optional[str] = None) -> Generator[Session, None, None]:
    engine = get_engine(url)
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def save_screener_results(session: Session, rows: Iterable[ScreenerResult]) -> int:
    count = 0
    for row in rows:
        session.add(row)
        count += 1
    session.commit()
    return count


def latest_screener_results(session: Session, limit: int = 100) -> list[ScreenerResult]:
    stmt = select(ScreenerResult).order_by(ScreenerResult.scan_time.desc()).limit(limit)
    return list(session.exec(stmt).all())


def replace_positions(
    session: Session,
    account_id: str,
    trade_date: str,
    positions: list[DailyPosition],
) -> int:
    existing = session.exec(
        select(DailyPosition).where(
            DailyPosition.account_id == account_id,
            DailyPosition.trade_date == trade_date,
        )
    ).all()
    for row in existing:
        session.delete(row)
    for pos in positions:
        session.add(pos)
    session.commit()
    return len(positions)


def upsert_trades(session: Session, trades: list[DailyTrade]) -> int:
    count = 0
    for t in trades:
        found = session.exec(
            select(DailyTrade).where(
                DailyTrade.account_id == t.account_id,
                DailyTrade.trade_id == t.trade_id,
            )
        ).first()
        if found:
            found.symbol = t.symbol
            found.direction = t.direction
            found.volume = t.volume
            found.price = t.price
            found.fee = t.fee
            found.trade_date = t.trade_date
        else:
            session.add(t)
        count += 1
    session.commit()
    return count


def get_positions(
    session: Session,
    account_id: str,
    trade_date: Optional[str] = None,
) -> list[DailyPosition]:
    stmt = select(DailyPosition).where(DailyPosition.account_id == account_id)
    if trade_date:
        stmt = stmt.where(DailyPosition.trade_date == trade_date)
    return list(session.exec(stmt).all())


def add_watchlist(session: Session, item: WatchlistItem) -> WatchlistItem:
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def cache_contracts(session: Session, contracts: list[OptionContractCache]) -> int:
    for c in contracts:
        session.add(c)
    session.commit()
    return len(contracts)
