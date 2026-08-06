"""SOTP valuation agent.

This module provides a lightweight "agent" that performs a Sum-of-the-Parts
valuation based on a list of business segments, then derives enterprise and
equity value using simple capital structure assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Dict, Any
import argparse
import json


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
    for seg in splits:
        name = str(seg["name"])
        revenue_share = float(seg["revenue_share"]) / split_sum
        seg_multiple = float(seg.get("valuation_multiple", base_multiple))
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
    args = parser.parse_args()

    if args.hybrid_scenarios_payload:
        hybrid_scenarios_payload = json.loads(args.hybrid_scenarios_payload)
        result = run_hybrid_yfinance_sotp_scenarios(hybrid_scenarios_payload)
    elif args.hybrid_payload:
        hybrid_payload = json.loads(args.hybrid_payload)
        result = run_hybrid_yfinance_sotp(hybrid_payload)
    elif args.ticker:
        result = run_yfinance_scenarios(args.ticker)
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

    print(json.dumps(result, indent=2))
