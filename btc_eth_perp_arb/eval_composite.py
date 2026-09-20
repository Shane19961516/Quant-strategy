"""Locked 5m MA+slope+volume+position composite: 1/10 isolated, 10x.

Declared before looking at this step's results:

- Four families, equal 0.25 weights, one 144×5m window, slope lag 24.
- |composite z|≥1 enters; |z|<1 or opposite side flattens.
- Isolated margin = 1/10 equity; notional = margin × 10.
- 2% price stop (a-priori from the Donchian MAE note). No 5× TP.
- BTC and ETH are two books. Train keep if after-cost equity return > 0;
  2026 OOS and last month score only. Do not grid weights or windows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    COMPOSITE_BAR_MINUTES,
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
LABELS = {"BTCUSDT": "BTC composite 10x", "ETHUSDT": "ETH composite 10x"}
COLORS = {"BTCUSDT": "#c47a20", "ETHUSDT": "#627eea"}


def evaluate(panel) -> dict:
    cfg = composite_config()
    if cfg.bar_minutes > 1:
        print(f"resample 1m → {cfg.bar_minutes}m rows={len(panel)}", flush=True)
        panel = resample_panel(panel, cfg.bar_minutes)
        print(f"resampled rows={len(panel)}", flush=True)
    signaled = add_composite(panel, cfg)
    out = {
        "step": "composite_ma_slope_vol_pos",
        "shell": {
            "bar_minutes": COMPOSITE_BAR_MINUTES,
            "window": COMPOSITE_WINDOW,
            "slope_lag": COMPOSITE_SLOPE_LAG,
            "entry_z": COMPOSITE_ENTRY_Z,
            "weights": list(COMPOSITE_WEIGHTS),
            "fraction": COMPOSITE_FRACTION,
            "leverage": COMPOSITE_LEVERAGE,
            "stop_price_pct": COMPOSITE_STOP_PRICE_PCT,
            "starting_equity": COMPOSITE_EQUITY,
            "symbols": list(COMPOSITE_SYMBOLS),
            "take_profit": None,
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name = {}
    keep = {}
    gate = {}
    for symbol in COMPOSITE_SYMBOLS:
        print(f"sim {symbol} composite 10x", flush=True)
        result = run_composite(
            signaled, symbol=symbol, cfg=cfg, trade_start_ts=_ts(FULL_START)
        )
        pack = {
            wname: window_pack(result, a, b, starting_equity=COMPOSITE_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        train_r = pack["train"]["equity_return"]
        oos_r = pack["oos"]["equity_return"]
        oos_liq = pack["oos"]["liquidation_events"]
        kept = train_r > 0
        keep[symbol] = bool(kept)
        gate[symbol] = {
            "passed": bool(kept and oos_r > 0 and oos_liq == 0),
            "keep": kept,
            "train": train_r,
            "oos": oos_r,
            "oos_liq": oos_liq,
        }
        out["variants"][symbol] = {
            "cfg": {
                "symbol": symbol,
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
            "train_keep": kept,
            "gate_c": gate[symbol],
        }
        bars_by_name[symbol] = result.bars
        print(
            json.dumps(
                {
                    "name": symbol,
                    "train": train_r,
                    "oos": oos_r,
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
    out["train_keep"] = keep
    out["gate_c"] = {
        "passed": any(g["passed"] for g in gate.values()),
        "rule": "kept book must have pre-locked OOS equity return > 0 after costs, 0 liq",
        "by_symbol": gate,
    }
    kept_names = [s for s, k in keep.items() if k]
    out["decision"] = (
        f"keep {', '.join(kept_names)} (train equity > 0); OOS scores only"
        if kept_names
        else "drop both (train after-cost equity return ≤ 0)"
    )
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| 书 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 手续费 | 价差项 | 期末 | 训练强平 | OOS强平 | 训练留 | Gate C |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for symbol in COMPOSITE_SYMBOLS:
        v = eval_out["variants"][symbol]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        keep_s = "留" if v.get("train_keep") else "丢"
        gate_s = "过" if v.get("gate_c", {}).get("passed") else "未过"
        lines.append(
            f"| {LABELS[symbol]} | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} | {tr['trades']} "
            f"| {oos['trades']} | {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_usd(full['ending_equity'])} | {tr['liquidation_events']} | {oos['liquidation_events']} "
            f"| {keep_s} | {gate_s} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    return f"""# 综合因子：MA + slope + 量 + 位置（1/10 仓，10x）

预先锁死，不是在近一月搜出来的。四类等权 0.25，144×5m 窗口，斜率回看 24 根。BTC / ETH 各一本 $1000。

| 项 | 取值 |
|---|---|
| bar | 5 分钟（1m 10x 已因 taker 死掉） |
| MA | close / SMA(144) − 1，再 rolling-z |
| slope | SMA 相对 24 根变化，再 rolling-z |
| 量 | (成交额/SMA − 1) × sign(ret)，再 rolling-z |
| 位置 | 144 根高低点 %B − 0.5，再 rolling-z |
| 合成 | 等权；\\|z\\|≥1 开，\\|z\\|<1 或反向平 |
| 仓位 | 投入 = 权益 1/10 逐仓；名义 = 投入 × 10 |
| 止损 | 2% 价格（≈0.2×投入），上一根 mark 判定 |
| 止盈 | 无固定倍数 |

## 结果

{chr(10).join(lines)}

{eval_out["decision"]}

Gate C：留下的书必须扣费后 OOS > 0 且 0 强平。近一月不参与选书、不在 BTC/ETH 之间挑。

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
    p = media / "composite-equity.png"
    plot_named_equity(
        labeled,
        p,
        "Composite MA+slope+vol+pos 10x (after costs)",
        colors,
    )
    paths["equity"] = str(p)
    oos = {LABELS[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / "composite-oos.png"
    plot_named_equity(oos, p, "OOS composite (locked, not searched)", colors)
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
