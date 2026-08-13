"""生成每日策略微信日报内容（美化版 + YTD + 本周生效持仓对齐）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from backtest import run_backtest
from config import CODES, PARAMS, STRATEGY_START, UNIVERSE
from data import build_panels, load_universe
from metrics import perf_stats
from strategy import generate_target_weights

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
OUT.mkdir(parents=True, exist_ok=True)


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


def _signed_pct(x: float | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.{digits}%}"


def _color(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "#666"
    if x > 0:
        return "#d93838"
    if x < 0:
        return "#1f9d55"
    return "#666"


def _prev_trading_day(calendar: pd.DatetimeIndex, dt: pd.Timestamp) -> pd.Timestamp | None:
    i = int(calendar.searchsorted(dt, side="left"))
    if i < 0:
        return None
    # if dt in calendar, previous; if not, searchsorted gives insertion point
    if i < len(calendar) and calendar[i] == dt:
        return calendar[i - 1] if i >= 1 else None
    j = i - 1
    return calendar[j] if j >= 0 else None


def _planned_exec(sig_dt: pd.Timestamp, calendar: pd.DatetimeIndex) -> pd.Timestamp:
    """周五信号 -> 下一交易日执行；若样本尚未含下一交易日，用工作日推算。"""
    pos = calendar.get_indexer([sig_dt])[0]
    if pos >= 0 and pos + 1 < len(calendar):
        return pd.Timestamp(calendar[pos + 1])
    # 数据截断在信号日：按工作日推下一个交易日（忽略法定节假日近似）
    nxt = pd.bdate_range(sig_dt + pd.Timedelta(days=1), periods=1)[0]
    return pd.Timestamp(nxt)


def _weight_lines(w: pd.Series | None) -> list[str]:
    if w is None:
        return []
    rows = []
    for c in CODES:
        v = float(w.get(c, 0.0))
        if abs(v) < 1e-4:
            continue
        rows.append((c, UNIVERSE[c]["name"], v))
    rows.sort(key=lambda x: -abs(x[2]))
    return [f"{c} {name}: {_pct(v)}" for c, name, v in rows]


def _weight_bars_html(w: pd.Series | None) -> str:
    if w is None:
        return "<p>—</p>"
    items = []
    for c in CODES:
        v = float(w.get(c, 0.0))
        if abs(v) < 1e-4:
            continue
        items.append((c, UNIVERSE[c]["name"], v))
    items.sort(key=lambda x: -abs(x[2]))
    if not items:
        return "<p>100% 债券/空仓</p>"
    # scale bar by max abs weight
    max_abs = max(abs(v) for _, _, v in items) or 1.0
    chunks = []
    for c, name, v in items:
        width = min(100.0, abs(v) / max_abs * 100.0)
        col = "#3b82f6" if v >= 0 else "#f59e0b"
        chunks.append(
            f"""
<div style="margin:8px 0;">
  <div style="font-size:13px;color:#333;">{c} {name} <b style="float:right;color:{col}">{_pct(v)}</b></div>
  <div style="background:#eef2ff;border-radius:6px;height:10px;overflow:hidden;">
    <div style="width:{width:.1f}%;background:{col};height:10px;border-radius:6px;"></div>
  </div>
