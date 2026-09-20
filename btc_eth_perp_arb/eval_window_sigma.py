"""Pre-declared train-only grid on frozen book A: z-window × entry |z|.

Declared before looking at this step's results:

Windows: 3d, 7d (baseline), 14d, 30d — z_window = days × 1440 minutes.
Entry |z|: 2, 2.5, 3, 4. If entry is 4, stop is 5 so [4, 5) is tradable;
otherwise stop stays 4. Exit 0.5, 2x, 01:00 UTC, 30bp hurdle, beta/corr
windows stay 1d. No other knobs. Not searched on OOS / last month.

Keep a cell if train after-cost equity > 0. If several pass, pick the
single best by train (not OOS). Gate C only on that chosen book:
pre-locked OOS > 0 after costs and 0 liquidations. 7d × |z|≥3 / ≥4
are the E3/E4 cells of this grid, not a separate search.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .config import (
    DELIVERY_ENTRY_HOUR_UTC,
    DELIVERY_ENTRY_ZS_GRID,
    DELIVERY_LEVERAGE,
    DELIVERY_STOP_Z_E4,
    DELIVERY_Z_WINDOW_DAYS,
    STARTING_EQUITY,
    STOP_Z,
    delivery_config,
    delivery_grid_config,
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
from .plots import plot_named_equity, _style
from .signals import add_signals

CACHE_LONG = "aligned_1m_long.parquet"
BASELINE = "d7_z2"


def cell_name(z_days: int, entry_z: float) -> str:
    zlab = f"{entry_z:g}".replace(".", "p")
    return f"d{int(z_days)}_z{zlab}"


def cell_label(z_days: int, entry_z: float) -> str:
    tag = " (frozen A)" if int(z_days) == 7 and float(entry_z) == 2.0 else ""
    return f"{int(z_days)}d |z|≥{entry_z:g}{tag}"


def all_cells() -> list[tuple[int, float]]:
    return [(d, z) for d in DELIVERY_Z_WINDOW_DAYS for z in DELIVERY_ENTRY_ZS_GRID]


def evaluate(panel) -> dict:
    cells = all_cells()
    out: dict = {
        "step": "window_sigma_grid",
        "shell": {
            "z_days": list(DELIVERY_Z_WINDOW_DAYS),
            "entry_zs": list(DELIVERY_ENTRY_ZS_GRID),
            "leverage": DELIVERY_LEVERAGE,
            "entry_hour_utc": DELIVERY_ENTRY_HOUR_UTC,
            "exit_z": delivery_config().exit_z,
            "stop_z_default": STOP_Z,
            "stop_z_at_4": DELIVERY_STOP_Z_E4,
            "beta_window": delivery_config().beta_window,
            "corr_window": delivery_config().corr_window,
            "starting_equity": STARTING_EQUITY,
            "note": "|z|≥4 uses stop 5 a priori; beta/corr stay 1440; E3/E4 are 7d×3 and 7d×4",
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "keep_rule": "train after-cost equity return > 0; pick single best train among keepers",
        "gate_c_rule": "chosen book must have pre-locked OOS > 0 after costs and 0 liq",
        "variants": {},
    }
    bars_by_name = {}
    signaled_by_days: dict[int, object] = {}
    for z_days, entry_z in cells:
        name = cell_name(z_days, entry_z)
        cfg = delivery_grid_config(z_days=z_days, entry_z=entry_z)
        if z_days not in signaled_by_days:
            print(f"signals z_window={cfg.z_window} ({z_days}d)", flush=True)
            signaled_by_days[z_days] = add_signals(panel, cfg)
        print(
            f"sim {name} z_days={z_days} entry_z={cfg.entry_z} stop_z={cfg.stop_z}",
            flush=True,
        )
        result = run_variant(signaled_by_days[z_days], cfg, FULL_START)
        pack = {
            wname: window_pack(result, a, b, starting_equity=STARTING_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        out["variants"][name] = {
            "cfg": {
                "z_days": z_days,
                "z_window": cfg.z_window,
                "entry_z": cfg.entry_z,
                "exit_z": cfg.exit_z,
                "stop_z": cfg.stop_z,
                "leverage": cfg.leverage,
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

    keepers = []
    for z_days, entry_z in cells:
        name = cell_name(z_days, entry_z)
        train_r = out["variants"][name]["windows"]["train"]["equity_return"]
        kept = bool(train_r > 0)
        out["variants"][name]["train_keep"] = kept
        if kept:
            keepers.append((train_r, cells.index((z_days, entry_z)), name))
    keepers.sort(key=lambda t: (-t[0], t[1]))
    chosen = keepers[0][2] if keepers else None
    out["chosen_on_train"] = chosen
    out["train_keepers"] = [n for _, _, n in keepers]
    if chosen is None:
        out["gate_c"] = {
            "passed": False,
            "keep": False,
            "chosen": None,
            "train": None,
            "oos": None,
        }
        out["decision"] = (
            "drop all 16 cells (none has train after-cost equity > 0). "
            "Adjusting window/sigma cannot make this book live-ready; do not keep searching."
        )
    else:
        pack = out["variants"][chosen]["windows"]
        oos_r = pack["oos"]["equity_return"]
        oos_liq = pack["oos"]["liquidation_events"]
        full_liq = pack["full"]["liquidation_events"]
        passed = bool(oos_r > 0 and oos_liq == 0 and full_liq == 0)
        g = {
            "passed": passed,
            "keep": True,
            "chosen": chosen,
            "train": pack["train"]["equity_return"],
            "oos": oos_r,
            "last_month": pack["last_month"]["equity_return"],
            "oos_liq": oos_liq,
            "full_liq": full_liq,
        }
        out["variants"][chosen]["gate_c"] = g
        out["gate_c"] = g
        if passed:
            out["decision"] = (
                f"keep {chosen} (best train among train>0); Gate C scored on pre-locked OOS"
            )
        else:
            out["decision"] = (
                f"drop {chosen} for live (best train among train>0, but pre-locked OOS "
                "not positive after costs or liquidations). Adjusting window/sigma cannot "
                "make this book live-ready; do not keep searching."
            )
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    lines = [
        "| 窗 | |z| | 止损 | 训练 | OOS | 近一月 | 训练笔 | OOS笔 | 手续费 | 价差项 | 期末 | 强平 | 训练>0 | 选中 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    chosen = eval_out.get("chosen_on_train")
    for z_days, entry_z in all_cells():
        name = cell_name(z_days, entry_z)
        v = eval_out["variants"][name]
        tr, oos, last, full = (v["windows"][k] for k in ("train", "oos", "last_month", "full"))
        keep_s = "是" if v.get("train_keep") else "否"
        pick_s = "★" if name == chosen else ""
        lines.append(
            f"| {z_days}d | {entry_z:g} | {v['cfg']['stop_z']:g} "
            f"| {_pct(tr['equity_return'])} | {_pct(oos['equity_return'])} "
            f"| {_pct(last['equity_return'])} | {tr['trades']} | {oos['trades']} "
            f"| {_usd(full['fee_sum'])} | {_usd(full['spread_like_pnl'])} "
            f"| {_usd(full['ending_equity'])} | {full['liquidation_events']} "
            f"| {keep_s} | {pick_s} |"
        )
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    chosen_s = chosen or "(none)"
    return f"""# 冻结书 A：rolling window × entry |z| 训练网格

