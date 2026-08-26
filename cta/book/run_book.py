# -*- coding: utf-8 -*-
"""运行用户定义三策略资金簿（策略二已移除）。

示例：
  python -m cta.book.run_book --data-dir cta_data_akshare --plot
"""

from __future__ import annotations

import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from ..metrics import format_summary
from .engine import run_defined_book
from .strategies_12 import BookConfig


def _load_panels(data_dir: str):
    from ..data import load_panels

    return load_panels(data_dir)


def _plot(result, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    s1r = float(result.summary_s1["total_return"])
    s3r = float(result.summary_s3["total_return"])
    s4r = float(result.summary_s4["total_return"])
    st = result.summary_total

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    axes[0].plot(
        result.nav_total.index,
        result.nav_total.values,
        color="#1f4e79",
        lw=2.2,
        label=f"Total  {st['total_return']:+.1%}",
    )
    axes[0].plot(
        result.nav_s1.index,
        result.nav_s1.values,
        lw=1.2,
        alpha=0.9,
        label=f"S1 MA+  {s1r:+.1%}",
    )
    axes[0].plot(
        result.nav_s3.index,
        result.nav_s3.values,
        lw=1.2,
        alpha=0.9,
        label=f"S3 Corr  {s3r:+.1%}",
    )
    axes[0].plot(
        result.nav_s4.index,
        result.nav_s4.values,
        lw=1.4,
        alpha=0.95,
        color="#2a9d8f",
        label=f"S4 Calendar  {s4r:+.1%}",
    )
    axes[0].set_title(
        "Book NAV (each sleeve = PnL / total capital; Total compounds r1+r3+r4)"
    )
    axes[0].legend(loc="upper left", fontsize=8)
    axes[0].grid(True, alpha=0.3)
    axes[0].text(
        0.99,
        0.04,
        f"Total={st['total_return']:.1%}  CAGR={st['cagr']:.1%}  "
        f"Sharpe={st['sharpe']:.2f}  MaxDD={st['max_drawdown']:.1%}",
        transform=axes[0].transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85, "edgecolor": "#ccc"},
    )
    peak = result.nav_total.cummax()
    dd = result.nav_total / peak - 1.0
    axes[1].fill_between(dd.index, dd.values, 0, color="#c44e52", alpha=0.55)
    axes[1].set_ylabel("Drawdown")
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "nav_book.png"), dpi=150)
    plt.close(fig)

    # 分策略图：图例带累计收益，避免 S4「看起来很平」被误读
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2.2, 1.4]})
    for name, series, color, ret in [
        ("S1 MA+", result.nav_s1, "#2a9d8f", s1r),
        ("S3 Corr", result.nav_s3, "#e76f51", s3r),
        ("S4 Calendar", result.nav_s4, "#264653", s4r),
    ]:
        axes[0].plot(series.index, series.values, label=f"{name}  {ret:+.1%}", color=color, lw=1.5)
    axes[0].set_title("Per-strategy NAV on total capital (1.0 = flat)")
    axes[0].legend(loc="upper left", fontsize=9)
    axes[0].grid(True, alpha=0.3)
    axes[0].axhline(1.0, color="#999", lw=0.8, ls="--")

    # S4 单独放大，避免被 S1/S3 尺度淹没
    axes[1].plot(result.nav_s4.index, result.nav_s4.values, color="#264653", lw=1.8, label=f"S4 only  {s4r:+.1%}")
    axes[1].fill_between(result.nav_s4.index, 1.0, result.nav_s4.values, alpha=0.25, color="#264653")
    axes[1].axhline(1.0, color="#999", lw=0.8, ls="--")
    axes[1].set_ylabel("S4 NAV")
    axes[1].set_title("S4 zoom (same series, own scale)")
    axes[1].legend(loc="upper left", fontsize=9)
    axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "nav_strategies.png"), dpi=150)
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="用户定义三策略资金簿（无布林）")
    p.add_argument("--data-dir", default="cta_data_akshare")
    p.add_argument("--save-dir", default="cta_result_book")
    p.add_argument("--contract-cache", default="cta_data_contracts")
    p.add_argument("--skip-s4-fetch", action="store_true", help="不拉取分合约（只用本地缓存）")
    p.add_argument("--capital", type=float, default=1_000_000.0)
    p.add_argument("--plot", action="store_true", default=True)
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args(argv)

    panels = _load_panels(args.data_dir)
    print(f"品种数={len(panels)}: {', '.join(sorted(panels))}")
    cfg = BookConfig(capital=args.capital)
    print(
        f"资金={cfg.capital:.0f} | 保证金权限 "
        f"S1={cfg.margin_s1:.0f} S3={cfg.margin_s3:.0f} S4={cfg.margin_s4:.0f} "
        f"(S2已移除)"
    )

    result = run_defined_book(
        panels,
        cfg=cfg,
        fetch_contracts=not args.skip_s4_fetch,
        contract_cache=args.contract_cache,
    )

    def _show(name, s):
        print(f"\n=== {name} ===")
        print(format_summary(s))
        print(
            f"  保证金占用 max={s.get('max_margin', float('nan')):,.0f} / "
            f"budget={s.get('margin_budget', float('nan')):,.0f} "
            f"(ok={bool(s.get('margin_ok', 0))})"
        )

    _show("策略一 均线14/16（改进）", result.summary_s1)
    _show("策略三 相关配对", result.summary_s3)
    _show("策略四 跨期价差", result.summary_s4)
    _show("总资金簿", result.summary_total)

    os.makedirs(args.save_dir, exist_ok=True)
    nav = result.nav_total.to_frame("NAV")
    nav["ret"] = result.ret_total
    nav["s1"] = result.nav_s1
    nav["s3"] = result.nav_s3
    nav["s4"] = result.nav_s4
    nav.to_csv(os.path.join(args.save_dir, "nav_book.csv"))

    rows = []
    for name, s in [
        ("s1_ma", result.summary_s1),
        ("s3_corr", result.summary_s3),
        ("s4_cal", result.summary_s4),
        ("total", result.summary_total),
    ]:
        rows.append({"strategy": name, **s})
    pd.DataFrame(rows).to_csv(os.path.join(args.save_dir, "summary_book.csv"), index=False)

    report_path = os.path.join(args.save_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 三策略资金簿报告（策略二已移除）\n\n")
        f.write(f"- 总资金：{cfg.capital:,.0f} 元\n")
        f.write(
            f"- 保证金权限：S1 {cfg.margin_s1:,.0f} / S3 {cfg.margin_s3:,.0f} / "
            f"S4 {cfg.margin_s4:,.0f}（合计 {cfg.margin_s1+cfg.margin_s3+cfg.margin_s4:,.0f}；"
            f"原 S2 30 万额度闲置为现金缓冲）\n"
        )
        f.write(
            "- 杠杆/保证金：S1/S3 按名义≈保证金×10；S4 按合约乘数与保证金率、跨期折扣计占用\n"
        )

        f.write(
            f"- 期末权益：{result.summary_total.get('ending_equity', float('nan')):,.0f} 元 "
            f"（绝对盈亏 {result.summary_total.get('pnl_abs', float('nan')):,.0f}）\n\n"
        )
        f.write("## 规则摘要\n\n")
        f.write(
            "1. **均线（改进）**：MA14/16 交叉开仓；仅在 MA60 同向过滤；"
            "中轨±1×ATR 缓冲平仓；2.5×ATR 跟踪止损；不重复开仓\n"
        )
        f.write("2. ~~布林带~~：**已移除**\n")
        f.write("3. **相关配对**：滚动20日相关>0.6；对数价差20日 z，|z|≥2 开、回0平、|z|≥4 止损；1:1 名义\n")
        f.write(
            "4. **跨期价差（实盘口径）**：仅 RB/HC/I/CU；固定近+次近月；"
            "交割前移仓；量/仓过滤；z 回归 + 成本门槛 + 半衰期过滤；"
            "乘数/保证金计仓；近月0.8bp+远月2bp\n\n"
        )
        f.write(
            "> 注：落地关键是品种池（黑色+铜）+固定合约对；全市场日更主力会虚增，"
            "纯利率持有成本带缺仓储项在商品上失效。半衰期过滤作可选项，默认关闭。\n\n"
        )

        f.write("## 绩效一览\n\n")
        f.write("| 策略 | 累计收益 | 年化 | 夏普 | 最大回撤 | 保证金峰值 |\n")
        f.write("|------|----------|------|------|----------|------------|\n")
        for title, s in [
            ("策略一 均线+", result.summary_s1),
            ("策略三 相关配对", result.summary_s3),
            ("策略四 跨期", result.summary_s4),
            ("合计", result.summary_total),
        ]:
            f.write(
                f"| {title} | {s['total_return']:.2%} | {s['cagr']:.2%} | {s['sharpe']:.3f} | "
                f"{s['max_drawdown']:.2%} | {s.get('max_margin', 0):,.0f} |\n"
            )
        f.write("\n## 输出文件\n\n")
        f.write("- `nav_book.csv` / `nav_book.png`\n")
        f.write("- `nav_strategies.png`\n")
        f.write("- `summary_book.csv`\n")
    print(f"\n报告: {report_path}")

    eq = result.cfg.capital * result.nav_total
    eq.to_frame("equity").assign(nav=result.nav_total).to_csv(
        os.path.join(args.save_dir, "equity_book.csv")
    )

    if args.plot and not args.no_plot:
        _plot(result, args.save_dir)
        print(f"曲线: {args.save_dir}/nav_book.png , {args.save_dir}/nav_strategies.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
