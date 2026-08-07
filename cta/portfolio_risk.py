# -*- coding: utf-8 -*-
"""保证金、相关性聚类与滚动 VaR 仓位控制。

约束（资金=1）：
- 单品种 10 倍杠杆 => 保证金 = |名义权重| / 10
- 全部策略总保证金 ≤ 30%  （总名义杠杆 ≤ 3x）
- 收益相关性 > 0.5 归为同一类；每类保证金 ≤ 10%
- 滚动 180 日历史 95% 单日 VaR ≤ 3%
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class MarginVaRLimits:
    instrument_leverage: float = 10.0  # 10 倍杠杆
    max_total_margin: float = 0.30  # 总占用资金 ≤ 30%
    max_cluster_margin: float = 0.10  # 每类保证金 ≤ 10%
    corr_threshold: float = 0.50
    var_window: int = 180
    var_alpha: float = 0.95
    max_var: float = 0.03  # 95% 单日 VaR ≤ 3%
    corr_window: int = 180
    min_history: int = 60


def margin_from_notional(notional_abs: pd.Series | float, leverage: float = 10.0) -> pd.Series | float:
    return notional_abs / leverage


def correlation_clusters(
    returns: pd.DataFrame,
    threshold: float = 0.5,
) -> Dict[str, int]:
    """基于相关矩阵的连通分量聚类：|corr| 不用，按 user 要求 corr>0.5（同向）。"""
    cols = list(returns.columns)
    if not cols:
        return {}
    corr = returns.corr().fillna(0.0)
    parent = {c: c for c in cols}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            if corr.loc[a, b] > threshold:
                union(a, b)

    roots = {c: find(c) for c in cols}
    root_ids = {r: i for i, r in enumerate(sorted(set(roots.values())))}
    return {c: root_ids[roots[c]] for c in cols}


def _scale_to_margin_caps(
    notionals: pd.Series,
    clusters: Dict[str, int],
    limits: MarginVaRLimits,
) -> pd.Series:
    """将名义权重缩放到满足总保证金与分类保证金上限。"""
    w = notionals.copy().astype(float)
    lev = limits.instrument_leverage
    if w.abs().sum() == 0:
        return w

    # 1) 总保证金
    total_margin = w.abs().sum() / lev
    if total_margin > limits.max_total_margin:
        w *= limits.max_total_margin / total_margin

    # 2) 分类保证金
    for cid in sorted(set(clusters.values())):
        members = [s for s, c in clusters.items() if c == cid and s in w.index]
        if not members:
            continue
        cm = w[members].abs().sum() / lev
        if cm > limits.max_cluster_margin:
            w[members] *= limits.max_cluster_margin / cm

    # 分类缩放可能使总和再超限，再压一次总保证金
    total_margin = w.abs().sum() / lev
    if total_margin > limits.max_total_margin:
        w *= limits.max_total_margin / total_margin
    return w


def historical_var(returns: pd.Series, alpha: float = 0.95) -> float:
    """历史模拟法 VaR（正数表示亏损幅度）。"""
    r = returns.dropna()
    if len(r) < 30:
        return 0.0
    q = np.quantile(r, 1.0 - alpha)
    return float(max(0.0, -q))


def apply_margin_var_controls(
    signal_directions: pd.DataFrame,
    asset_returns: pd.DataFrame,
    limits: MarginVaRLimits | None = None,
    base_notional_per_name: float = 1.0,
    cost_bps: float = 1.0,
    slip_bps: float = 1.0,
    initial_capital: float = 1.0,
) -> Tuple[pd.DataFrame, pd.Series, pd.Series, Dict[str, pd.Series], pd.DataFrame]:
    """按日因果施加保证金/相关性/VaR 约束。

    signal_directions: 各策略-品种方向，列名建议 method_symbol 或 symbol。
      这里约定列就是品种；多策略应先在外部合成/并列后传入。
      若列含多策略同一品种，调用方应先聚合。

    简化：输入为品种方向矩阵（已是多策略合成后的目标方向 ∈ [-1,1]），
    初始名义 = direction * base_notional_per_name，再按约束缩放。

    返回: weights(名义/NAV), net_returns, equity, diagnostics, cluster_ids_over_time
    """
    limits = limits or MarginVaRLimits()
    dirs = signal_directions.fillna(0.0).astype(float)
    rets = asset_returns.reindex(dirs.index).fillna(0.0)
    symbols = list(dirs.columns)

    n = len(dirs)
    weight_rows = np.zeros((n, len(symbols)))
    net_vals = np.zeros(n)
    equity_vals = np.zeros(n)
    total_margin = np.zeros(n)
    port_var = np.zeros(n)
    var_scale = np.ones(n)
    cluster_hist: List[Dict[str, int]] = []

    equity = float(initial_capital)
    prev_w = pd.Series(0.0, index=symbols)
    realized_port = []
    cluster_margin_daily = np.zeros(n)

    for i, dt in enumerate(dirs.index):
        start = max(0, i - limits.corr_window)
        hist = rets.iloc[start:i]
        if len(hist) >= limits.min_history:
            clusters = correlation_clusters(hist[symbols], threshold=limits.corr_threshold)
        else:
            clusters = {s: j for j, s in enumerate(symbols)}
        cluster_hist.append(clusters)

        raw = dirs.iloc[i] * base_notional_per_name
        capped = _scale_to_margin_caps(raw, clusters, limits)

        scale = 1.0
        v = 0.0
        if i >= limits.min_history:
            if len(realized_port) >= limits.min_history:
                window_rets = pd.Series(realized_port[-limits.var_window :])
                v = historical_var(window_rets, alpha=limits.var_alpha)
            look = rets.iloc[max(0, i - limits.var_window) : i]
            if len(look) >= limits.min_history:
                synth = look.mul(capped, axis=1).sum(axis=1)
                v = max(v, historical_var(synth, alpha=limits.var_alpha))
            if v > limits.max_var and v > 0:
                scale = limits.max_var / v
                capped = capped * scale
                capped = _scale_to_margin_caps(capped, clusters, limits)
                v = v * scale
        port_var[i] = v
        var_scale[i] = scale

        day_max_cm = 0.0
        for cid in set(clusters.values()):
            members = [s for s, c in clusters.items() if c == cid]
            cm = float(capped[members].abs().sum() / limits.instrument_leverage)
            day_max_cm = max(day_max_cm, cm)
        cluster_margin_daily[i] = day_max_cm

        weight_rows[i, :] = capped.to_numpy()
        total_margin[i] = float(capped.abs().sum() / limits.instrument_leverage)

        traded = prev_w
        gross = float((traded * rets.iloc[i]).sum())
        turnover = float((capped - prev_w).abs().sum())
        cost = turnover * ((cost_bps + slip_bps) / 10000.0)
        net = gross - cost
        equity *= 1.0 + net
        equity_vals[i] = equity
        net_vals[i] = net
        realized_port.append(net)
        prev_w = capped

    weights = pd.DataFrame(weight_rows, index=dirs.index, columns=symbols)
    net_s = pd.Series(net_vals, index=dirs.index, name="ret")
    equity_s = pd.Series(equity_vals, index=dirs.index, name="equity")
    diagnostics = {
        "total_margin": pd.Series(total_margin, index=dirs.index, name="total_margin"),
        "port_var95": pd.Series(port_var, index=dirs.index, name="port_var95"),
        "var_scale": pd.Series(var_scale, index=dirs.index, name="var_scale"),
        "cluster_margin_max": pd.Series(cluster_margin_daily, index=dirs.index, name="cluster_margin_max"),
    }
    cluster_df = pd.DataFrame(cluster_hist, index=dirs.index)
    return weights, net_s, equity_s, diagnostics, cluster_df
