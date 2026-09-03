"""Next-session report builder — docs/报告规范.md"""

from __future__ import annotations

from typing import Any, Sequence


def _pct(x: Any, d: int = 1) -> str:
    if x is None:
        return "N/A"
    return f"{float(x) * 100:.{d}f}%"


def _num(x: Any, d: int = 2) -> str:
    if x is None:
        return "N/A"
    return f"{float(x):,.{d}f}"


def build_next_session_report(meta: dict[str, Any], results: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    ts = meta.get("quote_asof", "")
    tgt = meta.get("target_session", "")
    lines.append(f"# 下一交易日候选 · Short Strangle · 数据截止 {ts}")
    lines.append("")
    lines.append("> **非即时成交声明**：本报告供下一交易日开盘前研究，不代表收盘价可立即成交。入场前须重新核验实时报价与保证金。")
    lines.append("")
    lines.append("## 元数据")
    lines.append("")
    for k in (
        "report_version",
        "methods_version",
        "rules_version",
        "data_source",
        "quote_asof",
        "target_session",
        "model",
        "account_equity",
    ):
        lines.append(f"- `{k}`: {meta.get(k)}")
    counts = meta.get("counts", {})
    lines.append(f"- 扫描品种: {counts.get('scanned', 0)} · 推荐: {counts.get('推荐', 0)} · 观察: {counts.get('观察', 0)} · 排除: {counts.get('排除', 0)}")
    lines.append("")

    rec = [r for r in results if r.get("classification") == "推荐"]
    watch = [r for r in results if r.get("classification") == "观察"]
    excl = [r for r in results if r.get("classification") == "排除"]

    lines.append("## 1. 分类摘要")
    lines.append("")
    if not rec:
        lines.append("**今日无推荐**（全部硬闸门未同时满足或缺少客户保证金/账户权益/252日IV历史）。")
        lines.append("")
    else:
        lines.append(f"共 **{len(rec)}** 个品种进入推荐。")
        lines.append("")

    if watch:
        lines.append("### 观察池")
        lines.append("")
        for r in watch:
            lines.append(
                f"- **{r.get('product_name')}** {r.get('underlying_futures')} — "
                + "; ".join(r.get("classification_reasons") or ["见明细"])
            )
        lines.append("")

    lines.append("## 2. 候选明细")
    lines.append("")
    show = rec + watch
    if not show:
        lines.append("无观察及以上候选。")
    else:
        lines.append(
            "| 分类 | 品种 | 标的期货 | 月份/DTE | 卖C/K/买一 | 卖P/K/买一 | σ* | IVR | IVP | VRP | 技分 | 权/保(无优惠) | RN-POP | 建议手数 |"
        )
        lines.append("|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
        for r in show:
            c, p = r.get("call"), r.get("put")
            m = r.get("margin") or {}
            lots = r.get("suggested_lots")
            if r.get("classification") == "推荐":
                lots_s = str(lots) if lots is not None else "N/A(缺权益)"
            else:
                lots_s = "—"
            lines.append(
                f"| {r.get('classification')} | {r.get('product_name')} | {r.get('underlying_futures')} | "
                f"{r.get('option_month')}/{r.get('dte')}d | "
                f"{c.get('strike') if c else '—'}/{c.get('bid') if c else '—'} | "
                f"{p.get('strike') if p else '—'}/{p.get('bid') if p else '—'} | "
                f"{_pct(r.get('sigma_star'))} | "
                f"{_num(r.get('iv_rank'), 1) if r.get('iv_rank') is not None else 'N/A'} | "
                f"{_num(r.get('iv_percentile'), 1) if r.get('iv_percentile') is not None else 'N/A'} | "
                f"{_pct(r.get('vrp')) if r.get('vrp') is not None else 'N/A'} | "
                f"{_num(r.get('technical_score'), 0)} | "
                f"{_pct(m.get('premium_margin_ratio_no_combo')) if m.get('premium_margin_ratio_no_combo') is not None else 'N/A'} | "
                f"{_pct(r.get('pop_risk_neutral'))} | {lots_s} |"
            )
        lines.append("")

        for r in show:
            lines.append(f"### {r.get('product_name')}（{r.get('classification')}）")
            lines.append("")
            lines.append(f"- 数据追溯: `{r.get('trace', {})}`")
            lines.append(f"- 行情日: {r.get('quote_date')} · 目标会话: {r.get('target_session')}")
            c, p = r.get("call"), r.get("put")
            if c and p:
                lines.append(
                    f"- 腿: `{c.get('symbol')}` K={c.get('strike')} bid/ask={c.get('bid')}/{c.get('ask')} "
                    f"Δ={c.get('delta'):.3f} IV={c.get('iv'):.3f} slippage={c.get('slippage'):.2f}"
                )
                lines.append(
                    f"- 腿: `{p.get('symbol')}` K={p.get('strike')} bid/ask={p.get('bid')}/{p.get('ask')} "
                    f"Δ={p.get('delta'):.3f} IV={p.get('iv'):.3f} slippage={p.get('slippage'):.2f}"
                )
            m = r.get("margin") or {}
            lines.append(
                f"- 保证金: 单腿C={_num(m.get('call_exchange'),0)} P={_num(m.get('put_exchange'),0)} · "
                f"无优惠={_num(m.get('no_combo_total'),0)} · 理论组合={_num(m.get('combo_theoretical'),0)} "
                f"({m.get('combo_status')}) · 客户预计={_num(m.get('client_estimated'),0) if m.get('client_estimated') else 'N/A'}"
            )
            lines.append(
                f"- 概率: RN到期盈利={_pct(r.get('pop_risk_neutral'))}（**非真实胜率**）· "
                f"Δ近似={_pct(r.get('pop_delta_approx'))} · 历史60日越界={_pct(r.get('hist_breach_rate'))}"
            )
            lines.append(f"- 盈亏平衡区间: [{_num(r.get('breakeven_low'),1)}, {_num(r.get('breakeven_high'),1)}]")
            lines.append(f"- 压力: {r.get('stress', {})}")
            failed = [g for g in (r.get("gates") or []) if not g.get("passed")]
            if failed:
                lines.append("- 未过闸门: " + "; ".join(f"{g['name']}({g['detail']})" for g in failed))
            if r.get("classification_reasons"):
                lines.append("- 分类理由: " + "; ".join(r["classification_reasons"]))
            if r.get("events"):
                lines.append("- 事件: " + "; ".join(r["events"]))
            lines.append("")

    if excl:
        lines.append("## 3. 排除清单（摘要）")
        lines.append("")
        lines.append("| 品种 | 标的 | 原因 |")
        lines.append("|---|---|---|")
        for r in excl[:20]:
            reason = "; ".join(r.get("classification_reasons") or []) or "硬过滤"
            lines.append(f"| {r.get('product_name')} | {r.get('underlying_futures')} | {reason[:120]} |")
        lines.append("")

    lines.append("## 4. 风险提示")
    lines.append("")
    lines.append("1. 卖出宽跨收益限于净权利金；尾部损失可能极大。")
    lines.append("2. 负 Gamma + 负 Vega：趋势行情与 IV 上升将同时恶化损益与保证金。")
    lines.append("3. 涨跌停/跳空、组合优惠失效、产业链共振需在实盘中单独压力测试。")
    lines.append("4. IVR/IVP 若基于 HV 缩放代理序列，**不得**作为推荐依据（闸门已标注）。")
    lines.append("5. 禁止自动下单；下一交易日开盘前重新核验。")
    lines.append("")
    return "\n".join(lines)
