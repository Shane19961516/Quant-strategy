# -*- coding: utf-8 -*-
"""完整 CTA 流水线：参数寻优 → 信号 → 保证金/相关性/VaR 仓位。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .metrics import performance_summary
from .optimize import (
    DEFAULT_METHODS,
    OptimizeResult,
    build_method_signal_frame,
    build_stopped_signal,
    optimize_all_methods,
    unit_strategy_returns,
)
from .pairs import apply_pairs_book_controls
from .portfolio_risk import MarginVaRLimits, apply_margin_var_controls
from .stops import aggregate_trade_stats


def _signal(method: str, ohlc: pd.DataFrame, params: Dict) -> pd.Series:
    """单品种信号（pairs 请用 build_method_signal_frame）。"""
    return build_stopped_signal(method, ohlc, params)


def _drawdown(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return (equity / peak - 1.0).rename("drawdown")


@dataclass
class PipelineResult:
    equity: pd.Series  # 总资金 NAV
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
    sleeve_nav: pd.DataFrame = field(default_factory=pd.DataFrame)  # 各策略 NAV
    sleeve_drawdown: pd.DataFrame = field(default_factory=pd.DataFrame)
    nav_drawdown: pd.Series = field(default_factory=pd.Series)

    def to_frames(self) -> Dict[str, pd.DataFrame]:
        nav = self.equity.rename("NAV").to_frame()
        nav["drawdown"] = self.nav_drawdown.reindex(nav.index)
        frames = {
            "nav_total": nav,
            "equity": self.equity.to_frame("equity"),
            "returns": self.returns.to_frame("ret"),
            "weights": self.weights,
            "signals": self.signals,
            "summary": pd.DataFrame([self.summary]),
            "clusters": self.clusters,
            "method_unit_summary": self.method_unit_summary,
            "sleeve_summary": self.sleeve_summary,
            "sleeve_nav": self.sleeve_nav,
            "sleeve_drawdown": self.sleeve_drawdown,
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
    sig, _ = build_method_signal_frame(panels, method, params)
    return sig


def build_method_signals_and_exits(
    panels: Dict[str, pd.DataFrame],
    method: str,
    params: Dict,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    return build_method_signal_frame(panels, method, params)


def _adjust_returns_for_stops(
    asset_ret: pd.DataFrame,
    signal: pd.DataFrame,
    stop_exit_ret: pd.DataFrame,
) -> pd.DataFrame:
    """止损日用止损价收益替换，使 weight_{t-1} * ret_t 等于止损实现盈亏。"""
    traded = signal.shift(1).fillna(0.0)
    adj = asset_ret.reindex_like(signal).fillna(0.0).copy()
    stop_exit_ret = stop_exit_ret.reindex_like(signal)
    for col in adj.columns:
        hit = stop_exit_ret[col].notna() & (traded[col] != 0)
        # exit_ret 已是持仓方向上的已实现收益；换算成价格收益
        # traded * ret_adj = exit_ret  => ret_adj = exit_ret / traded
        ret_adj = stop_exit_ret[col] / traded[col].replace(0, np.nan)
        adj.loc[hit, col] = ret_adj.loc[hit]
    return adj.fillna(0.0)


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
    methods = methods or list(DEFAULT_METHODS)
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
    jump = asset_ret.abs() > 0.08
    asset_ret = asset_ret.mask(jump, 0.0).fillna(0.0)

    method_signals: Dict[str, pd.DataFrame] = {}
    method_exits: Dict[str, pd.DataFrame] = {}
    unit_rows = []
    for m in methods:
        params = optim[m].best.as_dict()
        sig, exits = build_method_signals_and_exits(panels, m, params)
        method_signals[m] = sig
        method_exits[m] = exits
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

    # 验证集有效且训练夏普达标的 sleeve 才进组合（避免验证集运气仓）
    sharpe_w = {}
    for m in methods:
        ts = float(optim[m].chosen_metrics.get("train_sharpe", 0.0) or 0.0)
        vs = float(optim[m].chosen_metrics.get("valid_sharpe", 0.0) or 0.0)
        vt = float(optim[m].chosen_metrics.get("valid_total", 0.0) or 0.0)
        ok = (ts >= 0.15) and (vs > 0.0) and (vt > 0.0)
        sharpe_w[m] = (max(vs, 0.0) ** 2) if ok else 0.0
    wsum = sum(sharpe_w.values())
    if wsum <= 1e-12:
        # 兜底：在训练夏普为正的方法里等权；再不行全体等权
        pos = {m: 1.0 for m in methods if float(optim[m].chosen_metrics.get("train_sharpe", 0) or 0) > 0}
        if pos:
            sharpe_w = {m: pos.get(m, 0.0) for m in methods}
        else:
            sharpe_w = {m: 1.0 / len(methods) for m in methods}
        wsum = sum(sharpe_w.values())

    sleeve_rows = []
    sleeve_nav_cols = {}
    sleeve_dd_cols = {}
    sleeve_nets = {}
    sleeve_weights = {}
    sleeve_diags = {}
    for m in methods:
        params = optim[m].best.as_dict()
        sig = method_signals[m].reindex(columns=asset_ret.columns).fillna(0.0)
        exits = method_exits[m].reindex(columns=asset_ret.columns)
        adj_ret = _adjust_returns_for_stops(asset_ret, sig, exits)

        per_sym = {s: sig[s] for s in sig.columns if s in panels}
        tstats = aggregate_trade_stats(panels, per_sym)

        w_m = sharpe_w[m] / wsum
        lim_m = MarginVaRLimits(
            instrument_leverage=limits.instrument_leverage,
            max_total_margin=max(limits.max_total_margin * w_m, 1e-6),
            max_cluster_margin=min(limits.max_cluster_margin, max(limits.max_total_margin * w_m, 1e-6)),
            corr_threshold=limits.corr_threshold,
            var_window=limits.var_window,
            var_alpha=limits.var_alpha,
            max_var=max(limits.max_var * max(w_m, 0.25), 0.005),
            corr_window=limits.corr_window,
            min_history=limits.min_history,
        )

        if m == "pairs":
            sw_full, snet_full, seq_full, sdiag_full = apply_pairs_book_controls(
                panels, params, adj_ret, limits=limits, cost_bps=cost_bps, slip_bps=slip_bps
            )
            if w_m <= 1e-12:
                sw = sw_full * 0.0
                snet = pd.Series(0.0, index=adj_ret.index, name="ret")
                seq = pd.Series(1.0, index=adj_ret.index, name="equity")
                sdiag = {k: v * 0.0 for k, v in sdiag_full.items()}
            else:
                sw, snet, seq, sdiag = apply_pairs_book_controls(
                    panels, params, adj_ret, limits=lim_m, cost_bps=cost_bps, slip_bps=slip_bps
                )
        else:
            sw_full, snet_full, seq_full, sdiag_full, _ = apply_margin_var_controls(
                sig,
                adj_ret.reindex(sig.index).fillna(0.0),
                limits=limits,
                base_notional_per_name=3.0 / max(len(sig.columns), 1),
                cost_bps=cost_bps,
                slip_bps=slip_bps,
            )
            if w_m <= 1e-12:
                sw = sw_full * 0.0
                snet = pd.Series(0.0, index=adj_ret.index, name="ret")
                seq = pd.Series(1.0, index=adj_ret.index, name="equity")
                sdiag = {k: v * 0.0 for k, v in sdiag_full.items()}
            else:
                sw, snet, seq, sdiag, _ = apply_margin_var_controls(
                    sig,
                    adj_ret.reindex(sig.index).fillna(0.0),
                    limits=lim_m,
                    base_notional_per_name=(3.0 * w_m) / max(len(sig.columns), 1),
                    cost_bps=cost_bps,
                    slip_bps=slip_bps,
                )
        ssum = performance_summary(seq_full, snet_full)
        sdd = _drawdown(seq_full)
        sleeve_nav_cols[m] = seq_full.rename(m)
        sleeve_dd_cols[m] = sdd.rename(m)
        sleeve_nets[m] = snet.reindex(asset_ret.index).fillna(0.0)
        sleeve_weights[m] = sw
        sleeve_diags[m] = sdiag
        sleeve_rows.append(
            {
                "method": m,
                "params": optim[m].best.label(),
                "signal_weight": w_m,
                **ssum,
                "max_drawdown": float(sdd.min()),
                "n_trades": tstats["n_trades"],
                "trade_win_rate": tstats["trade_win_rate"],
                "trade_payoff": tstats["trade_payoff"],
                "avg_win": tstats["avg_win"],
                "avg_loss": tstats["avg_loss"],
                "expectancy": tstats["expectancy"],
                "avg_gross": float(sw_full.abs().sum(axis=1).mean()),
                "avg_margin": float(sdiag_full["total_margin"].mean()),
                "max_margin": float(sdiag_full["total_margin"].max()),
                "max_var": float(sdiag_full["port_var95"].max()),
            }
        )
    sleeve_summary = pd.DataFrame(sleeve_rows).set_index("method")
    sleeve_nav = pd.DataFrame(sleeve_nav_cols).sort_index()
    sleeve_drawdown = pd.DataFrame(sleeve_dd_cols).sort_index()

    # 总资金 NAV = 各策略加权 sleeve 收益之和（止损盈亏已在 sleeve 内正确结算）
    net = sum(sleeve_nets.values())
    equity = (1.0 + net).cumprod()
    equity = equity.rename("NAV")
    # 合成权重（名义）
    weights = None
    for m, sw in sleeve_weights.items():
        weights = sw.copy() if weights is None else weights.add(sw, fill_value=0.0)
    weights = weights.fillna(0.0)
    # 诊断：保证金/VaR 取加权 sleeve 近似
    diagnostics = {
        "total_margin": sum(
            (sleeve_diags[m]["total_margin"] * (sharpe_w[m] / wsum)) for m in methods
        ).rename("total_margin"),
        "port_var95": sum(
            (sleeve_diags[m]["port_var95"] * (sharpe_w[m] / wsum)) for m in methods
        ).rename("port_var95"),
        "var_scale": pd.Series(1.0, index=equity.index, name="var_scale"),
        "cluster_margin_max": sum(
            (sleeve_diags[m]["cluster_margin_max"] * (sharpe_w[m] / wsum)) for m in methods
        ).rename("cluster_margin_max"),
    }
    # 对总组合再施加一次总保证金/VaR 硬约束缩放（保持口径）
    # 若加权后总保证金超 30%，等比压缩净值收益与权重
    tm = weights.abs().sum(axis=1) / limits.instrument_leverage
    overflow = tm > limits.max_total_margin + 1e-12
    if overflow.any():
        scale = (limits.max_total_margin / tm.where(tm > 0, np.nan)).clip(upper=1.0).fillna(1.0)
        weights = weights.mul(scale, axis=0)
        net = net * scale.shift(1).fillna(1.0)
        equity = (1.0 + net).cumprod().rename("NAV")
        diagnostics["total_margin"] = (weights.abs().sum(axis=1) / limits.instrument_leverage).rename("total_margin")

    # 组合信号仅用于输出
    combined = None
    for m, sig in method_signals.items():
        part = sig * (sharpe_w[m] / wsum)
        combined = part if combined is None else combined.add(part, fill_value=0.0)
    combined = combined.clip(-1.0, 1.0).sort_index().fillna(0.0)
    combined = combined.reindex(index=equity.index, columns=asset_ret.columns).fillna(0.0)
    clusters = pd.DataFrame(index=equity.index)
    equity = equity.rename("NAV")
    nav_dd = _drawdown(equity)

    summary = performance_summary(equity, net)
    summary["max_daily_loss"] = float(net.min()) if len(net) else 0.0
    summary["max_drawdown"] = float(nav_dd.min())
    summary["max_total_margin"] = float(diagnostics["total_margin"].max())
    summary["avg_total_margin"] = float(diagnostics["total_margin"].mean())
    summary["max_cluster_margin"] = float(diagnostics["cluster_margin_max"].max())
    summary["max_port_var95"] = float(diagnostics["port_var95"].max())
    summary["var_ok"] = float(summary["max_port_var95"] <= limits.max_var + 1e-9)
    summary["margin_ok"] = float(summary["max_total_margin"] <= limits.max_total_margin + 1e-9)
    summary["cluster_margin_ok"] = float(summary["max_cluster_margin"] <= limits.max_cluster_margin + 1e-9)
    summary["max_gross_notional"] = float(weights.abs().sum(axis=1).max())
    summary["avg_gross_notional"] = float(weights.abs().sum(axis=1).mean())
    # 各策略最大回撤写入总摘要，便于一眼查看
    for m in methods:
        summary[f"max_dd_{m}"] = float(sleeve_summary.loc[m, "max_drawdown"])

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
        sleeve_nav=sleeve_nav,
        sleeve_drawdown=sleeve_drawdown,
        nav_drawdown=nav_dd,
    )
