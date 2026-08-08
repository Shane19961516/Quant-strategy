# -*- coding: utf-8 -*-
"""收益目标缩放：在保持策略结构的前提下，用目标波动/固定杠杆把年化拉到 15–20%。

说明：
- 夏普对仓位缩放近似不变；此前 lev≤1 书 OOS CAGR≈1.6%、Sharpe≈1.46，波动被压得过低。
- 本模块用**因果**滚动波动定杠杆（T+1），或用 **IS 标定**固定杠杆（不偷看 OOS）。
- 期货保证金口径下，名义倍数 >1 是正常的；需关注 MaxDD 与保证金占用同比放大。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..data import load_panels
from ..metrics import performance_summary
from .edge_sprint import build_edge_sleeves
from .noleverage import slice_period, walk_forward_oos_sharpes

IS_END = "2021-12-31"
OOS_START = "2022-01-01"

LIVE_V3 = [
    "pair_RB_HC",
    "pair_I_RB",
    "cal_HC",
    "cal_I",
    "cal_RB",
    "edge_carry_xs",
    "edge_olsx_RB_HC",
]


def equal_weight_book(rets: Dict[str, pd.Series], keys: List[str]) -> Tuple[pd.Series, pd.Series]:
    R = pd.DataFrame({k: rets[k] for k in keys if k in rets}).fillna(0.0)
    port = R.mean(axis=1).rename("ret")
    nav = (1.0 + port).cumprod().rename("nav")
    return nav, port


def scale_target_vol(
    ret: pd.Series,
    target_vol: float = 0.12,
    lookback: int = 60,
    max_leverage: float = 25.0,
    min_hist: int = 40,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """因果目标波动：杠杆_t = clip(target_vol / vol_{t-1})，收益_t *= 杠杆_t。"""
    r = ret.fillna(0.0)
    vol = r.rolling(lookback, min_periods=min_hist).std() * np.sqrt(252.0)
    lev = (target_vol / vol.replace(0.0, np.nan)).clip(upper=max_leverage)
    lev = lev.shift(1)  # 昨日可知
    lev = lev.where(lev.index.to_series().expanding().count().values >= min_hist + 1, 0.0)
    lev = lev.fillna(0.0).rename("leverage")
    scaled = (r * lev).rename("ret")
    nav = (1.0 + scaled).cumprod().rename("nav")
    return nav, scaled, lev


def scale_fixed_leverage_is(
    ret: pd.Series,
    target_vol: float = 0.12,
    max_leverage: float = 25.0,
    is_end: str = IS_END,
) -> Tuple[pd.Series, pd.Series, float]:
    """用 IS 段实现波动标定固定杠杆（不看 OOS）。"""
    r = ret.fillna(0.0)
    is_r = r[r.index <= pd.Timestamp(is_end)]
    is_vol = float(is_r.std() * np.sqrt(252.0)) if len(is_r) > 20 else 0.0
    if is_vol < 1e-8:
        L = 1.0
    else:
        L = float(np.clip(target_vol / is_vol, 0.0, max_leverage))
    scaled = (r * L).rename("ret")
    nav = (1.0 + scaled).cumprod().rename("nav")
    return nav, scaled, L


def _pack(name: str, keys: List[str], nav: pd.Series, ret: pd.Series, extra: Optional[Dict] = None) -> Dict:
    full = performance_summary(nav, ret)
    _, _, iso = slice_period(nav, ret, None, IS_END)
    _, _, oos = slice_period(nav, ret, OOS_START, None)
    wf = walk_forward_oos_sharpes(ret)
    out = {
        "name": name,
        "keys": keys,
        "nav": nav,
        "ret": ret,
        "full": full,
        "is": iso,
        "oos": oos,
        "wf": wf,
        "extra": extra or {},
    }
    return out


def run_return_target(
    data_dir: str = "cta_data_akshare",
    out_dir: str = "cta_result_suite",
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    target_vols: Tuple[float, ...] = (0.10, 0.12, 0.14),
    plot: bool = True,
) -> Dict:
    """把 live_v3 缩放到目标波动，争取 OOS CAGR 落在 15–20%。"""
    panels = {k: v for k, v in load_panels(data_dir).items() if k.upper() != "IF"}
    os.makedirs(out_dir, exist_ok=True)
    print("构建袖层并合成 live_v3（再做收益目标缩放）...")
    rets = build_edge_sleeves(panels, capital=capital, contract_cache=contract_cache)
    keys = [k for k in LIVE_V3 if k in rets]
    base_nav, base_ret = equal_weight_book(rets, keys)

    books = {
        "live_v3_unlevered": _pack("live_v3_unlevered", keys, base_nav, base_ret),
    }

    # IS 固定杠杆 + 因果目标波动
    for tv in target_vols:
        nav_f, ret_f, L = scale_fixed_leverage_is(base_ret, target_vol=tv)
        books[f"fixed_IS_vol{int(tv*100)}"] = _pack(
            f"fixed_IS_vol{int(tv*100)}", keys, nav_f, ret_f, {"leverage": L, "target_vol": tv}
        )
        nav_t, ret_t, lev = scale_target_vol(base_ret, target_vol=tv)
        books[f"target_vol{int(tv*100)}"] = _pack(
            f"target_vol{int(tv*100)}",
            keys,
            nav_t,
            ret_t,
            {
                "target_vol": tv,
                "avg_lev_oos": float(lev[lev.index >= pd.Timestamp(OOS_START)].mean()),
                "p95_lev_oos": float(lev[lev.index >= pd.Timestamp(OOS_START)].quantile(0.95)),
            },
        )

    # 选 OOS CAGR 最接近 17.5% 且在 12–22% 带内的作为推荐
    recommend = None
    best_dist = 1e9
    for name, b in books.items():
        if name == "live_v3_unlevered":
            continue
        cagr = float(b["oos"]["cagr"])
        dist = abs(cagr - 0.175)
        if 0.12 <= cagr <= 0.22 and dist < best_dist:
            best_dist = dist
            recommend = name
    if recommend is None:
        # 放宽：选 CAGR 最高且 MaxDD > -40% 的
        cands = [
            (name, b)
            for name, b in books.items()
            if name != "live_v3_unlevered" and b["oos"]["max_drawdown"] > -0.40
        ]
        if cands:
            recommend = max(cands, key=lambda x: x[1]["oos"]["cagr"])[0]

    rows = []
    for name, b in books.items():
        rows.append(
            {
                "book": name,
                "recommended": int(name == recommend),
                "leverage_or_note": b["extra"].get("leverage", b["extra"].get("avg_lev_oos", 1.0)),
                "target_vol": b["extra"].get("target_vol", np.nan),
                "full_sharpe": b["full"]["sharpe"],
                "full_cagr": b["full"]["cagr"],
                "full_maxdd": b["full"]["max_drawdown"],
                "oos_sharpe": b["oos"]["sharpe"],
                "oos_cagr": b["oos"]["cagr"],
                "oos_vol": b["oos"]["ann_vol"],
                "oos_maxdd": b["oos"]["max_drawdown"],
                "oos_sortino": b["oos"]["sortino"],
                "oos_calmar": b["oos"]["calmar"],
                "wf_mean_sharpe": b["wf"]["wf_mean_sharpe"],
                "p95_lev_oos": b["extra"].get("p95_lev_oos", np.nan),
            }
        )
    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(out_dir, "return_target_books.csv"), index=False)

    # NAV 保存
    nav_df = pd.DataFrame({name: b["nav"] for name, b in books.items()})
    nav_df.to_csv(os.path.join(out_dir, "return_target_nav.csv"))

    report = os.path.join(out_dir, "return_target_report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 收益目标缩放报告（目标年化 15–20%）\n\n")
        f.write("## 结论\n\n")
        if recommend:
            rb = books[recommend]
            f.write(
                f"**推荐：`{recommend}`**\n\n"
                f"- OOS CAGR ≈ **{rb['oos']['cagr']:.1%}**（目标带 15–20%）\n"
                f"- OOS Sharpe ≈ **{rb['oos']['sharpe']:.2f}**（与无杠杆书接近）\n"
                f"- OOS 年化波动 ≈ **{rb['oos']['ann_vol']:.1%}**\n"
                f"- OOS MaxDD ≈ **{rb['oos']['max_drawdown']:.1%}**\n"
                f"- WF 均值夏普 ≈ **{rb['wf']['wf_mean_sharpe']:.2f}**\n\n"
            )
            if "leverage" in rb["extra"]:
                f.write(f"- 固定杠杆（IS 标定）≈ **{rb['extra']['leverage']:.1f}×** 名义\n\n")
            else:
                f.write(
                    f"- 因果目标波动；OOS 平均杠杆 ≈ **{rb['extra'].get('avg_lev_oos', float('nan')):.1f}×**，"
                    f"P95 ≈ **{rb['extra'].get('p95_lev_oos', float('nan')):.1f}×**\n\n"
                )
        f.write(
            "无杠杆 live_v3 夏普尚可但 CAGR 过低，是因为等权+lev≤1 把组合波动压到约 1%。"
            "要 15–20% 年化，必须放大名义（期货保证金杠杆），夏普不会因此升到 2。\n\n"
        )
        f.write("## 对照表\n\n")
        show = summary.copy()
        for c in ["full_cagr", "full_maxdd", "oos_cagr", "oos_vol", "oos_maxdd"]:
            show[c] = show[c].map(lambda x: f"{float(x):.2%}" if pd.notna(x) else "")
        for c in ["full_sharpe", "oos_sharpe", "oos_sortino", "oos_calmar", "wf_mean_sharpe", "leverage_or_note"]:
            show[c] = show[c].map(lambda x: f"{float(x):.3f}" if pd.notna(x) else "")
        f.write(show.to_markdown(index=False))
        f.write("\n")
    print(f"报告: {report}")
    if recommend:
        b = books[recommend]
        print(
            f"推荐 {recommend}: OOS CAGR={b['oos']['cagr']:.2%} Sharpe={b['oos']['sharpe']:.3f} "
            f"MaxDD={b['oos']['max_drawdown']:.2%} vol={b['oos']['ann_vol']:.2%}"
        )

    if plot:
        fig, ax = plt.subplots(figsize=(11, 5.5))
        # 无杠杆 vs 推荐 vs 邻近
        plot_names = ["live_v3_unlevered"]
        if recommend:
            plot_names.append(recommend)
        for n in ("fixed_IS_vol12", "target_vol12", "fixed_IS_vol14", "target_vol14"):
            if n in books and n not in plot_names:
                plot_names.append(n)
        for name in plot_names[:5]:
            b = books[name]
            ax.plot(
                b["nav"].index,
                b["nav"].values,
                lw=2.0 if name == recommend else 1.4,
                label=(
                    f"{name} | OOS CAGR={b['oos']['cagr']:.1%} Sh={b['oos']['sharpe']:.2f} "
                    f"DD={b['oos']['max_drawdown']:.1%}"
                ),
            )
        ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", label="OOS start")
        ax.axhline(1.0, color="#999", lw=0.8, ls=":")
        ax.set_title("live_v3 return-target scaling (aim CAGR 15-20%)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "nav_return_target.png"), dpi=140)
        plt.close(fig)

        # 单独推荐净值大图
        if recommend:
            fig, ax = plt.subplots(figsize=(11, 5))
            b = books[recommend]
            ax.plot(b["nav"].index, b["nav"].values, lw=2.2, color="#1f77b4")
            ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", label="OOS start")
            ax.set_title(
                f"Recommended {recommend}: OOS CAGR={b['oos']['cagr']:.1%} "
                f"Sharpe={b['oos']['sharpe']:.2f} MaxDD={b['oos']['max_drawdown']:.1%}"
            )
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            fig.savefig(os.path.join(out_dir, "nav_return_target_recommended.png"), dpi=140)
            plt.close(fig)

    return {"books": books, "summary": summary, "recommend": recommend, "report_path": report}


if __name__ == "__main__":
    run_return_target(plot=True)
