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
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    axes[0].plot(result.nav_total.index, result.nav_total.values, color="#1f4e79", lw=2.0, label="Total NAV")
    axes[0].plot(result.nav_s1.index, result.nav_s1.values, lw=1.2, alpha=0.9, label="S1 MA14/16+")
    axes[0].plot(result.nav_s3.index, result.nav_s3.values, lw=1.2, alpha=0.9, label="S3 Corr pairs")
    axes[0].plot(result.nav_s4.index, result.nav_s4.values, lw=1.2, alpha=0.9, label="S4 Calendar")
    axes[0].set_title("Three-Strategy Book NAV (S2 removed, capital=1e6)")
    axes[0].legend(loc="upper left", fontsize=8)
    axes[0].grid(True, alpha=0.3)
    st = result.summary_total
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

    fig, ax = plt.subplots(figsize=(12, 5))
    for name, series, color in [
        ("S1 MA+", result.nav_s1, "#2a9d8f"),
        ("S3 Corr", result.nav_s3, "#e76f51"),
        ("S4 Calendar", result.nav_s4, "#264653"),
    ]:
        ax.plot(series.index, series.values, label=name, color=color, lw=1.4)
    ax.set_title("Per-strategy NAV (return on total capital)")
    ax.legend()
    ax.grid(True, alpha=0.3)
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
            "4. **跨期价差（实盘口径）**：固定近月+次近月（不按持仓量日更）；"
            "临近交割强制移仓；成交量/持仓过滤；按乘数与保证金率计手数；"
            "近月1bp+远月3bp 成本；默认排除 IF/RU；20日 z，|z|≥2 回归、回0平、|z|≥4 止损\n\n"
        )
        f.write(
            "> 注：旧版按每日持仓量重选主/次主力会使价差序列不连续，回测夏普会被虚增；"
            "本版按固定合约对+移仓规则后，S4 收益显著回落，更接近可交易口径。\n\n"
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
