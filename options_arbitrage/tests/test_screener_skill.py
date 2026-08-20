"""Tests for technical regime filters and event calendar."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.events import MarketEvent, filter_product_events
from core.technicals import evaluate_ranging_regime
from core.screener import run_screener, run_screener_with_rejects
from data_fetcher.market_data import generate_demo_snapshots
from report.daily_report import build_markdown_report


class TestTechnicals:
    def test_ranging_path_passes(self):
        rng = np.random.default_rng(1)
        n = 120
        prices = 100 * (1.0 + rng.normal(0, 0.0015, size=n))
        prices = prices * (100 / prices[-1])
        highs = (prices * 1.001).tolist()
        lows = (prices * 0.999).tolist()
        tech = evaluate_ranging_regime(prices.tolist(), highs=highs, lows=lows)
        assert tech.adx < 20
        assert tech.is_ranging

    def test_trending_path_fails(self):
        prices = np.linspace(80, 120, 120).tolist()
        tech = evaluate_ranging_regime(prices)
        assert not tech.is_ranging
        assert tech.reasons


class TestEvents:
    def test_high_event_blocks_product(self):
        extra = (MarketEvent(date(2026, 8, 22), "测试高压事件", "HIGH", ("m",)),)
        res = filter_product_events("m", as_of=date(2026, 8, 20), extra_events=extra)
        assert res.blocked is True

    def test_exclude_events_disabled(self):
        extra = (MarketEvent(date(2026, 8, 22), "测试", "HIGH", ("m",)),)
        res = filter_product_events("m", as_of=date(2026, 8, 20), exclude_events=False, extra_events=extra)
        assert res.blocked is False


class TestReportAndPipeline:
    def test_report_builds(self):
        snaps = generate_demo_snapshots(seed=7)
        cands, rejects, iv_passed = run_screener_with_rejects(snaps, exclude_events=False)
        md = build_markdown_report(
            scan_meta={"generated_at": "2026-08-20", "data_source": "demo", "params_summary": "test"},
            universe_stats={
                "scanned": len(snaps),
                "iv_passed": len(iv_passed),
                "liquidity_passed": 0,
                "ranging_passed": 0,
                "event_passed": 0,
                "recommended": len(cands),
            },
            iv_passed=iv_passed,
            recommendations=[c.to_dict() for c in cands],
            rejected=[r.to_dict() for r in rejects],
        )
        assert "卖出宽跨式" in md
        assert "风险提示" in md

    def test_trending_filtered_by_technical(self):
        snaps = generate_demo_snapshots(seed=42)
        results = run_screener(snaps, exclude_events=False, require_ranging=True)
        # SR601 constructed as trending elevated-IV — should not appear
        assert all(r.underlying != "SR601" for r in results)
