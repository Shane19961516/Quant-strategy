# -*- coding: utf-8 -*-
"""Report generation: tables, plots, markdown research note."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Mapping, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from .evaluate import FactorEval
    from .pipeline import FactorEngineeringResult


def _df_markdown(df: pd.DataFrame, floatfmt: str = "{:.4f}") -> str:
    """Minimal markdown table without requiring the tabulate package."""
    cols = [str(c) for c in df.columns]
    header = "| " + " | ".join([""] + cols) + " |"
    sep = "| " + " | ".join(["---"] * (len(cols) + 1)) + " |"
    rows = [header, sep]
    for idx, row in df.iterrows():
        cells = [str(idx)]
        for v in row.values:
            if isinstance(v, (float, np.floating)) or (
                isinstance(v, (int, np.integer)) and not isinstance(v, bool)
            ):
                try:
                    cells.append(floatfmt.format(float(v)))
                except (ValueError, TypeError):
                    cells.append(str(v))
            else:
                cells.append(str(v))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def save_report(result: "FactorEngineeringResult", out_dir: Path | str) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    result.scorecard.to_csv(out / "fe_scorecard.csv", encoding="utf-8-sig")
    result.corr_matrix.to_csv(out / "fe_factor_corr.csv", encoding="utf-8-sig")

    decay_rows = {n: ev.decay for n, ev in result.evals.items()}
    pd.DataFrame(decay_rows).T.to_csv(out / "fe_ic_decay.csv", encoding="utf-8-sig")

    ic_panel = pd.DataFrame({n: ev.ic for n, ev in result.evals.items()})
    ic_panel.to_csv(out / "fe_ic_series.csv", encoding="utf-8-sig")

    if result.selected_factors:
        pd.Series(result.selected_factors, name="selected").to_csv(
            out / "fe_selected_factors.csv", encoding="utf-8-sig"
        )

    if result.composite is not None:
        # don't dump full matrix; save IC + LS summary instead
        pass

    if result.blend_weights is not None:
        result.blend_weights.to_csv(out / "fe_blend_weights.csv", encoding="utf-8-sig")

    if result.backtest is not None:
        result.backtest.equity.to_csv(out / "fe_composite_equity.csv", encoding="utf-8-sig")
        pd.Series(result.backtest.summary).to_csv(
            out / "fe_composite_summary.csv", encoding="utf-8-sig"
        )

    # single-factor LS summaries
    if result.single_factor_bt is not None and not result.single_factor_bt.empty:
        result.single_factor_bt.to_csv(out / "fe_single_factor_bt.csv", encoding="utf-8-sig")

    _plot_ic_bars(result, out / "fe_ic_bars.png")
    _plot_ic_timeseries(result, out / "fe_ic_ts.png")
    _plot_decay(result, out / "fe_ic_decay.png")
    _plot_corr(result, out / "fe_corr_heatmap.png")
    _plot_quantiles(result, out / "fe_quantiles.png")
    if result.backtest is not None:
        _plot_nav(result, out / "fe_composite_nav.png")

    md = render_markdown(result)
    (out / "FACTOR_ENGINEERING_REPORT.md").write_text(md, encoding="utf-8")
    (out / "SUMMARY.txt").write_text(_summary_text(result), encoding="utf-8")
    return out


def _summary_text(result: "FactorEngineeringResult") -> str:
    lines = [
        "因子工程研究报告摘要",
        f"样本区间: {result.start} ~ {result.end}",
        f"股票数 x 月数: {result.n_stocks} x {result.n_months}",
        f"候选因子数: {len(result.factors)}",
        f"入选因子: {', '.join(result.selected_factors) if result.selected_factors else '(无)'}",
        "",
        "=== 因子质量榜 ===",
        result.scorecard[
            [
                c
                for c in [
                    "desc",
                    "family",
                    "ic_mean",
                    "icir",
                    "ic_tstat",
                    "ic_pos_ratio",
                    "q_spread",
                    "q_monotonicity",
                    "avg_turnover",
                    "quality",
                    "pass_all",
                    "direction",
                ]
                if c in result.scorecard.columns
            ]
        ].to_string(float_format=lambda x: f"{x:.4f}"),
    ]
    if result.backtest is not None:
        s = result.backtest.summary
        lines += [
            "",
            "=== 合成因子多空验证 ===",
            f"  累计: {s.get('total_return', float('nan')):.2%}",
            f"  年化: {s.get('cagr', float('nan')):.2%}",
            f"  夏普: {s.get('sharpe', float('nan')):.3f}",
            f"  最大回撤: {s.get('max_drawdown', float('nan')):.2%}",
            f"  平均换手: {s.get('avg_turnover', float('nan')):.2%}",
        ]
    return "\n".join(lines)


def render_markdown(result: "FactorEngineeringResult") -> str:
    sc = result.scorecard
    passed = sc.index[sc["pass_all"]].tolist() if "pass_all" in sc.columns else []
    lines = [
        "# 因子工程研究报告",
        "",
        "## 1. 概述",
        "",
        f"- **样本区间**: {result.start} ~ {result.end}",
        f"- **股票池**: {result.universe}（{result.n_stocks} 只，{result.n_months} 个月）",
        f"- **处理流程**: 滞后1期 → 缩尾(1%) → 行业中性 → 截面 z-score",
        f"- **候选因子**: {len(result.factors)} 个价量因子",
        f"- **入选因子**: {', '.join(passed) if passed else '无（放宽阈值或见质量榜）'}",
        "",
        "## 2. 因子质量榜",
        "",
        "| 因子 | 类别 | IC均值 | ICIR | t统计 | 胜率 | 分位价差 | 单调性 | 换手 | 质量分 | 入选 |",
        "|------|------|--------|------|-------|------|----------|--------|------|--------|------|",
    ]
    for name, row in sc.iterrows():
        lines.append(
            "| {name} | {family} | {ic:.3%} | {icir:.2f} | {t:.2f} | {hit:.1%} | "
            "{qs:.3%} | {mono:.2f} | {to:.2f} | {q:.3f} | {p} |".format(
                name=name,
                family=row.get("family", ""),
                ic=row.get("ic_mean", float("nan")),
                icir=row.get("icir", float("nan")),
                t=row.get("ic_tstat", float("nan")),
                hit=row.get("ic_pos_ratio", float("nan")),
                qs=row.get("q_spread", float("nan")),
                mono=row.get("q_monotonicity", float("nan")),
                to=row.get("avg_turnover", float("nan")),
                q=row.get("quality", float("nan")),
                p="Y" if row.get("pass_all") else "",
            )
        )
    lines += [
        "",
        "## 3. IC 衰减",
        "",
        "因子在预测未来第 h 个月收益时的平均 Rank IC（h=0 为可交易同期）。",
        "",
    ]
    decay = pd.DataFrame({n: ev.decay for n, ev in result.evals.items()}).T
    lines.append(_df_markdown(decay, floatfmt="{:.4f}"))
    lines += [
        "",
        "## 4. 因子相关性",
        "",
        "截面 Spearman 相关的时间均值（冗余筛选阈值 |ρ|≥0.85）。",
        "",
        _df_markdown(result.corr_matrix, floatfmt="{:.2f}"),
        "",
        "## 5. 合成与验证",
        "",
    ]
    if result.backtest is not None:
        s = result.backtest.summary
        lines += [
            f"合成方式: **{result.combine_method}**；多空五分位验证（成本 {result.cost_bps:.0f}bp 单边）。",
            "",
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| 累计收益 | {s.get('total_return', float('nan')):.2%} |",
            f"| 年化收益 | {s.get('cagr', float('nan')):.2%} |",
            f"| 年化波动 | {s.get('ann_vol', float('nan')):.2%} |",
            f"| 夏普 | {s.get('sharpe', float('nan')):.3f} |",
            f"| 最大回撤 | {s.get('max_drawdown', float('nan')):.2%} |",
            f"| 月胜率 | {s.get('win_rate', float('nan')):.2%} |",
            f"| 平均换手 | {s.get('avg_turnover', float('nan')):.2%} |",
            "",
        ]
    else:
        lines.append("未运行合成回测。")
    lines += [
        "",
        "## 6. 结论与使用建议",
        "",
        "1. A 股月频价量样本中，**短端反转 / 低波动 / 彩票类（MAX、偏度）**通常优于经典 12-1 动量。",
        "2. 入选因子应按 `direction` 列校正方向后再合成；IC 为负的因子在合成前取反。",
        "3. 高相关因子只保留质量分更高者，降低共线性。",
        "4. 本报告仅基于仓库内月度涨跌幅与中信行业，无基本面/资金流；扩展因子库时复用同一评估管线。",
        "",
        "---",
        "*由 `factor_engineering` 自动生成。*",
    ]
    return "\n".join(lines)


def _plot_ic_bars(result: "FactorEngineeringResult", path: Path) -> None:
    sc = result.scorecard
    fig, ax = plt.subplots(figsize=(10, 4.5))
    colors = ["#1B6B4A" if v >= 0 else "#A33B2B" for v in sc["ic_mean"]]
    ax.bar(range(len(sc)), sc["ic_mean"].values, color=colors, width=0.7)
    ax.set_xticks(range(len(sc)))
    ax.set_xticklabels(sc.index.tolist(), rotation=35, ha="right")
    ax.axhline(0, color="#333", lw=0.8)
    ax.set_ylabel("Mean Rank IC")
    ax.set_title("Factor Mean IC")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_ic_timeseries(result: "FactorEngineeringResult", path: Path) -> None:
    # plot top-4 by quality
    top = result.scorecard.head(4).index.tolist()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for name in top:
        ic = result.evals[name].ic.dropna()
        ax.plot(ic.index, ic.rolling(6, min_periods=3).mean(), lw=1.5, label=name)
    ax.axhline(0, color="#888", lw=0.8)
    ax.legend(frameon=False, ncol=2)
    ax.set_title("Rank IC (6m MA) — Top Quality Factors")
    ax.set_ylabel("IC")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_decay(result: "FactorEngineeringResult", path: Path) -> None:
    top = result.scorecard.head(5).index.tolist()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for name in top:
        d = result.evals[name].decay
        ax.plot(d.index.astype(float), d.values, marker="o", lw=1.6, label=name)
    ax.axhline(0, color="#888", lw=0.8)
    ax.set_xlabel("Horizon (months)")
    ax.set_ylabel("Mean Rank IC")
    ax.set_title("IC Decay")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_corr(result: "FactorEngineeringResult", path: Path) -> None:
    corr = result.corr_matrix
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(corr)))
    ax.set_yticks(range(len(corr)))
    ax.set_xticklabels(corr.columns, rotation=45, ha="right")
    ax.set_yticklabels(corr.index)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set_title("Factor Correlation (avg Spearman)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_quantiles(result: "FactorEngineeringResult", path: Path) -> None:
    top = result.scorecard.head(4).index.tolist()
    n = len(top)
    fig, axes = plt.subplots(1, n, figsize=(3.2 * n, 3.8), sharey=True)
    if n == 1:
        axes = [axes]
    for ax, name in zip(axes, top):
        means = result.evals[name].quantile_rets.mean()
        ax.bar(range(len(means)), means.values, color="#0B3D5C", width=0.7)
        ax.set_xticks(range(len(means)))
        ax.set_xticklabels(means.index.tolist(), rotation=0)
        ax.axhline(0, color="#888", lw=0.8)
        ax.set_title(name)
    axes[0].set_ylabel("Mean monthly return")
    fig.suptitle("Quantile Returns (Q1 low → Qn high)", y=1.02)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _plot_nav(result: "FactorEngineeringResult", path: Path) -> None:
    eq = result.backtest.equity.dropna()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(eq.index, eq.values, color="#0B3D5C", lw=1.8)
    ax.set_title("Composite Factor Long-Short NAV")
    ax.set_ylabel("NAV")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
