"""
回测引擎：周五收盘信号 -> 下一交易日调仓。
支持条件杠杆 + 日度回撤保护（默认：降杠杆到1x，并在下次再平衡时恢复）。
"""

from __future__ import annotations

from typing import Dict, Tuple

import pandas as pd

from config import CODES, PARAMS, SAFE


def map_signal_to_exec(
    signal_weights: pd.DataFrame, calendar: pd.DatetimeIndex
) -> Dict[pd.Timestamp, pd.Series]:
    pos = {d: i for i, d in enumerate(calendar)}
    exec_w: Dict[pd.Timestamp, pd.Series] = {}
    for sig_dt, row in signal_weights.iterrows():
        if sig_dt not in pos:
            continue
        i = pos[sig_dt]
        if i + 1 >= len(calendar):
            continue
        exec_dt = calendar[i + 1]
        w = row.reindex(CODES).fillna(0.0)
        if float(w.sum()) <= 0:
            w = pd.Series(0.0, index=CODES)
            w[SAFE] = 1.0
        exec_w[exec_dt] = w
    return exec_w


def run_backtest(
    close: pd.DataFrame,
    signal_weights: pd.DataFrame,
    cost_bps: float | None = None,
    borrow_rate: float | None = None,
    daily_dd_stop: float | None = None,
    daily_dd_resume: float | None = None,
    stop_only_levered: bool | None = None,
    stop_vol_mult: float | None = None,
    dd_action: str | None = None,
    resume_on_rebalance: bool | None = None,
) -> Tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    cost_bps = float(PARAMS["cost_bps"] if cost_bps is None else cost_bps)
    borrow_rate = float(
        PARAMS.get("borrow_rate", 0.03) if borrow_rate is None else borrow_rate
    )
    daily_dd_stop = float(
        PARAMS.get("daily_dd_stop", 0.99)
        if daily_dd_stop is None
        else daily_dd_stop
    )
    daily_dd_resume = float(
        PARAMS.get("daily_dd_resume", 0.02)
        if daily_dd_resume is None
        else daily_dd_resume
    )
    if stop_only_levered is None:
        stop_only_levered = bool(PARAMS.get("stop_only_levered", True))
    if stop_vol_mult is None:
        stop_vol_mult = float(PARAMS.get("stop_vol_mult", 1.0))
    if dd_action is None:
        dd_action = str(PARAMS.get("dd_action", "delever"))
    if resume_on_rebalance is None:
        resume_on_rebalance = bool(PARAMS.get("resume_on_rebalance", True))

    calendar = close.index
    daily_ret = close.pct_change().fillna(0.0)
    exec_w = map_signal_to_exec(signal_weights, calendar)

    cur = pd.Series(0.0, index=CODES)
    cur[SAFE] = 1.0
    target_gross = 1.0
    desired = cur.copy()
    equity = 1.0
    peak = 1.0
    stopped = False
    recent_rets = []
    nav_list = []
    w_rows = []
    trades = []

    for i, dt in enumerate(calendar):
        if dt in exec_w:
            desired = exec_w[dt].reindex(CODES).fillna(0.0)
            gs = float(desired.sum())
            if gs <= 0:
                desired = cur * 0
                desired[SAFE] = 1.0
                gs = 1.0
            # 默认：下次再平衡强制恢复，避免长期锁死在债券/降杠杆状态
            allow_rebalance = (not stopped) or resume_on_rebalance
            if allow_rebalance:
                turn = float((desired - cur).abs().sum()) / 2.0
                equity *= 1.0 - turn * cost_bps / 10000.0
                trades.append(
                    {
                        "exec_date": dt,
                        "turnover": turn,
                        "cost": turn * cost_bps / 10000.0,
                        "gross": gs,
                        "event": "rebalance",
                        **{c: float(desired[c]) for c in CODES},
                    }
                )
                cur = desired.copy()
                target_gross = gs
                stopped = False

        if i > 0:
            r = daily_ret.loc[dt]
            avail = close.loc[dt].notna()
            w = cur.copy()
            w[~avail] = 0.0
            s = float(w.sum())
            if s <= 0:
                w = cur * 0
                w[SAFE] = 1.0
                s = 1.0

            if target_gross <= 1.0 + 1e-9:
                w = w / s
                day_ret = float((w * r.fillna(0.0)).sum())
            else:
                w = w / s * target_gross
                day_ret = float((w * r.fillna(0.0)).sum())
                day_ret -= (target_gross - 1.0) * borrow_rate / 252.0

            equity *= 1.0 + day_ret
            cur = w
            peak = max(peak, equity)
            dd = equity / peak - 1.0
            recent_rets.append(day_ret)
            if len(recent_rets) > 20:
                recent_rets = recent_rets[-20:]
            vol20 = (
                float(pd.Series(recent_rets).std() * (252 ** 0.5))
                if len(recent_rets) > 5
                else 0.1
            )
            # stop_vol_mult:
            #   >0 时：高波动收紧止损（更利于控制MDD）
            #   =0 时：固定阈值
            if stop_vol_mult <= 0:
                dyn_stop = abs(daily_dd_stop)
            else:
                dyn_stop = abs(daily_dd_stop) / max(1.0, stop_vol_mult * vol20 / 0.10)

            apply_stop = True
            if stop_only_levered and target_gross <= 1.0 + 1e-9:
                apply_stop = False

            if apply_stop and (not stopped) and dd <= -dyn_stop:
                if dd_action == "bonds":
                    new_w = cur * 0
                    new_w[SAFE] = 1.0
                    event = "daily_dd_stop_bonds"
                else:
                    # delever: 保持相对结构，毛敞口降到 1x
                    ss = float(cur.sum())
                    new_w = (cur / ss) if ss > 0 else cur * 0
                    if float(new_w.sum()) <= 0:
                        new_w[SAFE] = 1.0
                    event = "daily_dd_delever"
                turn = float((new_w - cur).abs().sum()) / 2.0
                equity *= 1.0 - turn * cost_bps / 10000.0
                trades.append(
                    {
                        "exec_date": dt,
                        "turnover": turn,
                        "cost": turn * cost_bps / 10000.0,
                        "gross": float(new_w.sum()),
                        "event": event,
                        **{c: float(new_w[c]) for c in CODES},
                    }
                )
                cur = new_w
                target_gross = float(new_w.sum())
                stopped = True
            elif (
                stopped
                and (not resume_on_rebalance)
                and dd >= -abs(daily_dd_resume)
            ):
                gs = float(desired.sum()) if float(desired.sum()) > 0 else 1.0
                turn = float((desired - cur).abs().sum()) / 2.0
                equity *= 1.0 - turn * cost_bps / 10000.0
                trades.append(
                    {
                        "exec_date": dt,
                        "turnover": turn,
                        "cost": turn * cost_bps / 10000.0,
                        "gross": gs,
                        "event": "daily_dd_resume",
                        **{c: float(desired[c]) for c in CODES},
                    }
                )
                cur = desired.copy()
                target_gross = gs
                stopped = False

        nav_list.append(equity)
        w_rows.append(cur.copy())

    nav = pd.Series(nav_list, index=calendar, name="nav")
    weights_daily = pd.DataFrame(w_rows, index=calendar)
    trade_df = pd.DataFrame(trades).set_index("exec_date") if trades else pd.DataFrame()
    return nav, weights_daily, trade_df


def equal_weight_benchmark(close: pd.DataFrame) -> pd.Series:
    ret = close.pct_change()
    mask = ret.notna()
    w = mask.astype(float)
    w = w.div(w.sum(axis=1), axis=0)
    port = (w.shift(1) * ret).sum(axis=1).fillna(0.0)
    return (1.0 + port).cumprod().rename("equal_weight")


def buy_hold_asset(close: pd.DataFrame, code: str) -> pd.Series:
    s = close[code].dropna()
    return (s / s.iloc[0]).rename(code)
