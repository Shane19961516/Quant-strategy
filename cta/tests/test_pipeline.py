# -*- coding: utf-8 -*-
"""CTA 流水线单元测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.data import generate_synthetic_futures
from cta.optimize import optimize_strategy_params, param_grid
from cta.pipeline import run_cta_pipeline
from cta.portfolio_risk import MarginVaRLimits, correlation_clusters, historical_var


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 较短合成样本，加快测试
        cls.panels = generate_synthetic_futures(
            start="2018-01-01",
            end="2023-12-31",
            seed=11,
        )
        # 只用部分品种加速
        keep = ["IF", "RB", "AU", "CU", "M", "TA"]
        cls.panels = {k: cls.panels[k] for k in keep}

    def test_param_grids_nonempty(self):
        for m in ("dual_ma", "donchian", "tsmom"):
            self.assertGreater(len(param_grid(m)), 5)

    def test_optimize_tsmom(self):
        opt = optimize_strategy_params(
            self.panels,
            "tsmom",
            train_end="2021-06-30",
            valid_end="2022-12-31",
            min_train_sharpe=-1.0,
            min_valid_sharpe=-1.0,
            min_positive_asset_frac=0.0,
            max_local_sharpe_std=9.0,
        )
        self.assertEqual(opt.method, "tsmom")
        self.assertIn("lookback", opt.best.as_dict())
        self.assertFalse(opt.score_table.empty)

    def test_correlation_clusters(self):
        rng = np.random.default_rng(0)
        n = 200
        a = pd.Series(rng.normal(0, 0.01, n))
        b = a + rng.normal(0, 0.001, n)  # high corr
        c = pd.Series(rng.normal(0, 0.01, n))
        df = pd.DataFrame({"A": a, "B": b, "C": c})
        cl = correlation_clusters(df, threshold=0.5)
        self.assertEqual(cl["A"], cl["B"])
        self.assertNotEqual(cl["A"], cl["C"])

    def test_historical_var(self):
        r = pd.Series(np.random.default_rng(0).normal(0, 0.01, 300))
        v = historical_var(r, alpha=0.95)
        self.assertGreater(v, 0)

    def test_pipeline_constraints(self):
        limits = MarginVaRLimits(
            max_total_margin=0.30,
            max_cluster_margin=0.10,
            max_var=0.03,
            var_window=120,
            corr_window=120,
            min_history=40,
        )
        # 放宽寻优阈值以在合成数据上总能选出参数
        from cta.optimize import optimize_all_methods

        optim = optimize_all_methods(
            self.panels,
            methods=["tsmom", "dual_ma"],
            train_end="2021-06-30",
            valid_end="2022-12-31",
            min_train_sharpe=-2.0,
            min_valid_sharpe=-2.0,
            min_positive_asset_frac=0.0,
            max_local_sharpe_std=9.0,
        )
        res = run_cta_pipeline(
            self.panels,
            methods=["tsmom", "dual_ma"],
            train_end="2021-06-30",
            valid_end="2022-12-31",
            limits=limits,
            precomputed_optim=optim,
        )
        self.assertLessEqual(res.summary["max_total_margin"], 0.30 + 1e-9)
        self.assertLessEqual(res.summary["max_cluster_margin"], 0.10 + 1e-9)
        self.assertLessEqual(res.summary["max_port_var95"], 0.03 + 1e-9)
        self.assertTrue(np.isfinite(res.equity.iloc[-1]))


if __name__ == "__main__":
    unittest.main()
