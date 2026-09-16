"""Offline tests that do not require Yahoo or paid data."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from brent_quant.backtest import run_backtest
from brent_quant.contract_roll import difference_adjust, volume_roll_index
from brent_quant.data_cleaner import clean_ohlcv, detect_missing_bars
from brent_quant.data_loader import _flatten_ohlcv, merge_incremental, to_utc_index
from brent_quant.factors.trend import efficiency_ratio
from brent_quant.position import contracts_for_trade
from brent_quant.signal import add_factors


def _synth(n: int = 800, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-06-01", periods=n, freq="5min", tz="UTC")
    # Skip a weekend-like gap in the middle.
    rets = rng.normal(0.0, 0.0009, n)
    close = 80.0 * np.exp(np.cumsum(rets))
    noise = rng.normal(0.0, 0.03, n)
    high = close + np.abs(noise) + 0.02
    low = close - np.abs(noise) - 0.02
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.integers(50, 4000, n).astype(float)
    volume[::17] = 0.0
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    df.loc[df["high"] < df[["open", "close"]].max(axis=1), "high"] = df[["open", "close"]].max(axis=1)
    df.loc[df["low"] > df[["open", "close"]].min(axis=1), "low"] = df[["open", "close"]].min(axis=1)
    return df


def test_flatten_ignores_adj_close():
    cols = pd.MultiIndex.from_product([["Adj Close", "Close", "High", "Low", "Open", "Volume"], ["BZ=F"]])
    idx = pd.date_range("2026-01-01", periods=2, freq="5min", tz="America/New_York")
    raw = pd.DataFrame(
        [[1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]],
        index=idx,
        columns=cols,
    )
    out = _flatten_ohlcv(raw)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out["close"].iloc[0] == 2
    assert out["open"].iloc[0] == 5

    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], ["BZ=F"]])
    idx = pd.date_range("2026-01-01", periods=3, freq="5min", tz="America/New_York")
    raw = pd.DataFrame(np.arange(15).reshape(3, 5), index=idx, columns=cols)
    out = _flatten_ohlcv(raw)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert not out.columns.duplicated().any()


def test_missing_bar_detection():
    idx = pd.to_datetime(
        ["2026-01-05 10:00", "2026-01-05 10:05", "2026-01-05 10:15", "2026-01-05 10:20"],
        utc=True,
    )
    df = pd.DataFrame(
        {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        index=idx,
    )
    missing = detect_missing_bars(df)
    assert len(missing) == 1
    assert missing[0] == pd.Timestamp("2026-01-05 10:10", tz="UTC")


def test_clean_marks_zero_volume_and_abnormal():
    df = _synth(100)
    df.iloc[10, df.columns.get_loc("volume")] = 0.0
    df.iloc[20, df.columns.get_loc("close")] = df.iloc[19]["close"] * 1.2
    clean, qc = clean_ohlcv(df)
    assert qc["zero_volume_bars"] >= 1
    assert qc["abnormal_return_bars"] >= 1
    assert "volume" in clean.columns


def test_efficiency_ratio_bounds():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    er = efficiency_ratio(close, 3)
    assert er.dropna().between(0.0, 1.0).all()
    assert pytest.approx(er.iloc[-1], rel=1e-6) == 1.0


def test_pulse_hold_does_not_churn():
    df = _synth(500)
    res = run_backtest(df, model="A", time_stop_bars=24)
    assert res.positions.iloc[0] == 0.0
    if not res.trades.empty:
        assert len(res.trades) < 250
        assert (res.trades["bars_held"] >= 0).all()


def test_sizing_positive():
    qty = contracts_for_trade(1_000_000, 80.0, 0.15, 0.0035, 2.0, 1000.0, 3.0)
    assert qty > 0


def test_volume_roll_helper():
    idx = pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC")
    front = pd.Series([10, 10, 8, 5, 4, 3], index=idx)
    nxt = pd.Series([3, 4, 9, 12, 15, 20], index=idx)
    rolls = volume_roll_index(front, nxt, confirm_bars=2)
    assert len(rolls) >= 1
    assert difference_adjust(80, 81) == 1.0


def test_merge_incremental_dedup():
    a = _synth(10)
    b = _synth(10)
    b.iloc[0, b.columns.get_loc("close")] = 99.0
    merged = merge_incremental(a, b)
    assert merged.index.is_unique
    assert len(merged) == 10


def test_factors_no_lookahead_breakout_window():
    df = _synth(80)
    out = add_factors(df, breakout_n=10)
    # rolling_high at t must equal max(high[t-10:t]) i.e. exclude current high.
    i = 20
    expected = df["high"].iloc[i - 10 : i].max()
    assert out["rolling_high"].iloc[i] == expected
