"""User-declared 1-minute z-grid: log-spread vs BTC/ETH ratio.

24 books: 2 series × {120, 240} bars × |z| in {2, 2.5, 3} × {5x, 10x}.
$1000 start, $100 ticket × leverage ETH notional, rolling β hedge, no stop.
Train 2025-10-01→2026-06-30 ranks; 2026 OOS and last month score only.
Not a delivery search — last month is never used to pick.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import (
    ZGRID_ENTRY_ZS,
    ZGRID_EQUITY,
    ZGRID_LEVERAGES,
    ZGRID_TICKET_USD,
    ZGRID_WINDOWS,
    zgrid_config,
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
    window_pack,
)
from .metrics import monthly_table
from .plots import plot_named_equity
from .signals import add_signals
from .simulator import run_simulator

SCHEMES = ("spread", "ratio")
CACHE_LONG = "aligned_1m_long.parquet"

SCHEME_LABEL = {
    "spread": "log(ETH/BTC) z",
    "ratio": "BTC/ETH ratio z",
}


def combo_name(scheme: str, z_window: int, entry_z: float, leverage: float) -> str:
    zlab = f"{entry_z:g}".replace(".", "p")
    return f"{scheme}_w{z_window}_z{zlab}_x{int(leverage)}"


def combo_label(scheme: str, z_window: int, entry_z: float, leverage: float) -> str:
    return f"{scheme} {z_window}m |z|≥{entry_z:g} {int(leverage)}x"


def all_combos() -> list[tuple[str, int, float, float]]:
    return [
        (scheme, win, z, lev)
        for scheme in SCHEMES
        for win in ZGRID_WINDOWS
        for z in ZGRID_ENTRY_ZS
        for lev in ZGRID_LEVERAGES
    ]


def evaluate(panel: pd.DataFrame) -> dict:
    combos = all_combos()
    signaled: dict[tuple[str, int], pd.DataFrame] = {}
    for scheme in SCHEMES:
        for win in ZGRID_WINDOWS:
            cfg = zgrid_config(scheme=scheme, z_window=win, entry_z=2.0, leverage=5.0)
            print(f"signals {scheme} window={win}", flush=True)
            signaled[(scheme, win)] = add_signals(panel, cfg)

    out: dict = {
        "note": (
            "User-declared 24-combo 1m grid. Train ranks; OOS/last-month score only. "
            "Not a delivery book. Last month is never used to pick."
        ),
        "account": {
            "starting_equity": ZGRID_EQUITY,
            "ticket_usd": ZGRID_TICKET_USD,
            "eth_notional": "ticket × leverage",
            "btc_notional": "|β| × ETH notional (β window = z window)",
            "stop": "none (no stop_z, no time stop, corr gate off)",
            "exit_z": 0.5,
        },
        "windows": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in WINDOWS.items()},
        "variants": {},
    }
    bars_by_name: dict[str, pd.DataFrame] = {}

    for scheme, win, entry_z, lev in combos:
        name = combo_name(scheme, win, entry_z, lev)
        cfg = zgrid_config(scheme=scheme, z_window=win, entry_z=entry_z, leverage=lev)
        print(
            f"sim {name} mode={cfg.signal_mode} invert={cfg.invert_signal} "
            f"ticket={cfg.eth_ticket_usd}×{cfg.leverage:.0f}",
            flush=True,
        )
        result = run_simulator(signaled[(scheme, win)], cfg, trade_start_ts=_ts(FULL_START))
        pack = {
            wname: window_pack(result, a, b, starting_equity=ZGRID_EQUITY)
            for wname, (a, b) in WINDOWS.items()
        }
        monthly = monthly_table(result, pnl_start_ts=_ts(FULL_START))
        out["variants"][name] = {
            "cfg": {
                "scheme": scheme,
                "signal_mode": cfg.signal_mode,
                "invert_signal": cfg.invert_signal,
                "z_window": cfg.z_window,
                "beta_window": cfg.beta_window,
                "entry_z": cfg.entry_z,
                "exit_z": cfg.exit_z,
                "stop_z": cfg.stop_z,
                "leverage": cfg.leverage,
                "starting_equity": cfg.starting_equity,
                "eth_ticket_usd": cfg.eth_ticket_usd,
                "notional_times_leverage": cfg.notional_times_leverage,
            },
            "windows": pack,
            "monthly": monthly.to_dict(orient="records"),
        }
        bars_by_name[name] = result.bars
        print(
            json.dumps(
                {
                    "name": name,
                    "full": pack["full"]["equity_return"],
                    "train": pack["train"]["equity_return"],
                    "oos": pack["oos"]["equity_return"],
                    "last_month": pack["last_month"]["equity_return"],
                    "full_trades": pack["full"]["trades"],
                    "train_trades": pack["train"]["trades"],
                    "oos_trades": pack["oos"]["trades"],
                    "last_trades": pack["last_month"]["trades"],
                    "liq": pack["full"]["liquidation_events"],
                    "fees": pack["full"]["fee_sum"],
                    "funding": pack["full"]["funding_sum"],
                    "spread": pack["full"]["spread_like_pnl"],
                    "mdd": pack["full"]["max_drawdown"],
                    "end_eq": pack["full"]["ending_equity"],
                    "exit_reasons": pack["full"]["exit_reasons"],
                },
                indent=2,
            ),
            flush=True,
        )

    train_rets = {n: v["windows"]["train"]["equity_return"] for n, v in out["variants"].items()}
    out["train_rank"] = sorted(train_rets, key=train_rets.get, reverse=True)
    chosen = out["train_rank"][0]
    chosen_oos = out["variants"][chosen]["windows"]["oos"]["equity_return"]
    chosen_liq = out["variants"][chosen]["windows"]["oos"]["liquidation_events"]
    out["chosen_on_train"] = chosen
    out["gate_c"] = {
        "passed": bool(chosen_oos > 0 and chosen_liq == 0),
        "rule": "pre-locked OOS equity return after costs must be > 0 with 0 liquidations",
        "note": "train-best of this user grid is not a delivery pick; last month is not used",
        "oos_returns": {n: v["windows"]["oos"]["equity_return"] for n, v in out["variants"].items()},
        "chosen": chosen,
    }
    out["bars_by_name"] = bars_by_name
    return out


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _usd(x: float) -> str:
    return f"{x:,.2f}"


def render_report(eval_out: dict, charts: dict[str, str]) -> str:
    rows = []
    for name, v in eval_out["variants"].items():
        cfg = v["cfg"]
        tr = v["windows"]["train"]
        oos = v["windows"]["oos"]
        last = v["windows"]["last_month"]
        full = v["windows"]["full"]
        rows.append(
            {
                "name": name,
                "scheme": cfg["scheme"],
                "window": cfg["z_window"],
                "z": cfg["entry_z"],
                "lev": cfg["leverage"],
                "train": tr["equity_return"],
                "oos": oos["equity_return"],
                "last": last["equity_return"],
                "full": full["equity_return"],
                "train_n": tr["trades"],
                "oos_n": oos["trades"],
                "last_n": last["trades"],
                "full_n": full["trades"],
                "mdd": full["max_drawdown"],
                "liq": full["liquidation_events"],
                "fees": full["fee_sum"],
                "funding": full["funding_sum"],
                "spread": full["spread_like_pnl"],
                "end_eq": full["ending_equity"],
                "train_fees": tr["fee_sum"],
                "oos_fees": oos["fee_sum"],
                "train_spread": tr["spread_like_pnl"],
                "oos_spread": oos["spread_like_pnl"],
                "train_liq": tr["liquidation_events"],
                "oos_liq": oos["liquidation_events"],
            }
        )
    df = pd.DataFrame(rows)

    def table(subset: pd.DataFrame) -> str:
        lines = [
            "| 书 | 训练 | OOS | 近一月 | 全样本 | 训练笔 | OOS笔 | 近一月笔 | 全样本 MDD | 强平 | 手续费 | 价差项 | 期末 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in subset.itertuples(index=False):
            lines.append(
                f"| `{r.name}` | {_pct(r.train)} | {_pct(r.oos)} | {_pct(r.last)} | {_pct(r.full)} "
                f"| {r.train_n} | {r.oos_n} | {r.last_n} | {_pct(r.mdd)} | {r.liq} "
                f"| {_usd(r.fees)} | {_usd(r.spread)} | {_usd(r.end_eq)} |"
            )
        return "\n".join(lines)

    blocks = []
    for scheme in SCHEMES:
        for win in ZGRID_WINDOWS:
            sub = df[(df["scheme"] == scheme) & (df["window"] == win)].copy()
            blocks.append(f"### {SCHEME_LABEL[scheme]}，窗口 {win} 根 1m bar\n\n{table(sub)}")

    chosen = eval_out["chosen_on_train"]
    gate = eval_out["gate_c"]
    oos_pos = [n for n, r in gate["oos_returns"].items() if r > 0]
    liq_any = [r["name"] for r in rows if r["liq"] > 0]
    chart_lines = "\n".join(f"- `{p}`" for p in charts.values()) if charts else "(none)"
    train_min = min(r["train"] for r in rows)
    train_max = max(r["train"] for r in rows)
    fee_min = min(r["fees"] for r in rows)
    fee_max = max(r["fees"] for r in rows)
    eq5 = [r["end_eq"] for r in rows if r["lev"] == 5]
    eq10 = [r["end_eq"] for r in rows if r["lev"] == 10]

    return f"""# 1 分钟价差 / 比值 z 网格（$1000，票面 $100×杠杆）

