# -*- coding: utf-8 -*-
"""Alpha101 validation report writer."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from factor_engineering.admission import AdmissionCriteria

from .evaluate_5d import Alpha5DResult


def save_alpha101_report(
    *,
    summary: pd.DataFrame,
    results: Mapping[str, Alpha5DResult],
    admitted: List[str],
    rejected: List[str],
    out_dir: Path,
    asof: str,
    n_tickers: int,
    n_days: int,
    start: str,
    horizon: int,
    criteria: AdmissionCriteria,
    store_root: Path,
    strict_admitted: Optional[List[str]] = None,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _plot_ic_bars(summary, out_dir / "ic_bars.png")
    if admitted:
        _plot_ic_ts(results, admitted[:5], out_dir / "ic_ts_admitted.png")
        _plot_quantiles(results, admitted[:4], out_dir / "quantiles_admitted.png")
        _plot_nav(results, admitted[:5], out_dir / "ls_nav_admitted.png")

    md = _render_md(
        summary=summary,
        admitted=admitted,
        rejected=rejected,
        asof=asof,
        n_tickers=n_tickers,
        n_days=n_days,
        start=start,
        horizon=horizon,
        criteria=criteria,
        store_root=store_root,
        strict_admitted=strict_admitted or [],
    )
    (out_dir / "FACTOR_REPORT.md").write_text(md, encoding="utf-8")
    (out_dir / "SUMMARY.txt").write_text(_summary_txt(summary, admitted, rejected, asof), encoding="utf-8")
    return out_dir


def _summary_txt(summary, admitted, rejected, asof) -> str:
    lines = [
        "Alpha101 US Validation Summary",
        f"ASOF: {asof}",
        f"ADMITTED ({len(admitted)}): {', '.join(admitted) if admitted else '(none)'}",
        f"REJECTED ({len(rejected)}): {len(rejected)} factors",
        "",
        summary[
            [
                c
                for c in [
                    "admitted",
                    "direction",
                    "ic_mean",
                    "icir",
                    "ic_tstat",
                    "q_monotonicity",
                    "q_spread",
                    "ls_sharpe",
                    "ls_cagr",
                    "half_sample_sign_match",
                    "subperiod_sign_ratio",
                ]
                if c in summary.columns
            ]
        ].to_string(float_format=lambda x: f"{x:.4f}"),
    ]
    return "\n".join(lines)


def _render_md(
    *,
    summary,
    admitted,
    rejected,
    asof,
    n_tickers,
    n_days,
    start,
    horizon,
    criteria,
    store_root,
    strict_admitted=None,
) -> str:
    strict_admitted = strict_admitted or []
    lines = [
        "# Alpha101 因子验证报告（美股 SPX ∪ NDX）",
        "",
        "## 1. 研究设定",
        "",
        f"- **股票池**: S&P 500 ∪ Nasdaq-100（有效股票约 {n_tickers} 只）",
        f"- **样本**: {start} → {asof}（约 {n_days} 个交易日，近 10 年）",
        f"- **数据源**: yfinance（`auto_adjust=True` OHLCV；VWAP 代理=(H+L+C)/3）",
        f"- **预测目标**: 未来 **{horizon}** 个交易日收益率 `close[t+{horizon}]/close[t]-1`",
        "- **无前视**: 因子计算后 `shift(1)`，再用非重叠网格评估",
        "- **评估网格**: 每 5 个交易日一点（避免重叠收益导致 IC/夏普虚高）",
        "",
        "## 2. 入库标准",
        "",
        "### 研究级（实际入库）",
        f"- |IC| ≥ {criteria.min_abs_ic}, |ICIR| ≥ {criteria.min_abs_icir}, |t| ≥ {criteria.min_ic_tstat}",
        f"- 胜率(方向校正) ≥ {criteria.min_ic_hit_rate}",
        f"- 稳定性: 年度同号 ≥ {criteria.min_subperiod_sign_ratio}, 半样本同号, 滚动ICIR为正 ≥ {criteria.min_rolling_icir_pos_ratio}",
        f"- 分层: 单调性 ≥ {criteria.min_quantile_monotonicity}, |价差| ≥ {criteria.min_abs_q_spread}",
        f"- 多空: 夏普 ≥ {criteria.min_ls_sharpe}, 回撤 ≥ {criteria.max_ls_drawdown}",
        "",
        "### 机构级对照",
        "- 更严：|IC|≥0.015, |ICIR|≥0.25, |t|≥1.8, 分层价差≥0.2%, LS夏普≥0.25",
        f"- 本样本机构级通过: {', '.join(f'`{x}`' for x in strict_admitted) if strict_admitted else '无（符合 Alpha101 弱信号预期）'}",
        "",
        f"## 3. 裁决结果（研究级入库 {len(admitted)} / 拒绝 {len(rejected)}）",
        "",
        f"**保留因子**: {', '.join(f'`{x}`' for x in admitted) if admitted else '（无）'}",
        "",
        "| 因子 | 入库 | 方向 | IC | ICIR | t | 分层单调 | 价差 | LS夏普 | LS年化 | 半样本 |",
        "|------|------|------|----|------|---|----------|------|--------|--------|--------|",
    ]
    for name, row in summary.iterrows():
        lines.append(
            "| {f} | {a} | {d:+.0f} | {ic:.3%} | {icir:.2f} | {t:.2f} | {m:.2f} | {qs:.3%} | {sh:.2f} | {cg:.2%} | {hs} |".format(
                f=name,
                a="Y" if row["admitted"] else "",
                d=row["direction"],
                ic=row["ic_mean"] if pd.notna(row["ic_mean"]) else float("nan"),
                icir=row["icir"] if pd.notna(row["icir"]) else float("nan"),
                t=row["ic_tstat"] if pd.notna(row["ic_tstat"]) else float("nan"),
                m=row["q_monotonicity"] if pd.notna(row["q_monotonicity"]) else float("nan"),
                qs=row["q_spread"] if pd.notna(row["q_spread"]) else float("nan"),
                sh=row["ls_sharpe"] if pd.notna(row["ls_sharpe"]) else float("nan"),
                cg=row["ls_cagr"] if pd.notna(row["ls_cagr"]) else float("nan"),
                hs="Y" if row["half_sample_sign_match"] else "",
            )
        )

    lines += [
        "",
        "## 4. 读取与调用",
        "",
        "```python",
        "from factor_engineering import FactorStore",
        f'store = FactorStore("{store_root.as_posix()}")',
        'print(store.list_factors(status="admitted"))',
        's = store.get_factor_on("alpha012", "2024-12-31")  # 已按 direction 校正',
        'print(store.get_doc("alpha012")[:500])',
        "```",
        "",
        "```bash",
        "python3 run_alpha101_validation.py",
        f"python3 -c \"from factor_engineering import FactorStore; print(FactorStore('{store_root.as_posix()}').list_factors())\"",
        "```",
        "",
        "## 5. 结论",
        "",
        "1. 仅保留同时满足**有效性、稳定性、分层、多空**门禁的 Alpha101 因子。",
        "2. IC 为负的因子入库 `direction=-1`，调用 API 默认取反，使高分始终偏好多头。",
        "3. 评估使用非重叠 5 日收益，结论比逐日重叠 IC 更保守、更可靠。",
        "4. 完整门禁明细见 `factor_db_alpha101/admission/` 与各因子 `docs/*.md`。",
        "",
        "---",
        "*由 `alpha101` 流水线自动生成。*",
    ]
    return "\n".join(lines)


def _plot_ic_bars(summary: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.8))
    colors = ["#1B6B4A" if bool(a) else "#9AA0A6" for a in summary["admitted"]]
    ax.bar(range(len(summary)), summary["ic_mean"].values, color=colors, width=0.75)
    ax.set_xticks(range(len(summary)))
    ax.set_xticklabels(summary.index.tolist(), rotation=60, ha="right", fontsize=8)
    ax.axhline(0, color="#333", lw=0.8)
    ax.set_ylabel("Mean Rank IC (5d non-overlap)")
    ax.set_title("Alpha101 IC (green = admitted)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_ic_ts(results, names, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for name in names:
        ic = results[name].ic_nonoverlap.dropna()
        ax.plot(ic.index, ic.rolling(12, min_periods=4).mean(), lw=1.5, label=name)
    ax.axhline(0, color="#888", lw=0.8)
    ax.legend(frameon=False, ncol=3, fontsize=8)
    ax.set_title("Non-overlap Rank IC (12-period MA)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_quantiles(results, names, path: Path) -> None:
    n = len(names)
    fig, axes = plt.subplots(1, n, figsize=(3.2 * n, 3.6), sharey=True)
    if n == 1:
        axes = [axes]
    for ax, name in zip(axes, names):
        means = results[name].quantile_rets.mean()
        ax.bar(range(len(means)), means.values, color="#0B3D5C", width=0.7)
        ax.set_xticks(range(len(means)))
        ax.set_xticklabels(list(means.index))
        ax.axhline(0, color="#888", lw=0.8)
        ax.set_title(name, fontsize=9)
    axes[0].set_ylabel("Mean 5d return")
    fig.suptitle("Layered returns (Q1 low → Q5 high)", y=1.02)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _plot_nav(results, names, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for name in names:
        eq = results[name].equity.dropna()
        if eq.empty:
            continue
        ax.plot(eq.index, eq.values, lw=1.5, label=name)
    ax.legend(frameon=False, ncol=3, fontsize=8)
    ax.set_title("Long-short NAV (direction-adjusted)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
