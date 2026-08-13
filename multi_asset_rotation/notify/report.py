"""生成每日策略微信日报内容。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backtest import map_signal_to_exec, run_backtest
from config import CODES, PARAMS, STRATEGY_START, UNIVERSE
from data import build_panels, load_universe
from metrics import latest_rebalance_instruction, perf_stats
from strategy import generate_target_weights

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class DailyReport:
    asof: str
    title: str
    text: str
    html: str
    payload: dict[str, Any]


def _pct(x: float | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x:.{digits}%}"


def _prev_trading_day(calendar: pd.DatetimeIndex, dt: pd.Timestamp) -> pd.Timestamp | None:
    pos = calendar.get_indexer([dt], method=None)[0]
    if pos < 0:
        # nearest previous
        i = int(calendar.searchsorted(dt, side="right") - 1)
        return calendar[i] if i >= 0 else None
    return calendar[pos - 1] if pos >= 1 else None


def build_daily_report(
    force_download: bool = False,
    phone_hint: str = "",
) -> DailyReport:
    now = datetime.now(CN_TZ)
    asof_str = now.strftime("%Y-%m-%d %H:%M:%S CST")

    raw = load_universe(force=force_download)
    close, _ = build_panels(raw)
    close = close.loc[STRATEGY_START:]
    today = close.index.max()

    signal_w, info = generate_target_weights(close, PARAMS)
    nav, weights_daily, trades = run_backtest(close, signal_w)
    stats = perf_stats(nav)
    order = latest_rebalance_instruction(signal_w, weights_daily, close.index)
    meta = info["meta"]

    # 本周：最近一次“已到执行日”的信号
    exec_map = map_signal_to_exec(signal_w, close.index)
    exec_dates = sorted(exec_map.keys())
    active_exec = None
    for d in reversed(exec_dates):
        if d <= today:
            active_exec = d
            break

    week_ret = None
    week_from = None
    week_to = str(today.date())
    active_signal = None
    active_target = None
    if active_exec is not None:
        # 周一开盘调仓：用执行日前一交易日收盘净值作为起点，到今日收盘
        base = _prev_trading_day(close.index, active_exec)
        if base is not None and base in nav.index and today in nav.index:
            week_ret = float(nav.loc[today] / nav.loc[base] - 1.0)
            week_from = str(base.date())
        # 反查信号日
        for sig_dt, row in signal_w.iterrows():
            pos = close.index.get_indexer([sig_dt])[0]
            if pos >= 0 and pos + 1 < len(close.index) and close.index[pos + 1] == active_exec:
                active_signal = sig_dt
                active_target = row.reindex(CODES).fillna(0.0)
                break
        if active_target is None:
            active_target = exec_map[active_exec].reindex(CODES).fillna(0.0)

    # 今日收益
    day_ret = None
    if len(nav) >= 2:
        day_ret = float(nav.iloc[-1] / nav.iloc[-2] - 1.0)

    latest_meta = meta.iloc[-1] if len(meta) else None
    latest_sig = signal_w.index.max() if len(signal_w) else None

    # 最新目标（最新周五信号，可能尚未执行）
    latest_target = (
        signal_w.loc[latest_sig].reindex(CODES).fillna(0.0) if latest_sig is not None else None
    )

    # 数据新鲜度
    data_end = {c: str(close[c].dropna().index.max().date()) for c in CODES if c in close}

    targets_lines = []
    if latest_target is not None:
        for c in CODES:
            w = float(latest_target[c])
            if abs(w) < 1e-4:
                continue
            targets_lines.append(f"{c} {UNIVERSE[c]['name']}: {_pct(w)}")

    held_lines = []
    if active_target is not None:
        for c in CODES:
            w = float(active_target[c])
            if abs(w) < 1e-4:
                continue
            held_lines.append(f"{c} {UNIVERSE[c]['name']}: {_pct(w)}")

    regime = str(latest_meta["regime"]) if latest_meta is not None else "—"
    us_pick = str(latest_meta["us_pick"]) if latest_meta is not None else "—"
    elig = str(latest_meta["eligible"]) if latest_meta is not None else "—"
    gross = float(latest_meta["gross"]) if latest_meta is not None else None
    breadth = int(latest_meta["breadth_weak"]) if latest_meta is not None else None
    breaker = int(latest_meta["breaker"]) if latest_meta is not None else None

    phone_line = f"接收人：{phone_hint}" if phone_hint else ""

    title = f"多资产轮动日报 {today.date()}"

    lines: list[str] = [
        "【多资产轮动策略日报】",
        f"生成时间：{asof_str}",
    ]
    if phone_line:
        lines.append(phone_line)
    lines += [
        f"数据截至：{today.date()}",
        "",
        "一、策略决策",
        f"最新信号日：{latest_sig.date() if latest_sig is not None else '—'}",
        f"执行日：{order.get('exec_date')}",
        f"状态(regime)：{regime}",
        f"美股择强：{us_pick}",
        f"合格资产：{elig}",
        f"毛敞口gross：{gross:.3f}" if gross is not None else "毛敞口gross：—",
        f"金丝雀弱资产数：{breadth}",
        f"周度断路器：{'开启' if breaker else '关闭'}",
        "",
        "二、调仓 Target（最新信号）",
    ]
    lines.extend(targets_lines or ["（全债券/无风险仓）"])
    lines += [
        f"目标换手(相对信号日旧仓)：{_pct(order.get('turnover'))}",
        "",
        "三、本周整体收益",
        f"本周执行日：{active_exec.date() if active_exec is not None else '—'}",
        f"收益区间：{week_from or '—'} 收盘 → {week_to} 收盘",
        "（口径：周五信号→下交易日开盘调仓，用执行日前收盘净值为起点）",
        f"本周策略收益：{_pct(week_ret)}",
        f"今日策略收益：{_pct(day_ret)}",
        "",
        "四、组合概览",
        f"累计净值：{float(nav.iloc[-1]):.4f}",
        f"年化：{_pct(stats.get('ann_return'))}",
        f"Sharpe：{stats.get('sharpe_rf0', float('nan')):.3f}",
        f"最大回撤：{_pct(stats.get('max_drawdown'))}",
        "",
        "五、数据日期",
    ]
    for c, d in data_end.items():
        lines.append(f"{c} {UNIVERSE[c]['name']}: {d}")
    lines += ["", "六、本周持仓(已执行)"]
    lines.extend(held_lines or ["—"])
    lines += [
        "",
        "风险提示：历史回测不代表未来；QDII溢折价/汇率/融资未完全计入。",
    ]
    text = "\n".join(lines)

    # HTML for PushPlus
    def li(lines):
        if not lines:
            return "<li>—</li>"
        return "".join(f"<li>{x}</li>" for x in lines)

    html = f"""
