"""生成每日策略微信日报内容（美化版 + YTD + 本周生效持仓对齐）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from backtest import run_backtest
from config import CODES, PARAMS, STRATEGY_START, UNIVERSE
from data import build_panels, last_raw_dates, load_universe, stale_rebalance_codes
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


def _ytd_cumret_pct(nav: pd.Series, year: int) -> pd.Series:
    """年初至今累计收益率（%），用于折线图。"""
    pre = nav[nav.index.year < year]
    ynav = nav[nav.index.year == year]
    if len(ynav) < 2:
        return pd.Series(dtype=float)
    base = float(pre.iloc[-1]) if len(pre) else float(ynav.iloc[0])
    if base == 0:
        return pd.Series(dtype=float)
    return (ynav / base - 1.0) * 100.0


def _save_ytd_chart(nav: pd.Series, year: int, path) -> str:
    """生成高清累计收益率折线图 PNG（落盘 + 可供上传图床）。"""
    series = _ytd_cumret_pct(nav, year)
    if len(series) < 2:
        return ""

    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Heiti SC",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "DejaVu Sans",
        "Arial Unicode MS",
        "sans-serif",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(8.4, 4.2), dpi=180)
    ax.plot(series.index, series.values, color="#1d4ed8", lw=2.4, solid_capstyle="round")
    ax.fill_between(series.index, series.values, 0.0, color="#3b82f6", alpha=0.12)
    ax.axhline(0.0, color="#94a3b8", lw=1.0, ls="--")
    last_x, last_y = series.index[-1], float(series.iloc[-1])
    ax.scatter([last_x], [last_y], color="#dc2626", s=36, zorder=5)
    ax.annotate(
        f"{last_y:+.2f}%",
        xy=(last_x, last_y),
        xytext=(-8, 12),
        textcoords="offset points",
        ha="right",
        fontsize=11,
        fontweight="bold",
        color="#dc2626" if last_y < 0 else "#15803d",
    )
    ax.set_title(f"{year}年策略累计收益率（折线）", fontsize=14, pad=10)
    ax.set_ylabel("累计收益 (%)", fontsize=11)
    ax.grid(True, alpha=0.28, linestyle=":")
    ax.tick_params(axis="both", labelsize=9)
    fig.autofmt_xdate(rotation=20, ha="right")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white", edgecolor="none")
    plt.close(fig)
    return str(path)


def _downsample_series(series: pd.Series, max_points: int = 72) -> pd.Series:
    if len(series) <= max_points:
        return series
    idx = np.linspace(0, len(series) - 1, max_points).astype(int)
    idx = sorted(set(idx.tolist() + [len(series) - 1]))
    return series.iloc[idx]


def _quickchart_url(series: pd.Series, year: int, ytd_ret: float | None) -> str:
    """通过 QuickChart 生成可公开访问的 HTTPS 折线图 URL（PushPlus 可嵌 img）。"""
    import json
    import urllib.error
    import urllib.request

    s = _downsample_series(series, 64)
    if len(s) < 2:
        return ""
    labels = [pd.Timestamp(t).strftime("%m/%d") for t in s.index]
    values = [round(float(v), 3) for v in s.values]
    ytd_txt = _signed_pct(ytd_ret) if ytd_ret is not None else ""
    chart = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "累计收益率%",
                    "data": values,
                    "fill": True,
                    "borderColor": "#1d4ed8",
                    "backgroundColor": "rgba(59,130,246,0.15)",
                    "borderWidth": 3,
                    "pointRadius": 0,
                    "pointHoverRadius": 3,
                    "tension": 0.12,
                }
            ],
        },
        "options": {
            "plugins": {
                "title": {
                    "display": True,
                    "text": f"{year}年策略累计收益率 {ytd_txt}".strip(),
                    "font": {"size": 16, "weight": "bold"},
                    "color": "#0f172a",
                },
                "legend": {"display": False},
                "tooltip": {"callbacks": {}},
            },
            "layout": {"padding": {"top": 8, "right": 12, "bottom": 4, "left": 4}},
            "scales": {
                "x": {
                    "ticks": {"maxTicksLimit": 8, "color": "#475569", "font": {"size": 11}},
                    "grid": {"display": False},
                },
                "y": {
                    "ticks": {"color": "#475569", "font": {"size": 11}},
                    "grid": {"color": "#e2e8f0"},
                    "title": {"display": True, "text": "累计收益 (%)", "color": "#334155"},
                },
            },
        },
    }
    payload = {
        "chart": chart,
        "width": 760,
        "height": 380,
        "backgroundColor": "#ffffff",
        "devicePixelRatio": 2.0,
        "format": "png",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://quickchart.io/chart/create",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        url = str(body.get("url") or "")
        if url.startswith("http://"):
            url = "https://" + url[len("http://") :]
        return url if url.startswith("https://") else ""
    except Exception as e:  # noqa: BLE001
        print(f"[warn] quickchart create failed: {e}")
        return ""


def _upload_png_tmp_host(path) -> str:
    """备用：把本地 PNG 传到临时图床，拿 HTTPS 链接。"""
    import json
    import mimetypes
    import uuid
    import urllib.error
    import urllib.request
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return ""
    boundary = f"----cursor{uuid.uuid4().hex}"
    file_bytes = p.read_bytes()
    filename = p.name
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mimetypes.guess_type(filename)[0] or 'image/png'}\r\n\r\n"
    ).encode()
    body += file_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    # tmpfiles.org
    req = urllib.request.Request(
        "https://tmpfiles.org/api/v1/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        url = (((raw or {}).get("data") or {}).get("url")) or ""
        # tmpfiles returns http://tmpfiles.org/xxx/file -> direct link needs /dl/
        if url.startswith("http://"):
            url = "https://" + url[len("http://") :]
        if "tmpfiles.org/" in url and "/dl/" not in url:
            url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
        return url if url.startswith("https://") else ""
    except Exception as e:  # noqa: BLE001
        print(f"[warn] tmpfiles upload failed: {e}")
        return ""


def _publish_ytd_chart_url(nav: pd.Series, year: int, ytd_ret: float | None, png_path: str) -> str:
    series = _ytd_cumret_pct(nav, year)
    url = _quickchart_url(series, year, ytd_ret)
    if url:
        return url
    if png_path:
        return _upload_png_tmp_host(png_path)
    return ""


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
    # 允许 REPORT_DAY=YYYY-MM-DD 覆盖（便于测试周五文案）
    report_day_env = (os.environ.get("REPORT_DAY") or "").strip()
    report_day = pd.Timestamp(report_day_env) if report_day_env else pd.Timestamp(now.date())

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
        cutoff = min(report_day, data_asof).normalize()
        # 当前 ISO 周内、非周五：数据截断把周四/周三当成 week_end 的伪信号
        sig_week = sig_dt.strftime("%Y-%W")
        cutoff_week = cutoff.strftime("%Y-%W")
        if sig_week == cutoff_week and int(sig_dt.dayofweek) != 4:
            return False
        if int(sig_dt.dayofweek) != 4 and sig_week >= cutoff_week:
            return False
        # 历史缩短交易周（末交易日非周五且该周已结束）可接受
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

    # 本周生效：执行日 <= min(报告日, 数据日)；避免凌晨/截断数据把「尚未执行」的下交易日误当已生效
    effective_cutoff = min(report_day, data_asof).normalize()
    effective = None
    for r in reversed(sig_rows):
        if r["exec"].normalize() <= effective_cutoff:
            effective = r
            break
    pending = None
    latest = sig_rows[-1] if sig_rows else None
    if latest is not None and latest["exec"].normalize() > report_day.normalize():
        pending = latest
    if effective is None and latest is not None:
        effective = latest

    # 报告截止交易日（报告日与数据日取早）
    end_pt = min(data_asof, report_day.normalize())
    if end_pt not in nav.index:
        avail_end = nav.index[nav.index <= end_pt]
        end_pt = pd.Timestamp(avail_end.max()) if len(avail_end) else data_asof

    # 今日盈亏：报告截止日相对前一交易日
    day_ret = None
    if end_pt in nav.index:
        prev = _prev_trading_day(nav.index, end_pt)
        if prev is not None and prev in nav.index:
            day_ret = float(nav.loc[end_pt] / nav.loc[prev] - 1.0)

    # 本周至今盈亏：自然周周一前收盘 → 报告截止日收盘（含周一当日涨跌）
    week_ret = None
    week_from = None
    week_to = str(end_pt.date())
    week_note = ""
    week_start = report_day.normalize() - pd.Timedelta(days=int(report_day.dayofweek))
    base = _prev_trading_day(nav.index, week_start)
    if base is None:
        # 回退：本周首个交易日净值作起点（不含当日则为0）
        in_week = nav.index[(nav.index >= week_start) & (nav.index <= end_pt)]
        if len(in_week) >= 2:
            base = pd.Timestamp(in_week[0])
            week_ret = float(nav.loc[end_pt] / nav.loc[base] - 1.0)
            week_from = str(base.date())
            week_note = "本周起点暂用周内首个交易日收盘"
        elif len(in_week) == 1 and day_ret is not None:
            week_ret = day_ret
            week_from = str(in_week[0].date())
    elif base in nav.index and end_pt in nav.index:
        week_ret = float(nav.loc[end_pt] / nav.loc[base] - 1.0)
        week_from = str(base.date())
    if data_asof < report_day.normalize():
        week_note = (
            (week_note + "；" if week_note else "")
            + f"行情数据截至 {data_asof.date()}，今日/本周盈亏为截至该日"
        )

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
    ytd_ret_preview = ytd.get("ytd_return") if ytd else None
    chart_url = _publish_ytd_chart_url(nav, year, ytd_ret_preview, str(chart_path))
    if chart_url:
        print(f"[daily_notify] ytd chart url: {chart_url}", flush=True)
    else:
        print("[warn] ytd chart url unavailable; WeChat may show bars only", flush=True)

    # ASCII sparkline for text channel only (微信HTML改用真实折线图)
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

    # 真实最后行情日（ffill 前）。面板 ffill 后 close 末日会把港股/美股“看起来”已更新。
    raw_last = last_raw_dates(raw)
    data_end = {c: str(raw_last[c].date()) for c in CODES if c in raw_last}
    phone_line = f"接收人：{phone_hint}" if phone_hint else ""

    # 推送节奏（与回测一致）：
    # - 周一～周五：只推「本周已生效持仓 + 今日/本周盈亏」，不在周五抢发下周一目标
    #   （周五 19:00 港股/美股常未齐，2026-08-21 曾误推债券）
    # - 周六：加推「下周一调仓目标建议」（此时周五收盘更可能齐全）
    # 可用 REBALANCE_ADVISORY=1 强制进入调仓建议模式（测试）
    force_advisory = os.environ.get("REBALANCE_ADVISORY", "").strip() in {"1", "true", "TRUE", "yes"}
    is_rebalance_advisory = force_advisory or int(report_day.dayofweek) == 5
    monday_target = None
    monday_exec = None
    monday_signal = None
    monday_data_warning = ""
    monday_fallback_hold = False
    fri_anchor = None
    if is_rebalance_advisory:
        # 锚定最近周五信号日
        if int(report_day.dayofweek) == 5:
            fri_anchor = report_day.normalize() - pd.Timedelta(days=1)
        elif int(report_day.dayofweek) == 4:
            fri_anchor = report_day.normalize()
        else:
            fri_anchor = report_day.normalize() - pd.Timedelta(days=int(report_day.dayofweek) - 4)
            if fri_anchor > report_day.normalize():
                fri_anchor = fri_anchor - pd.Timedelta(days=7)
        if fri_anchor not in close.index:
            avail = close.index[close.index <= fri_anchor]
            fri_anchor = pd.Timestamp(avail.max()).normalize() if len(avail) else fri_anchor

        if pending is not None and pending["signal"].normalize() == fri_anchor:
            monday_target = pending["weights"]
            monday_exec = pending["exec"]
            monday_signal = pending["signal"]
        elif fri_anchor in signal_w.index:
            monday_signal = fri_anchor
            monday_target = signal_w.loc[fri_anchor].reindex(CODES).fillna(0.0)
            monday_exec = _planned_exec(fri_anchor, close.index)
        elif pending is not None:
            monday_target = pending["weights"]
            monday_exec = pending["exec"]
            monday_signal = pending["signal"]
        else:
            for r in reversed(sig_rows):
                if r["signal"].normalize() <= fri_anchor:
                    monday_target = r["weights"]
                    monday_exec = r["exec"]
                    monday_signal = r["signal"]
                    break

        # 双保险：仅当「目标仓位里的 hk/us 原生标的」周五收盘未入库时才回退
        target_codes = [
            c
            for c in CODES
            if monday_target is not None and abs(float(monday_target.get(c, 0.0))) > 1e-4
        ]
        stale_ox = stale_rebalance_codes(raw, fri_anchor, target_codes)
        if stale_ox and monday_target is not None:
            prev_hold = None
            for r in reversed(sig_rows):
                if r["signal"].normalize() < fri_anchor:
                    prev_hold = r
                    break
            if prev_hold is not None:
                proposed = monday_target.reindex(CODES).fillna(0.0)
                kept = prev_hold["weights"].reindex(CODES).fillna(0.0)
                turn = float((proposed - kept).abs().sum()) / 2.0
                stale_names = ",".join(
                    f"{c}/{UNIVERSE.get(c, {}).get('name', c)}" for c in stale_ox
                )
                monday_data_warning = (
                    f"⚠ 境外行情仍未齐（{stale_names} 真实收盘早于 {fri_anchor.date()}），"
                    f"新信号不可靠（若强行计算换手≈{turn*100:.1f}%）。"
                    "下周一目标暂沿用上周已生效持仓；请以周一开盘前复核为准。"
                )
                monday_target = kept
                monday_signal = prev_hold["signal"]
                monday_exec = _planned_exec(fri_anchor, close.index)
                monday_fallback_hold = True

    status_line = (
        f"本周持仓：信号 {effective['signal'].date()} → 已于 {effective['exec'].date()} 执行"
        if effective
        else "本周持仓：—"
    )

    title = f"📈 策略日报 {report_day.date()}｜日{_signed_pct(day_ret)} 周{_signed_pct(week_ret)}"
    if is_rebalance_advisory:
        title = (
            f"🔴下周一调仓目标建议！｜{report_day.date()} "
            f"日{_signed_pct(day_ret)} 周{_signed_pct(week_ret)}"
        )

    # -------- TEXT --------
    lines = ["【多资产轮动策略日报】"]
    if is_rebalance_advisory:
        lines += [
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
            "【下周一策略调仓目标建议！】",
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
        ]
    lines.append(f"生成：{asof_str}")
    if phone_line:
        lines.append(phone_line)
    mode_note = "周六调仓建议" if is_rebalance_advisory else "周一至周五持仓/盈亏"
    lines += [
        f"报告日：{report_day.date()}（约19:00｜{mode_note}｜数据截至 {data_asof.date()}）",
        "",
        "【今日盈亏】" + _signed_pct(day_ret),
        "【本周至今盈亏】" + _signed_pct(week_ret) + f"（{week_from or '—'} → {week_to}）",
        status_line,
    ]
    if week_note:
        lines.append(f"备注：{week_note}")

    if is_rebalance_advisory and monday_target is not None:
        lines += [
            "",
            "★★★ 下周一策略调仓目标建议 ★★★",
            f"信号日：{monday_signal.date() if monday_signal is not None else '—'}（周五收盘）",
            f"建议执行：{monday_exec.date() if monday_exec is not None else '下周一开盘'}",
        ]
        if monday_data_warning:
            lines.append(monday_data_warning)
            if monday_fallback_hold:
                lines.append("（数据未齐：目标=上周已生效持仓，勿按不完整新信号调仓）")
        lines.extend(_weight_lines(monday_target) or ["（全债券/空仓）"])

    lines += [
        "",
        "一、当前持仓（本周已生效，用于今日/本周盈亏）",
        f"信号→执行：{effective['signal'].date() if effective else '—'} → {effective['exec'].date() if effective else '—'}",
        f"状态：{regime}｜美股择强：{us_pick}",
        f"合格：{elig}",
        f"毛敞口：{gross:.3f}" if gross is not None else "毛敞口：—",
        f"金丝雀弱：{breadth}｜断路器：{'开' if breaker else '关'}",
    ]
    lines.extend(_weight_lines(eff_w) or ["（全债券/空仓）"])
    lines += [
        "",
        f"二、{year} 年初至今（YTD）",
        f"YTD：{_signed_pct(ytd.get('ytd_return'))}｜波动 {_pct(ytd.get('ann_vol'))}",
        f"回撤：{_pct(ytd.get('max_drawdown'))}｜Sharpe：{ytd.get('sharpe', float('nan')):.2f}"
        if ytd
        else "YTD：—",
        f"折线图：{chart_url}" if chart_url else (f"曲线：{spark}" if spark else ""),
        "",
        "三、全样本",
        f"净值 {float(nav.iloc[-1]):.4f}｜年化 {_pct(stats.get('ann_return'))}",
        f"Sharpe {stats.get('sharpe_rf0', float('nan')):.3f}｜MDD {_pct(stats.get('max_drawdown'))}",
        "",
        "四、数据日期（原始行情，未 ffill）",
    ]
    for c, d in data_end.items():
        mark = ""
        if (
            is_rebalance_advisory
            and fri_anchor is not None
            and pd.Timestamp(d) < fri_anchor
            and UNIVERSE.get(c, {}).get("market") in ("hk", "us")
        ):
            mark = " ⚠未齐"
        lines.append(f"{c} {UNIVERSE[c]['name']}:{d}{mark}")
    lines += [
        "",
        "推送说明：周一至周五=本周持仓与盈亏；周六=下周一调仓目标（对齐回测：周五信号→下交易日执行）。",
        "风险提示：历史不代表未来；QDII溢折价/汇率/融资未完全计入。",
    ]
    text = "\n".join([x for x in lines if x is not None])

    # -------- HTML --------
    ytd_ret = ytd.get("ytd_return") if ytd else None
    friday_banner = ""
    friday_target_block = ""
    if is_rebalance_advisory:
        sub = (
            "境外数据未齐：下方目标暂沿用上周持仓，周一开盘前请复核"
            if monday_fallback_hold
            else "请按下方红色卡片目标，于下周一开盘差额调仓"
        )
        friday_banner = f"""
  <div style="margin:0 0 14px 0;padding:14px 12px;border-radius:12px;background:#dc2626;color:#fff;text-align:center;border:3px solid #fecaca;">
    <div style="font-size:12px;letter-spacing:2px;opacity:.95;">SATURDAY REBALANCE ADVISORY</div>
    <div style="font-size:24px;font-weight:900;line-height:1.35;margin-top:4px;">下周一策略调仓目标建议！</div>
    <div style="font-size:13px;margin-top:6px;opacity:.95;">{sub}</div>
  </div>"""
        warn_html = (
            f"<div style='margin:8px 0;padding:8px 10px;background:#fff7ed;border:1px solid #f59e0b;"
            f"border-radius:8px;color:#9a3412;font-size:12px;line-height:1.5;'>{monday_data_warning}</div>"
            if monday_data_warning
            else ""
        )
        friday_target_block = f"""
  <div style="margin-top:12px;background:#fff1f2;color:#0f172a;border-radius:12px;padding:12px;border:2px solid #dc2626;">
    <div style="font-size:16px;font-weight:900;color:#dc2626;margin-bottom:6px;">下周一调仓目标建议</div>
    <div style="font-size:12px;color:#7f1d1d;margin-bottom:8px;">
      信号日 {monday_signal.date() if monday_signal is not None else "—"}（周五收盘）
      → 建议执行 {monday_exec.date() if monday_exec is not None else "下周一开盘"}
      {"｜数据未齐·沿用上周" if monday_fallback_hold else ""}
    </div>
    {warn_html}
    {_weight_bars_html(monday_target)}
  </div>"""

    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;background:#0b1220;color:#e5eefc;padding:16px;border-radius:14px;">
  {friday_banner}
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
    <div>
      <div style="font-size:12px;color:#93c5fd;letter-spacing:1px;">AFTER-CLOSE 19:00 REPORT</div>
      <div style="font-size:22px;font-weight:780;margin-top:4px;">每日策略盈亏简报</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:6px;">{asof_str}</div>
      {"<div style='font-size:12px;color:#94a3b8;'>" + phone_line + "</div>" if phone_line else ""}
      <div style="font-size:12px;color:#94a3b8;">报告日 {report_day.date()}｜数据截至 {data_asof.date()}</div>
    </div>
  </div>

  <div style="display:flex;gap:10px;margin-top:14px;">
    <div style="flex:1;background:#111827;border:1px solid #334155;border-radius:12px;padding:12px;">
      <div style="font-size:12px;color:#94a3b8;">今日盈亏</div>
      <div style="font-size:28px;font-weight:900;color:{_color(day_ret)};">{_signed_pct(day_ret)}</div>
    </div>
    <div style="flex:1;background:#111827;border:1px solid #334155;border-radius:12px;padding:12px;">
      <div style="font-size:12px;color:#94a3b8;">本周盈亏（至目前收盘）</div>
      <div style="font-size:28px;font-weight:900;color:{_color(week_ret)};">{_signed_pct(week_ret)}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px;">{week_from or "—"} → {week_to}</div>
    </div>
  </div>
  {f"<div style='margin-top:8px;font-size:12px;color:#fbbf24;'>{week_note}</div>" if week_note else ""}

  {friday_target_block}

  <div style="margin-top:12px;background:#f8fafc;color:#0f172a;border-radius:12px;padding:12px;">
    <div style="font-size:15px;font-weight:750;margin-bottom:4px;">当前持仓（本周已生效）</div>
    <div style="font-size:12px;color:#64748b;margin-bottom:8px;">
      信号 {effective['signal'].date() if effective else "—"} → 执行 {effective['exec'].date() if effective else "—"}
      ｜状态 <code>{regime}</code> ｜美股择强 <b>{us_pick}</b>
      ｜毛敞口 {f"{gross:.3f}" if gross is not None else "—"}
    </div>
    {_weight_bars_html(eff_w)}
  </div>

  <div style="margin-top:12px;background:#f8fafc;color:#0f172a;border-radius:12px;padding:12px;">
    <div style="font-size:15px;font-weight:750;margin-bottom:8px;">{year} 累计收益率折线图</div>
    <div style="font-size:13px;margin-bottom:8px;">
      YTD <b style="color:{_color(ytd_ret)};">{_signed_pct(ytd_ret)}</b>
      ｜波动 {_pct(ytd.get('ann_vol') if ytd else None)}
      ｜回撤 <span style="color:{_color(ytd.get('max_drawdown') if ytd else None)};">{_pct(ytd.get('max_drawdown') if ytd else None)}</span>
      ｜Sharpe {(f"{ytd.get('sharpe'):.2f}" if ytd and np.isfinite(ytd.get('sharpe', np.nan)) else "—")}
    </div>
    {
      (
        f"<div style='text-align:center;margin:8px 0 10px 0;'>"
        f"<img src='{chart_url}' alt='{year}累计收益率折线图' "
        f"style='width:100%;max-width:720px;height:auto;border:1px solid #e2e8f0;"
        f"border-radius:10px;background:#fff;display:block;margin:0 auto;'/>"
        f"</div>"
      )
      if chart_url
      else "<div style='font-size:12px;color:#b45309;margin:6px 0;'>折线图暂未能上传外链，请查看月度条形图。</div>"
    }
    <div style="font-size:12px;color:#64748b;margin:8px 0 6px 0;">月度收益</div>
    {_month_bars_html(monthly)}
  </div>

  <div style="margin-top:12px;background:#111827;border-radius:12px;padding:12px;border:1px solid #334155;">
    <div style="font-size:13px;color:#93c5fd;margin-bottom:6px;">全样本 / 数据</div>
    <div style="font-size:12px;color:#cbd5e1;line-height:1.7;">
      净值 {float(nav.iloc[-1]):.4f}｜年化 {_pct(stats.get('ann_return'))}
      ｜Sharpe {stats.get('sharpe_rf0', float('nan')):.3f}
      ｜MDD {_pct(stats.get('max_drawdown'))}<br/>
      {"；".join(f"{c}:{d}" for c,d in data_end.items())}
    </div>
  </div>

  <div style="margin-top:12px;font-size:11px;color:#94a3b8;line-height:1.5;">
    推送节奏：周一至周五约 19:00 推持仓/盈亏；周六约 19:00 加推下周一调仓目标。
  </div>
</div>
"""

    payload = {
        "asof": asof_str,
        "report_day": str(report_day.date()),
        "data_asof": str(data_asof.date()),
        "is_friday_close": False,
        "is_rebalance_advisory": bool(is_rebalance_advisory),
        "fri_anchor": str(fri_anchor.date()) if fri_anchor is not None else None,
        "effective_signal": str(effective["signal"].date()) if effective else None,
        "effective_exec": str(effective["exec"].date()) if effective else None,
        "monday_signal": str(monday_signal.date()) if monday_signal is not None else None,
        "monday_exec": str(monday_exec.date()) if monday_exec is not None else None,
        "monday_targets": {
            c: float(monday_target[c])
            for c in CODES
            if monday_target is not None and abs(float(monday_target[c])) > 1e-4
        },
        "monday_data_warning": monday_data_warning or None,
        "monday_fallback_hold": bool(monday_fallback_hold),
        "raw_data_end": data_end,
        "week_note": week_note,
        "week_return": week_ret,
        "day_return": day_ret,
        "held_targets": {
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
        "chart_url": chart_url or None,
    }

    return DailyReport(asof=asof_str, title=title, text=text, html=html, payload=payload)
