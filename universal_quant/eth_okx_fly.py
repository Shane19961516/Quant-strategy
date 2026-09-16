"""OKX ETH 10x fly study: N=20 1m bars, 100 clips, stop = liquidation."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pandas as pd

from universal_quant import config as cfg
from universal_quant.adapters.okx import get_okx_1m
from universal_quant.backtest.clip_engine import run_clip_backtest
from universal_quant.eth_okx_study import ETH_SPEC, _funding_haircut, _pack
from universal_quant.regimes.regime_engine import add_regime
from universal_quant.report import plot_drawdown, plot_model_pnl
from universal_quant.strategies.score import add_score
from universal_quant.strategies.selector import signal_from_model

N_BARS = 20
N_CLIPS = 100
LEVERAGE = 10.0
LIQ_PCT = 0.10


@contextmanager
def _n20_one_minute():
    keys = {
        "BAR_MINUTES": 1,
        "ER_N": N_BARS,
        "BREAKOUT_N": N_BARS,
        "VOL_N": N_BARS,
        "VOLUME_MA": N_BARS,
        "MOM_N": N_BARS,
        "SLOPE_N": N_BARS,
        "TIME_STOP_BARS": 10**9,
    }
    saved = {k: getattr(cfg, k) for k in keys}
    try:
        for k, v in keys.items():
            setattr(cfg, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


def _extras(pack: dict, trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        pack["n_liquidations"] = 0
        pack["median_clips_hint"] = 0.0
        return pack
    pack["n_liquidations"] = int((trades["exit_reason"] == "liquidation").sum())
    pack["liq_share"] = float((trades["exit_reason"] == "liquidation").mean())
    pack["median_bars_held"] = float(trades["bars_held"].median())
    pack["max_bars_held"] = float(trades["bars_held"].max())
    return pack


def main() -> int:
    out_dir = cfg.REPORTS_DIR / "eth_okx_1m"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("OKX ETH 1m N=20 / 10x / 100 clips / liq stop ...")
    df, meta = get_okx_1m("ETH-USDT-SWAP", days=60, force=False)
    print(f"ETH 1m bars={len(df)} {meta.get('start')} -> {meta.get('end')}")

    report = {
        "data": meta,
        "spec": {
            "n_bars": N_BARS,
            "n_clips": N_CLIPS,
            "leverage": LEVERAGE,
            "liq_pct": LIQ_PCT,
            "clip_weight": LEVERAGE / N_CLIPS,
            "stop": "liquidation, not ATR",
        },
        "runs": {},
    }

    with _n20_one_minute():
        work = add_score(add_regime(df))
        report["natr"] = {
            "median": float(work["natr"].median()),
            "mean": float(work["natr"].mean()),
            "p90": float(work["natr"].quantile(0.9)),
        }
        eq_b = None
        for model in ("B", "E", "A"):
            print("fly", model, "...")
            sig = signal_from_model(work, model)
            res = run_clip_backtest(
                df,
                sig,
                symbol="ETH-USDT-SWAP",
                model=model,
                spec=ETH_SPEC,
                n_clips=N_CLIPS,
                leverage=LEVERAGE,
                liq_pct=LIQ_PCT,
            )
            eq = res.daily_equity if len(res.daily_equity) else res.equity
            pack = _extras(_pack(eq, res.trades), res.trades)
            if not res.trades.empty:
                pack["funding_pct_of_initial_nav"] = _funding_haircut(res.trades)
            pack["min_nav"] = float(res.equity.min()) if len(res.equity) else float("nan")
            pack["equity_hit_zero"] = bool((res.equity <= 1.0).any()) if len(res.equity) else False
            pack["n_signal_bars"] = int((sig.abs() > 0).sum())
            report["runs"][f"fly_{model}"] = pack
            print(
                " ",
                model,
                "ret",
                pack.get("total_return"),
                "mdd",
                pack.get("max_drawdown"),
                "n",
                pack.get("n_trades"),
                "liq",
                pack.get("n_liquidations"),
                "min_nav",
                pack.get("min_nav"),
            )
            if model == "B":
                eq_b = eq
                plot_model_pnl({"ETH 1m 飞10x": eq}, out_dir / "eth_1m_fly_b_pnl.png", "OKX ETH 1m N=20 · 100 笔分批 · 10 倍 · 爆仓止损")
                plot_drawdown(eq, out_dir / "eth_1m_fly_b_dd.png", "OKX ETH 1m 飞10x 模型 B 回撤")
                eq.to_csv(out_dir / "eth_1m_fly_B_equity.csv", header=["equity"])
                if not res.trades.empty:
                    res.trades.to_csv(out_dir / "eth_1m_fly_B_trades.csv", index=False)

        print("fly 10x buy-hold ...")
        hold = pd.Series(0.0, index=df.index)
        hold.iloc[0] = 1.0
        res_h = run_clip_backtest(
            df,
            hold,
            symbol="ETH-USDT-SWAP",
            model="BHS10",
            spec=ETH_SPEC,
            n_clips=1,
            leverage=LEVERAGE,
            liq_pct=LIQ_PCT,
        )
        eq_h = res_h.daily_equity if len(res_h.daily_equity) else res_h.equity
        pack_h = _extras(_pack(eq_h, res_h.trades), res_h.trades)
        if not res_h.trades.empty:
            pack_h["funding_pct_of_initial_nav"] = _funding_haircut(res_h.trades)
        pack_h["min_nav"] = float(res_h.equity.min()) if len(res_h.equity) else float("nan")
        report["runs"]["fly_BHS10"] = pack_h
        print("  BHS10 ret", pack_h.get("total_return"), "mdd", pack_h.get("max_drawdown"), "liq", pack_h.get("n_liquidations"))
        if eq_b is not None and len(eq_h):
            plot_model_pnl(
                {"ETH 1m 飞10x B": eq_b, "ETH 10x 死拿": eq_h},
                out_dir / "eth_1m_fly_vs_bhs10_pnl.png",
                "N=20 分100 笔 10 倍 vs 10 倍死拿",
            )

        print("fly B one-shot 10x (100 clips same bar) ...")
        sig_b = signal_from_model(work, "B")
        # Approximate iceberg: 100 clips but fill them all on the first bar of a campaign
        # by using n_clips=1 weight 10. That's same-price split.
        res1 = run_clip_backtest(
            df,
            sig_b,
            symbol="ETH-USDT-SWAP",
            model="B1",
            spec=ETH_SPEC,
            n_clips=1,
            leverage=LEVERAGE,
            liq_pct=LIQ_PCT,
        )
        eq1 = res1.daily_equity if len(res1.daily_equity) else res1.equity
        pack1 = _extras(_pack(eq1, res1.trades), res1.trades)
        pack1["min_nav"] = float(res1.equity.min()) if len(res1.equity) else float("nan")
        pack1["note"] = "one 10x fill per pulse, still 10% liq stop"
        report["runs"]["fly_B_oneshot"] = pack1
        print("  oneshot ret", pack1.get("total_return"), "mdd", pack1.get("max_drawdown"), "n", pack1.get("n_trades"), "liq", pack1.get("n_liquidations"))

    (out_dir / "eth_okx_1m_fly.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("Wrote", out_dir / "eth_okx_1m_fly.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
