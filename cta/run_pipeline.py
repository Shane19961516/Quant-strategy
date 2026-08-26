# -*- coding: utf-8 -*-
"""CTA 流水线 CLI：参数寻优 + 保证金/VaR 仓位。

示例：
  python -m cta.run_pipeline --data-dir cta_data_akshare --plot
  python -m cta.run_pipeline --akshare --plot
"""

from __future__ import annotations

import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .metrics import format_summary
from .pipeline import PipelineResult, run_cta_pipeline
from .portfolio_risk import MarginVaRLimits


def _load_panels(args):
    if args.akshare:
        from .akshare_data import DEFAULT_AKSHARE_UNIVERSE, fetch_akshare_panels

        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or DEFAULT_AKSHARE_UNIVERSE
        panels, errors = fetch_akshare_panels(
            symbols=symbols,
            start_date=args.start,
            end_date=args.end,
            cache_dir=args.save_data_dir,
            force_refresh=args.refresh,
        )
        if errors:
            print("部分品种跳过：")
            for e in errors:
                print(" ", e)
        if not panels:
            raise RuntimeError("no panels from akshare")
        return panels
    from .data import load_panels

    if not args.data_dir:
        raise RuntimeError("请指定 --data-dir 或 --akshare")
    return load_panels(args.data_dir)


