"""Offline tests for UPV features, regimes, and next-bar execution."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from universal_quant.config import spec_for
from universal_quant.features.breakout import add_breakout
from universal_quant.features.trend import efficiency_ratio
from universal_quant.features.volatility import add_volatility
from universal_quant.regimes.regime_engine import add_regime
from universal_quant.backtest.engine import run_backtest
from universal_quant.strategies.score import add_score


def _synth(n=400, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    high = close * 1.002
    low = close * 0.998
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = rng.integers(1_000, 20_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def test_er_bounds():
    close = pd.Series([1.0, 2, 3, 4, 5.0])
    er = efficiency_ratio(close, 3)
    assert er.dropna().between(0, 1).all()


def test_breakout_excludes_current_bar():
    df = _synth(80)
    df = add_volatility(df)
    out = add_breakout(df, n=10)
    i = 20
    expected = df["high"].iloc[i - 10 : i].max()
    assert out["rolling_high"].iloc[i] == expected


def test_regime_labels():
    df = add_regime(_synth(200))
    assert set(df["regime"].unique()) <= {"trend", "range", "shock", "transition"}


def test_score_range():
    df = add_score(add_regime(_synth(200)))
    assert df["score"].between(-100, 100).all()


def test_next_bar_flat_start():
    res = run_backtest(_synth(300), model="A", symbol="SPY")
    assert res.equity.iloc[0] > 0
    assert spec_for("GC=F")["cluster"] == "Metals"
    if not res.trades.empty:
        assert (res.trades["bars_held"] >= 0).all()
