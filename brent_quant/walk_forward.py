"""Walk-forward: train window picks breakout_n by Sharpe, test is OOS."""

from __future__ import annotations

from typing import Any

import pandas as pd

from brent_quant import config as cfg
from brent_quant.backtest import BacktestResult, run_backtest
from brent_quant.metrics import summarize_result


def _split_by_days(df: pd.DataFrame, train_days: int, test_days: int, step_days: int):
    days = pd.Index(sorted({ts.tz_convert("UTC").normalize() for ts in df.index}))
    windows = []
    i = 0
    while True:
        train_start = days[i] if i < len(days) else None
        if train_start is None:
            break
        train_end_i = i + train_days - 1
        test_end_i = train_end_i + test_days
        if test_end_i >= len(days):
            break
        train_end = days[train_end_i]
        test_start = days[train_end_i + 1]
        test_end = days[test_end_i]
        windows.append((train_start, train_end, test_start, test_end))
        i += step_days
        if i >= len(days):
            break
    return windows


def walk_forward(
    df: pd.DataFrame,
    model: str = "D",
    train_days: int = 30,
    test_days: int = 10,
    step_days: int = 10,
    grid: tuple[int, ...] = cfg.BREAKOUT_GRID,
    **bt_kwargs,
) -> dict[str, Any]:
    windows = _split_by_days(df, train_days, test_days, step_days)
    oos_equity_parts: list[pd.Series] = []
    rows: list[dict[str, Any]] = []
    for train_start, train_end, test_start, test_end in windows:
        train = df[(df.index >= train_start) & (df.index <= train_end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))]
        test = df[(df.index >= test_start) & (df.index <= test_end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))]
        if len(train) < 200 or len(test) < 50:
            continue
        best_n = grid[0]
        best_sharpe = float("-inf")
        for n in grid:
            res = run_backtest(train, model=model, breakout_n=n, **bt_kwargs)
            stats = summarize_result(res)
            sharpe = stats.get("sharpe")
            score = sharpe if sharpe is not None and sharpe == sharpe else -999.0
            if score > best_sharpe:
                best_sharpe = score
                best_n = n
        oos = run_backtest(test, model=model, breakout_n=best_n, **bt_kwargs)
        stats = summarize_result(oos)
        rows.append(
            {
                "train_start": str(train_start.date()),
                "train_end": str(train_end.date()),
                "test_start": str(test_start.date()),
                "test_end": str(test_end.date()),
                "chosen_breakout_n": int(best_n),
                "train_sharpe": float(best_sharpe) if best_sharpe != float("-inf") else None,
                "oos_sharpe": stats.get("sharpe"),
                "oos_total_return": stats.get("total_return"),
                "oos_max_drawdown": stats.get("max_drawdown"),
                "oos_profit_factor": stats.get("profit_factor"),
                "oos_n_trades": stats.get("n_trades"),
            }
        )
        part = oos.daily_equity.copy()
        if oos_equity_parts:
            scale = oos_equity_parts[-1].iloc[-1] / part.iloc[0]
            part = part * scale
        oos_equity_parts.append(part)

    if oos_equity_parts:
        oos_eq = pd.concat(oos_equity_parts)
        oos_eq = oos_eq[~oos_eq.index.duplicated(keep="last")].sort_index()
        stitched = BacktestResult(
            model=f"{model}_WF",
            equity=oos_eq,
            daily_equity=oos_eq,
            positions=pd.Series(dtype=float),
            trades=pd.DataFrame(),
            bars=pd.DataFrame(),
            params={"model": model, "windows": rows},
        )
        summary = summarize_result(stitched)
    else:
        oos_eq = pd.Series(dtype=float)
        summary = {"error": "no walk-forward windows"}

    return {
        "windows": rows,
        "oos_summary": summary,
        "oos_equity": oos_eq,
    }