</div>"""
        )
    return "".join(chunks)


def _ytd_stats(nav: pd.Series, year: int) -> dict[str, float]:
    ynav = nav[nav.index.year == year]
    if len(ynav) < 2:
        # start from last point before year if needed
        pre = nav[nav.index.year < year]
        if len(pre) and len(ynav):
            base = float(pre.iloc[-1])
            ytd = float(ynav.iloc[-1] / base - 1.0)
        else:
            return {}
    else:
        # YTD: from last close of previous year if available, else first point in year
        pre = nav[nav.index.year < year]
        base = float(pre.iloc[-1]) if len(pre) else float(ynav.iloc[0])
        ytd = float(ynav.iloc[-1] / base - 1.0)

    # rebuild path from base for vol/mdd within YTD window
    if len(pre):
        path = pd.concat([pre.iloc[-1:], ynav])
        path = path / float(path.iloc[0])
    else:
        path = ynav / float(ynav.iloc[0])
    ret = path.pct_change().dropna()
    vol = float(ret.std() * np.sqrt(252)) if len(ret) else float("nan")
    mdd = float((path / path.cummax() - 1).min()) if len(path) else float("nan")
    sharpe = (ytd / vol) if vol and vol > 0 else float("nan")  # rough non-annualized for short YTD use ann
    # annualize ytd for sharpe-like: use ann return over ytd period
    n = max(len(ret), 1)
    ann = float(path.iloc[-1] ** (252 / n) - 1) if n > 5 else ytd
    sharpe_ann = (ann / vol) if vol and vol > 0 else float("nan")
    return {
        "ytd_return": ytd,
        "ann_return": ann,
        "ann_vol": vol,
        "max_drawdown": mdd,
        "sharpe": sharpe_ann,
        "nav_start": float(base) if len(pre) else float(ynav.iloc[0]),
        "nav_end": float(ynav.iloc[-1]),
    }


def _monthly_ytd(nav: pd.Series, year: int) -> pd.Series:
    ynav = nav[nav.index.year >= year - 0]  # keep helper simple
    # month ends within year
    ret = nav.pct_change().fillna(0.0)
    m = ret.groupby([ret.index.year, ret.index.month]).apply(lambda x: (1 + x).prod() - 1)
    out = {}
    for (y, mo), v in m.items():
        if y == year:
            out[f"{mo:02d}月"] = float(v)
    return pd.Series(out)


def _save_ytd_chart(nav: pd.Series, year: int, path) -> str:
    pre = nav[nav.index.year < year]
    ynav = nav[nav.index.year == year]
    if len(ynav) < 2:
        return ""
    if len(pre):
        series = pd.concat([pre.iloc[-1:], ynav])
        series = series / float(series.iloc[0])
        series = series.iloc[1:]  # start year at ~1.0 from first year day relative to prev close
        # Better: normalize so first day of year equals nav_year_start/prev_close
        base = float(pre.iloc[-1])
        series = ynav / base
    else:
        series = ynav / float(ynav.iloc[0])

    fig, ax = plt.subplots(figsize=(7.2, 3.2), dpi=140)
    ax.plot(series.index, series.values, color="#2563eb", lw=2.0, label="Strategy YTD NAV")
    ax.fill_between(series.index, series.values, series.values.min() * 0.98, color="#2563eb", alpha=0.12)
    ax.axhline(1.0, color="#94a3b8", lw=1, ls="--")
    ax.set_title(f"{year} YTD Strategy NAV (rebased)", fontsize=11)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(path)


def _month_bars_html(monthly: pd.Series) -> str:
    if monthly.empty:
        return "<p>—</p>"
    chunks = []
    max_abs = max(0.01, float(monthly.abs().max()))
    for name, v in monthly.items():
        width = abs(float(v)) / max_abs * 100
        col = _color(float(v))
        chunks.append(
            f"""
<div style="display:flex;align-items:center;margin:4px 0;font-size:12px;">
  <div style="width:42px;color:#555;">{name}</div>
  <div style="flex:1;background:#f1f5f9;border-radius:4px;height:8px;margin:0 8px;">
    <div style="width:{width:.1f}%;background:{col};height:8px;border-radius:4px;"></div>
  </div>
  <div style="width:64px;text-align:right;color:{col};font-weight:600;">{_signed_pct(float(v))}</div>