**24 组全部训练段亏完。** 5x 期末约 $19–21，10x 约 $9。手续费 {_usd(fee_min)}–{_usd(fee_max)} 先吃掉本金；价差/滑点项也全是负的。强平 0 — 不是爆仓，是换手把账户磨死。Gate C **未过**。近一月 24 组都是 0 笔 / 0%，因为训练结束时账户只剩几十美金，BTC 0.001 张已经下不进去；**近一月不参与选书。**

这是用户指定的 24 组 1 分钟网格，**不是**交付书，也没有在 2026-07–09 或近一月上搜参。训练段（2025-10-01→2026-06-30）只用来排序；OOS 与近一月只打分。

方案 2（BTC/ETH 比值 + invert）和方案 1（log 价差）几乎是同一本书：`z(BTC/ETH) ≈ −z(log ETH/BTC)`，对侧之后权益曲线贴在一起。

冻结的慢速书 A / funding / expanding 价差 **没有**改状态机。

## 规则（24 组共用）

| 项 | 取值 |
|---|---|
| 数据 | Binance USDⓈ-M BTCUSDT + ETHUSDT 永续，1 分钟 last/mark，双腿 inner 对齐，无 close ffill |
| 本金 | {ZGRID_EQUITY:.0f} USDT |
| 下单 | ETH 名义 = {ZGRID_TICKET_USD:.0f} × 杠杆（5x→500，10x→1000）；BTC 名义 = \\|β\\| × ETH |
| 对冲 | 与 z 同窗口的滚动 β（`shift(1)`，入场冻结） |
| 方案 1 | `z = (log(ETH/BTC) − μ) / σ`，μ/σ 用过去 120 或 240 根 bar |
| 方案 2 | `z = (BTC/ETH − μ) / σ`，同样窗口；高比值做空 BTC / 做多 ETH（`invert_signal`） |
| 开仓 | \\|z\\| ≥ x，x ∈ {{2, 2.5, 3}}；每分钟可开；无 01:00 门、无冷却、无 30bp 门槛 |
| 平仓 | \\|z\\| ≤ 0.5（回归止盈，**不止损**） |
| 杠杆 | 5x 与 10x 交叉保证金；维持保证金 0.4% |
| 成本 | VIP0 taker 5bp + 半价差 BTC 0.5bp / ETH 1.0bp + 冲击上限 10bp |
| 前视 | bar t 的 z/β 只在 bar t+1 open 成交 |

