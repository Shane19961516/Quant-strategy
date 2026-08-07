# -*- coding: utf-8 -*-
"""用户定义策略单元测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.book.strategies_12 import BookConfig, bollinger_fade_signal, ma_cross_mid_signal, run_s1
from cta.book.strategies_3 import run_s3
from cta.book.strategies_4 import (
    CalendarConfig,
    calendar_spread_signal,
    parse_contract_ym,
    select_near_deferred,
    _backtest_symbol_calendar,
)
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

    def test_select_near_deferred_and_fixed_pair(self):
        idx = pd.bdate_range("2020-01-02", periods=80)
        cfg = CalendarConfig(roll_days=10, min_volume=100.0, min_oi=100.0, z_window=20)

        def _mk(code: str, start_offset: int, n: int, base: float):
            d = idx[start_offset : start_offset + n]
            close = base + np.linspace(0, 1, len(d))
            return pd.DataFrame(
                {
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 5000.0,
                    "oi": 12000.0,
                },
                index=d,
            )

        contracts = {
            "RB2005": _mk("RB2005", 0, 60, 3400),
            "RB2006": _mk("RB2006", 0, 70, 3420),
            "RB2007": _mk("RB2007", 10, 70, 3430),
            "RB2008": _mk("RB2008", 10, 70, 3440),
        }
        dt0 = idx[15]
        pair = select_near_deferred(contracts, dt0, cfg)
        self.assertIsNotNone(pair)
        self.assertEqual(len(pair), 2)
        self.assertLess(parse_contract_ym(pair[0]), parse_contract_ym(pair[1]))

        # 固定合约对：在未触发移仓窗口前 near 不应日更
        bt = _backtest_symbol_calendar("RB", contracts, cfg)
        self.assertFalse(bt.empty)
        held = bt[(bt["near"] != "") & (bt["roll"] == 0)]
        if len(held) > 5:
            # 连续非 roll 日 near 应稳定（允许最后因到期切换）
            near_changes = (held["near"] != held["near"].shift(1)).sum()
            self.assertLessEqual(int(near_changes), 3)

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
