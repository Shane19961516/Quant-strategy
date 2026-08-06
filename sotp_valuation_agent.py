"""SOTP valuation agent.

This module provides a lightweight "agent" that performs a Sum-of-the-Parts
valuation based on a list of business segments, then derives enterprise and
equity value using simple capital structure assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Dict, Any
import argparse
import csv
import json
from io import StringIO
from datetime import datetime, timezone


@dataclass
class Segment:
    """A business segment used in SOTP valuation."""

    name: str
    metric_value: float
    valuation_multiple: float
    ownership: float = 1.0

    def implied_value(self) -> float:
        """Return attributable segment value."""
        return self.metric_value * self.valuation_multiple * self.ownership


class SOTPValuationAgent:
    """Agent-style utility for computing SOTP valuation outputs."""

    def __init__(
        self,
        segments: Iterable[Segment],
        net_debt: float = 0.0,
        non_operating_assets: float = 0.0,
        minority_interest: float = 0.0,
        shares_outstanding: float = 1.0,
    ) -> None:
        self.segments: List[Segment] = list(segments)
        self.net_debt = net_debt
        self.non_operating_assets = non_operating_assets
        self.minority_interest = minority_interest
        self.shares_outstanding = shares_outstanding
        self._validate_inputs()

    def _validate_inputs(self) -> None:
        if not self.segments:
            raise ValueError("At least one segment is required.")
        if self.shares_outstanding <= 0:
            raise ValueError("shares_outstanding must be positive.")
        for segment in self.segments:
            if segment.valuation_multiple < 0:
                raise ValueError(
                    f"Segment '{segment.name}' has negative valuation_multiple."
                )
            if not (0 <= segment.ownership <= 1):
                raise ValueError(
                    f"Segment '{segment.name}' ownership must be in [0, 1]."
                )

    def run(self) -> Dict[str, Any]:
        """Compute enterprise value, equity value, and per-share value."""
        segment_rows = []
        enterprise_value = 0.0

        for segment in self.segments:
            value = segment.implied_value()
            enterprise_value += value
            segment_rows.append(
                {
                    "name": segment.name,
                    "metric_value": segment.metric_value,
                    "valuation_multiple": segment.valuation_multiple,
                    "ownership": segment.ownership,
                    "implied_value": value,
                }
            )

        equity_value = (
            enterprise_value
            - self.net_debt
            + self.non_operating_assets
            - self.minority_interest
        )
        per_share_value = equity_value / self.shares_outstanding

        return {
            "segments": segment_rows,
            "enterprise_value": enterprise_value,
            "equity_value": equity_value,
            "per_share_value": per_share_value,
            "inputs": {
                "net_debt": self.net_debt,
                "non_operating_assets": self.non_operating_assets,
                "minority_interest": self.minority_interest,
                "shares_outstanding": self.shares_outstanding,
            },
        }


def _segment_from_dict(raw: Dict[str, Any]) -> Segment:
    return Segment(
        name=str(raw["name"]),
        metric_value=float(raw["metric_value"]),
        valuation_multiple=float(raw["valuation_multiple"]),
        ownership=float(raw.get("ownership", 1.0)),
    )


def run_from_json_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build the agent from JSON-like payload and return outputs."""
    segments = [_segment_from_dict(raw) for raw in payload["segments"]]
    agent = SOTPValuationAgent(
        segments=segments,
        net_debt=float(payload.get("net_debt", 0.0)),
        non_operating_assets=float(payload.get("non_operating_assets", 0.0)),
        minority_interest=float(payload.get("minority_interest", 0.0)),
        shares_outstanding=float(payload.get("shares_outstanding", 1.0)),
    )
    return agent.run()


def _require_yfinance():
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yfinance is required for --ticker mode. Install with: pip install yfinance"
        ) from exc
    return yf


def fetch_yfinance_snapshot(ticker: str) -> Dict[str, float]:
    """Fetch valuation inputs from yfinance."""
    yf = _require_yfinance()
    tk = yf.Ticker(ticker)
    info = tk.info or {}

    total_revenue = float(info.get("totalRevenue") or 0.0)
    shares_outstanding = float(info.get("sharesOutstanding") or 0.0)
    total_debt = float(info.get("totalDebt") or 0.0)
    total_cash = float(info.get("totalCash") or 0.0)
    enterprise_to_revenue = float(info.get("enterpriseToRevenue") or 0.0)
    current_price = float(
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
        or 0.0
    )

    if total_revenue <= 0:
        raise ValueError(f"Could not fetch positive totalRevenue for ticker '{ticker}'.")
    if shares_outstanding <= 0:
        raise ValueError(
            f"Could not fetch positive sharesOutstanding for ticker '{ticker}'."
        )

    # net_debt > 0 means net debt; net_debt < 0 means net cash.
    net_debt = total_debt - total_cash
    if enterprise_to_revenue <= 0:
        enterprise_to_revenue = 6.0

    return {
        "total_revenue": total_revenue,
        "shares_outstanding": shares_outstanding,
        "net_debt": net_debt,
        "enterprise_to_revenue": enterprise_to_revenue,
        "current_price": current_price,
    }


