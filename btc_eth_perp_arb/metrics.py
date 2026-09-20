"""Performance stats on the PnL window. Equity is the 10x account; notional is mark PnL."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import STARTING_EQUITY
from .simulator import SimResult


def max_drawdown(equity: pd.Series) -> float:
    s = equity.astype(float).dropna()
    if s.empty:
        return 0.0
    peak = s.cummax()
    dd = s / peak - 1.0
    return float(dd.min())


def summarize(result: SimResult, pnl_start_ts: int, starting_equity: float = STARTING_EQUITY) -> dict:
    bars = result.bars
    w = bars[bars["bar_open_ts"] >= pnl_start_ts].copy()
    if w.empty:
        raise ValueError("empty PnL window")
    eq = w["equity"].astype(float)
    start_eq = float(eq.iloc[0]) if np.isfinite(eq.iloc[0]) else starting_equity
    end_eq = float(eq.dropna().iloc[-1])
    equity_pnl = end_eq - start_eq
    equity_ret = equity_pnl / start_eq

    # Attribution on the window: Δequity = mark/spread/slip + funding − fees
    deq = eq.diff()
    fees = w["fee"].fillna(0.0)
    funding = w["funding_cash"].fillna(0.0)
    spread_like = (deq - funding + fees).fillna(0.0)

    trades = result.trades
    if not trades.empty:
        tw = trades[trades["bar_open_ts"] >= pnl_start_ts]
        n_fills = int(len(tw))
        n_enters = int((tw["reason"] == "enter").sum() // 2)  # two legs per enter
    else:
        tw = trades
        n_fills = 0
        n_enters = 0
    n_enters_bar = int((w["event"] == "enter").sum())
    n_trades = max(n_enters, n_enters_bar)

    complete = int((w["pair_incomplete"] == 0).sum())
    incomplete = int((w["pair_incomplete"] == 1).sum())
    liq = int(w["liq_flag"].sum()) if "liq_flag" in w else 0
    # Prefer simulator counter on the window via events.
    liq_events = int((w["event"] == "liq").sum())
    if liq_events == 0:
        liq_events = liq

    peak = eq.cummax()
    dd = eq / peak - 1.0
    mdd = float(dd.min()) if len(dd) else 0.0
    mdd_ts = w.loc[dd.idxmin(), "bar_open"] if len(dd) and "bar_open" in w else None

    in_pos = (w["qty_eth"].abs() > 0) | (w["qty_btc"].abs() > 0)
    hold_frac = float(in_pos.mean())
    turnover_notional = float(tw["fill_qty"].abs().mul(tw["fill_px"]).sum()) if n_fills else 0.0

    # Notional PnL: mark-to-mark on lagged position (held across bar).
    btc_held = w["qty_btc"].shift(1) * w["btc_mark_close"].diff()
    eth_held = w["qty_eth"].shift(1) * w["eth_mark_close"].diff()
    notional_mark = float(btc_held.fillna(0).sum() + eth_held.fillna(0).sum())

    minutes = int(len(w))
    days = minutes / 1440.0
    funding_on_settle = w.loc[w["btc_is_funding"] | w["eth_is_funding"], "funding_cash"]
    funding_nonzero_off_settle = float(
        w.loc[~(w["btc_is_funding"] | w["eth_is_funding"]), "funding_cash"].abs().sum()
    )

    return {
        "pnl_start": str(w["bar_open"].iloc[0]),
        "pnl_end": str(w["bar_open"].iloc[-1]),
        "minutes": minutes,
        "days": days,
        "complete_minutes": complete,
        "incomplete_minutes": incomplete,
        "starting_equity": start_eq,
        "ending_equity": end_eq,
        "equity_pnl": equity_pnl,
        "equity_return": equity_ret,
        "notional_mark_pnl": notional_mark,
        "notional_return_on_gross_10x": notional_mark / (start_eq * 10.0) if start_eq else 0.0,
        "max_drawdown": mdd,
        "max_drawdown_at": str(mdd_ts) if mdd_ts is not None else None,
        "trades": n_trades,
        "fills": n_fills,
        "fee_sum": float(fees.sum()),
        "funding_sum": float(funding.sum()),
        "spread_like_pnl": float(spread_like.sum()),
        "liquidation_events": liq_events,
        "time_in_market": hold_frac,
        "turnover_notional": turnover_notional,
        "funding_settlements_in_window": int((w["btc_is_funding"] | w["eth_is_funding"]).sum()),
        "funding_cash_off_settlement_abs": funding_nonzero_off_settle,
        "mean_gross_leverage_in_pos": float(w.loc[in_pos, "gross_leverage"].mean()) if in_pos.any() else 0.0,
        "exit_reasons": w.loc[w["event"].isin(["exit", "stop", "time", "corr_break", "gap", "liq", "nan_z"]), "event"]
        .value_counts()
        .to_dict(),
    }
