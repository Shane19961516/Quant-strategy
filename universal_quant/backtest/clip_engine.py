"""10x isolated clips: scale in N child orders, stop = liquidation."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from universal_quant import config as cfg
from universal_quant.backtest.engine import BacktestResult, _f


def run_clip_backtest(
    df: pd.DataFrame,
    signal: pd.Series,
    *,
    symbol: str,
    model: str,
    spec: dict | None = None,
    n_clips: int = 100,
    leverage: float = 10.0,
    liq_pct: float = 0.10,
    slippage_ticks: float = 1.0,
    cost_mult: float = 1.0,
) -> BacktestResult:
    """Pulse at close t, scale 1 clip per next bar up to `n_clips`.

    Each clip is isolated 10x on 1/`n_clips` of NAV. Stop is price ±`liq_pct`
    from that clip's entry (exchange liquidation), not ATR. Opposite pulse
    flattens remaining clips. First liquidation stops further scale-in.
    """
    spec = spec or cfg.spec_for(symbol)
    work = df.copy()
    sigs = signal.reindex(work.index).fillna(0.0).to_numpy(float)
    n = len(work)
    opens = work["open"].to_numpy(float)
    highs = work["high"].to_numpy(float)
    lows = work["low"].to_numpy(float)
    closes = work["close"].to_numpy(float)
    tick = float(spec.get("tick_size", 0.01))
    c_bps = float(spec.get("commission_bps", 2.0)) * cost_mult
    clip_w = leverage / float(n_clips)
    nav = float(cfg.INITIAL_NAV)
    equity = np.full(n, nav)
    pending = 0.0
    campaign_side = 0.0
    adding = False
    added = 0
    clips: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    def fee(notional: float) -> float:
        return abs(notional) * (c_bps / 1e4)

    def slip_px(price: float, is_buy: bool) -> float:
        return price + tick * slippage_ticks if is_buy else price - tick * slippage_ticks

    def close_clip(i: int, clip: dict[str, Any], price: float, reason: str, cap_margin: bool) -> None:
        nonlocal nav
        side = clip["side"]
        is_buy = side < 0
        fill = slip_px(price, is_buy)
        raw = clip["notional"] * side * (fill - clip["entry"]) / clip["entry"]
        if cap_margin:
            raw = max(raw, -clip["margin"])
        nav += (raw - clip["marked"]) - fee(clip["notional"])
        clip["marked"] = raw
        trades.append(
            {
                "symbol": symbol,
                "model": model,
                "entry_time": str(work.index[clip["entry_i"]]),
                "exit_time": str(work.index[i]),
                "side": float(side),
                "entry_price": float(clip["entry"]),
                "exit_price": float(fill),
                "weight": float(clip["weight"]),
                "PnL": float(raw - fee(clip["notional"]) * 2.0),
                "exit_reason": reason,
                "bars_held": int(max(0, i - clip["entry_i"])),
            }
        )

    def flatten_all(i: int, price: float, reason: str) -> None:
        nonlocal clips, campaign_side, adding, added
        for clip in clips:
            close_clip(i, clip, price, reason, cap_margin=False)
        clips = []
        campaign_side = 0.0
        adding = False
        added = 0

    for i in range(n):
        if nav <= 1.0:
            equity[i] = max(nav, 0.0)
            pending = _f(sigs[i], 0.0)
            continue

        tgt = pending
        ds = 0.0 if tgt == 0 else (1.0 if tgt > 0 else -1.0)
        if ds != 0 and campaign_side != 0 and ds != campaign_side:
            flatten_all(i, opens[i], "signal_change")
        if ds != 0 and campaign_side == 0:
            campaign_side = ds
            adding = True
            added = 0

        if adding and campaign_side != 0 and added < n_clips and nav > 1.0:
            is_buy = campaign_side > 0
            fill = slip_px(opens[i], is_buy)
            notional = clip_w * nav
            if notional > 0 and fill > 0:
                nav -= fee(notional)
                clips.append(
                    {
                        "side": campaign_side,
                        "entry": fill,
                        "weight": clip_w,
                        "notional": notional,
                        "margin": notional / leverage,
                        "entry_i": i,
                        "marked": 0.0,
                    }
                )
                added += 1
                if added >= n_clips:
                    adding = False

        still: list[dict[str, Any]] = []
        liq_hit = False
        for clip in clips:
            side = clip["side"]
            stop = clip["entry"] * (1.0 - liq_pct) if side > 0 else clip["entry"] * (1.0 + liq_pct)
            hit = (side > 0 and lows[i] <= stop) or (side < 0 and highs[i] >= stop)
            if hit:
                close_clip(i, clip, stop, "liquidation", cap_margin=True)
                liq_hit = True
            else:
                still.append(clip)
        clips = still
        if liq_hit:
            adding = False
        if not clips:
            campaign_side = 0.0
            adding = False
            added = 0

        px = closes[i]
        if clips and px > 0:
            for clip in clips:
                raw = clip["notional"] * clip["side"] * (px - clip["entry"]) / clip["entry"]
                nav += raw - clip["marked"]
                clip["marked"] = raw

        equity[i] = max(nav, 0.0)
        pending = _f(sigs[i], 0.0)

    if clips:
        flatten_all(n - 1, closes[-1], "eod_flatten")
        equity[-1] = max(nav, 0.0)

    eq = pd.Series(equity, index=work.index, name="equity")
    return BacktestResult(
        symbol=symbol,
        model=model.upper(),
        equity=eq,
        daily_equity=eq.resample("1D").last().dropna(),
        trades=pd.DataFrame(trades),
        params={
            "symbol": symbol,
            "model": model,
            "n_clips": n_clips,
            "leverage": leverage,
            "liq_pct": liq_pct,
            "clip_weight": clip_w,
        },
    )
