"""V2 market fetcher: option month → mapped underlying futures, bid/ask preserved."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from data_fetcher.akshare_fetcher import SINA_PRODUCTS, estimate_option_expiry, pick_contract_by_dte

logger = logging.getLogger(__name__)


def _load_json(name: str) -> dict:
    p = Path(__file__).resolve().parents[1] / "config" / name
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _norm_underlying_symbol(contract: str, exchange: str) -> str:
    c = contract.strip()
    if exchange == "CZCE":
        # czce uses uppercase letters + digits e.g. SR2611
        m = re.match(r"([A-Za-z]+)(\d+)", c)
        if m:
            return m.group(1).upper() + m.group(2)
    return c.lower()


@dataclass
class ChainRow:
    strike: float
    call_symbol: str
    put_symbol: str
    call_bid: Optional[float]
    call_ask: Optional[float]
    call_mid: Optional[float]
    call_oi: Optional[float]
    put_bid: Optional[float]
    put_ask: Optional[float]
    put_mid: Optional[float]
    put_oi: Optional[float]


@dataclass
class ProductSnapshotV2:
    product: str
    product_name: str
    exchange: str
    option_month: str
    underlying_futures: str
    quote_date: date
    quote_timestamp: datetime
    data_source: str
    F: float
    settle: Optional[float]
    multiplier: float
    tick_size: float
    expiry_date: date
    dte: int
    chain: list[ChainRow]
    futures_ohlc: pd.DataFrame
    all_months: list[str] = field(default_factory=list)
    tenor_points: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FetchManifest:
    data_source: str
    quote_asof: str
    products_ok: list[str]
    products_failed: list[dict[str, str]]
    methods_version: str = "methods-v2.0.0"


class V2MarketFetcher:
    """Fetch live snapshots via AkShare Sina chain + specific underlying futures."""

    def __init__(self) -> None:
        self.specs = _load_json("product_specs.json")
        self.ticks = _load_json("tick_sizes.json")

    def _tick(self, product: str) -> Optional[float]:
        prods = self.ticks.get("products", {})
        for k in (product, product.lower(), product.upper()):
            if k in prods:
                return float(prods[k])
        return float(self.ticks.get("default_tick", 1.0))

    def _multiplier(self, product: str) -> Optional[float]:
        prods = self.specs.get("products", {})
        for k in (product, product.lower(), product.upper()):
            if k in prods:
                return float(prods[k]["multiplier"])
        return None

    def fetch_underlying_futures(self, symbol: str) -> pd.DataFrame:
        import akshare as ak

        df = ak.futures_zh_daily_sina(symbol=symbol)
        df = df.rename(
            columns={
                "date": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "hold": "open_interest",
                "settle": "settle",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        for c in ("open", "high", "low", "close", "settle"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.sort_values("date").reset_index(drop=True)

    def fetch_product(
        self,
        cn_name: str,
        meta: dict[str, Any],
        *,
        dte_min: int = 30,
        dte_max: int = 60,
        as_of: Optional[date] = None,
    ) -> ProductSnapshotV2:
        import akshare as ak

        as_of = as_of or date.today()
        product = meta["product"]
        mult = self._multiplier(product)
        tick = self._tick(product)
        if mult is None:
            raise ValueError(f"missing multiplier for {product}")

        cdf = ak.option_commodity_contract_sina(symbol=cn_name)
        months = [str(x) for x in cdf["合约"].tolist()]
        picked = pick_contract_by_dte(months, dte_min=dte_min, dte_max=dte_max, as_of=as_of)
        if picked is None:
            raise ValueError("no option month in DTE window")
        option_month, expiry, dte = picked
        underlying = _norm_underlying_symbol(option_month, meta["exchange"])

        fut = self.fetch_underlying_futures(underlying)
        if fut.empty:
            raise ValueError(f"underlying futures empty: {underlying}")
        last = fut.iloc[-1]
        quote_date = pd.Timestamp(last["date"]).date()
        F = float(last["settle"] if pd.notna(last.get("settle")) else last["close"])
        settle = float(last["settle"]) if pd.notna(last.get("settle")) else None

        table = ak.option_commodity_contract_table_sina(symbol=cn_name, contract=option_month)
        chain: list[ChainRow] = []
        for _, row in table.iterrows():
            K = float(row["行权价"])
            cb = float(row["看涨合约-买价"]) if pd.notna(row.get("看涨合约-买价")) else None
            ca = float(row["看涨合约-卖价"]) if pd.notna(row.get("看涨合约-卖价")) else None
            pb = float(row["看跌合约-买价"]) if pd.notna(row.get("看跌合约-买价")) else None
            pa = float(row["看跌合约-卖价"]) if pd.notna(row.get("看跌合约-卖价")) else None
            if cb is not None and cb <= 0:
                cb = None
            if ca is not None and ca <= 0:
                ca = None
            if pb is not None and pb <= 0:
                pb = None
            if pa is not None and pa <= 0:
                pa = None
            cm = 0.5 * (cb + ca) if cb and ca else None
            pm = 0.5 * (pb + pa) if pb and pa else None
            chain.append(
                ChainRow(
                    strike=K,
                    call_symbol=str(row.get("看涨合约-看涨期权合约") or ""),
                    put_symbol=str(row.get("看跌合约-看跌期权合约") or ""),
                    call_bid=cb,
                    call_ask=ca,
                    call_mid=cm,
                    call_oi=float(row["看涨合约-持仓量"]) if pd.notna(row.get("看涨合约-持仓量")) else None,
                    put_bid=pb,
                    put_ask=pa,
                    put_mid=pm,
                    put_oi=float(row["看跌合约-持仓量"]) if pd.notna(row.get("看跌合约-持仓量")) else None,
                )
            )

        now = datetime.now()
        return ProductSnapshotV2(
            product=product,
            product_name=meta["name"],
            exchange=meta["exchange"],
            option_month=option_month,
            underlying_futures=underlying,
            quote_date=quote_date,
            quote_timestamp=now,
            data_source="akshare_sina_chain+futures_zh_daily",
            F=F,
            settle=settle,
            multiplier=mult,
            tick_size=tick or 1.0,
            expiry_date=expiry,
            dte=dte,
            chain=chain,
            futures_ohlc=fut,
            all_months=months,
        )

    def fetch_all(
        self,
        *,
        dte_min: int = 30,
        dte_max: int = 60,
        as_of: Optional[date] = None,
    ) -> tuple[list[ProductSnapshotV2], FetchManifest]:
        as_of = as_of or date.today()
        ok: list[ProductSnapshotV2] = []
        failed: list[dict[str, str]] = []
        for cn_name, meta in SINA_PRODUCTS.items():
            try:
                ok.append(self.fetch_product(cn_name, meta, dte_min=dte_min, dte_max=dte_max, as_of=as_of))
            except Exception as exc:
                logger.exception("fetch %s", cn_name)
                failed.append({"product": cn_name, "error": f"{type(exc).__name__}: {exc}"})

        manifest = FetchManifest(
            data_source="akshare_sina_chain+futures_zh_daily",
            quote_asof=datetime.now().isoformat(timespec="seconds"),
            products_ok=[s.product_name for s in ok],
            products_failed=failed,
        )
        return ok, manifest
