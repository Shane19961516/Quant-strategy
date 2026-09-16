"""Fetch Yahoo 1h bars and catch the paper book up to the last closed candle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from universal_quant import config as cfg
from universal_quant.adapters.yahoo import get_data
from universal_quant.live.session import PaperAccount
from universal_quant.live.trader import flatten, step_bar
from universal_quant.regimes.regime_engine import add_regime
from universal_quant.strategies.score import add_score
from universal_quant.strategies.selector import signal_from_model

PULSE = frozenset({"A", "B", "C", "D", "E", "BO"})


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def is_forming(ts, now: pd.Timestamp | None = None, bar_minutes: int = cfg.BAR_MINUTES) -> bool:
    now = now or _utc_now()
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    age_min = (now - t.tz_convert("UTC")).total_seconds() / 60.0
    return age_min < max(bar_minutes - 2, 1)


def load_bars(symbol: str, period: str = cfg.PAPER_PERIOD, force: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = cfg.PROCESSED_DIR / f"{symbol.replace('=', '').replace('-', '')}_{cfg.INTERVAL}.parquet"
    if not force and path.exists():
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age < cfg.DATA_CACHE_SEC:
            df = pd.read_parquet(path)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, utc=True)
            elif df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            meta = {"symbol": symbol, "rows": int(len(df)), "cached": True, "adapter": "parquet"}
            return df, meta
    return get_data(symbol, interval=cfg.INTERVAL, period=period)


def prepare(df: pd.DataFrame, model: str) -> pd.DataFrame:
    work = add_score(add_regime(df))
    work["signal"] = signal_from_model(work, model)
    return work


def catch_up(
    account: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    force_last: bool = False,
) -> dict[str, Any]:
    """Process new closed bars. force_last also consumes the latest (possibly forming) bar — for demo step."""
    model = str(account.get("model") or cfg.PAPER_MODEL)
    pulse = model.upper() in PULSE
    now = _utc_now()
    account_obj = PaperAccount(account)
    account_obj.rollover_day(now.date().isoformat())
    processed = 0
    errors: list[str] = []

    for symbol, raw in frames.items():
        spec = cfg.spec_for(symbol)
        book = account["books"].setdefault(symbol, {})
        try:
            work = prepare(raw, model)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{symbol}: {exc}")
            continue
        last = book.get("last_bar")
        rows = work
        if last:
            last_ts = pd.Timestamp(last)
            if last_ts.tzinfo is None:
                last_ts = last_ts.tz_localize("UTC")
            rows = work[work.index > last_ts]
        else:
            rows = work.iloc[-cfg.PAPER_LOOKBACK_BARS :] if len(work) > cfg.PAPER_LOOKBACK_BARS else work
        if rows.empty:
            # still refresh quote/regime on the latest bar
            last_row = work.iloc[-1]
            ts = work.index[-1]
            book["price"] = float(last_row["close"])
            book["regime"] = str(last_row.get("regime", ""))
            book["score"] = float(last_row.get("score") or 0.0)
            book["signal"] = float(last_row.get("signal") or 0.0)
            book["natr"] = float(last_row["natr"]) if pd.notna(last_row.get("natr")) else None
            book["atr"] = float(last_row["atr"]) if pd.notna(last_row.get("atr")) else None
            book["forming"] = bool(is_forming(ts, now))
            book["name"] = spec["name"]
            book["cluster"] = spec["cluster"]
            continue
        for ts, row in rows.iterrows():
            forming = is_forming(ts, now)
            if forming and not force_last:
                book["price"] = float(row["close"])
                book["regime"] = str(row.get("regime", ""))
                book["score"] = float(row.get("score") or 0.0)
                book["signal"] = float(row.get("signal") or 0.0)
                book["natr"] = float(row["natr"]) if pd.notna(row.get("natr")) else None
                book["atr"] = float(row["atr"]) if pd.notna(row.get("atr")) else None
                book["forming"] = True
                book["name"] = spec["name"]
                book["cluster"] = spec["cluster"]
                break
            kill = bool(account.get("kill"))
            step_bar(
                account,
                symbol,
                spec,
                str(ts),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["atr"]) if pd.notna(row.get("atr")) else 0.0,
                float(row["natr"]) if pd.notna(row.get("natr")) else 0.0,
                float(row["signal"]) if pd.notna(row.get("signal")) else 0.0,
                str(row.get("regime", "")),
                float(row.get("score") or 0.0),
                kill=kill,
                pulse=pulse,
            )
            processed += 1
            account_obj.mark_peak()
            account["equity"].append({"t": str(ts), "nav": float(account["nav"])})
            if account_obj.check_auto_kill() and kill is False and account.get("kill"):
                # newly tripped: flatten remaining names at this close
                for other, ob in account["books"].items():
                    if _safe_w(ob) != 0:
                        flatten(
                            account,
                            ob,
                            cfg.spec_for(other),
                            other,
                            model,
                            str(ts),
                            float(ob.get("last_close") or ob.get("price") or row["close"]),
                            "kill",
                        )

    account["last_error"] = "; ".join(errors)
    account["equity"].append({"t": now.isoformat(), "nav": float(account["nav"])})
    account["equity"] = account["equity"][-2000:]
    account["_processed_bars"] = processed
    return account


def _safe_w(book: dict) -> float:
    try:
        return float(book.get("weight") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def flatten_all(account: dict[str, Any], reason: str = "kill") -> None:
    model = str(account.get("model") or cfg.PAPER_MODEL)
    for symbol, book in account["books"].items():
        if _safe_w(book) == 0:
            continue
        px = float(book.get("last_close") or book.get("price") or book.get("entry") or 0.0)
        if px <= 0:
            continue
        flatten(account, book, cfg.spec_for(symbol), symbol, model, str(book.get("last_bar") or ""), px, reason)
        book["pending"] = 0.0


def snapshot(account: dict[str, Any]) -> dict[str, Any]:
    acc = PaperAccount(account)
    books = []
    for sym, b in account["books"].items():
        side = float(b.get("side") or 0.0)
        weight = float(b.get("weight") or 0.0)
        books.append(
            {
                "symbol": sym,
                "name": b.get("name") or cfg.spec_for(sym)["name"],
                "cluster": b.get("cluster") or cfg.spec_for(sym)["cluster"],
                "price": b.get("price"),
                "regime": b.get("regime") or "",
                "score": b.get("score") or 0.0,
                "signal": b.get("signal") or 0.0,
                "pending": b.get("pending") or 0.0,
                "side": side,
                "position": "多" if side > 0 and weight else ("空" if side < 0 and weight else "空仓"),
                "weight": weight,
                "stop": b.get("stop"),
                "held": int(b.get("held") or 0),
                "unrealized": float(b.get("trade_pnl") or 0.0) if weight else 0.0,
                "last_bar": b.get("last_bar"),
                "forming": bool(b.get("forming")),
                "natr": b.get("natr"),
            }
        )
    return {
        "nav": float(account["nav"]),
        "peak_nav": float(account.get("peak_nav") or account["nav"]),
        "day_start_nav": float(account.get("day_start_nav") or account["nav"]),
        "day_pnl": float(account["nav"]) - float(account.get("day_start_nav") or account["nav"]),
        "day_pnl_pct": acc.day_pnl_pct(),
        "drawdown": acc.drawdown(),
        "gross": acc.gross(),
        "net": acc.net(),
        "kill": bool(account.get("kill")),
        "kill_reason": account.get("kill_reason") or "",
        "model": account.get("model"),
        "updated_at": account.get("updated_at"),
        "last_error": account.get("last_error") or "",
        "processed_bars": int(account.get("_processed_bars") or 0),
        "books": books,
        "trades": list(reversed(account.get("trades") or []))[:40],
        "equity": account.get("equity") or [],
        "limits": {
            "daily_loss": cfg.DAILY_LOSS_LIMIT,
            "drawdown": cfg.PORT_DD_LIMIT,
            "gross_cap": cfg.PAPER_GROSS_CAP,
            "risk_per_trade": cfg.RISK_PER_TRADE,
            "time_stop_bars": cfg.TIME_STOP_BARS,
        },
    }


def refresh_paper(force: bool = False, reset: bool = False, kill: bool | None = None, step: bool = False) -> dict[str, Any]:
    from universal_quant.live.session import empty_account, load_account, save_account

    path = cfg.PAPER_STATE_PATH
    account = empty_account() if reset else load_account(path)
    if kill is True:
        account["kill"] = True
        account["kill_reason"] = account.get("kill_reason") or "manual"
        flatten_all(account, "kill")
    elif kill is False:
        account["kill"] = False
        account["kill_reason"] = ""
    frames: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    for sym in cfg.MVP_SYMBOLS:
        try:
            df, _meta = load_bars(sym, force=force)
            if not df.empty:
                frames[sym] = df
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sym}: {exc}")
    if frames:
        catch_up(account, frames, force_last=step)
    if errors:
        extra = "; ".join(errors)
        prev = account.get("last_error") or ""
        account["last_error"] = (prev + "; " + extra).strip("; ")
    save_account(account, path)
    return snapshot(account)
