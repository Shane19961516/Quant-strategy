"""Zero-touch market + settlement feed for product delivery.

- Auto quote sync (akshare / CTP via QUOTE_PROVIDER) into MarkQuote
- Optional CFMMC settlement pull when CFMMC_USER/PASSWORD are set
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session

from core.session_calendar import (
    cn_now,
    price_basis_note,
    suggested_session_date,
)
from core.settlement_import_service import import_settlement_file, next_session_date
from database.db import (
    delete_marks,
    get_active_settlement,
    get_engine,
    get_yesterday_positions,
    list_today_trades,
    upsert_mark,
)

logger = logging.getLogger(__name__)

_FEED_LOCK = threading.Lock()
_LAST_STATUS: dict[str, Any] = {
    "quotes": None,
    "cfmmc": None,
    "updated_at": None,
}


def feed_status() -> dict[str, Any]:
    return dict(_LAST_STATUS)


def default_account_id() -> str:
    return (
        os.getenv("CFMMC_ACCOUNT_ID")
        or os.getenv("ACCOUNT_ID")
        or "166308"
    ).strip()


def resolve_session_date(account_id: str, session_date: Optional[str] = None) -> str:
    if session_date:
        return session_date[:10]
    with Session(get_engine()) as session:
        imp = get_active_settlement(session, account_id)
        if imp:
            return next_session_date(imp.settlement_date)
    return suggested_session_date().isoformat()


def sync_quotes_for_account(
    account_id: Optional[str] = None,
    *,
    session_date: Optional[str] = None,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    """Pull akshare/CTP quotes and persist marks for the active book."""
    from data_fetcher.quote_provider import fetch_book_quotes

    acct = (account_id or default_account_id()).strip()
    sess = resolve_session_date(acct, session_date)
    prov = (provider or os.getenv("QUOTE_PROVIDER") or "akshare").lower()

    with Session(get_engine()) as session:
        y_rows = get_yesterday_positions(session, acct)
        t_rows = list_today_trades(session, acct, sess)
        underlyings = sorted(
            {p.underlying for p in y_rows} | {t.underlying for t in t_rows if t.underlying}
        )
        options = sorted({p.symbol for p in y_rows} | {t.symbol for t in t_rows})
        if not underlyings and not options:
            result = {
                "ok": False,
                "skipped": True,
                "reason": "no positions/trades — import settlement first",
                "account_id": acct,
                "session_date": sess,
                "price_basis": price_basis_note(),
            }
            _LAST_STATUS["quotes"] = result
            _LAST_STATUS["updated_at"] = cn_now().isoformat()
            return result

        payload = fetch_book_quotes(
            underlyings=underlyings,
            option_symbols=options,
            asof=sess,
            provider=prov,
        )
        written = 0
        for sym, px in (payload.get("marks") or {}).items():
            upsert_mark(session, acct, sess, sym, float(px))
            written += 1
        cleared = delete_marks(session, acct, sess, list(payload.get("clear_live_symbols") or []))

    result = {
        "ok": True,
        "account_id": acct,
        "session_date": sess,
        "provider": payload.get("provider"),
        "in_session": payload.get("in_session"),
        "night_session": payload.get("night_session"),
        "day_close_ref": payload.get("day_close_ref"),
        "price_basis": payload.get("price_basis"),
        "written": written,
        "cleared_live": cleared,
        "errors": payload.get("errors") or [],
        "quote_count": len(payload.get("quotes") or []),
        "rules": payload.get("rules"),
    }
    _LAST_STATUS["quotes"] = result
    _LAST_STATUS["updated_at"] = cn_now().isoformat()
    logger.info(
        "auto quote sync account=%s sess=%s written=%s cleared=%s errors=%s",
        acct,
        sess,
        written,
        cleared,
        len(result["errors"]),
    )
    return result


def sync_cfmmc_settlement(
    account_id: Optional[str] = None,
    *,
    trade_date: Optional[str] = None,
    skip_if_same_date: bool = True,
) -> dict[str, Any]:
    """Login CFMMC and import 逐日盯市 settlement when credentials exist."""
    from data_fetcher.cfmmc_client import CfmmcError, download_settlement_xls, previous_trading_day
    from core.settlement_import_service import UPLOAD_DIR

    user = (os.getenv("CFMMC_USER") or "").strip()
    password = os.getenv("CFMMC_PASSWORD") or ""
    if not user or not password:
        result = {
            "ok": False,
            "skipped": True,
            "reason": "CFMMC_USER/CFMMC_PASSWORD not set",
        }
        _LAST_STATUS["cfmmc"] = result
        return result

    acct = (account_id or default_account_id()).strip()
    td = (trade_date or previous_trading_day().isoformat())[:10]

    if skip_if_same_date:
        with Session(get_engine()) as session:
            imp = get_active_settlement(session, acct)
            if imp and imp.settlement_date == td:
                positions = get_yesterday_positions(session, acct)
                result = {
                    "ok": True,
                    "skipped": True,
                    "reason": "active settlement already matches trade_date",
                    "account_id": acct,
                    "settlement_date": td,
                    "position_count": len(positions),
                    "import_id": imp.id,
                }
                _LAST_STATUS["cfmmc"] = result
                return result

    try:
        dl = download_settlement_xls(
            user=user,
            password=password,
            trade_date=td,
            save_dir=UPLOAD_DIR / "cfmmc",
        )
        result = import_settlement_file(
            dl.filepath,
            account_id=acct,
            original_filename=dl.filename,
            keep_copy=True,
        )
        result.update(
            {
                "ok": True,
                "skipped": False,
                "source": "cfmmc_auto",
                "cfmmc_user": user,
                "cfmmc_trade_date": td,
            }
        )
    except CfmmcError as exc:
        result = {"ok": False, "skipped": False, "error": str(exc), "trade_date": td}
        logger.warning("CFMMC auto sync failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "skipped": False, "error": str(exc), "trade_date": td}
        logger.exception("CFMMC auto sync unexpected error")

    _LAST_STATUS["cfmmc"] = result
    _LAST_STATUS["updated_at"] = cn_now().isoformat()
    return result


def run_auto_feed(
    *,
    account_id: Optional[str] = None,
    include_cfmmc: bool = True,
    include_quotes: bool = True,
) -> dict[str, Any]:
    """Serialized feed tick used by startup + scheduler."""
    with _FEED_LOCK:
        out: dict[str, Any] = {"at": cn_now().isoformat()}
        if include_cfmmc and os.getenv("AUTO_CFMMC", "1") not in {"0", "false", "False"}:
            out["cfmmc"] = sync_cfmmc_settlement(account_id)
        if include_quotes and os.getenv("AUTO_QUOTES", "1") not in {"0", "false", "False"}:
            out["quotes"] = sync_quotes_for_account(account_id)
        _LAST_STATUS["updated_at"] = out["at"]
        return out


def run_auto_feed_background(**kwargs: Any) -> None:
    def _job() -> None:
        try:
            run_auto_feed(**kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("background auto feed failed")

    threading.Thread(target=_job, name="auto-feed", daemon=True).start()
