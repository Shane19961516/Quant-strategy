"""Market data adapters: CSV loaders + synthetic demo generator."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.bs76_engine import black76_greeks, black76_price
from core.screener import OptionContract, UnderlyingSnapshot


def load_price_csv(path: str | Path, price_col: str = "close") -> pd.DataFrame:
    df = pd.read_csv(path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
    if price_col not in df.columns:
        raise ValueError(f"missing price column: {price_col}")
    return df


def load_iv_csv(path: str | Path, iv_col: str = "iv") -> pd.Series:
    df = pd.read_csv(path)
    if iv_col not in df.columns:
        raise ValueError(f"missing iv column: {iv_col}")
    return df[iv_col].astype(float)


def generate_demo_snapshots(
    *,
    seed: int = 42,
    n_days: int = 280,
) -> list[UnderlyingSnapshot]:
    """
    Build realistic demo underlyings that pass / fail screener filters.

    Includes AG (should pass: high IVR/IVP and IV-HV spread) and a flat
    low-IV name that should be filtered out.
    """
    rng = np.random.default_rng(seed)
    today = datetime.utcnow().date()
    snapshots: list[UnderlyingSnapshot] = []

    # --- AG: elevated IV regime ---
    snapshots.append(
        _build_snapshot(
            underlying="AG2609",
            product="ag",
            exchange="SHFE",
            F0=7800.0,
            base_iv=0.22,
            regime_iv=0.34,
            multiplier=15.0,
            dte=38,
            rng=rng,
            n_days=n_days,
            today=today,
            elevate=True,
        )
    )

    # --- M (soymeal): also elevated ---
    snapshots.append(
        _build_snapshot(
            underlying="M2609",
            product="m",
            exchange="DCE",
            F0=3000.0,
            base_iv=0.18,
            regime_iv=0.28,
            multiplier=10.0,
            dte=35,
            rng=rng,
            n_days=n_days,
            today=today,
            elevate=True,
        )
    )

    # --- CU: calm / low IV — should fail filters ---
    snapshots.append(
        _build_snapshot(
            underlying="CU2609",
            product="cu",
            exchange="SHFE",
            F0=72000.0,
            base_iv=0.16,
            regime_iv=0.15,
            multiplier=5.0,
            dte=40,
            rng=rng,
            n_days=n_days,
            today=today,
            elevate=False,
        )
    )
    return snapshots


def _build_snapshot(
    *,
    underlying: str,
    product: str,
    exchange: str,
    F0: float,
    base_iv: float,
    regime_iv: float,
    multiplier: float,
    dte: int,
    rng: np.random.Generator,
    n_days: int,
    today,
    elevate: bool,
) -> UnderlyingSnapshot:
    # Geometric Brownian path for futures
    daily_vol = base_iv / np.sqrt(252)
    shocks = rng.normal(0, daily_vol, size=n_days)
    log_path = np.cumsum(shocks)
    prices = F0 * np.exp(log_path - log_path[-1])  # end near F0
    F = float(prices[-1])

    # IV history: mostly around base_iv, last ~40 days elevated if elevate
    iv_hist = base_iv + rng.normal(0, 0.015, size=n_days)
    iv_hist = np.clip(iv_hist, 0.05, 1.0)
    if elevate:
        iv_hist[-40:] = regime_iv + rng.normal(0, 0.01, size=40)
        current_iv = float(regime_iv + abs(rng.normal(0, 0.005)))
    else:
        current_iv = float(base_iv)

    expire = (today + timedelta(days=dte)).isoformat()
    contracts: list[OptionContract] = []

    # Strike ladder around F
    step = max(round(F * 0.02 / 10) * 10, 10)
    strikes = [F + i * step for i in range(-6, 7)]
    r = 0.02
    T = dte / 365.0

    for K in strikes:
        for opt_type in ("CALL", "PUT"):
            g = black76_greeks(F, K, T, r, current_iv, opt_type)  # type: ignore[arg-type]
            prem = black76_price(F, K, T, r, current_iv, opt_type)  # type: ignore[arg-type]
            tag = "C" if opt_type == "CALL" else "P"
            symbol = f"{underlying}-{tag}-{int(round(K))}"
            contracts.append(
                OptionContract(
                    symbol=symbol,
                    underlying=underlying,
                    option_type=opt_type,
                    strike=float(K),
                    dte=dte,
                    expire_date=expire,
                    iv=current_iv,
                    premium=float(prem),
                    F=F,
                    multiplier=multiplier,
                    exchange=exchange,
                    product=product,
                    delta=g.delta,
                    gamma=g.gamma,
                    vega=g.vega,
                    theta=g.theta,
                )
            )

    return UnderlyingSnapshot(
        underlying=underlying,
        F=F,
        prices=prices.tolist(),
        iv_history=iv_hist.tolist(),
        current_iv=current_iv,
        contracts=contracts,
        product=product,
        exchange=exchange,
        multiplier=multiplier,
    )


def snapshot_to_ohlc(prices: list[float], lookback: int = 120) -> pd.DataFrame:
    """Convert close series to synthetic OHLC + Donchian / Bollinger for charts."""
    closes = np.asarray(prices[-lookback:], dtype=float)
    n = len(closes)
    dates = pd.date_range(end=datetime.utcnow().date(), periods=n, freq="B")
    noise = np.maximum(closes * 0.002, 1e-6)
    rng = np.random.default_rng(7)
    high = closes + rng.uniform(0.5, 1.5, n) * noise
    low = closes - rng.uniform(0.5, 1.5, n) * noise
    open_ = closes + rng.normal(0, 1, n) * noise * 0.3
    df = pd.DataFrame(
        {"date": dates, "open": open_, "high": high, "low": low, "close": closes}
    )
    df["donchian_high"] = df["high"].rolling(20, min_periods=1).max()
    df["donchian_low"] = df["low"].rolling(20, min_periods=1).min()
    mid = df["close"].rolling(20, min_periods=1).mean()
    std = df["close"].rolling(20, min_periods=1).std().fillna(0)
    df["bb_mid"] = mid
    df["bb_upper"] = mid + 2 * std
    df["bb_lower"] = mid - 2 * std
    return df


def snapshot_vol_series(snap: UnderlyingSnapshot, lookback: int = 120) -> pd.DataFrame:
    """Build IV vs rolling HV30 series for lower chart."""
    prices = list(snap.prices)
    ivs = list(snap.iv_history)
    n = min(lookback, len(prices) - 31, len(ivs))
    rows = []
    dates = pd.date_range(end=datetime.utcnow().date(), periods=n, freq="B")
    for i in range(n):
        idx = len(prices) - n + i
        window_prices = prices[idx - 30 : idx + 1]
        from core.metrics import hv30 as _hv30

        try:
            hv = _hv30(window_prices)
        except ValueError:
            hv = np.nan
        rows.append({"date": dates[i], "iv": ivs[idx], "hv30": hv})
    return pd.DataFrame(rows)


class MarketDataClient:
    """Thin facade: CSV directory or demo mode."""

    def __init__(self, data_dir: Optional[str | Path] = None, use_demo: bool = True):
        self.data_dir = Path(data_dir) if data_dir else None
        self.use_demo = use_demo
        self._cache: Optional[list[UnderlyingSnapshot]] = None

    def fetch_snapshots(self, refresh: bool = False) -> list[UnderlyingSnapshot]:
        if self._cache is not None and not refresh:
            return self._cache
        if self.use_demo or self.data_dir is None:
            self._cache = generate_demo_snapshots()
            return self._cache
        # Placeholder for CSV directory convention: <underlying>_prices.csv / _iv.csv
        raise NotImplementedError("CSV multi-underlying loader — use demo mode or extend")
