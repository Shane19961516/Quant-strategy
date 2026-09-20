"""Locked 5m Donchian(144) breakout: 10x vs one-knob 50x.

Declared before looking at this step's results:

Shell frozen: 144 × 5-minute bars, break mark close vs prior channel,
1/10 isolated margin, no stop, take-profit at 5× margin, $1000 start.
BTC and ETH are two books, not a grid. Only change vs D10: leverage 50.
Train 2025-10-01→2026-06-30 keep 50x if after-cost equity > same-symbol 10x;
2026 OOS and last month score only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    DONCHIAN_BAR_MINUTES,
    DONCHIAN_EQUITY,
    DONCHIAN_FRACTION,
    DONCHIAN_LEVERAGE,
    DONCHIAN_LEVERAGE_50,
    DONCHIAN_SYMBOLS,
    DONCHIAN_TP_MULTIPLE,
    DONCHIAN_WINDOW,
    donchian_50_config,
    donchian_config,
)
from .data import load_panel, resample_panel
from .donchian import add_donchian, run_donchian
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
LEVERAGES = (DONCHIAN_LEVERAGE, DONCHIAN_LEVERAGE_50)
LABELS = {
    "BTCUSDT_x10": "BTC 10x (frozen)",
    "BTCUSDT_x50": "BTC 50x",
    "ETHUSDT_x10": "ETH 10x (frozen)",
    "ETHUSDT_x50": "ETH 50x",
}
COLORS = {
    "BTCUSDT_x10": "#c47a20",
    "BTCUSDT_x50": "#f7931a",
    "ETHUSDT_x10": "#8a9bd7",
    "ETHUSDT_x50": "#627eea",
}
ORDER = ("BTCUSDT_x10", "BTCUSDT_x50", "ETHUSDT_x10", "ETHUSDT_x50")


def _name(symbol: str, lev: float) -> str:
    return f"{symbol}_x{int(lev)}"


def evaluate(panel) -> dict:
    base_cfg = donchian_config()
    if base_cfg.bar_minutes > 1:
        print(f"resample 1m → {base_cfg.bar_minutes}m rows={len(panel)}", flush=True)
        panel = resample_panel(panel, base_cfg.bar_minutes)
        print(f"resampled rows={len(panel)}", flush=True)
    signaled = add_donchian(panel, base_cfg)
    out = {
        "step": "donchian_144_5m_50x",
        "shell": {
            "bar_minutes": DONCHIAN_BAR_MINUTES,
            "window": DONCHIAN_WINDOW,
            "fraction": DONCHIAN_FRACTION,
            "leverage_D10": DONCHIAN_LEVERAGE,
            "leverage_D50": DONCHIAN_LEVERAGE_50,
            "tp_multiple": DONCHIAN_TP_MULTIPLE,
            "starting_equity": DONCHIAN_EQUITY,
            "symbols": list(DONCHIAN_SYMBOLS),
            "stop": None,
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name = {}
    for symbol in DONCHIAN_SYMBOLS:
        for lev in LEVERAGES:
            name = _name(symbol, lev)
            cfg = donchian_50_config() if lev == DONCHIAN_LEVERAGE_50 else donchian_config()
            print(f"sim {name} lev={cfg.leverage}", flush=True)
            result = run_donchian(
                signaled, symbol=symbol, cfg=cfg, trade_start_ts=_ts(FULL_START)
            )
            pack = {
                wname: window_pack(result, a, b, starting_equity=DONCHIAN_EQUITY)
                for wname, (a, b) in WINDOWS.items()
            }
            out["variants"][name] = {
                "cfg": {
                    "symbol": symbol,
                    "window": cfg.window,
                    "fraction": cfg.fraction,
                    "leverage": cfg.leverage,
                    "tp_multiple": cfg.tp_multiple,
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
    for symbol in DONCHIAN_SYMBOLS:
        d10 = out["variants"][_name(symbol, DONCHIAN_LEVERAGE)]["windows"]["train"][
            "equity_return"
        ]
        d50 = out["variants"][_name(symbol, DONCHIAN_LEVERAGE_50)]["windows"]["train"][
            "equity_return"
        ]
        d50_oos = out["variants"][_name(symbol, DONCHIAN_LEVERAGE_50)]["windows"]["oos"][
            "equity_return"
        ]
        d50_liq = out["variants"][_name(symbol, DONCHIAN_LEVERAGE_50)]["windows"]["oos"][
            "liquidation_events"
        ]
        kept = d50 > d10
        keep[symbol] = bool(kept)
        gate[symbol] = {
            "passed": bool(kept and d50_oos > 0 and d50_liq == 0),
            "keep": kept,
            "d10_train": d10,
            "d50_train": d50,
            "oos": d50_oos,
            "oos_liq": d50_liq,
        }
        out["variants"][_name(symbol, DONCHIAN_LEVERAGE_50)]["train_keep"] = kept
        out["variants"][_name(symbol, DONCHIAN_LEVERAGE_50)]["gate_c"] = gate[symbol]
    out["train_keep"] = keep
    out["gate_c"] = {
        "passed": any(g["passed"] for g in gate.values()),
        "rule": "kept 50x book must have pre-locked OOS equity return > 0 after costs, 0 liq",
        "by_symbol": gate,
    }
    out["decision"] = (
        "keep 50x on at least one symbol (train better than frozen 10x); OOS scores only"
        if any(keep.values())
        else "drop 50x on both symbols (train equity not better than frozen 10x)"
    )
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| 书 | 杠杆 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 手续费 | 价差项 | 期末 | 强平 | 训练留50x | Gate C |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for name in ORDER:
        v = eval_out["variants"][name]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        lev = v["cfg"]["leverage"]
        is50 = lev == DONCHIAN_LEVERAGE_50
        keep_s = "—" if not is50 else ("留" if v.get("train_keep") else "丢")
        gate_s = "—" if not is50 else ("过" if v.get("gate_c", {}).get("passed") else "未过")
        lines.append(
            f"| {LABELS[name]} | {lev:g}x | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} | {tr['trades']} "
            f"| {oos['trades']} | {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_usd(full['ending_equity'])} | {full['liquidation_events']} | {keep_s} | {gate_s} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    return f"""# 唐奇安 144×5m：50x 对冻结 10x（1/10 仓，止盈 5×投入）

