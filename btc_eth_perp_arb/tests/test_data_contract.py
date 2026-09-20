"""Synthetic-panel tests for the 1-minute data contract (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_eth_perp_arb.config import BacktestConfig
from btc_eth_perp_arb.data import reconstruct_funding_from_premium
from btc_eth_perp_arb.signals import add_signals, _last_settled_rate
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


def test_exit_z_one_leaves_inside_one_sigma():
    df = _panel(n=400, funding_i=None)
    base = dict(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
        max_hold_bars=200,
    )
    cfg_half = BacktestConfig(**base, exit_z=0.5)
    cfg_one = BacktestConfig(**base, exit_z=1.0)
    out = add_signals(df, cfg_half)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80:89, "z"] = 3.0
    out.loc[90, "z"] = 0.8
    half = run_simulator(out, cfg_half)
    one = run_simulator(out, cfg_one)
    assert int(half.bars.index[half.bars["event"] == "enter"][0]) == 81
    assert int(one.bars.index[one.bars["event"] == "enter"][0]) == 81
    # z=0.8 at bar 90 → tradable at 91: early-exit book flattens, 0.5 book does not.
    assert one.bars.loc[91, "event"] == "exit"
    assert half.bars.loc[91, "event"] != "exit"
    assert abs(float(half.bars.loc[91, "qty_eth"])) > 0


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


def test_funding_carry_uses_prior_settlement_only():
    df = _panel(n=400, funding_i=200)
    df.loc[200, "btc_funding_rate"] = 0.0001
    df.loc[200, "eth_funding_rate"] = 0.0003
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        signal_mode="funding_carry",
        corr_min=0.0,
    )
    out = add_signals(df, cfg)
    # Settlement bar itself must not already see this print as the trading z.
    assert not (pd.notna(out.loc[200, "z_funding"]) and abs(out.loc[200, "z_funding"] - 2.0) < 1e-9)
    assert float(out.loc[201, "fund_diff"]) == pytest.approx(0.0002)
    assert float(out.loc[201, "z_funding"]) == pytest.approx(2.0)
    assert float(out.loc[201, "z"]) == pytest.approx(2.0)


def test_last_settled_rate_lags_one_bar():
    rate = pd.Series([np.nan, 0.0004, np.nan, np.nan])
    flag = pd.Series([False, True, False, False])
    got = _last_settled_rate(rate, flag)
    assert pd.isna(got.iloc[1])
    assert float(got.iloc[2]) == pytest.approx(0.0004)
    assert float(got.iloc[3]) == pytest.approx(0.0004)


def test_raw_spread_is_not_the_7d_residual():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        signal_mode="raw_spread",
        raw_spread_min_periods=80,
        corr_min=0.0,
    )
    out = add_signals(df, cfg)
    both = out[["z", "z_residual", "z_raw"]].dropna()
    assert (both["z"] - both["z_raw"]).abs().max() < 1e-12
    assert (both["z"] - both["z_residual"]).abs().max() > 1e-6
    s = out["log_spread"]
    mu = s.shift(1).expanding(min_periods=80).mean()
    sd = s.shift(1).expanding(min_periods=80).std(ddof=0)
    assert float(out.loc[200, "z_raw"]) == pytest.approx(float((s.iloc[200] - mu.iloc[200]) / sd.iloc[200]))


def test_ratio_btc_eth_z_is_price_ratio_not_log_spread():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        signal_mode="ratio_btc_eth",
        corr_min=-1.0,
    )
    out = add_signals(df, cfg)
    both = out[["z", "z_residual", "z_ratio", "px_ratio"]].dropna()
    assert (both["z"] - both["z_ratio"]).abs().max() < 1e-12
    assert (both["z"] - both["z_residual"]).abs().max() > 1e-6
    ratio = out["btc_mark_close"] / out["eth_mark_close"]
    mu = ratio.shift(1).rolling(50, min_periods=50).mean()
    sd = ratio.shift(1).rolling(50, min_periods=50).std(ddof=0)
    assert float(out.loc[200, "z_ratio"]) == pytest.approx(float((ratio.iloc[200] - mu.iloc[200]) / sd.iloc[200]))
    assert float(out.loc[200, "px_ratio"]) == pytest.approx(float(ratio.iloc[200]))


def test_ratio_high_z_longs_eth_shorts_btc():
    from btc_eth_perp_arb.config import zgrid_config

    df = _panel(n=400, funding_i=None)
    cfg = zgrid_config(
        scheme="ratio",
        z_window=50,
        entry_z=2.0,
        leverage=5.0,
        beta_window=30,
        corr_window=20,
        adv_participation=1.0,
        max_hold_bars=200,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 3.0
    res = run_simulator(out, cfg)
    eth = res.trades[(res.trades["reason"] == "enter") & (res.trades["leg"] == "eth")].iloc[0]
    btc = res.trades[(res.trades["reason"] == "enter") & (res.trades["leg"] == "btc")].iloc[0]
    assert float(eth["fill_qty"]) > 0
    assert float(btc["fill_qty"]) < 0


def test_zgrid_ticket_times_leverage_and_bypasses_floor():
    from btc_eth_perp_arb.config import zgrid_config

    df = _panel(n=400, funding_i=None)
    cfg = zgrid_config(
        scheme="spread",
        z_window=50,
        entry_z=2.0,
        leverage=5.0,
        beta_window=30,
        corr_window=20,
        adv_participation=1.0,
        max_hold_bars=200,
    )
    assert cfg.min_leg_notional == 0.0
    assert cfg.eth_ticket_usd == 100.0
    assert cfg.stop_z >= 1e8
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 3.0
    res = run_simulator(out, cfg)
    eth = res.trades[(res.trades["reason"] == "enter") & (res.trades["leg"] == "eth")].iloc[0]
    notion = abs(float(eth["fill_qty"]) * float(eth["fill_px"]))
    assert notion == pytest.approx(500.0, rel=0.08)
    # No stop even if |z| blows out.
    out.loc[81:120, "z"] = 50.0
    held = run_simulator(out, cfg)
    assert not (held.bars["event"] == "stop").any()


def test_default_min_leg_notional_still_blocks_tiny_tickets():
    df = _panel(n=400, funding_i=None)
    cfg = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=1_000.0,
        leverage=5.0,
        corr_min=0.0,
        adv_participation=1.0,
        eth_ticket_usd=20.0,
        notional_times_leverage=False,
        min_leg_notional=50.0,
    )
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 3.0
    res = run_simulator(out, cfg)
    assert not (res.bars["event"] == "enter").any()


def test_resample_5m_ohlc_funding_and_no_ffill():
    from btc_eth_perp_arb.data import resample_panel

    df = _panel(n=15, funding_i=0, gap_at=7)
    t0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC, 5m aligned
    df["bar_open_ts"] = t0 + np.arange(15, dtype=np.int64) * 60_000
    df["bar_close_ts"] = df["bar_open_ts"] + 59_999
    df["bar_open"] = pd.to_datetime(df["bar_open_ts"], unit="ms", utc=True)
    df.loc[0, "btc_is_funding"] = True
    df.loc[0, "btc_funding_rate"] = 0.0001
    df.loc[0, "eth_is_funding"] = True
    df.loc[0, "eth_funding_rate"] = 0.0002
    out = resample_panel(df, 5)
    assert len(out) == 3
    assert int(out.iloc[0]["bar_open_ts"]) == t0
    assert float(out.iloc[0]["btc_open"]) == pytest.approx(float(df.iloc[0]["btc_open"]))
    assert float(out.iloc[0]["btc_close"]) == pytest.approx(float(df.iloc[4]["btc_close"]))
    assert float(out.iloc[0]["btc_high"]) == pytest.approx(float(df.iloc[0:5]["btc_high"].max()))
    assert float(out.iloc[0]["btc_quote_volume"]) == pytest.approx(float(df.iloc[0:5]["btc_quote_volume"].sum()))
    assert bool(out.iloc[0]["btc_is_funding"]) is True
    assert float(out.iloc[0]["btc_funding_rate"]) == pytest.approx(0.0001)
    assert int(out.iloc[0]["pair_incomplete"]) == 0
    # gap at 1m index 7 lives in the second 5m bucket; do not ffill ETH.
    assert int(out.iloc[1]["pair_incomplete"]) == 1
    assert pd.isna(df.loc[7, "eth_close"])
    ident = resample_panel(df, 1)
    assert len(ident) == 15


def test_5m_z_lag_is_one_calendar_day_not_1440_bars():
    from btc_eth_perp_arb.data import resample_panel

    df = _panel(n=2000, funding_i=None)
    t0 = 1_704_067_200_000
    df["bar_open_ts"] = t0 + np.arange(2000, dtype=np.int64) * 60_000
    df["bar_close_ts"] = df["bar_open_ts"] + 59_999
    five = resample_panel(df, 5)
    cfg = BacktestConfig(z_window=50, beta_window=30, corr_window=20, corr_min=-1.0)
    out = add_signals(five, cfg)
    # 1 day / 5m = 288 bars.
    both = out["z_residual"].dropna()
    i = int(both.index[300])
    assert float(out.loc[i, "z_lag_1d"]) == pytest.approx(float(out.loc[i - 288, "z_residual"]))


def test_zgrid_leverage_filter_is_twelve_books():
    from btc_eth_perp_arb.eval_zgrid import all_combos, parse_leverages

    assert parse_leverages("5") == (5.0,)
    combos = all_combos((5.0,))
    assert len(combos) == 12
    assert {c[-1] for c in combos} == {5.0}


def test_repair_baseline_is_frozen_5m_5x_shell():
    from btc_eth_perp_arb.config import (
        REPAIR_BAR_MINUTES,
        REPAIR_COST_HURDLE_BPS,
        REPAIR_ENTRY_Z,
        REPAIR_LEVERAGE,
        REPAIR_SCHEME,
        REPAIR_Z_WINDOW,
        repair_baseline_config,
    )

    assert REPAIR_BAR_MINUTES == 5
    assert REPAIR_SCHEME == "spread"
    assert REPAIR_Z_WINDOW == 120
    assert REPAIR_ENTRY_Z == 2.0
    assert REPAIR_LEVERAGE == 5.0
    assert REPAIR_COST_HURDLE_BPS == 30.0
    b0 = repair_baseline_config()
    assert b0.cost_hurdle_bps == 0.0
    assert b0.leverage == 5.0
    assert b0.z_window == 120
    assert b0.entry_z == 2.0
    assert b0.exit_z == 0.5
    assert b0.stop_z >= 1e8
    assert b0.signal_mode == "residual"
    assert b0.invert_signal is False
    assert b0.eth_ticket_usd == 100.0
    assert b0.cooldown_bars == 0
    f1 = repair_baseline_config(cost_hurdle_bps=REPAIR_COST_HURDLE_BPS)
    assert f1.cost_hurdle_bps == 30.0
    assert f1.z_window == b0.z_window
    assert f1.entry_z == b0.entry_z
    assert f1.leverage == b0.leverage
    assert f1.signal_mode == "residual"


def test_hedge_residual_z_matches_cum_return_and_differs_from_log_spread():
    df = _panel(n=800, funding_i=None)
    # Stretch ETH so return β is not 1; log-spread z and hedge z must differ.
    scale = np.linspace(1.0, 1.6, len(df))
    for col in (
        "eth_open",
        "eth_high",
        "eth_low",
        "eth_close",
        "eth_mark_open",
        "eth_mark_high",
        "eth_mark_low",
        "eth_mark_close",
    ):
        df[col] = df[col] * scale
    cfg = BacktestConfig(
        signal_mode="hedge_residual",
        z_window=50,
        beta_window=50,
        corr_window=50,
        corr_min=-1.0,
    )
    out = add_signals(df, cfg)
    both = out[["z", "z_hedge", "z_residual", "ret_resid", "cum_resid", "beta"]].dropna()
    assert len(both) > 100
    assert (both["z"] - both["z_hedge"]).abs().max() < 1e-12
    assert (both["z"] - both["z_residual"]).abs().max() > 1e-3
    i = int(both.index[200])
    expected = out["eth_mark_ret"] - out["beta"] * out["btc_mark_ret"]
    assert float(out.loc[i, "ret_resid"]) == pytest.approx(float(expected.iloc[i]))
    cum = out["ret_resid"].cumsum()
    mu = cum.shift(1).rolling(50, min_periods=50).mean()
    sd = cum.shift(1).rolling(50, min_periods=50).std(ddof=0)
    assert float(out.loc[i, "z_hedge"]) == pytest.approx(
        float((cum.iloc[i] - mu.iloc[i]) / sd.iloc[i])
    )
    assert float(out.loc[i, "spread_dev_bps"]) == pytest.approx(
        float((cum.iloc[i] - mu.iloc[i]) * 1e4)
    )


def test_hedge_residual_high_z_shorts_eth():
    from btc_eth_perp_arb.config import repair_f2_config

    df = _panel(n=400, funding_i=None)
    cfg = repair_f2_config(adv_participation=1.0)
    assert cfg.signal_mode == "hedge_residual"
    assert cfg.cost_hurdle_bps == 30.0
    out = add_signals(df, cfg)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out["spread_dev_bps"] = 80.0
    out.loc[80, "z"] = 3.0
    res = run_simulator(out, cfg)
    eth = res.trades[(res.trades["reason"] == "enter") & (res.trades["leg"] == "eth")].iloc[0]
    btc = res.trades[(res.trades["reason"] == "enter") & (res.trades["leg"] == "btc")].iloc[0]
    assert float(eth["fill_qty"]) < 0
    assert float(btc["fill_qty"]) > 0


def test_donchian_channel_is_lagged_rolling_max_min():
    from btc_eth_perp_arb.config import DonchianConfig
    from btc_eth_perp_arb.donchian import add_donchian

    df = _panel(n=300, funding_i=None)
    cfg = DonchianConfig(window=20, bar_minutes=1, adv_participation=1.0)
    out = add_donchian(df, cfg)
    h = out["btc_mark_high"]
    lo = out["btc_mark_low"]
    exp_u = h.shift(1).rolling(20, min_periods=20).max()
    exp_l = lo.shift(1).rolling(20, min_periods=20).min()
    both = out["btc_donch_upper"].dropna()
    i = int(both.index[50])
    assert float(out.loc[i, "btc_donch_upper"]) == pytest.approx(float(exp_u.iloc[i]))
    assert float(out.loc[i, "btc_donch_lower"]) == pytest.approx(float(exp_l.iloc[i]))
    # Current bar's high must not leak into the channel used at that bar.
    assert float(out.loc[i, "btc_donch_upper"]) == pytest.approx(float(h.iloc[i - 20 : i].max()))


def test_donchian_break_fills_next_open_and_sizes_tenth_times_10x():
    from btc_eth_perp_arb.config import DonchianConfig
    from btc_eth_perp_arb.donchian import add_donchian, run_donchian

    df = _panel(n=400, funding_i=None)
    cfg = DonchianConfig(
        window=20,
        bar_minutes=1,
        adv_participation=1.0,
        starting_equity=1_000.0,
        fraction=0.10,
        leverage=10.0,
        tp_multiple=5.0,
    )
    out = add_donchian(df, cfg)
    out["btc_donch_side"] = np.int8(0)
    out["eth_donch_side"] = np.int8(0)
    out.loc[80, "btc_donch_side"] = np.int8(1)
    res = run_donchian(out, symbol="BTCUSDT", cfg=cfg)
    enters = res.bars.index[res.bars["event"] == "enter"]
    assert len(enters) > 0
    assert int(enters[0]) == 81
    fill = res.trades[(res.trades["reason"] == "enter")].iloc[0]
    open_px = float(out.loc[81, "btc_open"])
    exp_qty = np.floor((1_000.0 / open_px) / 0.001 + 1e-12) * 0.001
    assert float(fill["fill_qty"]) == pytest.approx(exp_qty)
    assert float(fill["fill_qty"]) > 0


def test_donchian_tp_five_times_margin_no_early_stop():
    from btc_eth_perp_arb.config import DonchianConfig
    from btc_eth_perp_arb.donchian import add_donchian, run_donchian

    df = _panel(n=400, funding_i=None)
    cfg = DonchianConfig(
        window=20,
        bar_minutes=1,
        adv_participation=1.0,
        starting_equity=1_000.0,
        fraction=0.10,
        leverage=10.0,
        tp_multiple=5.0,
        mmr=0.004,
    )
    out = add_donchian(df, cfg)
    out["btc_donch_side"] = np.int8(0)
    out.loc[80, "btc_donch_side"] = np.int8(1)
    # After fill at 81, a 5% adverse mark must not stop out (isolated 10x liq ~9.6%).
    px81 = float(out.loc[81, "btc_mark_close"])
    out.loc[82:90, "btc_mark_close"] = px81 * 0.95
    out.loc[82:90, "btc_open"] = px81 * 0.95
    mild = run_donchian(out, symbol="BTCUSDT", cfg=cfg)
    assert not (mild.bars.loc[82:90, "event"] == "tp").any()
    assert not (mild.bars.loc[82:90, "event"] == "liq").any()
    assert (mild.bars["event"] == "enter").any()
    # 52% favorable move on notional ≈ 5.2× margin → TP at the next open.
    out.loc[82:90, "btc_mark_close"] = px81 * 1.52
    out.loc[83:90, "btc_open"] = px81 * 1.52
    won = run_donchian(out, symbol="BTCUSDT", cfg=cfg)
    tps = won.bars.index[won.bars["event"] == "tp"]
    assert len(tps) > 0
    assert int(tps[0]) >= 83


def test_donchian_50x_sizes_five_times_equity_and_liqs_on_small_adverse():
    from btc_eth_perp_arb.config import donchian_50_config
    from btc_eth_perp_arb.donchian import add_donchian, run_donchian

    df = _panel(n=400, funding_i=None)
    cfg = donchian_50_config(window=20, bar_minutes=1, adv_participation=1.0)
    assert cfg.leverage == 50.0
    out = add_donchian(df, cfg)
    out["btc_donch_side"] = np.int8(0)
    out["eth_donch_side"] = np.int8(0)
    out.loc[80, "btc_donch_side"] = np.int8(1)
    res = run_donchian(out, symbol="BTCUSDT", cfg=cfg)
    fill = res.trades[(res.trades["reason"] == "enter")].iloc[0]
    open_px = float(out.loc[81, "btc_open"])
    exp_qty = np.floor((5_000.0 / open_px) / 0.001 + 1e-12) * 0.001
    assert float(fill["fill_qty"]) == pytest.approx(exp_qty)
    # Isolated 50x liqs around a 1.6% adverse move; 5% must flatten.
    px81 = float(out.loc[81, "btc_mark_close"])
    out.loc[82:90, "btc_mark_close"] = px81 * 0.95
    out.loc[82:90, "btc_open"] = px81 * 0.95
    out.loc[82:90, "btc_high"] = px81 * 0.96
    out.loc[82:90, "btc_low"] = px81 * 0.94
    blown = run_donchian(out, symbol="BTCUSDT", cfg=cfg)
    assert (blown.bars.loc[82:90, "event"] == "liq").any()


def test_donchian_atr_is_shifted_no_current_bar():
    from btc_eth_perp_arb.donchian_exits import add_atr

    df = _panel(n=200, funding_i=None)
    out = add_atr(df, n=14)
    h = df["btc_mark_high"].astype(float)
    low = df["btc_mark_low"].astype(float)
    c = df["btc_mark_close"].astype(float)
    prev = c.shift(1)
    tr = pd.concat([(h - low), (h - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    i = 40
    assert float(out.loc[i, "btc_atr_14"]) == pytest.approx(float(atr.shift(1).iloc[i]))
    assert float(out.loc[i, "btc_atr_14"]) != pytest.approx(float(tr.iloc[i]))


def test_donchian_train_excursions_censor_and_mae_mfe():
    from btc_eth_perp_arb.config import DonchianConfig
    from btc_eth_perp_arb.donchian_exits import (
        ExitStudySpec,
        breakout_excursions,
        prepare_exit_panel,
    )

    df = _panel(n=400, funding_i=None)
    cfg = DonchianConfig(window=20, bar_minutes=1, adv_participation=1.0)
    out = prepare_exit_panel(df, cfg)
    out["btc_donch_side"] = np.int8(0)
    out.loc[80, "btc_donch_side"] = np.int8(1)
    fill_i = 81
    px = float(out.loc[fill_i, "btc_open"])
    out.loc[82:90, "btc_mark_high"] = px * 1.08
    out.loc[82:90, "btc_mark_low"] = px * 0.999
    out.loc[82:90, "btc_mark_close"] = px * 1.04
    out.loc[91:100, "btc_mark_high"] = px * 1.01
    out.loc[91:100, "btc_mark_low"] = px * 0.97
    out.loc[91:100, "btc_mark_close"] = px * 0.99
    out.loc[120:, "btc_mark_high"] = px * 1.40
    out.loc[120:, "btc_mark_low"] = px * 1.30
    out.loc[120:, "btc_mark_close"] = px * 1.35
    train_end_ts = int(out.loc[110, "bar_open_ts"])
    spec = ExitStudySpec(
        train_start_ts=int(out.loc[0, "bar_open_ts"]),
        train_end_ts=train_end_ts,
        mode="independent",
    )
    trades = breakout_excursions(out, symbol="BTCUSDT", cfg=cfg, spec=spec)
    assert len(trades) == 1
    row = trades.iloc[0]
    assert float(row["mfe_price_pct"]) == pytest.approx(0.08, abs=0.015)
    assert float(row["mae_price_pct"]) == pytest.approx(0.03, abs=0.015)
    assert int(row["end_ts"]) <= train_end_ts
    assert float(row["mfe_price_pct"]) < 0.20
    assert row["exit_reason"] == "censored_train_end"


def test_donchian_half_channel_exit_is_lagged_close_break():
    from btc_eth_perp_arb.config import DonchianConfig
    from btc_eth_perp_arb.donchian_exits import (
        ExitStudySpec,
        breakout_excursions,
        prepare_exit_panel,
    )

    df = _panel(n=400, funding_i=None)
    cfg = DonchianConfig(window=20, bar_minutes=1, adv_participation=1.0)
    out = prepare_exit_panel(df, cfg)
    out["btc_donch_side"] = np.int8(0)
    out.loc[80, "btc_donch_side"] = np.int8(1)
    px = float(out.loc[81, "btc_open"])
    # 3% adverse close — through the 10-bar exit channel, not the ~9.6% liq.
    out.loc[82:90, "btc_mark_close"] = px * 0.97
    out.loc[82:90, "btc_mark_low"] = px * 0.969
    out.loc[82:90, "btc_mark_high"] = px * 0.975
    spec = ExitStudySpec(
        train_start_ts=int(out.loc[0, "bar_open_ts"]),
        train_end_ts=int(out.loc[200, "bar_open_ts"]),
        mode="independent",
    )
    trades = breakout_excursions(out, symbol="BTCUSDT", cfg=cfg, spec=spec)
    assert len(trades) == 1
    assert bool(trades.iloc[0]["hit_72_exit"])
    assert float(trades.iloc[0]["mae_at_72_price_pct"]) < 0.08


def test_composite_is_equal_weight_mean_and_fills_next_open():
    from btc_eth_perp_arb.composite import add_composite, run_composite
    from btc_eth_perp_arb.config import composite_config

    df = _panel(n=500, funding_i=None)
    cfg = composite_config(window=20, slope_lag=5, bar_minutes=1, adv_participation=1.0)
    out = add_composite(df, cfg)
    finite = out["btc_z_comp"].dropna()
    i = int(finite.index[40])
    expected = 0.25 * (
        float(out.loc[i, "btc_z_ma"])
        + float(out.loc[i, "btc_z_slope"])
        + float(out.loc[i, "btc_z_vol"])
        + float(out.loc[i, "btc_z_pos"])
    )
    assert float(out.loc[i, "btc_z_comp"]) == pytest.approx(expected)
    out["btc_comp_side"] = np.int8(0)
    out["eth_comp_side"] = np.int8(0)
    out.loc[80, "btc_comp_side"] = np.int8(1)
    res = run_composite(out, symbol="BTCUSDT", cfg=cfg)
    enters = res.bars.index[res.bars["event"] == "enter"]
    assert len(enters) > 0
    assert int(enters[0]) == 81
    fill = res.trades[(res.trades["reason"] == "enter")].iloc[0]
    open_px = float(out.loc[81, "btc_open"])
    exp_qty = np.floor((1_000.0 / open_px) / 0.001 + 1e-12) * 0.001
    assert float(fill["fill_qty"]) == pytest.approx(exp_qty)


def test_composite_two_pct_stop_and_no_fixed_tp():
    from btc_eth_perp_arb.composite import add_composite, run_composite
    from btc_eth_perp_arb.config import composite_config

    df = _panel(n=500, funding_i=None)
    cfg = composite_config(window=20, slope_lag=5, bar_minutes=1, adv_participation=1.0)
    out = add_composite(df, cfg)
    out["btc_comp_side"] = np.int8(0)
    out.loc[80:95, "btc_comp_side"] = np.int8(1)
    px81 = float(out.loc[81, "btc_mark_close"])
    out.loc[82:90, "btc_mark_close"] = px81 * 0.99
    out.loc[82:90, "btc_open"] = px81 * 0.99
    mild = run_composite(out, symbol="BTCUSDT", cfg=cfg)
    assert not (mild.bars.loc[82:90, "event"] == "stop").any()
    assert not (mild.bars.loc[82:90, "event"] == "liq").any()
    out.loc[82:90, "btc_mark_close"] = px81 * 0.975
    out.loc[83:90, "btc_open"] = px81 * 0.975
    stopped = run_composite(out, symbol="BTCUSDT", cfg=cfg)
    assert (stopped.bars.loc[82:90, "event"] == "stop").any()
    out.loc[82:90, "btc_mark_close"] = px81 * 1.20
    out.loc[83:90, "btc_open"] = px81 * 1.20
    won = run_composite(out, symbol="BTCUSDT", cfg=cfg)
    assert not (won.bars.loc[82:90, "event"] == "tp").any()


def test_composite_future_bars_do_not_change_prior_side():
    from btc_eth_perp_arb.composite import add_composite
    from btc_eth_perp_arb.config import composite_config

    df = _panel(n=500, funding_i=None)
    cfg = composite_config(window=20, slope_lag=5, bar_minutes=1)
    a = add_composite(df, cfg)
    bdf = df.copy()
    bdf.loc[300:, "btc_mark_close"] = bdf.loc[300:, "btc_mark_close"] * 1.5
    bdf.loc[300:, "btc_mark_high"] = bdf.loc[300:, "btc_mark_high"] * 1.5
    b = add_composite(bdf, cfg)
    i = 120
    assert int(a.loc[i, "btc_comp_side"]) == int(b.loc[i, "btc_comp_side"])
    if np.isfinite(a.loc[i, "btc_z_comp"]) and np.isfinite(b.loc[i, "btc_z_comp"]):
        assert float(a.loc[i, "btc_z_comp"]) == pytest.approx(float(b.loc[i, "btc_z_comp"]))


def test_composite_hourly_config_only_changes_bar_minutes():
    from btc_eth_perp_arb.config import composite_config, composite_hourly_config

    c0 = composite_config()
    c1 = composite_hourly_config()
    assert c0.bar_minutes == 5
    assert c1.bar_minutes == 60
    assert c1.window == c0.window
    assert c1.slope_lag == c0.slope_lag
    assert c1.entry_z == c0.entry_z
    assert c1.leverage == c0.leverage
    assert c1.stop_price_pct == c0.stop_price_pct
    assert c1.weights == c0.weights
    assert c1.flatten_in_band is True


def test_composite_hold_skips_dead_band_scratch():
    from btc_eth_perp_arb.composite import add_composite, run_composite
    from btc_eth_perp_arb.config import composite_hold_config

    df = _panel(n=500, funding_i=None)
    cfg = composite_hold_config(window=20, slope_lag=5, bar_minutes=1, adv_participation=1.0)
    assert cfg.flatten_in_band is False
    out = add_composite(df, cfg)
    out["btc_comp_side"] = np.int8(0)
    out.loc[80, "btc_comp_side"] = np.int8(1)
    out.loc[82:90, "btc_comp_side"] = np.int8(0)
    px81 = float(out.loc[81, "btc_mark_close"])
    out.loc[82:90, "btc_mark_close"] = px81 * 1.005
    out.loc[82:90, "btc_open"] = px81 * 1.005
    held = run_composite(out, symbol="BTCUSDT", cfg=cfg)
    assert not (held.bars.loc[82:90, "event"] == "exit").any()
    assert (held.bars["event"] == "enter").any()


def test_composite_waterfall_factories_are_one_knob():
    from btc_eth_perp_arb.config import (
        composite_daily_config,
        composite_hold_config,
        composite_hourly_config,
        composite_z2_config,
    )

    c1 = composite_hourly_config()
    c2 = composite_hold_config()
    c3 = composite_z2_config()
    c4 = composite_daily_config()
    assert c2.bar_minutes == c1.bar_minutes
    assert c2.flatten_in_band is False
    assert c2.entry_z == c1.entry_z
    assert c3.entry_z == 2.0
    assert c3.flatten_in_band is True
    assert c3.bar_minutes == c1.bar_minutes
    assert c4.bar_minutes == 1440
    assert c4.entry_z == c1.entry_z
    assert c4.window == c1.window


def test_delivery_1x_config_only_changes_leverage():
    from btc_eth_perp_arb.config import delivery_1x_config, delivery_config

    a = delivery_config()
    b = delivery_1x_config()
    assert a.leverage == 2.0
    assert b.leverage == 1.0
    assert b.z_window == a.z_window
    assert b.entry_hour_utc == a.entry_hour_utc
    assert b.entry_z == a.entry_z
    assert b.exit_z == a.exit_z
    assert b.stop_z == a.stop_z
    assert b.cost_hurdle_bps == a.cost_hurdle_bps
    assert b.starting_equity == a.starting_equity
    assert b.signal_mode == a.signal_mode


def test_delivery_grid_only_changes_window_and_entry_z():
    from btc_eth_perp_arb.config import delivery_config, delivery_grid_config

    a = delivery_config()
    g = delivery_grid_config(z_days=14, entry_z=2.5)
    assert g.z_window == 14 * 1440
    assert g.entry_z == 2.5
    assert g.stop_z == a.stop_z == 4.0
    assert g.exit_z == a.exit_z
    assert g.leverage == a.leverage
    assert g.beta_window == a.beta_window
    assert g.corr_window == a.corr_window
    assert g.entry_hour_utc == a.entry_hour_utc
    z4 = delivery_grid_config(z_days=3, entry_z=4.0)
    assert z4.z_window == 3 * 1440
    assert z4.entry_z == 4.0
    assert z4.stop_z == 5.0
    assert z4.exit_z == a.exit_z


def test_delivery_chosen_config_is_14d_z2_and_sweep_only_changes_leverage():
    from btc_eth_perp_arb.config import (
        DELIVERY_SWEEP_LEVERAGES,
        delivery_chosen_config,
        delivery_grid_config,
    )

    c = delivery_chosen_config()
    g = delivery_grid_config(z_days=14, entry_z=2.0)
    assert c.z_window == g.z_window == 14 * 1440
    assert c.entry_z == g.entry_z == 2.0
    assert c.stop_z == 4.0
    assert c.exit_z == 0.5
    assert c.leverage == 2.0
    assert c.entry_hour_utc == 1
    assert DELIVERY_SWEEP_LEVERAGES == (1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
    x50 = delivery_chosen_config(leverage=50.0)
    assert x50.leverage == 50.0
    assert x50.z_window == c.z_window
    assert x50.entry_z == c.entry_z
    assert x50.exit_z == c.exit_z
    assert x50.stop_z == c.stop_z
    assert x50.entry_hour_utc == c.entry_hour_utc


def test_delivery_e3_e4_are_one_knob_and_e4_stop_is_tradable():
    from btc_eth_perp_arb.config import (
        delivery_config,
        delivery_e3_config,
        delivery_e4_config,
    )
    from btc_eth_perp_arb.simulator import run_simulator
    from btc_eth_perp_arb.signals import add_signals

    a = delivery_config()
    e3 = delivery_e3_config()
    e4 = delivery_e4_config()
    assert e3.entry_z == 3.0
    assert e3.stop_z == a.stop_z == 4.0
    assert e3.exit_z == a.exit_z
    assert e3.leverage == a.leverage
    assert e3.z_window == a.z_window
    assert e3.entry_hour_utc == a.entry_hour_utc
    assert e4.entry_z == 4.0
    assert e4.stop_z == 5.0
    assert e4.exit_z == a.exit_z
    assert e4.leverage == a.leverage
    # Band logic on a stripped shell: 01:00 / 30bp hurdle / 1d corr would
    # block this 400-bar dummy. Factories above already freeze those knobs.
    loose = BacktestConfig(
        z_window=50,
        beta_window=30,
        corr_window=20,
        entry_z=2.0,
        exit_z=0.5,
        stop_z=4.0,
        starting_equity=100_000,
        leverage=2.0,
        corr_min=0.0,
        adv_participation=1.0,
        cost_hurdle_bps=0.0,
        entry_hour_utc=None,
    )
    e3_sim = BacktestConfig(**{**loose.__dict__, "entry_z": 3.0})
    e4_sim = BacktestConfig(**{**loose.__dict__, "entry_z": 4.0, "stop_z": 5.0})
    empty = BacktestConfig(**{**loose.__dict__, "entry_z": 4.0, "stop_z": 4.0})
    df = _panel(n=400, funding_i=None)
    out = add_signals(df, loose)
    out["z"] = 0.0
    out["beta"] = 1.0
    out["corr"] = 0.9
    out.loc[80, "z"] = 3.2
    assert (run_simulator(out, loose).bars["event"] == "enter").any()
    assert (run_simulator(out, e3_sim).bars["event"] == "enter").any()
    assert not (run_simulator(out, e4_sim).bars["event"] == "enter").any()
    out.loc[80, "z"] = 2.2
    assert (run_simulator(out, loose).bars["event"] == "enter").any()
    assert not (run_simulator(out, e3_sim).bars["event"] == "enter").any()
    out.loc[80, "z"] = 4.2
    assert not (run_simulator(out, empty).bars["event"] == "enter").any()
    assert (run_simulator(out, e4_sim).bars["event"] == "enter").any()


def test_composite_hourly_1x_config_only_changes_leverage():
    from btc_eth_perp_arb.config import (
        composite_hourly_1x_config,
        composite_hourly_config,
    )

    c10 = composite_hourly_config()
    c1 = composite_hourly_1x_config()
    assert c10.leverage == 10.0
    assert c1.leverage == 1.0
    assert c1.bar_minutes == c10.bar_minutes == 60
    assert c1.window == c10.window
    assert c1.slope_lag == c10.slope_lag
    assert c1.fraction == c10.fraction
    assert c1.entry_z == c10.entry_z
    assert c1.stop_price_pct == c10.stop_price_pct
    assert c1.flatten_in_band is True
    assert c1.weights == c10.weights


def test_composite_1x_sizes_one_tenth_of_10x():
    from btc_eth_perp_arb.composite import add_composite, run_composite
    from btc_eth_perp_arb.config import (
        composite_hourly_1x_config,
        composite_hourly_config,
    )

    df = _panel(n=500, funding_i=None)
    # $1000 start × 1/10 × 1x ≈ $100 notional can round below BTC qty step.
    cfg10 = composite_hourly_config(
        window=20,
        slope_lag=5,
        bar_minutes=1,
        adv_participation=1.0,
        starting_equity=10_000.0,
    )
    cfg1 = composite_hourly_1x_config(
        window=20,
        slope_lag=5,
        bar_minutes=1,
        adv_participation=1.0,
        starting_equity=10_000.0,
    )
    out = add_composite(df, cfg10)
    out["btc_comp_side"] = np.int8(0)
    out["eth_comp_side"] = np.int8(0)
    out.loc[80, "btc_comp_side"] = np.int8(1)
    r10 = run_composite(out, symbol="BTCUSDT", cfg=cfg10)
    r1 = run_composite(out, symbol="BTCUSDT", cfg=cfg1)
    assert not r10.trades.empty
    assert not r1.trades.empty
    fill10 = r10.trades[(r10.trades["reason"] == "enter")].iloc[0]
    fill1 = r1.trades[(r1.trades["reason"] == "enter")].iloc[0]
    open_px = float(out.loc[81, "btc_open"])
    exp10 = np.floor((10_000.0 / open_px) / 0.001 + 1e-12) * 0.001
    exp1 = np.floor((1_000.0 / open_px) / 0.001 + 1e-12) * 0.001
    assert float(fill10["fill_qty"]) == pytest.approx(exp10)
    assert float(fill1["fill_qty"]) == pytest.approx(exp1)
    # Qty step (0.001 BTC) makes the ratio inexact; 1x notional is 1/10 of 10x.


def test_bar_arb_config_scales_calendar_windows_and_drops_hour_gate():
    from dataclasses import asdict
    from btc_eth_perp_arb.config import (
        BAR_ARB_MINUTES,
        BAR_ARB_Z_DAYS,
        _calendar_bars,
        bar_arb_config,
        delivery_chosen_config,
    )

    base = delivery_chosen_config()
    assert BAR_ARB_MINUTES == (5, 10, 60, 120)
    assert BAR_ARB_Z_DAYS == 14
    expected_z = {5: 4032, 10: 2016, 60: 336, 120: 168}
    frozen = (
        "leverage",
        "entry_z",
        "exit_z",
        "stop_z",
        "corr_min",
        "cost_hurdle_bps",
        "starting_equity",
        "signal_mode",
    )
    for minutes in BAR_ARB_MINUTES:
        cfg = bar_arb_config(bar_minutes=minutes)
        assert cfg.z_window == expected_z[minutes]
        assert cfg.z_window == _calendar_bars(14 * 1440, minutes)
        assert cfg.beta_window == _calendar_bars(1440, minutes)
        assert cfg.corr_window == _calendar_bars(1440, minutes)
        assert cfg.max_hold_bars == _calendar_bars(5 * 1440, minutes)
        assert cfg.cooldown_bars == _calendar_bars(1440, minutes)
        assert cfg.raw_spread_min_periods == _calendar_bars(30 * 1440, minutes)
        assert cfg.entry_hour_utc is None
        for key in frozen:
            assert getattr(cfg, key) == getattr(base, key)

    a5 = asdict(bar_arb_config(bar_minutes=5))
    a120 = asdict(bar_arb_config(bar_minutes=120))
    changed = {k for k in a5 if a5[k] != a120[k]}
    assert changed == {
        "z_window",
        "beta_window",
        "corr_window",
        "max_hold_bars",
        "cooldown_bars",
        "raw_spread_min_periods",
    }



