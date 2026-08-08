# -*- coding: utf-8 -*-
"""测评 v2：IS/OOS、滚动 WF、成本压力、逆波动组合、严格落地门槛。"""

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
from .noleverage import slice_period, walk_forward_oos_sharpes
from .trend import run_trend_strategy

IS_END = "2021-12-31"
OOS_START = "2022-01-01"

STRATEGY_META = {
    "trend_tsmom": {
        "category": "趋势",
        "name_cn": "时间序列动量 TSMOM",
        "name_en": "TSMOM 60d inv-vol",
        "params": "L=60,skip=1,inv-vol,lev≤1",
        "source": "AQR / Baltas",
    },
    "trend_donchian": {
        "category": "趋势",
        "name_cn": "唐奇安长周期突破",
        "name_en": "Donchian 55/20",
        "params": "entry=55,exit=20,inv-vol",
        "source": "Turtle System2",
    },
    "trend_dualma": {
        "category": "趋势",
        "name_cn": "双均线+ATR止损",
        "name_en": "DualMA+ATR stop",
        "params": "MA20/60, ATR trail 2.5/3",
        "source": "经典CTA+止损",
    },
    "arb_calendar": {
        "category": "套利",
        "name_cn": "跨期价差回归",
        "name_en": "Calendar spread",
        "params": "z20±2; RB/HC/I/CU; ~1x",
        "source": "国内跨期主流",
    },
    "arb_pairs": {
        "category": "套利",
        "name_cn": "产业配对(相关+半衰期)",
        "name_en": "Gated economic pairs",
        "params": "z60, corr≥0.55, HL 5-45d",
        "source": "产业链统计套利",
    },
    "arb_xs_reversal": {
        "category": "套利",
        "name_cn": "截面短周期反转",
        "name_en": "XS 5d reversal",
        "params": "L=5, top/bottom 3",
        "source": "商品截面短反转",
    },
}

DEPLOY_NOTES = {
    "trend_tsmom": "落地高：逆波动分散；震荡磨损仍在，作趋势卫星仓。",
    "trend_donchian": "落地高：55/20 降低假突破；交易频次低，适合日频执行。",
    "trend_dualma": "落地高：ATR止损贴近实盘；与 TSMOM 相关偏高时可降权。",
    "arb_calendar": "落地中：需组合单与交割前移仓；远月流动性为硬约束。",
    "arb_pairs": "落地中高：相关/半衰期门控降低失效对；需单腿对账。",
    "arb_xs_reversal": "落地中：换手较高、成本敏感；适合小权重卫星。",
}


@dataclass
class StrategyResult:
    key: str
    nav: pd.Series
    ret: pd.Series
    summary_full: Dict
    summary_is: Dict
    summary_oos: Dict
    wf: Dict
    stress_oos_sharpe: float
    meta: Dict = field(default_factory=dict)


