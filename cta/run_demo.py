# -*- coding: utf-8 -*-
"""CTA 策略命令行入口。

示例：
  python -m cta.run_demo --akshare --plot
  python -m cta.run_demo --akshare --compare --plot
  python -m cta.run_demo --data-dir cta_data_akshare --method combo --plot
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
from .position_manager import RiskLimits


def _plot_equity(equity: pd.Series, out_path: str, title: str) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    axes[0].plot(equity.index, equity.values, color="#1f4e79", lw=1.4)
    axes[0].set_title(title)
    axes[0].set_ylabel("Equity")
    axes[0].grid(True, alpha=0.3)

    dd = equity / equity.cummax() - 1.0
    axes[1].fill_between(dd.index, dd.values, 0, color="#c44e52", alpha=0.5)
    axes[1].axhline(-0.08, color="#333333", ls="--", lw=0.9, label="DD limit 8%")
    axes[1].set_ylabel("Drawdown")
    axes[1].legend(loc="lower left", fontsize=8)
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _load_data(args) -> dict:
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
            print("部分品种拉取失败/跳过：")
            for e in errors:
                print(f"  - {e}")
        if not panels:
            raise RuntimeError("akshare 未成功加载任何品种")
        print(f"akshare 已加载 {len(panels)} 个品种: {', '.join(sorted(panels))}")
        return panels

    if args.data_dir and os.path.isdir(args.data_dir):
        panels = load_panels(args.data_dir)
        print(f"已加载 {len(panels)} 个品种: {', '.join(sorted(panels))}")
        return panels

    panels = generate_synthetic_futures(seed=args.seed)
    save_panels(panels, args.save_data_dir)
    print(f"已生成合成期货数据 -> {args.save_data_dir}/ ({len(panels)} 品种)")
    return panels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="期货量化 CTA 策略回测")
    parser.add_argument(
        "--method",
        default="combo",
        choices=["dual_ma", "donchian", "tsmom", "combo"],
        help="信号类型",
    )
    parser.add_argument("--akshare", action="store_true", help="使用 akshare 拉取主力连续真实数据")
    parser.add_argument("--symbols", default="", help="品种代码逗号分隔，如 RB,CU,AU,IF")
    parser.add_argument("--start", default="20180101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default="20500101", help="结束日期 YYYYMMDD")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存强制刷新 akshare 数据")
    parser.add_argument("--data-dir", default="", help="已有期货 CSV 目录；为空则合成或 akshare")
    parser.add_argument("--save-data-dir", default="cta_data_akshare", help="数据缓存/保存目录")
    parser.add_argument("--save-dir", default="cta_result", help="回测结果输出目录")
    parser.add_argument("--target-vol", type=float, default=0.04, help="单品种目标波动")
    parser.add_argument("--port-vol", type=float, default=0.05, help="组合目标波动")
    parser.add_argument("--max-leverage", type=float, default=1.0, help="组合绝对权重上限")
    parser.add_argument("--max-daily-loss", type=float, default=0.03, help="单日最大亏损")
    parser.add_argument("--max-dd", type=float, default=0.08, help="策略最大回撤硬限制")
    parser.add_argument("--dd-scale-start", type=float, default=0.04, help="回撤降仓起始阈值")
    parser.add_argument("--cost-bps", type=float, default=2.0, help="单边手续费 bp")
    parser.add_argument("--slip-bps", type=float, default=1.0, help="滑点 bp")
    parser.add_argument("--seed", type=int, default=42, help="合成数据随机种子")
    parser.add_argument("--no-pm", action="store_true", help="关闭仓位管理/硬止损")
    parser.add_argument("--compare", action="store_true", help="对比全部信号方法")
    parser.add_argument("--plot", action="store_true", help="保存净值/回撤图")
    args = parser.parse_args(argv)

    panels = _load_data(args)
    os.makedirs(args.save_dir, exist_ok=True)

    limits = RiskLimits(
        max_daily_loss=args.max_daily_loss,
        max_drawdown=args.max_dd,
        dd_scale_start=args.dd_scale_start,
    )
    bt_kwargs = dict(
        target_vol=args.target_vol,
        portfolio_target_vol=args.port_vol,
        max_leverage=args.max_leverage,
        cost_bps=args.cost_bps,
        slip_bps=args.slip_bps,
        risk_limits=limits,
        enable_position_manager=not args.no_pm,
    )

    if args.compare:
        cmp = run_strategy_comparison(panels, **bt_kwargs)
        out_csv = os.path.join(args.save_dir, "strategy_comparison.csv")
        cmp.to_csv(out_csv, float_format="%.6f")
        print("\n策略对比：")
        cols = [c for c in ["cagr", "ann_vol", "sharpe", "max_drawdown", "max_daily_loss", "calmar"] if c in cmp.columns]
        show = cmp[cols].copy()
        for c in ["cagr", "ann_vol", "max_drawdown", "max_daily_loss"]:
            if c in show.columns:
                show[c] = show[c].map(lambda x: f"{x:.2%}")
        for c in ["sharpe", "calmar"]:
            if c in show.columns:
                show[c] = show[c].map(lambda x: f"{x:.3f}")
        print(show.to_string())
        print(f"\n对比表已保存: {out_csv}")

    bt = CTABacktester(panels, method=args.method, **bt_kwargs)
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
    for name in ("risk_budget_scale", "dd_scale", "stop_hit", "dd_floor_hit"):
        if name in frames:
            frames[name].to_csv(os.path.join(args.save_dir, f"{name}.csv"))

    if args.plot:
        plot_path = os.path.join(args.save_dir, f"equity_{args.method}.png")
        _plot_equity(result.equity, plot_path, f"CTA Equity ({args.method}, akshare={args.akshare})")
        print(f"净值图已保存: {plot_path}")

    # 硬性约束检查
    ok_daily = result.summary.get("daily_loss_ok", 0) >= 1
    ok_dd = result.summary.get("drawdown_ok", 0) >= 1
    print(
        f"\n风控检查: 单日亏损<= {args.max_daily_loss:.0%} -> "
        f"{'PASS' if ok_daily else 'FAIL'} ({result.summary['max_daily_loss']:.2%}); "
        f"最大回撤<= {args.max_dd:.0%} -> "
        f"{'PASS' if ok_dd else 'FAIL'} ({result.summary['max_drawdown']:.2%})"
    )
    print(f"结果目录: {args.save_dir}/")
    return 0 if (ok_daily and ok_dd) else 2


if __name__ == "__main__":
    sys.exit(main())