一次只改杠杆。50x 时名义 = 权益的 5 倍；止盈仍是投入的 5 倍（约 10% 价格）；逐仓强平大约逆向 1.6%。

| 项 | 取值 |
|---|---|
| 外壳 | 5 分钟，144 根通道，1/10 逐仓保证金，不止损，止盈 5×投入，$1000 |
| D10 | 杠杆 10（上一刀，冻结） |
| D50 | 杠杆 50 |
| 决策 | 同品种训练段扣费后权益 D50 > D10 才留；OOS / 近一月只评分 |

## 结果

{chr(10).join(lines)}

{eval_out["decision"]}

Gate C：留下的 50x 必须扣费后 OOS > 0 且 0 强平。近一月不参与选书、不在 BTC/ETH 之间挑。

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_donchian
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    labeled = {LABELS[k]: _clip(v, FULL_START, FULL_END) for k, v in bars.items()}
    colors = {LABELS[k]: COLORS[k] for k in bars}
    paths = {}
    p = media / "donchian-50-equity.png"
    plot_named_equity(
        labeled,
        p,
        "Donchian 144×5m 50x vs frozen 10x (after costs)",
        colors,
    )
    paths["equity"] = str(p)
    oos = {LABELS[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / "donchian-50-oos.png"
    plot_named_equity(oos, p, "OOS Donchian 50x vs 10x (locked, not searched)", colors)
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
