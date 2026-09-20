"""Locked 1x leverage on two frozen shells. No other knobs.

Declared before looking at this step's results:

1. Frozen book A: 7d residual z, daily 01:00 UTC, 30bp hurdle, $100k.
   Baseline 2x → 1x. Gross notional halves.
2. Frozen composite C1: 1h four-family MA/slope/volume/position,
   |z|≥1 band-exit, 1/10 isolated margin, 2% stop, $1000, BTC/ETH separate.
   Baseline 10x → 1x. Notional falls from ~1× equity to ~0.1× equity.

Keep 1x if train after-cost equity return > 0 (not vs the leveraged
baseline — 1x has smaller |return| by construction). Do not search OOS
or last month. Gate C if kept and pre-locked OOS > 0 after costs and 0 liq.
1x is not a magic save if the edge is negative or fees still dominate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .composite import add_composite, run_composite
from .config import (
    COMPOSITE_BAR_MINUTES_1H,
    COMPOSITE_ENTRY_Z,
    COMPOSITE_EQUITY,
    COMPOSITE_FRACTION,
    COMPOSITE_LEVERAGE,
    COMPOSITE_LEVERAGE_1X,
    COMPOSITE_STOP_PRICE_PCT,
    COMPOSITE_SYMBOLS,
    COMPOSITE_WINDOW,
    DELIVERY_ENTRY_HOUR_UTC,
    DELIVERY_LEVERAGE,
    DELIVERY_LEVERAGE_1X,
    STARTING_EQUITY,
    composite_hourly_1x_config,
    composite_hourly_config,
    delivery_1x_config,
    delivery_config,
)
from .data import load_panel, resample_panel
from .eval_entry import (
    FULL_END,
    FULL_START,
    OOS_END,
    OOS_START,
    WINDOWS,
    _clip,
    _end_ts,
    _ts,
    run_variant,
    window_pack,
)
from .metrics import monthly_table
from .plots import plot_named_equity
from .signals import add_signals

CACHE_LONG = "aligned_1m_long.parquet"

A_LABELS = {
    "A_x2": "A 2x (frozen)",
    "A_x1": "A 1x",
}
A_COLORS = {
    "A 2x (frozen)": "#1f4e79",
    "A 1x": "#2ca02c",
}
C_LABELS = {
    "BTCUSDT_x10": "BTC C1 10x (frozen)",
    "BTCUSDT_x1": "BTC C1 1x",
    "ETHUSDT_x10": "ETH C1 10x (frozen)",
    "ETHUSDT_x1": "ETH C1 1x",
}
C_COLORS = {
    "BTC C1 10x (frozen)": "#c47a20",
    "BTC C1 1x": "#f7931a",
    "ETH C1 10x (frozen)": "#8a9bd7",
    "ETH C1 1x": "#627eea",
}
A_ORDER = ("A_x2", "A_x1")
C_ORDER = ("BTCUSDT_x10", "BTCUSDT_x1", "ETHUSDT_x10", "ETHUSDT_x1")


def _c_name(symbol: str, lev: float) -> str:
    return f"{symbol}_x{int(lev)}"


def _gate(train_r: float, oos_r: float, oos_liq: int, full_liq: int) -> dict:
    keep = bool(train_r > 0)
    return {
        "passed": bool(keep and oos_r > 0 and oos_liq == 0 and full_liq == 0),
        "keep": keep,
        "train": train_r,
        "oos": oos_r,
        "oos_liq": oos_liq,
        "full_liq": full_liq,
    }


def evaluate(panel) -> dict:
    out: dict = {
        "step": "leverage_1x",
        "shells": {
            "A": {
                "z_window": delivery_config().z_window,
                "entry_hour_utc": DELIVERY_ENTRY_HOUR_UTC,
                "leverage_baseline": DELIVERY_LEVERAGE,
                "leverage_1x": DELIVERY_LEVERAGE_1X,
                "starting_equity": STARTING_EQUITY,
            },
            "C1": {
                "bar_minutes": COMPOSITE_BAR_MINUTES_1H,
                "window": COMPOSITE_WINDOW,
                "entry_z": COMPOSITE_ENTRY_Z,
                "fraction": COMPOSITE_FRACTION,
                "leverage_baseline": COMPOSITE_LEVERAGE,
                "leverage_1x": COMPOSITE_LEVERAGE_1X,
                "stop_price_pct": COMPOSITE_STOP_PRICE_PCT,
                "starting_equity": COMPOSITE_EQUITY,
                "flatten_in_band": True,
                "note": "frozen C1 1h waterfall shell; not C2 hold-to-opposite",
            },
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "keep_rule": "1x train after-cost equity return > 0 (not vs leveraged baseline)",
        "gate_c_rule": "kept 1x and pre-locked OOS > 0 after costs and 0 liquidations",
        "variants": {},
    }
    bars_by_name: dict = {}

    signaled_a = add_signals(panel, delivery_config())
    for name, cfg in (("A_x2", delivery_config()), ("A_x1", delivery_1x_config())):
        print(f"sim {name} lev={cfg.leverage}", flush=True)
        result = run_variant(signaled_a, cfg, FULL_START)
        pack = {
            wname: window_pack(result, a, b, starting_equity=STARTING_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        out["variants"][name] = {
            "book": "A",
            "cfg": {
                "leverage": cfg.leverage,
                "z_window": cfg.z_window,
                "entry_hour_utc": cfg.entry_hour_utc,
                "entry_z": cfg.entry_z,
                "exit_z": cfg.exit_z,
                "cost_hurdle_bps": cfg.cost_hurdle_bps,
                "starting_equity": cfg.starting_equity,
            },
            "windows": pack,
            "monthly": monthly_table(result, pnl_start_ts=_ts(FULL_START)).to_dict(
                orient="records"
            ),
        }
        bars_by_name[name] = result.bars
        print(
            json.dumps(
                {
                    "name": name,
                    "train": pack["train"]["equity_return"],
                    "oos": pack["oos"]["equity_return"],
                    "last_month": pack["last_month"]["equity_return"],
                    "train_trades": pack["train"]["trades"],
                    "oos_trades": pack["oos"]["trades"],
                    "fees": pack["full"]["fee_sum"],
                    "spread": pack["full"]["spread_like_pnl"],
                    "end_eq": pack["full"]["ending_equity"],
                    "liq": pack["full"]["liquidation_events"],
                },
                indent=2,
            ),
            flush=True,
        )

    a1 = out["variants"]["A_x1"]
    a1_gate = _gate(
        a1["windows"]["train"]["equity_return"],
        a1["windows"]["oos"]["equity_return"],
        a1["windows"]["oos"]["liquidation_events"],
        a1["windows"]["full"]["liquidation_events"],
    )
    a1["train_keep"] = a1_gate["keep"]
    a1["gate_c"] = a1_gate

    cfg10 = composite_hourly_config()
    cfg1 = composite_hourly_1x_config()
    print(f"resample 1m → {cfg10.bar_minutes}m rows={len(panel)}", flush=True)
    hourly = resample_panel(panel, int(cfg10.bar_minutes))
    print(f"resampled rows={len(hourly)}", flush=True)
    signaled_c = add_composite(hourly, cfg10)
    for symbol in COMPOSITE_SYMBOLS:
        for lev, cfg in ((COMPOSITE_LEVERAGE, cfg10), (COMPOSITE_LEVERAGE_1X, cfg1)):
            name = _c_name(symbol, lev)
            print(f"sim {name} lev={cfg.leverage}", flush=True)
            result = run_composite(
                signaled_c, symbol=symbol, cfg=cfg, trade_start_ts=_ts(FULL_START)
            )
            pack = {
                wname: window_pack(result, a, b, starting_equity=COMPOSITE_EQUITY)
                for wname, (a, b) in WINDOWS.items()
            }
            out["variants"][name] = {
                "book": "C1",
                "cfg": {
                    "symbol": symbol,
                    "leverage": cfg.leverage,
                    "bar_minutes": cfg.bar_minutes,
                    "entry_z": cfg.entry_z,
                    "fraction": cfg.fraction,
                    "stop_price_pct": cfg.stop_price_pct,
                    "flatten_in_band": cfg.flatten_in_band,
                    "starting_equity": cfg.starting_equity,
                },
                "windows": pack,
                "monthly": monthly_table(result, pnl_start_ts=_ts(FULL_START)).to_dict(
                    orient="records"
                ),
            }
            bars_by_name[name] = result.bars
            print(
                json.dumps(
                    {
                        "name": name,
                        "train": pack["train"]["equity_return"],
                        "oos": pack["oos"]["equity_return"],
                        "last_month": pack["last_month"]["equity_return"],
                        "train_trades": pack["train"]["trades"],
                        "oos_trades": pack["oos"]["trades"],
                        "fees": pack["full"]["fee_sum"],
                        "spread": pack["full"]["spread_like_pnl"],
                        "end_eq": pack["full"]["ending_equity"],
                        "liq": pack["full"]["liquidation_events"],
                        "exit_reasons": pack["full"]["exit_reasons"],
                    },
                    indent=2,
                ),
                flush=True,
            )

    c_keep = {}
    c_gate = {}
    for symbol in COMPOSITE_SYMBOLS:
        v = out["variants"][_c_name(symbol, COMPOSITE_LEVERAGE_1X)]
        g = _gate(
            v["windows"]["train"]["equity_return"],
            v["windows"]["oos"]["equity_return"],
            v["windows"]["oos"]["liquidation_events"],
            v["windows"]["full"]["liquidation_events"],
        )
        v["train_keep"] = g["keep"]
        v["gate_c"] = g
        c_keep[symbol] = g["keep"]
        c_gate[symbol] = g

    out["train_keep"] = {"A_x1": a1_gate["keep"], **c_keep}
    out["gate_c"] = {
        "passed": bool(a1_gate["passed"] or any(g["passed"] for g in c_gate.values())),
        "rule": out["gate_c_rule"],
        "A_x1": a1_gate,
        "by_symbol": c_gate,
    }
    kept = []
    if a1_gate["keep"]:
        kept.append("A 1x")
    kept.extend(f"{s} C1 1x" for s, k in c_keep.items() if k)
    if kept:
        failed_oos = []
        if a1_gate["keep"] and not a1_gate["passed"]:
            failed_oos.append("A 1x")
        failed_oos.extend(
            f"{s} C1 1x" for s, g in c_gate.items() if g["keep"] and not g["passed"]
        )
        if failed_oos:
            out["decision"] = (
                "keep " + ", ".join(kept) + " on train > 0; Gate C fail on "
                + ", ".join(failed_oos)
                + " (OOS not positive after costs or liquidations). 1x is not a magic save."
            )
        else:
            out["decision"] = "keep " + ", ".join(kept) + "; Gate C scored on pre-locked OOS"
    else:
        out["decision"] = (
            "drop all 1x books (train after-cost equity ≤ 0). "
            "1x is not a magic save if the edge is negative or fees still dominate."
        )
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def _row(label: str, v: dict, is_1x: bool) -> str:
    tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
    lev = v["cfg"]["leverage"]
    keep_s = "—" if not is_1x else ("留" if v.get("train_keep") else "丢")
    gate_s = "—" if not is_1x else ("过" if v.get("gate_c", {}).get("passed") else "未过")
    return (
        f"| {label} | {lev:g}x | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
        f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} | {tr['trades']} "
        f"| {oos['trades']} | {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
        f"| {_usd(full['ending_equity'])} | {full['liquidation_events']} | {keep_s} | {gate_s} |"
    )


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    header = (
        "| 书 | 杠杆 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 手续费 | 价差项 | 期末 | 强平 | 训练留1x | Gate C |"
    )
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"
    a_lines = [header, sep]
    for name in A_ORDER:
        a_lines.append(_row(A_LABELS[name], eval_out["variants"][name], name.endswith("x1")))
    c_lines = [header, sep]
    for name in C_ORDER:
        c_lines.append(_row(C_LABELS[name], eval_out["variants"][name], name.endswith("x1")))
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    return f"""# 1x 杠杆（只改杠杆，两本冻结书）