预先锁死 4×4，只改这两个旋钮。不搜 OOS / 近一月。训练扣费后权益 **> 0** 才是候选；多个候选只留 **训练最好的一本**，再用预锁 OOS 打 Gate C。

| 旋钮 | 取值 |
|---|---|
| z 窗口 | 3d / 7d（基线）/ 14d / 30d（×1440 分钟） |
| 入场 \\|z\\| | 2 / 2.5 / 3 / 4 |
| 止损 | 4；入场 4 时 **预先** 提到 5（否则 [entry, stop) 为空） |
| 冻结 | 2x，01:00 UTC，exit 0.5，30bp 门槛，β/相关仍 1d |

7d × 3σ / 4σ 就是 E3/E4，折进这张表，不是另一次搜参。

选中（仅训练）：`{chosen_s}`

## 网格（OOS 只评分，不选书）

{chr(10).join(lines)}

{eval_out["decision"]}

## 图表

{chart_lines}

```bash
python -m btc_eth_perp_arb.eval_window_sigma
python -m pytest btc_eth_perp_arb/tests -q
```
"""


def _heatmap(eval_out: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days = list(DELIVERY_Z_WINDOW_DAYS)
    zs = list(DELIVERY_ENTRY_ZS_GRID)
    mat = np.full((len(days), len(zs)), np.nan)
    for i, d in enumerate(days):
        for j, z in enumerate(zs):
            mat[i, j] = eval_out["variants"][cell_name(d, z)]["windows"]["train"][
                "equity_return"
            ]
    _style()
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    im = ax.imshow(mat * 100.0, cmap="RdBu", vmin=-15, vmax=15, aspect="auto")
    ax.set_xticks(range(len(zs)), [f"{z:g}" for z in zs])
    ax.set_yticks(range(len(days)), [f"{d}d" for d in days])
    ax.set_xlabel("entry |z|")
    ax.set_ylabel("z window")
    ax.set_title("Train after-cost equity return (%) — pick on this, not OOS")
    for i in range(len(days)):
        for j in range(len(zs)):
            ax.text(
                j,
                i,
                f"{mat[i, j]:+.1%}",
                ha="center",
                va="center",
                fontsize=8,
                color="#111111",
            )
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    paths: dict[str, str] = {}
    p = media / "window-sigma-grid-train-heatmap.png"
    _heatmap(eval_out, p)
    paths["heatmap"] = str(p)

    chosen = eval_out.get("chosen_on_train")
    names = [BASELINE]
    if chosen and chosen not in names:
        names.append(chosen)
    for n in eval_out.get("train_keepers") or []:
        if n not in names and len(names) < 6:
            names.append(n)
    colors = {
        cell_label(7, 2.0): "#1f4e79",
        cell_label(7, 2.5): "#c45911",
        cell_label(7, 3.0): "#d62728",
        cell_label(7, 4.0): "#6a3d9a",
        cell_label(3, 2.0): "#2ca02c",
        cell_label(14, 2.0): "#17becf",
        cell_label(30, 2.0): "#8c564b",
    }
    labeled = {}
    color_map = {}
    for name in names:
        v = eval_out["variants"][name]
        lab = cell_label(v["cfg"]["z_days"], v["cfg"]["entry_z"])
        labeled[lab] = _clip(bars[name], FULL_START, FULL_END)
        color_map[lab] = colors.get(lab, "#333333")
    p = media / "window-sigma-grid-equity.png"
    plot_named_equity(
        labeled, p, "Window×sigma: frozen A vs train-selected (after costs)", color_map
    )
    paths["equity"] = str(p)
    oos = {
        cell_label(eval_out["variants"][n]["cfg"]["z_days"], eval_out["variants"][n]["cfg"]["entry_z"]): _clip(
            bars[n], OOS_START, OOS_END
        )
        for n in names
    }
    p = media / "window-sigma-grid-oos.png"
    plot_named_equity(oos, p, "OOS window×sigma (locked, not searched)", color_map)
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
