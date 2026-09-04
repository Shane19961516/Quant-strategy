# -*- coding: utf-8 -*-
"""Unit tests for factor engineering package."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor_engineering.backtest import run_backtest, scores_to_ls_weights
from factor_engineering.combine import combine_equal, combine_icir, orthogonalize_factors
from factor_engineering.data import generate_synthetic_panel, is_a_share, load_market_panel
from factor_engineering.evaluate import (
    evaluate_factor,
    ic_decay,
    pairwise_factor_corr,
    rank_ic_series,
)
from factor_engineering.factors import build_factor_panel, factor_rev_1
from factor_engineering.pipeline import run_factor_engineering
from factor_engineering.process import cs_zscore, industry_neutralize, winsorize
from factor_engineering.report import save_report
from factor_engineering.select import screen_factors


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
    raw = factor_rev_1(panel.returns)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=["rev_1"],
        winsor_q=0.0,
        neutralize_industry=False,
        standardize="none",
    )
    aligned = factors["rev_1"].iloc[:, 1:]
    raw_lag = raw.iloc[:, :-1]
    raw_lag.columns = aligned.columns
    mask = aligned.notna() & raw_lag.notna()
    assert np.allclose(
        aligned.to_numpy()[mask.to_numpy()],
        raw_lag.to_numpy()[mask.to_numpy()],
        equal_nan=True,
    )


def test_planted_reversal_positive_ic():
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=7)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=["rev_1", "vol_12"],
    )
    ic = rank_ic_series(factors["rev_1"], panel.returns)
    assert ic.dropna().mean() > 0.02


def test_ic_decay_and_corr():
    panel = generate_synthetic_panel(n_stocks=60, n_months=48, seed=8)
    factors = build_factor_panel(
        panel.returns, panel.industry, factor_names=["rev_1", "vol_12", "max_ret"]
    )
    decay = ic_decay(factors["rev_1"], panel.returns)
    assert 0 in decay.index
    corr = pairwise_factor_corr(factors)
    assert corr.shape == (3, 3)
    assert abs(corr.loc["rev_1", "rev_1"] - 1.0) < 1e-8


def test_orthogonalize_reduces_corr():
    panel = generate_synthetic_panel(n_stocks=80, n_months=48, seed=9)
    factors = build_factor_panel(
        panel.returns, panel.industry, factor_names=["vol_12", "vol_6", "rev_1"]
    )
    # vol_12 and vol_6 are highly related
    before = pairwise_factor_corr(factors)
    ortho = orthogonalize_factors(factors, order=["vol_12", "vol_6", "rev_1"])
    after = pairwise_factor_corr(ortho)
    assert abs(after.loc["vol_12", "vol_6"]) < abs(before.loc["vol_12", "vol_6"]) + 0.05
    # residual should be nearly uncorrelated with base
    assert abs(after.loc["vol_12", "vol_6"]) < 0.25


def test_combine_and_screen():
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=10)
    factors = build_factor_panel(
        panel.returns,
        panel.industry,
        factor_names=["rev_1", "vol_12", "max_ret"],
    )
    eq = combine_equal(factors)
    assert eq.shape == panel.returns.shape
    blend = combine_icir(factors, panel.returns, window=12, min_periods=6)
    assert blend["combined"].shape == panel.returns.shape

    evals = {
        n: evaluate_factor(n, f, panel.returns) for n, f in factors.items()
    }
    card = screen_factors(evals, corr_matrix=pairwise_factor_corr(factors))
    assert "quality" in card.columns
    assert card["quality"].is_monotonic_decreasing or len(card) == 1


def test_backtest_ls():
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=11)
    factors = build_factor_panel(
        panel.returns, panel.industry, factor_names=["rev_1", "vol_12"]
    )
    score = combine_equal(factors)
    w = scores_to_ls_weights(score)
    bt = run_backtest(w, panel.returns, cost_bps=10.0)
    assert bt.equity.iloc[-1] > 0
    assert bt.summary["sharpe"] > 0


def test_pipeline_synthetic(tmp_path):
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=12)
    result = run_factor_engineering(
        panel=panel,
        factor_names=["rev_1", "vol_12", "max_ret", "skew_12"],
        combine_method="equal",
        cost_bps=10.0,
        min_abs_ic=0.01,
        min_abs_icir=0.1,
    )
    assert len(result.selected_factors) >= 2
    assert result.backtest is not None
    out = save_report(result, tmp_path)
    assert (out / "SUMMARY.txt").exists()
    assert (out / "FACTOR_ENGINEERING_REPORT.md").exists()
    assert (out / "fe_ic_bars.png").exists()


def test_load_real_panel_smoke():
    try:
        panel = load_market_panel(start="2018-01-01", end="2019-12-31")
    except FileNotFoundError:
        pytest.skip("quote data missing")
    assert panel.returns.shape[1] >= 12
    assert panel.industry.shape == panel.returns.shape
    assert panel.industry.notna().mean().mean() > 0.5
