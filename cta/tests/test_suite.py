# -*- coding: utf-8 -*-
import unittest

from cta.data import generate_synthetic_futures
from cta.suite.trend import run_trend_strategy
from cta.suite.arb import run_arb_pairs, run_arb_bollinger


class TestSuiteSmoke(unittest.TestCase):
    def test_trend_and_arb_noleverage(self):
        panels = generate_synthetic_futures(start="2019-01-01", end="2022-12-31", seed=3)
        keep = ["RB", "HC", "I", "CU", "M", "Y", "C", "TA"]
        panels = {k: panels[k] for k in keep}
        nav, ret, _ = run_trend_strategy(panels, "trend_tsmom", capital=1e6)
        self.assertTrue(len(nav) > 100)
        self.assertAlmostEqual(float(nav.iloc[0]), 1.0, places=5)
        nav2, ret2, _ = run_arb_pairs(panels, capital=1e6)
        self.assertTrue(len(nav2) > 100)
        nav3, _, _ = run_arb_bollinger(panels, capital=1e6)
        self.assertTrue(len(nav3) > 50)


if __name__ == "__main__":
    unittest.main()
