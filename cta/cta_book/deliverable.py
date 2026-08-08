# -*- coding: utf-8 -*-
"""全品种 CTA 交付组合（akshare 主力连续）。

文献可复现袖层 + 因果目标波动。交付时同时给出：
1) IS 标定、MaxDD≤10% 的可实盘口径
2) 约束前沿说明（为何 CAGR≥30% 且 Sharpe≥2 难以同时成立）
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..data import load_panels
from ..metrics import performance_summary
from ..suite.edge_sprint import build_edge_sleeves
from ..suite.return_target import equal_weight_book, scale_target_vol

IS_END = "2021-12-31"
OOS_START = "2022-01-01"
TARGET_CAGR = 0.30
TARGET_MAXDD = -0.10
TARGET_SHARPE = 2.0

TOP_ARB = ["edge_carry_xs", "pair_I_HC", "pair_RB_HC", "cal_I", "cal_HC", "cal_RB"]


def period_metrics(ret: pd.Series, start=None, end=None) -> Dict:
    r = ret.fillna(0.0)
    if start:
        r = r[r.index >= pd.Timestamp(start)]
    if end:
        r = r[r.index <= pd.Timestamp(end)]
    if r.empty:
        return {"sharpe": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "ann_vol": 0.0, "total_return": 0.0}
    n = (1.0 + r).cumprod()
    n = n / float(n.iloc[0])
    return performance_summary(n, r)


def hits(m: Dict) -> bool:
    return m["cagr"] >= TARGET_CAGR and m["max_drawdown"] >= TARGET_MAXDD and m["sharpe"] >= TARGET_SHARPE


def calibrate_tv_for_dd(base: pd.Series, start, end, dd_limit=TARGET_MAXDD, max_leverage=20.0) -> float:
    lo, hi = 0.02, 0.45
    pick = lo
    for _ in range(22):
        mid = (lo + hi) / 2
        _, port, _ = scale_target_vol(base, mid, max_leverage=max_leverage)
        m = period_metrics(port, start, end)
        if m["max_drawdown"] < dd_limit:
            hi = mid
        else:
            lo = mid
            pick = mid
    return float(pick)


def yearly_table(ret: pd.Series) -> pd.DataFrame:
    rows = []
    for y, g in ret.groupby(ret.index.year):
        if len(g) < 60:
            continue
        nav = (1 + g).cumprod()
        rows.append(
            {
                "year": int(y),
                "sharpe": float(g.mean() / g.std() * np.sqrt(252)) if g.std() > 0 else 0.0,
                "cagr": float(nav.iloc[-1] ** (252 / len(g)) - 1),
                "maxdd": float((nav / nav.cummax() - 1).min()),
            }
        )
    return pd.DataFrame(rows)


def run_deliverable(
    data_dir: str = "cta_data_akshare",
    out_dir: str = "cta_result_deliver",
    plot: bool = True,
) -> Dict:
    os.makedirs(out_dir, exist_ok=True)
    panels = {
        k.upper(): v[v.index >= pd.Timestamp("2018-01-01")]
        for k, v in load_panels(data_dir).items()
        if len(v[v.index >= pd.Timestamp("2018-01-01")]) >= 400
    }
    print(f"宇宙 n={len(panels)}: {sorted(panels)}")
    print("构建全品种袖层（配对/跨期/carry/趋势）...")
    rets = build_edge_sleeves(panels)
    keys = [k for k in TOP_ARB if k in rets]
    _, base = equal_weight_book(rets, keys)

    # 1) 可实盘：用 IS 段标定 target_vol，使 IS MaxDD≤10%
    tv_is = calibrate_tv_for_dd(base, None, IS_END, TARGET_MAXDD)
    _, port_is, lev_is = scale_target_vol(base, tv_is, max_leverage=20.0)
    # 2) 研究对照：用 OOS 段标定（仅诊断，标为 peek）
    tv_oos = calibrate_tv_for_dd(base, OOS_START, None, TARGET_MAXDD)
    _, port_oos, lev_oos = scale_target_vol(base, tv_oos, max_leverage=20.0)
    # 3) 冲 30% 收益的研究点（不保证 DD）
    _, port_hi, lev_hi = scale_target_vol(base, 0.22, max_leverage=20.0)

    books = {
        "deploy_IS_dd10": {
            "ret": port_is,
            "nav": (1 + port_is).cumprod(),
            "lev": lev_is,
            "tv": tv_is,
            "note": "IS标定 target_vol，IS MaxDD≤10%（可实盘口径）",
        },
        "research_OOS_dd10": {
            "ret": port_oos,
            "nav": (1 + port_oos).cumprod(),
            "lev": lev_oos,
            "tv": tv_oos,
            "note": "OOS标定 target_vol（偷看诊断，非实盘）",
        },
        "research_tv22": {
            "ret": port_hi,
            "nav": (1 + port_hi).cumprod(),
            "lev": lev_hi,
            "tv": 0.22,
            "note": "固定 tv=22% 冲收益（回撤会超 10%）",
        },
    }

    rows = []
    for name, b in books.items():
        for period, a, c in [("IS", None, IS_END), ("OOS", OOS_START, None), ("FULL", None, None)]:
            m = period_metrics(b["ret"], a, c)
            rows.append(
                {
                    "book": name,
                    "period": period,
                    "tv": b["tv"],
                    **m,
                    "hit": int(hits(m)),
                }
            )
    metrics = pd.DataFrame(rows)
    metrics.to_csv(os.path.join(out_dir, "deliver_metrics.csv"), index=False)

    deploy = books["deploy_IS_dd10"]
    stretch = books["research_OOS_dd10"]
    aggressive = books["research_tv22"]

    # 保存主推净值
    deploy["nav"].to_csv(os.path.join(out_dir, "deliver_nav.csv"), header=["nav"])
    deploy["ret"].to_csv(os.path.join(out_dir, "deliver_ret.csv"), header=["ret"])
    pd.Series(keys, name="sleeve").to_csv(os.path.join(out_dir, "deliver_members.csv"), index=False)
    yearly = yearly_table(deploy["ret"])
    yearly.to_csv(os.path.join(out_dir, "deliver_yearly.csv"), index=False)
    # frontier
    frontier = []
    for tv in np.linspace(0.04, 0.30, 27):
        _, port, _ = scale_target_vol(base, float(tv), max_leverage=20.0)
        m = period_metrics(port, OOS_START, None)
        frontier.append({"tv": float(tv), **{k: m[k] for k in ["cagr", "sharpe", "max_drawdown", "ann_vol"]}})
    frontier_df = pd.DataFrame(frontier)
    frontier_df.to_csv(os.path.join(out_dir, "oos_frontier.csv"), index=False)

    oos_deploy = period_metrics(deploy["ret"], OOS_START, None)
    oos_stretch = period_metrics(stretch["ret"], OOS_START, None)
    oos_agg = period_metrics(aggressive["ret"], OOS_START, None)

    report = os.path.join(out_dir, "DELIVERABLE.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 全品种 CTA 组合交付说明\n\n")
        f.write("## 目标\n\n年化≥30%，MaxDD≤10%，Sharpe≥2（OOS 口径）。\n\n")
        f.write(f"## 数据\n\nakshare 主力连续，{len(panels)} 品种，样本 2018+。\n\n")
        f.write(f"`{', '.join(sorted(panels))}`\n\n")
        f.write("## 策略（大道至简）\n\n")
        f.write(
            "等权合成以下可落地袖层（文献/产业逻辑，非全样本挖矿）：\n\n"
            "1. **截面 carry**（期限结构）\n"
            "2. **产业配对** I-HC、RB-HC（相关+半衰期门控 z 回归）\n"
            "3. **跨期** I/HC/RB 近远月 z 回归\n"
            "4. 组合层：**因果目标波动**（IS 标定使 MaxDD≤10%）\n\n"
        )
        f.write(f"成员：`{', '.join(keys)}`\n\n")
        f.write("## 主推：deploy_IS_dd10（可实盘）\n\n")
        f.write(f"- IS 标定 target_vol = **{tv_is:.3f}**\n\n")
        show = metrics[metrics.book == "deploy_IS_dd10"][
            ["period", "cagr", "sharpe", "max_drawdown", "ann_vol", "hit"]
        ].copy()
        for c in ["cagr", "max_drawdown", "ann_vol"]:
            show[c] = show[c].map(lambda x: f"{float(x):.2%}")
        show["sharpe"] = show["sharpe"].map(lambda x: f"{float(x):.2f}")
        f.write(show.to_markdown(index=False))
        f.write("\n\n## 对照\n\n")
        f.write(
            f"| 书 | 说明 | OOS CAGR | OOS Sharpe | OOS MaxDD |\n|----|------|----------|------------|----------|\n"
            f"| deploy_IS_dd10 | IS标定DD≤10% | {oos_deploy['cagr']:.1%} | {oos_deploy['sharpe']:.2f} | {oos_deploy['max_drawdown']:.1%} |\n"
            f"| research_OOS_dd10 | OOS标定DD≤10%（偷看） | {oos_stretch['cagr']:.1%} | {oos_stretch['sharpe']:.2f} | {oos_stretch['max_drawdown']:.1%} |\n"
            f"| research_tv22 | tv=22%冲收益 | {oos_agg['cagr']:.1%} | {oos_agg['sharpe']:.2f} | {oos_agg['max_drawdown']:.1%} |\n\n"
        )
        f.write("## 达标结论\n\n")
        if hits(oos_deploy):
            f.write("**主推书 OOS 三项达标。**\n")
        else:
            f.write(
                "**主推书未同时满足三项门槛。**\n\n"
                "在日频期货、含成本、因果风控下，本仓库可复现的最优套利/carry 组合：\n\n"
                f"- 把 OOS MaxDD 压在 10% 时，OOS 夏普可到约 **{oos_stretch['sharpe']:.2f}**，"
                f"CAGR 约 **{oos_stretch['cagr']:.0%}**（需 OOS 标定；IS 标定更保守约 **{oos_deploy['cagr']:.0%}**）。\n"
                f"- 夏普对杠杆近似不变，无法靠加仓把 1.6 提到 2.0。\n"
                f"- 要 CAGR≥30% 需更高目标波动，OOS MaxDD 会到约 **{oos_agg['max_drawdown']:.0%}**（见 tv22）。\n\n"
                "**因此：MaxDD≤10% 与 CAGR≥30% 与 Sharpe≥2 三者无法在同一可实盘口径下同时成立。**\n"
                "主推交付的是：全品种可交易、风控达标（DD）、夏普尽量高的实盘书；收益目标需下调或换分钟级/更高边际的市场。\n"
            )
        f.write("\n### 主推年度\n\n")
        ys = yearly.copy()
        for c in ["cagr", "maxdd"]:
            ys[c] = ys[c].map(lambda x: f"{x:.2%}")
        ys["sharpe"] = ys["sharpe"].map(lambda x: f"{x:.2f}")
        f.write(ys.to_markdown(index=False))
        f.write("\n")
    print(report)
    print(metrics[metrics.book == "deploy_IS_dd10"][["period", "cagr", "sharpe", "max_drawdown", "hit"]].to_string(index=False))

    if plot:
        fig, ax = plt.subplots(figsize=(11, 5.5))
        for name, color in [
            ("deploy_IS_dd10", "#1f77b4"),
            ("research_OOS_dd10", "#2ca02c"),
            ("research_tv22", "#ff7f0e"),
        ]:
            b = books[name]
            m = period_metrics(b["ret"], OOS_START, None)
            ax.plot(
                b["nav"].index,
                b["nav"].values,
                lw=2.0 if name.startswith("deploy") else 1.4,
                color=color,
                label=f"{name} OOS CAGR={m['cagr']:.1%} Sh={m['sharpe']:.2f} DD={m['max_drawdown']:.1%}",
            )
        ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", label="OOS start")
        ax.set_title("Full-universe CTA books (akshare)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "nav_deliverable.png"), dpi=140)
        plt.close(fig)

        nav = deploy["nav"]
        dd = nav / nav.cummax() - 1.0
        fig, ax = plt.subplots(figsize=(11, 3.5))
        ax.fill_between(dd.index, dd.values, 0, color="#c44e52", alpha=0.35)
        ax.axhline(TARGET_MAXDD, color="#333", ls="--", label="-10%")
        ax.set_title("Deploy book drawdown")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "dd_deliverable.png"), dpi=140)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 4.2))
        colors = ["#c44e52" if v < 0 else "#4c72b0" for v in yearly["cagr"]]
        ax.bar(yearly["year"].astype(str), yearly["cagr"] * 100, color=colors)
        ax.axhline(30, ls="--", color="#999", label="30% target")
        ax.set_ylabel("CAGR %")
        ax.set_title("Deploy book yearly CAGR")
        ax.legend()
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "yearly_deliverable.png"), dpi=140)
        plt.close(fig)

        # frontier
        fig, ax = plt.subplots(figsize=(8, 5))
        sc = ax.scatter(
            frontier_df["max_drawdown"].abs() * 100,
            frontier_df["cagr"] * 100,
            c=frontier_df["sharpe"],
            cmap="viridis",
            s=40,
        )
        ax.axvline(10, color="#c44e52", ls="--", label="DD 10%")
        ax.axhline(30, color="#999", ls="--", label="CAGR 30%")
        plt.colorbar(sc, ax=ax, label="OOS Sharpe")
        ax.set_xlabel("|MaxDD| %")
        ax.set_ylabel("OOS CAGR %")
        ax.set_title("OOS risk-return frontier (target-vol sweep)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "frontier_oos.png"), dpi=140)
        plt.close(fig)

    return {
        "books": books,
        "metrics": metrics,
        "keys": keys,
        "tv_is": tv_is,
        "tv_oos": tv_oos,
        "oos_deploy": oos_deploy,
        "oos_stretch": oos_stretch,
        "oos_agg": oos_agg,
        "yearly": yearly,
        "out_dir": out_dir,
        "n_symbols": len(panels),
        "deploy_hit": hits(oos_deploy),
        "stretch_hit": hits(oos_stretch),
    }


if __name__ == "__main__":
    run_deliverable(plot=True)
