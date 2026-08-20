"""V2 market fetcher: option month → mapped underlying futures, bid/ask preserved."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from data_fetcher.akshare_fetcher import pick_contract_by_dte
from data_fetcher.option_universe import OptionProduct, load_universe
from data_fetcher.snapshot_models import ChainRow, FetchManifest, ProductSnapshotV2, norm_underlying_symbol

logger = logging.getLogger(__name__)


def _load_json(name: str) -> dict:
    p = Path(__file__).resolve().parents[1] / "config" / name
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class V2MarketFetcher:
    """Fetch live snapshots via Sina (bid/ask) or exchange daily settle chains."""

    def __init__(self) -> None:
        self.specs = _load_json("product_specs.json")
        self.ticks = _load_json("tick_sizes.json")
        self._exchange = None

    def _get_exchange(self):
        if self._exchange is None:
            from data_fetcher.exchange_fetcher import ExchangeChainFetcher

            self._exchange = ExchangeChainFetcher(
                self.specs, self.ticks, self.fetch_underlying_futures
            )
        return self._exchange

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

        def _pull(sym: str) -> pd.DataFrame:
            df = ak.futures_zh_daily_sina(symbol=sym)
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

        try:
            df = _pull(symbol)
            if not df.empty:
                return df
        except Exception:
            pass
        # fallback: continuous main contract XX0
        letters = re.match(r"^([A-Za-z]+)", symbol)
        if letters:
            root = letters.group(1)
            for alt in (root.upper() + "0", root.lower() + "0"):
                try:
                    df = _pull(alt)
                    if not df.empty:
                        return df
                except Exception:
                    continue
        return pd.DataFrame()

    def fetch_product_sina(
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
        underlying = norm_underlying_symbol(option_month, meta["exchange"])

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

    def fetch_product(
        self,
        item: OptionProduct,
        *,
        dte_min: int = 30,
        dte_max: int = 60,
        as_of: Optional[date] = None,
    ) -> ProductSnapshotV2:
        if item.source == "sina":
            meta = {
                "product": item.product,
                "name": item.name,
                "exchange": item.exchange,
            }
            return self.fetch_product_sina(
                item.cn_name, meta, dte_min=dte_min, dte_max=dte_max, as_of=as_of
            )
        return self._get_exchange().fetch_product(
            item, dte_min=dte_min, dte_max=dte_max, as_of=as_of
        )

    def fetch_all(
        self,
        *,
        dte_min: int = 30,
        dte_max: int = 60,
        as_of: Optional[date] = None,
        universe: Optional[list[OptionProduct]] = None,
    ) -> tuple[list[ProductSnapshotV2], FetchManifest]:
        as_of = as_of or date.today()
        items = universe or load_universe()
        ok: list[ProductSnapshotV2] = []
        failed: list[dict[str, str]] = []
        sources: set[str] = set()
        for item in items:
            try:
                snap = self.fetch_product(item, dte_min=dte_min, dte_max=dte_max, as_of=as_of)
                ok.append(snap)
                sources.add(snap.data_source)
            except Exception as exc:
                logger.exception("fetch %s (%s)", item.cn_name, item.product)
                failed.append(
                    {
                        "product": item.product,
                        "name": item.name,
                        "cn_name": item.cn_name,
                        "source": item.source,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        manifest = FetchManifest(
            data_source="multi:" + "+".join(sorted(sources)) if sources else "none",
            quote_asof=datetime.now().isoformat(timespec="seconds"),
            products_ok=[f"{s.product_name}({s.product})" for s in ok],
            products_failed=failed,
        )
        return ok, manifest


# Re-export for backward compatibility
__all__ = [
    "ChainRow",
    "FetchManifest",
    "ProductSnapshotV2",
    "V2MarketFetcher",
]
