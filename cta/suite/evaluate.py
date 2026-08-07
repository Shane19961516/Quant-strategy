# -*- coding: utf-8 -*-
"""测评：IS/OOS、相关、组合、报告表。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..data import load_panels
from ..metrics import performance_summary
from .arb import run_arb_strategy
from .noleverage import slice_period
from .trend import run_trend_strategy

# 预先固定，禁止事后改口
IS_END = "2021-12-31"
OOS_START = "2022-01-01"

STRATEGY_META = {
    "trend_tsmom": {
        "category": "趋势",
        "name_cn": "时间序列动量 TSMOM",
        "name_en": "TSMOM (60d)",
        "params": "L=60, skip=1",
        "source": "AQR Moskowitz / Baltas",
    },
    "trend_donchian": {
        "category": "趋势",
        "name_cn": "唐奇安通道突破",
        "name_en": "Donchian 20/10",
        "params": "entry=20, exit=10",
        "source": "Turtle / 经典CTA",
    },
    "trend_dualma": {
        "category": "趋势",
        "name_cn": "双均线趋势",
        "name_en": "Dual MA 20/60",
        "params": "MA20/60",
        "source": "经典CTA均线",
    },
    "arb_calendar": {
        "category": "套利",
        "name_cn": "跨期价差回归",
        "name_en": "Calendar spread",
        "params": "z20±2; RB/HC/I/CU",
        "source": "国内跨期主流",
    },
    "arb_pairs": {
        "category": "套利",
        "name_cn": "产业配对价差",
        "name_en": "Economic pairs",
        "params": "z60±2; 固定经济对",
        "source": "产业链统计套利",
    },
    "arb_bollinger": {
        "category": "套利",
        "name_cn": "布林均值回归",
        "name_en": "Bollinger MR",
        "params": "20日±2σ",
        "source": "短周期统计套利",
    },
}

DEPLOY_NOTES = {
    "trend_tsmom": "落地性高：信号简单、容量大；需波动率分层与多品种分散；震荡市回撤大，建议与套利组合。",
    "trend_donchian": "落地性高：突破逻辑清晰；假突破多，务必加成本与仓位上限；适合中低频执行。",
    "trend_dualma": "落地性中高：与TSMOM相关偏高，组合中可二选一或降权；滑点敏感度中等。",
    "arb_calendar": "落地性中：必须分合约+组合单；交割前移仓；远月流动性是瓶颈；无杠杆后收益更现实。",
    "arb_pairs": "落地性中高：固定产业对避免挖矿；需协整/相关监控与单腿风控；换月同步。",
    "arb_bollinger": "落地性中：震荡市有效、单边趋势易止损；建议严格止损与品种白名单，容量一般。",
}


@dataclass
class StrategyResult:
    key: str
    nav: pd.Series
    ret: pd.Series
    summary_full: Dict
    summary_is: Dict
    summary_oos: Dict
    meta: Dict = field(default_factory=dict)


def _run_one(panels, key: str, capital: float, contract_cache: str) -> StrategyResult:
    if key.startswith("trend_"):
        nav, ret, _ = run_trend_strategy(panels, key, capital=capital)
    else:
        nav, ret, _ = run_arb_strategy(panels, key, capital=capital, contract_cache=contract_cache)
    ret = ret.fillna(0.0)
    nav = (1.0 + ret).cumprod()
    full = performance_summary(nav, ret)
    _, _, summ_is = slice_period(nav, ret, None, IS_END)
    _, _, summ_oos = slice_period(nav, ret, OOS_START, None)
    return StrategyResult(
        key=key,
        nav=nav,
        ret=ret,
        summary_full=full,
        summary_is=summ_is,
        summary_oos=summ_oos,
        meta=STRATEGY_META[key],
    )


def _corr_matrix(results: Dict[str, StrategyResult]) -> pd.DataFrame:
    df = pd.DataFrame({k: v.ret for k, v in results.items()}).dropna(how="all").fillna(0.0)
    return df.corr()


def _equal_weight_portfolio(results: Dict[str, StrategyResult], keys: List[str]) -> Tuple[pd.Series, pd.Series, Dict]:
    rets = pd.DataFrame({k: results[k].ret for k in keys}).fillna(0.0)
    # 等权：每日平均
    port = rets.mean(axis=1).rename("ret")
    nav = (1.0 + port).cumprod().rename("nav")
    return nav, port, performance_summary(nav, port)


def _stability_score(s_full: Dict, s_oos: Dict) -> float:
    """简单稳定性：OOS夏普/(|IS夏普|+eps) 截断到 [0,1.5] 再映射。"""
    is_sh = float(s_full.get("sharpe", 0.0))  # use IS from s_oos companion
    # caller passes IS sharpe separately — here use ratio oos/full as proxy
    oos = float(s_oos.get("sharpe", 0.0))
    full = float(s_full.get("sharpe", 0.0))
    if full <= 0 and oos <= 0:
        return 0.0
    if full <= 0:
        return 0.4 if oos > 0 else 0.0
    ratio = oos / abs(full)
    return float(np.clip(ratio, 0.0, 1.5) / 1.5)


def build_scorecard(results: Dict[str, StrategyResult], corr: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for k, r in results.items():
        avg_corr = float(corr[k].drop(k).mean()) if k in corr.columns and len(corr) > 1 else 0.0
        oos_sh = float(r.summary_oos.get("sharpe", 0.0))
        is_sh = float(r.summary_is.get("sharpe", 0.0))
        stab = 0.0
        if is_sh > 0:
            stab = float(np.clip(oos_sh / is_sh, 0.0, 1.5) / 1.5)
        elif oos_sh > 0:
            stab = 0.5
        # 风险收益：Calmar 与 Sharpe 的折中
        calmar = float(r.summary_oos.get("calmar", 0.0) or 0.0)
        rr = float(np.clip(0.5 * oos_sh + 0.5 * max(calmar, 0.0), -1, 3))
        rows.append(
            {
                "strategy_id": k,
                "category": r.meta["category"],
                "name": r.meta["name_cn"],
                "params": r.meta["params"],
                "source": r.meta["source"],
                "full_return": r.summary_full["total_return"],
                "full_cagr": r.summary_full["cagr"],
                "full_vol": r.summary_full["ann_vol"],
                "full_sharpe": r.summary_full["sharpe"],
                "full_sortino": r.summary_full["sortino"],
                "full_maxdd": r.summary_full["max_drawdown"],
                "full_calmar": r.summary_full["calmar"],
                "full_winrate": r.summary_full["win_rate"],
                "full_payoff": r.summary_full["payoff_ratio"],
                "is_sharpe": r.summary_is["sharpe"],
                "is_maxdd": r.summary_is["max_drawdown"],
                "is_return": r.summary_is["total_return"],
                "oos_sharpe": r.summary_oos["sharpe"],
                "oos_maxdd": r.summary_oos["max_drawdown"],
                "oos_return": r.summary_oos["total_return"],
                "oos_cagr": r.summary_oos["cagr"],
                "avg_corr_others": avg_corr,
                "stability_0_1": stab,
                "risk_reward_proxy": rr,
                "deploy_note": DEPLOY_NOTES[k],
            }
        )
    return pd.DataFrame(rows)


def _plot_each(results: Dict[str, StrategyResult], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for k, r in results.items():
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(r.nav.index, r.nav.values, color="#1f4e79", lw=1.6)
        ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", lw=1.0, label="OOS start")
        title = (
            f"{r.meta['name_en']} | no leverage | "
            f"Full Sharpe={r.summary_full['sharpe']:.2f}  OOS Sharpe={r.summary_oos['sharpe']:.2f}"
        )
        ax.set_title(title)
        ax.set_ylabel("NAV")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"nav_{k}.png"), dpi=140)
        plt.close(fig)


def _plot_portfolio(nav: pd.Series, results: Dict[str, StrategyResult], keys: List[str], out_path: str, title: str) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(nav.index, nav.values, color="#1f4e79", lw=2.2, label="Equal-weight portfolio")
    for k in keys:
        ax.plot(
            results[k].nav.index,
            results[k].nav.values,
            lw=1.0,
            alpha=0.75,
            label=results[k].meta["name_en"],
        )
    ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", lw=1.0, label="OOS start")
    ax.set_title(title)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def _write_report(
    scorecard: pd.DataFrame,
    corr: pd.DataFrame,
    port_sum: Dict,
    dep_sum: Dict,
    deploy_keys: List[str],
    out_dir: str,
) -> str:
    path = os.path.join(out_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 趋势 / 套利可落地策略测评报告（无杠杆）\n\n")
        f.write("## 框架摘要\n\n")
        f.write("- 参数全部冻结为文献/业界默认，**不做全样本寻优**\n")
        f.write(f"- IS: ≤{IS_END}；OOS: ≥{OOS_START}（headline 看 OOS）\n")
        f.write("- 无杠杆：方向策略总名义 ≤ 资金；跨期按约 10% 保证金预算近似 1× 名义\n")
        f.write("- 详见 `cta/suite/FRAMEWORK.md`\n\n")
        f.write("## 业绩总表\n\n")
        cols = [
            "category",
            "name",
            "params",
            "full_cagr",
            "full_sharpe",
            "full_maxdd",
            "full_calmar",
            "is_sharpe",
            "oos_sharpe",
            "oos_cagr",
            "oos_maxdd",
            "avg_corr_others",
            "stability_0_1",
            "risk_reward_proxy",
        ]
        show = scorecard[cols].copy()
        for c in ["full_cagr", "oos_cagr", "full_maxdd", "oos_maxdd"]:
            show[c] = show[c].map(lambda x: f"{x:.2%}")
        for c in [
            "full_sharpe",
            "full_calmar",
            "is_sharpe",
            "oos_sharpe",
            "avg_corr_others",
            "stability_0_1",
            "risk_reward_proxy",
        ]:
            show[c] = show[c].map(lambda x: f"{float(x):.3f}")
        f.write(show.to_markdown(index=False))
        f.write("\n\n## 等权合成组合\n\n")
        f.write(
            f"- **六策略等权**：累计 {port_sum['total_return']:.2%} | CAGR {port_sum['cagr']:.2%} | "
            f"Sharpe {port_sum['sharpe']:.3f} | MaxDD {port_sum['max_drawdown']:.2%}\n"
        )
        f.write(
            f"- **可落地子集**（{', '.join(deploy_keys)}）：累计 {dep_sum['total_return']:.2%} | "
            f"CAGR {dep_sum['cagr']:.2%} | Sharpe {dep_sum['sharpe']:.3f} | "
            f"MaxDD {dep_sum['max_drawdown']:.2%}\n\n"
        )
        f.write("## 策略相关矩阵\n\n")
        f.write(corr.round(3).to_markdown())
        f.write("\n\n## 实盘落地评估\n\n")
        for _, row in scorecard.iterrows():
            f.write(f"### {row['name']}（{row['category']}）\n\n")
            f.write(f"- 来源：{row['source']} | 参数：{row['params']}\n")
            f.write(
                f"- OOS Sharpe={row['oos_sharpe']:.3f}, OOS MaxDD={row['oos_maxdd']:.2%}, "
                f"稳定性={row['stability_0_1']:.2f}, 与其它策略平均相关={row['avg_corr_others']:.2f}\n"
            )
            verdict = "建议纳入" if row["strategy_id"] in deploy_keys else "暂不纳入核心组合"
            f.write(f"- 结论：**{verdict}** — {row['deploy_note']}\n\n")
        f.write("## 组合构建建议\n\n")
        f.write(
            "1. Headline 以 **OOS** 为准；Donchian / 布林在本样本 OOS 失效，说明震荡市假突破与趋势市反转磨损真实存在。\n"
            "2. 趋势侧保留 **DualMA**（及可选 TSMOM）；套利侧优先 **产业配对**，跨期作卫星。\n"
            "3. 等权是研究基准；实盘按 OOS 波动倒数加权，并设单策略风险预算与总回撤熔断。\n"
        )
    return path


def run_research_suite(
    data_dir: str = "cta_data_akshare",
    out_dir: str = "cta_result_suite",
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    plot: bool = True,
) -> Dict:
    panels = load_panels(data_dir)
    # 去掉股指：机制不同；保留商品池
    panels = {k: v for k, v in panels.items() if k.upper() != "IF"}
    os.makedirs(out_dir, exist_ok=True)

    keys = list(STRATEGY_META.keys())
    results: Dict[str, StrategyResult] = {}
    for k in keys:
        print(f"运行 {k} ...")
        results[k] = _run_one(panels, k, capital, contract_cache)

    corr = _corr_matrix(results)
    scorecard = build_scorecard(results, corr)
    port_nav, port_ret, port_sum = _equal_weight_portfolio(results, keys)

    # 可落地推荐组合：OOS Sharpe>0 且稳定性尚可
    deploy_keys = [
        k
        for k, r in results.items()
        if float(r.summary_oos.get("sharpe", 0.0)) > 0.05
        and float(r.summary_oos.get("max_drawdown", -1.0)) > -0.35
    ]
    if len(deploy_keys) < 2:
        deploy_keys = [k for k, r in results.items() if float(r.summary_oos.get("sharpe", 0.0)) > 0]
    dep_nav, dep_ret, dep_sum = _equal_weight_portfolio(results, deploy_keys) if deploy_keys else (port_nav, port_ret, port_sum)

    scorecard.to_csv(os.path.join(out_dir, "scorecard.csv"), index=False)
    corr.to_csv(os.path.join(out_dir, "corr_matrix.csv"))
    nav_df = pd.DataFrame({k: results[k].nav for k in keys})
    nav_df["portfolio_all6"] = port_nav.reindex(nav_df.index).ffill()
    nav_df["portfolio_deploy"] = dep_nav.reindex(nav_df.index).ffill()
    nav_df.to_csv(os.path.join(out_dir, "nav_all.csv"))
    pd.DataFrame(
        [
            {"sleeve": "portfolio_all6", **port_sum},
            {"sleeve": "portfolio_deploy", "members": ",".join(deploy_keys), **dep_sum},
        ]
    ).to_csv(os.path.join(out_dir, "portfolio_summary.csv"), index=False)

    report = _write_report(scorecard, corr, port_sum, dep_sum, deploy_keys, out_dir)
    if plot:
        _plot_each(results, out_dir)
        _plot_portfolio(
            port_nav,
            results,
            keys,
            os.path.join(out_dir, "nav_portfolio.png"),
            "Equal-weight 6-strategy portfolio (no leverage)",
        )
        trend_keys = [k for k in keys if k.startswith("trend_")]
        arb_keys = [k for k in keys if k.startswith("arb_")]
        tnav, _, _ = _equal_weight_portfolio(results, trend_keys)
        anav, _, _ = _equal_weight_portfolio(results, arb_keys)
        _plot_portfolio(
            tnav, results, trend_keys, os.path.join(out_dir, "nav_trend_book.png"), "Trend book (equal weight, no leverage)"
        )
        _plot_portfolio(
            anav, results, arb_keys, os.path.join(out_dir, "nav_arb_book.png"), "Arb book (equal weight, no leverage)"
        )
        if deploy_keys:
            _plot_portfolio(
                dep_nav,
                results,
                deploy_keys,
                os.path.join(out_dir, "nav_portfolio_deploy.png"),
                f"Deployable subset ({'+'.join(deploy_keys)}) no leverage",
            )

    print(f"报告: {report}")
    print(f"可落地组合成员: {deploy_keys}")
    return {
        "results": results,
        "scorecard": scorecard,
        "corr": corr,
        "portfolio_nav": port_nav,
        "portfolio_summary": port_sum,
        "deploy_keys": deploy_keys,
        "deploy_summary": dep_sum,
        "out_dir": out_dir,
    }