def _run_one(
    panels,
    key: str,
    capital: float,
    contract_cache: str,
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Tuple[pd.Series, pd.Series]:
    if key.startswith("trend_"):
        return run_trend_strategy(panels, key, capital=capital, cost_bps=cost_bps, slip_bps=slip_bps)[:2]
    if key == "arb_calendar":
        # 跨期成本在 CalendarConfig；压力测试同步放大
        from ..book.strategies_4 import CalendarConfig

        scale = max(cost_bps / 1.5, 1.0)
        cal = CalendarConfig(near_cost_bps=0.8 * scale, far_cost_bps=2.0 * scale)
        from .arb import run_arb_calendar

        return run_arb_calendar(panels, capital=capital, contract_cache=contract_cache, cal_cfg=cal)[:2]
    return run_arb_strategy(
        panels, key, capital=capital, contract_cache=contract_cache, cost_bps=cost_bps, slip_bps=slip_bps
    )[:2]


def _build_result(panels, key, capital, contract_cache) -> StrategyResult:
    nav, ret = _run_one(panels, key, capital, contract_cache, 1.5, 1.5)
    ret = ret.fillna(0.0)
    nav = (1.0 + ret).cumprod()
    full = performance_summary(nav, ret)
    _, _, summ_is = slice_period(nav, ret, None, IS_END)
    _, _, summ_oos = slice_period(nav, ret, OOS_START, None)
    wf = walk_forward_oos_sharpes(ret)
    # 成本翻倍压力：仅看 OOS 夏普
    _, ret_s = _run_one(panels, key, capital, contract_cache, 3.0, 3.0)
    ret_s = ret_s.fillna(0.0)
    _, _, stress_oos = slice_period((1.0 + ret_s).cumprod(), ret_s, OOS_START, None)
    return StrategyResult(
        key=key,
        nav=nav,
        ret=ret,
        summary_full=full,
        summary_is=summ_is,
        summary_oos=summ_oos,
        wf=wf,
        stress_oos_sharpe=float(stress_oos.get("sharpe", 0.0)),
        meta=STRATEGY_META[key],
    )


def _corr_matrix(results: Dict[str, StrategyResult]) -> pd.DataFrame:
    return pd.DataFrame({k: v.ret for k, v in results.items()}).fillna(0.0).corr()


def _equal_weight_portfolio(results: Dict[str, StrategyResult], keys: List[str]) -> Tuple[pd.Series, pd.Series, Dict]:
    rets = pd.DataFrame({k: results[k].ret for k in keys}).fillna(0.0)
    port = rets.mean(axis=1).rename("ret")
    nav = (1.0 + port).cumprod().rename("nav")
    return nav, port, performance_summary(nav, port)


def _invvol_portfolio(
    results: Dict[str, StrategyResult],
    keys: List[str],
    vol_lookback: int = 60,
    trend_budget: float = 0.40,
    arb_budget: float = 0.60,
    max_weight: float = 0.30,
) -> Tuple[pd.Series, pd.Series, Dict]:
    """分层逆波动 + 单策略权重上限（避免低波跨期吞掉套利预算）。"""
    rets = pd.DataFrame({k: results[k].ret for k in keys}).fillna(0.0)
    vols = rets.rolling(vol_lookback, min_periods=20).std()
    trend_keys = [k for k in keys if k.startswith("trend_")]
    arb_keys = [k for k in keys if k.startswith("arb_")]
    w = pd.DataFrame(0.0, index=rets.index, columns=keys)

    def _fill(group: List[str], budget: float) -> None:
        if not group or budget <= 0:
            return
        inv = 1.0 / vols[group].replace(0.0, np.nan)
        ww = inv.div(inv.sum(axis=1), axis=0).fillna(0.0) * budget
        for k in group:
            w[k] = ww[k]

    if not trend_keys:
        _fill(arb_keys, 1.0)
    elif not arb_keys:
        _fill(trend_keys, 1.0)
    else:
        _fill(trend_keys, trend_budget)
        _fill(arb_keys, arb_budget)

    # 权重上限后重新归一
    w = w.clip(upper=max_weight)
    row_sum = w.sum(axis=1).replace(0.0, np.nan)
    w = w.div(row_sum, axis=0).fillna(0.0)

    w_lag = w.shift(1).fillna(0.0)
    port = (w_lag * rets).sum(axis=1).rename("ret")
    nav = (1.0 + port).cumprod().rename("nav")
    return nav, port, performance_summary(nav, port)


def _is_deployable(r: StrategyResult) -> bool:
    """严格落地门槛（预先固定，避免事后放宽）。"""
    oos_sh = float(r.summary_oos.get("sharpe", 0.0))
    oos_dd = float(r.summary_oos.get("max_drawdown", -1.0))
    wf_mean = float(r.wf.get("wf_mean_sharpe", 0.0))
    wf_pos = float(r.wf.get("wf_pos_frac", 0.0))
    stress = float(r.stress_oos_sharpe)
    return (
        oos_sh >= 0.10
        and oos_dd > -0.30
        and wf_mean >= 0.0
        and wf_pos >= 0.5
        and stress >= 0.0
    )


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
        calmar = float(r.summary_oos.get("calmar", 0.0) or 0.0)
        rr = float(np.clip(0.5 * oos_sh + 0.5 * max(calmar, 0.0), -1, 3))
        deploy = _is_deployable(r)
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
                "wf_mean_sharpe": r.wf.get("wf_mean_sharpe", 0.0),
                "wf_median_sharpe": r.wf.get("wf_median_sharpe", 0.0),
                "wf_pos_frac": r.wf.get("wf_pos_frac", 0.0),
                "wf_n": r.wf.get("wf_n", 0.0),
                "stress_oos_sharpe": r.stress_oos_sharpe,
                "avg_corr_others": avg_corr,
                "stability_0_1": stab,
                "risk_reward_proxy": rr,
                "deployable": int(deploy),
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
        ax.set_title(
            f"{r.meta['name_en']} | lev≤1 | OOS Sh={r.summary_oos['sharpe']:.2f} | "
            f"WF={r.wf.get('wf_mean_sharpe', 0):.2f} | stress={r.stress_oos_sharpe:.2f}"
        )
        ax.set_ylabel("NAV")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"nav_{k}.png"), dpi=140)
        plt.close(fig)


