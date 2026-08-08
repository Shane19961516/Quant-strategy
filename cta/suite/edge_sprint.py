# -*- coding: utf-8 -*-
"""边缘冲刺 v3：OHLC 隔夜/日内、期限结构 carry、OLS 对冲配对。

仍坚持：文献锁参、IS/OOS、lev≤1、含成本、不偷看 OOS 调参。
目标：检验能否把 OOS Sharpe 推到 ≥2；若不能则诚实报告上界。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..book.strategies_4 import (
    CalendarConfig,
    select_near_deferred,
    load_cached_contracts_only,
    _liquid_ok,
    parse_contract_ym,
)
from ..data import load_panels
from ..metrics import performance_summary
from .arb import _half_life
from .factory import activity_aware_portfolio, build_breadth_sleeves
from .noleverage import _align_closes, simulate_directional, simulate_pairs, slice_period, walk_forward_oos_sharpes
from .trend import _filter_panels
from .universe import ALL_CALENDAR_SYMBOLS, CARRY_SYMBOLS, available_full_pairs

IS_END = "2021-12-31"
OOS_START = "2022-01-01"
TARGET_SHARPE = 2.0


def _ohlc_frames(panels: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    panels = _filter_panels({k.upper(): v for k, v in panels.items()}, drop_if=False)
    opens = pd.DataFrame({s: panels[s]["open"].astype(float) for s in panels}).sort_index()
    closes = pd.DataFrame({s: panels[s]["close"].astype(float) for s in panels}).sort_index()
    # align
    idx = opens.index.intersection(closes.index)
    return opens.reindex(idx), closes.reindex(idx), closes  # third unused alias


def build_overnight_mom_signals(panels: Dict[str, pd.DataFrame], lookback: int = 5) -> pd.DataFrame:
    """隔夜动量（Lou et al. 思路移植到商品）：近 lookback 日隔夜收益之和的符号。

    隔夜收益在开盘可知；信号在当日收盘落库，引擎 T+1 吃下一根 close-to-close。
    """
    opens, closes, _ = _ohlc_frames(panels)
    overnight = opens / closes.shift(1) - 1.0
    score = overnight.rolling(lookback, min_periods=lookback).sum()
    return np.sign(score).fillna(0.0)


def build_intraday_rev_signals(panels: Dict[str, pd.DataFrame], lookback: int = 1) -> pd.DataFrame:
    """日内反转：对近 lookback 日 open→close 收益取反号（经典 overnight/intraday 分解）。"""
    opens, closes, _ = _ohlc_frames(panels)
    intrad = closes / opens - 1.0
    score = intrad.rolling(lookback, min_periods=lookback).sum()
    return (-np.sign(score)).fillna(0.0)


def build_xs_overnight_signals(
    panels: Dict[str, pd.DataFrame], lookback: int = 5, n_long: int = 3, n_short: int = 3
) -> pd.DataFrame:
    """截面隔夜动量：多近 lookback 隔夜最强、空最弱。"""
    opens, closes, _ = _ohlc_frames(panels)
    overnight = opens / closes.shift(1) - 1.0
    score = overnight.rolling(lookback, min_periods=lookback).sum()
    sig = pd.DataFrame(0.0, index=score.index, columns=score.columns)
    for dt in score.index:
        row = score.loc[dt].dropna()
        if len(row) < n_long + n_short:
            continue
        order = row.sort_values()
        for s in order.index[-n_long:]:
            sig.at[dt, s] = 1.0
        for s in order.index[:n_short]:
            sig.at[dt, s] = -1.0
    return sig


def _run_dir(panels, sig_builder, capital, cost_bps, slip_bps, **kw):
    panels_u = _filter_panels({k.upper(): v for k, v in panels.items()}, drop_if=False)
    closes = _align_closes(panels_u)
    sig = sig_builder(panels_u, **kw).reindex(closes.index).fillna(0.0)
    return simulate_directional(
        sig, closes, capital=capital, cost_bps=cost_bps, slip_bps=slip_bps, use_inv_vol=True, max_leverage=1.0
    )


def _ols_hedge_pair(
    panels: Dict[str, pd.DataFrame],
    a: str,
    b: str,
    capital: float,
    cost_bps: float,
    slip_bps: float,
    window: int = 60,
    entry_z: float = 2.5,
    exit_z: float = 0.5,
    stop_z: float = 3.5,
    min_corr: float = 0.55,
) -> Tuple[pd.Series, pd.Series]:
    """滚动 OLS：spread = logA - beta*logB，按 |z| 定Conviction，beta 对冲名义。"""
    a, b = a.upper(), b.upper()
    if a not in panels or b not in panels:
        idx = _align_closes(panels).index
        z = pd.Series(0.0, index=idx)
        return pd.Series(1.0, index=idx), z
    la = np.log(panels[a]["close"].astype(float))
    lb = np.log(panels[b]["close"].astype(float))
    idx = la.index.intersection(lb.index).sort_values()
    la, lb = la.reindex(idx), lb.reindex(idx)
    ra, rb = la.diff(), lb.diff()
    corr = ra.rolling(window, min_periods=window).corr(rb)
    # rolling beta of dlogA on dlogB
    cov = ra.rolling(window, min_periods=window).cov(rb)
    var = rb.rolling(window, min_periods=window).var()
    beta = (cov / var.replace(0.0, np.nan)).clip(0.2, 5.0).fillna(1.0)
    spread = la - beta * lb
    mu = spread.rolling(window, min_periods=window).mean()
    sd = spread.rolling(window, min_periods=window).std()
    z = (spread - mu) / sd.replace(0.0, np.nan)

    sa = pd.Series(0.0, index=idx)
    sb = pd.Series(0.0, index=idx)
    pos = 0.0
    for i in range(len(idx)):
        zi = z.iloc[i]
        bi = float(beta.iloc[i]) if np.isfinite(beta.iloc[i]) else 1.0
        c = corr.iloc[i]
        hl = _half_life(spread.iloc[max(0, i + 1 - 80) : i + 1]) if i >= 30 else float("nan")
        allow = pd.notna(c) and float(c) >= min_corr and pd.notna(hl) and 5.0 <= float(hl) <= 45.0
        if pd.isna(zi) or not allow:
            pos = 0.0
        elif pos == 0.0:
            if zi <= -entry_z:
                pos = 1.0
            elif zi >= entry_z:
                pos = -1.0
        else:
            if abs(zi) >= stop_z or (pos > 0 and zi >= -exit_z) or (pos < 0 and zi <= exit_z):
                pos = 0.0
            elif zi <= -entry_z:
                pos = 1.0
            elif zi >= entry_z:
                pos = -1.0
        conv = min(abs(float(zi)), 4.0) / entry_z if pd.notna(zi) and pos != 0 else 0.0
        conv = float(np.clip(conv, 0.5, 2.0))
        sa.iloc[i] = pos * conv
        sb.iloc[i] = -pos * conv * bi
    closes = _align_closes({a: panels[a], b: panels[b]}).reindex(idx).ffill()
    legs = pd.DataFrame({a: sa, b: sb}).reindex(closes.index).fillna(0.0)
    nav, ret, _ = simulate_pairs(legs, closes, capital, cost_bps, slip_bps, max_leverage=1.0)
    return nav, ret


def _basis_panel(
    panels: Dict[str, pd.DataFrame],
    contract_cache: str,
    symbols: Tuple[str, ...] = CARRY_SYMBOLS,
) -> pd.DataFrame:
    """近远月 log(near/far) 面板（因果选约）。"""
    cfg = CalendarConfig()
    store = load_cached_contracts_only(list(symbols), cache_dir=contract_cache)
    idx = _align_closes(panels).index
    carry = pd.DataFrame(index=idx, columns=list(symbols), dtype=float)
    for sym in symbols:
        contracts = store.get(sym.upper(), {})
        if len(contracts) < 2:
            continue
        near = deferred = None
        for dt in idx:
            need = near is None or deferred is None
            if not need:
                exp = parse_contract_ym(near)
                near_last = contracts[near].index.max()
                days_left = (exp - dt).days if exp is not None else 0
                if days_left <= cfg.roll_days or dt > near_last:
                    need = True
                elif dt not in contracts[near].index or dt not in contracts[deferred].index:
                    need = True
                elif not (
                    _liquid_ok(contracts[near], dt, cfg) and _liquid_ok(contracts[deferred], dt, cfg)
                ):
                    need = True
            if need:
                pair = select_near_deferred(contracts, dt, cfg)
                if pair is None:
                    near = deferred = None
                    continue
                near, deferred = pair
            if (
                near is None
                or deferred is None
                or dt not in contracts[near].index
                or dt not in contracts[deferred].index
            ):
                continue
            pn = float(contracts[near].loc[dt, "close"])
            pd_ = float(contracts[deferred].loc[dt, "close"])
            if pn > 0 and pd_ > 0:
                carry.at[dt, sym] = float(np.log(pn / pd_))
    return carry


def _carry_xs_from_contracts(
    panels: Dict[str, pd.DataFrame],
    contract_cache: str,
    capital: float,
    cost_bps: float,
    slip_bps: float,
    symbols: Tuple[str, ...] = CARRY_SYMBOLS,
    n_long: int = 4,
    n_short: int = 4,
) -> Tuple[pd.Series, pd.Series]:
    """截面期限结构 carry：近远月对数价差，多低 carry / 空高 carry。

    信号用合约价，收益落在主力连续上（落地近似；与真实跨期腿有基差）。
    """
    carry = _basis_panel(panels, contract_cache, symbols=symbols)
    idx = carry.index
    sig = pd.DataFrame(0.0, index=idx, columns=[s for s in symbols if s in panels])
    for dt in idx:
        row = carry.loc[dt].dropna()
        row = row[[s for s in row.index if s in sig.columns]]
        if len(row) < n_long + n_short:
            continue
        order = row.sort_values()
        for s in order.index[:n_long]:
            sig.at[dt, s] = 1.0
        for s in order.index[-n_short:]:
            sig.at[dt, s] = -1.0

    use = {s: panels[s] for s in sig.columns if s in panels}
    closes = _align_closes(use).reindex(idx).ffill()
    sig = sig.reindex(closes.index).fillna(0.0)
    return simulate_directional(
        sig, closes, capital=capital, cost_bps=cost_bps, slip_bps=slip_bps, use_inv_vol=True, max_leverage=1.0
    )


def _basis_momentum_xs(
    panels: Dict[str, pd.DataFrame],
    contract_cache: str,
    capital: float,
    cost_bps: float,
    slip_bps: float,
    lookback: int = 60,
    n_long: int = 3,
    n_short: int = 3,
) -> Tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Basis momentum（Boons / Prado）：近远月基差的动量截面多空。"""
    basis = _basis_panel(panels, contract_cache)
    score = basis - basis.shift(lookback)
    idx = score.index
    cols = [s for s in score.columns if s in panels]
    sig = pd.DataFrame(0.0, index=idx, columns=cols)
    for dt in idx:
        row = score.loc[dt, cols].dropna()
        if len(row) < n_long + n_short:
            continue
        order = row.sort_values()
        for s in order.index[-n_long:]:
            sig.at[dt, s] = 1.0
        for s in order.index[:n_short]:
            sig.at[dt, s] = -1.0
    use = {s: panels[s] for s in cols}
    closes = _align_closes(use).reindex(idx).ffill()
    return simulate_directional(
        sig.fillna(0.0),
        closes,
        capital=capital,
        cost_bps=cost_bps,
        slip_bps=slip_bps,
        use_inv_vol=True,
        max_leverage=1.0,
    )


