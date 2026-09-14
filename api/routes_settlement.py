"""Settlement upload, yesterday positions, today trades, and live PnL APIs."""

from __future__ import annotations

import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session

from core.pnl_engine import compute_live_pnl
from core.settlement_parser import (
    lookup_multiplier,
    parse_option_symbol,
    parse_settlement_xls,
)
from database.db import (
    add_today_trade,
    delete_today_trade,
    get_active_settlement,
    get_engine,
    get_marks,
    get_yesterday_positions,
    list_today_trades,
    save_settlement_import,
    upsert_mark,
)
from database.models import SettlementImport, TodayManualTrade, YesterdayOptionPosition

router = APIRouter(prefix="/api/v1/settlement", tags=["settlement"])

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class TodayTradeIn(BaseModel):
    account_id: str = "166308"
    session_date: str
    trade_id: Optional[str] = None
    symbol: str
    side: str  # BUY / SELL / 买 / 卖
    offset: str = "OPEN"  # OPEN / CLOSE / 开 / 平
    price: float
    volume: int
    fee: float = 0.0
    trade_time: str = ""
    note: str = ""


class MarkIn(BaseModel):
    account_id: str = "166308"
    session_date: str
    symbol: str
    price: float


class MarksBatchIn(BaseModel):
    account_id: str = "166308"
    session_date: str
    marks: dict[str, float]


def _norm_side(side: str) -> str:
    s = side.strip().upper()
    if s in {"买", "BUY", "B", "LONG"} or "买" in side:
        return "BUY"
    if s in {"卖", "SELL", "S", "SHORT"} or "卖" in side:
        return "SELL"
    if s in {"BUY", "SELL"}:
        return s
    raise HTTPException(status_code=400, detail=f"invalid side: {side}")


def _norm_offset(offset: str) -> str:
    s = offset.strip().upper()
    if "平" in offset or s in {"CLOSE", "C"}:
        return "CLOSE"
    return "OPEN"


def _next_session_date(settlement_date: str) -> str:
    try:
        d = datetime.strptime(settlement_date[:10], "%Y-%m-%d").date()
        return (d + timedelta(days=1)).isoformat()
    except ValueError:
        return date.today().isoformat()


@router.post("/upload")
async def upload_settlement(
    file: UploadFile = File(...),
    account_id: Optional[str] = Form(default=None),
) -> dict[str, Any]:
    """Upload yesterday's broker settlement .xls — becomes 昨日持仓基线."""
    if not file.filename or not file.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(status_code=400, detail="请上传 .xls / .xlsx 结算单")

    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        parsed = parse_settlement_xls(tmp_path)
    except Exception as exc:  # noqa: BLE001
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"结算单解析失败: {exc}") from exc

    acct = (account_id or parsed.fund.account_id or "UNKNOWN").strip()
    dest = UPLOAD_DIR / f"{acct}_{parsed.fund.trade_date}_{file.filename}"
    shutil.move(str(tmp_path), str(dest))

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
        filename=file.filename,
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

    session_date = _next_session_date(parsed.fund.trade_date)
    with Session(get_engine()) as session:
        saved = save_settlement_import(session, imp, positions, replace_active=True)
        import_id = int(saved.id) if saved.id is not None else 0
        # seed marks with settlement 今结算价 for next session
        for p in parsed.option_positions:
            upsert_mark(session, acct, session_date, p.symbol, p.settle_price)

    return {
        "import_id": import_id,
        "account_id": acct,
        "settlement_date": parsed.fund.trade_date,
        "suggested_session_date": session_date,
        "client_name": parsed.fund.client_name,
        "broker": parsed.fund.broker,
        "client_equity": parsed.fund.client_equity,
        "margin_occupied": parsed.fund.margin_occupied,
        "available": parsed.fund.available,
        "risk_degree": parsed.fund.risk_degree,
        "position_count": len(positions),
        "short_lots": sum(p.short_volume for p in parsed.option_positions),
        "long_lots": sum(p.long_volume for p in parsed.option_positions),
        "filename": file.filename,
        "message": "昨日结算单已导入；当日成交请在 /today-trades 手动录入（与持仓分离）",
    }


