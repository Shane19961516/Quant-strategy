"""Pre-declared C2–C4 waterfall on the frozen 1h four-family composite.

Locked before looking at results:

C1 frozen: 1h, |z|≥1, flatten in the dead band, 1/10×10x, 2% stop.
C2: hold until opposite (do not scratch when |z|<1).
C3: |z|≥2, band exit stays.
C4: daily bars, |z|≥1, band exit.

First of C2, C3, C4 with train equity > 0 is the candidate. Do not pick
the best of three, and do not search OOS / last month. Gate C if that
candidate's pre-locked OOS > 0 after costs and 0 liq.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    COMPOSITE_BAR_MINUTES_1D,
    COMPOSITE_BAR_MINUTES_1H,
    COMPOSITE_ENTRY_Z,
    COMPOSITE_ENTRY_Z_2,
    COMPOSITE_EQUITY,
    COMPOSITE_FRACTION,
    COMPOSITE_LEVERAGE,
    COMPOSITE_STOP_PRICE_PCT,
    COMPOSITE_SYMBOLS,
    COMPOSITE_WINDOW,
    CompositeConfig,
    composite_daily_config,
    composite_hold_config,
    composite_hourly_config,
    composite_z2_config,
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
STEP_ORDER = ("C1", "C2", "C3", "C4")
CANDIDATE_ORDER = ("C2", "C3", "C4")
FACTORIES = {
    "C1": composite_hourly_config,
    "C2": composite_hold_config,
    "C3": composite_z2_config,
    "C4": composite_daily_config,
}
LABELS = {
    "BTCUSDT_C1": "BTC C1 1h z1",
    "BTCUSDT_C2": "BTC C2 hold",
    "BTCUSDT_C3": "BTC C3 z2",
    "BTCUSDT_C4": "BTC C4 1d",
    "ETHUSDT_C1": "ETH C1 1h z1",
    "ETHUSDT_C2": "ETH C2 hold",
    "ETHUSDT_C3": "ETH C3 z2",
    "ETHUSDT_C4": "ETH C4 1d",
}
COLORS = {
    "BTCUSDT_C1": "#c47a20",
    "BTCUSDT_C2": "#f7931a",
    "BTCUSDT_C3": "#b22222",
    "BTCUSDT_C4": "#8b4513",
    "ETHUSDT_C1": "#8a9bd7",
    "ETHUSDT_C2": "#627eea",
    "ETHUSDT_C3": "#1f4e79",
    "ETHUSDT_C4": "#2ca02c",
}
ORDER = tuple(f"{s}_{st}" for s in COMPOSITE_SYMBOLS for st in STEP_ORDER)


def _name(symbol: str, step: str) -> str:
    return f"{symbol}_{step}"


def evaluate(panel) -> dict:
    out = {
        "step": "composite_waterfall_c2_c4",
        "shell": {
            "window": COMPOSITE_WINDOW,
            "fraction": COMPOSITE_FRACTION,
            "leverage": COMPOSITE_LEVERAGE,
            "stop_price_pct": COMPOSITE_STOP_PRICE_PCT,
            "starting_equity": COMPOSITE_EQUITY,
            "c1": "1h |z|>=1 band-exit (frozen)",
            "c2": "1h hold-to-opposite",
            "c3": "1h |z|>=2 band-exit",
            "c4": "1d |z|>=1 band-exit",
            "waterfall": list(CANDIDATE_ORDER),
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name = {}
    resamples: dict[int, object] = {}
    for step in STEP_ORDER:
        cfg: CompositeConfig = FACTORIES[step]()
        minutes = int(cfg.bar_minutes)
        if minutes not in resamples:
            print(f"resample 1m → {minutes}m rows={len(panel)}", flush=True)
            resamples[minutes] = resample_panel(panel, minutes)
            print(f"resampled rows={len(resamples[minutes])}", flush=True)
        signaled = add_composite(resamples[minutes], cfg)
        for symbol in COMPOSITE_SYMBOLS:
            name = _name(symbol, step)
            print(f"sim {name} bar={minutes}m z={cfg.entry_z} hold={not cfg.flatten_in_band}", flush=True)
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
                    "step": step,
                    "bar_minutes": cfg.bar_minutes,
                    "entry_z": cfg.entry_z,
                    "flatten_in_band": cfg.flatten_in_band,
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
    chosen = {}
    for symbol in COMPOSITE_SYMBOLS:
        pick = None
        for step in CANDIDATE_ORDER:
            tr = out["variants"][_name(symbol, step)]["windows"]["train"]["equity_return"]
            if tr > 0:
                pick = step
                break
        chosen[symbol] = pick
        kept = pick is not None
        keep[symbol] = bool(kept)
        if pick is None:
            gate[symbol] = {
                "passed": False,
                "keep": False,
                "chosen": None,
                "train": None,
                "oos": None,
                "oos_liq": None,
            }
        else:
            pack = out["variants"][_name(symbol, pick)]["windows"]
            oos_r = pack["oos"]["equity_return"]
            oos_liq = pack["oos"]["liquidation_events"]
            gate[symbol] = {
                "passed": bool(oos_r > 0 and oos_liq == 0),
                "keep": True,
                "chosen": pick,
                "train": pack["train"]["equity_return"],
                "oos": oos_r,
                "oos_liq": oos_liq,
            }
            out["variants"][_name(symbol, pick)]["train_keep"] = True
            out["variants"][_name(symbol, pick)]["gate_c"] = gate[symbol]
        for step in CANDIDATE_ORDER:
            name = _name(symbol, step)
            if "train_keep" not in out["variants"][name]:
                out["variants"][name]["train_keep"] = bool(pick == step)
    out["train_keep"] = keep
    out["chosen"] = chosen
    out["gate_c"] = {
        "passed": any(g["passed"] for g in gate.values()),
        "rule": "first waterfall step with train equity > 0 must have pre-locked OOS > 0 after costs, 0 liq",
        "by_symbol": gate,
    }
    picked = [f"{s} {st}" for s, st in chosen.items() if st]
    if picked:
        out["decision"] = (
            "keep " + ", ".join(picked) + " (first waterfall step with train > 0); OOS scores only"
        )
    else:
        out["decision"] = "drop C2/C3/C4 (none has train equity > 0); family still not live-ready"
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| 书 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 手续费 | 价差项 | 期末 | 强平 | 瀑布留 | Gate C |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for name in ORDER:
        v = eval_out["variants"][name]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        step = v["cfg"]["step"]
        keep_s = "—" if step == "C1" else ("留" if v.get("train_keep") else "—")
        gate_s = "—" if step == "C1" else (
            "过" if v.get("gate_c", {}).get("passed") else ("未过" if v.get("train_keep") else "—")
        )
        lines.append(
            f"| {LABELS[name]} | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} | {tr['trades']} "
            f"| {oos['trades']} | {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_usd(full['ending_equity'])} | {full['liquidation_events']} | {keep_s} | {gate_s} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    chosen = eval_out.get("chosen") or {}
    return f"""# 综合因子瀑布 C2→C3→C4（对冻结 1h C1）

预先写死顺序，不是在三者里挑训练最优，更不是近一月搜参。

| 步 | 唯一差别 |
|---|---|
| C1 | 1h，\\|z\\|≥1，带内刮出场（冻结） |
| C2 | 拿到反向才平 |
| C3 | \\|z\\|≥2，带内仍刮 |
| C4 | 日线，\\|z\\|≥1 |
| 仓位 | 1/10 逐仓 × 10x，2% 止损，无 5× 止盈 |
| 留 | 瀑布里第一个训练权益 > 0 的；只比 C1 少亏不够 |

选中：BTC `{chosen.get("BTCUSDT")}` / ETH `{chosen.get("ETHUSDT")}`

## 结果

{chr(10).join(lines)}

{eval_out["decision"]}

Gate C：留下的那一刀必须扣费后 OOS > 0 且 0 强平。近一月不参与选书。

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
    p = media / "composite-waterfall-equity.png"
    plot_named_equity(
        labeled,
        p,
        "Composite C1–C4 waterfall (after costs)",
        colors,
    )
    paths["equity"] = str(p)
    oos = {LABELS[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / "composite-waterfall-oos.png"
    plot_named_equity(oos, p, "OOS composite waterfall (locked, not searched)", colors)
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
