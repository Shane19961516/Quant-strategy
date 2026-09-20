"""Coarser-bar residual arb: 5m baseline vs 10 / 60 / 120 minutes.

Declared before looking at this step's results:

Frozen shell (same family as the train-chosen 14d residual book):
- signal: lagged residual z of log(ETH/BTC), rolling β
- calendar windows, not bar count: z = 14 days, β/corr = 1 day,
  max hold = 5 days, cooldown = 1 day
- |z|∈[2,4), exit 0.5, stop 4, 2x, 30bp hurdle, $100k
- every bar is a decision (signal t, fill t+1 open). No 01:00 UTC gate,
  so bar length changes trading rate. Not the dead 1-minute 10x book.

Only bar_minutes changes: 5 (baseline; 5m arb was unstable), 10, 60, 120.
Keep 10/60/120 if train after-cost equity > 0. Beating a still-negative 5m
is not a keep. Do not search OOS / last month. Gate C if kept and
pre-locked OOS > 0 after costs and 0 liquidations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    BAR_ARB_MINUTES,
    BAR_ARB_Z_DAYS,
    STARTING_EQUITY,
    bar_arb_config,
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
BASELINE_MINUTES = 5
COLORS = {
    5: "#c45911",
    10: "#2ca02c",
    60: "#1f4e79",
    120: "#6a3d9a",
}


def _name(minutes: int) -> str:
    return f"b{int(minutes)}m"


def _label(minutes: int) -> str:
    tag = " (baseline)" if int(minutes) == BASELINE_MINUTES else ""
    return f"{int(minutes)}m{tag}"


def evaluate(panel) -> dict:
    out: dict = {
        "step": "bar_10_60_120",
        "shell": {
            "signal": "residual z log(ETH/BTC)",
            "z_calendar_days": BAR_ARB_Z_DAYS,
            "beta_calendar_minutes": 1440,
            "entry_z": 2.0,
            "exit_z": 0.5,
            "stop_z": 4.0,
            "leverage": 2.0,
            "entry_hour_utc": None,
            "bar_minutes": list(BAR_ARB_MINUTES),
            "starting_equity": STARTING_EQUITY,
            "note": "calendar windows fixed; 5m is baseline; not 1m 10x",
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "keep_rule": "10/60/120 train after-cost equity > 0 (not merely better than 5m)",
        "gate_c_rule": "kept and pre-locked OOS > 0 after costs and 0 liquidations",
        "variants": {},
    }
    bars_by_name = {}
    for minutes in BAR_ARB_MINUTES:
        cfg = bar_arb_config(bar_minutes=minutes)
        print(f"resample 1m → {minutes}m", flush=True)
        bars = resample_panel(panel, minutes)
        print(
            f"sim {_name(minutes)} rows={len(bars)} z_window={cfg.z_window} "
            f"beta={cfg.beta_window} hour={cfg.entry_hour_utc}",
            flush=True,
        )
        signaled = add_signals(bars, cfg)
        result = run_variant(signaled, cfg, FULL_START)
        pack = {
            wname: window_pack(result, a, b, starting_equity=STARTING_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        is_base = int(minutes) == BASELINE_MINUTES
        train_r = pack["train"]["equity_return"]
        oos_r = pack["oos"]["equity_return"]
        oos_liq = pack["oos"]["liquidation_events"]
        full_liq = pack["full"]["liquidation_events"]
        kept = False if is_base else bool(train_r > 0)
        gate = {
            "passed": bool(kept and oos_r > 0 and oos_liq == 0 and full_liq == 0),
            "keep": kept,
            "baseline": is_base,
            "train": train_r,
            "oos": oos_r,
            "oos_liq": oos_liq,
            "full_liq": full_liq,
        }
        name = _name(minutes)
        out["variants"][name] = {
            "cfg": {
                "bar_minutes": minutes,
                "z_window": cfg.z_window,
                "beta_window": cfg.beta_window,
                "entry_z": cfg.entry_z,
                "exit_z": cfg.exit_z,
                "stop_z": cfg.stop_z,
                "leverage": cfg.leverage,
                "entry_hour_utc": cfg.entry_hour_utc,
            },
            "windows": pack,
            "train_keep": kept,
            "gate_c": gate,
            "monthly": monthly_table(result, pnl_start_ts=_ts(FULL_START)).to_dict(
                orient="records"
            ),
        }
        bars_by_name[name] = result.bars
        print(
            json.dumps(
                {
                    "name": name,
                    "train": train_r,
                    "oos": oos_r,
                    "last_month": pack["last_month"]["equity_return"],
                    "train_trades": pack["train"]["trades"],
                    "oos_trades": pack["oos"]["trades"],
                    "fees": pack["full"]["fee_sum"],
                    "spread": pack["full"]["spread_like_pnl"],
                    "mdd": pack["full"]["max_drawdown"],
                    "end_eq": pack["full"]["ending_equity"],
                    "liq": full_liq,
                    "keep": kept,
                    "gate_c": gate["passed"],
                },
                indent=2,
            ),
            flush=True,
        )
    keepers = [
        _name(m)
        for m in BAR_ARB_MINUTES
        if m != BASELINE_MINUTES and out["variants"][_name(m)]["train_keep"]
    ]
    passed = [n for n in keepers if out["variants"][n]["gate_c"]["passed"]]
    base_tr = out["variants"][_name(BASELINE_MINUTES)]["windows"]["train"]["equity_return"]
    out["train_keepers"] = keepers
    out["gate_c"] = {
        "passed": bool(passed),
        "rule": out["gate_c_rule"],
        "passed_rows": passed,
        "baseline_train": base_tr,
    }
    if keepers:
        fail = [n for n in keepers if not out["variants"][n]["gate_c"]["passed"]]
        extra = f" Gate C fail {', '.join(fail)}." if fail else ""
        out["decision"] = (
            f"keep {', '.join(keepers)} on train > 0 vs 5m baseline "
            f"({base_tr:+.2%}); Gate C pass {', '.join(passed) if passed else 'none'}."
            + extra
        )
    else:
        out["decision"] = (
            f"drop 10m/60m/120m (none has train after-cost equity > 0; "
            f"5m baseline {base_tr:+.2%}). Coarser bars are not a live save "
            "if the residual still cannot cover costs."
        )
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| bar | z根 | 训练 | OOS | 近一月 | 训练笔 | OOS笔 | 手续费 | 价差项 | MDD | 期末 | 强平 | 训练留 | Gate C |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for minutes in BAR_ARB_MINUTES:
        v = eval_out["variants"][_name(minutes)]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        is_base = int(minutes) == BASELINE_MINUTES
        keep_s = "—" if is_base else ("留" if v.get("train_keep") else "丢")
        gate_s = "—" if is_base else ("过" if v.get("gate_c", {}).get("passed") else "未过")
        lines.append(
            f"| {_label(minutes)} | {v['cfg']['z_window']} "
            f"| {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {tr['trades']} | {oos['trades']} "
            f"| {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_pct(full['max_drawdown'])} | {_usd(full['ending_equity'])} "
            f"| {full['liquidation_events']} | {keep_s} | {gate_s} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    return f"""# 残差套利 bar：5m 基线 vs 10 / 60 / 120 分钟

