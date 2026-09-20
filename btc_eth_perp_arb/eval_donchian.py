"""Locked 5m Donchian(144) breakout on BTCUSDT and ETHUSDT separately.

Declared before looking at this step's results:

144 × 5-minute bars, break mark close vs prior channel, 1/10 isolated
margin, 10x notional, no stop, take-profit at 5× margin. $1000 start.
BTC and ETH are two books, not a grid. Train 2025-10-01→2026-06-30
keep if after-cost equity > 0; 2026 OOS and last month score only.
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
    DONCHIAN_SYMBOLS,
    DONCHIAN_TP_MULTIPLE,
    DONCHIAN_WINDOW,
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
LABELS = {"BTCUSDT": "BTC Donchian 144×5m", "ETHUSDT": "ETH Donchian 144×5m"}
COLORS = {"BTCUSDT": "#f7931a", "ETHUSDT": "#627eea"}


def evaluate(panel) -> dict:
    cfg = donchian_config()
    if cfg.bar_minutes > 1:
        print(f"resample 1m → {cfg.bar_minutes}m rows={len(panel)}", flush=True)
        panel = resample_panel(panel, cfg.bar_minutes)
        print(f"resampled rows={len(panel)}", flush=True)
    signaled = add_donchian(panel, cfg)
    out = {
        "step": "donchian_144_5m",
        "shell": {
            "bar_minutes": DONCHIAN_BAR_MINUTES,
            "window": DONCHIAN_WINDOW,
            "fraction": DONCHIAN_FRACTION,
            "leverage": DONCHIAN_LEVERAGE,
            "tp_multiple": DONCHIAN_TP_MULTIPLE,
            "starting_equity": DONCHIAN_EQUITY,
            "symbols": list(DONCHIAN_SYMBOLS),
            "stop": None,
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name = {}
    keep = {}
    gate = {}
    for symbol in DONCHIAN_SYMBOLS:
        print(f"sim {symbol} donchian window={cfg.window} lev={cfg.leverage}", flush=True)
        result = run_donchian(
            signaled, symbol=symbol, cfg=cfg, trade_start_ts=_ts(FULL_START)
        )
        pack = {
            wname: window_pack(result, a, b, starting_equity=DONCHIAN_EQUITY)
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
            "oos": oos_r,
            "oos_liq": oos_liq,
        }
        out["variants"][symbol] = {
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
            "train_keep": kept,
            "gate_c": gate[symbol],
        }
        bars_by_name[symbol] = result.bars
        print(
            json.dumps(
                {
                    "name": symbol,
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
                    "keep": kept,
                    "gate_c": gate[symbol]["passed"],
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
    out["decision"] = (
        "at least one Donchian book has train > 0; OOS scores only"
        if any(keep.values())
        else "drop both Donchian books (train equity not > 0 after costs)"
    )
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| 书 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 近一月笔 | 手续费 | 价差项 | 期末 | 强平 | 训练留 | Gate C |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for symbol in DONCHIAN_SYMBOLS:
        v = eval_out["variants"][symbol]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        lines.append(
            f"| {LABELS[symbol]} | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} | {tr['trades']} "
            f"| {oos['trades']} | {last['trades']} | {_usd(full['fee_sum'])} "
            f"| {_usd(full['spread_like_pnl'])} | {_usd(full['ending_equity'])} "
            f"| {full['liquidation_events']} | {'留' if v['train_keep'] else '丢'} "
            f"| {'过' if v['gate_c']['passed'] else '未过'} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    return f"""# 唐奇安 144×5m 突破（1/10 仓，10x，止盈 5×投入）

预先锁死，不是在近一月搜出来的，也不是 F2 残差书的续修。BTC / ETH 各一本，互不合并。

| 项 | 取值 |
|---|---|
| bar | 5 分钟（1m UTC 对齐 OHLC，无 close ffill） |
| 通道 | 过去 144 根 mark 最高/最低（`shift(1)`） |
| 开仓 | mark close 上破 → 多；下破 → 空；bar t 信号 t+1 last open 成交 |
| 仓位 | 投入 = 当时权益的 1/10 作为逐仓保证金；名义 = 投入 × 10 |
| 止损 | 无（数据缺口仍平仓；逐仓权益 < 0.4%×名义则强平） |
| 止盈 | 浮动盈亏 ≥ 5 × 该笔投入（上一根 mark 判定，本根 open 平） |
| 本金 | 1000 USDT；VIP0 taker 5bp + 半价差 |

## 结果

{chr(10).join(lines)}

{eval_out["decision"]}

Gate C 规则：训练留下且扣费后 OOS > 0、0 强平。近一月不参与选书、不在 BTC/ETH 之间挑。

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
    p = media / "donchian-equity.png"
    plot_named_equity(
        labeled,
        p,
        "Donchian 144×5m 10x 1/10-size TP×5 (after costs)",
        colors,
    )
    paths["equity"] = str(p)
    oos = {LABELS[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / "donchian-oos.png"
    plot_named_equity(oos, p, "OOS Donchian BTC vs ETH (locked, not searched)", colors)
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
