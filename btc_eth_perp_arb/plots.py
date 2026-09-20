"""Charts for the last-month backtest (saved under the project media folder)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.grid": True,
            "grid.alpha": 0.25,
            "font.size": 10,
        }
    )


def plot_equity(window: pd.DataFrame, path: Path, title: str) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(window["bar_open"], window["equity"], color="#1f4e79", lw=1.1)
    ax.set_title(title)
    ax.set_ylabel("Equity (USDT)")
    ax.set_xlabel("UTC")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_drawdown(window: pd.DataFrame, path: Path) -> None:
    _style()
    eq = window["equity"].astype(float)
    dd = eq / eq.cummax() - 1.0
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.fill_between(window["bar_open"], dd * 100.0, 0.0, color="#b22222", alpha=0.45)
    ax.set_title("Equity drawdown")
    ax.set_ylabel("Drawdown (%)")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_z_and_pos(window: pd.DataFrame, path: Path) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(window["bar_open"], window["z"], color="#555555", lw=0.6, label="z (log ETH/BTC)")
    ax.axhline(2.0, color="#1f77b4", ls="--", lw=0.8)
    ax.axhline(-2.0, color="#1f77b4", ls="--", lw=0.8)
    ax.axhline(0.5, color="#aaaaaa", ls=":", lw=0.8)
    ax.axhline(-0.5, color="#aaaaaa", ls=":", lw=0.8)
    enters = window[window["event"] == "enter"]
    ax.scatter(enters["bar_open"], enters["z"], s=12, c="#2ca02c", label="enter", zorder=3)
    ax.set_title("Residual z-score (signal at t, traded t+1)")
    ax.set_ylabel("z")
    ax.legend(loc="upper right")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_attribution(window: pd.DataFrame, path: Path) -> None:
    _style()
    deq = window["equity"].astype(float).diff().fillna(0.0)
    fees = -window["fee"].fillna(0.0)
    funding = window["funding_cash"].fillna(0.0)
    spread = (deq - window["funding_cash"].fillna(0.0) + window["fee"].fillna(0.0)).cumsum()
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(window["bar_open"], spread, label="spread/mark/slippage (cum)", color="#1f77b4")
    ax.plot(window["bar_open"], funding.cumsum(), label="funding (cum)", color="#ff7f0e")
    ax.plot(window["bar_open"], fees.cumsum(), label="fees (cum)", color="#d62728")
    ax.plot(window["bar_open"], deq.cumsum(), label="net equity PnL (cum)", color="#000000", lw=1.2)
    ax.set_title("PnL attribution (cumulative, USDT)")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
