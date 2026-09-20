"""5-minute equal-weight MA / slope / volume / range-position composite.

Locked before looking at results:

- Bar: 5-minute UTC resample (1m 10x already died on taker cost).
- Window 144 (= 12h). Slope lag 24 (= 2h). Not searched on 2026-07–09.
- Four raw series, each rolling-z'd over the same 144 bars, clipped to ±5,
  then equal-weight 0.25. Trend-following, not a fade.
- Long when composite z ≥ 1; short when ≤ −1; flatten when |z| < 1.
- Signal at bar t is tradable from bar t+1 last open.
- Size: isolated margin = 1/10 equity; notional = margin × 10.
- Stop: 2% of entry notional (≈ 2% equity / 0.2× margin), decided on
  bar t-1 mark, filled t open. No 5× take-profit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (
    FUNDING_BLACKOUT_MIN,
    GAP_FREEZE_MINUTES,
    HALF_SPREAD_BPS,
    LIQUIDATION_EXTRA_BPS,
    QTY_STEP,
    CompositeConfig,
    composite_config,
)
from .donchian import _prefix
from .simulator import (
    SimResult,
    _apply_fill,
    _exec_price,
    _impact_bps,
    _round_qty,
)

FAMILIES = ("ma", "slope", "vol", "pos")


def _rolling_z(raw: pd.Series, n: int, clip: float) -> pd.Series:
    mu = raw.rolling(n, min_periods=n).mean()
    sd = raw.rolling(n, min_periods=n).std(ddof=0)
    z = (raw - mu) / sd.replace(0.0, np.nan)
    if clip and clip > 0:
        z = z.clip(-float(clip), float(clip))
    return z


def add_composite(panel: pd.DataFrame, cfg: CompositeConfig | None = None) -> pd.DataFrame:
    """Add per-leg factor z's, equal-weight composite, and side."""
    cfg = cfg or composite_config()
    df = panel.copy()
    n = int(cfg.window)
    k = int(cfg.slope_lag)
    w_ma, w_slope, w_vol, w_pos = (float(x) for x in cfg.weights)
    wsum = w_ma + w_slope + w_vol + w_pos
    if wsum <= 0:
        raise ValueError("composite weights must sum to > 0")
    complete = df["pair_incomplete"] == 0
    for prefix in ("btc", "eth"):
        close = df[f"{prefix}_mark_close"].where(complete)
        high = df[f"{prefix}_mark_high"].where(complete)
        low = df[f"{prefix}_mark_low"].where(complete)
        qvol = df[f"{prefix}_quote_volume"].where(complete)
        ret = close.pct_change()

        sma = close.rolling(n, min_periods=n).mean()
        ma_raw = close / sma - 1.0
        slope_raw = sma / sma.shift(k) - 1.0
        vol_sma = qvol.rolling(n, min_periods=n).mean()
        vol_raw = (qvol / vol_sma - 1.0) * np.sign(ret.fillna(0.0))
        hh = high.rolling(n, min_periods=n).max()
        ll = low.rolling(n, min_periods=n).min()
        width = (hh - ll).replace(0.0, np.nan)
        pos_raw = (close - ll) / width - 0.5

        z_ma = _rolling_z(ma_raw, n, cfg.z_clip)
        z_slope = _rolling_z(slope_raw, n, cfg.z_clip)
        z_vol = _rolling_z(vol_raw, n, cfg.z_clip)
        z_pos = _rolling_z(pos_raw, n, cfg.z_clip)
        stack = pd.concat(
            [
                z_ma.rename("ma"),
                z_slope.rename("slope"),
                z_vol.rename("vol"),
                z_pos.rename("pos"),
            ],
            axis=1,
        )
        comp = (
            stack["ma"] * w_ma
            + stack["slope"] * w_slope
            + stack["vol"] * w_vol
            + stack["pos"] * w_pos
        ) / wsum
        long_s = comp >= float(cfg.entry_z)
        short_s = comp <= -float(cfg.entry_z)
        side = np.where(long_s, 1, np.where(short_s, -1, 0)).astype("int8")
        side = np.where(complete.to_numpy() & np.isfinite(comp.to_numpy()), side, 0).astype(
            "int8"
        )
        df[f"{prefix}_z_ma"] = z_ma
        df[f"{prefix}_z_slope"] = z_slope
        df[f"{prefix}_z_vol"] = z_vol
        df[f"{prefix}_z_pos"] = z_pos
        df[f"{prefix}_z_comp"] = comp
        df[f"{prefix}_comp_side"] = side
    return df


