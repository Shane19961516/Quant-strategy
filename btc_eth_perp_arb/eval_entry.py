"""Locked one-knob comparisons on the slow 2x RV book.

Windows are fixed; 2026-07–09 and the last month are never searched.
`--compare exit` is the current step (A vs early-exit). `--compare entry`
reproduces the prior Gate A entry filter study.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .config import delivery_config
from .data import load_panel
from .metrics import monthly_table, summarize
from .plots import plot_drawdown, plot_named_equity, plot_z_and_pos
from .signals import add_signals
from .simulator import SimResult, run_simulator

TRAIN_START = date(2025, 10, 1)
TRAIN_END = date(2026, 6, 30)
OOS_START = date(2026, 7, 1)
OOS_END = date(2026, 9, 18)
LAST_START = date(2026, 8, 20)
LAST_END = date(2026, 9, 18)
FULL_START = TRAIN_START
FULL_END = OOS_END

WINDOWS = {
    "full": (FULL_START, FULL_END),
    "train": (TRAIN_START, TRAIN_END),
    "oos": (OOS_START, OOS_END),
    "last_month": (LAST_START, LAST_END),
}

LABELS = {
    "A_baseline": "A baseline exit 0.5",
    "B_confirm": "B confirm (shrink vs 1d)",
    "C_quality": "C quality |z|>=2.5",
    "D_exit1": "D exit |z|<=1.0",
}
COLORS = {
    "A_baseline": "#1f4e79",
    "B_confirm": "#2ca02c",
    "C_quality": "#c45911",
    "D_exit1": "#6a3d9a",
}


def _ts(d: date, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def _end_ts(d: date) -> int:
    return _ts(d, 23, 59)


def locked_entry_variants() -> dict:
    return {
        "A_baseline": delivery_config(),
        "B_confirm": delivery_config(require_reversion=True),
        "C_quality": delivery_config(entry_z=2.5),
    }


def locked_exit_variants() -> dict:
    """Book A frozen; only exit_z 0.5 -> 1.0."""
    return {
        "A_baseline": delivery_config(),
        "D_exit1": delivery_config(exit_z=1.0),
    }


def locked_variants(compare: str = "exit") -> dict:
    if compare == "entry":
        return locked_entry_variants()
    if compare == "exit":
        return locked_exit_variants()
    raise ValueError(f"unknown compare={compare}")


def slice_result(result: SimResult, start: date, end: date) -> SimResult:
    lo, hi = _ts(start), _end_ts(end)
    bars = result.bars[(result.bars["bar_open_ts"] >= lo) & (result.bars["bar_open_ts"] <= hi)].copy()
    trades = result.trades
    if trades is None or trades.empty:
        tw = trades
    else:
        tw = trades[(trades["bar_open_ts"] >= lo) & (trades["bar_open_ts"] <= hi)].copy()
    return SimResult(bars=bars, trades=tw, summary=dict(result.summary))


def window_pack(result: SimResult, start: date, end: date) -> dict:
    sliced = slice_result(result, start, end)
    stats = summarize(sliced, pnl_start_ts=_ts(start), starting_equity=100_000.0)
    stats["window_start"] = start.isoformat()
    stats["window_end"] = end.isoformat()
    return stats


def run_variant(panel: pd.DataFrame, cfg, trade_start: date) -> SimResult:
    return run_simulator(panel, cfg, trade_start_ts=_ts(trade_start))


def evaluate(panel: pd.DataFrame, compare: str = "exit") -> dict:
    variants = locked_variants(compare)
    signaled = add_signals(panel, delivery_config())
    out: dict = {
        "compare": compare,
        "variants": {},
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
    }
    bars_by_name: dict[str, pd.DataFrame] = {}
    for name, cfg in variants.items():
        print(
            f"sim {name} require_reversion={cfg.require_reversion} "
            f"entry_z={cfg.entry_z} exit_z={cfg.exit_z}",
            flush=True,
        )
        result = run_variant(signaled, cfg, FULL_START)
        pack = {wname: window_pack(result, a, b) for wname, (a, b) in WINDOWS.items()}
        monthly = monthly_table(result, pnl_start_ts=_ts(FULL_START))
        out["variants"][name] = {
            "cfg": {
                "require_reversion": cfg.require_reversion,
                "entry_z": cfg.entry_z,
                "exit_z": cfg.exit_z,
                "stop_z": cfg.stop_z,
                "leverage": cfg.leverage,
                "entry_hour_utc": cfg.entry_hour_utc,
                "z_window": cfg.z_window,
            },
            "windows": pack,
            "monthly": monthly.to_dict(orient="records"),
        }
        bars_by_name[name] = result.bars
        print(
            json.dumps(
                {
                    "name": name,
                    "full": pack["full"]["equity_return"],
                    "train": pack["train"]["equity_return"],
                    "oos": pack["oos"]["equity_return"],
                    "last_month": pack["last_month"]["equity_return"],
                    "full_trades": pack["full"]["trades"],
                    "train_trades": pack["train"]["trades"],
                    "oos_trades": pack["oos"]["trades"],
                    "last_trades": pack["last_month"]["trades"],
                    "liq": pack["full"]["liquidation_events"],
                    "exit_reasons": pack["full"]["exit_reasons"],
                },
                indent=2,
            ),
            flush=True,
        )
    train_rets = {n: v["windows"]["train"]["equity_return"] for n, v in out["variants"].items()}
    out["train_rank"] = sorted(train_rets, key=train_rets.get, reverse=True)
    chosen = out["train_rank"][0]
    chosen_oos = out["variants"][chosen]["windows"]["oos"]["equity_return"]
    chosen_liq = out["variants"][chosen]["windows"]["oos"]["liquidation_events"]
    out["chosen_on_train"] = chosen
    out["gate_c"] = {
        "passed": bool(chosen_oos > 0 and chosen_liq == 0),
        "rule": "pre-locked OOS equity return after costs must be > 0 with 0 liquidations",
        "oos_returns": {n: v["windows"]["oos"]["equity_return"] for n, v in out["variants"].items()},
        "chosen": chosen,
    }
    out["bars_by_name"] = bars_by_name
    return out


def _clip(bars: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    lo, hi = _ts(start), _end_ts(end)
    return bars[(bars["bar_open_ts"] >= lo) & (bars["bar_open_ts"] <= hi)]


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    compare = eval_out.get("compare", "exit")
    prefix = "delivery-exit" if compare == "exit" else "delivery-entry"
    names = list(bars)
    labeled = {LABELS.get(k, k): _clip(v, FULL_START, FULL_END) for k, v in bars.items()}
    color_map = {LABELS.get(k, k): COLORS.get(k, "#333333") for k in names}
    paths: dict[str, str] = {}

    p = media / f"{prefix}-equity-overlay.png"
    plot_named_equity(
        labeled,
        p,
        f"Slow RV 2x — {compare} variants (path-dependent, after costs)",
        colors=color_map,
    )
    paths["equity_overlay"] = str(p)

    oos_map = {LABELS.get(k, k): _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / f"{prefix}-oos.png"
    plot_named_equity(
        oos_map,
        p,
        "OOS 2026-07-01 → 2026-09-18 (locked, not searched)",
        colors=color_map,
    )
    paths["oos"] = str(p)

    last_map = {LABELS.get(k, k): _clip(v, LAST_START, LAST_END) for k, v in bars.items()}
    p = media / f"{prefix}-last-month.png"
    plot_named_equity(
        last_map,
        p,
        "Last month 2026-08-20 → 2026-09-18 (locked)",
        colors=color_map,
    )
    paths["last_month"] = str(p)

    base_full = _clip(bars["A_baseline"], FULL_START, FULL_END)
    p = media / "delivery-slow-rv-equity.png"
    plot_named_equity({"A baseline 2x": base_full}, p, "Slow RV baseline 2x equity (7d z, 01:00 UTC)")
    paths["baseline_equity"] = str(p)
    p = media / "delivery-slow-rv-drawdown.png"
    plot_drawdown(base_full, p)
    paths["baseline_dd"] = str(p)
    p = media / "delivery-slow-rv-last-month.png"
    plot_named_equity(
        {"A baseline 2x": _clip(bars["A_baseline"], LAST_START, LAST_END)},
        p,
        "Baseline last month",
    )
    paths["baseline_last"] = str(p)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .plots import _style

    w = base_full
    _style()
    fig, ax = plt.subplots(figsize=(10, 4.4))
    ax.plot(w["bar_open"], w["z"], color="#888888", lw=0.5, label="z")
    ax.axhline(2.0, color="#1f4e79", ls="--", lw=0.8)
    ax.axhline(-2.0, color="#1f4e79", ls="--", lw=0.8)
    ax.axhline(1.0, color="#6a3d9a", ls=":", lw=0.8)
    ax.axhline(-1.0, color="#6a3d9a", ls=":", lw=0.8)
    ax.axhline(0.5, color="#aaaaaa", ls=":", lw=0.8)
    ax.axhline(-0.5, color="#aaaaaa", ls=":", lw=0.8)
    for name in names:
        e = _clip(bars[name], FULL_START, FULL_END)
        enters = e[e["event"] == "enter"]
        exits = e[e["event"] == "exit"]
        ax.scatter(
            enters["bar_open"],
            enters["z"],
            s=18,
            c=COLORS.get(name, "#333"),
            marker="o",
            label=f"{LABELS.get(name, name)} enter",
            zorder=3,
        )
        ax.scatter(
            exits["bar_open"],
            exits["z"],
            s=18,
            c=COLORS.get(name, "#333"),
            marker="x",
            label=f"{LABELS.get(name, name)} exit",
            zorder=3,
        )
    ax.set_title("Enter/exit vs 7d residual z (signal at t, fill t+1)")
    ax.set_ylabel("z")
    ax.legend(loc="upper right", fontsize=7)
    fig.autofmt_xdate()
    fig.tight_layout()
    p = media / f"{prefix}-z-events.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths["z_events"] = str(p)

    chosen = eval_out["chosen_on_train"]
    p = media / f"{prefix}-chosen-z.png"
    plot_z_and_pos(_clip(bars[chosen], FULL_START, FULL_END), p)
    paths["chosen_z"] = str(p)
    return paths


def jsonable(eval_out: dict) -> dict:
    return {k: v for k, v in eval_out.items() if k != "bars_by_name"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--media-dir", default="")
    p.add_argument("--summary-json", default="")
    p.add_argument("--compare", choices=["entry", "exit"], default="exit")
    args = p.parse_args(argv)

    panel, _manifest = load_panel(name="aligned_1m_long.parquet")
    end_ts = _end_ts(FULL_END)
    panel = panel[panel["bar_open_ts"] <= end_ts].reset_index(drop=True)
    eval_out = evaluate(panel, compare=args.compare)
    paths = {}
    if args.media_dir:
        paths = write_charts(eval_out, Path(args.media_dir))
        eval_out["charts"] = paths
        print("charts", json.dumps(paths, indent=2))
        for path in paths.values():
            if not Path(path).exists():
                raise FileNotFoundError(path)
    payload = jsonable(eval_out)
    if args.summary_json:
        out = Path(args.summary_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