def run_yfinance_scenarios(ticker: str) -> Dict[str, Any]:
    """Run bear/base/bull valuation scenarios using yfinance inputs."""
    snap = fetch_yfinance_snapshot(ticker)
    base_multiple = snap["enterprise_to_revenue"]
    scenario_multiples = {
        "bear": round(base_multiple * 0.8, 4),
        "base": round(base_multiple, 4),
        "bull": round(base_multiple * 1.2, 4),
    }

    results: Dict[str, Any] = {}
    for scenario, multiple in scenario_multiples.items():
        agent = SOTPValuationAgent(
            segments=[
                Segment(
                    name="Revenue Base (yfinance)",
                    metric_value=snap["total_revenue"],
                    valuation_multiple=multiple,
                    ownership=1.0,
                )
            ],
            net_debt=snap["net_debt"],
            shares_outstanding=snap["shares_outstanding"],
        )
        results[scenario] = agent.run()

    return {
        "ticker": ticker.upper(),
        "data_source": "yfinance",
        "snapshot": snap,
        "scenario_multiples": scenario_multiples,
        "note": (
            "yfinance does not provide standardized segment revenue splits for SOTP; "
            "this mode values one revenue segment with scenario multiples."
        ),
        "scenarios": results,
    }


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _build_segment_evidence(seg: Dict[str, Any], chosen_multiple: float) -> Dict[str, Any]:
    """Build argument chain for selected segment multiple."""
    peer_multiples = [float(x) for x in seg.get("peer_multiples", [])]
    peer_median = _median(peer_multiples) if peer_multiples else None
    premium_vs_peer_median = (
        (chosen_multiple / peer_median - 1.0) if peer_median and peer_median > 0 else None
    )

    gross_margin = seg.get("gross_margin")
    market_share = seg.get("market_share")
    growth_rate = seg.get("growth_rate")
    lifecycle_stage = str(seg.get("lifecycle_stage", "unknown")).lower()

    reasons: List[str] = []
    if peer_median is not None:
        diff_pct = (chosen_multiple / peer_median - 1.0) * 100
        reasons.append(
            f"相对可比公司中位数倍数({peer_median:.2f}x)，当前设定为{chosen_multiple:.2f}x，溢价/折价{diff_pct:.1f}%"
        )
    else:
        reasons.append("未提供可比公司倍数，当前倍数主要基于主观假设")

    if gross_margin is not None:
        gm = float(gross_margin)
        reasons.append(f"毛利率假设为{gm:.1%}")
        if gm >= 0.55:
            reasons.append("高毛利通常支持更高估值中枢")
        elif gm <= 0.30:
            reasons.append("毛利率偏低，估值应谨慎给溢价")

    if market_share is not None:
        ms = float(market_share)
        reasons.append(f"市占率假设为{ms:.1%}")
        if ms >= 0.30:
            reasons.append("较高市占率体现规模与渠道壁垒")

    if growth_rate is not None:
        gr = float(growth_rate)
        reasons.append(f"增速假设为{gr:.1%}")
        if gr >= 0.20:
            reasons.append("高增速阶段可支持估值溢价")
        elif gr <= 0.05:
            reasons.append("低增速阶段更接近成熟期估值框架")

    lifecycle_map = {
        "introduction": "导入期",
        "growth": "成长期",
        "mature": "成熟期",
        "decline": "衰退期",
    }
    if lifecycle_stage in lifecycle_map:
        reasons.append(f"业务阶段判断：{lifecycle_map[lifecycle_stage]}")
    else:
        reasons.append("业务阶段未明确，建议补充成长阶段判断")

    return {
        "segment": str(seg.get("name", "unknown")),
        "chosen_multiple": chosen_multiple,
        "peer_multiples": peer_multiples,
        "peer_median_multiple": peer_median,
        "premium_vs_peer_median": premium_vs_peer_median,
        "gross_margin": gross_margin,
        "market_share": market_share,
        "growth_rate": growth_rate,
        "lifecycle_stage": lifecycle_stage,
        "reasons": reasons,
    }