@router.get("/active")
def active_settlement(account_id: str = Query(default="166308")) -> dict[str, Any]:
    with Session(get_engine()) as session:
        imp = get_active_settlement(session, account_id)
        if not imp:
            raise HTTPException(status_code=404, detail="无有效结算单，请先上传")
        positions = get_yesterday_positions(session, account_id)
        return {
            "import_id": imp.id,
            "account_id": imp.account_id,
            "settlement_date": imp.settlement_date,
            "suggested_session_date": _next_session_date(imp.settlement_date),
            "client_name": imp.client_name,
            "broker": imp.broker,
            "client_equity": imp.client_equity,
            "margin_occupied": imp.margin_occupied,
            "available": imp.available,
            "risk_degree": imp.risk_degree,
            "balance": imp.balance,
            "commission": imp.commission,
            "premium_net": imp.premium_net,
            "realized_pnl": imp.realized_pnl,
            "filename": imp.filename,
            "positions": [
                {
                    "symbol": p.symbol,
                    "underlying": p.underlying,
                    "option_type": p.option_type,
                    "strike": p.strike,
                    "long_volume": p.long_volume,
                    "long_avg_price": p.long_avg_price,
                    "short_volume": p.short_volume,
                    "short_avg_price": p.short_avg_price,
                    "prev_settle": p.prev_settle,
                    "settle_price": p.settle_price,
                    "margin": p.margin,
                    "multiplier": p.multiplier,
                }
                for p in positions
            ],
        }


@router.get("/yesterday-positions")
def yesterday_positions(
    account_id: str = Query(default="166308"),
) -> dict[str, Any]:
    with Session(get_engine()) as session:
        imp = get_active_settlement(session, account_id)
        rows = get_yesterday_positions(session, account_id)
        return {
            "account_id": account_id,
            "settlement_date": imp.settlement_date if imp else None,
            "count": len(rows),
            "positions": [
                {
                    "symbol": p.symbol,
                    "underlying": p.underlying,
                    "option_type": p.option_type,
                    "strike": p.strike,
                    "long_volume": p.long_volume,
                    "short_volume": p.short_volume,
                    "long_avg_price": p.long_avg_price,
                    "short_avg_price": p.short_avg_price,
                    "prev_settle": p.prev_settle,
                    "settle_price": p.settle_price,
                    "margin": p.margin,
                    "multiplier": p.multiplier,
                }
                for p in rows
            ],
        }


@router.post("/today-trades")
def create_today_trade(body: TodayTradeIn) -> dict[str, Any]:
    """手动录入当日成交（与昨日持仓分离存储）。"""
    side = _norm_side(body.side)
    offset = _norm_offset(body.offset)
    meta = parse_option_symbol(body.symbol)
    mult = lookup_multiplier(meta["underlying"])
    trade_id = body.trade_id or f"M{datetime.utcnow().strftime('%H%M%S%f')}"
    premium_cash = body.price * body.volume * mult * (1 if side == "SELL" else -1)

    row = TodayManualTrade(
        account_id=body.account_id,
        session_date=body.session_date,
        trade_id=trade_id,
        symbol=body.symbol.strip(),
        underlying=meta["underlying"],
        option_type=meta["option_type"],
        strike=meta["strike"],
        side=side,
        offset=offset,
        price=body.price,
        volume=body.volume,
        fee=body.fee,
        premium_cash=premium_cash,
        multiplier=mult,
        trade_time=body.trade_time,
        note=body.note,
    )
    with Session(get_engine()) as session:
        saved = add_today_trade(session, row)
        return {
            "id": saved.id,
            "trade_id": saved.trade_id,
            "symbol": saved.symbol,
            "side": saved.side,
            "offset": saved.offset,
            "price": saved.price,
            "volume": saved.volume,
            "fee": saved.fee,
            "premium_cash": saved.premium_cash,
            "multiplier": saved.multiplier,
            "session_date": saved.session_date,
        }


