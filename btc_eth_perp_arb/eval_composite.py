"""One-knob slower clock on the frozen four-family composite.

Declared before looking at this step's results:

C0 frozen: 5m, 144-bar window, slope lag 24, equal 0.25 weights, |z|≥1,
1/10 isolated × 10x, 2% stop, no 5× TP.
C1: only bar_minutes 5 → 60. MA/slope/volume/position are swing features;
the 5m |z| machine was the cost leak. Do not grid |z|, weights, or OOS.

Keep 1h if after-cost train equity > 0 (beating −94% 5m is not enough).
2026 OOS and last month score only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    COMPOSITE_BAR_MINUTES,
    COMPOSITE_BAR_MINUTES_1H,
    COMPOSITE_ENTRY_Z,
    COMPOSITE_EQUITY,
    COMPOSITE_FRACTION,
    COMPOSITE_LEVERAGE,
    COMPOSITE_SLOPE_LAG,
    COMPOSITE_STOP_PRICE_PCT,
    COMPOSITE_SYMBOLS,
    COMPOSITE_WEIGHTS,
    COMPOSITE_WINDOW,
    composite_config,
    composite_hourly_config,
)
from .composite import add_composite, run_composite
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
    window_pack,
)
from .metrics import monthly_table
from .plots import plot_named_equity

CACHE_LONG = "aligned_1m_long.parquet"
BARS = (COMPOSITE_BAR_MINUTES, COMPOSITE_BAR_MINUTES_1H)
LABELS = {
    "BTCUSDT_5m": "BTC 5m (frozen)",
    "BTCUSDT_1h": "BTC 1h",
    "ETHUSDT_5m": "ETH 5m (frozen)",
    "ETHUSDT_1h": "ETH 1h",
}
COLORS = {
    "BTCUSDT_5m": "#c47a20",
    "BTCUSDT_1h": "#f7931a",
    "ETHUSDT_5m": "#8a9bd7",
    "ETHUSDT_1h": "#627eea",
}
ORDER = ("BTCUSDT_5m", "BTCUSDT_1h", "ETHUSDT_5m", "ETHUSDT_1h")


def _name(symbol: str, minutes: int) -> str:
    return f"{symbol}_{'1h' if minutes == COMPOSITE_BAR_MINUTES_1H else '5m'}"


def evaluate(panel) -> dict:
    out = {
        "step": "composite_1h",
        "shell": {
            "window": COMPOSITE_WINDOW,
            "slope_lag": COMPOSITE_SLOPE_LAG,
            "entry_z": COMPOSITE_ENTRY_Z,
            "weights": list(COMPOSITE_WEIGHTS),
            "fraction": COMPOSITE_FRACTION,
            "leverage": COMPOSITE_LEVERAGE,
            "stop_price_pct": COMPOSITE_STOP_PRICE_PCT,
            "starting_equity": COMPOSITE_EQUITY,
            "bar_C0": COMPOSITE_BAR_MINUTES,
            "bar_C1": COMPOSITE_BAR_MINUTES_1H,
            "symbols": list(COMPOSITE_SYMBOLS),
            "take_profit": None,
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name = {}
    for minutes in BARS:
        cfg = composite_hourly_config() if minutes == COMPOSITE_BAR_MINUTES_1H else composite_config()
        print(f"resample 1m → {cfg.bar_minutes}m rows={len(panel)}", flush=True)
        resampled = resample_panel(panel, cfg.bar_minutes)
        print(f"resampled rows={len(resampled)}", flush=True)
        signaled = add_composite(resampled, cfg)
        for symbol in COMPOSITE_SYMBOLS:
            name = _name(symbol, minutes)
            print(f"sim {name}", flush=True)
            result = run_composite(
                signaled, symbol=symbol, cfg=cfg, trade_start_ts=_ts(FULL_START)
            )
            pack = {
                wname: window_pack(result, a, b, starting_equity=COMPOSITE_EQUITY)
                for wname, (a, b) in WINDOWS.items()
            }
            out["variants"][name] = {
                "cfg": {
                    "symbol": symbol,
                    "bar_minutes": cfg.bar_minutes,
                    "window": cfg.window,
                    "slope_lag": cfg.slope_lag,
                    "entry_z": cfg.entry_z,
                    "fraction": cfg.fraction,
                    "leverage": cfg.leverage,
                    "stop_price_pct": cfg.stop_price_pct,
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
    keep = {}
    gate = {}
    vs_5m = {}
    for symbol in COMPOSITE_SYMBOLS:
        c0 = out["variants"][_name(symbol, COMPOSITE_BAR_MINUTES)]["windows"]["train"][
            "equity_return"
        ]
        c1 = out["variants"][_name(symbol, COMPOSITE_BAR_MINUTES_1H)]["windows"]["train"][
            "equity_return"
        ]
        c1_oos = out["variants"][_name(symbol, COMPOSITE_BAR_MINUTES_1H)]["windows"]["oos"][
            "equity_return"
        ]
        c1_liq = out["variants"][_name(symbol, COMPOSITE_BAR_MINUTES_1H)]["windows"]["oos"][
            "liquidation_events"
        ]
        better = c1 > c0
        kept = c1 > 0
        keep[symbol] = bool(kept)
        vs_5m[symbol] = bool(better)
        gate[symbol] = {
            "passed": bool(kept and c1_oos > 0 and c1_liq == 0),
            "keep": kept,
            "better_than_5m": better,
            "c0_train": c0,
            "c1_train": c1,
            "oos": c1_oos,
            "oos_liq": c1_liq,
        }
        out["variants"][_name(symbol, COMPOSITE_BAR_MINUTES_1H)]["train_keep"] = kept
        out["variants"][_name(symbol, COMPOSITE_BAR_MINUTES_1H)]["better_than_5m"] = better
        out["variants"][_name(symbol, COMPOSITE_BAR_MINUTES_1H)]["gate_c"] = gate[symbol]
    out["train_keep"] = keep
    out["better_than_5m"] = vs_5m
    out["gate_c"] = {
        "passed": any(g["passed"] for g in gate.values()),
        "rule": "kept 1h book must have train equity > 0, pre-locked OOS > 0 after costs, 0 liq",
        "by_symbol": gate,
    }
    if any(keep.values()):
        out["decision"] = (
            "keep 1h on at least one symbol (train equity > 0); OOS scores only"
        )
    elif any(vs_5m.values()):
        out["decision"] = (
            "1h beat frozen 5m on train but still ≤ 0 — drop; beating −94% is not a book"
        )
    else:
        out["decision"] = "drop 1h on both symbols (train not better than frozen 5m and ≤ 0)"
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| 书 | bar | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 手续费 | 价差项 | 期末 | 强平 | 训练留1h | Gate C |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for name in ORDER:
        v = eval_out["variants"][name]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        minutes = v["cfg"]["bar_minutes"]
        is_1h = minutes == COMPOSITE_BAR_MINUTES_1H
        keep_s = "—" if not is_1h else ("留" if v.get("train_keep") else "丢")
        gate_s = "—" if not is_1h else ("过" if v.get("gate_c", {}).get("passed") else "未过")
        lines.append(
            f"| {LABELS[name]} | {minutes}m | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} | {tr['trades']} "
            f"| {oos['trades']} | {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_usd(full['ending_equity'])} | {full['liquidation_events']} | {keep_s} | {gate_s} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    return f"""# 综合因子 C1：1h 对冻结 5m（MA+slope+量+位置，1/10×10x）

