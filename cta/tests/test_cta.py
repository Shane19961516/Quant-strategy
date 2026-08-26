# -*- coding: utf-8 -*-
"""CTA 模块基础单元测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.backtest import CTABacktester
from cta.data import generate_synthetic_futures
from cta.metrics import performance_summary
from cta.position_manager import RiskLimits
from cta.risk import volatility_target_weights
from cta.signals import dual_ma_signal, ts_momentum_signal


class TestCTA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panels = generate_synthetic_futures(
            start="2020-01-01",
            end="2022-12-31",
            seed=7,
        )

    def test_dual_ma_values(self):
        close = self.panels["IF"]["close"]
        sig = dual_ma_signal(close, fast=10, slow=30)
        self.assertTrue(set(sig.dropna().unique()).issubset({-1.0, 0.0, 1.0}))

    def test_tsmom_length(self):
        close = self.panels["RB"]["close"]
        sig = ts_momentum_signal(close, lookback=40, skip=1)
        self.assertEqual(len(sig), len(close))

    def test_vol_target_clips(self):
        close = self.panels["AU"]["close"]
        sig = pd.Series(1.0, index=close.index)
        w = volatility_target_weights(close, sig, target_vol=0.1, max_leverage=1.5)
        self.assertLessEqual(w.abs().max(), 1.5 + 1e-9)

    def test_backtest_runs(self):
        bt = CTABacktester(self.panels, method="combo", cost_bps=2.0, slip_bps=1.0)
        res = bt.run()
        self.assertGreater(len(res.equity), 100)
        self.assertIn("sharpe", res.summary)
        self.assertTrue(np.isfinite(res.equity.iloc[-1]))

    def test_risk_limits_enforced(self):
        limits = RiskLimits(max_daily_loss=0.03, max_drawdown=0.08, dd_scale_start=0.04)
        bt = CTABacktester(
            self.panels,
            method="combo",
            target_vol=0.04,
            portfolio_target_vol=0.05,
            max_leverage=1.0,
            risk_limits=limits,
            enable_position_manager=True,
        )
        res = bt.run()
        self.assertGreaterEqual(res.summary["max_daily_loss"], -0.03 - 1e-9)
        self.assertGreaterEqual(res.summary["max_drawdown"], -0.08 - 1e-9)
        self.assertEqual(res.summary["daily_loss_ok"], 1.0)
        self.assertEqual(res.summary["drawdown_ok"], 1.0)

    def test_performance_summary(self):
        eq = pd.Series(np.cumprod(1 + np.random.default_rng(0).normal(0.0005, 0.01, 500)))
        s = performance_summary(eq)
        self.assertIn("cagr", s)
        self.assertIn("max_daily_loss", s)
        self.assertLess(s["max_drawdown"], 0.0)


if __name__ == "__main__":
    unittest.main()
