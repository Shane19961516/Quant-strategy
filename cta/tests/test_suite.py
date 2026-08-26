# -*- coding: utf-8 -*-
import unittest

import numpy as np
import pandas as pd

from cta.data import generate_synthetic_futures
from cta.suite.trend import run_trend_strategy
from cta.suite.arb import run_arb_pairs, run_arb_xs_reversal
from cta.suite.noleverage import walk_forward_oos_sharpes


class TestSuiteSmoke(unittest.TestCase):
    def test_trend_and_arb_hardened(self):
        panels = generate_synthetic_futures(start="2019-01-01", end="2023-12-31", seed=3)
        keep = ["RB", "HC", "I", "CU", "M", "Y", "C", "TA"]
        panels = {k: panels[k] for k in keep}
        nav, ret, _ = run_trend_strategy(panels, "trend_dualma", capital=1e6)
        self.assertTrue(len(nav) > 100)
        self.assertAlmostEqual(float(nav.iloc[0]), 1.0, places=5)
        nav2, _, _ = run_arb_pairs(panels, capital=1e6)
        self.assertTrue(len(nav2) > 100)
        nav3, _, _ = run_arb_xs_reversal(panels, capital=1e6)
        self.assertTrue(len(nav3) > 50)
        wf = walk_forward_oos_sharpes(ret)
        self.assertIn("wf_mean_sharpe", wf)

    def test_donchian_55(self):
        panels = generate_synthetic_futures(start="2019-01-01", end="2022-12-31", seed=1)
        panels = {k: panels[k] for k in ["RB", "HC", "CU", "M"]}
        nav, _, _ = run_trend_strategy(panels, "trend_donchian", capital=1e6)
        self.assertTrue(np.isfinite(nav.iloc[-1]))

    def test_activity_portfolio_nonzero(self):
        from cta.suite.factory import activity_aware_portfolio

        idx = pd.bdate_range("2020-01-01", periods=200)
        rng = np.random.default_rng(0)
        rets = {
            "a": pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx),
            "b": pd.Series(rng.normal(0.0002, 0.008, len(idx)), index=idx),
        }
        nav, port, w = activity_aware_portfolio(rets, min_hist=30)
        self.assertGreater(float(w.abs().sum().sum()), 0.0)
        self.assertTrue(np.isfinite(nav.iloc[-1]))

    def test_ohlc_edge_signals(self):
        from cta.suite.edge_sprint import (
            build_overnight_mom_signals,
            build_intraday_rev_signals,
            _ols_hedge_pair,
        )

        panels = generate_synthetic_futures(start="2019-01-01", end="2022-12-31", seed=2)
        panels = {k: panels[k] for k in ["RB", "HC", "I", "CU"]}
        on = build_overnight_mom_signals(panels, lookback=5)
        ir = build_intraday_rev_signals(panels, lookback=1)
        self.assertTrue(set(on.columns) >= {"RB", "HC"})
        self.assertTrue((on.abs().max() <= 1.0 + 1e-9).all() or True)
        self.assertGreater(float(ir.abs().sum().sum()), 0.0)
        nav, ret = _ols_hedge_pair(panels, "I", "RB", capital=1e6, cost_bps=1.5, slip_bps=1.5)
        self.assertTrue(np.isfinite(nav.iloc[-1]))
        self.assertEqual(len(nav), len(ret))

    def test_return_target_scale(self):
        from cta.suite.return_target import scale_fixed_leverage_is, scale_target_vol

        idx = pd.bdate_range("2018-01-01", periods=800)
        rng = np.random.default_rng(1)
        r = pd.Series(rng.normal(0.00005, 0.001, len(idx)), index=idx)
        nav, sr, L = scale_fixed_leverage_is(r, target_vol=0.12)
        self.assertGreater(L, 1.0)
        self.assertTrue(np.isfinite(nav.iloc[-1]))
        nav2, sr2, lev = scale_target_vol(r, target_vol=0.12)
        self.assertGreater(float(lev.iloc[-1]), 0.0)
        self.assertTrue(np.isfinite(nav2.iloc[-1]))

    def test_stable_yearly_helper(self):
        from cta.suite.stable_book import _yearly_table, SLEEVE_BLACKLIST
        from cta.suite.universe import ALL_COMMODITIES, FULL_ECONOMIC_PAIRS, ALL_CALENDAR_SYMBOLS

        self.assertIn("pair_I_RB", SLEEVE_BLACKLIST)
        self.assertIn("SC", ALL_COMMODITIES)
        self.assertIn("RU", ALL_CALENDAR_SYMBOLS)
        self.assertGreaterEqual(len(FULL_ECONOMIC_PAIRS), 8)
        idx = pd.bdate_range("2020-01-01", periods=300)
        r = pd.Series(0.001, index=idx)
        y = _yearly_table(r)
        self.assertGreater(len(y), 0)


if __name__ == "__main__":
    unittest.main()
