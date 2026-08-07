# -*- coding: utf-8 -*-
"""运行用户定义四策略资金簿。

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
    axes[0].plot(result.nav_s1.index, result.nav_s1.values, lw=1.1, alpha=0.85, label="S1 MA14/16")
    axes[0].plot(result.nav_s2.index, result.nav_s2.values, lw=1.1, alpha=0.85, label="S2 Bollinger")
    axes[0].plot(result.nav_s3.index, result.nav_s3.values, lw=1.1, alpha=0.85, label="S3 Corr pairs")
    axes[0].plot(result.nav_s4.index, result.nav_s4.values, lw=1.1, alpha=0.85, label="S4 Calendar")
    axes[0].set_title("Defined Four-Strategy Book NAV (capital=1e6)")
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
    path = os.path.join(out_dir, "nav_book.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)

    # 分策略
    fig, ax = plt.subplots(figsize=(12, 5))
    for name, series, color in [
        ("S1", result.nav_s1, "#2a9d8f"),
        ("S2", result.nav_s2, "#e9c46a"),
        ("S3", result.nav_s3, "#e76f51"),
        ("S4", result.nav_s4, "#264653"),
    ]:
        ax.plot(series.index, series.values, label=name, color=color, lw=1.3)
    ax.set_title("Per-strategy NAV (return on total capital)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "nav_strategies.png"), dpi=150)
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="用户定义四策略资金簿")
    p.add_argument("--data-dir", default="cta_data_akshare")
    p.add_argument("--save-dir", default="cta_result_book")
    p.add_argument("--contract-cache", default="cta_data_contracts")
    p.add_argument("--skip-s4-fetch", action="store_true", help="不拉取分合约（S4 若无缓存则空仓）")
    p.add_argument("--capital", type=float, default=1_000_000.0)
    p.add_argument("--plot", action="store_true", default=True)
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args(argv)

    panels = _load_panels(args.data_dir)
    print(f"品种数={len(panels)}: {', '.join(sorted(panels))}")
    cfg = BookConfig(capital=args.capital)
    print(
        f"资金={cfg.capital:.0f} | 保证金权限 "
        f"S1={cfg.margin_s1:.0f} S2={cfg.margin_s2:.0f} "
        f"S3={cfg.margin_s3:.0f} S4={cfg.margin_s4:.0f}"
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

    _show("策略一 均线14/16", result.summary_s1)
    _show("策略二 布林带套利", result.summary_s2)
    _show("策略三 相关配对", result.summary_s3)
    _show("策略四 跨期价差", result.summary_s4)
    _show("总资金簿", result.summary_total)

    os.makedirs(args.save_dir, exist_ok=True)
    nav = result.nav_total.to_frame("NAV")
    nav["ret"] = result.ret_total
    nav["s1"] = result.nav_s1
    nav["s2"] = result.nav_s2
    nav["s3"] = result.nav_s3
    nav["s4"] = result.nav_s4
    nav.to_csv(os.path.join(args.save_dir, "nav_book.csv"))

    rows = []
    for name, s in [
        ("s1_ma", result.summary_s1),
        ("s2_boll", result.summary_s2),
        ("s3_corr", result.summary_s3),
        ("s4_cal", result.summary_s4),
        ("total", result.summary_total),
    ]:
        rows.append({"strategy": name, **s})
    pd.DataFrame(rows).to_csv(os.path.join(args.save_dir, "summary_book.csv"), index=False)

    # 文字报告
    report_path = os.path.join(args.save_dir, "report.md")
    eq = result.cfg.capital * result.nav_total
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 四策略资金簿报告\n\n")
        f.write(f"- 总资金：{cfg.capital:,.0f} 元\n")
        f.write(
            f"- 保证金权限：S1 {cfg.margin_s1:,.0f} / S2 {cfg.margin_s2:,.0f} / "
            f"S3 {cfg.margin_s3:,.0f} / S4 {cfg.margin_s4:,.0f} "
            f"（合计 {cfg.margin_s1+cfg.margin_s2+cfg.margin_s3+cfg.margin_s4:,.0f}）\n"
        )
        f.write(f"- 杠杆假设：单边名义 = 保证金 × 10\n")
        f.write(
            f"- 期末权益：{result.summary_total.get('ending_equity', float('nan')):,.0f} 元 "
            f"（绝对盈亏 {result.summary_total.get('pnl_abs', float('nan')):,.0f}）\n\n"
        )
        f.write("## 规则摘要\n\n")
        f.write("1. **均线突破**：MA14 上穿 MA16 做多、下穿做空；价格回到中轨 `(MA14+MA16)/2` 平仓；平仓后须新交叉才再开仓\n")
        f.write("2. **布林带**：20日中轨、±2σ 上下轨；上穿上轨后再下穿上轨做空，下穿下轨后再上穿下轨做多；回中轨平仓；|z|≥4 止损；不重复开仓\n")
        f.write("3. **相关配对**：全品种滚动20日收益相关>0.6 入选；对数价差20日 z，|z|≥2 回归开仓、回0平仓、|z|≥4 止损；1:1 名义\n")
        f.write("4. **跨期价差**：分合约按持仓量（缺则成交量）取当日主力/次主力；价差=主力−次主力；20日 z，|z|≥2 回归、回0平、|z|≥4 止损\n\n")
        f.write("## 绩效一览\n\n")
        f.write("| 策略 | 累计收益 | 年化 | 夏普 | 最大回撤 | 保证金峰值 |\n")
        f.write("|------|----------|------|------|----------|------------|\n")
        for title, s in [
            ("策略一 均线", result.summary_s1),
            ("策略二 布林", result.summary_s2),
            ("策略三 相关配对", result.summary_s3),
            ("策略四 跨期", result.summary_s4),
            ("合计", result.summary_total),
        ]:
            f.write(
                f"| {title} | {s['total_return']:.2%} | {s['cagr']:.2%} | {s['sharpe']:.3f} | "
                f"{s['max_drawdown']:.2%} | {s.get('max_margin', 0):,.0f} |\n"
            )
        f.write("\n## 输出文件\n\n")
        f.write("- `nav_book.csv` / `nav_book.png`：总资金与分策略净值\n")
        f.write("- `nav_strategies.png`：分策略净值对比\n")
        f.write("- `summary_book.csv`：绩效表\n")
    print(f"\n报告: {report_path}")
    # 绝对权益
    eq.to_frame("equity").assign(nav=result.nav_total).to_csv(
        os.path.join(args.save_dir, "equity_book.csv")
    )

    if args.plot and not args.no_plot:
        _plot(result, args.save_dir)
        print(f"曲线: {args.save_dir}/nav_book.png , {args.save_dir}/nav_strategies.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