## 一句话对照

| | 训练权益 | 全样本期末 | 手续费 | 价差项 | 强平 |
|---|---|---|---|---|---|
| 24 组全体 | {_pct(train_min)} → {_pct(train_max)} | 5x ~{_usd(min(eq5))}–{_usd(max(eq5))}；10x ~{_usd(min(eq10))}–{_usd(max(eq10))} | {_usd(fee_min)}–{_usd(fee_max)} | 全部为负 | 0 |

OOS 百分比是路径依赖：相对训练后剩下的 ~$10–20 再算，不是重新拿 $1000 做样本外。多数组 OOS 只剩 0–3 笔。`spread_w240_z3_x5` 训练“最好”（−97.13%），OOS −26.89%（58 笔）——只是死得慢一点。

## 训练 / OOS / 近一月（扣费 + funding）

{chr(10).join(blocks)}

## 训练排序与 Gate C

训练段权益收益最高：`{chosen}`。该组 OOS = {_pct(eval_out["variants"][chosen]["windows"]["oos"]["equity_return"])}，OOS 强平 {eval_out["variants"][chosen]["windows"]["oos"]["liquidation_events"]}。

Gate C（预先锁死：扣费后 OOS > 0 且 0 强平）：**{"过" if gate["passed"] else "未过"}**。OOS 为正的组：{oos_pos if oos_pos else "无"}。全样本出现强平的组：{liq_any if liq_any else "无"}。