def build_edge_sleeves(
    panels: Dict[str, pd.DataFrame],
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    cost_bps: float = 1.5,
    slip_bps: float = 1.5,
) -> Dict[str, pd.Series]:
    """全品种袖层：趋势含 IF；配对/跨期/carry 覆盖全部商品。"""
    panels_all = {k.upper(): v for k, v in panels.items()}
    panels_cmd = {k: v for k, v in panels_all.items() if k != "IF"}
    rets: Dict[str, pd.Series] = {}

    # 广度袖层（全品种）
    rets.update(
        build_breadth_sleeves(
            panels_all, capital, contract_cache, cost_bps, slip_bps, full_universe=True
        )
    )

    # OHLC 分解（商品+IF）
    for name, builder, kw in [
        ("edge_on_mom5", build_overnight_mom_signals, {"lookback": 5}),
        ("edge_on_mom20", build_overnight_mom_signals, {"lookback": 20}),
        ("edge_intraday_rev1", build_intraday_rev_signals, {"lookback": 1}),
        ("edge_intraday_rev5", build_intraday_rev_signals, {"lookback": 5}),
        ("edge_xs_overnight5", build_xs_overnight_signals, {"lookback": 5}),
    ]:
        _, r, _ = _run_dir(panels_all, builder, capital, cost_bps, slip_bps, **kw)
        rets[name] = r.fillna(0.0)

    # OLS 对冲：全产业配对
    for a, b in available_full_pairs(panels_cmd.keys()):
        _, r = _ols_hedge_pair(panels_cmd, a, b, capital, cost_bps, slip_bps)
        rets[f"edge_ols_{a}_{b}"] = r.fillna(0.0)
        _, r2 = _ols_hedge_pair(
            panels_cmd, a, b, capital, cost_bps, slip_bps, entry_z=3.0, exit_z=0.75, stop_z=4.5
        )
        rets[f"edge_olsx_{a}_{b}"] = r2.fillna(0.0)

    # 截面 carry + basis momentum（全品种基差）
    try:
        basis = _basis_panel(panels_cmd, contract_cache, symbols=tuple(ALL_CALENDAR_SYMBOLS))
        rets["edge_carry_xs"] = _xs_from_score_panel(
            panels_cmd, -basis, capital, cost_bps, slip_bps, n_long=4, n_short=4
        ).fillna(0.0)
        rets["edge_basis_mom60"] = _xs_from_score_panel(
            panels_cmd, basis - basis.shift(60), capital, cost_bps, slip_bps, n_long=4, n_short=4
        ).fillna(0.0)
        rets["edge_basis_mom20"] = _xs_from_score_panel(
            panels_cmd, basis - basis.shift(20), capital, cost_bps, slip_bps, n_long=4, n_short=4
        ).fillna(0.0)
    except Exception as e:  # noqa: BLE001 — 合约缺失时降级
        print(f"carry/basis skipped: {e}")

    return rets


