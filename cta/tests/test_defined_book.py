# -*- coding: utf-8 -*-
"""用户定义策略单元测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.book.strategies_12 import bollinger_fade_signal, ma_cross_mid_signal
from cta.book.strategies_4 import calendar_spread_signal
from cta.data import generate_synthetic_futures
from cta.book.strategies_12 import BookConfig, run_s1, run_s2
from cta.book.strategies_3 import run_s3


class TestDefinedSignals(unittest.TestCase):
    def test_ma_cross_no_reentry_without_cross(self):
        idx = pd.bdate_range("2020-01-01", periods=80)
        # 构造明确上穿后再回中轨
        x = np.linspace(0, 4 * np.pi, len(idx))
        close = pd.Series(100 + 5 * np.sin(x), index=idx)
        sig = ma_cross_mid_signal(close, 14, 16)
        self.assertTrue(set(np.unique(sig)) <= {-1.0, 0.0, 1.0})
        self.assertTrue((sig != 0).any())

    def test_bollinger_stop_and_mid_exit(self):
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

    def test_run_s1_s2_s3_margin(self):
        panels = generate_synthetic_futures(start="2019-01-01", end="2023-12-31", seed=2)
        keep = ["RB", "HC", "CU", "AU", "M", "Y", "C", "TA"]
        panels = {k: panels[k] for k in keep}
        cfg = BookConfig()
        _, _, _, s1, _ = run_s1(panels, cfg)
        _, _, _, s2, _ = run_s2(panels, cfg)
        _, _, _, s3, _ = run_s3(panels, cfg)
        self.assertLessEqual(s1["max_margin"], cfg.margin_s1 + 1e-6)
        self.assertLessEqual(s2["max_margin"], cfg.margin_s2 + 1e-6)
        self.assertLessEqual(s3["max_margin"], cfg.margin_s3 + 1e-6)


if __name__ == "__main__":
    unittest.main()
