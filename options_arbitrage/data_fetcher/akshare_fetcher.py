"""AkShare live market data for China commodity options short-strangle scans."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.bs76_engine import black76_greeks, black76_price, implied_volatility
from core.screener import OptionContract, UnderlyingSnapshot

logger = logging.getLogger(__name__)

# Backward-compatible Sina product map (built from full universe registry)
def _build_sina_products() -> dict[str, dict[str, Any]]:
    try:
        from data_fetcher.option_universe import load_universe

        specs_path = Path(__file__).resolve().parents[1] / "config" / "product_specs.json"
        specs = json.loads(specs_path.read_text(encoding="utf-8")).get("products", {})
        out: dict[str, dict[str, Any]] = {}
        for item in load_universe():
            if item.source != "sina":
                continue
            prod = item.product
            mult = None
            for k in (prod, prod.lower(), prod.upper()):
                if k in specs:
                    mult = float(specs[k]["multiplier"])
                    break
            fut_sym = f"{prod.upper()}0" if item.exchange == "CZCE" else f"{prod.lower()}0"
            out[item.cn_name] = {
                "product": prod,
                "exchange": item.exchange,
                "name": item.name,
                "futures": fut_sym,
                "multiplier": mult or 10,
            }
        if out:
            return out
    except Exception:
        pass
    # fallback if universe module unavailable
    return {
        "豆粕期权": {"product": "m", "exchange": "DCE", "name": "豆粕"},
        "玉米期权": {"product": "c", "exchange": "DCE", "name": "玉米"},
    }


SINA_PRODUCTS: dict[str, dict[str, Any]] = _build_sina_products()

INDEX_PRODUCTS: dict[str, dict[str, Any]] = {
    "沪深300股指期权": {"product": "IO", "exchange": "CFFEX", "multiplier": 100, "name": "沪深300"},
    "中证1000股指期权": {"product": "MO", "exchange": "CFFEX", "multiplier": 100, "name": "中证1000"},
    "上证50股指期权": {"product": "HO", "exchange": "CFFEX", "multiplier": 100, "name": "上证50"},
}


def estimate_option_expiry(contract: str, as_of: Optional[date] = None) -> tuple[date, int]:
    """
    Estimate last trading day for China commodity options.

    Approximation: 5th calendar day before the 1st day of the delivery month,
    then roll back to a weekday if needed. Good enough for DTE bucketing.
    """
    today = as_of or date.today()
    c = contract.strip().upper()
    m4 = re.search(r"(\d{4})$", c)
    m3 = re.search(r"(\d{3})$", c)
    if m4:
        yymm = m4.group(1)
        year = 2000 + int(yymm[:2])
        month = int(yymm[2:])
    elif m3:
        yym = m3.group(1)
        decade = today.year // 10 * 10
        year = decade + int(yym[0])
        if year < today.year - 1:
            year += 10
        month = int(yym[1:])
    else:
        return today + timedelta(days=45), 45
    if month == 12:
        first = date(year + 1, 1, 1)
    else:
        first = date(year, month, 1)
    expiry = first - timedelta(days=5)
    while expiry.weekday() >= 5:
        expiry -= timedelta(days=1)
    dte = max((expiry - today).days, 0)
    return expiry, dte


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        v = float(x)
        if math.isnan(v):
            return default
        return v
    except Exception:
        return default


def fetch_futures_ohlc(symbol: str, start: str = "20250101") -> pd.DataFrame:
    """Continuous main contract OHLC via Sina."""
    import akshare as ak

    end = date.today().strftime("%Y%m%d")
    df = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    rename = {
        "日期": "date",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "成交量": "volume",
        "持仓量": "open_interest",
    }
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return df


def pick_contract_by_dte(
    contracts: list[str],
    *,
    dte_min: int = 30,
    dte_max: int = 60,
    as_of: Optional[date] = None,
) -> Optional[tuple[str, date, int]]:
    """Prefer DTE in [dte_min, dte_max]; else nearest above dte_min up to 90."""
    scored: list[tuple[int, str, date, int]] = []
    for c in contracts:
        exp, dte = estimate_option_expiry(c, as_of=as_of)
        scored.append((dte, c, exp, dte))
    scored.sort(key=lambda x: x[0])
    in_window = [x for x in scored if dte_min <= x[0] <= dte_max]
    if in_window:
        # pick closest to midpoint of window
        mid = (dte_min + dte_max) / 2
        best = min(in_window, key=lambda x: abs(x[0] - mid))
        return best[1], best[2], best[3]
    extended = [x for x in scored if dte_min <= x[0] <= 90]
    if extended:
        best = min(extended, key=lambda x: x[0])
        return best[1], best[2], best[3]
    # last resort: max DTE available that is still > 20
    far = [x for x in scored if x[0] >= 20]
    if not far:
        return None
    best = max(far, key=lambda x: x[0])
    return best[1], best[2], best[3]


def _invert_iv(premium: float, F: float, K: float, T: float, opt_type: str, r: float = 0.02) -> float:
    if premium <= 0 or T <= 0:
        return float("nan")
    try:
        return implied_volatility(premium, F, K, T, r, opt_type)  # type: ignore[arg-type]
    except Exception:
        return float("nan")


def chain_to_contracts(
    table: pd.DataFrame,
    *,
    underlying: str,
    product: str,
    exchange: str,
    multiplier: float,
    F: float,
    dte: int,
    expire_date: str,
    r: float = 0.02,
) -> tuple[list[OptionContract], float]:
    """Convert Sina option chain table into OptionContract list + ATM IV."""
    T = max(dte, 1) / 365.0
    contracts: list[OptionContract] = []
    atm_ivs: list[float] = []

    for _, row in table.iterrows():
        K = _to_float(row.get("行权价"))
        if K <= 0:
            continue
        call_px = _to_float(row.get("看涨合约-最新价"))
        put_px = _to_float(row.get("看跌合约-最新价"))
        call_bid = _to_float(row.get("看涨合约-买价"))
        call_ask = _to_float(row.get("看涨合约-卖价"))
        put_bid = _to_float(row.get("看跌合约-买价"))
        put_ask = _to_float(row.get("看跌合约-卖价"))
        call_oi = _to_float(row.get("看涨合约-持仓量"))
        put_oi = _to_float(row.get("看跌合约-持仓量"))
        call_sym = str(row.get("看涨合约-看涨期权合约") or f"{underlying}-C-{int(K)}")
        put_sym = str(row.get("看跌合约-看跌期权合约") or f"{underlying}-P-{int(K)}")

        # volume not always present on sina table — treat OI as activity proxy; volume=0
        for opt_type, px, bid, ask, oi, sym in (
            ("CALL", call_px, call_bid, call_ask, call_oi, call_sym),
            ("PUT", put_px, put_bid, put_ask, put_oi, put_sym),
        ):
            if px <= 0 and bid <= 0 and ask <= 0:
                continue
            mid = px if px > 0 else (0.5 * (bid + ask) if bid > 0 and ask > 0 else max(bid, ask))
            if mid <= 0:
                continue
            iv = _invert_iv(mid, F, K, T, opt_type, r=r)
            if not math.isnan(iv) and abs(K / F - 1.0) <= 0.03:
                atm_ivs.append(iv)
            if math.isnan(iv) or iv <= 0:
                iv = 0.25  # placeholder; greeks still informative
            g = black76_greeks(F, K, T, r, iv, opt_type)  # type: ignore[arg-type]
            spread = max(ask - bid, 0.0) if ask > 0 and bid > 0 else 0.0
            contracts.append(
                OptionContract(
                    symbol=sym,
                    underlying=underlying,
                    option_type=opt_type,
                    strike=K,
                    dte=dte,
                    expire_date=expire_date,
                    iv=float(iv),
                    premium=float(mid),
                    F=F,
                    multiplier=multiplier,
                    exchange=exchange,
                    product=product,
                    delta=g.delta,
                    gamma=g.gamma,
                    vega=g.vega,
                    theta=g.theta,
                    volume=0.0,
                    open_interest=float(oi),
                    bid=float(bid),
                    ask=float(ask),
                    spread=float(spread),
                )
            )

    current_iv = float(np.median(atm_ivs)) if atm_ivs else float(np.median([c.iv for c in contracts])) if contracts else 0.2
    return contracts, current_iv


def build_iv_history_proxy(closes: SequenceLike, current_iv: float, window: int = 30) -> list[float]:
    """
    Build a 1y-ish IV history proxy from rolling HV, scaled so the last point
    matches current ATM IV. Used when exchange IV time series is unavailable.
    """
    arr = np.asarray(list(closes), dtype=float)
    if len(arr) < window + 5:
        return [current_iv] * 60
    rets = np.diff(np.log(arr))
    hv_series: list[float] = []
    for i in range(window, len(rets) + 1):
        sample = rets[i - window : i]
        var = float(np.sum((sample - sample.mean()) ** 2) / (window - 1))
        hv_series.append(math.sqrt(252.0 * var))
    hv = np.asarray(hv_series, dtype=float)
    hv = np.clip(hv, 0.05, 1.5)
    scale = current_iv / max(hv[-1], 1e-6)
    iv_hist = (hv * scale).tolist()
    # keep last 252
    return iv_hist[-252:]


SequenceLike = Any


@dataclass
class FetchStatus:
    source: str
    products_ok: list[str]
    products_failed: list[str]
    notes: list[str]


class AkshareMarketData:
    """Fetch commodity option chains + futures history via AkShare."""

    def __init__(self, cache_dir: Optional[str | Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().parents[1] / "data" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_snapshots(
        self,
        *,
        dte_min: int = 30,
        dte_max: int = 60,
        include_index_options: bool = False,
        product_filter: Optional[list[str]] = None,
    ) -> tuple[list[UnderlyingSnapshot], FetchStatus]:
        import akshare as ak

        products = dict(SINA_PRODUCTS)
        if include_index_options:
            # Index options need separate adapters; mark as not implemented in this path
            pass

        ok: list[str] = []
        failed: list[str] = []
        notes: list[str] = []
        snapshots: list[UnderlyingSnapshot] = []

        for cn_name, meta in products.items():
            product = meta["product"]
            if product_filter and product not in product_filter and product.lower() not in [p.lower() for p in product_filter]:
                continue
            try:
                # contracts
                cdf = ak.option_commodity_contract_sina(symbol=cn_name)
                contracts = [str(x) for x in cdf["合约"].tolist()]
                picked = pick_contract_by_dte(contracts, dte_min=dte_min, dte_max=dte_max)
                if picked is None:
                    failed.append(cn_name)
                    notes.append(f"{cn_name}: 无合适 DTE 合约")
                    continue
                contract, expiry, dte = picked

                # futures ohlc
                ohlc = fetch_futures_ohlc(meta["futures"])
                if ohlc.empty:
                    failed.append(cn_name)
                    notes.append(f"{cn_name}: 期货行情为空")
                    continue
                F = float(ohlc["close"].iloc[-1])

                table = ak.option_commodity_contract_table_sina(symbol=cn_name, contract=contract)
                option_contracts, current_iv = chain_to_contracts(
                    table,
                    underlying=contract.upper() if meta["exchange"] == "CZCE" else contract,
                    product=product,
                    exchange=meta["exchange"],
                    multiplier=float(meta["multiplier"]),
                    F=F,
                    dte=dte,
                    expire_date=expiry.isoformat(),
                )
                if not option_contracts:
                    failed.append(cn_name)
                    notes.append(f"{cn_name}: 期权链为空")
                    continue

                iv_hist = build_iv_history_proxy(ohlc["close"].tolist(), current_iv)
                snap = UnderlyingSnapshot(
                    underlying=contract.upper() if meta["exchange"] == "CZCE" else contract,
                    F=F,
                    prices=ohlc["close"].tolist(),
                    iv_history=iv_hist,
                    current_iv=current_iv,
                    contracts=option_contracts,
                    product=product,
                    exchange=meta["exchange"],
                    multiplier=float(meta["multiplier"]),
                    highs=ohlc["high"].tolist(),
                    lows=ohlc["low"].tolist(),
                    product_name=meta["name"],
                    option_month=contract,
                    iv_history_source="hv_scaled_proxy",
                )
                snapshots.append(snap)
                ok.append(cn_name)
            except Exception as exc:
                logger.exception("fetch failed for %s", cn_name)
                failed.append(cn_name)
                notes.append(f"{cn_name}: {type(exc).__name__}: {exc}")

        notes.append("IV Rank/Percentile 使用 HV 缩放代理序列（真实 IV 历史缺失时的近似）。")
        status = FetchStatus(
            source="akshare_sina+futures_main",
            products_ok=ok,
            products_failed=failed,
            notes=notes,
        )
        return snapshots, status
