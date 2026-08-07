# -*- coding: utf-8 -*-
"""策略参数网格搜索：跨品种泛化 + 局部夏普稳定 + 样本外验证。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .metrics import performance_summary
from .signals import donchian_breakout_signal, dual_ma_signal, ts_momentum_signal


@dataclass(frozen=True)
class ParamSet:
    method: str
    params: Tuple[Tuple[str, float], ...]

    def as_dict(self) -> Dict[str, float]:
        return {k: int(v) if float(v).is_integer() else float(v) for k, v in self.params}

    def label(self) -> str:
        return self.method + "|" + ",".join(f"{k}={int(v)}" for k, v in self.params)


def _signal_from_params(method: str, ohlc: pd.DataFrame, params: Dict[str, float]) -> pd.Series:
    close, high, low = ohlc["close"], ohlc["high"], ohlc["low"]
    if method == "dual_ma":
        return dual_ma_signal(close, fast=int(params["fast"]), slow=int(params["slow"]))
    if method == "donchian":
        return donchian_breakout_signal(
            high, low, close, entry=int(params["entry"]), exit_=int(params["exit"])
        )
    if method == "tsmom":
        return ts_momentum_signal(close, lookback=int(params["lookback"]), skip=int(params.get("skip", 1)))
    raise ValueError(method)


def unit_strategy_returns(
    panels: Dict[str, pd.DataFrame],
    method: str,
    params: Dict[str, float],
    cost_bps: float = 1.0,
) -> pd.DataFrame:
    """各品种单位名义仓位（|w|=1）下的策略日收益，T+1。"""
    cols = {}
    for sym, ohlc in panels.items():
        sig = _signal_from_params(method, ohlc, params).fillna(0.0)
        ret = ohlc["close"].pct_change().fillna(0.0)
        traded = sig.shift(1).fillna(0.0)
        turnover = sig.diff().abs().fillna(sig.abs())
        cost = turnover * (cost_bps / 10000.0)
        cols[sym] = traded * ret - cost
    return pd.DataFrame(cols).sort_index().fillna(0.0)


def equal_weight_portfolio_returns(asset_strat_ret: pd.DataFrame) -> pd.Series:
    """跨品种等权组合收益（仅对当日有数据的品种平均）。"""
    return asset_strat_ret.mean(axis=1)


def rolling_sharpe(returns: pd.Series, window: int = 252) -> pd.Series:
    mu = returns.rolling(window, min_periods=max(60, window // 3)).mean()
    sd = returns.rolling(window, min_periods=max(60, window // 3)).std()
    return (mu / sd.replace(0, np.nan)) * np.sqrt(252)


def param_grid(method: str) -> List[ParamSet]:
    grids: List[ParamSet] = []
    if method == "dual_ma":
        for fast, slow in product([5, 10, 15, 20, 30], [40, 60, 80, 100, 120]):
            if fast < slow:
                grids.append(ParamSet("dual_ma", (("fast", fast), ("slow", slow))))
    elif method == "donchian":
        for entry, exit_ in product([10, 15, 20, 30, 40, 55], [5, 10, 15, 20]):
            if exit_ < entry:
                grids.append(ParamSet("donchian", (("entry", entry), ("exit", exit_))))
    elif method == "tsmom":
        for lookback, skip in product([20, 40, 60, 90, 120, 180], [1, 5]):
            grids.append(ParamSet("tsmom", (("lookback", lookback), ("skip", skip))))
    else:
        raise ValueError(method)
    return grids


def _neighbor_keys(ps: ParamSet, all_sets: Sequence[ParamSet]) -> List[ParamSet]:
    """参数空间一阶邻居（曼哈顿距离=1）。"""
    d0 = ps.as_dict()
    keys = list(d0.keys())
    out = []
    for other in all_sets:
        if other.method != ps.method:
            continue
        d1 = other.as_dict()
        diffs = [abs(d0[k] - d1[k]) for k in keys]
        # 网格步长不同，用“有几个维度变化且变化幅度相对小”
        changed = sum(1 for x in diffs if x > 0)
        if changed == 0:
            continue
        if changed == 1 and max(diffs) <= max(20, 0.5 * max(abs(d0[k]) for k in keys)):
            # 单维相邻：差值不超过该维常见步长
            out.append(other)
    # 若上面过宽，再按精确网格邻接过滤
    refined = []
    for other in out:
        d1 = other.as_dict()
        dist = 0
        ok = True
        for k in keys:
            if d0[k] == d1[k]:
                continue
            # 允许的邻接步长
            step = {"fast": 5, "slow": 20, "entry": 5, "exit": 5, "lookback": 20, "skip": 4}.get(k, 10)
            if abs(d0[k] - d1[k]) <= step * 1.01:
                dist += 1
            else:
                ok = False
                break
        if ok and dist == 1:
            refined.append(other)
    return refined


@dataclass
class OptimizeResult:
    method: str
    best: ParamSet
    score_table: pd.DataFrame
    chosen_metrics: Dict[str, float]


def optimize_strategy_params(
    panels: Dict[str, pd.DataFrame],
    method: str,
    train_end: str = "2021-12-31",
    valid_end: str = "2023-12-31",
    min_train_sharpe: float = 0.2,
    min_valid_sharpe: float = 0.0,
    min_positive_asset_frac: float = 0.4,
    max_local_sharpe_std: float = 0.35,
    cost_bps: float = 1.0,
) -> OptimizeResult:
    """在训练集上寻参，要求：

    1) 同一组参数作用于全部品种（泛化）
    2) 组合样本内夏普、收益为正；正收益品种占比达标
    3) 参数邻域局部夏普波动不大（抗过拟合）
    4) 验证集夏普不低于阈值（泛化）
    """
    grid = param_grid(method)
    train_end_ts = pd.Timestamp(train_end)
    valid_end_ts = pd.Timestamp(valid_end)

    rows = []
    ret_cache: Dict[str, pd.Series] = {}
    asset_ret_cache: Dict[str, pd.DataFrame] = {}

    for ps in grid:
        asset_ret = unit_strategy_returns(panels, method, ps.as_dict(), cost_bps=cost_bps)
        port = equal_weight_portfolio_returns(asset_ret)
        asset_ret_cache[ps.label()] = asset_ret
        ret_cache[ps.label()] = port

        tr = port.loc[:train_end_ts]
        va = port.loc[train_end_ts + pd.Timedelta(days=1) : valid_end_ts]
        if len(tr.dropna()) < 252 or len(va.dropna()) < 120:
            continue

        tr_sum = performance_summary((1 + tr).cumprod(), tr)
        va_sum = performance_summary((1 + va).cumprod(), va)
        # 品种层：训练期内累计收益为正的占比
        asset_tr = asset_ret.loc[:train_end_ts]
        pos_frac = float((asset_tr.sum() > 0).mean())

        rows.append(
            {
                "label": ps.label(),
                "method": method,
                **{f"p_{k}": v for k, v in ps.as_dict().items()},
                "train_sharpe": tr_sum["sharpe"],
                "train_cagr": tr_sum["cagr"],
                "train_total": tr_sum["total_return"],
                "train_maxdd": tr_sum["max_drawdown"],
                "valid_sharpe": va_sum["sharpe"],
                "valid_cagr": va_sum["cagr"],
                "valid_total": va_sum["total_return"],
                "pos_asset_frac": pos_frac,
            }
        )

    table = pd.DataFrame(rows)
    if table.empty:
        raise RuntimeError(f"no valid param rows for {method}")

    # 局部稳定性：邻域夏普标准差
    label_to_ps = {ps.label(): ps for ps in grid}
    sharpe_map = table.set_index("label")["train_sharpe"].to_dict()
    local_std = {}
    local_mean = {}
    for lab, sharpe in sharpe_map.items():
        neigh = _neighbor_keys(label_to_ps[lab], grid)
        vals = [sharpe] + [sharpe_map[n.label()] for n in neigh if n.label() in sharpe_map]
        local_std[lab] = float(np.std(vals)) if len(vals) >= 2 else 0.0
        local_mean[lab] = float(np.mean(vals))
    table["local_sharpe_std"] = table["label"].map(local_std)
    table["local_sharpe_mean"] = table["label"].map(local_mean)

    # 过滤
    cand = table[
        (table["train_sharpe"] >= min_train_sharpe)
        & (table["train_total"] > 0)
        & (table["valid_sharpe"] >= min_valid_sharpe)
        & (table["valid_total"] > -0.05)  # 验证集允许小幅回撤但不崩
        & (table["pos_asset_frac"] >= min_positive_asset_frac)
        & (table["local_sharpe_std"] <= max_local_sharpe_std)
    ].copy()

    if cand.empty:
        # 放宽：按综合得分取最优，仍记录未过硬筛的原因
        table["score"] = (
            0.45 * table["train_sharpe"]
            + 0.35 * table["valid_sharpe"]
            + 0.15 * table["pos_asset_frac"]
            - 0.40 * table["local_sharpe_std"]
        )
        table = table.sort_values("score", ascending=False)
        best_row = table.iloc[0]
    else:
        cand["score"] = (
            0.40 * cand["train_sharpe"]
            + 0.40 * cand["valid_sharpe"]
            + 0.15 * cand["pos_asset_frac"]
            - 0.35 * cand["local_sharpe_std"]
            + 0.10 * np.clip(cand["train_cagr"], -1, 1)
        )
        cand = cand.sort_values("score", ascending=False)
        table = table.merge(cand[["label", "score"]], on="label", how="left")
        table = table.sort_values("score", ascending=False, na_position="last")
        best_row = cand.iloc[0]

    best = label_to_ps[str(best_row["label"])]
    metrics = best_row.to_dict()
    return OptimizeResult(method=method, best=best, score_table=table, chosen_metrics=metrics)


def optimize_all_methods(
    panels: Dict[str, pd.DataFrame],
    methods: Optional[Iterable[str]] = None,
    **kwargs,
) -> Dict[str, OptimizeResult]:
    methods = list(methods or ["dual_ma", "donchian", "tsmom"])
    return {m: optimize_strategy_params(panels, m, **kwargs) for m in methods}
