# -*- coding: utf-8 -*-
"""CTA 策略命令行入口。

示例：
  python -m cta.run_demo
  python -m cta.run_demo --method donchian --plot
  python -m cta.run_demo --compare --save-dir cta_result
"""

from __future__ import annotations

import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .backtest import CTABacktester, run_strategy_comparison
from .data import generate_synthetic_futures, load_panels, save_panels
from .metrics import format_summary


def _plot_equity(equity: pd.Series, out_path: str, title: str) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    axes[0].plot(equity.index, equity.values, color="#1f4e79", lw=1.4)
    axes[0].set_title(title)
    axes[0].set_ylabel("Equity")
    axes[0].grid(True, alpha=0.3)

    dd = equity / equity.cummax() - 1.0
    axes[1].fill_between(dd.index, dd.values, 0, color="#c44e52", alpha=0.5)
    axes[1].set_ylabel("Drawdown")
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="期货量化 CTA 策略回测")
    parser.add_argument(
        "--method",
        default="combo",
        choices=["dual_ma", "donchian", "tsmom", "combo"],
        help="信号类型",
    )
    parser.add_argument("--data-dir", default="", help="已有期货 CSV 目录；为空则生成合成数据")
    parser.add_argument("--save-data-dir", default="cta_data", help="合成数据保存目录")
    parser.add_argument("--save-dir", default="cta_result", help="回测结果输出目录")
    parser.add_argument("--target-vol", type=float, default=0.10, help="单品种目标波动")
    parser.add_argument("--port-vol", type=float, default=0.12, help="组合目标波动")
    parser.add_argument("--cost-bps", type=float, default=2.0, help="单边手续费 bp")
    parser.add_argument("--slip-bps", type=float, default=1.0, help="滑点 bp")
    parser.add_argument("--seed", type=int, default=42, help="合成数据随机种子")
    parser.add_argument("--compare", action="store_true", help="对比全部信号方法")
    parser.add_argument("--plot", action="store_true", help="保存净值/回撤图")
    args = parser.parse_args(argv)

    if args.data_dir and os.path.isdir(args.data_dir):
        panels = load_panels(args.data_dir)
        print(f"已加载 {len(panels)} 个品种: {', '.join(sorted(panels))}")
    else:
        panels = generate_synthetic_futures(seed=args.seed)
        save_panels(panels, args.save_data_dir)
        print(f"已生成合成期货数据 -> {args.save_data_dir}/ ({len(panels)} 品种)")

    os.makedirs(args.save_dir, exist_ok=True)

    if args.compare:
        cmp = run_strategy_comparison(
            panels,
            target_vol=args.target_vol,
            portfolio_target_vol=args.port_vol,
            cost_bps=args.cost_bps,
            slip_bps=args.slip_bps,
        )
        out_csv = os.path.join(args.save_dir, "strategy_comparison.csv")
        cmp.to_csv(out_csv, float_format="%.6f")
        print("\n策略对比：")
        show = cmp[["cagr", "ann_vol", "sharpe", "max_drawdown", "calmar"]].copy()
        for c in ["cagr", "ann_vol", "max_drawdown"]:
            show[c] = show[c].map(lambda x: f"{x:.2%}")
        for c in ["sharpe", "calmar"]:
            show[c] = show[c].map(lambda x: f"{x:.3f}")
        print(show.to_string())
        print(f"\n对比表已保存: {out_csv}")

    bt = CTABacktester(
        panels,
        method=args.method,
        target_vol=args.target_vol,
        portfolio_target_vol=args.port_vol,
        cost_bps=args.cost_bps,
        slip_bps=args.slip_bps,
    )
    result = bt.run()
    print(f"\n方法 = {args.method}")
    print(format_summary(result.summary))
    print("\n品种累计贡献（毛收益）：")
    print(result.contrib.map(lambda x: f"{x:.2%}").to_string())

    frames = result.to_frames()
    frames["equity"].to_csv(os.path.join(args.save_dir, "equity.csv"))
    frames["weights"].to_csv(os.path.join(args.save_dir, "weights.csv"))
    frames["signals"].to_csv(os.path.join(args.save_dir, "signals.csv"))
    frames["summary"].to_csv(os.path.join(args.save_dir, "summary.csv"), index=False)
    frames["contrib"].to_csv(os.path.join(args.save_dir, "contrib.csv"))

    if args.plot:
        plot_path = os.path.join(args.save_dir, f"equity_{args.method}.png")
        _plot_equity(result.equity, plot_path, f"CTA Equity Curve ({args.method})")
        print(f"净值图已保存: {plot_path}")

    print(f"\n结果目录: {args.save_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