def run_hybrid_yfinance_sotp(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run multi-segment SOTP using yfinance + user segment split assumptions."""
    ticker = str(payload["ticker"]).upper()
    splits_raw = payload["segment_splits"]
    splits: List[Dict[str, Any]] = list(splits_raw)
    if not splits:
        raise ValueError("segment_splits must contain at least one segment.")

    snap = fetch_yfinance_snapshot(ticker)
    total_revenue = snap["total_revenue"]
    split_sum = sum(float(seg["revenue_share"]) for seg in splits)
    if split_sum <= 0:
        raise ValueError("Sum of segment revenue_share values must be positive.")

    # Normalize shares to 1.0 if user passes percentages or imperfect totals.
    normalized_segments: List[Segment] = []
    base_multiple = snap["enterprise_to_revenue"]
    evidence_report = []
    for seg in splits:
        name = str(seg["name"])
        revenue_share = float(seg["revenue_share"]) / split_sum
        seg_multiple = float(seg.get("valuation_multiple", base_multiple))
        evidence_report.append(_build_segment_evidence(seg, seg_multiple))
        normalized_segments.append(
            Segment(
                name=name,
                metric_value=total_revenue * revenue_share,
                valuation_multiple=seg_multiple,
                ownership=float(seg.get("ownership", 1.0)),
            )
        )

    agent = SOTPValuationAgent(
        segments=normalized_segments,
        net_debt=snap["net_debt"],
        shares_outstanding=snap["shares_outstanding"],
    )
    result = agent.run()
    result["ticker"] = ticker
    result["data_source"] = "yfinance+user-segment-splits"
    result["snapshot"] = snap
    result["input_segment_splits"] = splits
    result["evidence_report"] = evidence_report
    return result


def run_hybrid_yfinance_sotp_scenarios(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run bear/base/bull scenarios for hybrid yfinance SOTP."""
    ticker = str(payload["ticker"]).upper()
    splits_raw = payload["segment_splits"]
    splits: List[Dict[str, Any]] = list(splits_raw)
    if not splits:
        raise ValueError("segment_splits must contain at least one segment.")

    bear_factor = float(payload.get("bear_factor", 0.85))
    bull_factor = float(payload.get("bull_factor", 1.15))
    if bear_factor <= 0 or bull_factor <= 0:
        raise ValueError("bear_factor and bull_factor must be positive.")

    base_payload = {"ticker": ticker, "segment_splits": splits}
    base_result = run_hybrid_yfinance_sotp(base_payload)
    snapshot = base_result["snapshot"]
    base_multiple_fallback = float(snapshot["enterprise_to_revenue"])

    scenarios = {}
    scenario_definitions = {
        "bear": bear_factor,
        "base": 1.0,
        "bull": bull_factor,
    }
    for scenario_name, factor in scenario_definitions.items():
        adjusted_splits = []
        for seg in splits:
            segment_multiple = float(seg.get("valuation_multiple", base_multiple_fallback))
            adjusted = dict(seg)
            adjusted["valuation_multiple"] = segment_multiple * factor
            adjusted_splits.append(adjusted)

        scenario_payload = {"ticker": ticker, "segment_splits": adjusted_splits}
        scenarios[scenario_name] = run_hybrid_yfinance_sotp(scenario_payload)

    return {
        "ticker": ticker,
        "data_source": "yfinance+user-segment-splits",
        "factor_assumptions": scenario_definitions,
        "note": (
            "Each scenario scales segment valuation multiples while keeping "
            "segment revenue shares and yfinance balance-sheet/share inputs constant."
        ),
        "scenarios": scenarios,
    }


def _scenario_rows_from_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten scenario JSON into table-like rows."""
    scenarios = result.get("scenarios", {})
    rows: List[Dict[str, Any]] = []
    for scenario_name, data in scenarios.items():
        rows.append(
            {
                "scenario": scenario_name,
                "enterprise_value": float(data.get("enterprise_value", 0.0)),
                "equity_value": float(data.get("equity_value", 0.0)),
                "per_share_value": float(data.get("per_share_value", 0.0)),
            }
        )
    return rows


def render_rows_as_table(rows: List[Dict[str, Any]]) -> str:
    """Render rows as aligned plain-text table."""
    if not rows:
        return "No rows."

    headers = ["scenario", "enterprise_value", "equity_value", "per_share_value"]
    formatted_rows = []
    for row in rows:
        formatted_rows.append(
            {
                "scenario": str(row["scenario"]),
                "enterprise_value": f"{row['enterprise_value']:,.2f}",
                "equity_value": f"{row['equity_value']:,.2f}",
                "per_share_value": f"{row['per_share_value']:,.2f}",
            }
        )

    widths = {h: len(h) for h in headers}
    for row in formatted_rows:
        for h in headers:
            widths[h] = max(widths[h], len(row[h]))

    line = " | ".join(h.ljust(widths[h]) for h in headers)
    sep = "-+-".join("-" * widths[h] for h in headers)
    body = [
        " | ".join(row[h].ljust(widths[h]) for h in headers)
        for row in formatted_rows
    ]
    return "\n".join([line, sep] + body)


def render_rows_as_csv(rows: List[Dict[str, Any]]) -> str:
    """Render rows as CSV text."""
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["scenario", "enterprise_value", "equity_value", "per_share_value"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().strip()


def _fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.1%}"


def _fmt_stage(stage: Any) -> str:
    stage_map = {
        "introduction": "导入期",
        "growth": "成长期",
        "mature": "成熟期",
        "decline": "衰退期",
    }
    key = str(stage).lower()
    return stage_map.get(key, str(stage))


def _build_conclusion_text(result: Dict[str, Any]) -> str:
    scenarios = result.get("scenarios", {})
    if not scenarios:
        return "单点估值结果已生成。"
    bear = scenarios.get("bear", {})
    base = scenarios.get("base", {})
    bull = scenarios.get("bull", {})
    bear_ps = float(bear.get("per_share_value", 0.0))
    base_ps = float(base.get("per_share_value", 0.0))
    bull_ps = float(bull.get("per_share_value", 0.0))
    return (
        f"基准情景每股估值为 {base_ps:,.2f}，"
        f"区间为 {bear_ps:,.2f} ~ {bull_ps:,.2f}。"
        "估值区间主要由分部乘数假设驱动，建议优先复核高溢价板块的论据充分性。"
    )


def _build_risk_flags(result: Dict[str, Any]) -> List[str]:
    scenarios = result.get("scenarios", {})
    base = scenarios.get("base", result)
    evidence = base.get("evidence_report", [])
    flags: List[str] = []
    for item in evidence:
        premium = item.get("premium_vs_peer_median")
        growth = item.get("growth_rate")
        margin = item.get("gross_margin")
        stage = item.get("lifecycle_stage")
        segment = item.get("segment", "unknown")
        if premium is not None and growth is not None and stage in ("mature", "decline"):
            if float(premium) > 0.2 and float(growth) < 0.1:
                flags.append(
                    f"{segment}: 处于{stage}阶段且增速偏低，但相对可比中位数溢价超过20%，需补充论据。"
                )
        if premium is not None and margin is not None:
            if float(premium) > 0.25 and float(margin) < 0.35:
                flags.append(
                    f"{segment}: 毛利率偏低但估值溢价较高，建议下调倍数或强化竞争壁垒论证。"
                )
    if not flags:
        flags.append("未触发自动红旗规则，建议仍进行可比口径一致性复核。")
    return flags


def _build_investment_recommendation(result: Dict[str, Any]) -> Dict[str, Any]:
    """Create a simple investment-view section for the visual report."""
    scenarios = result.get("scenarios", {})
    base = scenarios.get("base", result)
    bear = scenarios.get("bear", {})
    bull = scenarios.get("bull", {})
    base_ps = float(base.get("per_share_value", 0.0))
    bear_ps = float(bear.get("per_share_value", base_ps))
    bull_ps = float(bull.get("per_share_value", base_ps))

    snapshot = base.get("snapshot", result.get("snapshot", {}))
    current_price = float(snapshot.get("current_price", 0.0) or 0.0)

    if current_price <= 0:
        return {
            "stance": "中性",
            "upside_pct": None,
            "downside_pct": None,
            "current_price": None,
            "base_fair_value": base_ps,
            "summary": "未获取到实时价格，无法计算安全边际，暂给中性结论。",
            "triggers": [
                "补充实时价格后重新评估上/下行空间",
                "复核可比公司样本和倍数口径一致性",
                "跟踪核心分部毛利率和增速是否偏离假设",
            ],
        }

    upside_pct = base_ps / current_price - 1.0
    downside_pct = bear_ps / current_price - 1.0
    bull_upside_pct = bull_ps / current_price - 1.0

    if upside_pct >= 0.2 and downside_pct > -0.15:
        stance = "偏积极"
    elif upside_pct <= 0.05:
        stance = "偏谨慎"
    else:
        stance = "中性"

    summary = (
        f"基准估值较现价的空间为 {upside_pct:.1%}，"
        f"悲观情景为 {downside_pct:.1%}，乐观情景为 {bull_upside_pct:.1%}。"
    )
    triggers = [
        "若核心分部增速与毛利率持续上修，可提升目标倍数并上调估值区间",
        "若可比公司中位数倍数下移或竞争加剧，需下调高溢价分部估值",
        "若公司持续回购/优化资本结构，可能提升每股价值兑现速度",
    ]

    return {
        "stance": stance,
        "upside_pct": upside_pct,
        "downside_pct": downside_pct,
        "bull_upside_pct": bull_upside_pct,
        "current_price": current_price,
        "base_fair_value": base_ps,
        "summary": summary,
        "triggers": triggers,
    }


def generate_visual_report(result: Dict[str, Any], output_path: str) -> None:
    """Generate a visual HTML report with conclusions and evidence."""
    title = f"SOTP Valuation Report - {result.get('ticker', 'N/A')}"
    conclusion = _build_conclusion_text(result)
    rows = _scenario_rows_from_result(result)
    if not rows:
        base = result
        rows = [
            {
                "scenario": "base",
                "enterprise_value": float(base.get("enterprise_value", 0.0)),
                "equity_value": float(base.get("equity_value", 0.0)),
                "per_share_value": float(base.get("per_share_value", 0.0)),
            }
        ]

    scenarios = result.get("scenarios", {})
    base_result = scenarios.get("base", result)
    evidence = base_result.get("evidence_report", [])
    flags = _build_risk_flags(result)
    investment_view = _build_investment_recommendation(result)

    table_html = "".join(
        [
            "<tr>"
            f"<td>{r['scenario']}</td>"
            f"<td>{_fmt_money(float(r['enterprise_value']))}</td>"
            f"<td>{_fmt_money(float(r['equity_value']))}</td>"
            f"<td>{float(r['per_share_value']):,.2f}</td>"
            "</tr>"
            for r in rows
        ]
    )

    evidence_cards = []
    assumption_rows = []
    for item in evidence:
        reasons = "".join([f"<li>{reason}</li>" for reason in item.get("reasons", [])])
        peer_med = item.get("peer_median_multiple")
        premium = item.get("premium_vs_peer_median")
        premium_text = "N/A" if premium is None else f"{float(premium) * 100:.1f}%"
        peer_text = "N/A" if peer_med is None else f"{float(peer_med):.2f}x"
        assumption_rows.append(
            "<tr>"
            f"<td>{item.get('segment', 'N/A')}</td>"
            f"<td>{float(item.get('chosen_multiple', 0.0)):.2f}x</td>"
            f"<td>{peer_text}</td>"
            f"<td>{premium_text}</td>"
            f"<td>{_fmt_pct(item.get('gross_margin'))}</td>"
            f"<td>{_fmt_pct(item.get('market_share'))}</td>"
            f"<td>{_fmt_pct(item.get('growth_rate'))}</td>"
            f"<td>{_fmt_stage(item.get('lifecycle_stage', 'N/A'))}</td>"
            "</tr>"
        )
        evidence_cards.append(
            "<div class='card'>"
            f"<h3>{item.get('segment', 'N/A')}</h3>"
            f"<p><b>给定倍数:</b> {float(item.get('chosen_multiple', 0.0)):.2f}x</p>"
            f"<p><b>可比中位数:</b> {peer_text}</p>"
            f"<p><b>相对溢价/折价:</b> {premium_text}</p>"
            f"<p><b>毛利率:</b> {_fmt_pct(item.get('gross_margin'))}</p>"
            f"<p><b>市占率:</b> {_fmt_pct(item.get('market_share'))}</p>"
            f"<p><b>增速:</b> {_fmt_pct(item.get('growth_rate'))}</p>"
            f"<p><b>发展阶段:</b> {_fmt_stage(item.get('lifecycle_stage', 'N/A'))}</p>"
            f"<ul>{reasons}</ul>"
            "</div>"
        )

    flags_html = "".join([f"<li>{flag}</li>" for flag in flags])
    trigger_html = "".join([f"<li>{t}</li>" for t in investment_view.get("triggers", [])])
    upside_text = (
        "N/A"
        if investment_view.get("upside_pct") is None
        else f"{float(investment_view['upside_pct']):.1%}"
    )
    downside_text = (
        "N/A"
        if investment_view.get("downside_pct") is None
        else f"{float(investment_view['downside_pct']):.1%}"
    )
    current_price_text = (
        "N/A"
        if investment_view.get("current_price") is None
        else f"{float(investment_view['current_price']):,.2f}"
    )
    evidence_block = (
        "<p>本次结果未提供 evidence_report，请在 hybrid 模式输入论据字段。</p>"
        if not evidence_cards
        else "".join(evidence_cards)
    )
    assumption_table_block = (
        "<p>未提供论据字段，无法生成估值假设矩阵。</p>"
        if not assumption_rows
        else (
            "<table><thead><tr>"
            "<th>分部</th><th>给定倍数</th><th>可比中位数</th><th>溢价/折价</th>"
            "<th>毛利率</th><th>市占率</th><th>增速</th><th>阶段</th>"
            "</tr></thead><tbody>"
            + "".join(assumption_rows)
            + "</tbody></table>"
        )
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #222; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .meta {{ color: #666; margin-bottom: 20px; }}
    .section {{ margin-top: 20px; }}
    .summary {{ background: #f3f7ff; border-left: 4px solid #4a78ff; padding: 12px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f6f6f6; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; background: #fff; }}
    .risk {{ background: #fff5f5; border-left: 4px solid #e05050; padding: 12px; }}
    .page {{ padding: 6px 0 24px 0; border-bottom: 2px dashed #ddd; margin-bottom: 24px; }}
    .kpi {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .kpi-item {{ background: #f8faff; border: 1px solid #dbe6ff; border-radius: 8px; padding: 10px; }}
    .kpi-label {{ color: #666; font-size: 13px; }}
    .kpi-value {{ font-weight: bold; font-size: 18px; margin-top: 4px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="meta">生成时间：{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC</div>

  <div class="page">
    <h2>第1页：Executive Summary</h2>
    <div class="section summary">
      <h2>结论</h2>
      <p>{conclusion}</p>
    </div>

    <div class="section kpi">
      <div class="kpi-item"><div class="kpi-label">当前立场</div><div class="kpi-value">{investment_view.get("stance", "中性")}</div></div>
      <div class="kpi-item"><div class="kpi-label">现价</div><div class="kpi-value">{current_price_text}</div></div>
      <div class="kpi-item"><div class="kpi-label">基准每股估值</div><div class="kpi-value">{float(investment_view.get("base_fair_value", 0.0)):,.2f}</div></div>
      <div class="kpi-item"><div class="kpi-label">上行空间(Base)</div><div class="kpi-value">{upside_text}</div></div>
      <div class="kpi-item"><div class="kpi-label">下行情景(Bear)</div><div class="kpi-value">{downside_text}</div></div>
    </div>

    <div class="section">
      <h2>情景估值结果</h2>
      <table>
        <thead>
          <tr><th>Scenario</th><th>Enterprise Value</th><th>Equity Value</th><th>Per Share</th></tr>
        </thead>
        <tbody>{table_html}</tbody>
      </table>
    </div>

    <div class="section summary">
      <h3>后续跟踪触发条件</h3>
      <ul>{trigger_html}</ul>
    </div>
  </div>

  <div class="page">
    <h2>第2页：估值假设矩阵</h2>
    <div class="section">
      {assumption_table_block}
    </div>
    <div class="section">
      <h3>分部论据卡片（Base情景）</h3>
      <div class="cards">{evidence_block}</div>
    </div>
  </div>

  <div class="page">
    <h2>第3页：风险与反证（What could go wrong）</h2>
    <div class="section risk">
      <h2>关键风险提示</h2>
      <ul>{flags_html}</ul>
    </div>
    <div class="section">
      <h3>反证检查清单</h3>
      <ul>
        <li>可比公司样本是否存在业务结构错配（硬件/软件、国内/海外、成长/成熟）？</li>
        <li>分部毛利率与增速是否可持续，是否受到周期/监管/竞争冲击？</li>
        <li>高溢价分部是否有可验证壁垒（品牌、生态、渠道、技术、成本）？</li>
        <li>估值倍数是否隐含过高终值假设，是否与历史分位偏离过大？</li>
        <li>资本结构变化（回购、并购、债务）对每股价值传导是否被高估？</li>
      </ul>
    </div>
  </div>

</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def _require_reportlab():
    try:
        from reportlab.lib import colors  # noqa: F401
        from reportlab.lib.pagesizes import A4  # noqa: F401
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: F401
        from reportlab.pdfbase import pdfmetrics  # noqa: F401
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # noqa: F401
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            PageBreak,
        )  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "reportlab is required for --pdf-out mode. Install with: pip install reportlab"
        ) from exc


def generate_pdf_report(result: Dict[str, Any], output_path: str) -> None:
    """Generate a polished committee-style PDF report."""
    _require_reportlab()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
    )

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=32,
        bottomMargin=32,
        title=f"SOTP Report - {result.get('ticker', 'N/A')}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCN",
        parent=styles["Title"],
        fontName="STSong-Light",
        fontSize=20,
        textColor=colors.HexColor("#1f3a6e"),
        spaceAfter=12,
    )
    heading_style = ParagraphStyle(
        "HeadingCN",
        parent=styles["Heading2"],
        fontName="STSong-Light",
        fontSize=14,
        textColor=colors.HexColor("#1f3a6e"),
        spaceBefore=10,
        spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "BodyCN",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=10.5,
        leading=15,
    )
    small_style = ParagraphStyle(
        "SmallCN",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=9.5,
        textColor=colors.HexColor("#555555"),
    )

    story = []
    ticker = result.get("ticker", "N/A")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    story.append(Paragraph(f"SOTP 估值报告（审委会版）- {ticker}", title_style))
    story.append(Paragraph(f"生成时间：{generated_at}", small_style))
    story.append(Spacer(1, 10))

    # Page 1: Executive summary
    story.append(Paragraph("第1页：Executive Summary", heading_style))
    conclusion = _build_conclusion_text(result)
    investment_view = _build_investment_recommendation(result)
    story.append(Paragraph(f"<b>结论：</b>{conclusion}", body_style))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            (
                f"<b>投资立场：</b>{investment_view.get('stance', '中性')} ｜ "
                f"<b>现价：</b>"
                f"{'N/A' if investment_view.get('current_price') is None else f'{float(investment_view.get('current_price')):,.2f}'} ｜ "
                f"<b>基准每股估值：</b>{float(investment_view.get('base_fair_value', 0.0)):,.2f}"
            ),
            body_style,
        )
    )
    story.append(Spacer(1, 8))

    rows = _scenario_rows_from_result(result)
    if not rows:
        rows = [
            {
                "scenario": "base",
                "enterprise_value": float(result.get("enterprise_value", 0.0)),
                "equity_value": float(result.get("equity_value", 0.0)),
                "per_share_value": float(result.get("per_share_value", 0.0)),
            }
        ]
    scenario_table_data = [["Scenario", "Enterprise Value", "Equity Value", "Per Share"]]
    for r in rows:
        scenario_table_data.append(
            [
                str(r["scenario"]),
                _fmt_money(float(r["enterprise_value"])),
                _fmt_money(float(r["equity_value"])),
                f"{float(r['per_share_value']):,.2f}",
            ]
        )
    scenario_table = Table(scenario_table_data, colWidths=[72, 150, 150, 100])
    scenario_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf1ff")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f3a6e")),
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c7d5f0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fbff")]),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(scenario_table)
    story.append(Spacer(1, 8))
    story.append(Paragraph("<b>跟踪触发条件：</b>", body_style))
    for trigger in investment_view.get("triggers", []):
        story.append(Paragraph(f"• {trigger}", body_style))

    # Page 2: assumption matrix + evidence
    story.append(PageBreak())
    story.append(Paragraph("第2页：估值假设矩阵与论据", heading_style))
    scenarios = result.get("scenarios", {})
    base_result = scenarios.get("base", result)
    evidence = base_result.get("evidence_report", [])
    if evidence:
        assumption_data = [[
            "分部", "给定倍数", "可比中位数", "溢价/折价", "毛利率", "市占率", "增速", "阶段"
        ]]
        for item in evidence:
            peer_med = item.get("peer_median_multiple")
            premium = item.get("premium_vs_peer_median")
            assumption_data.append(
                [
                    str(item.get("segment", "N/A")),
                    f"{float(item.get('chosen_multiple', 0.0)):.2f}x",
                    "N/A" if peer_med is None else f"{float(peer_med):.2f}x",
                    "N/A" if premium is None else f"{float(premium) * 100:.1f}%",
                    _fmt_pct(item.get("gross_margin")),
                    _fmt_pct(item.get("market_share")),
                    _fmt_pct(item.get("growth_rate")),
                    _fmt_stage(item.get("lifecycle_stage", "N/A")),
                ]
            )
        assumption_table = Table(
            assumption_data,
            colWidths=[90, 62, 70, 64, 52, 52, 50, 52],
        )
        assumption_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef7ee")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#2e6b3f")),
                    ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9e1cc")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fff8")]),
                    ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ]
            )
        )
        story.append(assumption_table)
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>论据摘要：</b>", body_style))
        for item in evidence:
            story.append(
                Paragraph(
                    f"<b>{item.get('segment', 'N/A')}</b>（{float(item.get('chosen_multiple', 0.0)):.2f}x）",
                    body_style,
                )
            )
            for reason in item.get("reasons", [])[:4]:
                story.append(Paragraph(f"• {reason}", body_style))
            story.append(Spacer(1, 4))
    else:
        story.append(
            Paragraph("未提供 evidence_report。请使用 hybrid 输入 peer_multiples/毛利/市占率/增速/阶段字段。", body_style)
        )

    # Page 3: risk and falsification
    story.append(PageBreak())
    story.append(Paragraph("第3页：风险与反证（What could go wrong）", heading_style))
    flags = _build_risk_flags(result)
    story.append(Paragraph("<b>关键风险提示：</b>", body_style))
    for flag in flags:
        story.append(Paragraph(f"• {flag}", body_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph("<b>反证检查清单：</b>", body_style))
    checklist = [
        "可比公司样本是否存在结构错配（地域、成长性、商业模式）？",
        "分部毛利率与增速是否可持续，是否有下行压力？",
        "高溢价分部是否有可验证壁垒（品牌、生态、渠道、技术）？",
        "当前倍数与历史分位是否偏离过大？",
        "资本结构变化对每股价值的传导是否被高估？",
    ]
    for item in checklist:
        story.append(Paragraph(f"• {item}", body_style))

    doc.build(story)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SOTP valuation agent.")
    parser.add_argument(
        "payload",
        nargs="?",
        help="JSON payload for manual SOTP run.",
    )
    parser.add_argument(
        "--ticker",
        help="Ticker symbol for yfinance-powered scenario valuation.",
    )
    parser.add_argument(
        "--hybrid-payload",
        help=(
            "JSON payload for hybrid SOTP. "
            "Example: "
            "'{\"ticker\":\"AAPL\",\"segment_splits\":[{\"name\":\"Products\","
            "\"revenue_share\":0.75,\"valuation_multiple\":6.5},"
            "{\"name\":\"Services\",\"revenue_share\":0.25,\"valuation_multiple\":12.0}]}'"
        ),
    )
    parser.add_argument(
        "--hybrid-scenarios-payload",
        help=(
            "JSON payload for hybrid bear/base/bull scenarios. "
            "Example: "
            "'{\"ticker\":\"AAPL\",\"segment_splits\":[{\"name\":\"Products\","
            "\"revenue_share\":0.75,\"valuation_multiple\":6.5},"
            "{\"name\":\"Services\",\"revenue_share\":0.25,\"valuation_multiple\":12.0}],"
            "\"bear_factor\":0.85,\"bull_factor\":1.15}'"
        ),
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "table", "csv"],
        default="json",
        help="Output format for scenario modes.",
    )
    parser.add_argument(
        "--report-out",
        help="Output path for visual HTML report (e.g. ./sotp_report.html).",
    )
    parser.add_argument(
        "--pdf-out",
        help="Output path for visual PDF report (e.g. ./sotp_report.pdf).",
    )
    args = parser.parse_args()

    is_scenario_mode = False
    if args.hybrid_scenarios_payload:
        hybrid_scenarios_payload = json.loads(args.hybrid_scenarios_payload)
        result = run_hybrid_yfinance_sotp_scenarios(hybrid_scenarios_payload)
        is_scenario_mode = True
    elif args.hybrid_payload:
        hybrid_payload = json.loads(args.hybrid_payload)
        result = run_hybrid_yfinance_sotp(hybrid_payload)
    elif args.ticker:
        result = run_yfinance_scenarios(args.ticker)
        is_scenario_mode = True
    elif args.payload:
        payload = json.loads(args.payload)
        result = run_from_json_payload(payload)
    else:
        raise SystemExit(
            "Usage: python sotp_valuation_agent.py '<json-payload>' OR "
            "python sotp_valuation_agent.py --ticker AAPL OR "
            "python sotp_valuation_agent.py --hybrid-payload '<json-payload>' OR "
            "python sotp_valuation_agent.py --hybrid-scenarios-payload '<json-payload>'"
        )

    if args.report_out:
        generate_visual_report(result, args.report_out)
        print(f"Visual HTML report generated: {args.report_out}")
    if args.pdf_out:
        generate_pdf_report(result, args.pdf_out)
        print(f"Visual PDF report generated: {args.pdf_out}")
    if args.report_out or args.pdf_out:
        pass
    elif args.output_format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.output_format == "table":
        if not is_scenario_mode:
            raise SystemExit("--output-format table is supported only for scenario modes.")
        rows = _scenario_rows_from_result(result)
        print(render_rows_as_table(rows))
    else:  # csv
        if not is_scenario_mode:
            raise SystemExit("--output-format csv is supported only for scenario modes.")
        rows = _scenario_rows_from_result(result)
        print(render_rows_as_csv(rows))
