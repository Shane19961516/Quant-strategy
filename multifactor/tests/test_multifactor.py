# -*- coding: utf-8 -*-
"""Unit tests for the multi-factor package."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from multifactor.backtest import run_backtest
from multifactor.combine import combine_factors, rolling_icir_weights
from multifactor.data import generate_synthetic_panel, is_a_share, load_market_panel
from multifactor.factors import build_factor_panel, factor_mom_12_1, factor_rev_1
from multifactor.metrics import factor_ic_summary, performance_summary, rank_ic_series
from multifactor.neutralize import cs_zscore, industry_neutralize, winsorize
from multifactor.pipeline import run_multifactor_pipeline, save_results
from multifactor.portfolio import scores_to_weights, turnover


def test_is_a_share():
    assert is_a_share("000001.SZ")
    assert is_a_share("600000.SH")
    assert is_a_share("300001.SZ")
    assert not is_a_share("000300.SH")
    assert not is_a_share("000001.SH")


def test_winsorize_and_zscore():
    rng = np.random.default_rng(0)
    df = pd.DataFrame(rng.normal(size=(100, 5)))
    df.iloc[0, 0] = 100
    w = winsorize(df, 0.05)
    assert w.iloc[0, 0] < 100
    z = cs_zscore(w)
    assert abs(z.mean().mean()) < 1e-8
    assert abs(z.std(ddof=0).mean() - 1.0) < 1e-8


def test_industry_neutralize_zero_mean():
    dates = pd.date_range("2020-01-31", periods=3, freq="ME")
    codes = ["a", "b", "c", "d"]
    panel = pd.DataFrame(
        [[1.0, 2.0, 3.0], [3.0, 4.0, 5.0], [10.0, 11.0, 12.0], [12.0, 13.0, 14.0]],
        index=codes,
        columns=dates,
    )
    ind = pd.DataFrame(
        [["X", "X", "X"], ["X", "X", "X"], ["Y", "Y", "Y"], ["Y", "Y", "Y"]],
        index=codes,
        columns=dates,
    )
    neut = industry_neutralize(panel, ind)
    for dt in dates:
        g = pd.DataFrame({"v": neut[dt], "i": ind[dt]}).dropna()
        means = g.groupby("i")["v"].mean()
        assert (means.abs() < 1e-10).all()


def test_factor_lag_no_lookahead():
    panel = generate_synthetic_panel(n_stocks=40, n_months=36, seed=1)
    raw = factor_mom_12_1(panel.returns)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=["mom_12_1"],
        winsor_q=0.0,
        neutralize_industry=False,
        zscore=False,
    )
    # shifted factor at t equals raw at t-1
    aligned = factors["mom_12_1"].iloc[:, 1:]
    raw_lag = raw.iloc[:, :-1]
    raw_lag.columns = aligned.columns
    mask = aligned.notna() & raw_lag.notna()
    assert np.allclose(
        aligned.to_numpy()[mask.to_numpy()],
        raw_lag.to_numpy()[mask.to_numpy()],
        equal_nan=True,
    )


def test_combine_equal_and_icir():
    panel = generate_synthetic_panel(n_stocks=60, n_months=48, seed=2)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=["mom_12_1", "vol_12", "rev_1"],
    )
    eq = combine_factors(factors)
    assert eq.shape == panel.returns.shape
    blend = rolling_icir_weights(factors, panel.returns, window=12, min_periods=6)
    assert blend["weights"].shape[0] == 3
    assert blend["combined"].shape == panel.returns.shape


def test_long_short_weights_dollar_neutral():
    panel = generate_synthetic_panel(n_stocks=50, n_months=30, seed=3)
    factors = build_factor_panel(panel.returns, panel.industry, factor_names=["mom_12_1"])
    w = scores_to_weights(factors["mom_12_1"], method="long_short", n_quantiles=5)
    # for dates with positions, long sum ≈ 1, short sum ≈ -1
    for dt in w.columns:
        col = w[dt]
        if col.abs().sum() == 0:
            continue
        assert abs(col[col > 0].sum() - 1.0) < 1e-8
        assert abs(col[col < 0].sum() + 1.0) < 1e-8


def test_backtest_and_metrics():
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=4)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=["mom_12_1", "vol_12"],
    )
    score = combine_factors(factors)
    w = scores_to_weights(score, method="long_short")
    bt = run_backtest(w, panel.returns, cost_bps=10.0)
    assert bt.equity.iloc[-1] > 0
    assert "sharpe" in bt.summary
    ic = factor_ic_summary(factors, panel.returns)
    assert set(ic.index) == {"mom_12_1", "vol_12"}
    # planted edge → mom / vol IC should lean positive
    mom_ic = rank_ic_series(factors["mom_12_1"], panel.returns)
    vol_ic = rank_ic_series(factors["vol_12"], panel.returns)
    assert mom_ic.dropna().mean() > 0
    assert vol_ic.dropna().mean() > 0
    assert bt.summary["sharpe"] > 0


def test_pipeline_synthetic(tmp_path):
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=5)
    result = run_multifactor_pipeline(
        panel=panel,
        factor_names=["mom_12_1", "vol_12", "rev_1"],
        combine_method="equal",
        portfolio_method="long_short",
        cost_bps=10.0,
    )
    assert result.backtest.summary["n_obs"] > 10
    out = save_results(result, tmp_path, prefix="test")
    assert (out / "test_summary.txt").exists()
    assert (out / "test_nav.png").exists()


def test_load_real_panel_smoke():
    """Smoke-load real repo data if present."""
    try:
        panel = load_market_panel(start="2018-01-01", end="2019-12-31")
    except FileNotFoundError:
        pytest.skip("quote data missing")
    assert panel.returns.shape[1] >= 12
    assert panel.industry.shape == panel.returns.shape
    # industry labels non-null for a good share
    assert panel.industry.notna().mean().mean() > 0.5


def test_turnover_nonnegative():
    dates = pd.date_range("2020-01-31", periods=3, freq="ME")
    w = pd.DataFrame(
        [[0.5, 0.0, 0.5], [0.5, 1.0, 0.5], [0.0, 0.0, 0.0]],
        index=["a", "b", "c"],
        columns=dates,
    )
    to = turnover(w)
    assert (to.iloc[1:] >= 0).all()


def test_performance_summary_basic():
    eq = pd.Series(np.cumprod(1 + np.array([0.01, -0.02, 0.03, 0.01])))
    s = performance_summary(eq, freq="M")
    assert s["n_obs"] == 3
    assert np.isfinite(s["sharpe"])