def _plot_total_nav(result: PipelineResult, path: str) -> None:
    """总资金 NAV 曲线 + 回撤。"""
    nav = result.equity
    dd = result.nav_drawdown.reindex(nav.index)
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]})
    axes[0].plot(nav.index, nav.values, color="#1f4e79", lw=1.6, label="Total Capital NAV")
    axes[0].set_title("Total Capital NAV")
    axes[0].set_ylabel("NAV (start=1)")
    axes[0].legend(loc="upper left", fontsize=9)
    axes[0].grid(True, alpha=0.3)
    axes[0].text(
        0.99,
        0.05,
        f"Total={result.summary['total_return']:.1%}  CAGR={result.summary['cagr']:.1%}  "
        f"Sharpe={result.summary['sharpe']:.2f}  MaxDD={result.summary['max_drawdown']:.1%}",
        transform=axes[0].transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8, "edgecolor": "#cccccc"},
    )

    axes[1].fill_between(dd.index, dd.values, 0, color="#c44e52", alpha=0.55)
    axes[1].set_ylabel("Drawdown")
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_strategy_nav(result: PipelineResult, path: str) -> None:
    """各策略 NAV + 总资金 NAV 对比，并标注最大回撤。"""
    colors = {
        "pairs": "#2a9d8f",
        "bollinger": "#e9c46a",
        "reversal": "#e76f51",
        "dual_ma": "#8ab17d",
        "donchian": "#f4a261",
        "tsmom": "#264653",
        "NAV": "#1f4e79",
    }
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.4]})

    for col in result.sleeve_nav.columns:
        axes[0].plot(
            result.sleeve_nav.index,
            result.sleeve_nav[col].values,
            color=colors.get(col, None),
            lw=1.2,
            alpha=0.9,
            label=f"{col} (MaxDD={result.sleeve_summary.loc[col, 'max_drawdown']:.1%})",
        )
    axes[0].plot(
        result.equity.index,
        result.equity.values,
        color=colors["NAV"],
        lw=2.0,
        label=f"Total NAV (MaxDD={result.summary['max_drawdown']:.1%})",
    )
    axes[0].set_title("Strategy NAV vs Total Capital NAV")
    axes[0].set_ylabel("NAV")
    axes[0].legend(loc="upper left", fontsize=8)
    axes[0].grid(True, alpha=0.3)

    for col in result.sleeve_drawdown.columns:
        axes[1].plot(
            result.sleeve_drawdown.index,
            result.sleeve_drawdown[col].values,
            color=colors.get(col, None),
            lw=1.0,
            alpha=0.8,
            label=col,
        )
    axes[1].plot(
        result.nav_drawdown.index,
        result.nav_drawdown.values,
        color=colors["NAV"],
        lw=1.5,
        label="Total",
    )
    axes[1].set_ylabel("Drawdown")
    axes[1].legend(loc="lower left", fontsize=8, ncol=4)
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_risk(result: PipelineResult, path: str) -> None:
    margin = result.diagnostics["total_margin"]
    var_ = result.diagnostics["port_var95"]
    fig, axes = plt.subplots(2, 1, figsize=(12, 5), sharex=True)
    axes[0].plot(margin.index, margin.values, color="#2a9d8f", lw=1.0)
    axes[0].axhline(0.30, color="#333", ls="--", lw=0.8, label="30% margin cap")
    axes[0].set_ylabel("Total margin")
    axes[0].legend(fontsize=8, loc="upper right")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(var_.index, var_.values, color="#e76f51", lw=1.0)
    axes[1].axhline(0.03, color="#333", ls="--", lw=0.8, label="3% VaR cap")
    axes[1].set_ylabel("95% VaR")
    axes[1].legend(fontsize=8, loc="upper right")
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CTA 参数寻优 + 仓位风控流水线")
    p.add_argument("--akshare", action="store_true")
    p.add_argument("--data-dir", default="cta_data_akshare")
    p.add_argument("--save-data-dir", default="cta_data_akshare")
    p.add_argument("--save-dir", default="cta_result")
    p.add_argument("--symbols", default="")
    p.add_argument("--start", default="20180101")
    p.add_argument("--end", default="20500101")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--train-end", default="2021-12-31")
    p.add_argument("--valid-end", default="2023-12-31")
    p.add_argument("--max-total-margin", type=float, default=0.30)
    p.add_argument("--max-cluster-margin", type=float, default=0.10)
    p.add_argument("--max-var", type=float, default=0.03)
    p.add_argument("--leverage", type=float, default=10.0)
    p.add_argument("--corr-threshold", type=float, default=0.5)
    p.add_argument("--var-window", type=int, default=180)
    p.add_argument("--cost-bps", type=float, default=0.5, help="单边手续费 bp（名义）")
    p.add_argument("--slip-bps", type=float, default=0.5, help="单边滑点 bp（名义）")
    p.add_argument("--plot", action="store_true", default=True)
    p.add_argument("--no-plot", action="store_true", help="不输出图")
    args = p.parse_args(argv)
    do_plot = args.plot and not args.no_plot

    panels = _load_panels(args)
    print(f"品种数={len(panels)}: {', '.join(sorted(panels))}")

    limits = MarginVaRLimits(
        instrument_leverage=args.leverage,
        max_total_margin=args.max_total_margin,
        max_cluster_margin=args.max_cluster_margin,
        corr_threshold=args.corr_threshold,
        var_window=args.var_window,
        max_var=args.max_var,
        corr_window=args.var_window,
    )

    print("开始参数寻优（套利/反转：跨配对或跨品种泛化 + 验证集）...")
    result = run_cta_pipeline(
        panels,
        train_end=args.train_end,
        valid_end=args.valid_end,
        limits=limits,
        cost_bps=args.cost_bps,
        slip_bps=args.slip_bps,
    )

    print("\n=== 各策略最优参数 ===")
    for m, opt in result.optim.items():
        cm = opt.chosen_metrics
        print(
            f"{m}: {opt.best.label()} | "
            f"train_sharpe={cm.get('train_sharpe', float('nan')):.3f} "
            f"valid_sharpe={cm.get('valid_sharpe', float('nan')):.3f} "
            f"local_std={cm.get('local_sharpe_std', float('nan')):.3f} "
            f"score={cm.get('score', float('nan')):.3f}"
        )

    print("\n=== 各策略绩效（含止损后 MaxDD / 交易胜率 / 赔率）===")
    cols = [
        "total_return",
        "cagr",
        "sharpe",
        "max_drawdown",
        "trade_win_rate",
        "trade_payoff",
        "n_trades",
        "signal_weight",
    ]
    show = result.sleeve_summary[cols].copy()
    for c in ["total_return", "cagr", "max_drawdown", "trade_win_rate"]:
        show[c] = show[c].map(lambda x: f"{x:.2%}")
    show["sharpe"] = result.sleeve_summary["sharpe"].map(lambda x: f"{x:.3f}")
    show["trade_payoff"] = result.sleeve_summary["trade_payoff"].map(lambda x: f"{x:.2f}")
    show["n_trades"] = result.sleeve_summary["n_trades"].map(lambda x: f"{int(x)}")
    show["signal_weight"] = result.sleeve_summary["signal_weight"].map(lambda x: f"{x:.2%}")
    print(show.to_string())

    print("\n=== 各策略最大回撤 ===")
    for m, row in result.sleeve_summary.iterrows():
        print(
            f"  {m}: MaxDD={row['max_drawdown']:.2%} | "
            f"交易胜率={row['trade_win_rate']:.1%} | "
            f"赔率={row['trade_payoff']:.2f} | "
            f"交易数={int(row['n_trades'])}"
        )
    print(f"  TOTAL资金组合: MaxDD = {result.summary['max_drawdown']:.2%}")

    print("\n=== 总资金组合（NAV）===")
    print(format_summary(result.summary))
    print(
        f"  平均总保证金: {result.summary.get('avg_total_margin', float('nan')):.2%} | "
        f"最大总保证金: {result.summary['max_total_margin']:.2%} "
        f"(ok={bool(result.summary['margin_ok'])})"
    )
    print(
        f"  最大分类保证金: {result.summary['max_cluster_margin']:.2%} "
        f"(ok={bool(result.summary['cluster_margin_ok'])})"
    )
    print(
        f"  最大95%VaR: {result.summary['max_port_var95']:.2%} "
        f"(ok={bool(result.summary['var_ok'])})"
    )
    print(
        f"  最大名义杠杆: {result.summary['max_gross_notional']:.2f}x | "
        f"平均名义杠杆: {result.summary['avg_gross_notional']:.2f}x"
    )
    print(
        f"  期末NAV: {float(result.equity.iloc[-1]):.4f} "
        f"(起点=1.0000)"
    )

    os.makedirs(args.save_dir, exist_ok=True)
    frames = result.to_frames()
    for name, df in frames.items():
        path = os.path.join(args.save_dir, f"{name}.csv")
        df.to_csv(path)

    if do_plot:
        nav_path = os.path.join(args.save_dir, "nav_total.png")
        strat_path = os.path.join(args.save_dir, "nav_strategies.png")
        risk_path = os.path.join(args.save_dir, "risk_monitors.png")
        _plot_total_nav(result, nav_path)
        _plot_strategy_nav(result, strat_path)
        _plot_risk(result, risk_path)
        print(f"\n总资金NAV曲线: {nav_path}")
        print(f"分策略NAV曲线: {strat_path}")
        print(f"风控监控图: {risk_path}")

    print(f"\n结果目录: {args.save_dir}/")
    print(f"NAV数据: {args.save_dir}/nav_total.csv , {args.save_dir}/sleeve_nav.csv")
    ok = bool(
        result.summary["margin_ok"]
        and result.summary["cluster_margin_ok"]
        and result.summary["var_ok"]
    )
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
