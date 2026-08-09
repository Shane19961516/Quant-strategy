"""图表输出。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from config import UNIVERSE


def _setup_font():
    plt.rcParams["axes.unicode_minus"] = False
    # 图内标签使用英文，避免环境缺中文字体告警
    plt.rcParams["font.family"] = "DejaVu Sans"


def plot_nav(nav: pd.Series, benchmarks: dict, out: Path):
    _setup_font()
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(nav.index, nav.values, label="Strategy", lw=2.0, color="#1f77b4")
    for name, s in benchmarks.items():
        s = s.reindex(nav.index).ffill()
        ax.plot(s.index, s.values, label=name, lw=1.2, alpha=0.85)
    ax.set_title("Multi-Asset Rotation NAV")
    ax.set_ylabel("NAV")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_drawdown(nav: pd.Series, out: Path):
    _setup_font()
    dd = nav / nav.cummax() - 1
    fig, ax = plt.subplots(figsize=(12, 3.8))
    ax.fill_between(dd.index, dd.values, 0, color="#d62728", alpha=0.35)
    ax.plot(dd.index, dd.values, color="#d62728", lw=1)
    ax.set_title("Strategy Drawdown")
    ax.set_ylabel("Drawdown")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_weights(weights_daily: pd.DataFrame, out: Path, freq: str = "W-FRI"):
    _setup_font()
    w = weights_daily.resample(freq).last().dropna(how="all")
    # 聚合美股
    us = [c for c in ["513500", "513110", "513400"] if c in w.columns]
    plot_df = pd.DataFrame(
        {
            "Bond": w.get("159816", 0),
            "Gold": w.get("159934", 0),
            "CN DivLowVol": w.get("515450", 0),
            "US Equity": w[us].sum(axis=1) if us else 0,
        },
        index=w.index,
    )
    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.stackplot(
        plot_df.index,
        plot_df.T.values,
        labels=plot_df.columns,
        alpha=0.9,
        colors=["#7f7f7f", "#ffbf00", "#2ca02c", "#1f77b4"],
    )
    ax.set_ylim(0, 1)
    ax.set_title("Sleeve Weights Over Time")
    ax.legend(loc="upper left", ncol=4)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_contribution(contrib: pd.DataFrame, out: Path):
    _setup_font()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    colors = np.where(contrib["cum_contribution"] >= 0, "#2ca02c", "#d62728")
    ax.barh(contrib["name"], contrib["cum_contribution"], color=colors)
    ax.axvline(0, color="black", lw=0.8)
    ax.set_title("Cumulative Return Contribution by Asset")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
