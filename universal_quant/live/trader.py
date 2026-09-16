"""Incremental pulse trader: same fill rules as the 1h backtest engine."""

from __future__ import annotations

from typing import Any

from universal_quant import config as cfg
from universal_quant.portfolio.sizing import target_weight


def _f(x, default=0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return v


def _cost(dw: float, nav: float, price: float, spec: dict, slippage_ticks: float) -> float:
    tick = float(spec.get("tick_size", 0.01))
    c_bps = float(spec.get("commission_bps", 2.0))
    slip = slippage_ticks * tick / max(price, 1e-9)
    return abs(dw) * nav * (c_bps / 1e4 + slip)


def flatten(
    account: dict[str, Any],
    book: dict[str, Any],
    spec: dict,
    symbol: str,
    model: str,
    ts: str,
    price: float,
    reason: str,
    slippage_ticks: float = 1.0,
) -> None:
    weight = _f(book.get("weight"))
    if weight == 0:
        return
    side = _f(book.get("side"))
    tick = float(spec.get("tick_size", 0.01))
    is_cover = side < 0
    fill = price + tick * slippage_ticks if is_cover else price - tick * slippage_ticks
    entry_time = book.get("entry_time")
    last_close = book.get("last_close")
    entry = _f(book.get("entry"))
    ref = entry if last_close is None else _f(last_close, entry)
    nav = _f(account["nav"])
    gap = weight * nav * ((fill - ref) / ref if ref else 0.0)
    fee = _cost(weight, nav, fill, spec, slippage_ticks)
    nav = nav + gap - fee
    account["nav"] = nav
    pnl = _f(book.get("trade_pnl")) + gap - fee
    account["trades"].append(
        {
            "symbol": symbol,
            "name": spec.get("name", symbol),
            "model": model,
            "entry_time": entry_time,
            "exit_time": ts,
            "side": side,
            "entry_price": _f(book.get("entry")),
            "exit_price": fill,
            "weight": weight,
            "regime": str(book.get("regime") or ""),
            "PnL": pnl,
            "exit_reason": reason,
            "bars_held": int(book.get("held") or 0),
        }
    )
    account["trades"] = account["trades"][-300:]
    book["weight"] = 0.0
    book["side"] = 0.0
    book["entry"] = None
    book["stop"] = None
    book["extreme"] = None
    book["held"] = 0
    book["trade_pnl"] = 0.0
    book["entry_time"] = None
    book["last_close"] = fill


def enter(
    account: dict[str, Any],
    book: dict[str, Any],
    spec: dict,
    ts: str,
    open_px: float,
    high: float,
    low: float,
    atr: float,
    natr: float,
    target: float,
    slippage_ticks: float = 1.0,
    gross_cap: float | None = None,
) -> None:
    if _f(book.get("weight")) != 0:
        return
    w = target_weight(natr, cfg.RISK_PER_TRADE, cfg.STOP_ATR_MULT, cfg.MAX_WEIGHT, abs(target))
    if w <= 0:
        return
    cap = float(gross_cap if gross_cap is not None else cfg.PAPER_GROSS_CAP)
    used = sum(abs(_f(b.get("weight"))) for b in account["books"].values())
    w = min(w, max(0.0, cap - used))
    if w <= 1e-6:
        return
    tick = float(spec.get("tick_size", 0.01))
    is_buy = target > 0
    fill = open_px + tick * slippage_ticks if is_buy else open_px - tick * slippage_ticks
    nav = _f(account["nav"])
    fee = _cost(w, nav, fill, spec, slippage_ticks)
    account["nav"] = nav - fee
    side = 1.0 if target > 0 else -1.0
    book["weight"] = w
    book["side"] = side
    book["entry"] = fill
    book["stop"] = fill - side * cfg.STOP_ATR_MULT * atr if atr > 0 else None
    book["extreme"] = high if side > 0 else low
    book["held"] = 0
    book["trade_pnl"] = -fee
    book["entry_time"] = ts
    book["last_close"] = fill


def step_bar(
    account: dict[str, Any],
    symbol: str,
    spec: dict,
    ts: str,
    open_px: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    natr: float,
    signal: float,
    regime: str,
    score: float,
    kill: bool,
    slippage_ticks: float = 1.0,
    pulse: bool = True,
) -> None:
    """Apply one closed bar. Signal is this bar's close; fill uses this bar's open from prior pending."""
    book = account["books"][symbol]
    model = str(account.get("model") or cfg.PAPER_MODEL)
    atr = _f(atr)
    natr = _f(natr)
    tgt = _f(book.get("pending"))
    ds = 0.0 if tgt == 0 else (1.0 if tgt > 0 else -1.0)
    cs = 0.0 if _f(book.get("side")) == 0 else (1.0 if _f(book.get("side")) > 0 else -1.0)

    if kill:
        if _f(book.get("weight")) != 0:
            flatten(account, book, spec, symbol, model, ts, open_px, "kill", slippage_ticks)
        book["pending"] = 0.0
    elif pulse:
        if _f(book.get("weight")) == 0 and ds != 0:
            enter(account, book, spec, ts, open_px, high, low, atr, natr, tgt, slippage_ticks)
        elif _f(book.get("weight")) != 0 and ds != 0 and ds != cs:
            flatten(account, book, spec, symbol, model, ts, open_px, "signal_change", slippage_ticks)
            enter(account, book, spec, ts, open_px, high, low, atr, natr, tgt, slippage_ticks)
    else:
        if ds != cs:
            if _f(book.get("weight")) != 0:
                flatten(account, book, spec, symbol, model, ts, open_px, "signal_change", slippage_ticks)
            if ds != 0 and _f(book.get("weight")) == 0:
                enter(account, book, spec, ts, open_px, high, low, atr, natr, tgt, slippage_ticks)

    if _f(book.get("weight")) != 0:
        book["held"] = int(book.get("held") or 0) + 1
        side = _f(book.get("side"))
        extreme = book.get("extreme")
        if side > 0:
            book["extreme"] = max(_f(extreme, high), high) if extreme is not None else high
        else:
            book["extreme"] = min(_f(extreme, low), low) if extreme is not None else low
        if atr > 0 and book.get("extreme") is not None:
            trail = _f(book["extreme"]) - side * cfg.TRAIL_ATR_MULT * atr
            stop = book.get("stop")
            if side > 0:
                book["stop"] = max(_f(stop, trail), trail) if stop is not None else trail
            else:
                book["stop"] = min(_f(stop, trail), trail) if stop is not None else trail
        stop = book.get("stop")
        hit = stop is not None and ((side > 0 and low <= _f(stop)) or (side < 0 and high >= _f(stop)))
        if hit:
            flatten(account, book, spec, symbol, model, ts, _f(stop), "stop", slippage_ticks)
        elif int(book.get("held") or 0) >= cfg.TIME_STOP_BARS:
            flatten(account, book, spec, symbol, model, ts, close, "time_stop", slippage_ticks)

    if _f(book.get("weight")) != 0:
        side = _f(book.get("side"))
        weight = abs(_f(book.get("weight")))
        prev = _f(book.get("last_close"), _f(book.get("entry")))
        if prev:
            mtm = side * weight * _f(account["nav"]) * ((close - prev) / prev)
            account["nav"] = _f(account["nav"]) + mtm
            book["trade_pnl"] = _f(book.get("trade_pnl")) + mtm
        book["last_close"] = close

    book["pending"] = 0.0 if kill else _f(signal)
    book["last_bar"] = ts
    book["regime"] = str(regime or "")
    book["score"] = _f(score)
    book["signal"] = _f(signal)
    book["price"] = close
    book["natr"] = natr
    book["atr"] = atr
    book["forming"] = False
    peak = max(_f(account.get("peak_nav"), _f(account["nav"])), _f(account["nav"]))
    account["peak_nav"] = peak
