"""Event-driven backtest: signal at close t, fill at open t+1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from brent_quant import config as cfg
from brent_quant.execution import close_fill, open_fill
from brent_quant.position import contracts_for_trade
from brent_quant.risk import extreme_bar, initial_stop, stop_hit, trailing_stop
from brent_quant.signal import build_signal_frame


@dataclass
class BacktestResult:
    model: str
    equity: pd.Series
    daily_equity: pd.Series
    positions: pd.Series
    trades: pd.DataFrame
    bars: pd.DataFrame
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        return self.daily_equity.pct_change().dropna()


def _as_float(val: Any, default: float = 0.0) -> float:
    try:
        x = float(val)
    except (TypeError, ValueError):
        return default
    if np.isnan(x):
        return default
    return x


def run_backtest(
    df: pd.DataFrame,
    model: str = "D",
    *,
    breakout_n: int = cfg.BREAKOUT_N,
    er_n: int = cfg.ER_N,
    er_threshold: float = cfg.ER_THRESHOLD,
    volume_ma: int = cfg.VOLUME_MA,
    volume_threshold: float = cfg.VOLUME_RATIO_THRESHOLD,
    slippage_ticks: float = cfg.SLIPPAGE_TICKS,
    commission_per_side: float = cfg.COMMISSION_PER_SIDE,
    cost_mult: float = 1.0,
    risk_per_trade: float = cfg.RISK_PER_TRADE,
    stop_atr_mult: float = cfg.STOP_ATR_MULT,
    trail_atr_mult: float = cfg.TRAIL_ATR_MULT,
    time_stop_bars: int = cfg.TIME_STOP_BARS,
    initial_nav: float = cfg.INITIAL_NAV,
    use_stops: bool = True,
    signal_override: pd.Series | None = None,
) -> BacktestResult:
    if signal_override is None:
        work = build_signal_frame(
            df,
            model,
            breakout_n=breakout_n,
            er_n=er_n,
            er_threshold=er_threshold,
            volume_ma=volume_ma,
            volume_threshold=volume_threshold,
        )
    else:
        work = build_signal_frame(
            df,
            "A",
            breakout_n=breakout_n,
            er_n=er_n,
            er_threshold=er_threshold,
            volume_ma=volume_ma,
            volume_threshold=volume_threshold,
        )
        work["signal"] = signal_override.reindex(work.index).fillna(0.0)

    n = len(work)
    opens = work["open"].to_numpy(dtype=float)
    highs = work["high"].to_numpy(dtype=float)
    lows = work["low"].to_numpy(dtype=float)
    closes = work["close"].to_numpy(dtype=float)
    atrs = work["atr"].to_numpy(dtype=float)
    signals = work["signal"].to_numpy(dtype=float)
    scores = work["score"].to_numpy(dtype=float) if "score" in work.columns else np.zeros(n)
    regimes = work["regime"].astype(str).to_numpy()
    vrs = work["volume_ratio"].to_numpy(dtype=float) if "volume_ratio" in work.columns else np.zeros(n)
    ers = work["er"].to_numpy(dtype=float) if "er" in work.columns else np.zeros(n)
    vzs = work["volume_z"].to_numpy(dtype=float) if "volume_z" in work.columns else np.zeros(n)
    zret = work["bar_return_z"].to_numpy(dtype=float) if "bar_return_z" in work.columns else np.zeros(n)
    index = work.index

    commission = commission_per_side * cost_mult
    nav = initial_nav
    qty = 0.0
    side = 0.0
    entry_price = np.nan
    stop = np.nan
    extreme = np.nan
    bars_held = 0
    pending_target = 0.0
    trade_pnl = 0.0
    entry_i = -1
    entry_reason = ""

    equity = np.full(n, initial_nav, dtype=float)
    pos_hist = np.zeros(n, dtype=float)
    trades: list[dict[str, Any]] = []

    def flatten(i: int, price: float, reason: str) -> None:
        nonlocal nav, qty, side, entry_price, stop, extreme, bars_held, trade_pnl, entry_i
        if qty == 0.0 or side == 0.0:
            return
        fill = close_fill(price, side, cfg.TICK_SIZE, slippage_ticks)
        if i == entry_i:
            last_ref = entry_price
        elif i > 0:
            last_ref = closes[i - 1]
        else:
            last_ref = entry_price
        gap = side * qty * cfg.MULTIPLIER * (fill - last_ref)
        fee = commission * abs(qty)
        nav += gap - fee
        trade_pnl += gap - fee
        trades.append(
            {
                "entry_time": str(index[entry_i]) if entry_i >= 0 else None,
                "exit_time": str(index[i]),
                "contract": cfg.CONTRACT,
                "signal": float(side),
                "score": float(scores[entry_i]) if entry_i >= 0 else np.nan,
                "regime": str(regimes[entry_i]) if entry_i >= 0 else "",
                "entry_price": float(entry_price),
                "exit_price": float(fill),
                "stop": float(stop) if not np.isnan(stop) else np.nan,
                "position": float(side * qty),
                "qty": float(qty),
                "volume_ratio": float(vrs[entry_i]) if entry_i >= 0 else np.nan,
                "ER": float(ers[entry_i]) if entry_i >= 0 else np.nan,
                "volume_z": float(vzs[entry_i]) if entry_i >= 0 else np.nan,
                "PnL": float(trade_pnl),
                "exit_reason": reason,
                "bars_held": int(bars_held),
                "entry_reason": entry_reason,
            }
        )
        qty = 0.0
        side = 0.0
        entry_price = np.nan
        stop = np.nan
        extreme = np.nan
        bars_held = 0
        trade_pnl = 0.0
        entry_i = -1

    def enter(i: int, target: float) -> None:
        nonlocal nav, qty, side, entry_price, stop, extreme, bars_held, trade_pnl, entry_i, entry_reason
        atr_i = _as_float(atrs[i], 0.0)
        size = contracts_for_trade(
            nav,
            opens[i],
            atr_i,
            risk_per_trade,
            stop_atr_mult,
            cfg.MULTIPLIER,
            cfg.MAX_LEVERAGE,
        )
        size *= abs(target)
        if size <= 0:
            return
        fill = open_fill(opens[i], target, cfg.TICK_SIZE, slippage_ticks)
        fee = commission * size
        nav -= fee
        qty = size
        side = 1.0 if target > 0 else -1.0
        entry_price = fill
        stop = initial_stop(fill, side, atr_i, stop_atr_mult)
        extreme = highs[i] if side > 0 else lows[i]
        bars_held = 0
        trade_pnl = -fee
        entry_i = i
        entry_reason = model

    for i in range(n):
        # 1) Open: fill pending target from previous close.
        if i > 0:
            target = pending_target
            if extreme_bar(_as_float(zret[i], 0.0), cfg.EXTREME_RETURN_Z) and qty == 0:
                target = 0.0
            desired = 0.0 if target == 0 else (1.0 if target > 0 else -1.0)
            current = 0.0 if side == 0 else (1.0 if side > 0 else -1.0)
            if desired != current:
                if qty != 0:
                    flatten(i, opens[i], "signal_change")
                if desired != 0 and qty == 0:
                    enter(i, target)

        # 2) Intrabar risk.
        if qty != 0 and use_stops:
            bars_held += 1
            if side > 0:
                extreme = max(extreme, highs[i]) if not np.isnan(extreme) else highs[i]
            else:
                extreme = min(extreme, lows[i]) if not np.isnan(extreme) else lows[i]
            atr_i = _as_float(atrs[i], 0.0)
            trail = trailing_stop(extreme, side, atr_i, trail_atr_mult)
            if not np.isnan(trail):
                if side > 0:
                    stop = max(stop, trail) if not np.isnan(stop) else trail
                else:
                    stop = min(stop, trail) if not np.isnan(stop) else trail
            if stop_hit(side, lows[i], highs[i], stop):
                flatten(i, stop, "stop")
            elif time_stop_bars and bars_held >= time_stop_bars:
                flatten(i, closes[i], "time_stop")

        # 3) Mark to close.
        if qty != 0:
            prev = opens[i] if bars_held == 0 and i == entry_i else (closes[i - 1] if i > 0 else entry_price)
            # After entry this bar, MTM from fill to close; otherwise close-to-close.
            if i == entry_i:
                prev = entry_price
            mtm = side * qty * cfg.MULTIPLIER * (closes[i] - prev)
            nav += mtm
            trade_pnl += mtm

        equity[i] = nav
        pos_hist[i] = side * qty

        # 4) New signal at close, executed next open.
        pending_target = _as_float(signals[i], 0.0)

    if qty != 0:
        flatten(n - 1, closes[-1], "eod_flatten")
        equity[-1] = nav

    eq = pd.Series(equity, index=index, name="equity")
    daily = eq.resample("1D").last().dropna()
    trades_df = pd.DataFrame(trades)
    params = {
        "model": model,
        "breakout_n": breakout_n,
        "er_n": er_n,
        "er_threshold": er_threshold,
        "volume_ma": volume_ma,
        "volume_threshold": volume_threshold,
        "slippage_ticks": slippage_ticks,
        "commission_per_side": commission,
        "cost_mult": cost_mult,
        "risk_per_trade": risk_per_trade,
        "stop_atr_mult": stop_atr_mult,
        "trail_atr_mult": trail_atr_mult,
        "time_stop_bars": time_stop_bars,
        "initial_nav": initial_nav,
    }
    bars = work[["open", "high", "low", "close", "volume", "signal", "atr"]].copy()
    bars["equity"] = eq
    bars["position"] = pos_hist
    return BacktestResult(
        model=model,
        equity=eq,
        daily_equity=daily,
        positions=pd.Series(pos_hist, index=index, name="position"),
        trades=trades_df,
        bars=bars,
        params=params,
    )


def buy_and_hold(df: pd.DataFrame, initial_nav: float = cfg.INITIAL_NAV) -> BacktestResult:
    work = df.copy()
    rets = work["close"].pct_change().fillna(0.0)
    eq = initial_nav * (1.0 + rets).cumprod()
    eq.iloc[0] = initial_nav
    daily = eq.resample("1D").last().dropna()
    trades = pd.DataFrame(
        [
            {
                "entry_time": str(work.index[0]),
                "exit_time": str(work.index[-1]),
                "contract": cfg.CONTRACT,
                "signal": 1.0,
                "score": np.nan,
                "regime": "n/a",
                "entry_price": float(work["close"].iloc[0]),
                "exit_price": float(work["close"].iloc[-1]),
                "stop": np.nan,
                "position": 1.0,
                "qty": np.nan,
                "volume_ratio": np.nan,
                "ER": np.nan,
                "volume_z": np.nan,
                "PnL": float(eq.iloc[-1] - initial_nav),
                "exit_reason": "buy_and_hold",
                "bars_held": int(len(work)),
                "entry_reason": "BHS",
            }
        ]
    )
    return BacktestResult(
        model="BHS",
        equity=eq.rename("equity"),
        daily_equity=daily,
        positions=pd.Series(1.0, index=work.index, name="position"),
        trades=trades,
        bars=work.assign(equity=eq, position=1.0, signal=1.0),
        params={"model": "BHS", "initial_nav": initial_nav},
    )


def random_entry_benchmark(
    df: pd.DataFrame,
    template: BacktestResult,
    seed: int = cfg.RANDOM_SEED,
) -> BacktestResult:
    rng = np.random.default_rng(seed)
    n = len(df)
    sig = np.zeros(n, dtype=float)
    if template.trades is None or template.trades.empty:
        override = pd.Series(sig, index=df.index)
        return run_backtest(df, model="A", signal_override=override)

    holds = template.trades["bars_held"].fillna(1).astype(int).clip(lower=1)
    used = np.zeros(n, dtype=bool)
    for hold in holds:
        for _ in range(50):
            start = int(rng.integers(1, max(2, n - int(hold) - 1)))
            end = min(n, start + int(hold))
            if used[start:end].any():
                continue
            used[start:end] = True
            sig[start:end] = float(rng.choice([-1.0, 1.0]))
            break
    override = pd.Series(sig, index=df.index)
    res = run_backtest(df, model="A", signal_override=override)
    res.model = "RANDOM"
    res.params["model"] = "RANDOM"
    return res
