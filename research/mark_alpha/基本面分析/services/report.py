"""Build a MARK-style fundamental valuation memo from live market snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _fmt_num(x: Any, digits: int = 2) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if abs(v) >= 1e12:
        return f"{v/1e12:.{digits}f}T"
    if abs(v) >= 1e8 and digits <= 2:
        # Prefer 亿 for CNY-scale when large
        if abs(v) >= 1e8:
            return f"{v/1e8:.{digits}f}亿"
    if abs(v) >= 1e9:
        return f"{v/1e9:.{digits}f}B"
    if abs(v) >= 1e6:
        return f"{v/1e6:.{digits}f}M"
    return f"{v:,.{digits}f}"


def _fmt_pct(x: Any, already_ratio: bool = True) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if already_ratio and abs(v) <= 5:
        v *= 100
    return f"{v:.1f}%"


def _fmt_mult(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.1f}x"
    except (TypeError, ValueError):
        return str(x)


def _money(x: Any, ccy: str) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if ccy == "CNY":
        if abs(v) >= 1e8:
            return f"{v/1e8:.2f} 亿元"
        if abs(v) >= 1e4:
            return f"{v/1e4:.2f} 万元"
        return f"{v:.2f} 元"
    if ccy == "HKD":
        if abs(v) >= 1e8:
            return f"HK${v/1e8:.2f}亿"
        return f"HK${v:,.2f}"
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}bn"
    if abs(v) >= 1e6:
        return f"${v/1e6:.2f}m"
    return f"${v:,.2f}"


def _scenario_table(price: float | None, fwd_eps: float | None, trail_pe: float | None) -> list[dict[str, Any]]:
    if price is None:
        return []
    # Heuristic multiples around current trailing / forward PE
    base_pe = fwd_eps and fwd_eps > 0 and price / fwd_eps or trail_pe or 25.0
    try:
        base_pe = float(base_pe)
    except (TypeError, ValueError):
        base_pe = 25.0
    eps = fwd_eps if fwd_eps not in (None, 0) else (price / base_pe if base_pe else None)
    if eps is None:
        return []

    rows = [
        ("乐观 25%", base_pe * 1.25, eps * 1.20, "增长与倍数双扩张"),
        ("基准 50%", base_pe * 0.95, eps * 1.00, "兑现一致预期，倍数略压"),
        ("悲观 25%", base_pe * 0.65, eps * 0.75, "增长下修 + 杀估值"),
    ]
    out = []
    for name, pe, e, note in rows:
        val = pe * e
        ret = (val / price - 1.0) * 100
        out.append(
            {
                "name": name,
                "assumptions": note + f"；PE {pe:.1f}x，EPS {e:.2f}",
                "value": val,
                "returnPct": ret,
            }
        )
    return out


def build_mark_report(resolved: dict[str, Any], snapshot: dict[str, Any], candles: list[dict]) -> dict[str, Any]:
    ccy = snapshot.get("currency") or resolved.get("currency") or "USD"
    price = snapshot.get("price")
    name = snapshot.get("name") or resolved.get("display")
    yahoo = resolved.get("yahoo")
    display = resolved.get("display")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Returns from candles
    ret_1m = ret_3m = ret_1y = None
    if candles:
        last = candles[-1]["close"]

        def _ret(n: int) -> float | None:
            if len(candles) <= n:
                return None
            base = candles[-1 - n]["close"]
            if not base:
                return None
            return (last / base - 1.0) * 100

        ret_1m, ret_3m, ret_1y = _ret(21), _ret(63), _ret(252 if len(candles) > 252 else len(candles) - 1)

    fwd_eps = snapshot.get("forwardEps")
    trail_eps = snapshot.get("trailingEps")
    scenarios = _scenario_table(price, fwd_eps, snapshot.get("trailingPE"))
    expected = None
    if scenarios:
        # probabilities embedded in labels
        probs = [0.25, 0.50, 0.25]
        expected = sum(p * s["returnPct"] for p, s in zip(probs, scenarios))

    # Rating heuristic
    pe = snapshot.get("forwardPE") or snapshot.get("trailingPE")
    growth = snapshot.get("earningsGrowth") or snapshot.get("revenueGrowth")
    rating = "Watchlist"
    position = "0.5–1.5%"
    one_liner = "数据快照已生成；质量与预期差需结合财报与产业验证后再定仓。"
    g = None
    if growth is not None:
        try:
            g = float(growth)
            g = g * 100 if abs(g) <= 5 else g
        except (TypeError, ValueError):
            g = None
    if pe:
        pe = float(pe)
        if pe > 60:
            rating = "Watchlist / 谨慎起步"
            position = "0.5–1.5%"
            one_liner = "质量可能不差，但倍数已高；更适合回撤后起步，而不是追高满仓。"
        elif pe > 35:
            rating = "Structural Alpha starter"
            position = "1.0–2.5%"
            one_liner = "成长溢价仍在价格里；用仓位纪律换取产业与盈利二阶验证时间。"
        elif g is not None and pe < 25 and g > 15:
            rating = "Structural Alpha starter"
            position = "1.5–3.0%"
            one_liner = "估值相对增长不算苛刻，可作为结构型起步仓，等待财报验证斜率。"
        elif g is not None and pe < 20 and g > 5:
            rating = "Structural Alpha starter"
            position = "1.5–3.0%"
            one_liner = "估值与增长匹配度尚可，适合作为结构型观察仓。"

    upside = None
    if price and snapshot.get("targetMeanPrice"):
        upside = (float(snapshot["targetMeanPrice"]) / float(price) - 1.0) * 100

    md_lines = [
        f"# {name}",
        "",
        f"**{display} / {yahoo} | 基本面估值快报 | {today}**",
        "",
        f"现价 **{_fmt_num(price)} {ccy}** | 市值 **{_money(snapshot.get('marketCap'), ccy)}** | "
        f"52 周 **{_fmt_num(snapshot.get('fiftyTwoWeekLow'))}–{_fmt_num(snapshot.get('fiftyTwoWeekHigh'))}**",
        "",
        "---",
        "",
        "## 1. 一句话观点",
        "",
        f"**{rating}，建议观察仓位 {position}。** {one_liner}",
        "",
        "> 本页为本地 Web 根据公开行情自动生成的买方框架快报，不是完整深度尽调。"
        "完整定性 alpha（不可逆变化、证伪条件、管理层资本配置）请继续用 `/mark-alpha-research`。",
        "",
        "## 2. 市场预期与估值快照",
        "",
        "| 项目 | 数据 |",
        "|---|---|",
        f"| 识别市场 | {resolved.get('name_hint')} / {resolved.get('market')} |",
        f"| 现价 / 涨跌 | {_fmt_num(price)} / {_fmt_num(snapshot.get('change'))} ({_fmt_pct(snapshot.get('changePct'), already_ratio=False)}) |",
        f"| 市值 | {_money(snapshot.get('marketCap'), ccy)} |",
        f"| 滚动 PE / 前瞻 PE | {_fmt_mult(snapshot.get('trailingPE'))} / {_fmt_mult(snapshot.get('forwardPE'))} |",
        f"| PEG / PB | {_fmt_mult(snapshot.get('pegRatio'))} / {_fmt_mult(snapshot.get('priceToBook'))} |",
        f"| EV/Sales / EV/EBITDA | {_fmt_mult(snapshot.get('enterpriseToRevenue'))} / {_fmt_mult(snapshot.get('enterpriseToEbitda'))} |",
        f"| TTM EPS / Forward EPS | {_fmt_num(trail_eps)} / {_fmt_num(fwd_eps)} |",
        f"| ROE / 毛利率 / 净利率 | {_fmt_pct(snapshot.get('returnOnEquity'))} / {_fmt_pct(snapshot.get('grossMargins'))} / {_fmt_pct(snapshot.get('profitMargins'))} |",
        f"| 收入增长 / 盈利增长 | {_fmt_pct(snapshot.get('revenueGrowth'))} / {_fmt_pct(snapshot.get('earningsGrowth'))} |",
        f"| 股息率 / Beta | {_fmt_pct(snapshot.get('dividendYield'))} / {_fmt_num(snapshot.get('beta'))} |",
        f"| 分析师目标价 / 隐含空间 | {_fmt_num(snapshot.get('targetMeanPrice'))} / {_fmt_pct(upside, already_ratio=False)} |",
        f"| 建议评级关键词 | {snapshot.get('recommendationKey') or '—'} |",
        f"| 数据时点 | {snapshot.get('asOf')} |",
        "",
        "## 3. 价格路径（K 线衍生）",
        "",
        f"- 近 1 月：{_fmt_pct(ret_1m, already_ratio=False)}",
        f"- 近 3 月：{_fmt_pct(ret_3m, already_ratio=False)}",
        f"- 近 1 年：{_fmt_pct(ret_1y, already_ratio=False)}",
        f"- 样本K线数量：{len(candles)}",
        "",
        "## 4. 盈利与回报拆解（数据层）",
        "",
        "```text",
        "价格 / 市值",
        "→ PE · PB · EV/Sales",
        "→ EPS / 收入增长 / 利润率",
        "→ ROE / FCF",
        "→ 目标价隐含回报",
        "→ 情景期望回报",
        "```",
        "",
        f"- 总现金：{_money(snapshot.get('totalCash'), ccy)}",
        f"- 总负债：{_money(snapshot.get('totalDebt'), ccy)}",
        f"- 自由现金流：{_money(snapshot.get('freeCashflow'), ccy)}",
        f"- 流动比率：{_fmt_num(snapshot.get('currentRatio'))}",
        "",
        "## 5. 二阶观察",
        "",
    ]

    ye = snapshot.get("yearEstimates") or []
    if ye:
        md_lines += ["| 期间 | 盈利增速 | 一致 EPS | 一致收入 | 分析师数 |", "|---|---:|---:|---:|---:|"]
        for row in ye:
            md_lines.append(
                f"| {row.get('period')} | {_fmt_pct(row.get('growth'))} | "
                f"{_fmt_num(row.get('earningsEstimateAvg'))} | "
                f"{_money(row.get('revenueEstimateAvg'), ccy)} | "
                f"{row.get('earningsEstimateNumAnalysts') or '—'} |"
            )
        md_lines.append("")
    else:
        md_lines += ["一致预期明细暂不可用，以上依赖关键统计字段。", ""]

    md_lines += [
        "## 6. 估值情景与期望回报",
        "",
    ]
    if scenarios:
        md_lines += [
            "| 情景 / 概率 | 关键假设 | 价值 | 相对现价 |",
            "|---|---|---:|---:|",
        ]
        for s in scenarios:
            md_lines.append(
                f"| {s['name']} | {s['assumptions']} | {_fmt_num(s['value'])} | {s['returnPct']:+.1f}% |"
            )
        md_lines += ["", f"**概率加权期望回报（启发式）：{_fmt_pct(expected, already_ratio=False)}**", ""]
    else:
        md_lines += ["缺少 EPS / 倍数锚，暂不生成情景表。", ""]

    md_lines += [
        "## 7. 领先指标与证伪（框架提示）",
        "",
        "- 证明观点在工作：收入/EPS 连续上修、利润率不塌、自由现金流跟上利润。",
        "- Thesis broken if：前瞻增长显著下修且倍数未压缩、ROE 趋势破坏、资产负债表恶化。",
        "- 噪音：单日波动、板块 beta、北向/被动资金进出。",
        "",
        "## 8. 组合拟合",
        "",
        f"- 分类：**{rating}**",
        f"- 建议仓位带：**{position}**",
        "- 导出后可粘贴进投委会备忘；深度因果链请跑完整 MARK 技能。",
        "",
        "## 9. 最终评级",
        "",
        f"**{rating}**",
        "",
        "---",
        "",
        "## 来源",
        "",
        f"1. Yahoo Finance chart / quoteSummary，标的 `{yahoo}`，抓取时点 {snapshot.get('asOf')}。",
        "2. 本地「基本面分析」Web 自动估值引擎（启发式情景，非卖方目标价模型）。",
        "",
        "*本报告不构成投资建议。*",
        "",
    ]

    markdown = "\n".join(md_lines)
    return {
        "title": f"{display} {name} 基本面估值快报",
        "rating": rating,
        "position": position,
        "oneLiner": one_liner,
        "expectedReturnPct": expected,
        "scenarios": scenarios,
        "markdown": markdown,
        "generatedAt": today,
    }
