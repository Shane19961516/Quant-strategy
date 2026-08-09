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
    name_map = {
        "地方债0-4Y": "Bond 159816",
        "黄金": "Gold 159934",
        "红利低波": "CN DivLowVol",
        "标普500": "S&P500",
        "纳斯达克100": "Nasdaq100",
        "道琼斯": "Dow",
    }
    labels = [name_map.get(n, n) for n in contrib["name"]]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    colors = np.where(contrib["cum_contribution"] >= 0, "#2ca02c", "#d62728")
    ax.barh(labels, contrib["cum_contribution"], color=colors)
    ax.axvline(0, color="black", lw=0.8)
    ax.set_title("Cumulative Return Contribution by Asset")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_yearly_bars(yearly: pd.Series, out: Path):
    _setup_font()
    y = yearly.copy()
    fig, ax = plt.subplots(figsize=(10, 4.5))
    colors = np.where(y.values >= 0, "#2ca02c", "#d62728")
    ax.bar([str(i) for i in y.index], y.values * 100, color=colors, alpha=0.85)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Yearly Returns (%)")
    ax.set_ylabel("%")
    ax.grid(True, axis="y", alpha=0.3)
    for i, v in enumerate(y.values):
        ax.text(i, v * 100 + (0.4 if v >= 0 else -0.8), f"{v*100:.1f}%", ha="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_monthly_heatmap(nav: pd.Series, out: Path) -> pd.DataFrame:
    _setup_font()
    ret = nav.pct_change().fillna(0.0)
    monthly = ret.groupby([ret.index.year, ret.index.month]).apply(lambda x: (1 + x).prod() - 1)
    mat = monthly.unstack(level=1).reindex(columns=range(1, 13))
    mat.columns = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    fig, ax = plt.subplots(figsize=(12, 4.8))
    data = mat.values * 100
    vmax = np.nanmax(np.abs(data)) if np.isfinite(data).any() else 1.0
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(12))
    ax.set_xticklabels(mat.columns)
    ax.set_yticks(range(len(mat.index)))
    ax.set_yticklabels([str(y) for y in mat.index])
    ax.set_title("Monthly Return Heatmap (%)")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return mat


def plot_monthly_timeline(nav: pd.Series, out: Path):
    _setup_font()
    ret = nav.pct_change().fillna(0.0)
    monthly = ret.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    fig, ax = plt.subplots(figsize=(12, 3.8))
    colors = np.where(monthly.values >= 0, "#2ca02c", "#d62728")
    ax.bar(monthly.index, monthly.values * 100, width=20, color=colors, alpha=0.8)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Monthly Returns Timeline (%)")
    ax.set_ylabel("%")
    ax.grid(True, axis="y", alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_month_seasonality(nav: pd.Series, out: Path) -> pd.Series:
    _setup_font()
    ret = nav.pct_change().fillna(0.0)
    monthly = ret.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    by_month = monthly.groupby(monthly.index.month).mean()
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    colors = np.where(by_month.values >= 0, "#2ca02c", "#d62728")
    ax.bar([labels[m - 1] for m in by_month.index], by_month.values * 100, color=colors, alpha=0.85)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Average Monthly Seasonality (%)")
    ax.set_ylabel("%")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return by_month


def plot_yearly_compare(yearly_map: dict, out: Path):
    """yearly_map: {label: pd.Series(year -> return)}"""
    _setup_font()
    labels = list(yearly_map.keys())
    years = sorted(set().union(*[set(s.index) for s in yearly_map.values()]))
    x = np.arange(len(years))
    width = 0.8 / max(len(labels), 1)
    fig, ax = plt.subplots(figsize=(12, 4.8))
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
    for i, lab in enumerate(labels):
        vals = [float(yearly_map[lab].get(y, np.nan)) * 100 for y in years]
        ax.bar(x + i * width, vals, width=width, label=lab, color=colors[i % len(colors)], alpha=0.9)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x + width * (len(labels) - 1) / 2)
    ax.set_xticklabels([str(y) for y in years])
    ax.set_ylabel("%")
    ax.set_title("Yearly Return Comparison")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


ASSET_LABELS = {
    "159816": "Bond",
    "159934": "Gold",
    "515450": "CN DivLowVol",
    "513500": "S&P500",
    "513110": "Nasdaq100",
    "513400": "Dow",
}


