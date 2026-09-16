"""Write equity/drawdown charts, trade logs, and a markdown report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from brent_quant import config as cfg
from brent_quant.backtest import BacktestResult


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return str(obj)
    if hasattr(obj, "item") and not isinstance(obj, (bytes, str)):
        try:
            return obj.item()
        except Exception:  # noqa: BLE001
            return str(obj)
    if obj != obj:  # NaN
        return None
    return obj


def plot_equity(result: BacktestResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    result.equity.plot(ax=ax, color="#1f4e79", linewidth=1.0)
    ax.set_title(f"Equity — {result.model}")
    ax.set_ylabel("NAV")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_drawdown(result: BacktestResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    eq = result.daily_equity if len(result.daily_equity) else result.equity
    dd = eq / eq.cummax() - 1.0
    fig, ax = plt.subplots(figsize=(10, 3.5))
    dd.plot(ax=ax, color="#9c2f2f", linewidth=1.0)
    ax.set_title(f"Drawdown — {result.model}")
    ax.set_ylabel("Drawdown")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def write_trades(result: BacktestResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.trades is None or result.trades.empty:
        pd.DataFrame().to_csv(path, index=False)
    else:
        result.trades.to_csv(path, index=False)
    return path


def write_json(data: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(data), indent=2, default=str), encoding="utf-8")
    return path


def comparison_table(summaries: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    keys = [
        "model",
        "total_return",
        "cagr",
        "sharpe",
        "calmar",
        "profit_factor",
        "max_drawdown",
        "n_trades",
        "win_rate",
        "expectancy",
        "ann_vol",
    ]
    for name, stats in summaries.items():
        row = {k: stats.get(k) for k in keys}
        row["model"] = stats.get("model", name)
        rows.append(row)
    return pd.DataFrame(rows)


def write_markdown_report(
    path: Path,
    *,
    data_meta: dict[str, Any],
    qc: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
    extra_sections: dict[str, Any] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = comparison_table(summaries)
    lines = [
        "# Brent 量价第一阶段回测报告",
        "",
        "## 数据",
        "",
        "```json",
        json.dumps(_jsonable(data_meta), indent=2, default=str),
        "```",
        "",
        "## 数据质检",
        "",
        "```json",
        json.dumps(_jsonable(qc), indent=2, default=str),
        "```",
        "",
        "## 模型对比",
        "",
        table.to_markdown(index=False) if hasattr(table, "to_markdown") else table.to_string(index=False),
        "",
        "第一层筛选指标：Sharpe、Calmar、盈亏比（Profit Factor）、最大回撤。",
        "",
    ]
    extra_sections = extra_sections or {}
    title_map = {
        "factor_analysis": "因子分析",
        "parameter_scan": "参数扫描",
        "walk_forward": "Walk-forward",
    }
    for title, body in extra_sections.items():
        heading = title_map.get(title, title)
        lines.extend([f"## {heading}", "", "```json", json.dumps(_jsonable(body), indent=2, default=str), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
