"""Shared helpers: parse settlement XLS path → persist as active 昨仓."""

from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session

from core.settlement_parser import parse_settlement_xls
from database.db import get_engine, save_settlement_import
from database.models import SettlementImport, YesterdayOptionPosition

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def next_session_date(settlement_date: str) -> str:
    try:
        d = datetime.strptime(settlement_date[:10], "%Y-%m-%d").date()
        return (d + timedelta(days=1)).isoformat()
    except ValueError:
        return date.today().isoformat()


def import_settlement_file(
    src: Path,
    *,
    account_id: Optional[str] = None,
    original_filename: Optional[str] = None,
    keep_copy: bool = True,
) -> dict[str, Any]:
    """
    Parse broker/CFMMC settlement workbook and replace active 昨仓 for the account.
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(str(src))
    parsed = parse_settlement_xls(src)
    acct = (account_id or parsed.fund.account_id or "UNKNOWN").strip()
    fname = original_filename or src.name
    dest = UPLOAD_DIR / f"{acct}_{parsed.fund.trade_date}_{fname}"
    if keep_copy:
        if src.resolve() != dest.resolve():
            shutil.copy2(str(src), str(dest))
    else:
        shutil.move(str(src), str(dest))

    imp = SettlementImport(
        account_id=acct,
        settlement_date=parsed.fund.trade_date,
        client_name=parsed.fund.client_name,
        broker=parsed.fund.broker,
        prev_balance=parsed.fund.prev_balance,
        balance=parsed.fund.balance,
        client_equity=parsed.fund.client_equity,
        margin_occupied=parsed.fund.margin_occupied,
        available=parsed.fund.available,
        risk_degree=parsed.fund.risk_degree,
        premium_net=parsed.fund.premium_net,
        commission=parsed.fund.commission,
        realized_pnl=parsed.fund.realized_pnl,
        filename=fname,
        is_active=True,
    )
    positions = [
        YesterdayOptionPosition(
            import_id=0,
            account_id=acct,
            settlement_date=parsed.fund.trade_date,
            symbol=p.symbol,
            underlying=p.underlying,
            option_type=p.option_type,
            strike=p.strike,
            long_volume=p.long_volume,
            long_avg_price=p.long_avg_price,
            short_volume=p.short_volume,
            short_avg_price=p.short_avg_price,
            prev_settle=p.prev_settle,
            settle_price=p.settle_price,
            margin=p.margin,
            multiplier=p.multiplier,
            trade_code=p.trade_code,
        )
        for p in parsed.option_positions
    ]
    with Session(get_engine()) as session:
        saved = save_settlement_import(session, imp, positions, replace_active=True)
        import_id = int(saved.id) if saved.id is not None else 0

    return {
        "import_id": import_id,
        "account_id": acct,
        "settlement_date": parsed.fund.trade_date,
        "suggested_session_date": next_session_date(parsed.fund.trade_date),
        "client_name": parsed.fund.client_name,
        "broker": parsed.fund.broker,
        "client_equity": parsed.fund.client_equity,
        "margin_occupied": parsed.fund.margin_occupied,
        "available": parsed.fund.available,
        "risk_degree": parsed.fund.risk_degree,
        "position_count": len(positions),
        "short_lots": sum(p.short_volume for p in parsed.option_positions),
        "long_lots": sum(p.long_volume for p in parsed.option_positions),
        "filename": fname,
        "stored_path": str(dest),
        "message": "昨日结算单已导入；当日成交请在 /today-trades 手动录入（与持仓分离）",
    }
