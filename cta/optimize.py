# -*- coding: utf-8 -*-
"""策略参数网格搜索：跨品种/跨配对泛化 + 局部夏普稳定 + 样本外验证。

默认策略集为套利/反转（pairs / bollinger / reversal）。
趋势方法仍保留，但反转/配对使用内置回归出场 + z 止损，不再套 ATR 跟踪止盈。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .metrics import performance_summary
from .pairs import build_pairs_symbol_signals, unit_pair_returns
from .signals import (
    bollinger_reversion_signal,
    donchian_breakout_signal,
    dual_ma_signal,
    short_term_reversal_signal,
    ts_momentum_signal,
)
from .stops import StopConfig, apply_atr_stop

DEFAULT_METHODS = ("pairs", "bollinger", "reversal")
TREND_METHODS = {"dual_ma", "donchian", "tsmom"}
REVERSION_METHODS = {"bollinger", "reversal"}
PAIR_METHODS = {"pairs"}


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
    if method == "bollinger":
        return bollinger_reversion_signal(
            close,
            window=int(params["window"]),
            n_std=float(params["n_std"]),
            exit_z=float(params.get("exit_z", 0.0)),
            stop_z=float(params["stop_z"]),
        )
    if method == "reversal":
        return short_term_reversal_signal(
            close,
            lookback=int(params["lookback"]),
            entry_z=float(params["entry_z"]),
            exit_z=float(params.get("exit_z", 0.0)),
            stop_z=float(params["stop_z"]),
        )
    if method == "pairs":
        raise ValueError("pairs is cross-asset; use build_method_signal_frame")
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


def uses_atr_stop(method: str) -> bool:
    return method in TREND_METHODS


def build_stopped_signal(method: str, ohlc: pd.DataFrame, params: Dict[str, float]) -> pd.Series:
    """单品种信号。趋势叠加 ATR 止损；反转用内置出场。"""
    if method == "pairs":
        raise ValueError("pairs is cross-asset; use build_method_signal_frame")
    raw = build_raw_signal(method, ohlc, params)
    if not uses_atr_stop(method):
        return raw.fillna(0.0)
    open_ = ohlc["open"] if "open" in ohlc.columns else None
    stopped, _, _ = apply_atr_stop(
        raw, ohlc["high"], ohlc["low"], ohlc["close"], _stop_config(params), open_=open_
    )
    return stopped


def build_stopped_signal_with_exits(
    method: str, ohlc: pd.DataFrame, params: Dict[str, float]
) -> Tuple[pd.Series, pd.Series]:
    """返回 (signal, stop_exit_ret)。反转/配对无 ATR 止损日收益（全 NaN）。"""
    if method == "pairs":
        raise ValueError("pairs is cross-asset; use build_method_signal_frame")
    raw = build_raw_signal(method, ohlc, params)
    if not uses_atr_stop(method):
        empty = pd.Series(np.nan, index=ohlc.index, name="stop_exit_ret")
        return raw.fillna(0.0), empty
    open_ = ohlc["open"] if "open" in ohlc.columns else None
    stopped, _, exit_ret = apply_atr_stop(
        raw, ohlc["high"], ohlc["low"], ohlc["close"], _stop_config(params), open_=open_
    )
    return stopped, exit_ret


def build_method_signal_frame(
    panels: Dict[str, pd.DataFrame],
    method: str,
    params: Dict[str, float],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """统一出口：返回 (signals, stop_exit_ret) 两个 DataFrame。"""
    if method == "pairs":
        sig = build_pairs_symbol_signals(panels, params)
        exits = pd.DataFrame(np.nan, index=sig.index, columns=sig.columns)
        return sig, exits
    sigs = {}
    exits = {}
    for sym, ohlc in panels.items():
        s, e = build_stopped_signal_with_exits(method, ohlc, params)
        sigs[sym] = s
        exits[sym] = e
    return (
        pd.DataFrame(sigs).sort_index().fillna(0.0),
        pd.DataFrame(exits).sort_index(),
    )


def unit_strategy_returns(
    panels: Dict[str, pd.DataFrame],
    method: str,
    params: Dict[str, float],
    cost_bps: float = 1.0,
) -> pd.DataFrame:
    """单位名义策略日收益。

    - 单品种方法：每列一个品种
    - pairs：每列一个经济配对（美元中性）
    """
    if method == "pairs":
        return unit_pair_returns(panels, params, cost_bps=cost_bps)

    cols = {}
    for sym, ohlc in panels.items():
        sig, exit_ret = build_stopped_signal_with_exits(method, ohlc, params)
        sig = sig.fillna(0.0)
        ret = ohlc["close"].pct_change().fillna(0.0)
        ret = ret.mask(ret.abs() > 0.08, 0.0)
        traded = sig.shift(1).fillna(0.0)
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
    """信号参数网格。"""
    grids: List[ParamSet] = []
    if method == "dual_ma":
        atr_grid = [1.5, 2.0, 2.5, 3.0]
        for fast, slow, atr_m in product([10, 20, 30], [60, 100, 120], atr_grid):
            if fast < slow:
                grids.append(
                    ParamSet(
                        "dual_ma",
                        (("fast", fast), ("slow", slow), ("atr_mult", atr_m), ("trail_mult", atr_m + 1.0)),
                    )
                )
    elif method == "donchian":
        atr_grid = [1.5, 2.0, 2.5, 3.0]
        for entry, exit_, atr_m in product([20, 40, 55], [10, 20], atr_grid):
            if exit_ < entry:
                grids.append(
                    ParamSet(
                        "donchian",
                        (("entry", entry), ("exit", exit_), ("atr_mult", atr_m), ("trail_mult", atr_m + 1.0)),
                    )
                )
    elif method == "tsmom":
        atr_grid = [1.5, 2.0, 2.5, 3.0]
        for lookback, atr_m in product([20, 60, 90, 120], atr_grid):
            grids.append(
                ParamSet(
                    "tsmom",
                    (("lookback", lookback), ("skip", 1), ("atr_mult", atr_m), ("trail_mult", atr_m + 1.0)),
                )
            )
    elif method == "bollinger":
        for window, n_std, stop_z in product([10, 20, 40], [1.5, 2.0, 2.5], [3.0, 3.5, 4.0]):
            if stop_z > n_std:
                grids.append(
                    ParamSet(
                        "bollinger",
                        (("window", window), ("n_std", n_std), ("exit_z", 0.0), ("stop_z", stop_z)),
                    )
                )
    elif method == "reversal":
        for lookback, entry_z, stop_z in product([3, 5, 10], [1.0, 1.5, 2.0], [2.5, 3.0, 3.5]):
            if stop_z > entry_z:
                grids.append(
                    ParamSet(
                        "reversal",
                        (("lookback", lookback), ("entry_z", entry_z), ("exit_z", 0.0), ("stop_z", stop_z)),
                    )
                )
    elif method == "pairs":
        # 同一组参数用于全部经济配对（跨配对泛化）
        for window, entry_z, stop_z in product([20, 40, 60, 90], [1.5, 2.0, 2.5], [3.0, 3.5, 4.0]):
            if stop_z > entry_z:
                grids.append(
                    ParamSet(
                        "pairs",
                        (("window", window), ("entry_z", entry_z), ("exit_z", 0.0), ("stop_z", stop_z)),
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
                "lookback": 2 if ps.method == "reversal" else 30,
                "skip": 4,
                "atr_mult": 0.5,
                "trail_mult": 0.5,
                "window": 20,
                "n_std": 0.5,
                "entry_z": 0.5,
                "exit_z": 0.25,
                "stop_z": 0.5,
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
    # 配对数量少，正收益“腿”占比阈值略放宽
    if method == "pairs" and min_positive_asset_frac > 0.4:
        min_positive_asset_frac = 0.4

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
        pos_frac = float((asset_tr.sum() > 0).mean()) if asset_tr.shape[1] else 0.0

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
        # 套利/反转更强调验证夏普与回撤
        return (
            0.30 * df["train_sharpe"]
            + 0.45 * df["valid_sharpe"]
            + 0.10 * df["pos_asset_frac"]
            - 0.25 * df["local_sharpe_std"]
            - 0.25 * df["train_maxdd"].abs()
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
    methods = list(methods or DEFAULT_METHODS)
    return {m: optimize_strategy_params(panels, m, **kwargs) for m in methods}
