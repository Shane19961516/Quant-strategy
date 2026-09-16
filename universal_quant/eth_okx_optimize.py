"""Fast 10x oneshot simulator + 1m ETH grid (train/test, not ATR)."""

from __future__ import annotations

import json
from itertools import product

import numpy as np
import pandas as pd

from universal_quant import config as cfg
from universal_quant.adapters.okx import get_okx_1m
from universal_quant.regimes.regime_engine import add_regime
from universal_quant.strategies.score import add_score
from universal_quant.strategies.selector import signal_from_model


def simulate_oneshot(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    sigs: np.ndarray,
    *,
    leverage: float = 10.0,
    liq_pct: float = 0.10,
    fee_bps: float = 5.0,
    hold_to_liq: bool = False,
    exit_when_flat: bool = False,
    init_nav: float = 1_000_000.0,
) -> tuple[np.ndarray, int, int]:
    """One 10x ticket. Pulse at close, fill next open. Stop = ±liq_pct."""
    n = len(closes)
    equity = np.empty(n, dtype=float)
    nav = float(init_nav)
    pending = 0.0
    side = 0.0
    entry = 0.0
    notional = 0.0
    marked = 0.0
    n_trades = 0
    n_liq = 0
    fee_r = fee_bps / 1e4

    def close_pos(px: float, cap: bool) -> None:
        nonlocal nav, side, entry, notional, marked, n_trades
        if side == 0.0 or entry <= 0:
            return
        raw = notional * side * (px - entry) / entry
        if cap:
            raw = max(raw, -notional / leverage)
        nav += (raw - marked) - abs(notional) * fee_r
        n_trades += 1
        side = 0.0
        entry = 0.0
        notional = 0.0
        marked = 0.0

    for i in range(n):
        if nav <= 1.0:
            equity[i] = max(nav, 0.0)
            pending = float(sigs[i]) if sigs[i] == sigs[i] else 0.0
            continue
        ds = 0.0 if pending == 0 else (1.0 if pending > 0 else -1.0)
        if exit_when_flat and side != 0 and ds == 0:
            close_pos(opens[i], cap=False)
        if not hold_to_liq and ds != 0 and side != 0 and ds != side:
            close_pos(opens[i], cap=False)
        if ds != 0 and side == 0 and nav > 1.0 and opens[i] > 0:
            side = ds
            entry = opens[i]
            notional = leverage * nav
            nav -= abs(notional) * fee_r
            marked = 0.0
        if side != 0:
            stop = entry * (1.0 - liq_pct) if side > 0 else entry * (1.0 + liq_pct)
            hit = (side > 0 and lows[i] <= stop) or (side < 0 and highs[i] >= stop)
            if hit:
                close_pos(stop, cap=True)
                n_liq += 1
        if side != 0 and closes[i] > 0:
            raw = notional * side * (closes[i] - entry) / entry
            nav += raw - marked
            marked = raw
        equity[i] = max(nav, 0.0)
        pending = float(sigs[i]) if sigs[i] == sigs[i] else 0.0

    if side != 0:
        close_pos(closes[-1], cap=False)
        equity[-1] = max(nav, 0.0)
        n_trades += 0
    return equity, n_trades, n_liq


def _window_cfg(n_bars: int):
    keys = {
        "BAR_MINUTES": 1,
        "ER_N": n_bars,
        "BREAKOUT_N": n_bars,
        "VOL_N": n_bars,
        "VOLUME_MA": n_bars,
        "MOM_N": n_bars,
        "SLOPE_N": n_bars,
        "TIME_STOP_BARS": 10**9,
    }
    saved = {k: getattr(cfg, k) for k in keys}
    for k, v in keys.items():
        setattr(cfg, k, v)
    return saved


def _restore(saved: dict) -> None:
    for k, v in saved.items():
        setattr(cfg, k, v)


def _gate(close: pd.Series, kind: str) -> pd.Series:
    if kind == "none":
        return pd.Series(True, index=close.index)
    if kind == "sma_4h":
        return close >= close.rolling(240, min_periods=240).mean()
    if kind == "sma_24h":
        return close >= close.rolling(1440, min_periods=1440).mean()
    raise ValueError(kind)


