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


if __name__ == "__main__":
    unittest.main()