@router.get("/today-trades")
def get_today_trades(
    account_id: str = Query(default="166308"),
    session_date: str = Query(...),
) -> dict[str, Any]:
    with Session(get_engine()) as session:
        rows = list_today_trades(session, account_id, session_date)
        return {
            "account_id": account_id,
            "session_date": session_date,
            "count": len(rows),
            "trades": [
                {
                    "id": r.id,
                    "trade_id": r.trade_id,
                    "symbol": r.symbol,
                    "underlying": r.underlying,
                    "option_type": r.option_type,
                    "strike": r.strike,
                    "side": r.side,
                    "offset": r.offset,
                    "price": r.price,
                    "volume": r.volume,
                    "fee": r.fee,
                    "premium_cash": r.premium_cash,
                    "multiplier": r.multiplier,
                    "trade_time": r.trade_time,
                    "note": r.note,
                }
                for r in rows
            ],
        }


@router.delete("/today-trades/{pk}")
def remove_today_trade(pk: int, account_id: str = Query(default="166308")) -> dict[str, Any]:
    with Session(get_engine()) as session:
        ok = delete_today_trade(session, pk, account_id)
        if not ok:
            raise HTTPException(status_code=404, detail="trade not found")
        return {"deleted": pk}


@router.post("/marks")
def set_mark(body: MarkIn) -> dict[str, Any]:
    with Session(get_engine()) as session:
        row = upsert_mark(session, body.account_id, body.session_date, body.symbol, body.price)
        return {"symbol": row.symbol, "price": row.price, "session_date": row.session_date}


@router.post("/marks/batch")
def set_marks_batch(body: MarksBatchIn) -> dict[str, Any]:
    with Session(get_engine()) as session:
        for sym, px in body.marks.items():
            upsert_mark(session, body.account_id, body.session_date, sym, float(px))
        return {"updated": len(body.marks)}


@router.get("/live-pnl")
def live_pnl(
    account_id: str = Query(default="166308"),
    session_date: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """实时盈亏：昨日持仓盯市 + 当日成交盈亏 − 手续费。"""
    with Session(get_engine()) as session:
        imp = get_active_settlement(session, account_id)
        if not imp:
            raise HTTPException(status_code=404, detail="无有效结算单，请先上传昨日结算单")
        sess = session_date or _next_session_date(imp.settlement_date)
        y_rows = get_yesterday_positions(session, account_id)
        t_rows = list_today_trades(session, account_id, sess)
        marks = get_marks(session, account_id, sess)

        yesterday = [
            {
                "symbol": p.symbol,
                "underlying": p.underlying,
                "option_type": p.option_type,
                "strike": p.strike,
                "long_volume": p.long_volume,
                "short_volume": p.short_volume,
                "long_avg_price": p.long_avg_price,
                "short_avg_price": p.short_avg_price,
                "settle_price": p.settle_price,
                "margin": p.margin,
                "multiplier": p.multiplier,
            }
            for p in y_rows
        ]
        # default mark = settlement price if not overridden
        for p in y_rows:
            marks.setdefault(p.symbol, p.settle_price)

        today = [
            {
                "symbol": t.symbol,
                "underlying": t.underlying,
                "option_type": t.option_type,
                "strike": t.strike,
                "side": t.side,
                "offset": t.offset,
                "price": t.price,
                "volume": t.volume,
                "fee": t.fee,
                "multiplier": t.multiplier,
                "trade_id": t.trade_id,
                "trade_time": t.trade_time,
                "trade_date": t.session_date,
            }
            for t in t_rows
        ]

        report = compute_live_pnl(
            account_id=account_id,
            settlement_date=imp.settlement_date,
            session_date=sess,
            yesterday_positions=yesterday,
            today_trades=today,
            marks=marks,
            opening_equity=imp.client_equity,
            margin_occupied_settlement=imp.margin_occupied,
            available_settlement=imp.available,
            risk_degree_settlement=imp.risk_degree,
        )
        out = report.to_dict()
        out["marks"] = marks
        out["yesterday_position_count"] = len(yesterday)
        out["today_trade_count"] = len(today)
        return out