def plot_asset_yearly_bars(asset_yearly: pd.DataFrame, out: Path, strategy_yearly: pd.Series | None = None):
    """同年各资产收益分组柱状图。"""
    _setup_font()
    df = asset_yearly.copy()
    if strategy_yearly is not None:
        df = df.copy()
        df["Strategy"] = strategy_yearly.reindex(df.index)
    labels = [ASSET_LABELS.get(c, c) if c != "Strategy" else "Strategy" for c in df.columns]
    years = list(df.index)
    x = np.arange(len(years))
    n = len(df.columns)
    width = 0.8 / max(n, 1)
    colors = ["#7f7f7f", "#ffbf00", "#2ca02c", "#1f77b4", "#9467bd", "#8c564b", "#d62728"]
    fig, ax = plt.subplots(figsize=(14, 5.2))
    for i, (col, lab) in enumerate(zip(df.columns, labels)):
        vals = df[col].values * 100
        ax.bar(x + i * width, vals, width=width, label=lab, color=colors[i % len(colors)], alpha=0.9)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x + width * (n - 1) / 2)
    ax.set_xticklabels([str(y) for y in years])
    ax.set_ylabel("%")
    ax.set_title("Same-Year Return Comparison by Asset")
    ax.legend(ncol=min(n, 4), loc="upper left")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_asset_yearly_heatmap(asset_yearly: pd.DataFrame, out: Path, strategy_yearly: pd.Series | None = None):
    """同年各资产收益热力图（行=年份，列=资产）。"""
    _setup_font()
    df = asset_yearly.copy()
    if strategy_yearly is not None:
        df["Strategy"] = strategy_yearly.reindex(df.index)
    labels = [ASSET_LABELS.get(c, c) if c != "Strategy" else "Strategy" for c in df.columns]
    data = df.values * 100
    fig, ax = plt.subplots(figsize=(12, 4.8))
    finite = data[np.isfinite(data)]
    vmax = float(np.nanmax(np.abs(finite))) if len(finite) else 1.0
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_yticks(range(len(df.index)))
    ax.set_yticklabels([str(y) for y in df.index])
    ax.set_title("Same-Year Asset Return Heatmap (%)")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_yearly_contribution_stacked(yearly_contrib: pd.DataFrame, out: Path):
    """策略持仓下，各资产分年贡献堆叠图。"""
    _setup_font()
    df = yearly_contrib.drop(columns=["StrategyApprox"], errors="ignore").copy()
    labels = [ASSET_LABELS.get(c, c) for c in df.columns]
    years = [str(y) for y in df.index]
    colors = ["#7f7f7f", "#ffbf00", "#2ca02c", "#1f77b4", "#9467bd", "#8c564b"]
    fig, ax = plt.subplots(figsize=(12, 5.0))
    bottom_pos = np.zeros(len(df))
    bottom_neg = np.zeros(len(df))
    x = np.arange(len(df))
    for i, (col, lab) in enumerate(zip(df.columns, labels)):
        vals = df[col].values * 100
        pos = np.where(vals >= 0, vals, 0.0)
        neg = np.where(vals < 0, vals, 0.0)
        ax.bar(x, pos, bottom=bottom_pos, label=lab, color=colors[i % len(colors)], width=0.7)
        ax.bar(x, neg, bottom=bottom_neg, color=colors[i % len(colors)], width=0.7)
        bottom_pos += pos
        bottom_neg += neg
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(years)
    ax.set_ylabel("Contribution (pp, arithmetic)")
    ax.set_title("Strategy Yearly Return Contribution by Asset")
    ax.legend(ncol=3, loc="upper left")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_yearly_contribution_heatmap(yearly_contrib: pd.DataFrame, out: Path):
    _setup_font()
    df = yearly_contrib.drop(columns=["StrategyApprox"], errors="ignore").copy()
    labels = [ASSET_LABELS.get(c, c) for c in df.columns]
    data = df.values * 100
    fig, ax = plt.subplots(figsize=(12, 4.8))
    finite = data[np.isfinite(data)]
    vmax = float(np.nanmax(np.abs(finite))) if len(finite) else 1.0
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_yticks(range(len(df.index)))
    ax.set_yticklabels([str(y) for y in df.index])
    ax.set_title("Strategy Yearly Contribution Heatmap (pp)")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
