"""Delivered ETH-USDT-SWAP book: 1m path, hourly UPV B, long-only, 2% risk.

This is the only 1m OKX configuration on the 60d sample that is profitable
in the first half, second half, and all three 20-day folds, without using
in-sample 10x overlay. N=20 / 10x / liquidation-stop is not that book.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from universal_quant import config as cfg
from universal_quant.adapters.okx import get_okx_1m
from universal_quant.backtest.engine import run_backtest
from universal_quant.eth_okx_optimize import _fold_rets, _stats
from universal_quant.eth_okx_study import (
    ETH_SPEC,
    _funding_haircut,
    _one_minute_time_stop,
    _pack,
    hourly_clock_on_1m,
)
from universal_quant.report import plot_drawdown, plot_model_pnl

# Frozen deliverable. Do not "tune" these against a new 60d window without
# a fresh hold-out.
DELIVER = {
    "symbol": "ETH-USDT-SWAP",
    "model": "B",
    "long_only": True,
    "risk_per_trade": 0.02,
    "max_weight": 4.0,
    "bar": "1m",
    "decision": "1h",
    "stop": "2x hourly ATR + 3x trail + 72h time stop",
    "order_split": "iceberg/maker in live; backtest is one fill (100 taker clips destroy expectancy)",
}


@contextmanager
def _deliver_risk():
    saved = {"RISK_PER_TRADE": cfg.RISK_PER_TRADE, "MAX_WEIGHT": cfg.MAX_WEIGHT}
    cfg.RISK_PER_TRADE = float(DELIVER["risk_per_trade"])
    cfg.MAX_WEIGHT = float(DELIVER["max_weight"])
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


def run_delivered(df):
    sig, overlay = hourly_clock_on_1m(df, DELIVER["model"])
    if DELIVER["long_only"]:
        sig = sig.clip(lower=0.0)
    with _one_minute_time_stop(), _deliver_risk():
        return run_backtest(
            df,
            model=DELIVER["model"],
            symbol=DELIVER["symbol"],
            spec=ETH_SPEC,
            signal=sig,
            overlay=overlay,
            prepared=True,
        ), sig


def _fold_pack(eq) -> dict:
    daily = eq.resample("1D").last().dropna()
    a = daily.to_numpy(float)
    n = len(a)
    cuts = [0, n // 3, 2 * n // 3, n]
    folds = _fold_rets(a, cuts)
    mid = n // 2
    return {
        "train_ret": _stats(a[:mid])["ret"],
        "test_ret": _stats(a[mid:])["ret"],
        "fold0": folds[0],
        "fold1": folds[1],
        "fold2": folds[2],
        "all_folds_positive": bool(all(x > 0 for x in folds)),
        "both_halves_positive": bool(_stats(a[:mid])["ret"] > 0 and _stats(a[mid:])["ret"] > 0),
    }


def main() -> int:
    out_dir = cfg.REPORTS_DIR / "eth_okx_1m"
    out_dir.mkdir(parents=True, exist_ok=True)
    df, meta = get_okx_1m(DELIVER["symbol"], days=60, force=False)
    print("deliver", DELIVER)
    res, sig = run_delivered(df)
    eq = res.daily_equity if len(res.daily_equity) else res.equity
    pack = _pack(eq, res.trades)
    if not res.trades.empty:
        pack["funding_pct_of_initial_nav"] = _funding_haircut(res.trades)
    pack.update(_fold_pack(eq))
    pack["n_signal_bars"] = int((sig.abs() > 0).sum())
    report = {"data": meta, "deliver": DELIVER, "performance": pack}
    (out_dir / "eth_okx_deliver.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    plot_model_pnl({"ETH 交付 B 只做多": eq}, out_dir / "eth_1m_deliver_b_pnl.png", "OKX ETH 1m 路径 · 小时决策 · 只做多 · 2% 风险")
    plot_drawdown(eq, out_dir / "eth_1m_deliver_b_dd.png", "交付配置回撤")
    eq.to_csv(out_dir / "eth_1m_deliver_B_equity.csv", header=["equity"])
    if not res.trades.empty:
        res.trades.to_csv(out_dir / "eth_1m_deliver_B_trades.csv", index=False)
    print(
        "ret",
        pack.get("total_return"),
        "mdd",
        pack.get("max_drawdown"),
        "n",
        pack.get("n_trades"),
        "w",
        pack.get("median_weight"),
        "folds",
        pack.get("fold0"),
        pack.get("fold1"),
        pack.get("fold2"),
        "stable",
        pack.get("all_folds_positive"),
    )
    print("Wrote", out_dir / "eth_okx_deliver.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
