"""Two locked forks: 2024 OOS on frozen book A, and 8h funding-carry.

Declared before looking at results:

Fork 1 window: 2024-01-01 → 2024-12-31 UTC, warmup 14 days (2023-12-18).
Book A frozen (7d residual z, 01:00 UTC, 2x, |z| in [2,4), exit 0.5).

Fork 2 factor: last-settled ETH minus BTC 8h funding, in bp.
Carry hypothesis: rich ETH funding → ETH underperforms BTC (IC < 0 vs
forward log-spread). Same slow 2x shell; residual cost hurdle off.
Train 2025-10-01→2026-06-30 decides keep/drop; 2026 OOS scores only.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from .config import CACHE_DIR, delivery_config, funding_carry_config, raw_spread_config
from .data import build_aligned_panel, load_panel, save_panel
from .eval_entry import (
    LAST_END,
    LAST_START,
    OOS_END,
    OOS_START,
    TRAIN_END,
    TRAIN_START,
    _clip,
    _end_ts,
    _ts,
    window_pack,
)
from .metrics import monthly_table
from .plots import plot_named_equity
from .signals import add_signals
from .simulator import run_simulator

OOS_2024_START = date(2024, 1, 1)
OOS_2024_END = date(2024, 12, 31)
WARMUP_2024_DAYS = 14
CACHE_2024 = "aligned_1m_2024.parquet"
CACHE_LONG = "aligned_1m_long.parquet"

KNOWN_A_2026 = {
    "train": 0.09612906698835198,
    "oos_2026": -0.01607141520766437,
    "last_month": -0.0038754185162549323,
    "full": 0.07851272163159242,
}


def load_or_build_2024():
    path = CACHE_DIR / CACHE_2024
    if path.exists():
        print(f"Loaded cache {path}", flush=True)
        return load_panel(name=CACHE_2024)
    print("Building 2024 panel from Vision (declared OOS 2024-01-01→2024-12-31)", flush=True)
    panel, manifest = build_aligned_panel(
        OOS_2024_START, OOS_2024_END, warmup_days=WARMUP_2024_DAYS
    )
    save_panel(panel, manifest, name=CACHE_2024)
    return panel, manifest


def rank_ic_daily(df: pd.DataFrame, signal: str, start: date, end: date, horizons=(1, 5), hour=1) -> dict:
    lo, hi = _ts(start), _end_ts(end)
    w = df[(df["bar_open_ts"] >= lo) & (df["bar_open_ts"] <= hi)].copy()
    minute = (w["bar_open_ts"] // 60_000) % 1440
    daily = w.loc[minute == hour * 60, [signal, "log_spread", "bar_open"]].copy()
    daily = daily.reset_index(drop=True)
    out = {"n_days": int(len(daily)), "hour_utc": hour, "signal": signal}
    for h in horizons:
        fwd = daily["log_spread"].shift(-h) - daily["log_spread"]
        pair = pd.DataFrame({"x": daily[signal], "y": fwd}).dropna()
        if len(pair) < 20:
            ic = float("nan")
        else:
            ic = float(pair["x"].rank().corr(pair["y"].rank()))
        out[f"ic_{h}d"] = ic
        out[f"n_{h}d"] = int(len(pair))
    return out


def run_book(panel: pd.DataFrame, cfg, start: date, end: date):
    end_ts = _end_ts(end)
    clipped = panel[panel["bar_open_ts"] <= end_ts].reset_index(drop=True)
    signaled = add_signals(clipped, cfg)
    result = run_simulator(signaled, cfg, trade_start_ts=_ts(start))
    stats = window_pack(result, start, end)
    monthly = monthly_table(result, pnl_start_ts=_ts(start))
    return result, stats, monthly, signaled


def fork1_2024(media: Path | None, summary_json: Path | None) -> dict:
    panel, manifest = load_or_build_2024()
    cfg = delivery_config()
    result, stats, monthly, _ = run_book(panel, cfg, OOS_2024_START, OOS_2024_END)
    charts = {}
    if media is not None:
        media.mkdir(parents=True, exist_ok=True)
        w = _clip(result.bars, OOS_2024_START, OOS_2024_END)
        p = media / "fork1-a-2024-equity.png"
        plot_named_equity({"A frozen 2024 OOS": w}, p, "Fork 1 — frozen book A, 2024 OOS (after costs)")
        charts["equity"] = str(p)
        p = media / "fork1-a-2024-drawdown.png"
        from .plots import plot_drawdown

        plot_drawdown(w, p)
        charts["drawdown"] = str(p)
        for path in charts.values():
            if not Path(path).exists():
                raise FileNotFoundError(path)
    payload = {
        "fork": "2024_oos_frozen_A",
        "declared_window": [OOS_2024_START.isoformat(), OOS_2024_END.isoformat()],
        "warmup_days": WARMUP_2024_DAYS,
        "cfg": {"signal_mode": cfg.signal_mode, "exit_z": cfg.exit_z, "entry_z": cfg.entry_z, "leverage": cfg.leverage},
        "oos_2024": stats,
        "monthly": monthly.to_dict(orient="records"),
        "compare_locked_2026": KNOWN_A_2026,
        "manifest_rows": manifest.get("rows"),
        "manifest_complete": manifest.get("complete_rows"),
        "charts": charts,
        "gate_c": bool(stats["equity_return"] > 0 and stats["liquidation_events"] == 0),
    }
    print(json.dumps({k: payload[k] for k in ("fork", "declared_window", "gate_c") if k in payload}, indent=2))
    print(
        json.dumps(
            {
                "oos_2024_return": stats["equity_return"],
                "trades": stats["trades"],
                "mdd": stats["max_drawdown"],
                "fees": stats["fee_sum"],
                "liq": stats["liquidation_events"],
                "ending_equity": stats["ending_equity"],
            },
            indent=2,
        ),
        flush=True,
    )
    if summary_json is not None:
        summary_json.parent.mkdir(parents=True, exist_ok=True)
        summary_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print("wrote", summary_json)
    return payload


def fork2_funding(media: Path | None, summary_json: Path | None) -> dict:
    panel, _manifest = load_panel(name=CACHE_LONG)
    cfg = funding_carry_config()
    end_ts = _end_ts(OOS_END)
    clipped = panel[panel["bar_open_ts"] <= end_ts].reset_index(drop=True)
    signaled = add_signals(clipped, cfg)
    ic_train = rank_ic_daily(signaled, "fund_diff", TRAIN_START, TRAIN_END, (1, 5))
    ic_1 = ic_train.get("ic_1d")
    ic_5 = ic_train.get("ic_5d")
    gate_a = bool(pd.notna(ic_1) and pd.notna(ic_5) and ic_1 < 0 and ic_5 < 0)
    print("FORK2 Gate A IC (train only, carry hypothesis IC<0)", json.dumps(ic_train, indent=2, default=str), flush=True)
    print("gate_a", gate_a, flush=True)

    payload = {
        "fork": "funding_carry",
        "factor": "last_settled ETH_funding - BTC_funding, z = diff * 1e4 (1bp=1)",
        "hypothesis": "positive fund_diff → ETH underperforms BTC; Spearman IC vs forward d_log_spread < 0",
        "ic_train": ic_train,
        "gate_a_pass": gate_a,
        "cfg": {
            "signal_mode": cfg.signal_mode,
            "cost_hurdle_bps": cfg.cost_hurdle_bps,
            "entry_z": cfg.entry_z,
            "exit_z": cfg.exit_z,
            "leverage": cfg.leverage,
            "entry_hour_utc": cfg.entry_hour_utc,
        },
        "book": None,
        "gate_c": False,
        "stop_reason": None,
        "charts": {},
    }
    if not gate_a:
        payload["stop_reason"] = "Gate A fail on train IC (wrong sign or undefined); no state-machine repair"
        if summary_json is not None:
            summary_json.parent.mkdir(parents=True, exist_ok=True)
            summary_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            print("wrote", summary_json)
        return payload

    result = run_simulator(signaled, cfg, trade_start_ts=_ts(TRAIN_START))
    windows = {
        "train": window_pack(result, TRAIN_START, TRAIN_END),
        "oos": window_pack(result, OOS_START, OOS_END),
        "last_month": window_pack(result, LAST_START, LAST_END),
        "full": window_pack(result, TRAIN_START, OOS_END),
    }
    monthly = monthly_table(result, pnl_start_ts=_ts(TRAIN_START))
    train_ok = windows["train"]["equity_return"] > 0 and windows["train"]["liquidation_events"] == 0
    if not train_ok:
        payload["stop_reason"] = "train after-cost equity return <= 0; OOS scored but not a candidate"
    payload["book"] = windows
    payload["monthly"] = monthly.to_dict(orient="records")
    payload["gate_c"] = bool(
        train_ok
        and windows["oos"]["equity_return"] > 0
        and windows["oos"]["liquidation_events"] == 0
    )
    print(
        json.dumps(
            {
                "train": windows["train"]["equity_return"],
                "oos": windows["oos"]["equity_return"],
                "last_month": windows["last_month"]["equity_return"],
                "trades_train": windows["train"]["trades"],
                "trades_oos": windows["oos"]["trades"],
                "liq": windows["full"]["liquidation_events"],
                "gate_c": payload["gate_c"],
            },
            indent=2,
        ),
        flush=True,
    )
    if media is not None:
        media.mkdir(parents=True, exist_ok=True)
        full = _clip(result.bars, TRAIN_START, OOS_END)
        p = media / "fork2-funding-equity.png"
        plot_named_equity({"F funding carry 2x": full}, p, "Fork 2 — 8h ETH−BTC funding carry (after costs)")
        payload["charts"]["equity"] = str(p)
        p = media / "fork2-funding-oos.png"
        plot_named_equity({"F funding carry 2x": _clip(result.bars, OOS_START, OOS_END)}, p, "Fork 2 OOS 2026-07-01→2026-09-18")
        payload["charts"]["oos"] = str(p)
        for path in payload["charts"].values():
            if not Path(path).exists():
                raise FileNotFoundError(path)
    if summary_json is not None:
        dump = {k: v for k, v in payload.items()}
        summary_json.parent.mkdir(parents=True, exist_ok=True)
        summary_json.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
        print("wrote", summary_json)
    return payload


def fork3_raw_spread(media: Path | None, summary_json: Path | None) -> dict:
    """Raw log(ETH/BTC) vs expanding long-run mean. Declared before 2026 OOS."""
    panel, _manifest = load_panel(name=CACHE_LONG)
    cfg = raw_spread_config()
    end_ts = _end_ts(OOS_END)
    clipped = panel[panel["bar_open_ts"] <= end_ts].reset_index(drop=True)
    signaled = add_signals(clipped, cfg)
    ic_train = rank_ic_daily(signaled, "z_raw", TRAIN_START, TRAIN_END, (1, 5))
    ic_1 = ic_train.get("ic_1d")
    ic_5 = ic_train.get("ic_5d")
    gate_a = bool(pd.notna(ic_1) and pd.notna(ic_5) and ic_1 < 0 and ic_5 < 0)
    print(
        "FORK3 Gate A IC (train only, raw-spread mean-revert IC<0)",
        json.dumps(ic_train, indent=2, default=str),
        flush=True,
    )
    print("gate_a", gate_a, flush=True)
    payload = {
        "fork": "raw_spread",
        "factor": "log(ETH_mark/BTC_mark) vs expanding mean/std (min_periods 30d, shift 1); not 7d residual z",
        "hypothesis": "ETH rich vs long-run BTC → subsequent underperformance; Spearman IC vs forward d_log_spread < 0",
        "entry_rule": "|z_raw| in [2, 4) at 01:00 UTC; exit |z_raw|<=0.5; stop 4; 5d hold; 30bp |S-μ_exp| hurdle; 2x",
        "ic_train": ic_train,
        "gate_a_pass": gate_a,
        "cfg": {
            "signal_mode": cfg.signal_mode,
            "raw_spread_min_periods": cfg.raw_spread_min_periods,
            "entry_z": cfg.entry_z,
            "exit_z": cfg.exit_z,
            "cost_hurdle_bps": cfg.cost_hurdle_bps,
            "leverage": cfg.leverage,
            "entry_hour_utc": cfg.entry_hour_utc,
        },
        "book": None,
        "gate_c": False,
        "stop_reason": None,
        "charts": {},
        "compare_A": KNOWN_A_2026,
    }
    if not gate_a:
        payload["stop_reason"] = "Gate A fail on train IC (wrong sign or undefined); no extra filters"
        if summary_json is not None:
            summary_json.parent.mkdir(parents=True, exist_ok=True)
            summary_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            print("wrote", summary_json)
        return payload

    result = run_simulator(signaled, cfg, trade_start_ts=_ts(TRAIN_START))
    windows = {
        "train": window_pack(result, TRAIN_START, TRAIN_END),
        "oos": window_pack(result, OOS_START, OOS_END),
        "last_month": window_pack(result, LAST_START, LAST_END),
        "full": window_pack(result, TRAIN_START, OOS_END),
    }
    monthly = monthly_table(result, pnl_start_ts=_ts(TRAIN_START))
    train_ok = windows["train"]["equity_return"] > 0 and windows["train"]["liquidation_events"] == 0
    if not train_ok:
        payload["stop_reason"] = "train after-cost equity return <= 0; OOS scored but not a candidate"
    payload["book"] = windows
    payload["monthly"] = monthly.to_dict(orient="records")
    payload["gate_c"] = bool(
        train_ok
        and windows["oos"]["equity_return"] > 0
        and windows["oos"]["liquidation_events"] == 0
    )
    print(
        json.dumps(
            {
                "train": windows["train"]["equity_return"],
                "oos": windows["oos"]["equity_return"],
                "last_month": windows["last_month"]["equity_return"],
                "trades_train": windows["train"]["trades"],
                "trades_oos": windows["oos"]["trades"],
                "trades_last": windows["last_month"]["trades"],
                "liq": windows["full"]["liquidation_events"],
                "gate_c": payload["gate_c"],
            },
            indent=2,
        ),
        flush=True,
    )
    if media is not None:
        media.mkdir(parents=True, exist_ok=True)
        p = media / "fork3-raw-spread-equity.png"
        plot_named_equity(
            {"G raw log-spread 2x": _clip(result.bars, TRAIN_START, OOS_END)},
            p,
            "Fork 3 — raw log(ETH/BTC) vs expanding mean (after costs)",
        )
        payload["charts"]["equity"] = str(p)
        p = media / "fork3-raw-spread-oos.png"
        plot_named_equity(
            {"G raw log-spread 2x": _clip(result.bars, OOS_START, OOS_END)},
            p,
            "Fork 3 OOS 2026-07-01→2026-09-18 (locked)",
        )
        payload["charts"]["oos"] = str(p)
        p = media / "fork3-raw-spread-last-month.png"
        plot_named_equity(
            {"G raw log-spread 2x": _clip(result.bars, LAST_START, LAST_END)},
            p,
            "Fork 3 last month (locked)",
        )
        payload["charts"]["last_month"] = str(p)
        for path in payload["charts"].values():
            if not Path(path).exists():
                raise FileNotFoundError(path)
    if summary_json is not None:
        summary_json.parent.mkdir(parents=True, exist_ok=True)
        summary_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print("wrote", summary_json)
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fork", choices=["2024", "funding", "spread", "both"], default="both")
    p.add_argument("--media-dir", default="")
    p.add_argument("--out-dir", default="")
    args = p.parse_args(argv)
    media = Path(args.media_dir) if args.media_dir else None
    out_dir = Path(args.out_dir) if args.out_dir else Path("/tmp")
    if args.fork in ("2024", "both"):
        fork1_2024(media, out_dir / "fork1-2024-oos.json")
    if args.fork in ("funding", "both"):
        fork2_funding(media, out_dir / "fork2-funding.json")
    if args.fork in ("spread", "both"):
        fork3_raw_spread(media, out_dir / "fork3-raw-spread.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
