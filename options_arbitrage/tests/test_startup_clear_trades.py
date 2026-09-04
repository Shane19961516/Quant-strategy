"""Startup clears hand-entered today trades."""

from __future__ import annotations

from sqlmodel import Session, select

from database.db import (
    add_futures_trade,
    add_today_trade,
    clear_all_session_trades,
    get_engine,
    init_db,
    list_today_trades,
)
from database.models import FuturesManualTrade, TodayManualTrade


def test_clear_all_session_trades_wipes_options_and_futures():
    init_db()
    with Session(get_engine()) as session:
        # isolate from leftover demo seed data
        clear_all_session_trades(session)
        add_today_trade(
            session,
            TodayManualTrade(
                account_id="166308",
                session_date="2026-08-13",
                trade_id="T1",
                symbol="V2610-C-4850",
                underlying="V2610",
                option_type="CALL",
                strike=4850,
                side="SELL",
                offset="OPEN",
                price=20.5,
                volume=5,
                fee=0.0,
                premium_cash=512.5,
                multiplier=5.0,
            ),
        )
        add_futures_trade(
            session,
            FuturesManualTrade(
                account_id="166308",
                session_date="2026-08-13",
                trade_id="F1",
                symbol="V2610",
                side="SELL",
                volume=1,
                price=4515.0,
                last=4555.0,
                fee=0.0,
                multiplier=5.0,
            ),
        )
        assert len(list_today_trades(session, "166308", "2026-08-13")) == 1
        cleared = clear_all_session_trades(session)
        assert cleared["today_option_trades"] == 1
        assert cleared["futures_trades"] == 1
        assert list_today_trades(session, "166308", "2026-08-13") == []
        assert session.exec(select(FuturesManualTrade)).all() == []
