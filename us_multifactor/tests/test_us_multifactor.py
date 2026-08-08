# -*- coding: utf-8 -*-
"""Tests for US multi-factor package (no network required if cache exists)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from us_multifactor.backtest import performance_summary, top_n_weights
from us_multifactor.enhanced import run_enhanced_backtest, score_weighted_top_n
from us_multifactor.factors import process_factor, rank_ic_series
from us_multifactor.frozen import DEFAULT_PARAMS


def test_top_n_weights_sums_to_one():
    rng = np.random.default_rng(0)
    scores = pd.DataFrame(rng.normal(size=(20, 30)), columns=[f"S{i}" for i in range(30)])
    w = top_n_weights(scores, n=10)
    for i in range(len(w)):
        s = w.iloc[i].sum()
        assert abs(s - 1.0) < 1e-8 or s == 0.0
        assert (w.iloc[i] > 0).sum() in (0, 10)


def test_score_weighted_positive():
    rng = np.random.default_rng(1)
    scores = pd.DataFrame(rng.normal(size=(15, 25)), columns=[f"S{i}" for i in range(25)])
    w = score_weighted_top_n(scores, n=10)
    assert (w.to_numpy() >= -1e-12).all()


def test_rank_ic_vectorized():
    rng = np.random.default_rng(2)
    f = pd.DataFrame(rng.normal(size=(40, 50)))
    r = f + rng.normal(scale=0.5, size=f.shape)
    ic = rank_ic_series(process_factor(f), r)
    assert ic.notna().sum() > 10
    assert ic.mean() > 0


def test_enhanced_backtest_smoke():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2020-01-03", periods=120, freq="W-FRI")
    cols = [f"S{i}" for i in range(40)]
    scores = pd.DataFrame(rng.normal(size=(120, 40)), index=idx, columns=cols)
    rets = pd.DataFrame(rng.normal(scale=0.02, size=(120, 40)), index=idx, columns=cols)
    # plant mild edge
    rets = rets + 0.01 * scores.shift(1).fillna(0)
    spy = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.01, size=500)), index=pd.date_range("2019-01-01", periods=500, freq="B"))
    bt = run_enhanced_backtest(scores, rets, spy, top_n=10, cost_bps=5.0, vol_target=None, lever_cap=1.0)
    assert bt.equity.iloc[-1] > 0
    assert "sharpe" in bt.summary


def test_default_params_targets_keys():
    assert DEFAULT_PARAMS["top_n"] == 10
    assert abs(sum(DEFAULT_PARAMS["tilt"].values()) - 1.0) < 1e-9


def test_causal_regime_is_lagged():
    from us_multifactor.causal import causal_regime_exposure

    idx = pd.date_range("2020-01-03", periods=40, freq="W-FRI")
    spy = pd.Series(np.linspace(100, 140, 200), index=pd.date_range("2019-01-01", periods=200, freq="B"))
    exp = causal_regime_exposure(idx, spy, fast=5, slow=10, mode="ma")
    # first observation after shift must be 0 / unknown
    assert float(exp.iloc[0]) == 0.0


def test_enhanced_spy_pos_uses_prior_week():
    """Guard against regressing same-week SPY look-ahead."""
    import inspect
    from us_multifactor import enhanced

    src = inspect.getsource(enhanced.run_enhanced_backtest)
    assert "shift(1)" in src
    assert "pct_change().shift(1)" in src or "pct_change().fillna(0) > 0).astype(float)" not in src.replace(
        "shift(1)", ""
    )


@pytest.mark.slow
def test_frozen_against_cache_if_present():
    from pathlib import Path
    from us_multifactor.data_yfinance import DATA_DIR
    from us_multifactor.frozen import run_frozen

    cache = list(Path(DATA_DIR).glob("adj_close_*.parquet"))
    if not cache:
        pytest.skip("price cache missing")
    result = run_frozen(out_dir=Path("/tmp/us_mf_test_out"), reselect_factors=True)
    assert result["summary"]["n_obs"] > 50
