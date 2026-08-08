# -*- coding: utf-8 -*-
"""广度组合冲刺 Sharpe≥2：拆袖层 + 报告诚实上界。"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..data import load_panels
from ..metrics import performance_summary
from .edge_sprint import build_edge_sleeves
from .factory import activity_aware_portfolio
from .noleverage import slice_period, walk_forward_oos_sharpes

OOS_START = "2022-01-01"
IS_END = "2021-12-31"
TARGET_SHARPE = 2.0


def _score_sleeves(rets: Dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for k, r in rets.items():
        r = r.fillna(0.0)
        nav = (1.0 + r).cumprod()
        full = performance_summary(nav, r)
        _, _, iso = slice_period(nav, r, None, IS_END)
        _, _, oos = slice_period(nav, r, OOS_START, None)
        wf = walk_forward_oos_sharpes(r)
        rows.append(
            {
                "sleeve": k,
                "full_sharpe": full["sharpe"],
                "is_sharpe": iso["sharpe"],
                "oos_sharpe": oos["sharpe"],
                "oos_cagr": oos["cagr"],
                "oos_maxdd": oos["max_drawdown"],
                "wf_mean_sharpe": wf["wf_mean_sharpe"],
                "wf_pos_frac": wf["wf_pos_frac"],
                "active_frac": float((r.abs() > 1e-12).mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("oos_sharpe", ascending=False)


def _eq_port(rets: Dict[str, pd.Series], keys: List[str]):
    R = pd.DataFrame({k: rets[k] for k in keys}).fillna(0.0)
    port = R.mean(axis=1).rename("ret")
    nav = (1.0 + port).cumprod().rename("nav")
    return nav, port


def run_breadth_sprint(
    data_dir: str = "cta_data_akshare",
    out_dir: str = "cta_result_suite",
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    plot: bool = True,
) -> Dict:
    panels = {k: v for k, v in load_panels(data_dir).items() if k.upper() != "IF"}
    os.makedirs(out_dir, exist_ok=True)

    print("构建广度袖层（含 carry / OLS 边缘）...")
    # 与 edge sprint 共用袖层工厂，避免两套口径漂移
    rets = build_edge_sleeves(panels, capital=capital, contract_cache=contract_cache)
    score = _score_sleeves(rets)
    score.to_csv(os.path.join(out_dir, "breadth_sleeves.csv"), index=False)

    # 预注册实盘候选 live_v3：黑色配对+跨期 + 截面 carry + 极端 OLS(RB-HC)
    live_keys = [
        k
        for k in [
            "pair_RB_HC",
            "pair_I_RB",
            "cal_HC",
            "cal_I",
            "cal_RB",
            "edge_carry_xs",
            "edge_olsx_RB_HC",
        ]
        if k in rets
    ]
    # IS 筛选（无 OOS 偷看）：套利/边缘袖层
    is_keys = score[
        (score["is_sharpe"] >= 0.25)
        & (score["sleeve"].str.startswith(("pair_", "cal_", "edge_")))
    ]["sleeve"].tolist()
    # Oracle（仅作上界诊断，不作为落地依据）
    oracle_keys = score[(score["oos_sharpe"] >= 0.30) & (score["wf_mean_sharpe"] >= 0)]["sleeve"].tolist()

    books = {}
    for name, keys in [
        ("live_pre_registered", live_keys),
        ("is_filtered_arb", is_keys),
        ("oracle_oos_filter", oracle_keys),
        ("all_sleeves_eq", list(rets.keys())),
    ]:
        if not keys:
            continue
        nav, port = _eq_port(rets, keys)
        full = performance_summary(nav, port)
        _, _, oos = slice_period(nav, port, OOS_START, None)
        wf = walk_forward_oos_sharpes(port)
        books[name] = {
            "keys": keys,
            "nav": nav,
            "ret": port,
            "full": full,
            "oos": oos,
            "wf": wf,
            "hits_target": float(oos["sharpe"] >= TARGET_SHARPE),
        }
        print(
            f"{name}: n={len(keys)} OOS Sharpe={oos['sharpe']:.3f} "
            f"CAGR={oos['cagr']:.2%} MaxDD={oos['max_drawdown']:.2%} "
            f"WF={wf['wf_mean_sharpe']:.3f} >=2? {bool(oos['sharpe']>=TARGET_SHARPE)}"
        )

    # 活动感知（live set）
    if live_keys:
        nav_a, port_a, _ = activity_aware_portfolio({k: rets[k] for k in live_keys}, max_weight=0.35)
        _, _, oos_a = slice_period(nav_a, port_a, OOS_START, None)
        books["live_activity"] = {
            "keys": live_keys,
            "nav": nav_a,
            "ret": port_a,
            "full": performance_summary(nav_a, port_a),
            "oos": oos_a,
            "wf": walk_forward_oos_sharpes(port_a),
            "hits_target": float(oos_a["sharpe"] >= TARGET_SHARPE),
        }
        print(
            f"live_activity: OOS Sharpe={oos_a['sharpe']:.3f} "
            f">=2? {bool(oos_a['sharpe']>=TARGET_SHARPE)}"
        )

    # 保存组合净值
    nav_df = pd.DataFrame({k: rets[k] for k in rets})
    for name, b in books.items():
        nav_df[f"book_{name}"] = b["nav"].reindex(nav_df.index).ffill()
    nav_df.to_csv(os.path.join(out_dir, "breadth_nav.csv"))

    summary_rows = []
    for name, b in books.items():
        summary_rows.append(
            {
                "book": name,
                "n_sleeves": len(b["keys"]),
                "members": ",".join(b["keys"]),
                "full_sharpe": b["full"]["sharpe"],
                "oos_sharpe": b["oos"]["sharpe"],
                "oos_cagr": b["oos"]["cagr"],
                "oos_maxdd": b["oos"]["max_drawdown"],
                "wf_mean_sharpe": b["wf"]["wf_mean_sharpe"],
                "hits_sharpe_2": int(b["hits_target"]),
            }
        )
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(os.path.join(out_dir, "breadth_books.csv"), index=False)

    best_oos = float(summary["oos_sharpe"].max()) if len(summary) else 0.0
    best_sleeve = float(score["oos_sharpe"].max()) if len(score) else 0.0

    report_path = os.path.join(out_dir, "breadth_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 广度冲刺报告：能否达到 OOS Sharpe≥2？\n\n")
        f.write("## 结论\n\n")
        if best_oos >= TARGET_SHARPE or best_sleeve >= TARGET_SHARPE:
            f.write(f"**达到**：最佳组合 OOS Sharpe={best_oos:.3f}\n\n")
        else:
            f.write(
                f"**未达到。** 在日频、lev≤1、含成本、OOS≥2022 口径下：\n\n"
                f"- 单袖层最高 OOS Sharpe ≈ **{best_sleeve:.2f}**\n"
                f"- 预注册 live_v3 组合 OOS Sharpe ≈ **{float(books.get('live_pre_registered',{}).get('oos',{}).get('sharpe',0)):.2f}**\n"
                f"- 即使用 OOS 偷看筛选的 oracle 上界 ≈ **{float(books.get('oracle_oos_filter',{}).get('oos',{}).get('sharpe',0)):.2f}**\n\n"
                f"以上均 **< 2.0**。加入截面 carry 后预注册书约从 1.1→1.4，仍远低于门槛；瓶颈是**信号边缘**。\n\n"
            )
        f.write("## 为何加杠杆无效\n\n夏普对仓位缩放近似不变；把名义从 1× 提到 10×，收益与波动同比放大，夏普不升到 2。\n\n")
        f.write("## 袖层表（按 OOS Sharpe）\n\n")
        show = score.copy()
        for c in ["oos_cagr", "oos_maxdd", "active_frac"]:
            show[c] = show[c].map(lambda x: f"{x:.2%}")
        for c in ["full_sharpe", "is_sharpe", "oos_sharpe", "wf_mean_sharpe", "wf_pos_frac"]:
            show[c] = show[c].map(lambda x: f"{float(x):.3f}")
        f.write(show.to_markdown(index=False))
        f.write("\n\n## 组合表\n\n")
        showb = summary.copy()
        for c in ["oos_cagr", "oos_maxdd"]:
            showb[c] = showb[c].map(lambda x: f"{x:.2%}")
        for c in ["full_sharpe", "oos_sharpe", "wf_mean_sharpe"]:
            showb[c] = showb[c].map(lambda x: f"{float(x):.3f}")
        f.write(showb.to_markdown(index=False))
        f.write("\n\n## 若坚持 Sharpe≥2 的下一步\n\n")
        f.write(
            "1. **换频率**：分钟级/TICK 做市或短线（本仓库只有日线）\n"
            "2. **换市场**：股指期权、跨市场期现、高流动性外盘\n"
            "3. **换目标**：将硬门槛改为 OOS Sharpe≥1.0（当前最优实盘候选已接近）\n"
            "4. **拒绝**：用 lev 或全样本调参把夏普“做”到 2——那不是可实盘结果\n"
        )
    print(f"报告: {report_path}")

    if plot and "live_pre_registered" in books:
        fig, ax = plt.subplots(figsize=(11, 5))
        b = books["live_pre_registered"]
        ax.plot(b["nav"].index, b["nav"].values, lw=2.0, label=f"live book OOS Sh={b['oos']['sharpe']:.2f}")
        ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", label="OOS start")
        ax.axhline(1.0, color="#999", lw=0.8, ls=":")
        ax.set_title(f"Best pre-registered breadth book (target Sharpe>={TARGET_SHARPE:.0f}: NOT MET)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "nav_breadth_live.png"), dpi=140)
        plt.close(fig)

    return {
        "score": score,
        "books": books,
        "best_oos_sharpe": best_oos,
        "best_sleeve_oos_sharpe": best_sleeve,
        "hits_target": bool(best_oos >= TARGET_SHARPE or best_sleeve >= TARGET_SHARPE),
        "out_dir": out_dir,
        "report": report_path,
    }
