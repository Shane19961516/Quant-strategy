"""1-minute dual-leg simulator: mark PnL, funding at settlement, no lookahead."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import (
    FUNDING_BLACKOUT_MIN,
    GAP_FREEZE_MINUTES,
    HALF_SPREAD_BPS,
    IMPACT_CAP_BPS,
    IMPACT_K,
    LIQUIDATION_EXTRA_BPS,
    QTY_STEP,
    BacktestConfig,
)


def _round_qty(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    signed = 1.0 if qty >= 0 else -1.0
    return signed * np.floor(abs(qty) / step + 1e-12) * step


def _exec_price(open_px: float, side_buy: bool, half_spread_bps: float, impact_bps: float) -> float:
    adj = (half_spread_bps + impact_bps) / 1e4
    return open_px * (1.0 + adj) if side_buy else open_px * (1.0 - adj)


def _impact_bps(order_notional: float, quote_volume: float) -> float:
    if quote_volume <= 0 or not np.isfinite(quote_volume):
        return IMPACT_CAP_BPS
    frac = IMPACT_K * abs(order_notional) / quote_volume
    return float(min(IMPACT_CAP_BPS, frac * 1e4))


def reversion_confirmed(z_sig: float, z_lag: float) -> bool:
    """Same-sign dislocation that is already shrinking vs 1d-ago z.

    This is a confirmation filter, not a looser |z| band. Expanding moves
    (including a fresh cross of the entry threshold) are blocked.
    """
    if not (np.isfinite(z_sig) and np.isfinite(z_lag)):
        return False
    if z_sig * z_lag <= 0:
        return False
    return abs(float(z_sig)) < abs(float(z_lag))


def _apply_fill(
    qty: float, entry: float, fill_qty: float, fill_px: float, cash: float, fee_bps: float
) -> tuple[float, float, float, float, float]:
    """Signed qty. Returns qty, entry, cash, fee, realized."""
    fee = abs(fill_qty) * fill_px * fee_bps / 1e4
    cash -= fee
    if abs(fill_qty) < 1e-16:
        return qty, entry, cash, fee, 0.0
    if abs(qty) < 1e-16:
        return fill_qty, fill_px, cash, fee, 0.0
    same = qty * fill_qty > 0
    if same:
        new_qty = qty + fill_qty
        new_entry = (abs(qty) * entry + abs(fill_qty) * fill_px) / abs(new_qty)
        return new_qty, new_entry, cash, fee, 0.0
    closed = min(abs(qty), abs(fill_qty))
    realized = closed * (fill_px - entry) * (1.0 if qty > 0 else -1.0)
    cash += realized
    new_qty = qty + fill_qty
    if abs(new_qty) < 1e-12:
        return 0.0, 0.0, cash, fee, realized
    if qty * new_qty < 0:
        return new_qty, fill_px, cash, fee, realized
    return new_qty, entry, cash, fee, realized


@dataclass
class SimResult:
    bars: pd.DataFrame
    trades: pd.DataFrame
    summary: dict = field(default_factory=dict)


def run_simulator(
    df: pd.DataFrame,
    cfg: BacktestConfig | None = None,
    trade_start_ts: int | None = None,
) -> SimResult:
    cfg = cfg or BacktestConfig()
    n = len(df)
    btc_open = df["btc_open"].to_numpy(dtype=float)
    eth_open = df["eth_open"].to_numpy(dtype=float)
    btc_high = df["btc_high"].to_numpy(dtype=float)
    btc_low = df["btc_low"].to_numpy(dtype=float)
    eth_high = df["eth_high"].to_numpy(dtype=float)
    eth_low = df["eth_low"].to_numpy(dtype=float)
    btc_mark = df["btc_mark_close"].to_numpy(dtype=float)
    eth_mark = df["eth_mark_close"].to_numpy(dtype=float)
    btc_qvol = df["btc_quote_volume"].to_numpy(dtype=float)
    eth_qvol = df["eth_quote_volume"].to_numpy(dtype=float)
    z = df["z"].to_numpy(dtype=float)
    beta = df["beta"].to_numpy(dtype=float)
    corr = df["corr"].to_numpy(dtype=float)
    if "spread_dev_bps" in df.columns:
        spread_dev = df["spread_dev_bps"].to_numpy(dtype=float)
    else:
        spread_dev = np.full(n, np.nan)
    if "z_lag_1d" in df.columns:
        z_lag_1d = df["z_lag_1d"].to_numpy(dtype=float)
    else:
        z_lag_1d = np.full(n, np.nan)
    incomplete = df["pair_incomplete"].to_numpy(dtype=np.int8)
    gap_run = df["gap_run"].to_numpy(dtype=np.int32)
    btc_fund = df["btc_funding_rate"].to_numpy(dtype=float)
    eth_fund = df["eth_funding_rate"].to_numpy(dtype=float)
    btc_is_fund = df["btc_is_funding"].to_numpy(dtype=bool)
    eth_is_fund = df["eth_is_funding"].to_numpy(dtype=bool)
    btc_next = df["btc_next_funding_ts"].to_numpy(dtype=np.int64)
    ts = df["bar_open_ts"].to_numpy(dtype=np.int64)

    cash = float(cfg.starting_equity)
    qty_btc = 0.0
    qty_eth = 0.0
    entry_btc = 0.0
    entry_eth = 0.0
    hold_bars = 0
    frozen_beta = 0.0
    liq_count = 0
    last_flat_i = -10**9

    equity = np.full(n, np.nan)
    notional_pnl = np.zeros(n)
    mark_pnl_btc = np.zeros(n)
    mark_pnl_eth = np.zeros(n)
    fee_arr = np.zeros(n)
    fund_arr = np.zeros(n)
    liq_arr = np.zeros(n)
    pos_btc = np.zeros(n)
    pos_eth = np.zeros(n)
    lev_arr = np.zeros(n)
    reason = np.array([""] * n, dtype=object)
    target_side = np.zeros(n, dtype=np.int8)

    trades: list[dict] = []
    prev_mark_btc = np.nan
    prev_mark_eth = np.nan

    def unrealized(mb: float, me: float) -> float:
        u = 0.0
        if abs(qty_btc) > 0:
            u += qty_btc * (mb - entry_btc)
        if abs(qty_eth) > 0:
            u += qty_eth * (me - entry_eth)
        return u

    def gross_notional(mb: float, me: float) -> float:
        return abs(qty_btc) * mb + abs(qty_eth) * me

    def in_funding_blackout(i: int) -> bool:
        if btc_next[i] < 0:
            return False
        dist_min = abs(ts[i] - btc_next[i]) / 60_000
        return dist_min <= FUNDING_BLACKOUT_MIN

    def flatten(i: int, why: str, use_pessimistic: bool = False) -> None:
        nonlocal cash, qty_btc, qty_eth, entry_btc, entry_eth, hold_bars, frozen_beta, liq_count
        if abs(qty_btc) < 1e-16 and abs(qty_eth) < 1e-16:
            return
        mb, me = btc_mark[i], eth_mark[i]
        fee_i = 0.0
        realized_i = 0.0
        for prefix, qty, entry, open_px, high, low, qvol, half in (
            (
                "btc",
                qty_btc,
                entry_btc,
                btc_open[i],
                btc_high[i],
                btc_low[i],
                btc_qvol[i],
                HALF_SPREAD_BPS["BTCUSDT"],
            ),
            (
                "eth",
                qty_eth,
                entry_eth,
                eth_open[i],
                eth_high[i],
                eth_low[i],
                eth_qvol[i],
                HALF_SPREAD_BPS["ETHUSDT"],
            ),
        ):
            if abs(qty) < 1e-16:
                continue
            buy = qty < 0  # covering a short
            if use_pessimistic and np.isfinite(high) and np.isfinite(low):
                px = high if buy else low
            else:
                notion = abs(qty) * open_px
                imp = _impact_bps(notion, qvol)
                extra = LIQUIDATION_EXTRA_BPS if why.startswith("liq") else 0.0
                px = _exec_price(open_px, buy, half, imp + extra)
            fill_qty = -qty
            new_qty, new_entry, cash, fee, realized = _apply_fill(
                qty, entry, fill_qty, px, cash, cfg.taker_fee_bps
            )
            fee_i += fee
            realized_i += realized
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
            if prefix == "btc":
                qty_btc, entry_btc = new_qty, new_entry
            else:
                qty_eth, entry_eth = new_qty, new_entry
        fee_arr[i] += fee_i
        if why.startswith("liq"):
            liq_arr[i] += abs(realized_i) * 0.0 + fee_i
            liq_count += 1
        hold_bars = 0
        frozen_beta = 0.0
        reason[i] = why

    def enter(i: int, side: int, z_sig: float, beta_i: float) -> None:
        """side +1 = long ETH / short β BTC. Sized on mark; filled on last open."""
        nonlocal cash, qty_btc, qty_eth, entry_btc, entry_eth, hold_bars, frozen_beta
        mb, me = btc_mark[i], eth_mark[i]
        eq = cash + unrealized(mb, me)
        if eq <= 0 or not np.isfinite(beta_i) or abs(beta_i) > 5:
            return
        gross_budget = eq * cfg.leverage
        denom = 1.0 + abs(beta_i)
        n_eth = gross_budget / denom
        n_btc = abs(beta_i) * n_eth
        # ADV cap on entries.
        cap_eth = cfg.adv_participation * max(eth_qvol[i], 0.0)
        cap_btc = cfg.adv_participation * max(btc_qvol[i], 0.0)
        if cap_eth > 0:
            n_eth = min(n_eth, cap_eth)
            n_btc = abs(beta_i) * n_eth
        if cap_btc > 0 and n_btc > cap_btc:
            n_btc = cap_btc
            n_eth = n_btc / max(abs(beta_i), 1e-8)
        if n_eth < 50 or n_btc < 50:
            return
        qty_e_tgt = _round_qty(side * n_eth / me, QTY_STEP["ETHUSDT"])
        qty_b_tgt = _round_qty(-side * beta_i * n_eth / mb, QTY_STEP["BTCUSDT"])
        if abs(qty_e_tgt) < QTY_STEP["ETHUSDT"] or abs(qty_b_tgt) < QTY_STEP["BTCUSDT"]:
            return

        fee_i = 0.0
        for prefix, tgt, open_px, high, low, qvol, half, step in (
            (
                "btc",
                qty_b_tgt,
                btc_open[i],
                btc_high[i],
                btc_low[i],
                btc_qvol[i],
                HALF_SPREAD_BPS["BTCUSDT"],
                QTY_STEP["BTCUSDT"],
            ),
            (
                "eth",
                qty_e_tgt,
                eth_open[i],
                eth_high[i],
                eth_low[i],
                eth_qvol[i],
                HALF_SPREAD_BPS["ETHUSDT"],
                QTY_STEP["ETHUSDT"],
            ),
        ):
            fill_qty = _round_qty(tgt, step)
            buy = fill_qty > 0
            if cfg.exec_mode == "pessimistic":
                px = high if buy else low
            else:
                imp = _impact_bps(abs(fill_qty) * open_px, qvol)
                px = _exec_price(open_px, buy, half, imp)
            cur_qty = qty_btc if prefix == "btc" else qty_eth
            cur_entry = entry_btc if prefix == "btc" else entry_eth
            new_qty, new_entry, cash, fee, realized = _apply_fill(
                cur_qty, cur_entry, fill_qty, px, cash, cfg.taker_fee_bps
            )
            fee_i += fee
            trades.append(
                {
                    "bar_open_ts": int(ts[i]),
                    "leg": prefix,
                    "fill_qty": fill_qty,
                    "fill_px": px,
                    "fee": fee,
                    "realized": realized,
                    "reason": "enter",
                    "z": float(z_sig),
                    "beta": float(beta_i),
                    "side": int(side),
                }
            )
            if prefix == "btc":
                qty_btc, entry_btc = new_qty, new_entry
            else:
                qty_eth, entry_eth = new_qty, new_entry
        fee_arr[i] += fee_i
        hold_bars = 1
        frozen_beta = float(beta_i)
        reason[i] = "enter"
        target_side[i] = np.int8(side)

    for i in range(n):
        # Funding on the position held into this minute, settlement bars only.
        if abs(qty_btc) > 0 and btc_is_fund[i] and np.isfinite(btc_fund[i]) and np.isfinite(prev_mark_btc):
            notion = qty_btc * prev_mark_btc
            cash_f = -notion * float(btc_fund[i])
            cash += cash_f
            fund_arr[i] += cash_f
        if abs(qty_eth) > 0 and eth_is_fund[i] and np.isfinite(eth_fund[i]) and np.isfinite(prev_mark_eth):
            notion = qty_eth * prev_mark_eth
            cash_f = -notion * float(eth_fund[i])
            cash += cash_f
            fund_arr[i] += cash_f

        tradable = (
            incomplete[i] == 0
            and np.isfinite(btc_open[i])
            and np.isfinite(eth_open[i])
            and np.isfinite(btc_mark[i])
            and np.isfinite(eth_mark[i])
        )
        in_window = trade_start_ts is None or int(ts[i]) >= int(trade_start_ts)

        # Signal from bar i-1 is executable at bar i open (no lookahead).
        z_sig = z[i - 1] if i > 0 else np.nan
        beta_sig = beta[i - 1] if i > 0 else np.nan
        corr_sig = corr[i - 1] if i > 0 else np.nan
        dev_sig = spread_dev[i - 1] if i > 0 else np.nan
        z_lag_sig = z_lag_1d[i - 1] if i > 0 else np.nan
        in_pos = abs(qty_btc) > 0 or abs(qty_eth) > 0

        if tradable:
            if in_pos:
                hold_bars += 1
                flatten_why = None
                if (gap_run[i] > GAP_FREEZE_MINUTES) or (
                    i > 0 and gap_run[i - 1] > GAP_FREEZE_MINUTES
                ):
                    flatten_why = "gap"
                elif not np.isfinite(z_sig):
                    flatten_why = "nan_z"
                elif abs(z_sig) <= cfg.exit_z:
                    flatten_why = "exit"
                elif abs(z_sig) >= cfg.stop_z:
                    flatten_why = "stop"
                elif hold_bars >= cfg.max_hold_bars:
                    flatten_why = "time"
                elif np.isfinite(corr_sig) and corr_sig < cfg.corr_min:
                    flatten_why = "corr_break"
                if flatten_why:
                    flatten(i, flatten_why, use_pessimistic=(cfg.exec_mode == "pessimistic"))
                    in_pos = abs(qty_btc) > 0 or abs(qty_eth) > 0
                    last_flat_i = i
            else:
                stride_ok = cfg.decision_stride <= 1 or (
                    (int(ts[i]) // 60_000) % int(cfg.decision_stride) == 0
                )
                if cfg.entry_hour_utc is not None:
                    minute_of_day = (int(ts[i]) // 60_000) % 1440
                    stride_ok = minute_of_day == int(cfg.entry_hour_utc) * 60
                cool_ok = cfg.cooldown_bars <= 0 or (i - last_flat_i) >= int(cfg.cooldown_bars)
                hurdle_ok = cfg.cost_hurdle_bps <= 0 or (
                    np.isfinite(dev_sig) and abs(float(dev_sig)) >= float(cfg.cost_hurdle_bps)
                )
                confirm_ok = (not cfg.require_reversion) or reversion_confirmed(z_sig, z_lag_sig)
                can_enter = (
                    in_window
                    and np.isfinite(z_sig)
                    and np.isfinite(beta_sig)
                    and np.isfinite(corr_sig)
                    and corr_sig >= cfg.corr_min
                    and not in_funding_blackout(i)
                    and gap_run[i] == 0
                    and (i == 0 or gap_run[i - 1] <= GAP_FREEZE_MINUTES)
                    and stride_ok
                    and cool_ok
                    and hurdle_ok
                    and confirm_ok
                )
                side_sign = -1 if cfg.invert_signal else 1
                if can_enter and cfg.entry_z <= z_sig < cfg.stop_z:
                    enter(i, side_sign * -1, z_sig, beta_sig)
                elif can_enter and -cfg.stop_z < z_sig <= -cfg.entry_z:
                    enter(i, side_sign * 1, z_sig, beta_sig)

        mb = btc_mark[i] if tradable else prev_mark_btc
        me = eth_mark[i] if tradable else prev_mark_eth
        if tradable and np.isfinite(prev_mark_btc) and abs(qty_btc) > 0:
            mark_pnl_btc[i] = qty_btc * (mb - prev_mark_btc)
        if tradable and np.isfinite(prev_mark_eth) and abs(qty_eth) > 0:
            mark_pnl_eth[i] = qty_eth * (me - prev_mark_eth)
        notional_pnl[i] = mark_pnl_btc[i] + mark_pnl_eth[i]

        u = unrealized(mb, me) if np.isfinite(mb) and np.isfinite(me) else 0.0
        eq = cash + u
        g = gross_notional(mb, me) if np.isfinite(mb) and np.isfinite(me) else 0.0
        # Liquidation on mark (cross). Check after marking this bar.
        if tradable and g > 0 and eq < cfg.mmr * g:
            flatten(i, "liq", use_pessimistic=True)
            last_flat_i = i
            u = unrealized(btc_mark[i], eth_mark[i])
            eq = cash + u
            g = 0.0
        equity[i] = eq
        pos_btc[i] = qty_btc
        pos_eth[i] = qty_eth
        lev_arr[i] = (g / eq) if eq > 1e-8 else 0.0
        if tradable:
            prev_mark_btc = btc_mark[i]
            prev_mark_eth = eth_mark[i]

        if in_pos and hold_bars > 0 and abs(qty_eth) > 0:
            target_side[i] = np.int8(1 if qty_eth > 0 else -1)

    bars = df.copy()
    bars["equity"] = equity
    bars["mark_pnl"] = notional_pnl
    bars["mark_pnl_btc"] = mark_pnl_btc
    bars["mark_pnl_eth"] = mark_pnl_eth
    bars["fee"] = fee_arr
    bars["funding_cash"] = fund_arr
    bars["liq_flag"] = (liq_arr > 0).astype("int8")
    bars["qty_btc"] = pos_btc
    bars["qty_eth"] = pos_eth
    bars["gross_leverage"] = lev_arr
    bars["event"] = reason
    bars["side"] = target_side
    bars["equity_ret"] = bars["equity"].diff() / cfg.starting_equity
    trade_df = pd.DataFrame(trades)
    summary = {
        "starting_equity": cfg.starting_equity,
        "ending_equity": float(equity[np.isfinite(equity)][-1]) if np.isfinite(equity).any() else cfg.starting_equity,
        "liquidation_events": int(liq_count),
        "n_fills": int(len(trade_df)),
        "fee_sum": float(fee_arr.sum()),
        "funding_sum": float(fund_arr.sum()),
        "mark_pnl_sum": float(notional_pnl.sum()),
    }
    return SimResult(bars=bars, trades=trade_df, summary=summary)