<h3>多资产轮动策略日报</h3>
<p><b>生成</b>：{asof_str}<br/>
{"<b>" + phone_line + "</b><br/>" if phone_line else ""}
<b>数据截至</b>：{today.date()}</p>

<h3>1. 策略决策</h3>
<ul>
<li>最新信号日：{latest_sig.date() if latest_sig is not None else "—"}</li>
<li>执行日：{order.get("exec_date")}</li>
<li>状态 regime：<code>{regime}</code></li>
<li>美股择强：{us_pick}</li>
<li>合格资产：{elig}</li>
<li>毛敞口 gross：{f"{gross:.3f}" if gross is not None else "—"}</li>
<li>金丝雀弱资产数：{breadth}</li>
<li>周度断路器：{"开启" if breaker else "关闭"}</li>
</ul>

<h3>2. 调仓 Target（最新信号）</h3>
<ul>{li(targets_lines)}</ul>
<p>目标换手(相对信号日旧仓)：{_pct(order.get("turnover"))}</p>

<h3>3. 本周整体收益</h3>
<ul>
<li>本周执行日：{active_exec.date() if active_exec is not None else "—"}</li>
<li>收益区间：{week_from or "—"} 收盘 → {week_to} 收盘</li>
<li>口径：周五信号 → 下一交易日开盘调仓；执行日前收盘净值 → 今日收盘</li>
<li><b>本周策略收益：{_pct(week_ret)}</b></li>
<li>今日策略收益：{_pct(day_ret)}</li>
</ul>

<h3>4. 组合概览</h3>
<ul>
<li>累计净值：{float(nav.iloc[-1]):.4f}</li>
<li>年化：{_pct(stats.get("ann_return"))}</li>
<li>Sharpe：{stats.get("sharpe_rf0", float("nan")):.3f}</li>
<li>最大回撤：{_pct(stats.get("max_drawdown"))}</li>
</ul>

<h3>5. 数据日期</h3>
<ul>{li([f"{c} {UNIVERSE[c]['name']}: {d}" for c, d in data_end.items()])}</ul>

<h3>6. 本周持仓（已执行）</h3>
<ul>{li(held_lines)}</ul>

<p style="color:#666">风险提示：历史回测不代表未来；实盘请注意 QDII 溢折价、汇率与融资约束。</p>
"""

    payload = {
        "asof": asof_str,
        "data_asof": str(today.date()),
        "phone_hint": phone_hint,
        "signal_date": str(latest_sig.date()) if latest_sig is not None else None,
        "exec_date": order.get("exec_date"),
        "regime": regime,
        "us_pick": us_pick,
        "eligible": elig,
        "gross": gross,
        "targets": {
            c: float(latest_target[c])
            for c in CODES
            if latest_target is not None and abs(float(latest_target[c])) > 1e-4
        },
        "week_exec": str(active_exec.date()) if active_exec is not None else None,
        "week_return": week_ret,
        "day_return": day_ret,
        "nav": float(nav.iloc[-1]),
        "stats": {
            "ann_return": stats.get("ann_return"),
            "sharpe_rf0": stats.get("sharpe_rf0"),
            "max_drawdown": stats.get("max_drawdown"),
        },
        "order": order,
    }

    return DailyReport(
        asof=asof_str,
        title=title,
        text=text,
        html=html,
        payload=payload,
    )
