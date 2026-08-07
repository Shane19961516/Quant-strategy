# -*- coding: utf-8 -*-
"""策略参数网格搜索：跨品种泛化 + 局部夏普稳定 + 样本外验证。

所有候选参数都叠加 ATR 止损（趋势策略高赔率低胜率的必要组件）。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .metrics import performance_summary
from .signals import donchian_breakout_signal, dual_ma_signal, ts_momentum_signal
from .stops import StopConfig, apply_atr_stop


@dataclass(frozen=True)
class ParamSet:
    method: str
    params: Tuple[Tuple[str, float], ...]

    def as_dict(self) -> Dict[str, float]:
        return {k: int(v) if float(v).is_integer() else float(v) for k, v in self.params}

    def label(self) -> str:
        parts = []
        for k, v in self.params:
            parts.append(f"{k}={int(v) if float(v).is_integer() else v}")
        return self.method + "|" + ",".join(parts)


def build_raw_signal(method: str, ohlc: pd.DataFrame, params: Dict[str, float]) -> pd.Series:
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


def _stop_config(params: Dict[str, float]) -> StopConfig:
    atr_mult = float(params.get("atr_mult", 2.0))
    trail_mult = float(params.get("trail_mult", max(atr_mult + 1.0, 3.0)))
    return StopConfig(
        atr_window=int(params.get("atr_window", 20)),
        atr_mult=atr_mult,
        trail_mult=trail_mult,
        use_trailing=bool(int(params.get("use_trailing", 1))),
        cooldown_bars=int(params.get("cooldown", 0)),
    )


def build_stopped_signal(method: str, ohlc: pd.DataFrame, params: Dict[str, float]) -> pd.Series:
    """原始信号 + ATR 止损/跟踪止损。"""
    raw = build_raw_signal(method, ohlc, params)
    open_ = ohlc["open"] if "open" in ohlc.columns else None
    stopped, _, _ = apply_atr_stop(
        raw, ohlc["high"], ohlc["low"], ohlc["close"], _stop_config(params), open_=open_
    )
    return stopped


def build_stopped_signal_with_exits(
    method: str, ohlc: pd.DataFrame, params: Dict[str, float]
) -> Tuple[pd.Series, pd.Series]:
    """返回 (stopped_signal, stop_exit_ret)。"""
    raw = build_raw_signal(method, ohlc, params)
    open_ = ohlc["open"] if "open" in ohlc.columns else None
    stopped, _, exit_ret = apply_atr_stop(
        raw, ohlc["high"], ohlc["low"], ohlc["close"], _stop_config(params), open_=open_
    )
    return stopped, exit_ret


def unit_strategy_returns(
    panels: Dict[str, pd.DataFrame],
    method: str,
    params: Dict[str, float],
    cost_bps: float = 1.0,
) -> pd.DataFrame:
    """各品种单位名义仓位（|w|=1）下的策略日收益，T+1。

    若当日触发 ATR 止损，用止损价收益替换 close-to-close，避免穿仓高估亏损。
    """
    cols = {}
    for sym, ohlc in panels.items():
        sig, exit_ret = build_stopped_signal_with_exits(method, ohlc, params)
        sig = sig.fillna(0.0)
        ret = ohlc["close"].pct_change().fillna(0.0)
        ret = ret.mask(ret.abs() > 0.08, 0.0)
        traded = sig.shift(1).fillna(0.0)
        # 止损日：持仓收益按止损价结算
        asset_pnl = traded * ret
        stop_hit = exit_ret.notna() & (traded != 0)
        asset_pnl = asset_pnl.where(~stop_hit, exit_ret)
        turnover = sig.diff().abs().fillna(sig.abs())
        cost = turnover * (cost_bps / 10000.0)
        cols[sym] = asset_pnl - cost
    return pd.DataFrame(cols).sort_index().fillna(0.0)


def equal_weight_portfolio_returns(asset_strat_ret: pd.DataFrame) -> pd.Series:
    return asset_strat_ret.mean(axis=1)


def param_grid(method: str) -> List[ParamSet]:
    """信号参数 × 止损参数。网格控制规模以免过拟合搜索空间过大。"""
    grids: List[ParamSet] = []
    # 趋势策略：紧止损截断左尾，宽跟踪止损让利润奔跑
    atr_grid = [1.5, 2.0, 2.5, 3.0]
    if method == "dual_ma":
        for fast, slow, atr_m in product([10, 20, 30], [60, 100, 120], atr_grid):
            if fast < slow:
                grids.append(
                    ParamSet(
                        "dual_ma",
                        (("fast", fast), ("slow", slow), ("atr_mult", atr_m), ("trail_mult", atr_m + 1.0)),
                    )
                )
    elif method == "donchian":
        for entry, exit_, atr_m in product([20, 40, 55], [10, 20], atr_grid):
            if exit_ < entry:
                grids.append(
                    ParamSet(
                        "donchian",
                        (("entry", entry), ("exit", exit_), ("atr_mult", atr_m), ("trail_mult", atr_m + 1.0)),
                    )
                )
    elif method == "tsmom":
        for lookback, atr_m in product([20, 60, 90, 120], atr_grid):
            grids.append(
                ParamSet(
                    "tsmom",
                    (("lookback", lookback), ("skip", 1), ("atr_mult", atr_m), ("trail_mult", atr_m + 1.0)),
                )
            )
    else:
        raise ValueError(method)
    return grids


def _neighbor_keys(ps: ParamSet, all_sets: Sequence[ParamSet]) -> List[ParamSet]:
    d0 = ps.as_dict()
    keys = list(d0.keys())
    refined = []
    for other in all_sets:
        if other.method != ps.method or other.label() == ps.label():
            continue
        d1 = other.as_dict()
        dist = 0
        ok = True
        for k in keys:
            if d0[k] == d1[k]:
                continue
            step = {
                "fast": 10,
                "slow": 20,
                "entry": 15,
                "exit": 10,
                "lookback": 30,
                "skip": 4,
                "atr_mult": 0.5,
                "trail_mult": 0.5,
            }.get(k, 10)
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
    min_train_sharpe: float = 0.15,
    min_valid_sharpe: float = 0.0,
    min_positive_asset_frac: float = 0.35,
    max_local_sharpe_std: float = 0.40,
    cost_bps: float = 0.5,
) -> OptimizeResult:
    grid = param_grid(method)
    train_end_ts = pd.Timestamp(train_end)
    valid_end_ts = pd.Timestamp(valid_end)

    rows = []
    for ps in grid:
        asset_ret = unit_strategy_returns(panels, method, ps.as_dict(), cost_bps=cost_bps)
        port = equal_weight_portfolio_returns(asset_ret)

        tr = port.loc[:train_end_ts]
        va = port.loc[train_end_ts + pd.Timedelta(days=1) : valid_end_ts]
        if len(tr.dropna()) < 252 or len(va.dropna()) < 120:
            continue

        tr_sum = performance_summary((1 + tr).cumprod(), tr)
        va_sum = performance_summary((1 + va).cumprod(), va)
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
                "valid_maxdd": va_sum["max_drawdown"],
                "pos_asset_frac": pos_frac,
            }
        )

    table = pd.DataFrame(rows)
    if table.empty:
        raise RuntimeError(f"no valid param rows for {method}")

    label_to_ps = {ps.label(): ps for ps in grid}
    sharpe_map = table.set_index("label")["train_sharpe"].to_dict()
    local_std = {}
    for lab, sharpe in sharpe_map.items():
        neigh = _neighbor_keys(label_to_ps[lab], grid)
        vals = [sharpe] + [sharpe_map[n.label()] for n in neigh if n.label() in sharpe_map]
        local_std[lab] = float(np.std(vals)) if len(vals) >= 2 else 0.0
    table["local_sharpe_std"] = table["label"].map(local_std)

    cand = table[
        (table["train_sharpe"] >= min_train_sharpe)
        & (table["train_total"] > 0)
        & (table["valid_sharpe"] >= min_valid_sharpe)
        & (table["valid_total"] > -0.05)
        & (table["pos_asset_frac"] >= min_positive_asset_frac)
        & (table["local_sharpe_std"] <= max_local_sharpe_std)
    ].copy()

    def _score(df: pd.DataFrame) -> pd.Series:
        # 趋势策略更看重验证夏普与回撤控制
        return (
            0.35 * df["train_sharpe"]
            + 0.40 * df["valid_sharpe"]
            + 0.10 * df["pos_asset_frac"]
            - 0.30 * df["local_sharpe_std"]
            - 0.15 * df["train_maxdd"].abs()
            + 0.10 * np.clip(df["train_cagr"], -1, 1)
        )

    if cand.empty:
        table["score"] = _score(table)
        table = table.sort_values("score", ascending=False)
        best_row = table.iloc[0]
    else:
        cand["score"] = _score(cand)
        cand = cand.sort_values("score", ascending=False)
        table = table.merge(cand[["label", "score"]], on="label", how="left")
        table = table.sort_values("score", ascending=False, na_position="last")
        best_row = cand.iloc[0]

    best = label_to_ps[str(best_row["label"])]
    return OptimizeResult(method=method, best=best, score_table=table, chosen_metrics=best_row.to_dict())


def optimize_all_methods(
    panels: Dict[str, pd.DataFrame],
    methods: Optional[Iterable[str]] = None,
    **kwargs,
) -> Dict[str, OptimizeResult]:
    methods = list(methods or ["dual_ma", "donchian", "tsmom"])
    return {m: optimize_strategy_params(panels, m, **kwargs) for m in methods}