def run_composite(
    df: pd.DataFrame,
    *,
    symbol: str,
    cfg: CompositeConfig | None = None,
    trade_start_ts: int | None = None,
) -> SimResult:
    cfg = cfg or composite_config()
    prefix = _prefix(symbol)
    other = "eth" if prefix == "btc" else "btc"
    n = len(df)
    last_open = df[f"{prefix}_open"].to_numpy(dtype=float)
    last_high = df[f"{prefix}_high"].to_numpy(dtype=float)
    last_low = df[f"{prefix}_low"].to_numpy(dtype=float)
    mark = df[f"{prefix}_mark_close"].to_numpy(dtype=float)
    qvol = df[f"{prefix}_quote_volume"].to_numpy(dtype=float)
    side_sig = df[f"{prefix}_comp_side"].to_numpy(dtype=np.int8)
    z_comp = df[f"{prefix}_z_comp"].to_numpy(dtype=float)
    incomplete = df["pair_incomplete"].to_numpy(dtype=np.int8)
    gap_run = df["gap_run"].to_numpy(dtype=np.int32)
    fund_rate = df[f"{prefix}_funding_rate"].to_numpy(dtype=float)
    is_fund = df[f"{prefix}_is_funding"].to_numpy(dtype=bool)
    next_fund = df[f"{prefix}_next_funding_ts"].to_numpy(dtype=np.int64)
    ts = df["bar_open_ts"].to_numpy(dtype=np.int64)
    half = HALF_SPREAD_BPS[symbol]
    step = QTY_STEP[symbol]
    stop_pct = float(cfg.stop_price_pct)

    cash = float(cfg.starting_equity)
    qty = 0.0
    entry = 0.0
    margin_used = 0.0
    notional_entry = 0.0
    pos_side = 0
    liq_count = 0
    prev_mark = np.nan
    armed = True

    equity = np.full(n, np.nan)
    mark_pnl = np.zeros(n)
    fee_arr = np.zeros(n)
    fund_arr = np.zeros(n)
    liq_arr = np.zeros(n)
    pos = np.zeros(n)
    pos_other = np.zeros(n)
    lev_arr = np.zeros(n)
    reason = np.array([""] * n, dtype=object)
    trades: list[dict] = []

    def unrealized(px: float) -> float:
        if abs(qty) < 1e-16 or not np.isfinite(px):
            return 0.0
        return qty * (px - entry)

    def in_funding_blackout(i: int) -> bool:
        if next_fund[i] < 0:
            return False
        dist_min = abs(ts[i] - next_fund[i]) / 60_000
        return dist_min <= FUNDING_BLACKOUT_MIN

    def flatten(i: int, why: str, use_pessimistic: bool = False) -> None:
        nonlocal cash, qty, entry, margin_used, notional_entry, liq_count, pos_side, armed
        if abs(qty) < 1e-16:
            return
        buy = qty < 0
        if use_pessimistic and np.isfinite(last_high[i]) and np.isfinite(last_low[i]):
            px = last_high[i] if buy else last_low[i]
        else:
            notion = abs(qty) * last_open[i]
            imp = _impact_bps(notion, qvol[i])
            extra = LIQUIDATION_EXTRA_BPS if why.startswith("liq") else 0.0
            px = _exec_price(last_open[i], buy, half, imp + extra)
        fill_qty = -qty
        new_qty, new_entry, cash, fee, realized = _apply_fill(
            qty, entry, fill_qty, px, cash, cfg.taker_fee_bps
        )
        fee_arr[i] += fee
        trades.append(
            {
                "bar_open_ts": int(ts[i]),
                "leg": prefix,
                "fill_qty": fill_qty,
                "fill_px": px,
                "fee": fee,
                "realized": realized,
                "reason": why,
            }
        )
        qty, entry = new_qty, new_entry
        margin_used = 0.0
        notional_entry = 0.0
        pos_side = 0
        if why.startswith("liq"):
            liq_arr[i] += fee
            liq_count += 1
        if why in {"stop", "liq"}:
            armed = False
        reason[i] = why

    def enter(i: int, side: int) -> None:
        nonlocal cash, qty, entry, margin_used, notional_entry, pos_side
        px_mark = mark[i]
        eq = cash + unrealized(px_mark)
        if eq <= 0 or not np.isfinite(px_mark) or not np.isfinite(last_open[i]):
            return
        margin = float(cfg.fraction) * eq
        notion = margin * float(cfg.leverage)
        cap = cfg.adv_participation * max(qvol[i], 0.0)
        if cap > 0:
            notion = min(notion, cap)
        if notion < float(cfg.min_notional):
            return
        tgt = _round_qty(side * notion / last_open[i], step)
        if abs(tgt) < step:
            return
        buy = tgt > 0
        imp = _impact_bps(abs(tgt) * last_open[i], qvol[i])
        fill_px = _exec_price(last_open[i], buy, half, imp)
        fill_qty = tgt
        new_qty, new_entry, cash, fee, realized = _apply_fill(
            qty, entry, fill_qty, fill_px, cash, cfg.taker_fee_bps
        )
        fee_arr[i] += fee
        trades.append(
            {
                "bar_open_ts": int(ts[i]),
                "leg": prefix,
                "fill_qty": fill_qty,
                "fill_px": fill_px,
                "fee": fee,
                "realized": realized,
                "reason": "enter",
                "side": int(side),
            }
        )
        qty, entry = new_qty, new_entry
        notional_entry = abs(qty) * fill_px
        margin_used = notional_entry / float(cfg.leverage) if cfg.leverage else 0.0
        pos_side = 1 if qty > 0 else -1
        reason[i] = "enter"

    for i in range(n):
        if abs(qty) > 0 and is_fund[i] and np.isfinite(fund_rate[i]) and np.isfinite(prev_mark):
            notion = qty * prev_mark
            cash_f = -notion * float(fund_rate[i])
            cash += cash_f
            fund_arr[i] += cash_f

        tradable = (
            incomplete[i] == 0
            and np.isfinite(last_open[i])
            and np.isfinite(mark[i])
        )
        in_window = trade_start_ts is None or int(ts[i]) >= int(trade_start_ts)
        sig = int(side_sig[i - 1]) if i > 0 else 0
        u_prev = qty * (prev_mark - entry) if abs(qty) > 0 and np.isfinite(prev_mark) else 0.0
        in_pos = abs(qty) > 0
        if (not armed) and sig == 0:
            armed = True
        exited_this_bar = False

        if tradable:
            if in_pos:
                flatten_why = None
                if (gap_run[i] > GAP_FREEZE_MINUTES) or (
                    i > 0 and gap_run[i - 1] > GAP_FREEZE_MINUTES
                ):
                    flatten_why = "gap"
                elif (
                    stop_pct > 0
                    and notional_entry > 0
                    and np.isfinite(u_prev)
                    and u_prev <= -stop_pct * notional_entry
                ):
                    flatten_why = "stop"
                elif sig == 0 or sig != pos_side:
                    flatten_why = "exit"
                if flatten_why:
                    flatten(i, flatten_why, use_pessimistic=False)
                    in_pos = abs(qty) > 0
                    exited_this_bar = True
            if (not in_pos) and in_window and (not exited_this_bar):
                can_enter = (
                    armed
                    and sig != 0
                    and not in_funding_blackout(i)
                    and gap_run[i] == 0
                    and (i == 0 or gap_run[i - 1] <= GAP_FREEZE_MINUTES)
                )
                if can_enter:
                    enter(i, sig)

        px = mark[i] if tradable else prev_mark
        if tradable and np.isfinite(prev_mark) and abs(qty) > 0:
            mark_pnl[i] = qty * (px - prev_mark)
        u = unrealized(px)
        eq = cash + u
        g = abs(qty) * px if np.isfinite(px) else 0.0
        if tradable and abs(qty) > 0 and margin_used > 0 and np.isfinite(u):
            isolated_eq = margin_used + u
            if isolated_eq < cfg.mmr * max(g, notional_entry):
                flatten(i, "liq", use_pessimistic=True)
                u = unrealized(mark[i])
                eq = cash + u
                g = 0.0
        equity[i] = eq
        pos[i] = qty
        lev_arr[i] = (g / eq) if eq > 1e-8 else 0.0
        if tradable:
            prev_mark = mark[i]

    bars = df.copy()
    bars["equity"] = equity
    bars["mark_pnl"] = mark_pnl
    bars["mark_pnl_btc"] = mark_pnl if prefix == "btc" else 0.0
    bars["mark_pnl_eth"] = mark_pnl if prefix == "eth" else 0.0
    bars["fee"] = fee_arr
    bars["funding_cash"] = fund_arr
    bars["liq_flag"] = (liq_arr > 0).astype("int8")
    bars["qty_btc"] = pos if prefix == "btc" else pos_other
    bars["qty_eth"] = pos if prefix == "eth" else pos_other
    bars["gross_leverage"] = lev_arr
    bars["event"] = reason
    bars["z"] = z_comp
    trade_df = pd.DataFrame(trades)
    finite = equity[np.isfinite(equity)]
    summary = {
        "starting_equity": cfg.starting_equity,
        "ending_equity": float(finite[-1]) if len(finite) else cfg.starting_equity,
        "liquidation_events": int(liq_count),
        "n_fills": int(len(trade_df)),
        "fee_sum": float(fee_arr.sum()),
        "funding_sum": float(fund_arr.sum()),
        "mark_pnl_sum": float(mark_pnl.sum()),
        "symbol": symbol,
    }
    return SimResult(bars=bars, trades=trade_df, summary=summary)
