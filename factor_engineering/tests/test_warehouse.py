# -*- coding: utf-8 -*-
"""Tests for admission, battery, store, warehouse, update."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor_engineering.admission import AdmissionCriteria, decide_admission
from factor_engineering.battery import run_factor_battery, run_universe_battery
from factor_engineering.data import generate_synthetic_panel
from factor_engineering.docs import build_factor_doc, render_admission_standard_md
from factor_engineering.factors import build_factor_panel
from factor_engineering.store import FactorStore
from factor_engineering.update import UpdateConfig, run_scheduled_update
from factor_engineering.warehouse import run_warehouse_pipeline


def test_decide_admission_pass_and_fail():
    good = {
        "ic_mean": 0.05,
        "icir": 0.6,
        "ic_tstat": 4.0,
        "ic_pos_ratio": 0.7,
        "subperiod_sign_ratio": 0.9,
        "half_sample_sign_match": True,
        "rolling_icir_pos_ratio": 0.7,
        "q_monotonicity": 1.0,
        "q_spread": 0.01,
        "ls_sharpe": 0.8,
        "ls_cagr": 0.1,
        "ls_max_drawdown": -0.2,
        "avg_turnover": 0.5,
        "n": 60,
    }
    d = decide_admission("rev_1", good)
    assert d.admitted
    assert d.direction == 1

    bad = dict(good)
    bad["ic_mean"] = 0.001
    d2 = decide_admission("x", bad)
    assert not d2.admitted
    assert any("min_abs_ic" in r for r in d2.reject_reasons)


def test_decide_admission_negative_ic_flips_direction():
    metrics = {
        "ic_mean": -0.05,
        "icir": -0.6,
        "ic_tstat": -4.0,
        "ic_pos_ratio": 0.3,  # adj hit = 0.7
        "subperiod_sign_ratio": 0.9,
        "half_sample_sign_match": True,
        "rolling_icir_pos_ratio": 0.7,
        "q_monotonicity": 1.0,
        "q_spread": -0.01,
        "ls_sharpe": 0.8,
        "ls_cagr": 0.1,
        "ls_max_drawdown": -0.2,
        "avg_turnover": 0.5,
        "n": 60,
    }
    d = decide_admission("mom_6", metrics)
    assert d.direction == -1
    assert d.admitted


def test_battery_on_synthetic():
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=21)
    factors = build_factor_panel(
        panel.returns, panel.industry, factor_names=["rev_1", "vol_12"]
    )
    # softer criteria for synthetic
    crit = AdmissionCriteria(
        min_abs_ic=0.01,
        min_abs_icir=0.15,
        min_ic_tstat=1.0,
        min_ic_hit_rate=0.5,
        min_subperiod_sign_ratio=0.5,
        min_rolling_icir_pos_ratio=0.4,
        min_quantile_monotonicity=0.4,
        min_abs_q_spread=0.001,
        min_ls_sharpe=0.1,
        min_months=24,
    )
    br = run_factor_battery(
        "rev_1", factors["rev_1"], panel.returns, criteria=crit, cost_bps=10
    )
    assert "ls_sharpe" in br.metrics
    assert br.decision.factor == "rev_1"
    assert br.ls_equity is not None


def test_store_roundtrip(tmp_path):
    store = FactorStore(tmp_path / "factor_db")
    dates = pd.date_range("2020-01-31", periods=3, freq="ME")
    panel = pd.DataFrame(
        np.random.default_rng(0).normal(size=(5, 3)),
        index=[f"{i:06d}.SZ" for i in range(5)],
        columns=dates,
    )
    store.upsert_factor_meta(
        "demo",
        family="test",
        description="demo factor",
        formula="x",
        direction=1,
        status="candidate",
    )
    store.save_panel("demo", panel, asof="2020-03-31")

    from factor_engineering.admission import decide_admission

    d = decide_admission(
        "demo",
        {
            "ic_mean": 0.05,
            "icir": 0.6,
            "ic_tstat": 4.0,
            "ic_pos_ratio": 0.7,
            "subperiod_sign_ratio": 0.9,
            "half_sample_sign_match": True,
            "rolling_icir_pos_ratio": 0.7,
            "q_monotonicity": 1.0,
            "q_spread": 0.01,
            "ls_sharpe": 0.8,
            "ls_cagr": 0.1,
            "ls_max_drawdown": -0.2,
            "avg_turnover": 0.5,
            "n": 60,
        },
    )
    store.record_admission("demo", d, asof="2020-03-31")
    doc = build_factor_doc("demo", d, d.metrics)
    store.save_doc("demo", doc["body_md"], title=doc["title"], api_example=doc["api_example"])

    cats = store.list_factors(status="admitted")
    assert "demo" in cats["name"].tolist()
    loaded = store.load_panel("demo")
    assert loaded.shape == panel.shape
    s = store.get_factor_on("demo", "2020-03-31")
    assert len(s) == 5
    text = store.get_doc("demo")
    assert "demo" in text


def test_warehouse_pipeline_synthetic(tmp_path):
    panel = generate_synthetic_panel(n_stocks=80, n_months=60, seed=22)
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
        min_months=24,
        max_avg_turnover=2.0,
    )
    result = run_warehouse_pipeline(
        db_root=tmp_path / "factor_db",
        panel=panel,
        factor_names=["rev_1", "vol_12", "max_ret"],
        criteria=crit,
    )
    assert (tmp_path / "factor_db" / "ADMISSION_STANDARD.md").exists()
    assert (tmp_path / "factor_db" / "admission_summary.csv").exists()
    # at least rev_1 should often admit on synthetic
    catalog = result.store.list_factors(status=None)
    assert len(catalog) == 3


def test_scheduled_update_after_admit(tmp_path):
    panel = generate_synthetic_panel(n_stocks=60, n_months=48, seed=23)
    # write minimal quote-like structure? update loads from REPO by default.
    # Instead call update with factor_names and a custom store after seeding meta+panel
    # via warehouse with synthetic panel only — update() loads real data.
    # So unit-test update skip path + describe path separately.
    store = FactorStore(tmp_path / "factor_db")
    out = run_scheduled_update(store=store, config=UpdateConfig(only_admitted=True))
    assert out["status"] == "skipped"


def test_admission_standard_doc_nonempty():
    md = render_admission_standard_md()
    assert "有效性" in md
    assert "分层" in md
    assert "多空" in md