近一月数字只作切片，**不**据此选 x、窗口或杠杆。

## 怎么读这些数字

1 分钟、\\|z\\|≥2、120/240 根窗口会高频穿越（训练段 500–1170 笔）。两腿 taker 开平大约 20bp 名义成本；票面 $100×5/10 时，一轮费用相对 $1000 本金是几十 bp。换手一高，手续费会先吃掉均值回复，价差项也没有把费用赚回来。这和已经死掉的 1 分钟 10x 残差书是同一类微观结构，不是新的慢因子。

## 图表

{chart_lines}

## 复现

```bash
python -m btc_eth_perp_arb.eval_zgrid --long-cache
python -m pytest btc_eth_perp_arb/tests -q
```
"""


GROUP_COLORS = {
    "z2 5x": "#1f4e79",
    "z2 10x": "#6baed6",
    "z2.5 5x": "#2ca02c",
    "z2.5 10x": "#98df8a",
    "z3 5x": "#c45911",
    "z3 10x": "#fd8d3c",
}


def _group_key(entry_z: float, leverage: float) -> str:
    return f"z{entry_z:g} {int(leverage)}x"


def write_charts(eval_out: dict, media: Path) -> dict[str, str]:
    media.mkdir(parents=True, exist_ok=True)
    bars = eval_out["bars_by_name"]
    paths: dict[str, str] = {}

    for scheme in SCHEMES:
        for win in ZGRID_WINDOWS:
            labeled = {}
            colors = {}
            for entry_z in ZGRID_ENTRY_ZS:
                for lev in ZGRID_LEVERAGES:
                    name = combo_name(scheme, win, entry_z, lev)
                    key = _group_key(entry_z, lev)
                    labeled[key] = _clip(bars[name], FULL_START, FULL_END)
                    colors[key] = GROUP_COLORS[key]
            p = media / f"zgrid-{scheme}-w{win}-equity.png"
            plot_named_equity(
                labeled,
                p,
                f"{SCHEME_LABEL[scheme]}  window={win}  $1000 / $100×lev (after costs)",
                colors=colors,
            )
            paths[f"{scheme}_w{win}"] = str(p)

            oos_map = {
                _group_key(z, lev): _clip(
                    bars[combo_name(scheme, win, z, lev)], OOS_START, OOS_END
                )
                for z in ZGRID_ENTRY_ZS
                for lev in ZGRID_LEVERAGES
            }
            p = media / f"zgrid-{scheme}-w{win}-oos.png"
            plot_named_equity(
                oos_map,
                p,
                f"OOS {SCHEME_LABEL[scheme]} window={win} (locked, not searched)",
                colors=GROUP_COLORS,
            )
            paths[f"{scheme}_w{win}_oos"] = str(p)

    # Train vs OOS scatter-style table as bars.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .plots import _style

    names = list(eval_out["variants"])
    train = [eval_out["variants"][n]["windows"]["train"]["equity_return"] * 100 for n in names]
    oos = [eval_out["variants"][n]["windows"]["oos"]["equity_return"] * 100 for n in names]
    _style()
    fig, ax = plt.subplots(figsize=(11, 7))
    y = range(len(names))
    ax.barh([i + 0.15 for i in y], train, height=0.3, color="#1f4e79", label="train")
    ax.barh([i - 0.15 for i in y], oos, height=0.3, color="#c45911", label="OOS")
    ax.axvline(0.0, color="#888", lw=0.8)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("Equity return (%)")
    ax.set_title("1m z-grid train vs OOS (after costs) — last month not shown")
    ax.legend(loc="best")
    fig.tight_layout()
    p = media / "zgrid-train-vs-oos.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    paths["train_vs_oos"] = str(p)
    return paths


def jsonable(eval_out: dict) -> dict:
    return {k: v for k, v in eval_out.items() if k != "bars_by_name"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--media-dir", default="")
    p.add_argument("--summary-json", default="")
    p.add_argument("--report-md", default="")
    p.add_argument("--long-cache", action="store_true", default=True)
    args = p.parse_args(argv)

    panel, _manifest = load_panel(name=CACHE_LONG)
    end_ts = _end_ts(FULL_END)
    panel = panel[panel["bar_open_ts"] <= end_ts].reset_index(drop=True)
    eval_out = evaluate(panel)
    paths: dict[str, str] = {}
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
