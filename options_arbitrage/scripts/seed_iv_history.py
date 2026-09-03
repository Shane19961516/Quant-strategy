#!/usr/bin/env python3
"""
Seed / update fixed-tenor ATM IV history for IV Rank & Percentile gates.

Sources:
  - CZCE: option_hist_czce ATM (Δ≈0.5) 隐含波动率
  - SHFE: invert ATM IV from option_hist_shfe settlement + underlying futures
  - CSV:  --import-csv PRODUCT=path.csv

Usage:
  python scripts/seed_iv_history.py --days 260 --products SR,CF,TA,MA,RM,OI
  python scripts/seed_iv_history.py --import-csv SR=./data/fixtures/SR_atm30.csv
  python scripts/seed_iv_history.py --from-fixtures   # copy bundled samples
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.bs76_engine import implied_volatility
from core.iv_history_store import IVHistoryStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_iv")
warnings.filterwarnings("ignore")

CZCE_MAP = {
    "SR": "白糖期权",
    "CF": "棉花期权",
    "TA": "PTA期权",
    "MA": "甲醇期权",
    "RM": "菜籽粕期权",
    "OI": "菜籽油期权",
    "PK": "花生期权",
    "ZC": "动力煤期权",
    "AP": "苹果期权",
    "CJ": "红枣期权",
    "FG": "玻璃期权",
    "SA": "纯碱期权",
    "UR": "尿素期权",
    "SF": "硅铁期权",
    "SM": "锰硅期权",
    "PF": "短纤期权",
    "PR": "瓶片期权",
    "PL": "丙烯期权",
    "PX": "对二甲苯期权",
    "SH": "烧碱期权",
}

SHFE_MAP = {
    "cu": "铜期权",
    "au": "黄金期权",
    "ru": "天胶期权",
    "ag": "白银期权",
    "al": "铝期权",
    "rb": "螺纹钢期权",
    "br": "丁二烯橡胶期权",
    "sn": "锡期权",
    "ni": "镍期权",
    "zn": "锌期权",
    "pb": "铅期权",
    "ao": "氧化铝期权",
    "sc": "原油期权",
    "sp": "纸浆期权",
    "nr": "20号胶期权",
}

GFEX_MAP = {
    "si": "工业硅",
    "lc": "碳酸锂",
    "ps": "多晶硅",
}


def _trading_days(end: date, n: int) -> list[date]:
    # business days approx; exchange calendar filtered by empty API responses
    days = pd.bdate_range(end=end, periods=n).date.tolist()
    return list(days)


def atm_iv_from_czce_df(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    c = df[df["合约代码"].astype(str).str.contains("C", regex=False)].copy()
    if c.empty:
        return None
    c["DELTA"] = pd.to_numeric(c["DELTA"], errors="coerce")
    c["隐含波动率"] = pd.to_numeric(c["隐含波动率"], errors="coerce")
    c = c.dropna(subset=["DELTA", "隐含波动率"])
    if c.empty:
        return None
    row = c.iloc[(c["DELTA"] - 0.5).abs().argmin()]
    iv = float(row["隐含波动率"])
    return iv / 100.0 if iv > 3 else iv


def fetch_czce_day(cn_name: str, d: date) -> tuple[str, float | None]:
    import akshare as ak

    ds = d.strftime("%Y%m%d")
    try:
        df = ak.option_hist_czce(symbol=cn_name, trade_date=ds)
        return d.isoformat(), atm_iv_from_czce_df(df)
    except Exception:
        return d.isoformat(), None


def seed_czce(product: str, days: int, end: date, workers: int = 12) -> int:
    cn = CZCE_MAP[product]
    store = IVHistoryStore()
    existing = store.load(product)
    have = set(existing.dates) if existing else set()
    targets = [d for d in _trading_days(end, days) if d.isoformat() not in have]
    logger.info("%s CZCE: need %d days (have %d)", product, len(targets), len(have))
    if not targets:
        return existing.n if existing else 0

    dates: list[str] = []
    vals: list[float] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_czce_day, cn, d): d for d in targets}
        for fut in as_completed(futs):
            ds, iv = fut.result()
            if iv is not None and iv > 0:
                dates.append(ds)
                vals.append(iv)
    if dates:
        series = store.save(product, dates, vals, source="exchange_czce_atm", merge=True)
        logger.info("%s saved n=%d source=%s", product, series.n, series.source)
        return series.n
    return existing.n if existing else 0


def _invert_shfe_atm(df: pd.DataFrame, F: float, trade_date: date) -> float | None:
    """Invert ATM call IV from SHFE hist settle prices."""
    if df is None or df.empty or F <= 0:
        return None
    # contract like cu2609C70000
    df = df.copy()
    df["合约代码"] = df["合约代码"].astype(str)
    calls = df[df["合约代码"].str.contains("C")].copy()
    if calls.empty:
        return None
    # parse strike from end digits
    def strike(code: str) -> float | None:
        import re

        m = re.search(r"C(\d+)$", code.upper())
        return float(m.group(1)) if m else None

    calls["K"] = calls["合约代码"].map(strike)
    calls["settle"] = pd.to_numeric(calls.get("结算价", calls.get("收盘价")), errors="coerce")
    calls = calls.dropna(subset=["K", "settle"])
    calls = calls[calls["settle"] > 0]
    if calls.empty:
        return None
    # nearest ATM
    row = calls.iloc[(calls["K"] / F - 1.0).abs().argmin()]
    # crude DTE: assume ~45d mid if unknown
    T = 45 / 365.0
    try:
        return implied_volatility(float(row["settle"]), F, float(row["K"]), T, 0.02, "CALL")
    except Exception:
        return None


def seed_shfe(product: str, days: int, end: date, workers: int = 8) -> int:
    import akshare as ak

    cn_name = SHFE_MAP.get(product.lower())
    if not cn_name:
        logger.warning("no SHFE map for %s", product)
        return 0
    store = IVHistoryStore()
    existing = store.load(product)
    have = set(existing.dates) if existing else set()
    targets = [d for d in _trading_days(end, days) if d.isoformat() not in have]
    logger.info("%s SHFE: need %d days", product, len(targets))
    fut_sym = f"{product.upper()}0" if product.lower() in {"cu", "au", "ag", "al", "rb"} else f"{product.lower()}0"
    try:
        fut = ak.futures_main_sina(symbol=fut_sym, start_date="20240101", end_date=end.strftime("%Y%m%d"))
        fut["日期"] = pd.to_datetime(fut["日期"])
        fut = fut.set_index("日期")
    except Exception as exc:
        logger.warning("futures fail %s: %s", product, exc)
        return existing.n if existing else 0

    dates: list[str] = []
    vals: list[float] = []

    def one(d: date):
        ds = d.strftime("%Y%m%d")
        try:
            df = ak.option_hist_shfe(symbol=cn_name, trade_date=ds)
            if df.empty:
                return d.isoformat(), None
            if d.isoformat()[:10] in fut.index.strftime("%Y-%m-%d"):
                F = float(fut.loc[fut.index.strftime("%Y-%m-%d") == d.isoformat()]["收盘价"].iloc[-1])
            else:
                prior = fut[fut.index.date <= d]
                if prior.empty:
                    return d.isoformat(), None
                F = float(prior["收盘价"].iloc[-1])
            return d.isoformat(), _invert_shfe_atm(df, F, d)
        except Exception:
            return d.isoformat(), None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed({ex.submit(one, d): d for d in targets}):
            ds, iv = fut.result()
            if iv and iv > 0:
                dates.append(ds)
                vals.append(float(iv))
    if dates:
        series = store.save(product, dates, vals, source="exchange_shfe_inverted", merge=True)
        return series.n
    return existing.n if existing else 0


def write_fixture_from_series(product: str, out: Path) -> None:
    store = IVHistoryStore()
    s = store.load(product)
    if not s:
        raise SystemExit(f"no series for {product}")
    pd.DataFrame({"date": s.dates, "atm_iv": s.values}).to_csv(out, index=False)


def build_hv_calibrated_csv(
    product: str,
    futures_symbol: str,
    current_atm: float,
    out: Path,
    days: int = 260,
) -> Path:
    """
    Build a user_csv ATM IV path shaped by HV, anchored to current ATM.
    For DCE etc. when exchange hist unavailable — marked user_csv / bootstrap.
    """
    import akshare as ak

    end = date.today().strftime("%Y%m%d")
    fut = ak.futures_main_sina(symbol=futures_symbol, start_date="20240101", end_date=end)
    closes = pd.to_numeric(fut["收盘价"], errors="coerce").dropna().tolist()
    if len(closes) < days + 5:
        raise RuntimeError("insufficient futures history")
    rets = np.diff(np.log(closes))
    hv = []
    dates = pd.to_datetime(fut["日期"]).iloc[1:].tolist()
    for i in range(30, len(rets) + 1):
        s = rets[i - 30 : i]
        var = float(np.sum((s - s.mean()) ** 2) / 29)
        hv.append(math_sqrt(252 * var))
    hv = np.asarray(hv[-days:], dtype=float)
    scale = current_atm / max(hv[-1], 1e-6)
    atm = (hv * scale).tolist()
    d_out = [pd.Timestamp(x).strftime("%Y-%m-%d") for x in dates[-len(atm) :]]
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": d_out, "atm_iv": atm}).to_csv(out, index=False)
    store = IVHistoryStore()
    store.import_csv(product, out, source="user_csv")
    return out


def math_sqrt(x: float) -> float:
    return float(np.sqrt(x))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=260)
    p.add_argument("--products", type=str, default="SR,CF,TA,MA,RM,OI")
    p.add_argument("--end", type=str, default=None)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--import-csv", action="append", default=[], help="PRODUCT=/path/to.csv")
    p.add_argument("--seed-dce-csv", action="store_true", help="Build user_csv for DCE from HV×ATM")
    p.add_argument("--dce-atm", type=str, default="m:0.22,c:0.13,i:0.16,pg:0.29")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    end = date.fromisoformat(args.end) if args.end else date.today()

    for item in args.import_csv:
        if "=" not in item:
            raise SystemExit("--import-csv PRODUCT=path")
        prod, path = item.split("=", 1)
        s = IVHistoryStore().import_csv(prod, Path(path), source="user_csv")
        logger.info("imported %s n=%d", prod, s.n)

    products = [x.strip() for x in args.products.split(",") if x.strip() and x.strip().upper() != "NONE"]
    for prod in products:
        if prod.upper() in CZCE_MAP:
            seed_czce(prod.upper(), args.days, end, workers=args.workers)
        elif prod.lower() in SHFE_MAP:
            seed_shfe(prod.lower(), args.days, end, workers=max(4, args.workers // 2))
        elif prod.lower() in GFEX_MAP:
            logger.warning("GFEX IV seed via exchange hist not implemented for %s; use --import-csv", prod)
        else:
            logger.warning("skip unsupported product %s (use --import-csv or --seed-dce-csv)", prod)

    if args.seed_dce_csv:
        fut_map = {"m": "M0", "c": "C0", "i": "I0", "pg": "PG0"}
        atm_map = {}
        for pair in args.dce_atm.split(","):
            k, v = pair.split(":")
            atm_map[k.strip()] = float(v)
        fix_dir = ROOT / "data" / "fixtures" / "iv_history"
        for prod, atm in atm_map.items():
            out = fix_dir / f"{prod.upper()}_atm30.csv"
            build_hv_calibrated_csv(prod, fut_map[prod], atm, out, days=args.days)
            logger.info("DCE csv seeded %s -> %s", prod, out)

    # summary
    store = IVHistoryStore()
    for p in store.list_products():
        s = store.load(p)
        if s:
            print(f"{p}: n={s.n} source={s.source} last={s.dates[-1] if s.dates else None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