一次只改 K 线周期。5m `|z|≥1` 训练 2966/5112 笔，手续费把本金磨掉。1h 时 144 根 = 6 天均线，斜率 24 小时。其它冻结：等权、\\|z\\|≥1、2% 止损、无 5× 止盈。

| 项 | 取值 |
|---|---|
| C0 | 5 分钟（冻结，已丢） |
| C1 | 60 分钟 |
| 决策 | 1h 训练扣费后权益 > 0 才留；只比 5m 少亏不够 |

## 结果

{chr(10).join(lines)}

{eval_out["decision"]}

Gate C：留下的 1h 必须扣费后 OOS > 0 且 0 强平。近一月不参与选书。不要在 OOS 上改 \\|z\\| 或权重。

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_composite
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    labeled = {LABELS[k]: _clip(v, FULL_START, FULL_END) for k, v in bars.items()}
    colors = {LABELS[k]: COLORS[k] for k in bars}
    paths = {}
    p = media / "composite-1h-equity.png"
    plot_named_equity(
        labeled,
        p,
        "Composite 1h vs frozen 5m (after costs)",
        colors,
    )
    paths["equity"] = str(p)
    oos = {LABELS[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / "composite-1h-oos.png"
    plot_named_equity(oos, p, "OOS composite 1h vs 5m (locked, not searched)", colors)
    paths["oos"] = str(p)
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