</div>"""
        )
    return "".join(chunks)


def build_daily_report(
    force_download: bool = False,
    phone_hint: str = "",
) -> DailyReport:
    now = datetime.now(CN_TZ)
    asof_str = now.strftime("%Y-%m-%d %H:%M:%S CST")
    report_day = pd.Timestamp(now.date())

    raw = load_universe(force=force_download)
    close, _ = build_panels(raw)
    close = close.loc[STRATEGY_START:]
    data_asof = pd.Timestamp(close.index.max().date())

    signal_w, info = generate_target_weights(close, PARAMS)
    nav, weights_daily, trades = run_backtest(close, signal_w)
    stats = perf_stats(nav)
    meta = info["meta"]

    # ---- 信号/执行对齐 ----
    # week_ends() 在“本周数据尚未到周五”时会把周三/四当成周末信号，这不是真实再平衡日。
    # 规则：仅把“该 ISO 周最后一个交易日，且已走完到周五（或当天已是周五及以后）”的点视为有效周信号。
    def _is_actionable_week_signal(sig_dt: pd.Timestamp) -> bool:
        sig_dt = pd.Timestamp(sig_dt)
        week = sig_dt.strftime("%Y-%W")
        same = [pd.Timestamp(d) for d in close.index if pd.Timestamp(d).strftime("%Y-%W") == week]
        if not same:
            return False
        week_last = max(same)
        if sig_dt.normalize() != week_last.normalize():
            return False
        # 当前周且还没到周五：数据截断产生的伪信号，忽略
        report_week = report_day.strftime("%Y-%W")
        if week == report_week and int(report_day.dayofweek) < 4 and int(sig_dt.dayofweek) < 4:
            return False
        # 历史周：若该周最后一个交易日不是周五，但确实是假期缩短周，则接受
        return True

    sig_rows = []
    for sig_dt, row in signal_w.iterrows():
        if not _is_actionable_week_signal(sig_dt):
            continue
        exec_dt = _planned_exec(pd.Timestamp(sig_dt), close.index)
        sig_rows.append(
            {
                "signal": pd.Timestamp(sig_dt),
                "exec": exec_dt,
                "weights": row.reindex(CODES).fillna(0.0),
            }
        )

    # 本周生效：执行日 <= 报告日的最近一条周信号；整周适用直到下次执行
    effective = None
    for r in reversed(sig_rows):
        if r["exec"].normalize() <= report_day.normalize():
            effective = r
            break
    pending = None
    latest = sig_rows[-1] if sig_rows else None
    if latest is not None and latest["exec"].normalize() > report_day.normalize():
        pending = latest
    if effective is None and latest is not None:
        effective = latest

    # 本周收益：生效执行日前收盘 -> min(数据日, 今天)
    week_ret = None
    week_from = None
    week_to = str(min(data_asof, report_day).date())
    week_note = ""
    if effective is not None:
        base = _prev_trading_day(close.index, effective["exec"])
        # 若 exec 不在样本内（推算的周一），用信号日收盘作起点
        if base is None or base not in nav.index:
            if effective["signal"] in nav.index:
                base = effective["signal"]
                week_note = "执行日行情尚未入库，起点暂用信号日收盘净值"
        end_pt = min(data_asof, report_day)
        # map end_pt to available nav date
        if end_pt not in nav.index:
            end_pt = data_asof
        if base is not None and base in nav.index and end_pt in nav.index:
            week_ret = float(nav.loc[end_pt] / nav.loc[base] - 1.0)
            week_from = str(base.date())
        if data_asof < report_day:
            week_note = (
                (week_note + "；" if week_note else "")
                + f"行情数据截至 {data_asof.date()}，本周收益为截至该日的收益"
            )

    day_ret = None
    if len(nav) >= 2:
        day_ret = float(nav.iloc[-1] / nav.iloc[-2] - 1.0)

    # meta for effective signal if available else latest
    meta_row = None
    if effective is not None and effective["signal"] in meta.index:
        meta_row = meta.loc[effective["signal"]]
    elif len(meta):
        meta_row = meta.iloc[-1]

    regime = str(meta_row["regime"]) if meta_row is not None else "—"
    us_pick = str(meta_row["us_pick"]) if meta_row is not None else "—"
    elig = str(meta_row["eligible"]) if meta_row is not None else "—"
    gross = float(meta_row["gross"]) if meta_row is not None else None
    breadth = int(meta_row["breadth_weak"]) if meta_row is not None else None
    breaker = int(meta_row["breaker"]) if meta_row is not None else None

    eff_w = effective["weights"] if effective else None
    pending_w = pending["weights"] if pending else None

    # YTD
    year = int(report_day.year)
    ytd = _ytd_stats(nav, year)
    monthly = _monthly_ytd(nav, year)
    chart_path = OUT / f"ytd_nav_{year}.png"
    _save_ytd_chart(nav, year, chart_path)

    # ASCII sparkline for text channel
    ynav = nav[nav.index.year == year]
    spark = ""
    if len(ynav) >= 2:
        pre = nav[nav.index.year < year]
        base = float(pre.iloc[-1]) if len(pre) else float(ynav.iloc[0])
        s = (ynav / base).values
        # downsample
        idx = np.linspace(0, len(s) - 1, min(28, len(s))).astype(int)
        pts = s[idx]
        lo, hi = float(pts.min()), float(pts.max())
        chars = "▁▂▃▄▅▆▇█"
        spark = "".join(
            chars[int((p - lo) / (hi - lo + 1e-12) * (len(chars) - 1))] for p in pts
        )

    data_end = {c: str(close[c].dropna().index.max().date()) for c in CODES if c in close}
    phone_line = f"接收人：{phone_hint}" if phone_hint else ""

    status_line = (
        f"本周生效仓：信号 {effective['signal'].date()} → 执行 {effective['exec'].date()}"
        if effective
        else "本周生效仓：—"
    )
    if pending:
        status_line += f"；另有待执行信号 {pending['signal'].date()}（执行 {pending['exec'].date()}）"

    title = f"📈 多资产轮动日报 {report_day.date()}"

    # -------- TEXT --------
    lines = [
        "【多资产轮动策略日报】",
        f"生成：{asof_str}",
    ]
    if phone_line:
        lines.append(phone_line)
    lines += [
        f"报告日：{report_day.date()}（数据截至 {data_asof.date()}）",
        status_line,
        "",
        "一、策略决策（本周生效）",
        f"信号日：{effective['signal'].date() if effective else '—'}",
        f"执行日：{effective['exec'].date() if effective else '—'}",
        f"适用说明：该目标自执行日起生效，直至下一次再平衡",
        f"状态：{regime}",
        f"美股择强：{us_pick}",
        f"合格资产：{elig}",
        f"毛敞口：{gross:.3f}" if gross is not None else "毛敞口：—",
        f"金丝雀弱个数：{breadth}｜断路器：{'开' if breaker else '关'}",
        "",
        "二、本周持仓 Target（= 生效目标）",
    ]
    lines.extend(_weight_lines(eff_w) or ["（全债券/空仓）"])
    if pending_w is not None:
        lines += ["", "（待执行信号目标，尚未到执行日）"]
        lines.extend(_weight_lines(pending_w))
    lines += [
        "",
        "三、本周整体收益",
        f"收益区间：{week_from or '—'} → {week_to}",
        f"本周收益：{_signed_pct(week_ret)}",
        f"今日收益：{_signed_pct(day_ret)}",
    ]
    if week_note:
        lines.append(f"备注：{week_note}")
    lines += [
        "",
        f"四、{year} 年初至今（YTD）",
        f"YTD收益：{_signed_pct(ytd.get('ytd_return'))}",
        f"区间年化：{_signed_pct(ytd.get('ann_return'))}",
        f"波动：{_pct(ytd.get('ann_vol'))}",
        f"回撤：{_pct(ytd.get('max_drawdown'))}",
        f"Sharpe：{ytd.get('sharpe', float('nan')):.2f}" if ytd else "Sharpe：—",
        f"曲线：{spark}" if spark else "",
        "",
        "五、全样本概览",
        f"净值：{float(nav.iloc[-1]):.4f}",
        f"年化：{_pct(stats.get('ann_return'))}｜Sharpe：{stats.get('sharpe_rf0', float('nan')):.3f}",
        f"MDD：{_pct(stats.get('max_drawdown'))}",
        "",
        "六、数据日期",
    ]
    for c, d in data_end.items():
        lines.append(f"{c} {UNIVERSE[c]['name']}:{d}")
    lines += ["", "风险提示：历史不代表未来；QDII溢折价/汇率/融资未完全计入。"]
    text = "\n".join([x for x in lines if x is not None])

    # -------- HTML (beautiful card) --------
    ytd_ret = ytd.get("ytd_return") if ytd else None
    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;background:#0b1220;color:#e5eefc;padding:16px;border-radius:14px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div>
      <div style="font-size:12px;color:#93c5fd;letter-spacing:1px;">MULTI-ASSET ROTATION</div>
      <div style="font-size:22px;font-weight:760;margin-top:4px;">每日策略简报</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:6px;">{asof_str}</div>
      {"<div style='font-size:12px;color:#94a3b8;'>" + phone_line + "</div>" if phone_line else ""}
    </div>
    <div style="text-align:right;">
      <div style="font-size:12px;color:#94a3b8;">本周收益</div>
      <div style="font-size:26px;font-weight:800;color:{_color(week_ret)};">{_signed_pct(week_ret)}</div>
      <div style="font-size:12px;color:#94a3b8;">今日 {_signed_pct(day_ret)}</div>
    </div>
  </div>

  <div style="margin-top:14px;padding:12px;border-radius:12px;background:linear-gradient(135deg,#1e293b,#111827);border:1px solid #334155;">
    <div style="font-size:13px;color:#93c5fd;margin-bottom:6px;">本周生效说明</div>
    <div style="font-size:14px;line-height:1.55;">
      信号 <b>{effective['signal'].date() if effective else '—'}</b>
      → 执行 <b>{effective['exec'].date() if effective else '—'}</b><br/>
      <span style="color:#cbd5e1;">自执行日起整周适用，直到下一次再平衡。
      报告日 {report_day.date()}，行情截至 {data_asof.date()}。</span>
      {f"<br/><span style='color:#fbbf24;'>{week_note}</span>" if week_note else ""}
    </div>
  </div>

  <div style="display:flex;gap:10px;margin-top:12px;">
    <div style="flex:1;background:#111827;border:1px solid #334155;border-radius:12px;padding:10px;">
      <div style="font-size:12px;color:#94a3b8;">{year} YTD</div>
      <div style="font-size:22px;font-weight:800;color:{_color(ytd_ret)};">{_signed_pct(ytd_ret)}</div>
    </div>
    <div style="flex:1;background:#111827;border:1px solid #334155;border-radius:12px;padding:10px;">
      <div style="font-size:12px;color:#94a3b8;">Sharpe / MDD</div>
      <div style="font-size:16px;font-weight:700;margin-top:4px;">
        {(f"{ytd.get('sharpe'):.2f}" if ytd and np.isfinite(ytd.get('sharpe', np.nan)) else "—")}
        <span style="color:#94a3b8;font-size:12px;"> / </span>
        <span style="color:{_color(ytd.get('max_drawdown') if ytd else None)};">{_pct(ytd.get('max_drawdown') if ytd else None)}</span>
      </div>
    </div>
    <div style="flex:1;background:#111827;border:1px solid #334155;border-radius:12px;padding:10px;">
      <div style="font-size:12px;color:#94a3b8;">全样本年化</div>
      <div style="font-size:18px;font-weight:750;color:#60a5fa;">{_pct(stats.get('ann_return'))}</div>
    </div>
  </div>

  <div style="margin-top:14px;background:#f8fafc;color:#0f172a;border-radius:12px;padding:12px;">
    <div style="font-size:15px;font-weight:750;margin-bottom:8px;">① 策略决策</div>
    <div style="font-size:13px;line-height:1.7;color:#334155;">
      状态 <code style="background:#e2e8f0;padding:1px 6px;border-radius:4px;">{regime}</code>
      ｜美股择强 <b>{us_pick}</b><br/>
      合格：{elig}<br/>
      毛敞口 <b>{f"{gross:.3f}" if gross is not None else "—"}</b>
      ｜金丝雀弱 {breadth} ｜断路器 {"开" if breaker else "关"}
    </div>
  </div>

  <div style="margin-top:12px;background:#f8fafc;color:#0f172a;border-radius:12px;padding:12px;">
    <div style="font-size:15px;font-weight:750;margin-bottom:4px;">② 本周持仓 Target</div>
    <div style="font-size:12px;color:#64748b;margin-bottom:8px;">与“本周生效仓”一致（不再单独展示冲突旧仓）</div>
    {_weight_bars_html(eff_w)}
  </div>

  <div style="margin-top:12px;background:#f8fafc;color:#0f172a;border-radius:12px;padding:12px;">
    <div style="font-size:15px;font-weight:750;margin-bottom:8px;">③ {year} 年初至今曲线与分月</div>
    <div style="font-size:12px;color:#64748b;margin-bottom:6px;">NAV 火花线：<span style="font-size:16px;letter-spacing:1px;">{spark or "—"}</span></div>
    <div style="font-size:13px;margin-bottom:8px;">
      YTD <b style="color:{_color(ytd_ret)};">{_signed_pct(ytd_ret)}</b>
      ｜波动 {_pct(ytd.get('ann_vol') if ytd else None)}
      ｜回撤 <span style="color:{_color(ytd.get('max_drawdown') if ytd else None)};">{_pct(ytd.get('max_drawdown') if ytd else None)}</span>
      ｜Sharpe {(f"{ytd.get('sharpe'):.2f}" if ytd and np.isfinite(ytd.get('sharpe', np.nan)) else "—")}
    </div>
    {_month_bars_html(monthly)}
    <div style="font-size:11px;color:#94a3b8;margin-top:8px;">完整曲线图已生成：output/{chart_path.name}</div>
  </div>

  <div style="margin-top:12px;background:#f8fafc;color:#0f172a;border-radius:12px;padding:12px;">
    <div style="font-size:15px;font-weight:750;margin-bottom:8px;">④ 全样本业绩</div>
    <div style="font-size:13px;color:#334155;line-height:1.7;">
      净值 <b>{float(nav.iloc[-1]):.4f}</b>｜年化 <b>{_pct(stats.get('ann_return'))}</b><br/>
      Sharpe <b>{stats.get('sharpe_rf0', float('nan')):.3f}</b>｜MDD <b style="color:{_color(stats.get('max_drawdown'))};">{_pct(stats.get('max_drawdown'))}</b>
    </div>
  </div>

  <div style="margin-top:12px;background:#111827;border-radius:12px;padding:12px;border:1px solid #334155;">
    <div style="font-size:13px;color:#93c5fd;margin-bottom:6px;">⑤ 数据日期</div>
    <div style="font-size:12px;color:#cbd5e1;line-height:1.65;">
      {"<br/>".join(f"{c} {UNIVERSE[c]['name']}: {d}" for c,d in data_end.items())}
    </div>
  </div>

  <div style="margin-top:12px;font-size:11px;color:#94a3b8;line-height:1.5;">
    风险提示：历史回测不代表未来；实盘需考虑 QDII 溢折价、汇率与融资约束。
  </div>
</div>
"""

    payload = {
        "asof": asof_str,
        "report_day": str(report_day.date()),
        "data_asof": str(data_asof.date()),
        "effective_signal": str(effective["signal"].date()) if effective else None,
        "effective_exec": str(effective["exec"].date()) if effective else None,
        "pending_signal": str(pending["signal"].date()) if pending else None,
        "week_note": week_note,
        "week_return": week_ret,
        "day_return": day_ret,
        "targets": {
            c: float(eff_w[c]) for c in CODES if eff_w is not None and abs(float(eff_w[c])) > 1e-4
        },
        "ytd": ytd,
        "monthly_ytd": monthly.to_dict(),
        "stats": stats,
        "regime": regime,
        "us_pick": us_pick,
        "eligible": elig,
        "gross": gross,
        "chart": str(chart_path),
    }

    return DailyReport(asof=asof_str, title=title, text=text, html=html, payload=payload)
