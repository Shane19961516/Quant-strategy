"""Locked Gate-A entry comparison on the slow 2x RV book.

Three pre-declared books, same exits/hold/costs/leverage. Train vs OOS
windows are fixed; 2026-07–09 and the last month are not searched.
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


def _ts(d: date, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


def _end_ts(d: date) -> int:
    return _ts(d, 23, 59)


def locked_variants() -> dict:
    """Entry-only variants. Names and knobs locked before looking at OOS."""
    return {
        "A_baseline": delivery_config(),
        "B_confirm": delivery_config(require_reversion=True),
        "C_quality": delivery_config(entry_z=2.5),
    }


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


def evaluate(panel: pd.DataFrame) -> dict:
    variants = locked_variants()
    signaled = add_signals(panel, delivery_config())
    out: dict = {"variants": {}, "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()}}
    bars_by_name: dict[str, pd.DataFrame] = {}
    for name, cfg in variants.items():
        print(f"sim {name} require_reversion={cfg.require_reversion} entry_z={cfg.entry_z}", flush=True)
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
                },
                indent=2,
            ),
            flush=True,
        )
    # Train-only ranking (do not pick on OOS).
    train_rets = {n: v["windows"]["train"]["equity_return"] for n, v in out["variants"].items()}
    out["train_rank"] = sorted(train_rets, key=train_rets.get, reverse=True)
    out["gate_c"] = {
        "passed": False,
        "rule": "pre-locked OOS equity return after costs must be > 0 with 0 liquidations",
        "oos_returns": {n: v["windows"]["oos"]["equity_return"] for n, v in out["variants"].items()},
    }
    any_oos_pos = any(r > 0 for r in out["gate_c"]["oos_returns"].values())
    any_liq = any(v["windows"]["oos"]["liquidation_events"] for v in out["variants"].values())
    out["gate_c"]["passed"] = bool(any_oos_pos and not any_liq)
    # Honest: Gate C requires the *chosen* (train-best) rule, not any lucky variant.
    chosen = out["train_rank"][0]
    chosen_oos = out["variants"][chosen]["windows"]["oos"]["equity_return"]
    chosen_liq = out["variants"][chosen]["windows"]["oos"]["liquidation_events"]
    out["chosen_on_train"] = chosen
    out["gate_c"]["chosen"] = chosen
    out["gate_c"]["passed"] = bool(chosen_oos > 0 and chosen_liq == 0)
    out["bars_by_name"] = bars_by_name  # stripped before JSON dump
    return out


def _clip(bars: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    lo, hi = _ts(start), _end_ts(end)
    return bars[(bars["bar_open_ts"] >= lo) & (bars["bar_open_ts"] <= hi)]


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    colors = {"A_baseline": "#1f4e79", "B_confirm": "#2ca02c", "C_quality": "#c45911"}
    labels = {
        "A_baseline": "A baseline |z|≥2",
        "B_confirm": "B confirm (shrink vs 1d)",
        "C_quality": "C quality |z|≥2.5",
    }
    paths: dict[str, str] = {}

    full_map = {labels[k]: _clip(v, FULL_START, FULL_END) for k, v in bars.items()}
    p = media / "delivery-entry-equity-overlay.png"
    plot_named_equity(full_map, p, "Slow RV 2x — entry variants (path-dependent, after costs)", colors={labels[k]: colors[k] for k in bars})
    paths["equity_overlay"] = str(p)

    oos_map = {labels[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / "delivery-entry-oos.png"
    plot_named_equity(oos_map, p, "OOS 2026-07-01 → 2026-09-18 (locked, not searched)", colors={labels[k]: colors[k] for k in bars})
    paths["oos"] = str(p)

    last_map = {labels[k]: _clip(v, LAST_START, LAST_END) for k, v in bars.items()}
    p = media / "delivery-entry-last-month.png"
    plot_named_equity(last_map, p, "Last month 2026-08-20 → 2026-09-18 (locked)", colors={labels[k]: colors[k] for k in bars})
    paths["last_month"] = str(p)

    base_full = _clip(bars["A_baseline"], FULL_START, FULL_END)
    p = media / "delivery-slow-rv-equity.png"
    plot_named_equity({"A baseline 2x": base_full}, p, "Slow RV baseline 2x equity (7d z, 01:00 UTC)")
    paths["baseline_equity"] = str(p)
    p = media / "delivery-slow-rv-drawdown.png"
    plot_drawdown(base_full, p)
    paths["baseline_dd"] = str(p)
    p = media / "delivery-slow-rv-last-month.png"
    plot_named_equity({"A baseline 2x": _clip(bars["A_baseline"], LAST_START, LAST_END)}, p, "Baseline last month")
    paths["baseline_last"] = str(p)

    # z with all three entry sets
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
    ax.axhline(2.5, color="#c45911", ls=":", lw=0.8)
    ax.axhline(-2.5, color="#c45911", ls=":", lw=0.8)
    for name, color, marker in (
        ("A_baseline", colors["A_baseline"], "o"),
        ("B_confirm", colors["B_confirm"], "s"),
        ("C_quality", colors["C_quality"], "^"),
    ):
        e = _clip(bars[name], FULL_START, FULL_END)
        e = e[e["event"] == "enter"]
        ax.scatter(e["bar_open"], e["z"], s=18, c=color, marker=marker, label=labels[name], zorder=3)
    ax.set_title("Entries vs 7d residual z (signal at t, fill t+1 01:00 UTC)")
    ax.set_ylabel("z")
    ax.legend(loc="upper right", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    p = media / "delivery-entry-z-entries.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths["z_entries"] = str(p)

    chosen = eval_out["chosen_on_train"]
    p = media / "delivery-entry-chosen-z.png"
    plot_z_and_pos(_clip(bars[chosen], FULL_START, FULL_END), p)
    paths["chosen_z"] = str(p)
    return paths


def jsonable(eval_out: dict) -> dict:
    dump = {k: v for k, v in eval_out.items() if k != "bars_by_name"}
    return dump


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--media-dir", default="")
    p.add_argument("--summary-json", default="")
    args = p.parse_args(argv)

    panel, _manifest = load_panel(name="aligned_1m_long.parquet")
    end_ts = _end_ts(FULL_END)
    panel = panel[panel["bar_open_ts"] <= end_ts].reset_index(drop=True)
    eval_out = evaluate(panel)
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
