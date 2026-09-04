"""Build a readable MARK-style fundamental research memo from live snapshots."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any


def _n(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fmt_num(x: Any, digits: int = 2) -> str:
    v = _n(x)
    if v is None:
        return "—"
    if abs(v) >= 1e12:
        return f"{v/1e12:.{digits}f}T"
    if abs(v) >= 1e8:
        return f"{v/1e8:.{digits}f}亿"
    if abs(v) >= 1e9:
        return f"{v/1e9:.{digits}f}B"
    if abs(v) >= 1e6:
        return f"{v/1e6:.{digits}f}M"
    return f"{v:,.{digits}f}"


def _fmt_pct(x: Any, already_ratio: bool = True) -> str:
    v = _n(x)
    if v is None:
        return "—"
    if already_ratio and abs(v) <= 5:
        v *= 100
    return f"{v:.1f}%"


def _fmt_mult(x: Any) -> str:
    v = _n(x)
    if v is None:
        return "—"
    return f"{v:.1f}x"


def _money(x: Any, ccy: str) -> str:
    v = _n(x)
    if v is None:
        return "—"
    if ccy == "CNY":
        if abs(v) >= 1e8:
            return f"{v/1e8:.2f} 亿元"
        return f"{v:,.2f} 元"
    if ccy == "HKD":
        if abs(v) >= 1e8:
            return f"HK${v/1e8:.2f} 亿"
        return f"HK${v:,.2f}"
    if abs(v) >= 1e12:
        return f"${v/1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}bn"
    if abs(v) >= 1e6:
        return f"${v/1e6:.2f}m"
    return f"${v:,.2f}"


def _signed_pct(x: Any) -> str:
    v = _n(x)
    if v is None:
        return "—"
    return f"{v:+.1f}%"


def _growth_pct(x: Any) -> float | None:
    v = _n(x)
    if v is None:
        return None
    return v * 100 if abs(v) <= 5 else v


def _scenario_table(price: float | None, fwd_eps: float | None, trail_pe: float | None) -> list[dict[str, Any]]:
    if price is None:
        return []
    eps = None
    base_pe = None
    if fwd_eps not in (None, 0):
        eps = _n(fwd_eps)
        if eps:
            base_pe = price / eps
    if eps is None and trail_pe not in (None, 0):
        base_pe = _n(trail_pe)
        if base_pe:
            eps = price / base_pe
    if not eps or not base_pe or eps <= 0 or base_pe <= 0:
        return []
    rows = [
        ("乐观 25%", base_pe * 1.25, eps * 1.20, "增长与倍数双扩张"),
        ("基准 50%", base_pe * 0.95, eps * 1.00, "兑现经营预期，倍数略压"),
        ("悲观 25%", base_pe * 0.65, eps * 0.75, "增长下修并杀估值"),
    ]
    out = []
    for name, pe, e, note in rows:
        val = pe * e
        out.append(
            {
                "name": name,
                "assumptions": f"{note}；锚定 PE {pe:.1f}x × EPS {e:.2f}",
                "value": val,
                "valueText": _fmt_num(val),
                "returnPct": (val / price - 1.0) * 100,
            }
        )
    return out


def _coverage(snapshot: dict[str, Any]) -> dict[str, Any]:
    keys = [
        ("price", "现价"),
        ("marketCap", "市值"),
        ("trailingPE", "滚动市盈率"),
        ("forwardPE", "前瞻市盈率"),
        ("priceToBook", "市净率"),
        ("trailingEps", "TTM EPS"),
        ("forwardEps", "前瞻 EPS"),
        ("grossMargins", "毛利率"),
        ("profitMargins", "净利率"),
        ("returnOnEquity", "ROE"),
        ("revenueGrowth", "收入增长"),
        ("earningsGrowth", "盈利增长"),
        ("targetMeanPrice", "目标价"),
        ("totalCash", "现金"),
        ("freeCashflow", "现金流"),
    ]
    present = []
    missing = []
    for k, label in keys:
        if _n(snapshot.get(k)) is not None or (k == "price" and snapshot.get(k) is not None):
            present.append(label)
        else:
            missing.append(label)
    score = int(round(100 * len(present) / len(keys))) if keys else 0
    return {"score": score, "present": present, "missing": missing, "total": len(keys)}


def _price_path(candles: list[dict]) -> dict[str, Any]:
    out = {"ret1m": None, "ret3m": None, "ret1y": None, "fromHigh": None, "fromLow": None, "count": len(candles)}
    if not candles:
        return out
    last = candles[-1]["close"]

    def _ret(n: int) -> float | None:
        if len(candles) <= n:
            return None
        base = candles[-1 - n]["close"]
        return None if not base else (last / base - 1.0) * 100

    out["ret1m"] = _ret(21)
    out["ret3m"] = _ret(63)
    out["ret1y"] = _ret(min(252, len(candles) - 1))
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    if highs and lows:
        mx, mn = max(highs), min(lows)
        out["fromHigh"] = (last / mx - 1.0) * 100 if mx else None
        out["fromLow"] = (last / mn - 1.0) * 100 if mn else None
    return out


def _verdict(snapshot: dict[str, Any], path: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    pe = _n(snapshot.get("forwardPE")) or _n(snapshot.get("trailingPE"))
    g = _growth_pct(snapshot.get("earningsGrowth"))
    if g is None:
        g = _growth_pct(snapshot.get("revenueGrowth"))
    roe = _growth_pct(snapshot.get("returnOnEquity"))
    gm = _growth_pct(snapshot.get("grossMargins"))

    rating = "Watchlist"
    position = "0.5–1.5%"
    tone = "neutral"
    reasons: list[str] = []

    if coverage["score"] < 40:
        rating = "数据不足 / 仅观察"
        position = "0–1%"
        tone = "warn"
        reasons.append(f"关键字段完整度仅 {coverage['score']}%，先补齐增长/利润率再谈仓位。")
    elif pe is not None and pe > 80:
        rating = "Watchlist / 谨慎起步"
        position = "0.5–1.5%"
        tone = "warn"
        reasons.append(f"估值偏贵（PE {pe:.1f}x），更适合回撤后小仓验证。")
    elif pe is not None and pe > 40:
        if g is not None and g > 20:
            rating = "Structural Alpha starter"
            position = "1.0–2.0%"
            tone = "ok"
            reasons.append(f"高增长支撑溢价（PE {pe:.1f}x，增长约 {g:.0f}%），用仓位纪律换验证时间。")
        else:
            rating = "Watchlist / 谨慎起步"
            position = "0.5–1.5%"
            tone = "warn"
            reasons.append(f"成长溢价仍在价格里（PE {pe:.1f}x），增长证据不足则降仓。")
    elif pe is not None and pe < 25 and g is not None and g > 10:
        rating = "Structural Alpha starter"
        position = "1.5–3.0%"
        tone = "ok"
        reasons.append(f"估值与增长匹配度尚可（PE {pe:.1f}x，增长约 {g:.0f}%）。")
    elif pe is not None and pe < 25 and (roe is not None and roe > 12) and (gm is not None and gm > 30):
        rating = "Quality compounder / starter"
        position = "1.5–2.5%"
        tone = "ok"
        reasons.append(
            f"倍数不高（PE {pe:.1f}x）且质量指标扎实（ROE {_fmt_pct(snapshot.get('returnOnEquity'))}，"
            f"毛利率 {_fmt_pct(snapshot.get('grossMargins'))}），适合作为结构型观察仓。"
        )
    elif pe is not None and pe < 20:
        rating = "Structural Alpha starter"
        position = "1.5–2.5%"
        tone = "ok"
        reasons.append(f"倍数不高（PE {pe:.1f}x），可作为结构型观察仓。")
    else:
        reasons.append("已形成可交易快照；预期差仍需下一季财报与产业验证。")

    if path.get("ret3m") is not None and path["ret3m"] < -20:
        reasons.append(f"近 3 月回调 {_signed_pct(path['ret3m'])}，情绪与基本面可能错位。")
    if path.get("fromHigh") is not None and path["fromHigh"] < -25:
        reasons.append(f"相对区间高点回撤 {_signed_pct(path['fromHigh'])}。")

    upside = None
    price = _n(snapshot.get("price"))
    target = _n(snapshot.get("targetMeanPrice"))
    if price and target:
        upside = (target / price - 1.0) * 100
        reasons.append(f"公开目标价隐含空间约 {_signed_pct(upside)}。")

    return {
        "rating": rating,
        "position": position,
        "tone": tone,
        "oneLiner": " ".join(reasons[:3]),
        "bullets": reasons[:4],
        "upside": upside,
    }


def _narrative(snapshot: dict[str, Any], path: dict[str, Any], resolved: dict[str, Any]) -> list[dict[str, str]]:
    """Readable section blurbs grounded only in available numbers."""
    pe = _n(snapshot.get("forwardPE")) or _n(snapshot.get("trailingPE"))
    pb = _n(snapshot.get("priceToBook"))
    g_rev = _growth_pct(snapshot.get("revenueGrowth"))
    g_earn = _growth_pct(snapshot.get("earningsGrowth"))
    roe = _growth_pct(snapshot.get("returnOnEquity"))
    gm = _growth_pct(snapshot.get("grossMargins"))
    pm = _growth_pct(snapshot.get("profitMargins"))
    blocks: list[dict[str, str]] = []

    val_bits = []
    if pe is not None:
        if pe > 60:
            val_bits.append(f"滚动/前瞻 PE {_fmt_mult(pe)}，市场在为远期增长付费。")
        elif pe < 25:
            val_bits.append(f"PE {_fmt_mult(pe)}，绝对倍数不高。")
        else:
            val_bits.append(f"PE {_fmt_mult(pe)}，处于中等偏贵区间。")
    if pb is not None:
        val_bits.append(f"PB {_fmt_mult(pb)}。")
    if not val_bits:
        val_bits.append("缺少可靠 PE/PB，估值结论暂缓。")
    blocks.append({"title": "估值怎么读", "body": " ".join(val_bits)})

    qual = []
    if gm is not None:
        qual.append(f"毛利率 {_fmt_pct(snapshot.get('grossMargins'))}")
    if pm is not None:
        qual.append(f"净利率 {_fmt_pct(snapshot.get('profitMargins'))}")
    if roe is not None:
        qual.append(f"ROE {_fmt_pct(snapshot.get('returnOnEquity'))}")
    if g_rev is not None:
        qual.append(f"收入增长 {_fmt_pct(snapshot.get('revenueGrowth'))}")
    if g_earn is not None:
        qual.append(f"盈利增长 {_fmt_pct(snapshot.get('earningsGrowth'))}")
    if qual:
        body = "、".join(qual) + "。"
        if gm is not None and gm > 50 and (roe is None or roe > 10):
            body += " 利润池质量偏高，重点盯增长是否失速、而非毛利率能否维持。"
        elif g_earn is not None and g_earn > 30:
            body += " 盈利弹性大，需同时核对基数与一次性项目。"
        elif g_earn is not None and g_earn < 0 and pe is not None and pe < 25:
            body += " 盈利走弱但倍数不高，属于「便宜是否陷阱」区间，优先核对扣非与现金流。"
        blocks.append({"title": "质量与增长", "body": body})
    else:
        blocks.append({"title": "质量与增长", "body": "利润率/ROE/增长暂缺。港股与部分美股需依赖财报源；本次未拿到完整质量指标。"})

    mom = []
    if path.get("ret1m") is not None:
        mom.append(f"近1月 {_signed_pct(path['ret1m'])}")
    if path.get("ret3m") is not None:
        mom.append(f"近3月 {_signed_pct(path['ret3m'])}")
    if path.get("ret1y") is not None:
        mom.append(f"近1年 {_signed_pct(path['ret1y'])}")
    if path.get("fromHigh") is not None:
        mom.append(f"距高点 {_signed_pct(path['fromHigh'])}")
    if mom:
        body = "；".join(mom) + "。"
        if path.get("fromHigh") is not None and path["fromHigh"] < -30:
            body += " 深度回撤后，更适合用基本面验证替代追涨。"
        blocks.append({"title": "价格位置", "body": body})

    fund_asof = snapshot.get("fundamentalsAsOf")
    fund_type = snapshot.get("fundamentalsReportType")
    if fund_asof or fund_type:
        blocks.append(
            {
                "title": "财报锚点",
                "body": f"基本面字段主要对齐 {fund_type or '最新报告'}（{fund_asof or '日期未知'}）。"
                f" 市场：{resolved.get('name_hint')} / {resolved.get('yahoo')}。",
            }
        )
    return blocks


def _metric_groups(snapshot: dict[str, Any], resolved: dict[str, Any], path: dict[str, Any], ccy: str) -> list[dict[str, Any]]:
    price = snapshot.get("price")
    upside = None
    if _n(price) and _n(snapshot.get("targetMeanPrice")):
        upside = (_n(snapshot["targetMeanPrice"]) / _n(price) - 1.0) * 100
    cf_label = "经营现金流" if snapshot.get("cashflowIsOperating") else "自由现金流"
    return [
        {
            "title": "估值",
            "rows": [
                {"label": "现价 / 涨跌", "value": f"{_fmt_num(price)} {ccy}  ({_signed_pct(snapshot.get('changePct'))})"},
                {"label": "市值", "value": _money(snapshot.get("marketCap"), ccy)},
                {"label": "滚动 PE / 前瞻 PE", "value": f"{_fmt_mult(snapshot.get('trailingPE'))} / {_fmt_mult(snapshot.get('forwardPE'))}"},
                {"label": "PB / PEG", "value": f"{_fmt_mult(snapshot.get('priceToBook'))} / {_fmt_mult(snapshot.get('pegRatio'))}"},
                {"label": "TTM EPS / 前瞻 EPS", "value": f"{_fmt_num(snapshot.get('trailingEps'))} / {_fmt_num(snapshot.get('forwardEps'))}"},
                {"label": "目标价 / 隐含空间", "value": f"{_fmt_num(snapshot.get('targetMeanPrice'))} / {_signed_pct(upside)}"},
            ],
        },
        {
            "title": "质量与增长",
            "rows": [
                {"label": "ROE", "value": _fmt_pct(snapshot.get("returnOnEquity"))},
                {"label": "毛利率 / 净利率", "value": f"{_fmt_pct(snapshot.get('grossMargins'))} / {_fmt_pct(snapshot.get('profitMargins'))}"},
                {"label": "收入增长 / 盈利增长", "value": f"{_fmt_pct(snapshot.get('revenueGrowth'))} / {_fmt_pct(snapshot.get('earningsGrowth'))}"},
                {"label": "现金 / 负债", "value": f"{_money(snapshot.get('totalCash'), ccy)} / {_money(snapshot.get('totalDebt'), ccy)}"},
                {"label": cf_label, "value": _money(snapshot.get("freeCashflow"), ccy)},
                {"label": "每股净资产", "value": _fmt_num(snapshot.get("bookValue"))},
            ],
        },
        {
            "title": "交易与识别",
            "rows": [
                {"label": "市场识别", "value": f"{resolved.get('name_hint')} / {resolved.get('market')} / {resolved.get('yahoo')}"},
                {"label": "近1月 / 近3月 / 近1年", "value": f"{_signed_pct(path.get('ret1m'))} / {_signed_pct(path.get('ret3m'))} / {_signed_pct(path.get('ret1y'))}"},
                {"label": "距区间高/低", "value": f"{_signed_pct(path.get('fromHigh'))} / {_signed_pct(path.get('fromLow'))}"},
                {"label": "52 周区间", "value": f"{_fmt_num(snapshot.get('fiftyTwoWeekLow'))} – {_fmt_num(snapshot.get('fiftyTwoWeekHigh'))}"},
                {"label": "数据时点", "value": str(snapshot.get("asOf") or "—")},
                {"label": "数据源", "value": " · ".join(snapshot.get("dataSources") or ["混合公开源"])},
            ],
        },
    ]


def _to_html(report: dict[str, Any]) -> str:
    v = report["verdict"]
    cov = report["coverage"]
    kpis = "".join(
        f"<div class='kpi'><div class='k'>{escape(k['label'])}</div><div class='v'>{escape(k['value'])}</div></div>"
        for k in report["kpis"]
    )
    bullets = "".join(f"<li>{escape(b)}</li>" for b in v.get("bullets") or [v["oneLiner"]])
    narrative = "".join(
        f"<div class='read-card'><h4>{escape(n['title'])}</h4><p>{escape(n['body'])}</p></div>"
        for n in report["narrative"]
    )
    groups = []
    for g in report["metricGroups"]:
        rows = "".join(
            f"<tr><th>{escape(r['label'])}</th><td>{escape(r['value'])}</td></tr>" for r in g["rows"]
        )
        groups.append(
            f"<div class='metric-group'><h4>{escape(g['title'])}</h4>"
            f"<table class='data'><tbody>{rows}</tbody></table></div>"
        )
    scen = ""
    if report["scenarios"]:
        body = "".join(
            f"<tr><td>{escape(s['name'])}</td><td>{escape(s['assumptions'])}</td>"
            f"<td>{escape(s['valueText'])}</td><td>{s['returnPct']:+.1f}%</td></tr>"
            for s in report["scenarios"]
        )
        scen = f"""
        <h3>估值情景</h3>
        <table class='data scen'><thead><tr><th>情景</th><th>假设</th><th>价值</th><th>回报</th></tr></thead>
        <tbody>{body}</tbody></table>
        <p class='note'>概率加权期望回报：<strong>{_fmt_pct(report.get('expectedReturnPct'), already_ratio=False)}</strong>
        （乐观25% / 基准50% / 悲观25%）。</p>
        """
    else:
        scen = "<h3>估值情景</h3><p class='note'>缺少可靠 EPS/PE 锚，本报告<strong>不编造</strong>情景数字。</p>"

    missing = "、".join(cov["missing"][:8]) if cov["missing"] else "无"
    present_n = len(cov["present"])
    return f"""
    <section class='verdict tone-{escape(v['tone'])}'>
      <div class='badge'>{escape(v['rating'])}</div>
      <div class='pos'>建议仓位 {escape(v['position'])}</div>
      <ul class='verdict-list'>{bullets}</ul>
    </section>
    <section class='kpis'>{kpis}</section>
    <section class='block'>
      <h3>读数</h3>
      <div class='read-grid'>{narrative}</div>
    </section>
    <section class='block'>
      <h3>关键指标</h3>
      <div class='metric-grid'>{''.join(groups)}</div>
    </section>
    <section class='block'>{scen}</section>
    <section class='block'>
      <h3>领先指标与证伪</h3>
      <div class='falsify'>
        <div><h4>证明在工作</h4><p>收入/EPS 上修、利润率不塌、现金流跟上利润。</p></div>
        <div><h4>观点作废</h4><p>增长显著下修且倍数不压、ROE 趋势破坏、资产负债表恶化。</p></div>
        <div><h4>可忍受噪音</h4><p>单日波动、板块 beta、资金进出，不自动改结论。</p></div>
      </div>
    </section>
    <section class='block'>
      <h3>数据完整度</h3>
      <div class='coverage-bar'><span style='width:{cov["score"]}%'></span></div>
      <p>完整度 <strong>{cov['score']}%</strong>（{present_n}/{cov['total']}）。暂缺：{escape(missing)}。</p>
      <p class='note'>本页是本地 Web 自动估值备忘，不是完整定性尽调。深度 alpha 请用 <code>/mark-alpha-research</code>。</p>
    </section>
    <section class='block'>
      <h3>来源</h3>
      <ol class='sources'>
        {''.join(f'<li>{escape(s)}</li>' for s in report['sources'])}
      </ol>
    </section>
    """


def _to_markdown(report: dict[str, Any]) -> str:
    v = report["verdict"]
    lines = [
        f"# {report['name']}",
        "",
        f"**{report['display']} / {report['yahoo']} | 基本面估值报告 | {report['generatedAt']}**",
        "",
        " | ".join(f"**{k['label']}** {k['value']}" for k in report["kpis"][:6]),
        "",
        "---",
        "",
        "## 1. 一句话观点",
        "",
        f"**{v['rating']}，建议仓位 {v['position']}。**",
        "",
    ]
    for b in v.get("bullets") or [v["oneLiner"]]:
        lines.append(f"- {b}")
    lines += ["", "## 2. 读数", ""]
    for n in report["narrative"]:
        lines += [f"### {n['title']}", "", n["body"], ""]
    lines += ["## 3. 关键指标", ""]
    for g in report["metricGroups"]:
        lines += [f"### {g['title']}", "", "| 项目 | 数据 |", "|---|---|"]
        for row in g["rows"]:
            lines.append(f"| {row['label']} | {row['value']} |")
        lines.append("")
    lines += ["## 4. 估值情景", ""]
    if report["scenarios"]:
        lines += ["| 情景 | 假设 | 价值 | 回报 |", "|---|---|---:|---:|"]
        for s in report["scenarios"]:
            lines.append(
                f"| {s['name']} | {s['assumptions']} | {s['valueText']} | {s['returnPct']:+.1f}% |"
            )
        lines += ["", f"**概率加权期望回报：{_fmt_pct(report.get('expectedReturnPct'), already_ratio=False)}**", ""]
    else:
        lines += ["缺少可靠 EPS/PE 锚，不编造情景。", ""]
    cov = report["coverage"]
    lines += [
        "## 5. 领先指标与证伪",
        "",
        "- 证明在工作：收入/EPS 上修、利润率不塌、现金流跟上利润。",
        "- 观点作废：增长显著下修且倍数不压、ROE 趋势破坏、资产负债表恶化。",
        "- 噪音：单日波动、板块 beta、资金进出。",
        "",
        "## 6. 数据完整度与来源",
        "",
        f"- 完整度：{cov['score']}%（{len(cov['present'])}/{cov['total']}）",
        f"- 暂缺：{'、'.join(cov['missing']) if cov['missing'] else '无'}",
        "",
    ]
    for i, src in enumerate(report["sources"], 1):
        lines.append(f"{i}. {src}")
    lines += ["", "*本报告不构成投资建议。*", ""]
    return "\n".join(lines)


def build_mark_report(resolved: dict[str, Any], snapshot: dict[str, Any], candles: list[dict]) -> dict[str, Any]:
    ccy = snapshot.get("currency") or resolved.get("currency") or "USD"
    name = snapshot.get("name") or resolved.get("display")
    display = resolved.get("display")
    yahoo = resolved.get("yahoo")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    path = _price_path(candles)
    coverage = _coverage(snapshot)
    verdict = _verdict(snapshot, path, coverage)
    scenarios = _scenario_table(snapshot.get("price"), snapshot.get("forwardEps"), snapshot.get("trailingPE"))
    expected = None
    if scenarios:
        expected = 0.25 * scenarios[0]["returnPct"] + 0.5 * scenarios[1]["returnPct"] + 0.25 * scenarios[2]["returnPct"]

    kpis = [
        {"label": "现价", "value": f"{_fmt_num(snapshot.get('price'))} {ccy}"},
        {"label": "市值", "value": _money(snapshot.get("marketCap"), ccy)},
        {"label": "滚动 PE", "value": _fmt_mult(snapshot.get("trailingPE"))},
        {"label": "ROE", "value": _fmt_pct(snapshot.get("returnOnEquity"))},
        {
            "label": "收入增长",
            "value": _fmt_pct(snapshot.get("revenueGrowth")),
        },
        {"label": "完整度", "value": f"{coverage['score']}%"},
    ]
    metric_groups = _metric_groups(snapshot, resolved, path, ccy)
    narrative = _narrative(snapshot, path, resolved)
    sources = snapshot.get("dataSources") or []
    if not sources:
        sources = ["公开行情接口（Yahoo / 新浪 / 腾讯 / 东财 / Nasdaq 等，按可用性自动切换）"]
    sources = list(sources) + [f"生成时点 {snapshot.get('asOf') or today}"]

    report = {
        "title": f"{display} {name} 基本面估值报告",
        "name": name,
        "display": display,
        "yahoo": yahoo,
        "generatedAt": today,
        "rating": verdict["rating"],
        "position": verdict["position"],
        "oneLiner": verdict["oneLiner"],
        "expectedReturnPct": expected,
        "scenarios": scenarios,
        "verdict": verdict,
        "coverage": coverage,
        "kpis": kpis,
        "metricGroups": metric_groups,
        "narrative": narrative,
        "kvRows": [row for g in metric_groups for row in g["rows"]],
        "sources": sources,
        "path": path,
    }
    report["html"] = _to_html(report)
    report["markdown"] = _to_markdown(report)
    return report
