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
from .pipeline import run_cta_pipeline
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


def _plot(equity: pd.Series, margin: pd.Series, var_: pd.Series, path: str) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True, gridspec_kw={"height_ratios": [3, 1.2, 1.2]})
    axes[0].plot(equity.index, equity.values, color="#1f4e79", lw=1.3)
    axes[0].set_title("CTA Pipeline Equity")
    axes[0].set_ylabel("Equity")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(margin.index, margin.values, color="#2a9d8f", lw=1.0)
    axes[1].axhline(0.30, color="#333", ls="--", lw=0.8, label="30% margin cap")
    axes[1].set_ylabel("Total margin")
    axes[1].legend(fontsize=8, loc="upper right")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(var_.index, var_.values, color="#e76f51", lw=1.0)
    axes[2].axhline(0.03, color="#333", ls="--", lw=0.8, label="3% VaR cap")
    axes[2].set_ylabel("95% VaR")
    axes[2].legend(fontsize=8, loc="upper right")
    axes[2].grid(True, alpha=0.3)
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
    p.add_argument("--plot", action="store_true")
    args = p.parse_args(argv)

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

    print("开始参数寻优（跨品种泛化 + 局部夏普稳定 + 验证集）...")
    result = run_cta_pipeline(
        panels,
        train_end=args.train_end,
        valid_end=args.valid_end,
        limits=limits,
    )

    print("\n=== 各策略最优参数（单位名义、等权品种）===")
    for m, opt in result.optim.items():
        cm = opt.chosen_metrics
        print(
            f"{m}: {opt.best.label()} | "
            f"train_sharpe={cm.get('train_sharpe', float('nan')):.3f} "
            f"valid_sharpe={cm.get('valid_sharpe', float('nan')):.3f} "
            f"local_std={cm.get('local_sharpe_std', float('nan')):.3f} "
            f"pos_frac={cm.get('pos_asset_frac', float('nan')):.2f} "
            f"score={cm.get('score', float('nan')):.3f}"
        )

    print("\n=== 单位仓位全样本绩效 ===")
    show = result.method_unit_summary[["cagr", "ann_vol", "sharpe", "max_drawdown", "total_return"]].copy()
    for c in ["cagr", "ann_vol", "max_drawdown", "total_return"]:
        show[c] = show[c].map(lambda x: f"{x:.2%}")
    show["sharpe"] = result.method_unit_summary["sharpe"].map(lambda x: f"{x:.3f}")
    print(show.to_string())

    print("\n=== 组合（保证金+相关性+VaR 约束后）===")
    print(format_summary(result.summary))
    print(
        f"  最大总保证金: {result.summary['max_total_margin']:.2%} "
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

    os.makedirs(args.save_dir, exist_ok=True)
    frames = result.to_frames()
    for name, df in frames.items():
        path = os.path.join(args.save_dir, f"{name}.csv")
        df.to_csv(path)

    if args.plot:
        plot_path = os.path.join(args.save_dir, "pipeline_equity.png")
        _plot(result.equity, result.diagnostics["total_margin"], result.diagnostics["port_var95"], plot_path)
        print(f"图已保存: {plot_path}")

    print(f"\n结果目录: {args.save_dir}/")
    ok = bool(
        result.summary["margin_ok"]
        and result.summary["cluster_margin_ok"]
        and result.summary["var_ok"]
    )
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
