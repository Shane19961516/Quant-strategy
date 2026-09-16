"""OKX ETH-USDT-SWAP 1m study: naive 1m formulas vs hourly-clock on 1m path."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pandas as pd

from brent_quant.metrics import performance_from_equity
from universal_quant import config as cfg
from universal_quant.adapters.okx import get_okx_1m
from universal_quant.backtest.engine import run_backtest
from universal_quant.portfolio.risk_budget import calendar_cagr
from universal_quant.regimes.regime_engine import add_regime
from universal_quant.report import plot_drawdown, plot_model_pnl
from universal_quant.strategies.score import add_score
from universal_quant.strategies.selector import signal_from_model

ETH_SPEC = {
    "name": "ETH-OKX",
    "asset_class": "crypto_swap",
    "cluster": "Crypto",
    "tick_size": 0.01,
    "multiplier": 1.0,
    "commission_bps": 5.0,
    "volume_type": "contracts",
    "timezone": "UTC",
}


@contextmanager
def _one_minute_windows():
    """Map 1h lookbacks onto 1m: 24h features, 72h time stop."""
    keys = {
        "BAR_MINUTES": 1,
        "ER_N": 1440,
        "BREAKOUT_N": 1440,
        "VOL_N": 1440,
        "VOLUME_MA": 1440,
        "MOM_N": 1440,
        "SLOPE_N": 24,
        "TIME_STOP_BARS": 72 * 60,
    }
    saved = {k: getattr(cfg, k) for k in keys}
    try:
        for k, v in keys.items():
            setattr(cfg, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


@contextmanager
def _one_minute_time_stop():
    """Keep 72h time-stop when executing the hourly clock on 1m bars."""
    saved = {"BAR_MINUTES": cfg.BAR_MINUTES, "TIME_STOP_BARS": cfg.TIME_STOP_BARS}
    cfg.BAR_MINUTES = 1
    cfg.TIME_STOP_BARS = 72 * 60
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


def resample_okx_1h(df_1m: pd.DataFrame) -> pd.DataFrame:
    hourly = df_1m.resample("1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return hourly.dropna(subset=["open", "high", "low", "close"])


def hourly_clock_on_1m(df_1m: pd.DataFrame, model: str) -> tuple[pd.Series, dict[str, pd.Series]]:
    """UPV 1h decision clock on a 1m path, without intra-hour look-ahead.

    Hour-t features (pandas left-labeled) use that hour's close. The pulse is
    released on the last 1m bar of the hour; the engine fills the next minute
    (the next hour's open). ATR/NATR used from that next hour onward are the
    completed previous hour's values.
    """
    hourly = resample_okx_1h(df_1m)
    feat = add_score(add_regime(hourly))
    sig_h = signal_from_model(feat, model)
    sig = pd.Series(0.0, index=df_1m.index, dtype=float)
    released = sig_h.copy()
    released.index = sig_h.index + pd.Timedelta(minutes=59)
    common = released.index.intersection(df_1m.index)
    if len(common):
        sig.loc[common] = released.loc[common].astype(float)

    shifted = feat.copy()
    shifted.index = feat.index + pd.Timedelta(hours=1)
    overlay = {
        "atr": shifted["atr"].reindex(df_1m.index, method="ffill"),
        "natr": shifted["natr"].reindex(df_1m.index, method="ffill"),
        "regime": shifted["regime"].reindex(df_1m.index, method="ffill"),
    }
    return sig, overlay


def _pack(eq: pd.Series, trades: pd.DataFrame | None = None) -> dict:
    st = performance_from_equity(eq, trades)
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
        "avg_holding_hours": (float(st["avg_holding_bars"]) / 60.0) if st.get("avg_holding_bars") == st.get("avg_holding_bars") else None,
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
    if trades is None or trades.empty:
        return 0.0
    held = trades["bars_held"].astype(float).clip(lower=1.0)
    w = trades["weight"].astype(float).abs()
    return float((w * (held / 480.0) * rate_8h).sum())


def _gap_blowups(df: pd.DataFrame, trades: pd.DataFrame, liq_pct: float = 0.10) -> int:
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
                if (sl["low"].min() - entry) / entry <= -liq_pct:
                    n += 1
            else:
                if (sl["high"].max() - entry) / entry >= liq_pct:
                    n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def main() -> int:
    out_dir = cfg.REPORTS_DIR / "eth_okx_1m"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Downloading OKX ETH-USDT-SWAP 1m (~60d)...")
    df, meta = get_okx_1m("ETH-USDT-SWAP", days=60, force=False)
    print(f"ETH 1m bars={len(df)} {meta.get('start')} -> {meta.get('end')}")

    saved_risk, saved_max = cfg.RISK_PER_TRADE, cfg.MAX_WEIGHT
    report: dict = {
        "data": meta,
        "windows": "24h lookback / 72h time-stop mapped onto 1m bars",
        "runs": {},
        "hourly_clock_runs": {},
    }

    with _one_minute_windows():
        feat = add_regime(df)
        report["natr"] = {
            "median": float(feat["natr"].median()),
            "mean": float(feat["natr"].mean()),
            "p90": float(feat["natr"].quantile(0.9)),
        }
        med = report["natr"]["median"]
        report["implied_weight_at_median_natr"] = float(min(saved_max, 0.01 / (2.0 * med)) if med and med > 0 else 0.0)

        for model in ("B", "E", "A", "BHS"):
            print("run", model, "...")
            res = run_backtest(df, model=model, symbol="ETH-USDT-SWAP", spec=ETH_SPEC)
            eq = res.daily_equity if len(res.daily_equity) else res.equity
            pack = _pack(eq, res.trades if model != "BHS" else None)
            if model != "BHS" and not res.trades.empty:
                pack["funding_pct_of_initial_nav"] = _funding_haircut(res.trades)
                pack["near_10pct_adverse_trades"] = _gap_blowups(df, res.trades, 0.10)
                pack["near_3pct_adverse_trades"] = _gap_blowups(df, res.trades, 0.03)
            report["runs"][f"risk_budget_{model}"] = pack
            print(" ", model, "cagr", pack.get("calendar_cagr"), "mdd", pack.get("max_drawdown"), "w", pack.get("median_weight"), "n", pack.get("n_trades"))
            if model == "B" and len(eq):
                plot_model_pnl({"ETH 1m": eq}, out_dir / "eth_1m_model_b_pnl.png", "OKX ETH 永续 1m 模型 B（风险预算）")
                plot_drawdown(eq, out_dir / "eth_1m_model_b_dd.png", "OKX ETH 1m 模型 B 回撤")
                eq.to_csv(out_dir / "eth_1m_B_equity.csv", header=["equity"])
                if not res.trades.empty:
                    res.trades.to_csv(out_dir / "eth_1m_B_trades.csv", index=False)

        print("run 10x B ...")
        cfg.RISK_PER_TRADE = 10.0
        cfg.MAX_WEIGHT = 10.0
        try:
            res10 = run_backtest(df, model="B", symbol="ETH-USDT-SWAP", spec=ETH_SPEC)
            eq10 = res10.daily_equity if len(res10.daily_equity) else res10.equity
            pack10 = _pack(eq10, res10.trades)
            pack10["funding_pct_of_initial_nav"] = _funding_haircut(res10.trades)
            pack10["near_10pct_adverse_trades"] = _gap_blowups(df, res10.trades, 0.10)
            pack10["near_3pct_adverse_trades"] = _gap_blowups(df, res10.trades, 0.03)
            pack10["equity_hit_zero"] = bool((eq10 <= 0).any()) if len(eq10) else False
            pack10["min_nav"] = float(eq10.min()) if len(eq10) else float("nan")
            report["runs"]["full_10x_B"] = pack10
            print("  10x B cagr", pack10.get("calendar_cagr"), "mdd", pack10.get("max_drawdown"), "min_nav", pack10.get("min_nav"), "liq10", pack10.get("near_10pct_adverse_trades"))
            if len(eq10):
                plot_model_pnl({"ETH 1m 10x": eq10}, out_dir / "eth_1m_model_b_10x_pnl.png", "OKX ETH 1m 模型 B 始终 10 倍名义")
                eq10.to_csv(out_dir / "eth_1m_B_10x_equity.csv", header=["equity"])
                if not res10.trades.empty:
                    res10.trades.to_csv(out_dir / "eth_1m_B_10x_trades.csv", index=False)
        finally:
            cfg.RISK_PER_TRADE = saved_risk
            cfg.MAX_WEIGHT = saved_max

    print("hourly clock on 1m path ...")
    hour_natr = None
    with _one_minute_time_stop():
        for model in ("B", "E", "A", "BHS"):
            print("run hourly", model, "...")
            if model == "BHS":
                res = run_backtest(df, model="BHS", symbol="ETH-USDT-SWAP", spec=ETH_SPEC, prepared=True)
                eq = res.daily_equity if len(res.daily_equity) else res.equity
                pack = _pack(eq, None)
            else:
                sig, overlay = hourly_clock_on_1m(df, model)
                if hour_natr is None:
                    hour_natr = overlay["natr"]
                res = run_backtest(
                    df,
                    model=model,
                    symbol="ETH-USDT-SWAP",
                    spec=ETH_SPEC,
                    signal=sig,
                    overlay=overlay,
                    prepared=True,
                )
                eq = res.daily_equity if len(res.daily_equity) else res.equity
                pack = _pack(eq, res.trades)
                if not res.trades.empty:
                    pack["funding_pct_of_initial_nav"] = _funding_haircut(res.trades)
                    pack["near_10pct_adverse_trades"] = _gap_blowups(df, res.trades, 0.10)
                    pack["near_3pct_adverse_trades"] = _gap_blowups(df, res.trades, 0.03)
            report["hourly_clock_runs"][f"risk_budget_{model}"] = pack
            print("  hourly", model, "cagr", pack.get("calendar_cagr"), "mdd", pack.get("max_drawdown"), "w", pack.get("median_weight"), "n", pack.get("n_trades"))
            if model == "B" and len(eq):
                plot_model_pnl({"ETH 1m 小时钟": eq}, out_dir / "eth_1m_hourly_b_pnl.png", "OKX ETH 1m 路径 · 小时决策 · 模型 B")
                plot_drawdown(eq, out_dir / "eth_1m_hourly_b_dd.png", "OKX ETH 1m 小时钟模型 B 回撤")
                eq.to_csv(out_dir / "eth_1m_hourly_B_equity.csv", header=["equity"])
                if not res.trades.empty:
                    res.trades.to_csv(out_dir / "eth_1m_hourly_B_trades.csv", index=False)

        if hour_natr is not None:
            nn = hour_natr.dropna()
            report["hourly_natr"] = {
                "median": float(nn.median()) if len(nn) else None,
                "mean": float(nn.mean()) if len(nn) else None,
                "p90": float(nn.quantile(0.9)) if len(nn) else None,
            }
            med_h = report["hourly_natr"]["median"] or 0.0
            report["hourly_implied_weight"] = float(min(saved_max, 0.01 / (2.0 * med_h)) if med_h > 0 else 0.0)

        print("run hourly 10x B ...")
        cfg.RISK_PER_TRADE = 10.0
        cfg.MAX_WEIGHT = 10.0
        try:
            sig10, ov10 = hourly_clock_on_1m(df, "B")
            res10h = run_backtest(
                df,
                model="B",
                symbol="ETH-USDT-SWAP",
                spec=ETH_SPEC,
                signal=sig10,
                overlay=ov10,
                prepared=True,
            )
            eq10h = res10h.daily_equity if len(res10h.daily_equity) else res10h.equity
            pack10h = _pack(eq10h, res10h.trades)
            if not res10h.trades.empty:
                pack10h["funding_pct_of_initial_nav"] = _funding_haircut(res10h.trades)
                pack10h["near_10pct_adverse_trades"] = _gap_blowups(df, res10h.trades, 0.10)
                pack10h["near_3pct_adverse_trades"] = _gap_blowups(df, res10h.trades, 0.03)
            pack10h["equity_hit_zero"] = bool((eq10h <= 0).any()) if len(eq10h) else False
            pack10h["min_nav"] = float(eq10h.min()) if len(eq10h) else float("nan")
            report["hourly_clock_runs"]["full_10x_B"] = pack10h
            print("  hourly 10x B cagr", pack10h.get("calendar_cagr"), "mdd", pack10h.get("max_drawdown"), "min_nav", pack10h.get("min_nav"), "liq10", pack10h.get("near_10pct_adverse_trades"))
            if len(eq10h):
                plot_model_pnl({"ETH 1m 小时钟 10x": eq10h}, out_dir / "eth_1m_hourly_b_10x_pnl.png", "OKX ETH 1m 小时钟 · 始终 10 倍名义")
                eq10h.to_csv(out_dir / "eth_1m_hourly_B_10x_equity.csv", header=["equity"])
                if not res10h.trades.empty:
                    res10h.trades.to_csv(out_dir / "eth_1m_hourly_B_10x_trades.csv", index=False)
        finally:
            cfg.RISK_PER_TRADE = saved_risk
            cfg.MAX_WEIGHT = saved_max

    (out_dir / "eth_okx_1m_study.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("Wrote", out_dir / "eth_okx_1m_study.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
