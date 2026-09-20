"""Synthetic-panel tests for the 1-minute data contract (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_eth_perp_arb.config import BacktestConfig
from btc_eth_perp_arb.data import reconstruct_funding_from_premium
from btc_eth_perp_arb.signals import add_signals
from btc_eth_perp_arb.simulator import reversion_confirmed, run_simulator


def _panel(n: int = 2000, gap_at: int | None = None, funding_i: int | None = 1800) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    t0 = 1_700_000_000_000  # dummy ms
    ts = t0 + np.arange(n, dtype=np.int64) * 60_000
    # Cointegrated-ish logs with a mean-reverting spread.
    btc = 100_000 + np.cumsum(rng.normal(0, 8, n))
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.95 * spread[i - 1] + rng.normal(0, 0.0004)
    eth = 3_500 * np.exp(np.log(btc / 100_000) + spread)
    df = pd.DataFrame(
        {
            "bar_open_ts": ts,
            "bar_close_ts": ts + 59_999,
            "btc_open": btc,
            "btc_high": btc * 1.0002,
            "btc_low": btc * 0.9998,
            "btc_close": btc,
            "btc_volume": 10.0,
            "btc_quote_volume": btc * 10.0,
            "btc_n_trades": 100,
            "btc_mark_open": btc,
            "btc_mark_high": btc * 1.0002,
            "btc_mark_low": btc * 0.9998,
            "btc_mark_close": btc,
            "btc_index_close": btc,
            "eth_open": eth,
            "eth_high": eth * 1.0003,
            "eth_low": eth * 0.9997,
            "eth_close": eth,
            "eth_volume": 80.0,
            "eth_quote_volume": eth * 80.0,
            "eth_n_trades": 200,
            "eth_mark_open": eth,
            "eth_mark_high": eth * 1.0003,
            "eth_mark_low": eth * 0.9997,
            "eth_mark_close": eth,
            "eth_index_close": eth,
        }
    )
    df["pair_incomplete"] = np.int8(0)
    df["gap_run"] = np.int32(0)
    df["data_gap"] = np.int8(0)
    df["btc_funding_rate"] = np.nan
    df["eth_funding_rate"] = np.nan
    df["btc_is_funding"] = False
    df["eth_is_funding"] = False
    funding_ts = int(ts[min(funding_i, n - 1)]) if funding_i is not None else -1
    df["btc_next_funding_ts"] = funding_ts
    df["eth_next_funding_ts"] = funding_ts
    df["bar_open"] = pd.to_datetime(df["bar_open_ts"], unit="ms", utc=True)
    if funding_i is not None and 0 <= funding_i < n:
        df.loc[funding_i, "btc_funding_rate"] = 0.0001
        df.loc[funding_i, "eth_funding_rate"] = 0.0001
        df.loc[funding_i, "btc_is_funding"] = True
        df.loc[funding_i, "eth_is_funding"] = True
    if gap_at is not None:
        df.loc[gap_at, ["eth_close", "eth_mark_close", "eth_open", "eth_mark_open"]] = np.nan
        df.loc[gap_at, "pair_incomplete"] = 1
        df.loc[gap_at, "gap_run"] = 1
    return df


def test_no_ffill_on_incomplete_bar():
    df = _panel(n=1600, gap_at=1500)
    out = add_signals(df, BacktestConfig(z_window=100, beta_window=50, corr_window=40))
    assert pd.isna(out.loc[1500, "eth_close"])
    assert out.loc[1500, "pair_incomplete"] == 1
    # Must not carry previous close into the hole.
    assert pd.isna(out.loc[1500, "z"])
    assert pd.isna(out.loc[1500, "log_spread"])


def test_signal_trades_next_bar_open():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        starting_equity=100_000,
        leverage=5.0,
        max_hold_bars=30,
        corr_min=0.0,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    # z known at bar 80 close → first tradable bar is 81 open.
    out.loc[80, "z"] = 3.0
    res = run_simulator(out, cfg)
    enters = res.bars.index[res.bars["event"] == "enter"]
    assert len(enters) > 0
    i = int(enters[0])
    assert i == 81
    trade = res.trades[(res.trades["reason"] == "enter") & (res.trades["leg"] == "btc")].iloc[0]
    assert int(trade["bar_open_ts"]) == int(out.loc[i, "bar_open_ts"])
    open_px = float(out.loc[i, "btc_open"])
    assert abs(trade["fill_px"] - open_px) / open_px < 0.005


def test_no_entry_inside_stop_band():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 5.0
    res = run_simulator(out, cfg)
    assert not (res.bars["event"] == "enter").any()


def test_liquidation_on_mark():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.0,
        stop_z=99.0,
        starting_equity=10_000,
        leverage=10.0,
        mmr=0.5,
        max_hold_bars=500,
        corr_min=0.0,
        adv_participation=1.0,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80:, "z"] = 3.0
    res = run_simulator(out, cfg)
    assert res.summary["liquidation_events"] >= 1 or bool((res.bars["event"] == "liq").any())


def test_funding_only_on_settlement_minute():
    df = _panel(n=2000, funding_i=1900)
    cfg = BacktestConfig(
        z_window=200,
        beta_window=80,
        corr_window=40,
        entry_z=1.0,
        exit_z=0.1,
        starting_equity=100_000,
        leverage=5.0,
        max_hold_bars=400,
    )
    out = add_signals(df, cfg)
    res = run_simulator(out, cfg)
    funded = res.bars["funding_cash"].fillna(0.0)
    settle = res.bars["btc_is_funding"] | res.bars["eth_is_funding"]
    assert float(funded[~settle].abs().sum()) == 0.0
    # If a position spanned the settlement, cashflow is non-zero only there.
    if (res.bars.loc[settle, ["qty_btc", "qty_eth"]].abs().max(axis=1) > 0).any():
        # Position is measured at bar close; funding uses pre-trade hold.
        pass
    assert int(settle.sum()) == 1


def test_reconstruct_funding_clamp():
    idx = pd.date_range("2026-08-01", periods=8 * 60, freq="min", tz="UTC")
    # Premium inside ±5bp of interest → funding should equal interest 1bp.
    s = pd.Series(0.0, index=idx)
    rec = reconstruct_funding_from_premium(s, interval_hours=8)
    assert len(rec) >= 1
    assert rec["last_funding_rate"].iloc[-1] == pytest.approx(0.0001, abs=1e-9)


def test_reversion_confirmed_helper():
    assert reversion_confirmed(2.4, 3.2) is True
    assert reversion_confirmed(-2.4, -3.2) is True
    assert reversion_confirmed(3.0, 2.2) is False  # expanding
    assert reversion_confirmed(2.4, -3.2) is False  # sign flip
    assert reversion_confirmed(2.4, float("nan")) is False


def test_invert_signal_flips_side_not_thresholds():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 3.0
    orig = run_simulator(out, cfg)
    inv = run_simulator(out, BacktestConfig(**{**cfg.__dict__, "invert_signal": True}))
    o = orig.trades[(orig.trades["reason"] == "enter") & (orig.trades["leg"] == "eth")].iloc[0]
    i = inv.trades[(inv.trades["reason"] == "enter") & (inv.trades["leg"] == "eth")].iloc[0]
    assert o["fill_qty"] * i["fill_qty"] < 0
    assert int(o["bar_open_ts"]) == int(i["bar_open_ts"])


def test_cost_hurdle_blocks_small_dislocation():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
        cost_hurdle_bps=50.0,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out["spread_dev_bps"] = 10.0
    out.loc[80, "z"] = 3.0
    res = run_simulator(out, cfg)
    assert not (res.bars["event"] == "enter").any()
    out["spread_dev_bps"] = 80.0
    res2 = run_simulator(out, cfg)
    assert (res2.bars["event"] == "enter").any()


def test_require_reversion_blocks_expanding_allows_shrinking():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
        require_reversion=True,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out["z_lag_1d"] = np.nan
    # Expanding dislocation: |z| rose vs 1d ago → no fill.
    out.loc[80, "z"] = 3.0
    out.loc[80, "z_lag_1d"] = 2.2
    blocked = run_simulator(out, cfg)
    assert not (blocked.bars["event"] == "enter").any()
    # Same sign, |z| already shrinking → fill at t+1 open.
    out.loc[80, "z"] = 2.4
    out.loc[80, "z_lag_1d"] = 3.2
    allowed = run_simulator(out, cfg)
    enters = allowed.bars.index[allowed.bars["event"] == "enter"]
    assert len(enters) > 0
    assert int(enters[0]) == 81
    # Sign flip is not a reversion confirm.
    out.loc[80, "z"] = 2.4
    out.loc[80, "z_lag_1d"] = -3.2
    flipped = run_simulator(out, cfg)
    assert not (flipped.bars["event"] == "enter").any()
    # Baseline (no confirm) still fades the expanding print.
    cfg_off = BacktestConfig(**{**cfg.__dict__, "require_reversion": False})
    out2 = out.copy()
    out2["z"] = 0.0
    out2.loc[80, "z"] = 3.0
    out2["z_lag_1d"] = 2.2
    baseline = run_simulator(out2, cfg_off)
    assert (baseline.bars["event"] == "enter").any()


def test_higher_entry_z_is_stricter_not_looser():
    df = _panel(n=400, funding_i=None)
    loose = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
    )
    tight = BacktestConfig(**{**loose.__dict__, "entry_z": 2.5})
    out = add_signals(df, loose)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 2.2
    assert (run_simulator(out, loose).bars["event"] == "enter").any()
    assert not (run_simulator(out, tight).bars["event"] == "enter").any()
    out.loc[80, "z"] = 2.7
    assert (run_simulator(out, tight).bars["event"] == "enter").any()


def test_cooldown_blocks_immediate_reentry():
    df = _panel(n=500, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
        cooldown_bars=30,
        max_hold_bars=5,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 3.0
    out.loc[90:200, "z"] = 3.0
    res = run_simulator(out, cfg)
    enters = list(res.bars.index[res.bars["event"] == "enter"])
    assert len(enters) >= 1
    if len(enters) >= 2:
        assert enters[1] - enters[0] >= 30

