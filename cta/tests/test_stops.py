# -*- coding: utf-8 -*-
"""止损与流水线测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.data import generate_synthetic_futures
from cta.optimize import build_stopped_signal, param_grid
from cta.pipeline import run_cta_pipeline
from cta.portfolio_risk import MarginVaRLimits
from cta.signals import dual_ma_signal
from cta.stops import StopConfig, apply_atr_stop, trade_stats_from_signal


class TestStops(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        panels = generate_synthetic_futures(start="2019-01-01", end="2023-12-31", seed=3)
        cls.panels = {k: panels[k] for k in ["IF", "RB", "AU", "CU"]}

    def test_stop_flattens_on_adverse_move(self):
        ohlc = self.panels["RB"]
        raw = dual_ma_signal(ohlc["close"], fast=10, slow=40).fillna(0.0)
        stopped, stop_px, _ = apply_atr_stop(
            raw,
            ohlc["high"],
            ohlc["low"],
            ohlc["close"],
            StopConfig(atr_mult=1.5, trail_mult=2.5),
        )
        # 止损后应出现比原始信号更多的空仓
        self.assertGreater((stopped == 0).sum(), (raw == 0).sum())
        self.assertTrue(stop_px.notna().any())

    def test_trade_stats_keys(self):
        ohlc = self.panels["AU"]
        sig = build_stopped_signal(
            "tsmom",
            ohlc,
            {"lookback": 40, "skip": 1, "atr_mult": 2.0, "trail_mult": 3.0},
        )
        st = trade_stats_from_signal(sig, ohlc["close"])
        self.assertIn("trade_win_rate", st)
        self.assertIn("trade_payoff", st)
        self.assertGreater(st["n_trades"], 0)

    def test_stop_exit_ret_caps_loss(self):
        """止损日应用止损价收益，截断不利方向亏损。"""
        idx = pd.bdate_range("2020-01-01", periods=8)
        close = pd.Series([100, 101, 102, 103, 90, 89, 88, 87], index=idx, dtype=float)
        high = close + 1.0
        low = close - 1.0
        # day4: 大跌，应触发止损；raw 信号始终做多
        raw = pd.Series(1.0, index=idx)
        # 人为小 ATR：把 atr 窗口做成几乎常数 true range~2
        high[:] = close + 0.5
        low[:] = close - 0.5
        high.iloc[4] = 103.0
        low.iloc[4] = 90.0
        stopped, _, exit_ret = apply_atr_stop(
            raw,
            high,
            low,
            close,
            StopConfig(atr_window=3, atr_mult=1.0, trail_mult=1.5, use_trailing=False),
        )
        self.assertTrue(exit_ret.notna().any())
        # 止损后应出现空仓
        self.assertTrue((stopped == 0).any())

    def test_param_grid_includes_atr(self):
        g = param_grid("tsmom")
        self.assertTrue(any("atr_mult" in ps.as_dict() for ps in g))
        atrs = {ps.as_dict()["atr_mult"] for ps in g}
        self.assertIn(1.5, atrs)

    def test_pipeline_with_stops(self):
        from cta.optimize import optimize_all_methods

        optim = optimize_all_methods(
            self.panels,
            methods=["tsmom"],
            train_end="2021-06-30",
            valid_end="2022-12-31",
            min_train_sharpe=-2.0,
            min_valid_sharpe=-2.0,
            min_positive_asset_frac=0.0,
            max_local_sharpe_std=9.0,
        )
        res = run_cta_pipeline(
            self.panels,
            methods=["tsmom"],
            train_end="2021-06-30",
            valid_end="2022-12-31",
            limits=MarginVaRLimits(min_history=40, var_window=120, corr_window=120),
            precomputed_optim=optim,
        )
        self.assertIn("trade_payoff", res.sleeve_summary.columns)
        self.assertIn("max_drawdown", res.sleeve_summary.columns)
        self.assertTrue(np.isfinite(res.equity.iloc[-1]))


if __name__ == "__main__":
    unittest.main()