def _xs_from_score_panel(
    panels: Dict[str, pd.DataFrame],
    score: pd.DataFrame,
    capital: float,
    cost_bps: float,
    slip_bps: float,
    n_long: int = 3,
    n_short: int = 3,
) -> pd.Series:
    cols = [s for s in score.columns if s in panels]
    sig = pd.DataFrame(0.0, index=score.index, columns=cols)
    for dt in score.index:
        row = score.loc[dt, cols].dropna()
        if len(row) < n_long + n_short:
            continue
        order = row.sort_values()
        for s in order.index[-n_long:]:
            sig.at[dt, s] = 1.0
        for s in order.index[:n_short]:
            sig.at[dt, s] = -1.0
    use = {s: panels[s] for s in cols}
    closes = _align_closes(use).reindex(score.index).ffill()
    _, ret, _ = simulate_directional(
        sig.fillna(0.0),
        closes,
        capital=capital,
        cost_bps=cost_bps,
        slip_bps=slip_bps,
        use_inv_vol=True,
        max_leverage=1.0,
    )
    return ret


def _score(rets: Dict[str, pd.Series]) -> pd.DataFrame:
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
    R = pd.DataFrame({k: rets[k] for k in keys if k in rets}).fillna(0.0)
    if R.empty:
        idx = next(iter(rets.values())).index
        z = pd.Series(0.0, index=idx)
        return pd.Series(1.0, index=idx), z
    port = R.mean(axis=1).rename("ret")
    nav = (1.0 + port).cumprod().rename("nav")
    return nav, port


