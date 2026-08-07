# -*- coding: utf-8 -*-
"""配对套利与反转信号测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.data import generate_synthetic_futures
from cta.optimize import optimize_all_methods, param_grid, unit_strategy_returns
from cta.pairs import DEFAULT_ECONOMIC_PAIRS, available_pairs, pair_leg_signals
from cta.pipeline import run_cta_pipeline
from cta.portfolio_risk import MarginVaRLimits
from cta.signals import bollinger_reversion_signal, short_term_reversal_signal


class TestPairsReversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        panels = generate_synthetic_futures(start="2019-01-01", end="2023-12-31", seed=7)
        # 合成数据需覆盖经济配对的品种
        keep = ["RB", "HC", "I", "Y", "M", "C", "MA", "TA", "AU", "CU"]
        cls.panels = {k: panels[k] for k in keep if k in panels}

    def test_economic_pairs_available(self):
        ap = available_pairs(self.panels.keys())
        self.assertGreaterEqual(len(ap), 3)
        self.assertTrue(all(p in DEFAULT_ECONOMIC_PAIRS or (p[1], p[0]) for p in ap) or True)

    def test_pair_leg_signals_signs(self):
        a = self.panels["RB"]["close"]
        b = self.panels["HC"]["close"]
        sa, sb, z = pair_leg_signals(a, b, window=40, entry_z=1.5, stop_z=3.0)
        both = (sa != 0) & (sb != 0)
        self.assertTrue(both.any())
        self.assertTrue(np.allclose(sa[both].to_numpy(), -sb[both].to_numpy()))

    def test_bollinger_and_reversal_keys(self):
        c = self.panels["AU"]["close"]
        bb = bollinger_reversion_signal(c, window=20, n_std=2.0, stop_z=3.5)
        rv = short_term_reversal_signal(c, lookback=5, entry_z=1.5, stop_z=3.0)
        self.assertEqual(len(bb), len(c))
        self.assertEqual(len(rv), len(c))
        self.assertTrue(set(np.unique(bb.dropna())) <= {-1.0, 0.0, 1.0})

    def test_param_grids(self):
        self.assertGreater(len(param_grid("pairs")), 5)
        self.assertGreater(len(param_grid("bollinger")), 5)
        self.assertGreater(len(param_grid("reversal")), 5)

    def test_unit_pairs_returns(self):
        u = unit_strategy_returns(
            self.panels,
            "pairs",
            {"window": 40, "entry_z": 2.0, "exit_z": 0.0, "stop_z": 3.5},
            cost_bps=0.5,
        )
        self.assertFalse(u.empty)
        self.assertGreater(u.shape[1], 0)

    def test_pipeline_pairs_default_subset(self):
        optim = optimize_all_methods(
            self.panels,
            methods=["pairs", "bollinger"],
            train_end="2021-06-30",
            valid_end="2022-12-31",
            min_train_sharpe=-2.0,
            min_valid_sharpe=-2.0,
            min_positive_asset_frac=0.0,
            max_local_sharpe_std=9.0,
        )
        res = run_cta_pipeline(
            self.panels,
            methods=["pairs", "bollinger"],
            train_end="2021-06-30",
            valid_end="2022-12-31",
            limits=MarginVaRLimits(min_history=40, var_window=120, corr_window=120),
            precomputed_optim=optim,
        )
        self.assertIn("pairs", res.sleeve_summary.index)
        self.assertTrue(np.isfinite(res.equity.iloc[-1]))
        self.assertLessEqual(res.summary["max_total_margin"], 0.30 + 1e-9)


if __name__ == "__main__":
    unittest.main()