def _plot_portfolio(
    nav: pd.Series, results: Dict[str, StrategyResult], keys: List[str], out_path: str, title: str
) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(nav.index, nav.values, color="#1f4e79", lw=2.2, label="Portfolio")
    for k in keys:
        ax.plot(results[k].nav.index, results[k].nav.values, lw=1.0, alpha=0.75, label=results[k].meta["name_en"])
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
    dep_eq: Dict,
    dep_iv: Dict,
    deploy_keys: List[str],
    out_dir: str,
) -> str:
    path = os.path.join(out_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 趋势/套利可落地测评报告 v2（lev≤1 · WF · 成本压力）\n\n")
        f.write("## 框架\n\n")
        f.write("- 文献锁参，禁止全样本寻优；详见 `cta/suite/FRAMEWORK.md`\n")
        f.write(f"- IS≤{IS_END} / OOS≥{OOS_START}；另报滚动 3y→1y WF 夏普\n")
        f.write("- 逆波动配权 + 杠杆上限 1；成本压力=佣金/滑点各 3bp\n")
        f.write(
            "- **落地门槛**：OOS Sharpe≥0.10 且 MaxDD>-30% 且 WF均值≥0 且 WF正比例≥50% 且压力OOS Sharpe≥0\n\n"
        )
        f.write("## 业绩总表\n\n")
        cols = [
            "category",
            "name",
            "params",
            "full_sharpe",
            "oos_sharpe",
            "oos_cagr",
            "oos_maxdd",
            "wf_mean_sharpe",
            "wf_pos_frac",
            "stress_oos_sharpe",
            "avg_corr_others",
            "deployable",
        ]
        show = scorecard[cols].copy()
        for c in ["oos_cagr", "oos_maxdd"]:
            show[c] = show[c].map(lambda x: f"{x:.2%}")
        for c in ["full_sharpe", "oos_sharpe", "wf_mean_sharpe", "wf_pos_frac", "stress_oos_sharpe", "avg_corr_others"]:
            show[c] = show[c].map(lambda x: f"{float(x):.3f}")
        f.write(show.to_markdown(index=False))
        f.write("\n\n## 组合\n\n")
        f.write(
            f"- 六策略等权：CAGR {port_sum['cagr']:.2%} | Sharpe {port_sum['sharpe']:.3f} | "
            f"MaxDD {port_sum['max_drawdown']:.2%}\n"
        )
        f.write(f"- 可落地子集等权（{', '.join(deploy_keys) or '无'}）：")
        if deploy_keys:
            f.write(
                f"CAGR {dep_eq['cagr']:.2%} | Sharpe {dep_eq['sharpe']:.3f} | MaxDD {dep_eq['max_drawdown']:.2%}\n"
            )
            f.write(
                f"- 可落地子集逆波动：CAGR {dep_iv['cagr']:.2%} | Sharpe {dep_iv['sharpe']:.3f} | "
                f"MaxDD {dep_iv['max_drawdown']:.2%}\n\n"
            )
        else:
            f.write("无策略过门槛\n\n")
        f.write("## 相关矩阵\n\n")
        f.write(corr.round(3).to_markdown())
        f.write("\n\n## 落地评估\n\n")
        for _, row in scorecard.iterrows():
            f.write(f"### {row['name']}（{row['category']}）\n\n")
            f.write(
                f"- OOS Sh={row['oos_sharpe']:.3f}, WF={row['wf_mean_sharpe']:.3f} "
                f"(pos {row['wf_pos_frac']:.0%}), stress={row['stress_oos_sharpe']:.3f}, "
                f"corr={row['avg_corr_others']:.2f}\n"
            )
            f.write(f"- **{'纳入' if row['deployable'] else '不纳入'}** — {row['deploy_note']}\n\n")
        f.write("## 实盘建议\n\n")
        f.write(
            "1. 只交易通过门槛的袖层；趋势与套利低相关，优先逆波动合成。\n"
            "2. 跨期用交易所组合单；配对监控相关失效应急平仓。\n"
            "3. 成本压力后仍为正才加仓；上线后保留样本外监控与回撤熔断。\n"
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
    panels = {k: v for k, v in panels.items() if k.upper() != "IF"}
    os.makedirs(out_dir, exist_ok=True)

    keys = list(STRATEGY_META.keys())
    results: Dict[str, StrategyResult] = {}
    for k in keys:
        print(f"运行 {k} ...")
        results[k] = _build_result(panels, k, capital, contract_cache)

    corr = _corr_matrix(results)
    scorecard = build_scorecard(results, corr)
    port_nav, _, port_sum = _equal_weight_portfolio(results, keys)

    deploy_keys = [k for k, r in results.items() if _is_deployable(r)]
    if deploy_keys:
        dep_nav_eq, _, dep_eq = _equal_weight_portfolio(results, deploy_keys)
        dep_nav_iv, _, dep_iv = _invvol_portfolio(results, deploy_keys)
    else:
        dep_nav_eq, dep_eq = port_nav, port_sum
        dep_nav_iv, dep_iv = port_nav, port_sum

    scorecard.to_csv(os.path.join(out_dir, "scorecard.csv"), index=False)
    corr.to_csv(os.path.join(out_dir, "corr_matrix.csv"))
    nav_df = pd.DataFrame({k: results[k].nav for k in keys})
    nav_df["portfolio_all6"] = port_nav.reindex(nav_df.index).ffill()
    nav_df["portfolio_deploy_eq"] = dep_nav_eq.reindex(nav_df.index).ffill()
    nav_df["portfolio_deploy_invvol"] = dep_nav_iv.reindex(nav_df.index).ffill()
    nav_df.to_csv(os.path.join(out_dir, "nav_all.csv"))
    pd.DataFrame(
        [
            {"sleeve": "portfolio_all6", **port_sum},
            {"sleeve": "portfolio_deploy_eq", "members": ",".join(deploy_keys), **dep_eq},
            {"sleeve": "portfolio_deploy_invvol", "members": ",".join(deploy_keys), **dep_iv},
        ]
    ).to_csv(os.path.join(out_dir, "portfolio_summary.csv"), index=False)

    report = _write_report(scorecard, corr, port_sum, dep_eq, dep_iv, deploy_keys, out_dir)
    if plot:
        _plot_each(results, out_dir)
        _plot_portfolio(
            port_nav, results, keys, os.path.join(out_dir, "nav_portfolio.png"), "All-6 equal weight (lev<=1)"
        )
        trend_keys = [k for k in keys if k.startswith("trend_")]
        arb_keys = [k for k in keys if k.startswith("arb_")]
        tnav, _, _ = _equal_weight_portfolio(results, trend_keys)
        anav, _, _ = _equal_weight_portfolio(results, arb_keys)
        _plot_portfolio(tnav, results, trend_keys, os.path.join(out_dir, "nav_trend_book.png"), "Trend book")
        _plot_portfolio(anav, results, arb_keys, os.path.join(out_dir, "nav_arb_book.png"), "Arb book")
        if deploy_keys:
            _plot_portfolio(
                dep_nav_eq,
                results,
                deploy_keys,
                os.path.join(out_dir, "nav_portfolio_deploy.png"),
                f"Deployable EQ ({'+'.join(deploy_keys)})",
            )
            _plot_portfolio(
                dep_nav_iv,
                results,
                deploy_keys,
                os.path.join(out_dir, "nav_portfolio_deploy_invvol.png"),
                f"Deployable inv-vol ({'+'.join(deploy_keys)})",
            )

    print(f"报告: {report}")
    print(f"可落地: {deploy_keys}")
    return {
        "results": results,
        "scorecard": scorecard,
        "corr": corr,
        "portfolio_summary": port_sum,
        "deploy_keys": deploy_keys,
        "deploy_eq": dep_eq,
        "deploy_invvol": dep_iv,
        "out_dir": out_dir,
    }
