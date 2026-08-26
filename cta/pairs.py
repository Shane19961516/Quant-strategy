# -*- coding: utf-8 -*-
"""统计套利 / 价差配对：仅使用有产业逻辑的品种对，避免全市场挖矿。

信号：对数价差的滚动 z-score
- |z| > entry_z 开仓（高估空价差 / 低估多价差）
- z 回归到 exit_z（默认 0）平仓
- |z| >= stop_z 止损离场（反转策略：截断继续发散的亏损）
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# 经济关联配对（黑色、油脂、化工、谷物链条）
DEFAULT_ECONOMIC_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("RB", "HC"),  # 螺纹-热卷
    ("I", "RB"),  # 铁矿-螺纹
    ("Y", "M"),  # 豆油-豆粕
    ("C", "M"),  # 玉米-豆粕
    ("MA", "TA"),  # 甲醇-PTA
)


def available_pairs(
    symbols: Iterable[str],
    pairs: Optional[Sequence[Tuple[str, str]]] = None,
) -> List[Tuple[str, str]]:
    syms = {s.upper() for s in symbols}
    out = []
    for a, b in pairs or DEFAULT_ECONOMIC_PAIRS:
        a, b = a.upper(), b.upper()
        if a in syms and b in syms and a != b:
            out.append((a, b))
    return out


def pair_spread_z(
    close_a: pd.Series,
    close_b: pd.Series,
    window: int = 60,
) -> pd.Series:
    """对数价差滚动 z-score。"""
    a = np.log(close_a.astype(float))
    b = np.log(close_b.astype(float))
    spread = (a - b).rename("spread")
    mu = spread.rolling(window, min_periods=window).mean()
    sd = spread.rolling(window, min_periods=window).std()
    z = (spread - mu) / sd.replace(0.0, np.nan)
    return z.rename("z")


def pair_leg_signals(
    close_a: pd.Series,
    close_b: pd.Series,
    window: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.0,
    stop_z: float = 3.5,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """返回 (signal_a, signal_b, z)。

    pos=+1：做多 A / 做空 B（价差低估）
    pos=-1：做空 A / 做多 B（价差高估）
    """
    if stop_z <= entry_z:
        raise ValueError("stop_z must be > entry_z")
    z = pair_spread_z(close_a, close_b, window=window)
    zz = z.to_numpy(dtype=float)
    n = len(zz)
    sa = np.zeros(n, dtype=float)
    sb = np.zeros(n, dtype=float)
    pos = 0.0
    for i in range(n):
        zi = zz[i]
        if np.isnan(zi):
            sa[i] = 0.0
            sb[i] = 0.0
            continue
        if pos == 0.0:
            if zi >= entry_z:
                pos = -1.0
            elif zi <= -entry_z:
                pos = 1.0
        else:
            # 止损：价差继续发散
            if abs(zi) >= stop_z:
                pos = 0.0
            # 回归出场
            elif pos > 0 and zi >= exit_z:
                pos = 0.0
            elif pos < 0 and zi <= -exit_z:
                pos = 0.0
        sa[i] = pos
        sb[i] = -pos
    idx = z.index
    return (
        pd.Series(sa, index=idx, name=getattr(close_a, "name", "A")),
        pd.Series(sb, index=idx, name=getattr(close_b, "name", "B")),
        z,
    )


def build_pairs_symbol_signals(
    panels: Dict[str, pd.DataFrame],
    params: Dict[str, float],
    pairs: Optional[Sequence[Tuple[str, str]]] = None,
) -> pd.DataFrame:
    """将多对配对信号合成到品种维度（等权平均）。"""
    use = available_pairs(panels.keys(), pairs)
    if not use:
        raise RuntimeError("no available economic pairs in panels")
    window = int(params.get("window", 60))
    entry_z = float(params.get("entry_z", 2.0))
    exit_z = float(params.get("exit_z", 0.0))
    stop_z = float(params.get("stop_z", 3.5))

    acc: Dict[str, pd.Series] = {s: None for s in panels}  # type: ignore
    counts = {s: 0 for s in panels}
    for a, b in use:
        sa, sb, _ = pair_leg_signals(
            panels[a]["close"],
            panels[b]["close"],
            window=window,
            entry_z=entry_z,
            exit_z=exit_z,
            stop_z=stop_z,
        )
        for sym, sig in ((a, sa), (b, sb)):
            counts[sym] += 1
            if acc[sym] is None:
                acc[sym] = sig.astype(float)
            else:
                acc[sym] = acc[sym].add(sig, fill_value=0.0)

    # 对参与配对的品种：多对信号求和后裁剪到 [-1,1]；未参与保持 0
    data = {}
    for sym in panels:
        if counts[sym] == 0 or acc[sym] is None:
            data[sym] = pd.Series(0.0, index=panels[sym].index)
        else:
            data[sym] = (
                acc[sym].reindex(panels[sym].index).fillna(0.0).clip(-1.0, 1.0)
            )
    return pd.DataFrame(data).sort_index().fillna(0.0)


def unit_pair_returns(
    panels: Dict[str, pd.DataFrame],
    params: Dict[str, float],
    pairs: Optional[Sequence[Tuple[str, str]]] = None,
    cost_bps: float = 0.5,
) -> pd.DataFrame:
    """每列一对的单位美元中性收益（每腿 0.5 名义）。"""
    use = available_pairs(panels.keys(), pairs)
    cols = {}
    window = int(params.get("window", 60))
    entry_z = float(params.get("entry_z", 2.0))
    exit_z = float(params.get("exit_z", 0.0))
    stop_z = float(params.get("stop_z", 3.5))
    for a, b in use:
        sa, sb, _ = pair_leg_signals(
            panels[a]["close"],
            panels[b]["close"],
            window=window,
            entry_z=entry_z,
            exit_z=exit_z,
            stop_z=stop_z,
        )
        ra = panels[a]["close"].pct_change().fillna(0.0)
        rb = panels[b]["close"].pct_change().fillna(0.0)
        ra = ra.mask(ra.abs() > 0.08, 0.0)
        rb = rb.mask(rb.abs() > 0.08, 0.0)
        ta, tb = sa.shift(1).fillna(0.0), sb.shift(1).fillna(0.0)
        pnl = 0.5 * (ta * ra + tb * rb)
        turn = 0.5 * (
            sa.diff().abs().fillna(sa.abs()) + sb.diff().abs().fillna(sb.abs())
        )
        cols[f"{a}_{b}"] = pnl - turn * (cost_bps / 10000.0)
    return pd.DataFrame(cols).sort_index().fillna(0.0)


def apply_pairs_book_controls(
    panels: Dict[str, pd.DataFrame],
    params: Dict[str, float],
    asset_returns: pd.DataFrame,
    limits,
    cost_bps: float = 0.5,
    slip_bps: float = 0.5,
    pairs: Optional[Sequence[Tuple[str, str]]] = None,
) -> Tuple[pd.DataFrame, pd.Series, pd.Series, Dict[str, pd.Series]]:
    """配对组合风控：整本书统一缩放，保持每对美元中性（不拆腿）。

    满仓时目标总名义杠杆 = max_total_margin * instrument_leverage，再按滚动 VaR
    与单对保证金上限等比压缩。
    """
    from .portfolio_risk import MarginVaRLimits, historical_var

    limits = limits or MarginVaRLimits()
    use = available_pairs(panels.keys(), pairs)
    if not use:
        raise RuntimeError("no pairs for book controls")

    window = int(params.get("window", 60))
    entry_z = float(params.get("entry_z", 2.0))
    exit_z = float(params.get("exit_z", 0.0))
    stop_z = float(params.get("stop_z", 3.5))

    idx = asset_returns.index
    symbols = list(asset_returns.columns)
    n = len(idx)
    n_pairs = len(use)
    max_gross = limits.max_total_margin * limits.instrument_leverage
    leg_base = max_gross / (2.0 * n_pairs)

    pair_pos = []
    for a, b in use:
        sa, sb, _ = pair_leg_signals(
            panels[a]["close"],
            panels[b]["close"],
            window=window,
            entry_z=entry_z,
            exit_z=exit_z,
            stop_z=stop_z,
        )
        pair_pos.append(
            (a, b, sa.reindex(idx).fillna(0.0), sb.reindex(idx).fillna(0.0))
        )

    weight_rows = np.zeros((n, len(symbols)))
    net_vals = np.zeros(n)
    equity_vals = np.zeros(n)
    total_margin = np.zeros(n)
    port_var = np.zeros(n)
    var_scale_arr = np.ones(n)
    cluster_margin_daily = np.zeros(n)

    equity = 1.0
    prev_w = pd.Series(0.0, index=symbols)
    rets = asset_returns.reindex(idx).fillna(0.0)

    for i in range(n):
        raw = pd.Series(0.0, index=symbols)
        for a, b, sa, sb in pair_pos:
            raw[a] = float(raw[a]) + float(sa.iloc[i]) * leg_base
            raw[b] = float(raw[b]) + float(sb.iloc[i]) * leg_base

        scale = 1.0
        gross = float(raw.abs().sum())
        if gross > 1e-12:
            margin = gross / limits.instrument_leverage
            if margin > limits.max_total_margin:
                scale *= limits.max_total_margin / margin
            elif margin > 1e-15:
                # 未用满保证金时整书上调（保持中性）
                scale *= min(limits.max_total_margin / margin, 8.0)
        scaled = raw * scale

        look = rets.iloc[max(0, i - limits.var_window) : i]
        v = 0.0
        if len(look) >= limits.min_history and float(scaled.abs().sum()) > 0:
            synth = look.mul(scaled, axis=1).sum(axis=1)
            v = historical_var(synth, alpha=limits.var_alpha)
            if v > limits.max_var and v > 0:
                vs = limits.max_var / v
                scaled = scaled * vs
                scale *= vs
                v = limits.max_var

        max_cm = 0.0
        for a, b, sa, sb in pair_pos:
            cm = (abs(float(scaled[a])) + abs(float(scaled[b]))) / limits.instrument_leverage
            max_cm = max(max_cm, cm)
        if max_cm > limits.max_cluster_margin + 1e-12 and max_cm > 0:
            vs2 = limits.max_cluster_margin / max_cm
            scaled = scaled * vs2
            scale *= vs2
            max_cm = limits.max_cluster_margin
            # 分类压缩后若总保证金仍超，再压
            tm = float(scaled.abs().sum() / limits.instrument_leverage)
            if tm > limits.max_total_margin + 1e-12:
                vs3 = limits.max_total_margin / tm
                scaled = scaled * vs3
                scale *= vs3
                max_cm *= vs3

        var_scale_arr[i] = scale
        port_var[i] = float(v)
        total_margin[i] = float(scaled.abs().sum() / limits.instrument_leverage)
        cluster_margin_daily[i] = max_cm
        weight_rows[i, :] = scaled.reindex(symbols).fillna(0.0).to_numpy()

        traded = prev_w
        gross_pnl = float((traded * rets.iloc[i]).sum())
        turnover = float((scaled - prev_w).abs().sum())
        cost = turnover * ((cost_bps + slip_bps) / 10000.0)
        net = gross_pnl - cost
        equity *= 1.0 + net
        equity_vals[i] = equity
        net_vals[i] = net
        prev_w = scaled

    weights = pd.DataFrame(weight_rows, index=idx, columns=symbols)
    net_s = pd.Series(net_vals, index=idx, name="ret")
    equity_s = pd.Series(equity_vals, index=idx, name="equity")
    diagnostics = {
        "total_margin": pd.Series(total_margin, index=idx, name="total_margin"),
        "port_var95": pd.Series(port_var, index=idx, name="port_var95"),
        "var_scale": pd.Series(var_scale_arr, index=idx, name="var_scale"),
        "cluster_margin_max": pd.Series(cluster_margin_daily, index=idx, name="cluster_margin_max"),
    }
    return weights, net_s, equity_s, diagnostics
