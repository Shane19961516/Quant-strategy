"""Event-driven engine: signal at close t, fill at next open. Risk-based weights."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from universal_quant import config as cfg
from universal_quant.portfolio.sizing import target_weight
from universal_quant.regimes.regime_engine import add_regime
from universal_quant.strategies.score import add_score
from universal_quant.strategies.selector import signal_from_model

PULSE_MODELS = frozenset({"A", "B", "C", "D", "E", "BO"})


@dataclass
class BacktestResult:
    symbol: str
    model: str
    equity: pd.Series
    daily_equity: pd.Series
    trades: pd.DataFrame
    params: dict[str, Any] = field(default_factory=dict)


def _f(x, default=0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return default if np.isnan(v) else v


def run_backtest(
    df: pd.DataFrame,
    model: str,
    symbol: str,
    spec: dict | None = None,
    slippage_ticks: float = 1.0,
    cost_mult: float = 1.0,
    signal: pd.Series | None = None,
    overlay: dict[str, pd.Series] | None = None,
    prepared: bool = False,
) -> BacktestResult:
    spec = spec or cfg.spec_for(symbol)
    work = df.copy() if prepared else add_score(add_regime(df))
    if overlay:
        for col, series in overlay.items():
            work[col] = series.reindex(work.index)
    if model.upper() == "BHS":
        rets = work["close"].pct_change().fillna(0.0)
        eq = cfg.INITIAL_NAV * (1.0 + rets).cumprod()
        eq.iloc[0] = cfg.INITIAL_NAV
        daily = eq.resample("1D").last().dropna()
        return BacktestResult(
            symbol=symbol,
            model="BHS",
            equity=eq.rename("equity"),
            daily_equity=daily,
            trades=pd.DataFrame(),
            params={"model": "BHS", "symbol": symbol},
        )

    if signal is not None:
        work["signal"] = signal.reindex(work.index).fillna(0.0).astype(float)
    else:
        work["signal"] = signal_from_model(work, model)
    if "regime" not in work.columns:
        work["regime"] = ""
    n = len(work)
    opens = work["open"].to_numpy(float)
    highs = work["high"].to_numpy(float)
    lows = work["low"].to_numpy(float)
    closes = work["close"].to_numpy(float)
    natrs = work["natr"].to_numpy(float)
    atrs = work["atr"].to_numpy(float)
    sigs = work["signal"].to_numpy(float)
    regimes = work["regime"].astype(str).to_numpy()
    tick = float(spec.get("tick_size", 0.01))
    c_bps = float(spec.get("commission_bps", 2.0)) * cost_mult

    nav = cfg.INITIAL_NAV
    weight = 0.0
    side = 0.0
    entry = np.nan
    stop = np.nan
    extreme = np.nan
    held = 0
    pending = 0.0
    trade_pnl = 0.0
    entry_i = -1
    equity = np.full(n, nav)
    trades: list[dict[str, Any]] = []
    pulse = model.upper() in PULSE_MODELS

    def cost_on_trade(dw: float, price: float) -> float:
        slip = slippage_ticks * tick / max(price, 1e-9)
        return abs(dw) * nav * (c_bps / 1e4 + slip)

    def flatten(i: int, price: float, reason: str) -> None:
        nonlocal nav, weight, side, entry, stop, extreme, held, trade_pnl, entry_i
        if weight == 0:
            return
        is_buy = side < 0
        fill = price + tick * slippage_ticks if is_buy else price - tick * slippage_ticks
        if i == entry_i:
            ref = entry
        elif i > 0:
            ref = closes[i - 1]
        else:
            ref = entry
        gap = weight * nav * ((fill - ref) / ref if ref else 0.0)
        fee = cost_on_trade(weight, fill)
        nav += gap - fee
        trade_pnl += gap - fee
        trades.append(
            {
                "symbol": symbol,
                "model": model,
                "entry_time": str(work.index[entry_i]) if entry_i >= 0 else None,
                "exit_time": str(work.index[i]),
                "side": float(side),
                "entry_price": float(entry),
                "exit_price": float(fill),
                "weight": float(weight),
                "regime": str(regimes[entry_i]) if entry_i >= 0 else "",
                "PnL": float(trade_pnl),
                "exit_reason": reason,
                "bars_held": int(held),
            }
        )
        weight = 0.0
        side = 0.0
        entry = np.nan
        stop = np.nan
        extreme = np.nan
        held = 0
        trade_pnl = 0.0
        entry_i = -1

    def enter(i: int, target: float) -> None:
        nonlocal nav, weight, side, entry, stop, extreme, held, trade_pnl, entry_i
        natr = _f(natrs[i], 0.0)
        w = target_weight(natr, cfg.RISK_PER_TRADE, cfg.STOP_ATR_MULT, cfg.MAX_WEIGHT, abs(target))
        if w <= 0:
            return
        is_buy = target > 0
        fill = opens[i] + tick * slippage_ticks if is_buy else opens[i] - tick * slippage_ticks
        fee = cost_on_trade(w, fill)
        nav -= fee
        weight = w
        side = 1.0 if target > 0 else -1.0
        entry = fill
        atr = _f(atrs[i], 0.0)
        stop = fill - side * cfg.STOP_ATR_MULT * atr
        extreme = highs[i] if side > 0 else lows[i]
        held = 0
        trade_pnl = -fee
        entry_i = i

    for i in range(n):
        if i > 0:
            tgt = pending
            ds = 0.0 if tgt == 0 else (1.0 if tgt > 0 else -1.0)
            cs = 0.0 if side == 0 else (1.0 if side > 0 else -1.0)
            if pulse:
                if weight == 0 and ds != 0:
                    enter(i, tgt)
                elif weight != 0 and ds != 0 and ds != cs:
                    flatten(i, opens[i], "signal_change")
                    enter(i, tgt)
            else:
                if ds != cs:
                    if weight != 0:
                        flatten(i, opens[i], "signal_change")
                    if ds != 0 and weight == 0:
                        enter(i, tgt)

        if weight != 0:
            held += 1
            if side > 0:
                extreme = max(extreme, highs[i]) if not np.isnan(extreme) else highs[i]
            else:
                extreme = min(extreme, lows[i]) if not np.isnan(extreme) else lows[i]
            atr = _f(atrs[i], 0.0)
            if atr > 0 and not np.isnan(extreme):
                trail = extreme - side * cfg.TRAIL_ATR_MULT * atr
                if side > 0:
                    stop = max(stop, trail) if not np.isnan(stop) else trail
                else:
                    stop = min(stop, trail) if not np.isnan(stop) else trail
            hit = (side > 0 and lows[i] <= stop) or (side < 0 and highs[i] >= stop)
            if hit and not np.isnan(stop):
                flatten(i, stop, "stop")
            elif held >= cfg.TIME_STOP_BARS:
                flatten(i, closes[i], "time_stop")

        if weight != 0:
            prev = entry if i == entry_i else (closes[i - 1] if i > 0 else entry)
            if prev:
                mtm = side * abs(weight) * nav * ((closes[i] - prev) / prev)
                nav += mtm
                trade_pnl += mtm
        equity[i] = nav
        pending = _f(sigs[i], 0.0)

    if weight != 0:
        flatten(n - 1, closes[-1], "eod_flatten")
        equity[-1] = nav

    eq = pd.Series(equity, index=work.index, name="equity")
    return BacktestResult(
        symbol=symbol,
        model=model.upper(),
        equity=eq,
        daily_equity=eq.resample("1D").last().dropna(),
        trades=pd.DataFrame(trades),
        params={"symbol": symbol, "model": model, "spec": spec, "slippage_ticks": slippage_ticks},
    )