只改 K 线周期。日历窗口冻结 14 日残差 z（与训练选中的 14d 书同一族），不是 5m×240 的 20h。入场 `|z|≥2`，出场 0.5，止损 4，2x。每根 bar 决策（信号 t，t+1 开盘成交），没有 01:00 门。不是死掉的 1 分钟 10x。

5 分钟是基线；10/60/120 训练扣费后权益 **> 0** 才留。只比 5m 少亏不够。不搜 OOS / 近一月。

## 结果

{chr(10).join(lines)}

{eval_out["decision"]}

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_bar_clock
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    labeled = {}
    colors = {}
    for minutes in BAR_ARB_MINUTES:
        lab = _label(minutes)
        labeled[lab] = _clip(bars[_name(minutes)], FULL_START, FULL_END)
        colors[lab] = COLORS[int(minutes)]
    paths = {}
    p = media / "bar-10-60-120-equity.png"
    plot_named_equity(
        labeled, p, "Residual arb bar clock: 5m vs 10/60/120 (after costs)", colors
    )
    paths["equity"] = str(p)
    oos = {
        _label(m): _clip(bars[_name(m)], OOS_START, OOS_END) for m in BAR_ARB_MINUTES
    }
    p = media / "bar-10-60-120-oos.png"
    plot_named_equity(oos, p, "OOS bar clock (locked, not searched)", colors)
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
