# -*- coding: utf-8 -*-
"""用户定义策略单元测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.book.strategies_12 import BookConfig, bollinger_fade_signal, ma_cross_mid_signal, run_s1
from cta.book.strategies_3 import run_s3
from cta.book.strategies_4 import calendar_spread_signal
from cta.book.engine import run_defined_book
from cta.data import generate_synthetic_futures


class TestDefinedSignals(unittest.TestCase):
    def test_ma_improved_has_positions(self):
        idx = pd.bdate_range("2020-01-01", periods=200)
        x = np.linspace(0, 6 * np.pi, len(idx))
        close = pd.Series(100 + np.cumsum(0.2 * np.sin(x) + 0.05), index=idx)
        high = close + 1.0
        low = close - 1.0
        sig = ma_cross_mid_signal(close, high, low)
        self.assertTrue(set(np.unique(sig)) <= {-1.0, 0.0, 1.0})

    def test_bollinger_still_callable(self):
        idx = pd.bdate_range("2020-01-01", periods=120)
        rng = np.random.default_rng(0)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1, len(idx))), index=idx)
        sig = bollinger_fade_signal(close, 20, 2.0, 4.0)
        self.assertTrue(set(np.unique(sig)) <= {-1.0, 0.0, 1.0})

    def test_calendar_z(self):
        idx = pd.bdate_range("2020-01-01", periods=100)
        spread = pd.Series(np.sin(np.linspace(0, 10, len(idx))) * 3, index=idx)
        sig = calendar_spread_signal(spread, 20, 2.0, 0.0, 4.0)
        self.assertEqual(len(sig), len(spread))

    def test_run_s1_s3_margin_and_book(self):
        panels = generate_synthetic_futures(start="2019-01-01", end="2023-12-31", seed=2)
        keep = ["RB", "HC", "CU", "AU", "M", "Y", "C", "TA"]
        panels = {k: panels[k] for k in keep}
        cfg = BookConfig()
        _, _, _, s1, _ = run_s1(panels, cfg)
        _, _, _, s3, _ = run_s3(panels, cfg)
        self.assertLessEqual(s1["max_margin"], cfg.margin_s1 + 1e-6)
        self.assertLessEqual(s3["max_margin"], cfg.margin_s3 + 1e-6)
        res = run_defined_book(panels, cfg=cfg, fetch_contracts=False)
        self.assertTrue(np.isfinite(res.nav_total.iloc[-1]))
        self.assertNotIn("nav_s2", res.__dataclass_fields__)


if __name__ == "__main__":
    unittest.main()
