"""One-knob repairs of the 5m 5x log-spread z book.

Declared before looking at this step's results:

Shell (frozen): 5-minute bars, 120-bar window, |z|≥2, 5x, ETH notional
$100×leverage, exit |z|≤0.5, no stop, rolling β = z window.
Train 2025-10-01→2026-06-30 keep/drop; 2026 OOS and last month score only.

F1: cost_hurdle_bps=30 on log-spread z.
F2: same 30bp hurdle, z of cum(r_ETH − β r_BTC) instead of z(log ETH/BTC).
F3 (later): cooldown/stride. Do not grid OOS / last month.
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
    REPAIR_SIGNAL_F2,
    REPAIR_Z_WINDOW,
    ZGRID_EQUITY,
    repair_baseline_config,
    repair_f1_config,
    repair_f2_config,
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
    window_pack,
)
from .metrics import monthly_table
from .plots import plot_named_equity
from .signals import add_signals
from .simulator import run_simulator

CACHE_LONG = "aligned_1m_long.parquet"
STEPS = ("F1", "F2")
LABELS = {
    "B0": "B0 no hurdle",
    "F1": "F1 cost hurdle 30bp",
    "F2": "F2 hedge residual z",
}
COLORS = {"B0": "#1f4e79", "F1": "#c45911", "F2": "#2e7d32"}


def variants_for(step: str) -> dict:
    if step == "F1":
        return {
            "B0": repair_baseline_config(),
            "F1": repair_f1_config(),
        }
    if step == "F2":
        return {
            "F1": repair_f1_config(),
            "F2": repair_f2_config(),
        }
    raise ValueError(f"unknown repair step={step}")


def evaluate(panel, step: str = "F2") -> dict:
    step = step.upper()
    if step not in STEPS:
        raise ValueError(f"unknown repair step={step}")
    if REPAIR_BAR_MINUTES > 1:
        print(f"resample 1m → {REPAIR_BAR_MINUTES}m rows={len(panel)}", flush=True)
        panel = resample_panel(panel, REPAIR_BAR_MINUTES)
        print(f"resampled rows={len(panel)}", flush=True)
    cfgs = variants_for(step)
    names = list(cfgs)
    out = {
        "step": {"F1": "F1_cost_hurdle", "F2": "F2_hedge_residual"}[step],
        "shell": {
            "bar_minutes": REPAIR_BAR_MINUTES,
            "scheme": REPAIR_SCHEME,
            "z_window": REPAIR_Z_WINDOW,
            "entry_z": REPAIR_ENTRY_Z,
            "leverage": REPAIR_LEVERAGE,
            "cost_hurdle_bps": REPAIR_COST_HURDLE_BPS,
            "signal_mode_F2": REPAIR_SIGNAL_F2,
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name = {}
    for name, cfg in cfgs.items():
        print(
            f"sim {name} hurdle={cfg.cost_hurdle_bps} mode={cfg.signal_mode}",
            flush=True,
        )
        signaled = add_signals(panel, cfg)
        result = run_simulator(signaled, cfg, trade_start_ts=_ts(FULL_START))
        pack = {
            wname: window_pack(result, a, b, starting_equity=ZGRID_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        out["variants"][name] = {
            "cfg": {
                "cost_hurdle_bps": cfg.cost_hurdle_bps,
                "entry_z": cfg.entry_z,
                "z_window": cfg.z_window,
                "signal_mode": cfg.signal_mode,
            },
            "windows": pack,
            "monthly": monthly_table(result, pnl_start_ts=_ts(FULL_START)).to_dict(
                orient="records"
            ),
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
    base, cand = names[0], names[1]
    base_tr = out["variants"][base]["windows"]["train"]["equity_return"]
    cand_tr = out["variants"][cand]["windows"]["train"]["equity_return"]
    cand_oos = out["variants"][cand]["windows"]["oos"]["equity_return"]
    cand_liq = out["variants"][cand]["windows"]["oos"]["liquidation_events"]
    keep = cand_tr > base_tr
    keep_key = f"train_keep_{cand}"
    out[keep_key] = bool(keep)
    out["baseline"] = base
    out["candidate"] = cand
    out["decision"] = (
        f"keep {cand} as new baseline (train equity better than {base}); OOS scores only"
        if keep
        else f"drop {cand} (train equity not better than {base}); next repair starts from {base}"
    )
    out["gate_c"] = {
        "passed": bool(keep and cand_oos > 0 and cand_liq == 0),
        "rule": "kept repair must have pre-locked OOS equity return > 0 after costs, 0 liq",
        keep_key: keep,
        f"{cand.lower()}_oos": cand_oos,
    }
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    names = [n for n in ("B0", "F1", "F2") if n in eval_out["variants"]]
    rows = []
    for name in names:
        v = eval_out["variants"][name]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        rows.append((name, tr, oos, last, full, v["cfg"]))
    lines = [
        "| 书 | 信号 | 门槛 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 近一月笔 | 手续费 | 价差项 | 期末 | 强平 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, tr, oos, last, full, cfg in rows:
        lines.append(
            f"| {LABELS[name]} | {cfg['signal_mode']} | {cfg['cost_hurdle_bps']:g}bp "
            f"| {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_pct(full['equity_return'])} "
            f"| {tr['trades']} | {oos['trades']} | {last['trades']} "
            f"| {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_usd(full['ending_equity'])} | {full['liquidation_events']} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    cand = eval_out["candidate"]
    keep = eval_out[f"train_keep_{cand}"]
    step = eval_out["step"]
    if step == "F1_cost_hurdle":
        title = "修复 F1：30bp 成本门槛（5m / 5x / 120 根 log 价差）"
        knob = "本刀：**开仓必须 `|log(ETH/BTC)−μ| ≥ 30bp`**，其余冻结。"
        nxt = "下一刀（若 F1 留下则叠在 F1 上，否则仍从 B0）：F2 把 z 改成对冲残差 `cum(r_ETH − β r_BTC)`。"
    else:
        title = "修复 F2：对冲残差 z（叠在 F1 30bp 门槛上）"
        knob = "本刀：**z = 滚动 z of `cumsum(r_ETH − β r_BTC)`**，门槛仍 30bp（打在残差 bp 上）。窗口/杠杆/出场不动。"
        nxt = "下一刀（若 F2 留下则叠在 F2 上，否则仍从 F1）：F3 冷却/stride，压换手。"
    return f"""# {title}

