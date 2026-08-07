# -*- coding: utf-8 -*-
"""完整 CTA 流水线：参数寻优 → 信号 → 保证金/相关性/VaR 仓位。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .metrics import performance_summary
from .optimize import OptimizeResult, optimize_all_methods, unit_strategy_returns
from .portfolio_risk import MarginVaRLimits, apply_margin_var_controls
from .signals import donchian_breakout_signal, dual_ma_signal, ts_momentum_signal


def _signal(method: str, ohlc: pd.DataFrame, params: Dict) -> pd.Series:
    c, h, l = ohlc["close"], ohlc["high"], ohlc["low"]
    if method == "dual_ma":
        return dual_ma_signal(c, fast=int(params["fast"]), slow=int(params["slow"]))
    if method == "donchian":
        return donchian_breakout_signal(h, l, c, entry=int(params["entry"]), exit_=int(params["exit"]))
    if method == "tsmom":
        return ts_momentum_signal(c, lookback=int(params["lookback"]), skip=int(params.get("skip", 1)))
    raise ValueError(method)


@dataclass
class PipelineResult:
    equity: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    signals: pd.DataFrame
    method_signals: Dict[str, pd.DataFrame]
    summary: Dict[str, float]
    optim: Dict[str, OptimizeResult]
    diagnostics: Dict[str, pd.Series] = field(default_factory=dict)
    clusters: pd.DataFrame = field(default_factory=pd.DataFrame)
    method_unit_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    sleeve_summary: pd.DataFrame = field(default_factory=pd.DataFrame)

    def to_frames(self) -> Dict[str, pd.DataFrame]:
        frames = {
            "equity": self.equity.to_frame("equity"),
            "returns": self.returns.to_frame("ret"),
            "weights": self.weights,
            "signals": self.signals,
            "summary": pd.DataFrame([self.summary]),
            "clusters": self.clusters,
            "method_unit_summary": self.method_unit_summary,
            "sleeve_summary": self.sleeve_summary,
        }
        for name, series in self.diagnostics.items():
            frames[name] = series.to_frame(name)
        for m, sig in self.method_signals.items():
            frames[f"signal_{m}"] = sig
        for m, opt in self.optim.items():
            frames[f"param_search_{m}"] = opt.score_table
            frames[f"best_param_{m}"] = pd.DataFrame(
                [{"label": opt.best.label(), **opt.best.as_dict(), **opt.chosen_metrics}]
            )
        return frames


def build_method_signals(
    panels: Dict[str, pd.DataFrame],
    method: str,
    params: Dict,
) -> pd.DataFrame:
    data = {}
    for sym, ohlc in panels.items():
        data[sym] = _signal(method, ohlc, params)
    return pd.DataFrame(data).sort_index().fillna(0.0)


def run_cta_pipeline(
    panels: Dict[str, pd.DataFrame],
    methods: Optional[List[str]] = None,
    train_end: str = "2021-12-31",
    valid_end: str = "2023-12-31",
    limits: Optional[MarginVaRLimits] = None,
    cost_bps: float = 0.5,
    slip_bps: float = 0.5,
    precomputed_optim: Optional[Dict[str, OptimizeResult]] = None,
) -> PipelineResult:
    """主流程。"""
    methods = methods or ["dual_ma", "donchian", "tsmom"]
    limits = limits or MarginVaRLimits()

    optim = precomputed_optim or optimize_all_methods(
        panels,
        methods=methods,
        train_end=train_end,
        valid_end=valid_end,
        cost_bps=cost_bps,
    )

    closes = pd.DataFrame({s: panels[s]["close"] for s in panels}).sort_index().ffill()
    asset_ret = closes.pct_change()
    # 主力连续换月缺口：极端跳空不视为可交易收益，置 0 以免扭曲趋势信号与 PnL
    jump = asset_ret.abs() > 0.08
    asset_ret = asset_ret.mask(jump, 0.0).fillna(0.0)

    method_signals: Dict[str, pd.DataFrame] = {}
    unit_rows = []
    for m in methods:
        params = optim[m].best.as_dict()
        sig = build_method_signals(panels, m, params)
        method_signals[m] = sig
        u = unit_strategy_returns(panels, m, params, cost_bps=cost_bps)
        port = u.mean(axis=1)
        summ = performance_summary((1 + port).cumprod(), port)
        unit_rows.append(
            {
                "method": m,
                "params": optim[m].best.label(),
                **summ,
                **{
                    f"opt_{k}": optim[m].chosen_metrics.get(k)
                    for k in ("train_sharpe", "valid_sharpe", "local_sharpe_std", "pos_asset_frac", "score")
                },
            }
        )
    method_unit_summary = pd.DataFrame(unit_rows).set_index("method")

    sharpe_w = {}
    for m in methods:
        vs = float(optim[m].chosen_metrics.get("valid_sharpe", 0.0) or 0.0)
        # 按验证夏普平方加权，弱策略权重迅速衰减，避免稀释
        sharpe_w[m] = max(vs, 0.0) ** 2
    wsum = sum(sharpe_w.values())
    if wsum <= 1e-12:
        sharpe_w = {m: 1.0 / len(methods) for m in methods}
        wsum = 1.0

    # 各策略单独打满风控预算，便于核对收益是否合理
    sleeve_rows = []
    for m in methods:
        sig = method_signals[m].reindex(columns=asset_ret.columns).fillna(0.0)
        sw, snet, seq, sdiag, _ = apply_margin_var_controls(
            sig,
            asset_ret.reindex(sig.index).fillna(0.0),
            limits=limits,
            base_notional_per_name=3.0 / max(len(sig.columns), 1),
            cost_bps=cost_bps,
            slip_bps=slip_bps,
        )
        ssum = performance_summary(seq, snet)
        sleeve_rows.append(
            {
                "method": m,
                "params": optim[m].best.label(),
                "signal_weight": sharpe_w[m] / wsum,
                **ssum,
                "avg_gross": float(sw.abs().sum(axis=1).mean()),
                "avg_margin": float(sdiag["total_margin"].mean()),
                "max_margin": float(sdiag["total_margin"].max()),
                "max_var": float(sdiag["port_var95"].max()),
            }
        )
    sleeve_summary = pd.DataFrame(sleeve_rows).set_index("method")

    combined = None
    for m, sig in method_signals.items():
        part = sig * (sharpe_w[m] / wsum)
        combined = part if combined is None else combined.add(part, fill_value=0.0)
    combined = combined.clip(-1.0, 1.0).sort_index().fillna(0.0)
    asset_ret = asset_ret.reindex(combined.index).fillna(0.0)
    combined = combined.reindex(columns=asset_ret.columns).fillna(0.0)

    n_assets = max(len(combined.columns), 1)
    base = 3.0 / n_assets
    weights, net, equity, diagnostics, clusters = apply_margin_var_controls(
        combined,
        asset_ret,
        limits=limits,
        base_notional_per_name=base,
        cost_bps=cost_bps,
        slip_bps=slip_bps,
    )

    summary = performance_summary(equity, net)
    summary["max_daily_loss"] = float(net.min()) if len(net) else 0.0
    summary["max_total_margin"] = float(diagnostics["total_margin"].max())
    summary["avg_total_margin"] = float(diagnostics["total_margin"].mean())
    summary["max_cluster_margin"] = float(diagnostics["cluster_margin_max"].max())
    summary["max_port_var95"] = float(diagnostics["port_var95"].max())
    summary["var_ok"] = float(summary["max_port_var95"] <= limits.max_var + 1e-9)
    summary["margin_ok"] = float(summary["max_total_margin"] <= limits.max_total_margin + 1e-9)
    summary["cluster_margin_ok"] = float(summary["max_cluster_margin"] <= limits.max_cluster_margin + 1e-9)
    summary["max_gross_notional"] = float(weights.abs().sum(axis=1).max())
    summary["avg_gross_notional"] = float(weights.abs().sum(axis=1).mean())

    return PipelineResult(
        equity=equity,
        returns=net,
        weights=weights,
        signals=combined,
        method_signals=method_signals,
        summary=summary,
        optim=optim,
        diagnostics=diagnostics,
        clusters=clusters,
        method_unit_summary=method_unit_summary,
        sleeve_summary=sleeve_summary,
    )
