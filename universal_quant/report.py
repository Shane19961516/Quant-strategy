"""UPV equity / P&L charts."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.sans-serif"] = [
    "WenQuanYi Micro Hei",
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "WenQuanYi Zen Hei",
    "Droid Sans Fallback",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_model_pnl(navs: dict[str, pd.Series], path: Path, title: str) -> Path:
    fig, ax = plt.subplots(figsize=(11, 4.6))
    for name, eq in navs.items():
        s = eq.dropna()
        if s.empty or s.iloc[0] == 0:
            continue
        pnl = s / s.iloc[0] - 1.0
        ax.plot(pnl.index, pnl.values, label=name, linewidth=1.4)
    ax.axhline(0.0, color="#999999", linewidth=0.8)
    ax.set_title(title)
    ax.set_ylabel("累计收益")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", ncol=2, frameon=False)
    ax.yaxis.set_major_formatter(lambda x, _pos: f"{x:.0%}")
    return _save(fig, path)


def plot_portfolio_nav(series_map: dict[str, pd.Series], path: Path, title: str) -> Path:
    fig, ax = plt.subplots(figsize=(11, 4.6))
    for name, eq in series_map.items():
        s = eq.dropna()
        if s.empty:
            continue
        ax.plot(s.index, s.values / 1_000_000.0, label=name, linewidth=1.6)
    ax.set_title(title)
    ax.set_ylabel("净值（百万）")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", frameon=False)
    return _save(fig, path)


def plot_drawdown(eq: pd.Series, path: Path, title: str) -> Path:
    s = eq.dropna()
    dd = s / s.cummax() - 1.0
    fig, ax = plt.subplots(figsize=(11, 3.4))
    ax.fill_between(dd.index, dd.values, 0.0, color="#9c2f2f", alpha=0.55)
    ax.plot(dd.index, dd.values, color="#7a1f1f", linewidth=1.0)
    ax.set_title(title)
    ax.set_ylabel("回撤")
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(lambda x, _pos: f"{x:.1%}")
    return _save(fig, path)
