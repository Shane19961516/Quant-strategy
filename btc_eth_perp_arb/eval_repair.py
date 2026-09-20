"""One-knob repairs of the 5m 5x log-spread z book.

Declared before looking at this step's results:

Shell (frozen): 5-minute bars, z of log(ETH/BTC), 120-bar window, |z|≥2,
5x, ETH notional $100×leverage, exit |z|≤0.5, no stop, rolling β = z window.
Train 2025-10-01→2026-06-30 keep/drop; 2026 OOS and last month score only.

F1 (this file): cost_hurdle_bps=30. Round-trip taker 4×5bp plus half-spreads
2×(0.5+1.0)bp ≈ 23bp; 30bp is ~1.25× that. Does not change z, window, or side.
Later steps (not in this run): F2 residual-vs-β signal, F3 cooldown/stride.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    REPAIR_BAR_MINUTES,
    REPAIR_COST_HURDLE_BPS,
    REPAIR_ENTRY_Z,
    REPAIR_LEVERAGE,
    REPAIR_SCHEME,
    REPAIR_Z_WINDOW,
    ZGRID_EQUITY,
    repair_baseline_config,
)
from .data import load_panel, resample_panel
from .eval_entry import (
    FULL_END,
    FULL_START,
    LAST_END,
    LAST_START,
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
from .signals import add_signals
from .simulator import run_simulator

CACHE_LONG = "aligned_1m_long.parquet"
LABELS = {"B0": "B0 no hurdle", "F1": "F1 cost hurdle 30bp"}
COLORS = {"B0": "#1f4e79", "F1": "#c45911"}


def variants() -> dict:
    return {
        "B0": repair_baseline_config(),
        "F1": repair_baseline_config(cost_hurdle_bps=REPAIR_COST_HURDLE_BPS),
    }


def evaluate(panel) -> dict:
    if REPAIR_BAR_MINUTES > 1:
        print(f"resample 1m → {REPAIR_BAR_MINUTES}m rows={len(panel)}", flush=True)
        panel = resample_panel(panel, REPAIR_BAR_MINUTES)
        print(f"resampled rows={len(panel)}", flush=True)
    signaled = add_signals(panel, repair_baseline_config())
    out = {
        "step": "F1_cost_hurdle",
        "shell": {
            "bar_minutes": REPAIR_BAR_MINUTES,
            "scheme": REPAIR_SCHEME,
            "z_window": REPAIR_Z_WINDOW,
            "entry_z": REPAIR_ENTRY_Z,
            "leverage": REPAIR_LEVERAGE,
            "cost_hurdle_bps_F1": REPAIR_COST_HURDLE_BPS,
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name = {}
    for name, cfg in variants().items():
        print(f"sim {name} hurdle={cfg.cost_hurdle_bps}", flush=True)
        result = run_simulator(signaled, cfg, trade_start_ts=_ts(FULL_START))
        pack = {
            wname: window_pack(result, a, b, starting_equity=ZGRID_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        out["variants"][name] = {
            "cfg": {"cost_hurdle_bps": cfg.cost_hurdle_bps, "entry_z": cfg.entry_z, "z_window": cfg.z_window},
            "windows": pack,
            "monthly": monthly_table(result, pnl_start_ts=_ts(FULL_START)).to_dict(orient="records"),
        }
        bars_by_name[name] = result.bars
        w = pack
        print(
            json.dumps(
                {
                    "name": name,
                    "train": w["train"]["equity_return"],
                    "oos": w["oos"]["equity_return"],
                    "last_month": w["last_month"]["equity_return"],
                    "train_trades": w["train"]["trades"],
                    "oos_trades": w["oos"]["trades"],
                    "fees": w["full"]["fee_sum"],
                    "spread": w["full"]["spread_like_pnl"],
                    "end_eq": w["full"]["ending_equity"],
                    "liq": w["full"]["liquidation_events"],
                    "exit_reasons": w["full"]["exit_reasons"],
                },
                indent=2,
            ),
            flush=True,
        )
    b0 = out["variants"]["B0"]["windows"]["train"]["equity_return"]
    f1 = out["variants"]["F1"]["windows"]["train"]["equity_return"]
    f1_oos = out["variants"]["F1"]["windows"]["oos"]["equity_return"]
    f1_liq = out["variants"]["F1"]["windows"]["oos"]["liquidation_events"]
    keep = f1 > b0
    out["train_keep_F1"] = bool(keep)
    out["decision"] = (
        "keep F1 as new baseline (train equity better than B0); OOS scores only"
        if keep
        else "drop F1 (train equity not better than B0); next repair starts from B0"
    )
    out["gate_c"] = {
        "passed": bool(keep and f1_oos > 0 and f1_liq == 0),
        "rule": "kept repair must have pre-locked OOS equity return > 0 after costs, 0 liq",
        "keep_F1": keep,
        "f1_oos": f1_oos,
    }
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    rows = []
    for name in ("B0", "F1"):
        v = eval_out["variants"][name]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        rows.append((name, tr, oos, last, full, v["cfg"]["cost_hurdle_bps"]))
    lines = [
        "| 书 | 门槛 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 近一月笔 | 手续费 | 价差项 | 期末 | 强平 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, tr, oos, last, full, hurdle in rows:
        lines.append(
            f"| {LABELS[name]} | {hurdle:g}bp | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} | {tr['trades']} | {oos['trades']} "
            f"| {last['trades']} | {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_usd(full['ending_equity'])} | {full['liquidation_events']} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    keep = eval_out["train_keep_F1"]
    return f"""# 修复 F1：30bp 成本门槛（5m / 5x / 120 根 log 价差）

一次只改一个旋钮。本刀：**开仓必须 `|log(ETH/BTC)−μ| ≥ 30bp`**，其余冻结。

| 项 | 取值 |
|---|---|
| 外壳 | 5 分钟 bar，log(ETH/BTC) z，120 根（10h），\\|z\\|≥2，5x，ETH 名义 $500，平 \\|z\\|≤0.5，不止损 |
| B0 | 无门槛（上一轮用户网格的这一本） |
| F1 | 门槛 30bp ≈ 1.25×（4×5bp taker + 两腿半价差） |
| 决策 | 只看训练段扣费后权益；OOS / 近一月只评分 |

## 结果

{chr(10).join(lines)}

训练是否留下 F1：**{"留" if keep else "丢"}**。{eval_out["decision"]}

Gate C：**{"过" if eval_out["gate_c"]["passed"] else "未过"}**。近一月不参与选书。

下一刀（若 F1 留下则叠在 F1 上，否则仍从 B0）：F2 把 z 改成对冲残差 `r_ETH − β r_BTC`，让信号和对冲同一件事。

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_repair
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    labeled = {LABELS[k]: _clip(v, FULL_START, FULL_END) for k, v in bars.items()}
    colors = {LABELS[k]: COLORS[k] for k in bars}
    paths = {}
    p = media / "repair-f1-equity.png"
    plot_named_equity(labeled, p, "F1 cost hurdle vs B0 (5m 5x 120-bar log-spread, after costs)", colors)
    paths["equity"] = str(p)
    oos = {LABELS[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / "repair-f1-oos.png"
    plot_named_equity(oos, p, "OOS F1 vs B0 (locked, not searched)", colors)
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
