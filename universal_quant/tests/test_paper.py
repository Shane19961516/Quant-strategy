"""Paper trader: next-bar fill, kill switch, persistence."""

from __future__ import annotations

from universal_quant.live.runner import catch_up, snapshot
from universal_quant.live.session import empty_account
from universal_quant.live.trader import flatten, step_bar
from universal_quant.tests.test_offline import _synth


def test_paper_enters_on_pending_pulse():
    acc = empty_account(symbols=("SPY",))
    spec = {"name": "SPY", "tick_size": 0.01, "commission_bps": 0.0, "cluster": "Equity"}
    acc["books"]["SPY"]["pending"] = 1.0
    step_bar(acc, "SPY", spec, "2026-01-01 01:00", 100.0, 100.15, 99.95, 100.05, 0.4, 0.004, 0.0, "trend", 40.0, kill=False)
    assert acc["books"]["SPY"]["side"] == 1.0
    assert acc["books"]["SPY"]["weight"] > 0
    assert acc["books"]["SPY"]["pending"] == 0.0


def test_kill_flattens_and_blocks_entry():
    acc = empty_account(symbols=("SPY",))
    spec = {"name": "SPY", "tick_size": 0.01, "commission_bps": 0.0, "cluster": "Equity"}
    acc["books"]["SPY"]["pending"] = 1.0
    step_bar(acc, "SPY", spec, "t1", 100.0, 100.12, 99.96, 100.05, 0.5, 0.005, 1.0, "trend", 50.0, kill=False)
    assert acc["books"]["SPY"]["weight"] > 0
    flatten(acc, acc["books"]["SPY"], spec, "SPY", "B", "t2", 100.2, "kill")
    assert acc["books"]["SPY"]["weight"] == 0.0
    acc["books"]["SPY"]["pending"] = 1.0
    step_bar(acc, "SPY", spec, "t3", 100.3, 100.4, 100.2, 100.35, 0.5, 0.005, 1.0, "trend", 50.0, kill=True)
    assert acc["books"]["SPY"]["weight"] == 0.0
    assert acc["books"]["SPY"]["pending"] == 0.0


def test_catch_up_on_synth():
    df = _synth(120, seed=1)
    acc = empty_account(symbols=("SPY",))
    catch_up(acc, {"SPY": df}, force_last=True)
    snap = snapshot(acc)
    assert snap["nav"] > 0
    assert snap["books"][0]["last_bar"]
    assert snap["books"][0]["symbol"] == "SPY"