def _stats(eq: np.ndarray) -> dict:
    if len(eq) < 2 or eq[0] <= 0:
        return {"ret": -1.0, "mdd": -1.0, "end": float(eq[-1] if len(eq) else 0.0)}
    s = pd.Series(eq)
    dd = float((s / s.cummax() - 1.0).min())
    return {"ret": float(eq[-1] / eq[0] - 1.0), "mdd": dd, "end": float(eq[-1])}


def _fold_rets(eq: np.ndarray, cuts: list[int]) -> list[float]:
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b <= a + 10 or eq[a] <= 0:
            out.append(-1.0)
            continue
        out.append(float(eq[b - 1] / eq[a] - 1.0))
    return out


def run_grid(df: pd.DataFrame) -> pd.DataFrame:
    opens = df["open"].to_numpy(float)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    n = len(df)
    mid = n // 2
    cuts = [0, n // 3, 2 * n // 3, n]
    rows = []
    n_list = (20, 60, 120)
    models = ("A", "C")
    for n_bars in n_list:
        saved = _window_cfg(n_bars)
        try:
            feat = add_score(add_regime(df))
            raw = {m: signal_from_model(feat, m) for m in models}
        finally:
            _restore(saved)
        gates = {
            "none": _gate(df["close"], "none"),
            "sma_4h": _gate(df["close"], "sma_4h"),
            "sma_24h": _gate(df["close"], "sma_24h"),
        }
        for model, long_only, hold, liq, gname in product(
            models, (True, False), (True, False), (0.05, 0.10), gates
        ):
            sig = raw[model].copy()
            if long_only:
                sig = sig.clip(lower=0.0)
            sig = sig.where(gates[gname], 0.0)
            s = sig.fillna(0.0).to_numpy(float)
            eq, n_tr, n_liq = simulate_oneshot(
                opens, highs, lows, closes, s, leverage=10.0, liq_pct=liq, hold_to_liq=hold
            )
            full = _stats(eq)
            train = _stats(eq[:mid])
            test = _stats(eq[mid:])
            folds = _fold_rets(eq, cuts)
            rows.append(
                {
                    "n_bars": n_bars,
                    "model": model,
                    "long_only": long_only,
                    "hold_to_liq": hold,
                    "liq_pct": liq,
                    "htf": gname,
                    "leverage": 10.0,
                    "ret": full["ret"],
                    "mdd": full["mdd"],
                    "n_trades": n_tr,
                    "n_liq": n_liq,
                    "train_ret": train["ret"],
                    "train_mdd": train["mdd"],
                    "test_ret": test["ret"],
                    "test_mdd": test["mdd"],
                    "fold0": folds[0],
                    "fold1": folds[1],
                    "fold2": folds[2],
                    "n_signal": int((s != 0).sum()),
                    "end_nav": full["end"],
                }
            )

    for n_sma, lev, liq in product((240, 720, 1440, 2880), (2.0, 3.0, 5.0, 10.0), (0.10,)):
        sma = df["close"].rolling(int(n_sma), min_periods=int(n_sma)).mean()
        sig = (df["close"] > sma).astype(float).fillna(0.0).to_numpy(float)
        eq, n_tr, n_liq = simulate_oneshot(
            opens, highs, lows, closes, sig, leverage=lev, liq_pct=liq, hold_to_liq=False, exit_when_flat=True
        )
        full = _stats(eq)
        train = _stats(eq[:mid])
        test = _stats(eq[mid:])
        folds = _fold_rets(eq, cuts)
        rows.append(
            {
                "n_bars": int(n_sma),
                "model": "SMA",
                "long_only": True,
                "hold_to_liq": False,
                "liq_pct": liq,
                "htf": f"sticky_sma_{n_sma}",
                "leverage": lev,
                "ret": full["ret"],
                "mdd": full["mdd"],
                "n_trades": n_tr,
                "n_liq": n_liq,
                "train_ret": train["ret"],
                "train_mdd": train["mdd"],
                "test_ret": test["ret"],
                "test_mdd": test["mdd"],
                "fold0": folds[0],
                "fold1": folds[1],
                "fold2": folds[2],
                "n_signal": int((sig != 0).sum()),
                "end_nav": full["end"],
            }
        )
    hh = df["high"].shift(1).rolling(1440, min_periods=1440).max()
    for lev in (2.0, 3.0, 5.0, 10.0):
        sig = (df["close"] > hh).astype(float).fillna(0.0).to_numpy(float)
        eq, n_tr, n_liq = simulate_oneshot(
            opens, highs, lows, closes, sig, leverage=lev, liq_pct=0.10, hold_to_liq=False, exit_when_flat=True
        )
        full = _stats(eq)
        train = _stats(eq[:mid])
        test = _stats(eq[mid:])
        folds = _fold_rets(eq, cuts)
        rows.append(
            {
                "n_bars": 1440,
                "model": "DON24H",
                "long_only": True,
                "hold_to_liq": False,
                "liq_pct": 0.10,
                "htf": "sticky_don_24h",
                "leverage": lev,
                "ret": full["ret"],
                "mdd": full["mdd"],
                "n_trades": n_tr,
                "n_liq": n_liq,
                "train_ret": train["ret"],
                "train_mdd": train["mdd"],
                "test_ret": test["ret"],
                "test_mdd": test["mdd"],
                "fold0": folds[0],
                "fold1": folds[1],
                "fold2": folds[2],
                "n_signal": int((sig != 0).sum()),
                "end_nav": full["end"],
            }
        )
    return pd.DataFrame(rows)


def pick_winner(grid: pd.DataFrame) -> pd.Series | None:
    g = grid.copy()
    g["folds_ok"] = (g["fold0"] > 0) & (g["fold1"] > 0) & (g["fold2"] > 0)
    g["halves_ok"] = (g["train_ret"] > 0.02) & (g["test_ret"] > 0.02)
    g["alive"] = (g["mdd"] > -0.55) & (g["train_mdd"] > -0.55) & (g["test_mdd"] > -0.55)
    g["traded"] = (g["n_trades"] >= 4) & (g["n_trades"] <= 250)
    hard = g[g["folds_ok"] & g["halves_ok"] & g["alive"] & g["traded"]]
    if hard.empty:
        hard = g[g["halves_ok"] & g["alive"] & (g["n_trades"] >= 3) & (g["n_trades"] <= 400)]
    if hard.empty:
        hard = g[g["halves_ok"] & (g["n_trades"] >= 3)]
    if hard.empty:
        g["score"] = g[["train_ret", "test_ret"]].min(axis=1)
        return g.sort_values(["score", "mdd"], ascending=[False, False]).iloc[0]
    hard = hard.copy()
    hard["score"] = hard[["fold0", "fold1", "fold2"]].min(axis=1) + 0.15 * hard["ret"] + 0.2 * hard["mdd"]
    return hard.sort_values("score", ascending=False).iloc[0]


def main() -> int:
    out_dir = cfg.REPORTS_DIR / "eth_okx_1m"
    out_dir.mkdir(parents=True, exist_ok=True)
    df, meta = get_okx_1m("ETH-USDT-SWAP", days=60, force=False)
    print(f"grid on {len(df)} bars {meta.get('start')} -> {meta.get('end')}")
    grid = run_grid(df)
    grid = grid.sort_values("ret", ascending=False)
    winner = pick_winner(grid)
    grid.to_csv(out_dir / "eth_1m_fly_grid.csv", index=False)
    payload = {
        "data": meta,
        "winner": None if winner is None else {k: (None if pd.isna(v) else v) for k, v in winner.to_dict().items()},
        "n_configs": int(len(grid)),
        "n_halves_ok": int(((grid["train_ret"] > 0.02) & (grid["test_ret"] > 0.02)).sum()),
        "n_three_folds_ok": int(((grid["fold0"] > 0) & (grid["fold1"] > 0) & (grid["fold2"] > 0)).sum()),
        "top10": grid.head(10).to_dict(orient="records"),
    }
    (out_dir / "eth_1m_fly_grid.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print("winner", payload["winner"])
    print("halves_ok", payload["n_halves_ok"], "folds_ok", payload["n_three_folds_ok"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
