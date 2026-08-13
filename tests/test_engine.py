"""Unit tests for BS76 engine, vol metrics, screener, and allocator."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.bs76_engine import black76_greeks, black76_price, implied_volatility
from core.capital_allocator import allocate_strangle, short_option_margin, short_strangle_margin
from core.metrics import hv30, iv_hv_spread, iv_percentile, iv_rank, pop_approx, pop_lognormal
from core.screener import OptionContract, UnderlyingSnapshot, pair_delta_strikes, run_screener
from data_fetcher.market_data import generate_demo_snapshots


class TestBlack76:
    def test_atm_call_put_parity_style(self):
        F, K, T, r, sigma = 100.0, 100.0, 0.5, 0.02, 0.25
        call = black76_price(F, K, T, r, sigma, "CALL")
        put = black76_price(F, K, T, r, sigma, "PUT")
        # For ATM futures options under Black76: C ≈ P when F=K
        assert abs(call - put) < 1e-10

    def test_call_delta_bounds(self):
        g = black76_greeks(100, 100, 0.5, 0.02, 0.25, "CALL")
        assert 0.4 < g.delta < 0.6
        assert g.gamma > 0
        assert g.vega > 0

    def test_put_delta_negative(self):
        g = black76_greeks(100, 100, 0.5, 0.02, 0.25, "PUT")
        assert -0.6 < g.delta < -0.4

    def test_known_reference_values(self):
        """
        Cross-check against independently computed Black-76 values.
        F=100, K=100, T=1, r=0, sigma=0.2
        d1 = 0.1, d2 = -0.1
        Call = F*N(d1)-K*N(d2) = 100*(N(0.1)-N(-0.1)) ≈ 7.9656
        """
        from scipy.stats import norm

        F, K, T, r, sigma = 100.0, 100.0, 1.0, 0.0, 0.2
        d1 = 0.1
        expected_call = F * norm.cdf(d1) - K * norm.cdf(d1 - sigma)
        price = black76_price(F, K, T, r, sigma, "CALL")
        assert abs(price - expected_call) < 1e-8

        g = black76_greeks(F, K, T, r, sigma, "CALL")
        expected_delta = math.exp(-r * T) * norm.cdf(d1)
        assert abs(g.delta - expected_delta) < 1e-8
        expected_gamma = math.exp(-r * T) * norm.pdf(d1) / (F * sigma * math.sqrt(T))
        assert abs(g.gamma - expected_gamma) < 1e-10
        expected_vega = F * math.exp(-r * T) * norm.pdf(d1) * math.sqrt(T) * 0.01
        assert abs(g.vega - expected_vega) < 1e-10

    def test_implied_vol_roundtrip(self):
        F, K, T, r, sigma = 120.0, 115.0, 40 / 365, 0.02, 0.33
        px = black76_price(F, K, T, r, sigma, "CALL")
        iv = implied_volatility(px, F, K, T, r, "CALL")
        assert abs(iv - sigma) < 1e-5

    def test_gamma_call_equals_put(self):
        gc = black76_greeks(100, 95, 0.4, 0.01, 0.22, "CALL")
        gp = black76_greeks(100, 95, 0.4, 0.01, 0.22, "PUT")
        assert abs(gc.gamma - gp.gamma) < 1e-12
        assert abs(gc.vega - gp.vega) < 1e-12


class TestMetrics:
    def test_hv30_constant_prices(self):
        prices = [100.0] * 40
        # zero returns => HV = 0
        assert hv30(prices) == 0.0

    def test_hv30_known_series(self):
        rng = np.random.default_rng(0)
        # geometric path with daily vol ~ 1%
        rets = rng.normal(0, 0.01, size=60)
        prices = 100 * np.exp(np.cumsum(rets))
        prices = np.insert(prices, 0, 100.0)
        hv = hv30(prices.tolist())
        # annualized ~ 0.01 * sqrt(252) ≈ 0.1587
        assert 0.10 < hv < 0.25

    def test_iv_rank_and_percentile(self):
        hist = list(np.linspace(0.10, 0.30, 252))
        current = 0.28
        ivr = iv_rank(current, hist)
        ivp = iv_percentile(current, hist)
        assert 85.0 < ivr < 95.0
        assert ivp > 85.0

    def test_iv_hv_spread(self):
        assert abs(iv_hv_spread(0.32, 0.22) - 0.10) < 1e-12

    def test_pop_approx(self):
        assert abs(pop_approx(0.20, -0.20) - 0.60) < 1e-12

    def test_pop_lognormal_wide_strikes(self):
        # very wide strangle should have high POP
        pop = pop_lognormal(F=100, K_put=70, K_call=130, T=0.1, sigma=0.2)
        assert pop > 0.95


class TestAllocator:
    def test_short_option_margin_positive(self):
        m = short_option_margin(45.5, 3000, 3200, "CALL", multiplier=10, underlying_margin_rate=0.10)
        assert m > 45.5 * 10

    def test_strangle_combo_and_sizing(self):
        margin = short_strangle_margin(
            F=3000,
            call_strike=3200,
            put_strike=2800,
            call_premium=45,
            put_premium=38,
            multiplier=10,
            underlying_margin_rate=0.10,
        )
        assert margin.unit_margin > 0
        assert margin.total_premium_cash == (45 + 38) * 10

        alloc = allocate_strangle(
            3000,
            3200,
            2800,
            45,
            38,
            total_equity=100_000,
            max_allocation_per_symbol=0.30,
            product="m",
            exchange="DCE",
            multiplier=10,
        )
        assert alloc.max_pairs >= 1
        assert alloc.expected_roi > 0

    def test_margin_cap_blocks(self):
        alloc = allocate_strangle(
            3000,
            3200,
            2800,
            45,
            38,
            total_equity=100_000,
            max_allocation_per_symbol=0.30,
            current_margin_used=70_000,  # 70% > 60%
            max_margin_usage=0.60,
            product="m",
            exchange="DCE",
            multiplier=10,
        )
        assert alloc.blocked_by_margin_cap is True
        assert alloc.max_pairs == 0


class TestScreener:
    def test_pair_delta_near_020(self):
        snaps = generate_demo_snapshots(seed=1)
        ag = next(s for s in snaps if s.underlying.startswith("AG"))
        pair = pair_delta_strikes(ag.contracts)
        assert pair is not None
        call, put = pair
        assert abs(call.delta - 0.20) < 0.08
        assert abs(put.delta - (-0.20)) < 0.08

    def test_demo_screener_finds_elevated_iv(self):
        snaps = generate_demo_snapshots(seed=42)
        results = run_screener(snaps)
        underlyings = {r.underlying for r in results}
        # CU should fail; AG/M should typically pass
        assert "CU2609" not in underlyings
        assert len(results) >= 1
        for r in results:
            assert 30 <= r.dte <= 45
            assert r.iv_rank > 50
            assert r.iv_percentile > 70
            assert r.iv_hv_spread > 0.05
            assert r.max_pairs >= 0