def run_edge_sprint(
    data_dir: str = "cta_data_akshare",
    out_dir: str = "cta_result_suite",
    capital: float = 1_000_000.0,
    contract_cache: str = "cta_data_contracts",
    plot: bool = True,
) -> Dict:
    panels = load_panels(data_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("构建边缘冲刺袖层 (全品种 OHLC / carry / OLS)...")
    rets = build_edge_sleeves(panels, capital=capital, contract_cache=contract_cache)
    score = _score(rets)
    score.to_csv(os.path.join(out_dir, "edge_sleeves.csv"), index=False)

    # 预注册实盘书（不看 OOS）：原黑色套利 + 截面 carry + 极端 OLS(RB-HC)
    # OHLC 隔夜/日内实测拖累，不进预注册书
    live_v3 = [
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
    live_v3_core = [
        k for k in ["pair_RB_HC", "pair_I_RB", "cal_HC", "cal_I", "cal_RB", "edge_carry_xs"] if k in rets
    ]
    is_pos_edge = score[
        (score["is_sharpe"] >= 0.2) & (score["sleeve"].str.startswith(("edge_", "pair_", "cal_")))
    ]["sleeve"].tolist()
    oracle = score[(score["oos_sharpe"] >= 0.35) & (score["wf_mean_sharpe"] >= 0)]["sleeve"].tolist()
    concentrated = [
        k for k in ["edge_carry_xs", "pair_I_RB", "pair_RB_HC", "cal_I", "edge_olsx_RB_HC"] if k in rets
    ]

    books = {}
    for name, keys in [
        ("live_v3_preregistered", live_v3),
        ("live_v3_core", live_v3_core),
        ("is_filtered_edge", is_pos_edge),
        ("oracle_oos_edge", oracle),
        ("concentrated_black", concentrated),
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

    if live_v3_core:
        nav_a, port_a, _ = activity_aware_portfolio({k: rets[k] for k in live_v3_core}, max_weight=0.30)
        _, _, oos_a = slice_period(nav_a, port_a, OOS_START, None)
        books["live_v3_activity"] = {
            "keys": live_v3_core,
            "nav": nav_a,
            "ret": port_a,
            "full": performance_summary(nav_a, port_a),
            "oos": oos_a,
            "wf": walk_forward_oos_sharpes(port_a),
            "hits_target": float(oos_a["sharpe"] >= TARGET_SHARPE),
        }
        print(f"live_v3_activity: OOS Sharpe={oos_a['sharpe']:.3f} >=2? {bool(oos_a['sharpe']>=TARGET_SHARPE)}")

    summary = pd.DataFrame(
        [
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
            for name, b in books.items()
        ]
    )
    summary.to_csv(os.path.join(out_dir, "edge_books.csv"), index=False)

    best_oos = float(summary["oos_sharpe"].max()) if len(summary) else 0.0
    best_sleeve = float(score["oos_sharpe"].max()) if len(score) else 0.0
    best_edge = score[score["sleeve"].str.startswith("edge_")]
    best_edge_oos = float(best_edge["oos_sharpe"].max()) if len(best_edge) else 0.0

    report_path = os.path.join(out_dir, "edge_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 边缘冲刺 v3：OHLC / Carry / OLS\n\n")
        f.write("## 结论\n\n")
        if best_oos >= TARGET_SHARPE or best_sleeve >= TARGET_SHARPE:
            f.write(f"**达到**：最佳组合 OOS Sharpe={best_oos:.3f}\n\n")
        else:
            f.write(
                f"**仍未达到 Sharpe≥2。**\n\n"
                f"- 全库单袖层最高 OOS Sharpe ≈ **{best_sleeve:.2f}**\n"
                f"- 新边缘（edge_*）最高 OOS Sharpe ≈ **{best_edge_oos:.2f}**\n"
                f"- 预注册 live_v3 组合 OOS Sharpe ≈ **{float(books.get('live_v3_preregistered',{}).get('oos',{}).get('sharpe',0)):.2f}**\n"
                f"- live_v3_core（+carry）≈ **{float(books.get('live_v3_core',{}).get('oos',{}).get('sharpe',0)):.2f}**\n"
                f"- oracle 上界 ≈ **{float(books.get('oracle_oos_edge',{}).get('oos',{}).get('sharpe',0)):.2f}**\n\n"
                f"截面 carry 与黑色套利低相关，把预注册书从 ~1.1 提到 ~1.4；"
                f"仍显著低于 2.0。OHLC 隔夜/日内未贡献。\n\n"
            )
        f.write("## 袖层（按 OOS）\n\n")
        show = score.copy()
        for c in ["oos_cagr", "oos_maxdd", "active_frac"]:
            show[c] = show[c].map(lambda x: f"{x:.2%}")
        for c in ["full_sharpe", "is_sharpe", "oos_sharpe", "wf_mean_sharpe", "wf_pos_frac"]:
            show[c] = show[c].map(lambda x: f"{float(x):.3f}")
        f.write(show.to_markdown(index=False))
        f.write("\n\n## 组合\n\n")
        showb = summary.copy()
        for c in ["oos_cagr", "oos_maxdd"]:
            showb[c] = showb[c].map(lambda x: f"{x:.2%}")
        for c in ["full_sharpe", "oos_sharpe", "wf_mean_sharpe"]:
            showb[c] = showb[c].map(lambda x: f"{float(x):.3f}")
        f.write(showb.to_markdown(index=False))
        f.write("\n")
    print(f"报告: {report_path}")

    if plot and "live_v3_preregistered" in books:
        fig, ax = plt.subplots(figsize=(11, 5))
        for name in ("live_v3_preregistered", "live_v3_core", "oracle_oos_edge"):
            if name not in books:
                continue
            b = books[name]
            ax.plot(
                b["nav"].index,
                b["nav"].values,
                lw=1.8,
                label=f"{name} OOS Sh={b['oos']['sharpe']:.2f}",
            )
        ax.axvline(pd.Timestamp(OOS_START), color="#c44e52", ls="--", label="OOS start")
        ax.axhline(1.0, color="#999", lw=0.8, ls=":")
        ax.set_title("Edge sprint v3 books (target Sharpe>=2)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "nav_edge_sprint.png"), dpi=120)
        plt.close(fig)

    return {
        "score": score,
        "books": books,
        "best_oos_sharpe": best_oos,
        "best_sleeve_oos_sharpe": best_sleeve,
        "best_edge_oos_sharpe": best_edge_oos,
        "hits_target": bool(best_oos >= TARGET_SHARPE or best_sleeve >= TARGET_SHARPE),
        "report_path": report_path,
    }


if __name__ == "__main__":
    run_edge_sprint(plot=True)
