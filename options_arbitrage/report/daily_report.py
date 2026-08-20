"""Markdown report builder for short-strangle daily scans."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def _fmt(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:,.{digits}f}"


def build_markdown_report(
    *,
    scan_meta: dict[str, Any],
    universe_stats: dict[str, Any],
    iv_passed: Sequence[dict[str, Any]],
    recommendations: Sequence[dict[str, Any]],
    rejected: Sequence[dict[str, Any]] | None = None,
) -> str:
    """
    Skill step 9 output structure:
    1) market overview  2) recommendations  3) detail tables  4) risk notes
    """
    now = scan_meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    source = scan_meta.get("data_source", "unknown")
    lines: list[str] = []
    lines.append("# 卖出宽跨式（Short Strangle）机会扫描报告")
    lines.append("")
    lines.append(f"- 生成时间：{now}")
    lines.append(f"- 数据来源：{source}")
    lines.append(f"- 扫描参数：{scan_meta.get('params_summary', '-')}")
    lines.append("")
    lines.append("> 本报告仅供辅助决策，不构成投资建议。实盘前请核对交易所最新保证金、限仓与公告。")
    lines.append("")

    # 1. Overview
    lines.append("## 1. 市场概览")
    lines.append("")
    lines.append(f"- 扫描品种数：**{universe_stats.get('scanned', 0)}**")
    lines.append(f"- 通过 IV 条件：**{universe_stats.get('iv_passed', 0)}**")
    lines.append(f"- 通过流动性：**{universe_stats.get('liquidity_passed', 0)}**")
    lines.append(f"- 通过震荡格局：**{universe_stats.get('ranging_passed', 0)}**")
    lines.append(f"- 事件过滤后剩余：**{universe_stats.get('event_passed', 0)}**")
    lines.append(f"- 最终推荐：**{universe_stats.get('recommended', 0)}**")
    lines.append("")

    if iv_passed:
        lines.append("### 符合 IV 条件的品种")
        lines.append("")
        lines.append("| 品种 | 标的 | 当前IV | IV Rank | IV Percentile | IV-HV | HV30 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for row in iv_passed:
            lines.append(
                "| {name} | {und} | {iv} | {ivr:.1f} | {ivp:.1f} | {spread} | {hv} |".format(
                    name=row.get("product_name", row.get("product", "")),
                    und=row.get("underlying", ""),
                    iv=_pct(row.get("current_iv", 0.0)),
                    ivr=row.get("iv_rank", 0.0),
                    ivp=row.get("iv_percentile", 0.0),
                    spread=_pct(row.get("iv_hv_spread", 0.0)),
                    hv=_pct(row.get("hv30", 0.0)),
                )
            )
        lines.append("")

    # 2. Recommendations
    lines.append("## 2. 最终推荐品种及理由")
    lines.append("")
    if not recommendations:
        lines.append("今日无完全满足全部硬性条件的卖出宽跨候选。可放宽 `iv_rank_min` / 技术面阈值后复扫，或等待波动率抬升。")
        lines.append("")
    else:
        for i, rec in enumerate(recommendations, 1):
            lines.append(
                f"### {i}. {rec.get('product_name', rec.get('product', ''))}（{rec.get('underlying', '')}）"
            )
            lines.append("")
            lines.append(rec.get("rationale", "高 IV 溢价 + 震荡格局 + 流动性合格。"))
            lines.append("")

    # 3. Detail tables
    lines.append("## 3. 推荐组合明细")
    lines.append("")
    if recommendations:
        lines.append(
            "| 品种 | 月份/DTE | 卖Call | Call权利金 | CallΔ | 卖Put | Put权利金 | PutΔ | "
            "权利金收入 | 组合保证金 | 权/保比 | 组合Δ | Γ | Θ | Vega | 胜率 |"
        )
        lines.append("|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for rec in recommendations:
            lines.append(
                "| {name} | {month}/{dte}d | {ck} | {cp} | {cd:.3f} | {pk} | {pp} | {pd:.3f} | "
                "{prem} | {marg} | {ratio} | {netd:.3f} | {g:.4f} | {th:.2f} | {v:.2f} | {pop} |".format(
                    name=rec.get("product_name", rec.get("product", "")),
                    month=rec.get("option_month", ""),
                    dte=rec.get("dte", ""),
                    ck=int(rec.get("call_strike", 0)),
                    cp=_fmt(rec.get("call_premium"), 1),
                    cd=rec.get("call_delta", 0.0),
                    pk=int(rec.get("put_strike", 0)),
                    pp=_fmt(rec.get("put_premium"), 1),
                    pd=rec.get("put_delta", 0.0),
                    prem=_fmt(rec.get("premium_cash"), 0),
                    marg=_fmt(rec.get("unit_margin"), 0),
                    ratio=_pct(rec.get("premium_margin_ratio", 0.0)),
                    netd=rec.get("net_delta", 0.0),
                    g=rec.get("net_gamma", 0.0),
                    th=rec.get("net_theta", 0.0),
                    v=rec.get("net_vega", 0.0),
                    pop=_pct(rec.get("pop", 0.0)),
                )
            )
        lines.append("")

        for rec in recommendations:
            lines.append(f"#### {rec.get('product_name', '')} 细节")
            lines.append("")
            lines.append(f"- 标的价格 F = **{_fmt(rec.get('F'), 2)}**")
            lines.append(
                f"- Call `{rec.get('call_symbol', '')}` / Put `{rec.get('put_symbol', '')}`"
            )
            lines.append(
                f"- IV Rank={rec.get('iv_rank', 0):.1f}，IV Percentile={rec.get('iv_percentile', 0):.1f}，"
                f"IV-HV={_pct(rec.get('iv_hv_spread', 0.0))}"
            )
            lines.append(
                f"- 技术面：ADX={_fmt(rec.get('adx'), 1)}，%B={_fmt(rec.get('bb_pct_b'), 2)}，"
                f"30日区间 [{_fmt(rec.get('range_low_30'), 1)}, {_fmt(rec.get('range_high_30'), 1)}]"
            )
            lines.append(f"- 建议手数（按资金约束）：**{rec.get('max_pairs', 0)}** 对")
            if rec.get("notes"):
                lines.append(f"- 备注：{rec['notes']}")
            lines.append(
                "- 风险：卖出宽跨收益有限、亏损理论上巨大；建议权利金上涨 50–100% 或突破关键区间时止损；"
                "可考虑铁鹰式保护尾部。"
            )
            lines.append("")

    if rejected:
        lines.append("## 附录：接近条件但被过滤的品种")
        lines.append("")
        lines.append("| 品种 | 标的 | 主要拒绝原因 |")
        lines.append("|---|---|---|")
        for row in rejected[:30]:
            lines.append(
                f"| {row.get('product_name', row.get('product', ''))} | {row.get('underlying', '')} | "
                f"{row.get('reject_reason', '')} |"
            )
        lines.append("")

    # 4. Risk
    lines.append("## 4. 风险提示与交易建议")
    lines.append("")
    lines.append("1. **收益有限、风险巨大**：卖出宽跨最大盈利为收取的权利金，标的单边大幅波动时亏损可迅速扩大。")
    lines.append("2. **仓位**：单品种保证金占用建议不超过总资金的 20–30%；账户总占用建议 < 60%。")
    lines.append("3. **止损**：权利金上涨 50–100%，或标的突破建仓时震荡区间、IV 继续攀升时，优先减仓/平仓。")
    lines.append("4. **美式提前行权**：国内商品期权多为美式，深度实值时存在被提前行权风险。")
    lines.append("5. **夜盘与外盘**：贵金属、原油、有色等需警惕隔夜外盘跳空；事件窗口建议空仓或轻仓。")
    lines.append("6. **保证金优惠**：组合保证金规则可能调整，下单前以期货公司试算为准。")
    lines.append("7. **替代结构**：若担心尾部风险，可用铁鹰式（Iron Condor）买入更虚值期权保护。")
    lines.append("")
    return "\n".join(lines)
