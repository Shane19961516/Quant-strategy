"""Exchange daily option chain fetchers (CZCE / SHFE / GFEX / DCE)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

from data_fetcher.akshare_fetcher import estimate_option_expiry, pick_contract_by_dte
from data_fetcher.option_universe import OptionProduct
from data_fetcher.snapshot_models import ChainRow, ProductSnapshotV2, norm_underlying_symbol

logger = logging.getLogger(__name__)

_CZCE_RE = re.compile(r"^([A-Za-z]{1,2}\d{3,4})([CP])(\d+)$")
_SHFE_RE = re.compile(r"^([A-Za-z]+\d{4})([CP])(\d+)$", re.I)
_GFEX_RE = re.compile(r"^([A-Za-z]+\d{4})-([CP])-(\d+)$", re.I)
_DCE_RE = re.compile(r"^([A-Za-z]+\d{4})-([CP])-(\d+)$", re.I)


def _recent_trade_dates(as_of: date, n: int = 8) -> list[str]:
    days: list[str] = []
    d = as_of
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return days


def _parse_iv(raw: Any) -> Optional[float]:
    try:
        v = float(raw)
        if v <= 0 or pd.isna(v):
            return None
        return v / 100.0 if v > 3 else v
    except Exception:
        return None


def _parse_settle(raw: Any) -> Optional[float]:
    try:
        v = float(raw)
        if v <= 0 or pd.isna(v):
            return None
        return v
    except Exception:
        return None


def _build_chain_from_pairs(
    pairs: dict[float, dict[str, Any]],
    *,
    month: str,
    product: str,
) -> list[ChainRow]:
    chain: list[ChainRow] = []
    for K in sorted(pairs.keys()):
        row = pairs[K]
        chain.append(
            ChainRow(
                strike=K,
                call_symbol=row.get("call_symbol", f"{month}C{int(K)}"),
                put_symbol=row.get("put_symbol", f"{month}P{int(K)}"),
                call_bid=row.get("call_bid"),
                call_ask=row.get("call_ask"),
                call_mid=row.get("call_settle"),
                call_oi=row.get("call_oi"),
                put_bid=row.get("put_bid"),
                put_ask=row.get("put_ask"),
                put_mid=row.get("put_settle"),
                put_oi=row.get("put_oi"),
                call_iv=row.get("call_iv"),
                put_iv=row.get("put_iv"),
            )
        )
    return chain


def _group_czce(df: pd.DataFrame) -> dict[str, dict[float, dict[str, Any]]]:
    months: dict[str, dict[float, dict[str, Any]]] = {}
    for _, row in df.iterrows():
        sym = str(row["合约代码"])
        m = _CZCE_RE.match(sym)
        if not m:
            continue
        month, cp, strike_s = m.group(1), m.group(2).upper(), m.group(3)
        K = float(strike_s)
        settle = _parse_settle(row.get("今结算") or row.get("今收盘"))
        iv = _parse_iv(row.get("隐含波动率"))
        oi = float(row["持仓量"]) if pd.notna(row.get("持仓量")) else None
        months.setdefault(month, {}).setdefault(K, {})
        side = "call" if cp == "C" else "put"
        months[month][K][f"{side}_symbol"] = sym
        months[month][K][f"{side}_settle"] = settle
        months[month][K][f"{side}_oi"] = oi
        months[month][K][f"{side}_iv"] = iv
    return months


def _group_shfe(df: pd.DataFrame) -> dict[str, dict[float, dict[str, Any]]]:
    months: dict[str, dict[float, dict[str, Any]]] = {}
    for _, row in df.iterrows():
        sym = str(row["合约代码"])
        m = _SHFE_RE.match(sym)
        if not m:
            continue
        month, cp, strike_s = m.group(1), m.group(2).upper(), m.group(3)
        K = float(strike_s)
        settle = _parse_settle(row.get("结算价") or row.get("收盘价"))
        oi = float(row["持仓量"]) if pd.notna(row.get("持仓量")) else None
        months.setdefault(month, {}).setdefault(K, {})
        side = "call" if cp == "C" else "put"
        months[month][K][f"{side}_symbol"] = sym
        months[month][K][f"{side}_settle"] = settle
        months[month][K][f"{side}_oi"] = oi
    return months


def _group_gfex(df: pd.DataFrame) -> dict[str, dict[float, dict[str, Any]]]:
    months: dict[str, dict[float, dict[str, Any]]] = {}
    for _, row in df.iterrows():
        sym = str(row["合约名称"])
        m = _GFEX_RE.match(sym)
        if not m:
            continue
        month, cp, strike_s = m.group(1), m.group(2).upper(), m.group(3)
        K = float(strike_s)
        settle = _parse_settle(row.get("结算价") or row.get("收盘价"))
        iv = _parse_iv(row.get("隐含波动率"))
        oi = float(row["持仓量"]) if pd.notna(row.get("持仓量")) else None
        months.setdefault(month, {}).setdefault(K, {})
        side = "call" if cp == "C" else "put"
        months[month][K][f"{side}_symbol"] = sym
        months[month][K][f"{side}_settle"] = settle
        months[month][K][f"{side}_oi"] = oi
        months[month][K][f"{side}_iv"] = iv
    return months


def _group_dce(df: pd.DataFrame) -> dict[str, dict[float, dict[str, Any]]]:
    """DCE daily quotes: contract field like m2611-C-3000."""
    months: dict[str, dict[float, dict[str, Any]]] = {}
    contract_col = "合约" if "合约" in df.columns else "contractId"
    for _, row in df.iterrows():
        sym = str(row.get(contract_col, ""))
        m = _DCE_RE.match(sym)
        if not m:
            continue
        month, cp, strike_s = m.group(1), m.group(2).upper(), m.group(3)
        K = float(strike_s)
        settle = _parse_settle(row.get("结算价") or row.get("clearPrice") or row.get("收盘价"))
        iv = _parse_iv(row.get("隐含波动率(%)") or row.get("impliedVolatility"))
        oi = float(row["持仓量"]) if pd.notna(row.get("持仓量")) else None
        months.setdefault(month, {}).setdefault(K, {})
        side = "call" if cp == "C" else "put"
        months[month][K][f"{side}_symbol"] = sym
        months[month][K][f"{side}_settle"] = settle
        months[month][K][f"{side}_oi"] = oi
        months[month][K][f"{side}_iv"] = iv
    return months


class ExchangeChainFetcher:
    """Fetch option chains from exchange daily settlement APIs."""

    def __init__(self, specs: dict, ticks: dict, fetch_underlying_futures) -> None:
        self.specs = specs
        self.ticks = ticks
        self.fetch_underlying_futures = fetch_underlying_futures

    def _multiplier(self, product: str) -> Optional[float]:
        prods = self.specs.get("products", {})
        for k in (product, product.lower(), product.upper()):
            if k in prods:
                return float(prods[k]["multiplier"])
        return None

    def _tick(self, product: str) -> float:
        prods = self.ticks.get("products", {})
        for k in (product, product.lower(), product.upper()):
            if k in prods:
                return float(prods[k])
        return float(self.ticks.get("default_tick", 1.0))

    def _fetch_exchange_df(
        self,
        item: OptionProduct,
        trade_date: str,
    ) -> pd.DataFrame:
        import akshare as ak

        sym = item.fetch_symbol
        if item.source == "czce":
            return ak.option_hist_czce(symbol=sym, trade_date=trade_date)
        if item.source == "shfe":
            return ak.option_hist_shfe(symbol=sym, trade_date=trade_date)
        if item.source == "gfex":
            # GFEX API uses commodity name without 期权
            gfex_sym = sym.replace("期权", "")
            return ak.option_hist_gfex(symbol=gfex_sym, trade_date=trade_date)
        if item.source == "dce":
            from data_fetcher.dce_client import option_hist_dce_browser

            return option_hist_dce_browser(symbol=sym, trade_date=trade_date)
        raise ValueError(f"unsupported exchange source: {item.source}")

    def _group_df(self, item: OptionProduct, df: pd.DataFrame) -> dict[str, dict[float, dict[str, Any]]]:
        if item.source == "czce":
            return _group_czce(df)
        if item.source == "shfe":
            return _group_shfe(df)
        if item.source == "gfex":
            return _group_gfex(df)
        if item.source == "dce":
            return _group_dce(df)
        raise ValueError(item.source)

    def fetch_product(
        self,
        item: OptionProduct,
        *,
        dte_min: int = 30,
        dte_max: int = 60,
        as_of: Optional[date] = None,
    ) -> ProductSnapshotV2:
        as_of = as_of or date.today()
        mult = self._multiplier(item.product)
        if mult is None:
            raise ValueError(f"missing multiplier for {item.product}")

        df: pd.DataFrame | None = None
        quote_date = as_of
        for ds in _recent_trade_dates(as_of):
            try:
                tmp = self._fetch_exchange_df(item, ds)
                if tmp is not None and len(tmp) > 0:
                    df = tmp
                    quote_date = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
                    break
            except Exception:
                continue
        if df is None or df.empty:
            raise ValueError(f"exchange chain empty for {item.cn_name}")

        grouped = self._group_df(item, df)
        months = list(grouped.keys())
        picked = pick_contract_by_dte(months, dte_min=dte_min, dte_max=dte_max, as_of=as_of)
        if picked is None:
            raise ValueError("no option month in DTE window")
        option_month, expiry, dte = picked
        underlying = norm_underlying_symbol(option_month, item.exchange)

        fut = self.fetch_underlying_futures(underlying)
        if fut.empty:
            raise ValueError(f"underlying futures empty: {underlying}")
        last = fut.iloc[-1]
        F = float(last["settle"] if pd.notna(last.get("settle")) else last["close"])
        settle = float(last["settle"]) if pd.notna(last.get("settle")) else None

        pairs = grouped[option_month]
        chain = _build_chain_from_pairs(pairs, month=option_month, product=item.product)

        return ProductSnapshotV2(
            product=item.product,
            product_name=item.name,
            exchange=item.exchange,
            option_month=option_month,
            underlying_futures=underlying,
            quote_date=quote_date,
            quote_timestamp=datetime.now(),
            data_source=f"akshare_exchange_{item.source}_settle",
            F=F,
            settle=settle,
            multiplier=mult,
            tick_size=self._tick(item.product),
            expiry_date=expiry,
            dte=dte,
            chain=chain,
            futures_ohlc=fut,
            all_months=months,
        )
