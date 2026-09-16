"""ETH-USD 1h study as a proxy for OKX ETHUSDT perp + leverage overlays."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from brent_quant.metrics import performance_from_equity
from universal_quant import config as cfg
from universal_quant.adapters.yahoo import get_data
from universal_quant.backtest.engine import run_backtest
from universal_quant.portfolio.risk_budget import calendar_cagr
from universal_quant.report import plot_drawdown, plot_model_pnl


def _pack(eq: pd.Series, trades: pd.DataFrame | None = None) -> dict:
    st = performance_from_equity(eq if not isinstance(eq, pd.Series) else eq, trades)
    out = {
        "calendar_cagr": calendar_cagr(eq),
        "cagr_252": st.get("cagr"),
        "total_return": st.get("total_return"),
        "sharpe": st.get("sharpe"),
        "max_drawdown": st.get("max_drawdown"),
        "ann_vol": st.get("ann_vol"),
        "n_trades": st.get("n_trades"),
        "profit_factor": st.get("profit_factor"),
        "expectancy": st.get("expectancy"),
        "win_rate": st.get("win_rate"),
        "avg_holding_bars": st.get("avg_holding_bars"),
        "start": st.get("start"),
        "end": st.get("end"),
        "end_nav": st.get("end_nav"),
    }
    if trades is not None and not trades.empty:
        out["median_weight"] = float(trades["weight"].median())
        out["mean_weight"] = float(trades["weight"].mean())
        out["p90_weight"] = float(trades["weight"].quantile(0.9))
        out["long_share"] = float((trades["side"] > 0).mean())
        reasons = trades["exit_reason"].value_counts(normalize=True).to_dict()
        out["exit_reasons"] = {str(k): float(v) for k, v in reasons.items()}
    return out


def _funding_haircut(trades: pd.DataFrame, rate_8h: float = 0.0001) -> float:
    """Rough OKX-style funding: 8h, paid on notional. Sign ignored (always pay |rate|)."""
    if trades is None or trades.empty:
        return 0.0
    held = trades["bars_held"].astype(float).clip(lower=1.0)
    w = trades["weight"].astype(float).abs()
    # one 1h bar ≈ 1/8 of a funding interval
    return float((w * (held / 8.0) * rate_8h).sum())


def _gap_blowups(df: pd.DataFrame, trades: pd.DataFrame, liq_pct: float = 0.10) -> int:
    """Count trades where high/low vs entry exceeded liq_pct before exit (1h bar path)."""
    if trades is None or trades.empty:
        return 0
    n = 0
    idx = df.index
    for rec in trades.itertuples(index=False):
        try:
            t0 = pd.Timestamp(rec.entry_time)
            t1 = pd.Timestamp(rec.exit_time)
            if t0.tzinfo is None:
                t0 = t0.tz_localize("UTC")
            if t1.tzinfo is None:
                t1 = t1.tz_localize("UTC")
            sl = df.loc[(idx >= t0) & (idx <= t1)]
            if sl.empty:
                continue
            entry = float(rec.entry_price)
            side = float(rec.side)
            if side > 0:
                dd = (sl["low"].min() - entry) / entry
                if dd <= -liq_pct:
                    n += 1
            else:
                uu = (sl["high"].max() - entry) / entry
                if uu >= liq_pct:
                    n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def main() -> int:
    out_dir = cfg.REPORTS_DIR / "eth_okx"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = cfg.spec_for("ETH-USD")
    df, meta = get_data("ETH-USD", interval="1h", period="730d")
    print(f"ETH bars={len(df)} {meta.get('start')} -> {meta.get('end')}")

    saved_risk, saved_max = cfg.RISK_PER_TRADE, cfg.MAX_WEIGHT
    report: dict = {"data": {k: meta[k] for k in meta if k != "qc"}, "runs": {}}

    try:
        for model in ("B", "E", "A", "BHS"):
            res = run_backtest(df, model=model, symbol="ETH-USD", spec=spec)
            pack = _pack(res.daily_equity if len(res.daily_equity) else res.equity, res.trades if model != "BHS" else None)
            if model != "BHS" and not res.trades.empty:
                pack["funding_pct_of_initial_nav"] = _funding_haircut(res.trades)
                pack["near_10pct_adverse_trades"] = _gap_blowups(df, res.trades, 0.10)
            report["runs"][f"risk_budget_{model}"] = pack
            print(model, pack.get("calendar_cagr"), pack.get("max_drawdown"), pack.get("median_weight"))
            if model == "B" and len(res.daily_equity):
                plot_model_pnl({"ETH": res.daily_equity}, out_dir / "eth_model_b_pnl.png", "ETH 模型 B（风险预算，非 10 倍满仓）")
                plot_drawdown(res.daily_equity, out_dir / "eth_model_b_dd.png", "ETH 模型 B 回撤")
                res.daily_equity.to_csv(out_dir / "eth_B_equity.csv", header=["equity"])
                if not res.trades.empty:
                    res.trades.to_csv(out_dir / "eth_B_trades.csv", index=False)
                    natr = df.assign(natr=np.nan)
                report["runs"]["risk_budget_B"]["median_natr"] = float(
                    pd.Series(res.params.get("spec")).get("x") or np.nan
                )

        # NATR on feature window from last run_backtest internals: recompute via engine features
        from universal_quant.regimes.regime_engine import add_regime

        feat = add_regime(df)
        report["natr"] = {
            "median": float(feat["natr"].median()),
            "mean": float(feat["natr"].mean()),
            "p90": float(feat["natr"].quantile(0.9)),
        }
        # implied weight at median NATR
        med = report["natr"]["median"]
        report["implied_weight_at_median_natr"] = float(
            min(saved_max, 0.01 / (2.0 * med)) if med and med > 0 else 0.0
        )

        cfg.RISK_PER_TRADE = 10.0
        cfg.MAX_WEIGHT = 10.0
        res10 = run_backtest(df, model="B", symbol="ETH-USD", spec=spec)
        pack10 = _pack(res10.daily_equity if len(res10.daily_equity) else res10.equity, res10.trades)
        pack10["funding_pct_of_initial_nav"] = _funding_haircut(res10.trades)
        pack10["near_10pct_adverse_trades"] = _gap_blowups(df, res10.trades, 0.10)
        pack10["liq_threshold"] = "10% adverse vs entry at 10x ≈ wipe equity"
        eq = res10.daily_equity if len(res10.daily_equity) else res10.equity
        ruined = bool((eq <= 0).any()) if len(eq) else False
        pack10["equity_hit_zero"] = ruined
        min_nav = float(eq.min()) if len(eq) else float("nan")
        pack10["min_nav"] = min_nav
        report["runs"]["full_10x_B"] = pack10
        print("10x B", pack10.get("calendar_cagr"), pack10.get("max_drawdown"), pack10.get("median_weight"), "min_nav", min_nav)
        if len(eq):
            plot_model_pnl({"ETH 10x": eq}, out_dir / "eth_model_b_10x_pnl.png", "ETH 模型 B 始终 10 倍名义")
            eq.to_csv(out_dir / "eth_B_10x_equity.csv", header=["equity"])
            if not res10.trades.empty:
                res10.trades.to_csv(out_dir / "eth_B_10x_trades.csv", index=False)
    finally:
        cfg.RISK_PER_TRADE = saved_risk
        cfg.MAX_WEIGHT = saved_max

    (out_dir / "eth_okx_study.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("Wrote", out_dir / "eth_okx_study.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
