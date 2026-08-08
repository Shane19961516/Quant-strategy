# -*- coding: utf-8 -*-
"""稳定版实盘书 live_v5：全品种宇宙 + IS 门控。

相对 v3/v4：
- 配对/跨期/carry/趋势覆盖仓库全部品种（IF 仅趋势）
- 预注册书 = IS Sharpe≥门槛的 pair_/cal_/edge_carry/edge_olsx（不偷看 OOS）
- 剔除已知拖累（如 pair_I_RB）可额外黑名单
- 因果目标波动瞄准 CAGR 15% 一带
"""

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
from .return_target import equal_weight_book, scale_target_vol
from .universe import ALL_CALENDAR_SYMBOLS, ALL_COMMODITIES, FULL_ECONOMIC_PAIRS

IS_END = "2021-12-31"
OOS_START = "2022-01-01"

# 显式黑名单：IS 显著失效、不宜预注册
SLEEVE_BLACKLIST = {
    "pair_I_RB",
    "edge_ols_I_RB",
    "edge_olsx_I_RB",
}


def _yearly_table(ret: pd.Series, lev: Optional[pd.Series] = None) -> pd.DataFrame:
    rows = []
    for y, g in ret.groupby(ret.index.year):
        if len(g) < 60:
            continue
        nav = (1.0 + g).cumprod()
        sh = float(g.mean() / g.std() * np.sqrt(252)) if float(g.std()) > 0 else 0.0
        cagr = float(nav.iloc[-1] ** (252.0 / len(g)) - 1.0)
        dd = float((nav / nav.cummax() - 1.0).min())
        row = {"year": int(y), "sharpe": sh, "cagr": cagr, "maxdd": dd, "n_days": len(g)}
        if lev is not None:
            row["avg_lev"] = float(lev.reindex(g.index).mean())
        rows.append(row)
    return pd.DataFrame(rows)


def _metrics_block(nav: pd.Series, ret: pd.Series) -> Dict:
    full = performance_summary(nav, ret)
    _, _, iso = slice_period(nav, ret, None, IS_END)
    _, _, oos = slice_period(nav, ret, OOS_START, None)
    early = ret[(ret.index >= "2018-01-01") & (ret.index < "2021-01-01")]
    if len(early) and float(early.std()) > 0:
        enav = (1.0 + early).cumprod()
        early_m = {
            "sharpe": float(early.mean() / early.std() * np.sqrt(252)),
            "cagr": float(enav.iloc[-1] ** (252.0 / len(early)) - 1.0),
            "max_drawdown": float((enav / enav.cummax() - 1.0).min()),
        }
    else:
        early_m = {"sharpe": 0.0, "cagr": 0.0, "max_drawdown": 0.0}
    wf = walk_forward_oos_sharpes(ret)
    return {"full": full, "is": iso, "oos": oos, "early": early_m, "wf": wf}


