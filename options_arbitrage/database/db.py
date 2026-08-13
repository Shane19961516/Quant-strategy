"""Database engine, session helpers, and CRUD utilities."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterable, Optional

from sqlmodel import Session, SQLModel, create_engine, select

from .models import (
    DailyPosition,
    DailyTrade,
    FuturesManualTrade,
    MarkQuote,
    OptionContractCache,
    ScreenerResult,
    SettlementImport,
    TodayManualTrade,
    WatchlistItem,
    YesterdayOptionPosition,
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


def reset_engine() -> None:
    global _ENGINE
    _ENGINE = None


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


# ---- Settlement CRUD -------------------------------------------------------


def deactivate_settlements(session: Session, account_id: str) -> None:
    rows = session.exec(
        select(SettlementImport).where(
            SettlementImport.account_id == account_id,
            SettlementImport.is_active == True,  # noqa: E712
        )
    ).all()
    for r in rows:
        r.is_active = False


def save_settlement_import(
    session: Session,
    imp: SettlementImport,
    positions: list[YesterdayOptionPosition],
    *,
    replace_active: bool = True,
) -> SettlementImport:
    if replace_active:
        deactivate_settlements(session, imp.account_id)
    session.add(imp)
    session.commit()
    session.refresh(imp)
    for p in positions:
        p.import_id = imp.id  # type: ignore[assignment]
        session.add(p)
    session.commit()
    session.refresh(imp)
    return imp


def get_active_settlement(session: Session, account_id: str) -> Optional[SettlementImport]:
    return session.exec(
        select(SettlementImport)
        .where(SettlementImport.account_id == account_id, SettlementImport.is_active == True)  # noqa: E712
        .order_by(SettlementImport.imported_at.desc())
    ).first()


def get_yesterday_positions(
    session: Session,
    account_id: str,
    settlement_date: Optional[str] = None,
) -> list[YesterdayOptionPosition]:
    if settlement_date:
        return list(
            session.exec(
                select(YesterdayOptionPosition).where(
                    YesterdayOptionPosition.account_id == account_id,
                    YesterdayOptionPosition.settlement_date == settlement_date,
                )
            ).all()
        )
    active = get_active_settlement(session, account_id)
    if not active:
        return []
    return list(
        session.exec(
            select(YesterdayOptionPosition).where(YesterdayOptionPosition.import_id == active.id)
        ).all()
    )


def list_today_trades(
    session: Session,
    account_id: str,
    session_date: str,
) -> list[TodayManualTrade]:
    return list(
        session.exec(
            select(TodayManualTrade)
            .where(
                TodayManualTrade.account_id == account_id,
                TodayManualTrade.session_date == session_date,
            )
            .order_by(TodayManualTrade.created_at.asc())
        ).all()
    )


def add_today_trade(session: Session, trade: TodayManualTrade) -> TodayManualTrade:
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


def delete_today_trade(session: Session, trade_id_pk: int, account_id: str) -> bool:
    row = session.get(TodayManualTrade, trade_id_pk)
    if not row or row.account_id != account_id:
        return False
    session.delete(row)
    session.commit()
    return True


def upsert_mark(
    session: Session,
    account_id: str,
    session_date: str,
    symbol: str,
    price: float,
) -> MarkQuote:
    found = session.exec(
        select(MarkQuote).where(
            MarkQuote.account_id == account_id,
            MarkQuote.session_date == session_date,
            MarkQuote.symbol == symbol,
        )
    ).first()
    if found:
        found.price = price
        session.add(found)
        session.commit()
        session.refresh(found)
        return found
    row = MarkQuote(account_id=account_id, session_date=session_date, symbol=symbol, price=price)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_marks(session: Session, account_id: str, session_date: str) -> dict[str, float]:
    rows = session.exec(
        select(MarkQuote).where(
            MarkQuote.account_id == account_id,
            MarkQuote.session_date == session_date,
        )
    ).all()
    return {r.symbol: r.price for r in rows}


def list_futures_trades(
    session: Session,
    account_id: str,
    session_date: str,
) -> list[FuturesManualTrade]:
    return list(
        session.exec(
            select(FuturesManualTrade)
            .where(
                FuturesManualTrade.account_id == account_id,
                FuturesManualTrade.session_date == session_date,
            )
            .order_by(FuturesManualTrade.created_at.asc())
        ).all()
    )


def add_futures_trade(session: Session, trade: FuturesManualTrade) -> FuturesManualTrade:
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


def delete_futures_trade(session: Session, pk: int, account_id: str) -> bool:
    row = session.get(FuturesManualTrade, pk)
    if not row or row.account_id != account_id:
        return False
    session.delete(row)
    session.commit()
    return True


def clear_futures_trades(session: Session, account_id: str, session_date: str) -> int:
    rows = list_futures_trades(session, account_id, session_date)
    for r in rows:
        session.delete(r)
    session.commit()
    return len(rows)
