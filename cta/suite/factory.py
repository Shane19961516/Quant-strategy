# -*- coding: utf-8 -*-
"""多袖层工厂：把低相关押注拆开，提高有效广度（冲 Sharpe≥2）。

原则（文献）：组合夏普 ≈ 单笔夏普 × sqrt(独立押注数) / 相关惩罚。
做法：每个产业对、每个跨期品种、多个趋势周期各自成袖层，再用活动感知配资合成。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..book.strategies_4 import (
    CONTRACT_SPEC,
    _backtest_symbol_calendar,
    load_cached_contracts_only,
    simulate_spread_book_realistic,
)
from ..pairs import pair_leg_signals
from ..signals import donchian_breakout_signal, dual_ma_signal, ts_momentum_signal
from ..stops import StopConfig, apply_atr_stop
from .arb import _half_life
from .noleverage import _align_closes, simulate_directional, simulate_pairs
from .trend import _filter_panels
from .universe import ALL_CALENDAR_SYMBOLS, available_full_pairs, full_calendar_config


def _pair_sleeve(
    panels: Dict[str, pd.DataFrame],
    a: str,
    b: str,
    capital: float,
    cost_bps: float,
    slip_bps: float,
    entry_z: float = 2.5,
    exit_z: float = 0.5,
    stop_z: float = 3.5,
    window: int = 60,
    min_corr: float = 0.55,
) -> Tuple[pd.Series, pd.Series]:
    a, b = a.upper(), b.upper()
    if a not in panels or b not in panels:
        idx = _align_closes(panels).index
        z = pd.Series(0.0, index=idx)
        return pd.Series(1.0, index=idx), z
    sa, sb, _ = pair_leg_signals(
        panels[a]["close"],
        panels[b]["close"],
        window=window,
        entry_z=entry_z,
        exit_z=exit_z,
        stop_z=stop_z,
    )
    ra = panels[a]["close"].pct_change()
    rb = panels[b]["close"].pct_change()
    corr = ra.rolling(60, min_periods=60).corr(rb)
    spread = np.log(panels[a]["close"].astype(float)) - np.log(panels[b]["close"].astype(float))
    idx = sa.index.union(sb.index).sort_values()
    sa = sa.reindex(idx).fillna(0.0)
    sb = sb.reindex(idx).fillna(0.0)
    corr = corr.reindex(idx)
    ga = sa.copy()
    gb = sb.copy()
    pos = 0.0
    for i in range(len(idx)):
        c = corr.iloc[i]
        hl = _half_life(spread.iloc[max(0, i + 1 - 80) : i + 1]) if i >= 30 else float("nan")
        allow = pd.notna(c) and float(c) >= min_corr and pd.notna(hl) and 5.0 <= float(hl) <= 45.0
        raw = float(sa.iloc[i])
        if not allow:
            pos = 0.0
        elif pos == 0.0:
            pos = float(np.sign(raw)) if raw != 0 else 0.0
        else:
            if raw == 0.0:
                pos = 0.0
            elif float(np.sign(raw)) != pos:
                pos = float(np.sign(raw))
        ga.iloc[i] = pos
        gb.iloc[i] = -pos if pos != 0 else 0.0
    closes = _align_closes({a: panels[a], b: panels[b]}).reindex(idx).ffill()
    legs = pd.DataFrame({a: ga, b: gb}).reindex(closes.index).fillna(0.0)
    nav, ret, _ = simulate_pairs(legs, closes, capital, cost_bps, slip_bps, max_leverage=1.0)
    return nav, ret


def _calendar_sleeve(
    panels: Dict[str, pd.DataFrame],
    sym: str,
    capital: float,
    contract_cache: str,
) -> Tuple[pd.Series, pd.Series]:
    store = load_cached_contracts_only([sym], cache_dir=contract_cache)
    cal = full_calendar_config(allow=(sym.upper(),))
    book = {}
    contracts = store.get(sym.upper(), {})
    if len(contracts) >= 2:
        df = _backtest_symbol_calendar(sym.upper(), contracts, cal)
        if not df.empty:
            book[sym.upper()] = df
    idx = _align_closes(panels).index
    if not book:
        z = pd.Series(0.0, index=idx)
        return pd.Series(1.0, index=idx), z
    nav, ret, _, _, _ = simulate_spread_book_realistic(
        book, margin_budget=capital * 0.10, capital=capital, cal_cfg=cal
    )
    ret = ret.reindex(idx).fillna(0.0)
    nav = (1.0 + ret).cumprod()
    return nav, ret


def _trend_sleeve(
    panels: Dict[str, pd.DataFrame],
    kind: str,
    capital: float,
    cost_bps: float,
    slip_bps: float,
) -> Tuple[pd.Series, pd.Series]:
    panels = _filter_panels(panels)
    closes = _align_closes(panels)
    if kind == "tsmom60":
        sig = pd.DataFrame({s: ts_momentum_signal(df["close"], 60, 1) for s, df in panels.items()})
    elif kind == "tsmom120":
        sig = pd.DataFrame({s: ts_momentum_signal(df["close"], 120, 1) for s, df in panels.items()})
    elif kind == "donchian55":
        sig = pd.DataFrame(
            {
                s: donchian_breakout_signal(df["high"], df["low"], df["close"], 55, 20)
                for s, df in panels.items()
            }
        )
    elif kind == "dualma_atr":
        cfg = StopConfig(atr_mult=2.5, trail_mult=3.0, use_trailing=True)
        cols = {}
        for s, df in panels.items():
            raw = dual_ma_signal(df["close"], 20, 60)
            cols[s] = apply_atr_stop(raw, df["high"], df["low"], df["close"], cfg)[0]
        sig = pd.DataFrame(cols)
    else:
        raise ValueError(kind)
    sig = sig.reindex(closes.index).fillna(0.0)
    nav, ret, _ = simulate_directional(
        sig, closes, capital, cost_bps, slip_bps, use_inv_vol=True, max_leverage=1.0
    )
    return nav, ret


def build_breadth_sleeves(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
    full_universe: bool = True,
) -> Dict[str, pd.Series]:
    """返回各袖层日收益（已按自身 full capital / lev≤1 计）。

    full_universe=True：全商品配对 + 全品种跨期（除 IF）。
    """
    # 趋势可含 IF；套利/跨期不含
    panels_all = {k.upper(): v for k, v in panels.items()}
    panels_cmd = {k: v for k, v in panels_all.items() if k != "IF"}
    rets: Dict[str, pd.Series] = {}

    for kind in ("tsmom60", "tsmom120", "donchian55", "dualma_atr"):
        _, r = _trend_sleeve(panels_all if full_universe else panels_cmd, kind, capital, cost_bps, slip_bps)
        rets[f"trend_{kind}"] = r.fillna(0.0)

    pairs = available_full_pairs(panels_cmd.keys()) if full_universe else available_full_pairs(
        [s for s in ("RB", "HC", "I", "Y", "M", "C", "MA", "TA") if s in panels_cmd]
    )
    for a, b in pairs:
        _, r = _pair_sleeve(panels_cmd, a, b, capital, cost_bps, slip_bps)
        rets[f"pair_{a}_{b}"] = r.fillna(0.0)

    cal_syms = ALL_CALENDAR_SYMBOLS if full_universe else ("RB", "HC", "I", "CU")
    for sym in cal_syms:
        if sym in panels_cmd or sym in CONTRACT_SPEC:
            _, r = _calendar_sleeve(panels_cmd, sym, capital, contract_cache)
            rets[f"cal_{sym}"] = r.fillna(0.0)

    return rets


def activity_aware_portfolio(
    sleeve_rets: Dict[str, pd.Series],
    vol_lookback: int = 60,
    activity_eps: float = 1e-12,
    max_weight: float = 0.25,
    min_hist: int = 40,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """活动感知配资：昨日有盈亏活动的袖层才分钱，逆波动加权，单袖层权重上限。

    这是实盘可做的「闲置资金回收」，不是杠杆放大。
    """
    R = pd.DataFrame(sleeve_rets).sort_index().fillna(0.0)
    vols = R.rolling(vol_lookback, min_periods=20).std()
    # 活动：近 3 日有非零收益视为在场（用已实现，无前视）
    active = (R.abs().rolling(3, min_periods=1).max() > activity_eps).astype(float)
    active = active.shift(1).fillna(0.0)  # 昨日可知

    w_arr = np.zeros((len(R), len(R.columns)), dtype=float)
    cols = list(R.columns)
    col_ix = {c: j for j, c in enumerate(cols)}
    vol_v = vols.to_numpy(dtype=float)
    act_v = active.to_numpy(dtype=float)

    for i in range(len(R)):
        if i < min_hist:
            continue
        act_idx = [j for j, c in enumerate(cols) if act_v[i, j] > 0]
        if not act_idx:
            act_idx = list(range(len(cols)))
        inv = []
        for j in act_idx:
            v = vol_v[i, j]
            inv.append(0.0 if (not np.isfinite(v) or v < 1e-12) else 1.0 / v)
        inv_arr = np.asarray(inv, dtype=float)
        if inv_arr.sum() <= 0:
            inv_arr = np.ones(len(act_idx))
        ww = inv_arr / inv_arr.sum()
        for _ in range(8):
            ww = np.minimum(ww, max_weight)
            s = float(ww.sum())
            if s <= 0:
                ww = np.ones(len(act_idx)) / len(act_idx)
                break
            ww = ww / s
            if float(ww.max()) <= max_weight + 1e-12:
                break
        for j, wi in zip(act_idx, ww):
            w_arr[i, j] = float(wi)

    w = pd.DataFrame(w_arr, index=R.index, columns=cols)
    w_lag = w.shift(1).fillna(0.0)
    port = (w_lag * R).sum(axis=1).rename("ret")
    nav = (1.0 + port).cumprod().rename("nav")
    return nav, port, w
