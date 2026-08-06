"""SOTP valuation agent.

This module provides a lightweight "agent" that performs a Sum-of-the-Parts
valuation based on a list of business segments, then derives enterprise and
equity value using simple capital structure assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Dict, Any
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


if __name__ == "__main__":
    # Example payload for a quick local run:
    # python sotp_valuation_agent.py '{"segments":[...],"net_debt":...}'
    import sys

    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python sotp_valuation_agent.py '<json-payload>'"
        )

    payload = json.loads(sys.argv[1])
    result = run_from_json_payload(payload)
    print(json.dumps(result, indent=2))
