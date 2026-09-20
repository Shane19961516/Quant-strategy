"""Leverage sweep on the already-chosen 14d × |z|≥2 book.

Declared before looking at this step's results:

Shell frozen: 14-day residual z, |z|∈[2,4), exit 0.5, stop 4, daily 01:00 UTC,
30bp hurdle, $100k. That cell won the window×sigma grid on train. Only
leverage changes.

Sweep: 1, 2 (current), 5, 10, 20, 50. Keep a row if train after-cost equity
> 0 (not vs 2x — |return| scales with leverage until liquidation). Do not
search OOS / last month. Gate C per row: kept and pre-locked OOS > 0 after
costs and 0 liquidations. Expect near-linear scaling until path/liq kick in.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    CHOSEN_ENTRY_Z,
    CHOSEN_Z_DAYS,
    DELIVERY_SWEEP_LEVERAGES,
    STARTING_EQUITY,
    delivery_chosen_config,
)
from .data import load_panel
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
COLORS = {
    1.0: "#2ca02c",
    2.0: "#1f4e79",
    5.0: "#c45911",
    10.0: "#d62728",
    20.0: "#9467bd",
    50.0: "#8c564b",
}


def _name(lev: float) -> str:
    return f"x{int(lev)}"


def _label(lev: float) -> str:
    tag = " (current)" if float(lev) == 2.0 else ""
    return f"{int(lev)}x{tag}"


def evaluate(panel) -> dict:
    base = delivery_chosen_config()
    signaled = add_signals(panel, base)
    out: dict = {
        "step": "leverage_sweep_14d",
        "shell": {
            "z_days": CHOSEN_Z_DAYS,
            "z_window": base.z_window,
            "entry_z": CHOSEN_ENTRY_Z,
            "exit_z": base.exit_z,
            "stop_z": base.stop_z,
            "entry_hour_utc": base.entry_hour_utc,
            "leverages": list(DELIVERY_SWEEP_LEVERAGES),
            "starting_equity": STARTING_EQUITY,
            "note": "chosen window×sigma cell; leverage only",
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "keep_rule": "train after-cost equity return > 0 (not vs 2x)",
        "gate_c_rule": "kept and pre-locked OOS > 0 after costs and 0 liquidations",
        "variants": {},
    }
    bars_by_name = {}
    for lev in DELIVERY_SWEEP_LEVERAGES:
        name = _name(lev)
        cfg = delivery_chosen_config(leverage=float(lev))
        print(f"sim {name} lev={cfg.leverage} z_days={CHOSEN_Z_DAYS} entry_z={cfg.entry_z}", flush=True)
        result = run_variant(signaled, cfg, FULL_START)
        pack = {
            wname: window_pack(result, a, b, starting_equity=STARTING_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        train_r = pack["train"]["equity_return"]
        oos_r = pack["oos"]["equity_return"]
        oos_liq = pack["oos"]["liquidation_events"]
        full_liq = pack["full"]["liquidation_events"]
        kept = bool(train_r > 0)
        gate = {
            "passed": bool(kept and oos_r > 0 and oos_liq == 0 and full_liq == 0),
            "keep": kept,
            "train": train_r,
            "oos": oos_r,
            "oos_liq": oos_liq,
            "full_liq": full_liq,
        }
        out["variants"][name] = {
            "cfg": {
                "leverage": cfg.leverage,
                "z_window": cfg.z_window,
                "entry_z": cfg.entry_z,
                "exit_z": cfg.exit_z,
                "stop_z": cfg.stop_z,
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
    keepers = [n for n in (_name(x) for x in DELIVERY_SWEEP_LEVERAGES) if out["variants"][n]["train_keep"]]
    passed = [n for n in keepers if out["variants"][n]["gate_c"]["passed"]]
    blown = [
        n
        for n in (_name(x) for x in DELIVERY_SWEEP_LEVERAGES)
        if out["variants"][n]["windows"]["full"]["liquidation_events"] > 0
    ]
    out["train_keepers"] = keepers
    out["gate_c"] = {
        "passed": bool(passed),
        "rule": out["gate_c_rule"],
        "passed_rows": passed,
        "blown": blown,
    }
    if blown:
        blow_s = ", ".join(blown) + " hit liquidations (path/liq, not linear scale)."
    else:
        blow_s = "no liquidations in this sweep."
    out["decision"] = (
        f"keep {', '.join(keepers) if keepers else 'none'} on train > 0; "
        f"Gate C pass {', '.join(passed) if passed else 'none'}. {blow_s} "
        "2x remains the chosen shell's leverage; this sweep is not an OOS re-pick."
    )
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| 杠杆 | 训练 | OOS | 近一月 | 手续费 | 价差项 | 训练 MDD | 全样本 MDD | 强平 | 训练留 | Gate C |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for lev in DELIVERY_SWEEP_LEVERAGES:
        v = eval_out["variants"][_name(lev)]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        keep_s = "留" if v.get("train_keep") else "丢"
        gate_s = "过" if v.get("gate_c", {}).get("passed") else "未过"
        lines.append(
            f"| {_label(lev)} | {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {_usd(full['fee_sum'])} "
            f"| {_usd(full['spread_like_pnl'])} | {_pct(tr['max_drawdown'])} "
            f"| {_pct(full['max_drawdown'])} | {full['liquidation_events']} "
            f"| {keep_s} | {gate_s} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    return f"""# 14d × |z|≥2：杠杆 1 / 2 / 5 / 10 / 20 / 50

外壳冻结：14 日残差 z，`|z|∈[2,4)`，exit 0.5，stop 4，每天 01:00 UTC，30bp 门槛。只改杠杆。不搜 OOS / 近一月。训练扣费后权益 **> 0** 才留（不是跟 2x 比大小）。Gate C 逐行：留下且预锁 OOS > 0、0 强平。

收益应近似随杠杆放大，直到强平/路径打断线性。交叉保证金强平条件是权益 < 0.4% × 毛名义。

## 结果

{chr(10).join(lines)}

{eval_out["decision"]}

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_leverage_sweep
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    labeled = {}
    colors = {}
    for lev in DELIVERY_SWEEP_LEVERAGES:
        lab = _label(lev)
        labeled[lab] = _clip(bars[_name(lev)], FULL_START, FULL_END)
        colors[lab] = COLORS[float(lev)]
    paths = {}
    p = media / "leverage-sweep-14d-equity.png"
    plot_named_equity(
        labeled, p, "14d |z|≥2 leverage sweep (after costs)", colors
    )
    paths["equity"] = str(p)
    oos = {
        _label(lev): _clip(bars[_name(lev)], OOS_START, OOS_END)
        for lev in DELIVERY_SWEEP_LEVERAGES
    }
    p = media / "leverage-sweep-14d-oos.png"
    plot_named_equity(oos, p, "OOS 14d leverage sweep (locked, not searched)", colors)
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