一次只改一个旋钮。{knob}

| 项 | 取值 |
|---|---|
| 外壳 | 5 分钟 bar，120 根，\\|z\\|≥2，5x，ETH 名义 $500，平 \\|z\\|≤0.5，不止损，30bp 门槛（F1） |
| 决策 | 只看训练段扣费后权益；OOS / 近一月只评分 |

## 结果

{chr(10).join(lines)}

训练是否留下 {cand}：**{"留" if keep else "丢"}**。{eval_out["decision"]}

Gate C：**{"过" if eval_out["gate_c"]["passed"] else "未过"}**。近一月不参与选书。

{nxt}

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_repair --step F1
python -m btc_eth_perp_arb.eval_repair --step F2
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    labeled = {LABELS[k]: _clip(v, FULL_START, FULL_END) for k, v in bars.items()}
    colors = {LABELS[k]: COLORS[k] for k in bars}
    step = eval_out["step"]
    prefix = "repair-f1" if step == "F1_cost_hurdle" else "repair-f2"
    title = (
        "F1 cost hurdle vs B0 (5m 5x 120-bar log-spread, after costs)"
        if step == "F1_cost_hurdle"
        else "F2 hedge-residual z vs F1 (5m 5x 30bp hurdle, after costs)"
    )
    paths = {}
    p = media / f"{prefix}-equity.png"
    plot_named_equity(labeled, p, title, colors)
    paths["equity"] = str(p)
    oos = {LABELS[k]: _clip(v, OOS_START, OOS_END) for k, v in bars.items()}
    p = media / f"{prefix}-oos.png"
    plot_named_equity(oos, p, f"OOS {eval_out['candidate']} vs {eval_out['baseline']} (locked, not searched)", colors)
    paths["oos"] = str(p)
    return paths


def jsonable(eval_out: dict) -> dict:
    return {k: v for k, v in eval_out.items() if k != "bars_by_name"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--step", default="F2", choices=["F1", "F2", "f1", "f2"])
    p.add_argument("--media-dir", default="")
    p.add_argument("--summary-json", default="")
    p.add_argument("--report-md", default="")
    args = p.parse_args(argv)
    panel, _ = load_panel(name=CACHE_LONG)
    panel = panel[panel["bar_open_ts"] <= _end_ts(FULL_END)].reset_index(drop=True)
    eval_out = evaluate(panel, step=args.step.upper())
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
