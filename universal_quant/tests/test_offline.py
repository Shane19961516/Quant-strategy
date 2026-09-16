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


def test_target_weight_caps():
    from universal_quant.portfolio.sizing import target_weight

    assert target_weight(0.01, 0.01, 2.0, 2.5) == 0.5
    assert target_weight(0.001, 0.01, 2.0, 2.5) == 2.5
    assert target_weight(0.0, 0.01, 2.0, 2.5) == 0.0


def test_equal_risk_vol_target():
    from universal_quant.portfolio.risk_budget import blend_equal_risk, equal_risk_nav

    idx = pd.date_range("2022-01-03", periods=504, freq="B")
    rng = np.random.default_rng(7)
    a = pd.Series(1_000_000 * np.cumprod(1.0 + rng.normal(0.0004, 0.008, len(idx))), index=idx)
    b = pd.Series(1_000_000 * np.cumprod(1.0 + rng.normal(0.0002, 0.018, len(idx))), index=idx)
    navs = pd.DataFrame({"a": a, "b": b})
    port = equal_risk_nav(navs, target_vol=0.12, max_leverage=8.0)
    vol = float(port.pct_change().dropna().std(ddof=1) * np.sqrt(252))
    assert 0.09 < vol < 0.15
    blend = blend_equal_risk(navs, target_vol=0.12)
    assert blend["leverage"] > 0.0
    assert abs(float(blend["weights"].sum()) - 1.0) < 1e-9


def test_equal_risk_cagr_target():
    from universal_quant.portfolio.risk_budget import blend_equal_risk, calendar_cagr

    idx = pd.date_range("2022-01-03", periods=504, freq="B")
    rng = np.random.default_rng(3)
    a = pd.Series(1_000_000 * np.cumprod(1.0 + rng.normal(0.00025, 0.006, len(idx))), index=idx)
    b = pd.Series(1_000_000 * np.cumprod(1.0 + rng.normal(0.00015, 0.012, len(idx))), index=idx)
    navs = pd.DataFrame({"a": a, "b": b})
    blend = blend_equal_risk(navs, target_vol=0.20, max_leverage=8.0, target_cagr=0.27)
    assert 0.24 <= calendar_cagr(blend["nav"]) <= 0.30


def test_hourly_clock_no_intrabar_leak():
    from universal_quant.eth_okx_study import hourly_clock_on_1m

    idx = pd.date_range("2026-01-01", periods=48 * 60, freq="1min", tz="UTC")
    close = pd.Series(100.0, index=idx)
    spike = pd.Timestamp("2026-01-02 10:30:00", tz="UTC")
    close.loc[spike] = 130.0
    df = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": pd.Series(100.0, index=idx),
            "close": close,
            "volume": 1_000.0,
        },
        index=idx,
    )
    df.loc[spike, "high"] = 130.0
    sig, overlay = hourly_clock_on_1m(df, "A")
    leaked = overlay["atr"].loc["2026-01-02 10:00":"2026-01-02 10:59"]
    after = overlay["atr"].loc["2026-01-02 11:00"]
    assert float(leaked.max()) < 0.2 or leaked.isna().all()
    assert float(after) > 1.0
    fired = sig[sig.abs() > 0]
    if len(fired):
        assert (fired.index.minute == 59).all()


def test_run_backtest_prepared_signal():
    idx = pd.date_range("2026-01-01", periods=80, freq="1min", tz="UTC")
    px = pd.Series(100.0, index=idx)
    df = pd.DataFrame({"open": px, "high": px + 0.1, "low": px - 0.1, "close": px, "volume": 1.0}, index=idx)
    sig = pd.Series(0.0, index=idx)
    sig.iloc[10] = 1.0
    overlay = {
        "atr": pd.Series(1.0, index=idx),
        "natr": pd.Series(0.01, index=idx),
        "regime": pd.Series("trend", index=idx),
    }
    res = run_backtest(df, model="B", symbol="ETH-USDT-SWAP", signal=sig, overlay=overlay, prepared=True)
    assert res.equity.iloc[0] > 0
    assert not res.trades.empty
    assert str(res.trades.iloc[0]["entry_time"]).startswith("2026-01-01")
