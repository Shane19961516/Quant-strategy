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


def plot_named_equity(
    series: dict[str, pd.DataFrame],
    path: Path,
    title: str,
    colors: dict[str, str] | None = None,
) -> None:
    """Overlay several named equity paths (entry-rule comparison)."""
    _style()
    fig, ax = plt.subplots(figsize=(10, 4.4))
    default = ["#1f4e79", "#2ca02c", "#c45911", "#9467bd", "#7f7f7f"]
    for i, (name, frame) in enumerate(series.items()):
        color = (colors or {}).get(name, default[i % len(default)])
        ax.plot(frame["bar_open"], frame["equity"], color=color, lw=1.2, label=name)
    ax.set_title(title)
    ax.set_ylabel("Equity (USDT)")
    ax.set_xlabel("UTC")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_equity_overlay(
    original: pd.DataFrame,
    inverted: pd.DataFrame,
    path: Path,
    title: str = "Original vs inverted-signal equity (10x)",
) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(10, 4.4))
    ax.plot(original["bar_open"], original["equity"], color="#1f4e79", lw=1.2, label="original (mean-revert)")
    ax.plot(inverted["bar_open"], inverted["equity"], color="#c45911", lw=1.2, label="inverted (momentum / flipped side)")
    ax.set_title(title)
    ax.set_ylabel("Equity (USDT)")
    ax.set_xlabel("UTC")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_attribution_pair(
    original: pd.DataFrame,
    inverted: pd.DataFrame,
    path: Path,
) -> None:
    """Fees stay negative on both books; mark/spread path can flip."""
    _style()

    def parts(w: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
        deq = w["equity"].astype(float).diff().fillna(0.0)
        fees = -w["fee"].fillna(0.0)
        funding = w["funding_cash"].fillna(0.0)
        spread = (deq - w["funding_cash"].fillna(0.0) + w["fee"].fillna(0.0))
        return spread.cumsum(), fees.cumsum(), funding.cumsum()

    s0, f0, u0 = parts(original)
    s1, f1, u1 = parts(inverted)
    t = original["bar_open"]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    ax.plot(t, s0, color="#1f4e79", lw=1.1, label="original spread/slip")
    ax.plot(t, s1, color="#c45911", lw=1.1, label="inverted spread/slip")
    ax.plot(t, f0, color="#1f4e79", lw=1.0, ls="--", label="original fees (always ≤ 0)")
    ax.plot(t, f1, color="#c45911", lw=1.0, ls="--", label="inverted fees (always ≤ 0)")
    ax.plot(t, u0, color="#1f4e79", lw=0.8, ls=":", label="original funding")
    ax.plot(t, u1, color="#c45911", lw=0.8, ls=":", label="inverted funding")
    ax.set_title("Flipping the side does not flip fees")
    ax.set_ylabel("Cumulative USDT")
    ax.legend(loc="best", fontsize=8)
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
