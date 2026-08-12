# -*- coding: utf-8 -*-
"""Tests for Alpha101 US pipeline (synthetic, no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from alpha101.alphas import compute_alphas
from alpha101.data import make_synthetic_panel
from alpha101.evaluate_5d import US_ALPHA101_CRITERIA, evaluate_alpha_5d, forward_return
from alpha101.operators import rank, ts_std
from alpha101.pipeline import run_alpha101_pipeline
from factor_engineering.admission import AdmissionCriteria


def test_operators_shapes():
    panel = make_synthetic_panel(n_tickers=20, n_days=120, seed=1)
    r = rank(panel.close)
    assert r.shape == panel.close.shape
    assert abs(r.iloc[-1].mean() - 0.5) < 0.15


def test_compute_subset_alphas():
    panel = make_synthetic_panel(n_tickers=25, n_days=300, seed=2)
    fac = compute_alphas(
        panel, names=["alpha012", "alpha033", "alpha101", "alpha006"], lag=1
    )
    assert set(fac) == {"alpha012", "alpha033", "alpha101", "alpha006"}
    # lag: first row should be mostly nan
    assert fac["alpha012"].iloc[0].isna().mean() > 0.5


def test_forward_return_horizon():
    panel = make_synthetic_panel(n_tickers=10, n_days=30, seed=3)
    fwd = forward_return(panel.close, 5)
    # manual check one cell
    t = panel.close.index[10]
    t5 = panel.close.index[15]
    ticker = panel.close.columns[0]
    expected = panel.close.loc[t5, ticker] / panel.close.loc[t, ticker] - 1
    assert abs(fwd.loc[t, ticker] - expected) < 1e-12


def test_pipeline_synthetic(tmp_path):
    panel = make_synthetic_panel(n_tickers=40, n_days=520, seed=4)
    crit = AdmissionCriteria(
        min_abs_ic=0.01,
        min_abs_icir=0.1,
        min_ic_tstat=0.8,
        min_ic_hit_rate=0.5,
        min_subperiod_sign_ratio=0.4,
        min_rolling_icir_pos_ratio=0.35,
        min_quantile_monotonicity=0.35,
        min_abs_q_spread=0.0005,
        min_ls_sharpe=0.05,
        min_months=20,
        max_avg_turnover=2.5,
        cost_bps=5.0,
    )
    result = run_alpha101_pipeline(
        panel=panel,
        factor_names=["alpha012", "alpha033", "alpha101", "alpha006", "alpha003"],
        criteria=crit,
        db_root=tmp_path / "db",
        out_dir=tmp_path / "out",
        data_dir=tmp_path / "data",
    )
    assert (tmp_path / "out" / "FACTOR_REPORT.md").exists()
    assert (tmp_path / "db" / "ADMISSION_STANDARD.md").exists()
    catalog = result.store.list_factors(status=None)
    assert len(catalog) == 5
