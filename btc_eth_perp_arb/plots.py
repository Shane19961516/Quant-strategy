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


def plot_mae_mfe_scatter(
    trades: dict[str, pd.DataFrame],
    path: Path,
    *,
    liq_price_pct: float = 0.096,
    title: str = "Train MAE vs MFE (frozen 10x Donchian breakouts)",
) -> None:
    """Price-percent MAE/MFE scatter. Not an OOS chart."""
    _style()
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    colors = {"BTCUSDT": "#f7931a", "ETHUSDT": "#627eea"}
    for name, frame in trades.items():
        if frame is None or frame.empty:
            continue
        ax.scatter(
            frame["mae_price_pct"] * 100.0,
            frame["mfe_price_pct"] * 100.0,
            s=18,
            alpha=0.75,
            c=colors.get(name, "#555555"),
            label=name.replace("USDT", ""),
            zorder=3,
        )
    ax.axvline(2.0, color="#1f4e79", ls="--", lw=0.9, label="2% equity stop")
    ax.axvline(liq_price_pct * 100.0, color="#b22222", ls="--", lw=0.9, label="isolated 10x liq")
    ax.axhline(10.0, color="#2ca02c", ls=":", lw=0.9, label="1×投入 (10% price)")
    ax.axhline(50.0, color="#c45911", ls=":", lw=0.9, label="5×投入 (50% price)")
    ax.set_xlabel("MAE (% price)")
    ax.set_ylabel("MFE (% price)")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_tp_hit_bars(
    hit_rates: dict[str, dict],
    path: Path,
    stop_name: str = "2pct_equity",
    title: str = "Train: TP reached before 2% stop (same bar counts as stop)",
) -> None:
    """Grouped bars of tp_before_stop by symbol, for locked TP multiples."""
    _style()
    labels = [f"{m:g}×" for m in (0.2, 0.5, 1.0, 2.0, 3.0, 5.0)]
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    width = 0.36
    x = list(range(len(labels)))
    colors = {"BTCUSDT": "#f7931a", "ETHUSDT": "#627eea"}
    for i, (sym, table) in enumerate(hit_rates.items()):
        bucket = (table or {}).get(stop_name) or {}
        ys = [
            (bucket.get(k) or {}).get("tp_before_stop") or 0.0
            for k in ("0.2x", "0.5x", "1x", "2x", "3x", "5x")
        ]
        shift = -width / 2 if i == 0 else width / 2
        ax.bar(
            [xi + shift for xi in x],
            [y * 100.0 for y in ys],
            width=width,
            color=colors.get(sym, "#555555"),
            label=sym.replace("USDT", ""),
        )
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Share of train clips (%)")
    ax.set_xlabel("Take-profit as multiple of invested margin")
    ax.set_title(title)
    ax.legend(loc="best")
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