一次只改杠杆。不搜 OOS / 近一月。训练段扣费后权益 **> 0** 才留（不是跟有杠杆书比大小：1x 的 |收益| 本来就更小）。Gate C：留下且预锁 OOS 扣费后 > 0、0 强平。

| 书 | 外壳 | 基线 → 1x |
|---|---|---|
| A | 7 日残差 z，每天 01:00 UTC，30bp 门槛，$100k | 2x → 1x（名义大约减半） |
| C1 | 1h 四类等权综合，\\|z\\|≥1 带内刮，1/10 逐仓，2% 止损，$1000 | 10x → 1x（名义从约 1× 权益降到 0.1×） |

冻结综合因子是 **C1 1h**，不是瀑布里已经训练失败的 C2 hold-to-opposite。

## 书 A（7d z）

{chr(10).join(a_lines)}

## 综合 C1 1h（BTC / ETH 分开）

{chr(10).join(c_lines)}

{eval_out["decision"]}

1x 不是魔法：边为负或手续费仍主导时，降杠杆只是把亏损和费用同时缩小，训练仍可能 ≤ 0，OOS 仍可能为负。近一月不参与选书。

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_leverage
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    paths: dict[str, str] = {}

    a_labeled = {A_LABELS[k]: _clip(bars[k], FULL_START, FULL_END) for k in A_ORDER}
    p = media / "leverage-1x-a-equity.png"
    plot_named_equity(a_labeled, p, "Frozen book A: 1x vs 2x (after costs)", A_COLORS)
    paths["a_equity"] = str(p)
    a_oos = {A_LABELS[k]: _clip(bars[k], OOS_START, OOS_END) for k in A_ORDER}
    p = media / "leverage-1x-a-oos.png"
    plot_named_equity(a_oos, p, "OOS book A 1x vs 2x (locked, not searched)", A_COLORS)
    paths["a_oos"] = str(p)

    c_labeled = {C_LABELS[k]: _clip(bars[k], FULL_START, FULL_END) for k in C_ORDER}
    p = media / "leverage-1x-composite-equity.png"
    plot_named_equity(
        c_labeled, p, "Frozen C1 1h composite: 1x vs 10x (after costs)", C_COLORS
    )
    paths["c_equity"] = str(p)
    c_oos = {C_LABELS[k]: _clip(bars[k], OOS_START, OOS_END) for k in C_ORDER}
    p = media / "leverage-1x-composite-oos.png"
    plot_named_equity(
        c_oos, p, "OOS C1 1h 1x vs 10x (locked, not searched)", C_COLORS
    )
    paths["c_oos"] = str(p)
    return paths


def jsonable(eval_out: dict) -> dict:
    return {k: v for k, v in eval_out.items() if k != "bars_by_name"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--media-dir", default="")
    p.add_argument("--summary-json", default="")
    p.add_argument("--report-md", default="")
    args = p.parse_args(argv)
    panel, _ = load_panel(name=CACHE_LONG)
    panel = panel[panel["bar_open_ts"] <= _end_ts(FULL_END)].reset_index(drop=True)
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
    if args.report_md:
        md = render_report(eval_out, paths)
        out = Path(args.report_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
