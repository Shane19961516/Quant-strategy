# -*- coding: utf-8 -*-
"""保证金、相关性聚类与滚动 VaR 仓位控制。

约束（资金=1）：
- 单品种 10 倍杠杆 => 保证金 = |名义权重| / 10
- 全部策略总保证金 ≤ 30%  （总名义杠杆 ≤ 3x）
- 收益相关性 > 0.5 归为同一类；每类保证金 ≤ 10%
- 滚动 180 日、按*当前目标权重*合成的历史 95% 单日 VaR ≤ 3%
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


@dataclass
class MarginVaRLimits:
    instrument_leverage: float = 10.0
    max_total_margin: float = 0.30
    max_cluster_margin: float = 0.10
    corr_threshold: float = 0.50
    var_window: int = 180
    var_alpha: float = 0.95
    max_var: float = 0.03
    corr_window: int = 180
    min_history: int = 60


def margin_from_notional(notional_abs: pd.Series | float, leverage: float = 10.0) -> pd.Series | float:
    return notional_abs / leverage


def correlation_clusters(
    returns: pd.DataFrame,
    threshold: float = 0.5,
) -> Dict[str, int]:
    """corr > threshold 的品种并入同一连通分量。"""
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


def _cap_margins_down(
    notionals: pd.Series,
    clusters: Dict[str, int],
    limits: MarginVaRLimits,
) -> pd.Series:
    """仅向下压缩：分类 ≤10%，总计 ≤30%。"""
    w = notionals.astype(float).copy()
    lev = limits.instrument_leverage
    if float(w.abs().sum()) == 0.0:
        return w

    for cid in set(clusters.values()):
        members = [s for s, c in clusters.items() if c == cid and s in w.index]
        if not members:
            continue
        cm = float(w[members].abs().sum() / lev)
        if cm > limits.max_cluster_margin + 1e-15:
            w[members] *= limits.max_cluster_margin / cm

    total = float(w.abs().sum() / lev)
    if total > limits.max_total_margin + 1e-15:
        w *= limits.max_total_margin / total
    return w


def _fill_margin_budget(
    notionals: pd.Series,
    clusters: Dict[str, int],
    limits: MarginVaRLimits,
) -> pd.Series:
    """在不突破分类/总上限的前提下，尽量打满总保证金额度。"""
    w = _cap_margins_down(notionals, clusters, limits)
    lev = limits.instrument_leverage
    if float(w.abs().sum()) == 0.0:
        return w

    def cluster_margins(x: pd.Series) -> Dict[int, float]:
        out: Dict[int, float] = {}
        for cid in set(clusters.values()):
            members = [s for s, c in clusters.items() if c == cid and s in x.index]
            out[cid] = float(x[members].abs().sum() / lev) if members else 0.0
        return out

    for _ in range(8):
        total = float(w.abs().sum() / lev)
        if total <= 1e-15 or total >= limits.max_total_margin - 1e-12:
            break
        cms = cluster_margins(w)
        headrooms = [limits.max_cluster_margin / cm for cm in cms.values() if cm > 1e-15]
        if not headrooms:
            break
        up = min(limits.max_total_margin / total, min(headrooms))
        if up <= 1.0 + 1e-12:
            break
        w *= up
    return w


def _scale_to_margin_caps(
    notionals: pd.Series,
    clusters: Dict[str, int],
    limits: MarginVaRLimits,
    fill: bool = True,
) -> pd.Series:
    """先分类/总向下压缩；可选再把总保证金余量填满。"""
    w = _cap_margins_down(notionals, clusters, limits)
    if fill:
        w = _fill_margin_budget(w, clusters, limits)
    return w


def historical_var(returns: pd.Series, alpha: float = 0.95) -> float:
    """历史模拟法 VaR（正数表示亏损幅度）。"""
    r = returns.dropna()
    if len(r) < 30:
        return 0.0
    q = float(np.quantile(r.to_numpy(), 1.0 - alpha))
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

    VaR 只使用「当前目标权重 × 历史品种收益」的合成序列，
    绝不用旧仓位下的已实现组合收益（否则会把当前仓位错误压低）。
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
    cluster_margin_daily = np.zeros(n)

    equity = float(initial_capital)
    prev_w = pd.Series(0.0, index=symbols)

    for i in range(n):
        start = max(0, i - limits.corr_window)
        hist = rets.iloc[start:i]
        if len(hist) >= limits.min_history:
            clusters = correlation_clusters(hist[symbols], threshold=limits.corr_threshold)
        else:
            clusters = {s: j for j, s in enumerate(symbols)}
        cluster_hist.append(clusters)

        raw = dirs.iloc[i] * base_notional_per_name
        # 先按保证金约束分配（含余量填充）
        capped = _scale_to_margin_caps(raw, clusters, limits, fill=True)

        scale = 1.0
        v = 0.0
        look = rets.iloc[max(0, i - limits.var_window) : i]
        if len(look) >= limits.min_history:
            synth = look.mul(capped, axis=1).sum(axis=1)
            v = historical_var(synth, alpha=limits.var_alpha)
            if v > limits.max_var and v > 0:
                scale = limits.max_var / v
                capped = capped * scale
                # VaR 绑定后只向下压缩保证金，不再回补额度
                capped = _scale_to_margin_caps(capped, clusters, limits, fill=False)
                v2 = historical_var(look.mul(capped, axis=1).sum(axis=1), alpha=limits.var_alpha)
                if v2 > limits.max_var + 1e-12 and v2 > 0:
                    scale2 = limits.max_var / v2
                    capped = capped * scale2
                    scale *= scale2
                    v = limits.max_var
                else:
                    v = v2
        port_var[i] = float(min(v, limits.max_var)) if v > 0 else 0.0
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