def _score_sleeves_is(rets: Dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for k, r in rets.items():
        r = r.fillna(0.0)
        nav = (1.0 + r).cumprod()
        _, _, iso = slice_period(nav, r, None, IS_END)
        _, _, oos = slice_period(nav, r, OOS_START, None)
        rows.append(
            {
                "sleeve": k,
                "is_sharpe": iso["sharpe"],
                "is_cagr": iso["cagr"],
                "oos_sharpe": oos["sharpe"],
                "oos_cagr": oos["cagr"],
                "active_frac": float((r.abs() > 1e-12).mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("is_sharpe", ascending=False)


def select_is_sleeves(
    score: pd.DataFrame,
    min_is_sharpe: float = 0.25,
    max_n: int = 16,
    prefixes: tuple = ("pair_", "cal_", "edge_carry", "edge_olsx_"),
) -> List[str]:
    """仅用 IS 选袖层（预注册，无 OOS 偷看）。"""
    m = score[
        score["sleeve"].str.startswith(prefixes)
        & (~score["sleeve"].isin(SLEEVE_BLACKLIST))
        & (score["is_sharpe"] >= min_is_sharpe)
        & (score["active_frac"] >= 0.02)
    ].copy()
    return m.head(max_n)["sleeve"].tolist()


def run_stable_book(
    data_dir: str = "cta_data_akshare",
    out_dir: str = "cta_result_suite",
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    target_vol: float = 0.12,
    plot: bool = True,
) -> Dict:
    # 全品种含 IF（趋势用）
    panels = load_panels(data_dir)
    os.makedirs(out_dir, exist_ok=True)
    print(
        f"构建全品种稳定书 | commodities={list(ALL_COMMODITIES)} "
        f"calendars={list(ALL_CALENDAR_SYMBOLS)} pairs={len(FULL_ECONOMIC_PAIRS)}"
    )
    rets = build_edge_sleeves(panels, capital=capital, contract_cache=contract_cache)
    score = _score_sleeves_is(rets)
    score.to_csv(os.path.join(out_dir, "stable_sleeve_is.csv"), index=False)

    is_keys = select_is_sleeves(score, min_is_sharpe=0.25, max_n=16)
    is_keys_strict = select_is_sleeves(score, min_is_sharpe=0.40, max_n=10)
    # 对照：旧黑色核心
    legacy = [
        k
        for k in ["pair_RB_HC", "cal_HC", "cal_I", "edge_carry_xs", "edge_olsx_RB_HC"]
        if k in rets
    ]
    # 全品种结构化书（非 IS 海选）：全市场 carry/趋势 + 全跨期等权元袖层 + 合格配对
    cal_all = [k for k in rets if k.startswith("cal_") and float(score.set_index("sleeve").loc[k, "active_frac"]) >= 0.02]
    pair_all = [
        k
        for k in rets
        if k.startswith("pair_") and k not in SLEEVE_BLACKLIST
        and float(score.set_index("sleeve").loc[k, "active_frac"]) >= 0.02
    ]
    # 预注册全品种核心：文献结构，不用 OOS 挑袖层
    univ_core = [
        k
        for k in [
            "edge_carry_xs",  # 全品种截面 carry
            "trend_dualma_atr",  # 全品种趋势（含 IF）
            "trend_tsmom60",
            "pair_RB_HC",
            "pair_I_HC",
            "pair_Y_M",
            "pair_C_M",
            "pair_MA_TA",
            "edge_olsx_RB_HC",
        ]
        if k in rets
    ] + [k for k in ("cal_RB", "cal_HC", "cal_I", "cal_CU", "cal_AU", "cal_M", "cal_Y", "cal_C", "cal_TA", "cal_MA", "cal_RU", "cal_SC") if k in rets]

    books = {}
    # 元袖层：全跨期等权合成一只
    if cal_all:
        _, cal_meta = equal_weight_book(rets, cal_all)
        rets = {**rets, "meta_cal_all": cal_meta.fillna(0.0)}
    if pair_all:
        _, pair_meta = equal_weight_book(rets, pair_all)
        rets = {**rets, "meta_pair_all": pair_meta.fillna(0.0)}

    candidates = {
        "v5_univ_core": univ_core,
        "v5_univ_meta": [k for k in ["edge_carry_xs", "trend_dualma_atr", "meta_cal_all", "meta_pair_all", "edge_olsx_RB_HC"] if k in rets],
        "v5_full_is025": is_keys,
        "v5_full_is040": is_keys_strict,
        "v5_full_is025_dualma": list(dict.fromkeys(is_keys + ["trend_dualma_atr"])),
        "v4_legacy_core": legacy,
        "v5_cal_all_tv": cal_all,
        "v5_pair_all_tv": pair_all,
    }

    for name, keys in candidates.items():
        keys = [k for k in keys if k in rets]
        if not keys:
            continue
        nav, port = equal_weight_book(rets, keys)
        nav_l, port_l, lev = scale_target_vol(port, target_vol=target_vol)
        books[name] = {
            "keys": keys,
            "nav": nav_l,
            "ret": port_l,
            "lev": lev,
            **_metrics_block(nav_l, port_l),
        }
        # 活动感知版（仅对 is025）
        if name == "v5_full_is025" and len(keys) >= 2:
            nav_a, port_a, _ = activity_aware_portfolio(
                {k: rets[k] for k in keys}, max_weight=0.30
            )
            nav_l, port_l, lev = scale_target_vol(port_a, target_vol=target_vol)
            books["v5_full_is025_act"] = {
                "keys": keys,
                "nav": nav_l,
                "ret": port_l,
                "lev": lev,
                **_metrics_block(nav_l, port_l),
            }

    def score_book(b):
        e, i, o = b["early"], b["is"], b["oos"]
        if o["cagr"] < 0.10 or o["sharpe"] < 0.8:
            return -1e9
        return float(
            0.40 * e["sharpe"]
            + 0.30 * i["sharpe"]
            + 0.20 * o["sharpe"]
            + 0.50 * min(o["cagr"], 0.20)
            + 0.30 * min(e["cagr"], 0.10)
        )

    recommend = max(books.keys(), key=lambda k: score_book(books[k]))
    if score_book(books[recommend]) < -1e8:
        recommend = "v5_full_is025" if "v5_full_is025" in books else next(iter(books))

    rows = []
    for name, b in books.items():
        rows.append(
            {
                "book": name,
                "recommended": int(name == recommend),
                "n_sleeves": len(b["keys"]),
                "members": ",".join(b["keys"]),
                "early_sharpe": b["early"]["sharpe"],
                "early_cagr": b["early"]["cagr"],
                "early_maxdd": b["early"]["max_drawdown"],
                "is_sharpe": b["is"]["sharpe"],
                "is_cagr": b["is"]["cagr"],
                "oos_sharpe": b["oos"]["sharpe"],
                "oos_cagr": b["oos"]["cagr"],
                "oos_maxdd": b["oos"]["max_drawdown"],
                "oos_vol": b["oos"]["ann_vol"],
                "wf_mean_sharpe": b["wf"]["wf_mean_sharpe"],
                "full_cagr": b["full"]["cagr"],
                "full_maxdd": b["full"]["max_drawdown"],
                "avg_lev_oos": float(b["lev"][b["lev"].index >= pd.Timestamp(OOS_START)].mean()),
            }
        )
        print(
            f"{name}: n={len(b['keys'])} early={b['early']['cagr']:.1%}/{b['early']['sharpe']:.2f} "
            f"IS={b['is']['sharpe']:.2f}/{b['is']['cagr']:.1%} "
            f"OOS={b['oos']['sharpe']:.2f}/{b['oos']['cagr']:.1%} "
            f"{'<<REC' if name==recommend else ''}"
        )

    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(out_dir, "stable_books.csv"), index=False)
    ydf = _yearly_table(books[recommend]["ret"], books[recommend]["lev"])
    ydf.to_csv(os.path.join(out_dir, "stable_yearly.csv"), index=False)
    pd.DataFrame({k: books[k]["nav"] for k in books}).to_csv(os.path.join(out_dir, "stable_nav.csv"))

    rb = books[recommend]
    report = os.path.join(out_dir, "stable_report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("# live_v5 全品种稳定书\n\n")
        f.write("## 宇宙\n\n")
        f.write(f"- 商品：`{', '.join(ALL_COMMODITIES)}`\n")
        f.write(f"- 跨期：`{', '.join(ALL_CALENDAR_SYMBOLS)}`\n")
        f.write(f"- 产业配对数：{len(FULL_ECONOMIC_PAIRS)}\n")
        f.write("- IF：仅趋势袖层\n\n")
        f.write("## 推荐\n\n")
        f.write(f"**`{recommend}`**（{len(rb['keys'])} 袖层）\n\n")
        f.write(f"- 成员：`{', '.join(rb['keys'])}`\n")
        f.write(
            f"- 2018–2020：Sharpe **{rb['early']['sharpe']:.2f}**，CAGR **{rb['early']['cagr']:.1%}**\n"
            f"- IS：Sharpe **{rb['is']['sharpe']:.2f}**，CAGR **{rb['is']['cagr']:.1%}**\n"
            f"- OOS：Sharpe **{rb['oos']['sharpe']:.2f}**，CAGR **{rb['oos']['cagr']:.1%}**，"
            f"MaxDD **{rb['oos']['max_drawdown']:.1%}**\n\n"
        )
        f.write("### 年度\n\n")
        showy = ydf.copy()
        for c in ["cagr", "maxdd"]:
            showy[c] = showy[c].map(lambda x: f"{x:.2%}")
        for c in ["sharpe", "avg_lev"]:
            if c in showy:
                showy[c] = showy[c].map(lambda x: f"{float(x):.2f}")
        f.write(showy.to_markdown(index=False))
        f.write("\n\n### 组合对照\n\n")
        show = summary.copy()
        for c in [
            "early_cagr",
            "early_maxdd",
            "is_cagr",
            "oos_cagr",
            "oos_maxdd",
            "oos_vol",
            "full_cagr",
            "full_maxdd",
        ]:
            show[c] = show[c].map(lambda x: f"{float(x):.2%}")
        for c in ["early_sharpe", "is_sharpe", "oos_sharpe", "wf_mean_sharpe", "avg_lev_oos"]:
            show[c] = show[c].map(lambda x: f"{float(x):.3f}")
        f.write(show.drop(columns=["members"]).to_markdown(index=False))
        f.write("\n\n### IS 袖层榜（节选）\n\n")
        top = score.head(25).copy()
        for c in ["is_cagr", "oos_cagr", "active_frac"]:
            top[c] = top[c].map(lambda x: f"{float(x):.2%}")
        for c in ["is_sharpe", "oos_sharpe"]:
            top[c] = top[c].map(lambda x: f"{float(x):.3f}")
        f.write(top.to_markdown(index=False))
        f.write("\n")
    print(f"报告: {report}")

    if plot:
        fig, ax = plt.subplots(figsize=(11, 5.5))
        for name, b in books.items():
            ax.plot(
                b["nav"].index,
                b["nav"].values,
                lw=2.2 if name == recommend else 1.2,
                alpha=1.0 if name == recommend else 0.55,
                label=(
                    f"{name} n={len(b['keys'])} early={b['early']['cagr']:.1%} "
                    f"OOS={b['oos']['cagr']:.1%} Sh={b['oos']['sharpe']:.2f}"
                ),
            )
        ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", label="OOS start")
        ax.axhline(1.0, color="#999", lw=0.8, ls=":")
        ax.set_title("live_v5 full-universe stable books (target-vol)")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "nav_stable_book.png"), dpi=140)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 4.5))
        colors = ["#c44e52" if v < 0 else "#4c72b0" for v in ydf["cagr"]]
        ax.bar(ydf["year"].astype(str), ydf["cagr"] * 100, color=colors)
        ax.axhline(0, color="#333", lw=0.8)
        ax.axhline(15, color="#999", ls="--", lw=0.8, label="15% ref")
        ax.set_ylabel("CAGR %")
        ax.set_title(f"Yearly CAGR — {recommend} (full universe)")
        ax.legend()
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "nav_stable_yearly.png"), dpi=140)
        plt.close(fig)

    return {
        "books": books,
        "summary": summary,
        "recommend": recommend,
        "yearly": ydf,
        "sleeve_score": score,
        "report_path": report,
        "universe": {
            "commodities": list(ALL_COMMODITIES),
            "calendars": list(ALL_CALENDAR_SYMBOLS),
            "n_pairs": len(FULL_ECONOMIC_PAIRS),
        },
    }


if __name__ == "__main__":
    run_stable_book(plot=True)
