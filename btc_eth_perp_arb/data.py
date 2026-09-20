"""Fetch and align Binance USDT-M 1-minute last/mark/index/premium + funding.

Venue REST (`fapi.binance.com`) is geo-blocked from this environment (HTTP 451).
Historical files come from `data.binance.vision`, which is the same Binance UM
archive. Daily kline dumps lag ~1–2 days; funding monthly dumps lag further
(September 2026 was not published as of 2026-09-20). Missing official funding
rows are reconstructed from 1-minute premium-index klines with Binance's
published clamp formula and checked against the last official month.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests

from .config import (
    CACHE_DIR,
    INTEREST_PER_8H,
    RAW_DIR,
    SYMBOLS,
    VENUE,
    VISION_BASE,
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "btc-eth-perp-arb/0.1 (research backtest)"})

KLINE_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


def _get(url: str, retries: int = 5, timeout: int = 60) -> requests.Response:
    last: Exception | None = None
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout)
            if r.status_code == 404:
                return r
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (2**i))
    raise RuntimeError(f"GET failed {url}: {last}")


def _daterange(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def latest_vision_day(symbol: str = "BTCUSDT", lookback_days: int = 10) -> date:
    """Most recent daily kline zip that actually exists on Vision."""
    today = datetime.now(timezone.utc).date()
    for i in range(lookback_days):
        d = today - timedelta(days=i)
        url = (
            f"{VISION_BASE}/daily/klines/{symbol}/1m/"
            f"{symbol}-1m-{d.isoformat()}.zip"
        )
        r = _get(url)
        if r.status_code == 200 and r.content[:2] == b"PK":
            return d
    raise RuntimeError("no recent Vision daily kline zip found")


def _save_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def download_daily_zip(kind: str, symbol: str, day: date) -> Path | None:
    """kind: klines | markPriceKlines | indexPriceKlines | premiumIndexKlines."""
    name = f"{symbol}-1m-{day.isoformat()}.zip"
    url = f"{VISION_BASE}/daily/{kind}/{symbol}/1m/{name}"
    dest = RAW_DIR / kind / symbol / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = _get(url)
    if r.status_code == 404:
        return None
    _save_bytes(dest, r.content)
    return dest


def download_monthly_kline_zip(kind: str, symbol: str, year: int, month: int) -> Path | None:
    """kind: klines | markPriceKlines | indexPriceKlines | premiumIndexKlines."""
    name = f"{symbol}-1m-{year:04d}-{month:02d}.zip"
    url = f"{VISION_BASE}/monthly/{kind}/{symbol}/1m/{name}"
    dest = RAW_DIR / "monthly" / kind / symbol / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = _get(url, timeout=120)
    if r.status_code == 404:
        return None
    _save_bytes(dest, r.content)
    return dest


def _month_end(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


def _split_monthly_and_daily(start: date, end: date) -> tuple[list[tuple[int, int]], list[date]]:
    """Complete calendar months as monthly zips; leftover days as daily zips."""
    months: list[tuple[int, int]] = []
    days: list[date] = []
    cur = start
    while cur <= end:
        me = _month_end(cur)
        month_start = date(cur.year, cur.month, 1)
        if cur == month_start and me <= end:
            months.append((cur.year, cur.month))
            cur = me + timedelta(days=1)
        else:
            chunk_end = min(me, end)
            days.extend(_daterange(cur, chunk_end))
            cur = chunk_end + timedelta(days=1)
    return months, days


def download_monthly_funding(symbol: str, year: int, month: int) -> Path | None:
    name = f"{symbol}-fundingRate-{year:04d}-{month:02d}.zip"
    url = f"{VISION_BASE}/monthly/fundingRate/{symbol}/{name}"
    dest = RAW_DIR / "fundingRate" / symbol / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = _get(url)
    if r.status_code == 404:
        return None
    _save_bytes(dest, r.content)
    return dest


def _read_kline_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        inner = zf.namelist()[0]
        raw = zf.read(inner)
    bio = io.BytesIO(raw)
    peek = raw[:80].decode("utf-8", errors="ignore")
    header = peek.lower().startswith("open_time")
    df = pd.read_csv(bio, header=0 if header else None)
    if not header:
        n = min(len(df.columns), len(KLINE_COLS))
        df.columns = list(KLINE_COLS[:n]) + [f"extra_{i}" for i in range(n, len(df.columns))]
    df = df.rename(columns={c: str(c).strip() for c in df.columns})
    numeric = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "count",
    ]
    for c in numeric:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open_time"]).drop_duplicates("open_time")
    df["open_time"] = df["open_time"].astype("int64")
    return df.sort_values("open_time").reset_index(drop=True)


def _read_funding_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        raw = zf.read(zf.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    df["calc_time"] = pd.to_numeric(df["calc_time"], errors="coerce")
    df["funding_interval_hours"] = pd.to_numeric(df["funding_interval_hours"], errors="coerce")
    df["last_funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
    df["funding_ts"] = (df["calc_time"].astype("int64") // 60_000) * 60_000
    df["source"] = "official_monthly"
    return df.dropna(subset=["funding_ts", "last_funding_rate"])


def load_official_funding(symbol: str, months: Iterable[tuple[int, int]]) -> pd.DataFrame:
    frames = []
    for y, m in months:
        path = download_monthly_funding(symbol, y, m)
        if path is None:
            continue
        part = _read_funding_zip(path)
        part["symbol"] = symbol
        frames.append(part)
    if not frames:
        return pd.DataFrame(
            columns=[
                "funding_ts",
                "last_funding_rate",
                "funding_interval_hours",
                "symbol",
                "source",
            ]
        )
    return pd.concat(frames, ignore_index=True)


def reconstruct_funding_from_premium(
    premium_close: pd.Series,
    interval_hours: int = 8,
    interest_per_8h: float = INTEREST_PER_8H,
) -> pd.DataFrame:
    """Binance UM funding ≈ P + clamp(I - P, -0.05%, +0.05%).

    `premium_close` is 1-minute premium index, indexed by bar open (UTC).
    Settlement times are the usual 00:00/08:00/16:00 UTC grid (8h default).
    Mean of premium over [T-interval, T) is used; T itself is excluded so the
    average does not peek into the settlement minute.
    """
    if premium_close.empty:
        return pd.DataFrame(columns=["funding_ts", "last_funding_rate", "funding_interval_hours"])
    s = premium_close.sort_index().astype(float)
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    else:
        s.index = s.index.tz_convert("UTC")
    interest = interest_per_8h * (interval_hours / 8.0)
    start = s.index.min().floor(f"{interval_hours}h")
    end = s.index.max().ceil(f"{interval_hours}h")
    settlements = pd.date_range(start, end, freq=f"{interval_hours}h", tz="UTC")
    rows = []
    delta = pd.Timedelta(hours=interval_hours)
    for t in settlements:
        window = s.loc[(s.index >= t - delta) & (s.index < t)]
        if window.empty:
            continue
        p = float(window.mean())
        clamped = min(0.0005, max(-0.0005, interest - p))
        rate = p + clamped
        rows.append(
            {
                "funding_ts": int(t.value // 1_000_000),
                "last_funding_rate": rate,
                "funding_interval_hours": interval_hours,
                "source": "premium_reconstructed",
            }
        )
    return pd.DataFrame(rows)


def _leg_frame(kind_map: dict[str, pd.DataFrame], prefix: str) -> pd.DataFrame:
    last = kind_map["klines"]
    mark = kind_map["markPriceKlines"]
    index = kind_map["indexPriceKlines"]
    prem = kind_map.get("premiumIndexKlines")

    out = pd.DataFrame(
        {
            "bar_open_ts": last["open_time"].astype("int64"),
            "bar_close_ts": last["open_time"].astype("int64") + 59_999,
            f"{prefix}_open": last["open"],
            f"{prefix}_high": last["high"],
            f"{prefix}_low": last["low"],
            f"{prefix}_close": last["close"],
            f"{prefix}_volume": last["volume"],
            f"{prefix}_quote_volume": last["quote_volume"],
            f"{prefix}_n_trades": last["count"],
        }
    )
    mark_s = mark.set_index("open_time")
    out[f"{prefix}_mark_open"] = mark_s.reindex(out["bar_open_ts"])["open"].to_numpy()
    out[f"{prefix}_mark_high"] = mark_s.reindex(out["bar_open_ts"])["high"].to_numpy()
    out[f"{prefix}_mark_low"] = mark_s.reindex(out["bar_open_ts"])["low"].to_numpy()
    out[f"{prefix}_mark_close"] = mark_s.reindex(out["bar_open_ts"])["close"].to_numpy()
    idx_s = index.set_index("open_time")
    out[f"{prefix}_index_close"] = idx_s.reindex(out["bar_open_ts"])["close"].to_numpy()
    if prem is not None:
        prem_s = prem.set_index("open_time")
        out[f"{prefix}_premium"] = prem_s.reindex(out["bar_open_ts"])["close"].to_numpy()
    return out


def _download_symbol_days(symbol: str, days: list[date]) -> dict[str, pd.DataFrame]:
    kinds = ("klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines")
    jobs = [(kind, day) for kind in kinds for day in days]
    paths: dict[tuple[str, date], Path] = {}

    def work(job: tuple[str, date]) -> tuple[tuple[str, date], Path | None]:
        kind, day = job
        return job, download_daily_zip(kind, symbol, day)

    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = [pool.submit(work, j) for j in jobs]
        for fut in as_completed(futs):
            job, path = fut.result()
            if path is not None:
                paths[job] = path

    out: dict[str, pd.DataFrame] = {}
    for kind in kinds:
        frames = []
        missing = []
        for day in days:
            p = paths.get((kind, day))
            if p is None:
                missing.append(day)
                continue
            frames.append(_read_kline_zip(p))
        if missing:
            print(f"  {symbol} {kind}: missing {len(missing)} day(s), first={missing[0]}")
        if not frames:
            out[kind] = pd.DataFrame(columns=KLINE_COLS)
        else:
            out[kind] = (
                pd.concat(frames, ignore_index=True)
                .drop_duplicates("open_time")
                .sort_values("open_time")
                .reset_index(drop=True)
            )
    return out


def _download_symbol_range(symbol: str, start: date, end: date) -> dict[str, pd.DataFrame]:
    kinds = ("klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines")
    months, days = _split_monthly_and_daily(start, end)
    print(f"  {symbol}: {len(months)} monthly zip(s), {len(days)} daily zip(s)")
    frames: dict[str, list[pd.DataFrame]] = {k: [] for k in kinds}

    def month_job(job: tuple[str, int, int]) -> tuple[tuple[str, int, int], Path | None]:
        kind, y, m = job
        return job, download_monthly_kline_zip(kind, symbol, y, m)

    month_jobs = [(kind, y, m) for kind in kinds for y, m in months]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(month_job, j) for j in month_jobs]
        for fut in as_completed(futs):
            (kind, y, m), path = fut.result()
            if path is None:
                print(f"  {symbol} monthly {kind} {y}-{m:02d} missing → daily fallback")
                ms = date(y, m, 1)
                for day in _daterange(ms, _month_end(ms)):
                    p = download_daily_zip(kind, symbol, day)
                    if p is not None:
                        frames[kind].append(_read_kline_zip(p))
            else:
                frames[kind].append(_read_kline_zip(path))

    if days:
        daily = _download_symbol_days(symbol, days)
        for kind in kinds:
            if not daily[kind].empty:
                frames[kind].append(daily[kind])

    out: dict[str, pd.DataFrame] = {}
    for kind in kinds:
        if not frames[kind]:
            raise RuntimeError(f"no {kind} data for {symbol}")
        out[kind] = (
            pd.concat(frames[kind], ignore_index=True)
            .drop_duplicates("open_time")
            .sort_values("open_time")
            .reset_index(drop=True)
        )
    return out


def _merge_funding(
    official: pd.DataFrame, reconstructed: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    rec = reconstructed.copy()
    off = official.copy()
    if rec.empty and off.empty:
        raise RuntimeError("no funding series")
    if off.empty:
        merged = rec
        overlap_stats = {"official_rows": 0, "reconstructed_only": int(len(rec))}
    else:
        off_idx = set(off["funding_ts"].astype("int64"))
        rec_extra = rec[~rec["funding_ts"].isin(off_idx)]
        merged = pd.concat(
            [
                off[["funding_ts", "last_funding_rate", "funding_interval_hours", "source"]],
                rec_extra[["funding_ts", "last_funding_rate", "funding_interval_hours", "source"]],
            ],
            ignore_index=True,
        )
        overlap = rec[rec["funding_ts"].isin(off_idx)]
        if not overlap.empty:
            aligned = overlap.merge(
                off[["funding_ts", "last_funding_rate"]],
                on="funding_ts",
                suffixes=("_rec", "_off"),
            )
            err = (aligned["last_funding_rate_rec"] - aligned["last_funding_rate_off"]).abs()
            overlap_stats = {
                "official_rows": int(len(off)),
                "overlap": int(len(aligned)),
                "mae": float(err.mean()) if len(aligned) else None,
                "max_abs_err": float(err.max()) if len(aligned) else None,
            }
        else:
            overlap_stats = {"official_rows": int(len(off)), "overlap": 0}
    merged = merged.drop_duplicates("funding_ts").sort_values("funding_ts")
    return merged, overlap_stats


def _attach_funding(df: pd.DataFrame, funding: pd.DataFrame, prefix: str) -> pd.DataFrame:
    f = funding.sort_values("funding_ts").copy()
    f["next_funding_ts"] = f["funding_ts"]
    # Each bar gets the *next* settlement timestamp (known schedule) and the
    # rate only on the settlement minute itself.
    bars = df["bar_open_ts"].to_numpy()
    f_ts = f["funding_ts"].to_numpy(dtype="int64")
    f_rate = f["last_funding_rate"].to_numpy(dtype="float64")
    nxt = np.full(len(bars), np.int64(-1))
    rate = np.full(len(bars), np.nan)
    j = 0
    for i, ts in enumerate(bars):
        while j < len(f_ts) and f_ts[j] < ts:
            j += 1
        if j < len(f_ts):
            nxt[i] = f_ts[j]
        if j < len(f_ts) and f_ts[j] == ts:
            rate[i] = f_rate[j]
    df[f"{prefix}_funding_rate"] = rate
    df[f"{prefix}_next_funding_ts"] = nxt
    df[f"{prefix}_is_funding"] = np.isfinite(rate)
    return df


def build_aligned_panel(
    start: date,
    end: date,
    warmup_days: int = 5,
) -> tuple[pd.DataFrame, dict]:
    """Complete UTC minute calendar, inner-join semantics without ffill of close."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    fetch_start = start - timedelta(days=warmup_days)
    print(f"Downloading Binance Vision 1m files {fetch_start} → {end}")

    legs = {}
    for symbol in SYMBOLS:
        print(f"  {symbol} ...")
        legs[symbol] = _download_symbol_range(symbol, fetch_start, end)

    btc = _leg_frame(legs["BTCUSDT"], "btc")
    eth = _leg_frame(legs["ETHUSDT"], "eth")

    # Full calendar so missing minutes are visible (not silently dropped).
    t0 = int(
        datetime(fetch_start.year, fetch_start.month, fetch_start.day, tzinfo=timezone.utc).timestamp()
        * 1000
    )
    t1 = int(
        datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp() * 1000
    )
    calendar = pd.DataFrame({"bar_open_ts": np.arange(t0, t1 + 1, 60_000, dtype="int64")})
    panel = calendar.merge(btc, on="bar_open_ts", how="left").merge(
        eth.drop(columns=["bar_close_ts"], errors="ignore"), on="bar_open_ts", how="left"
    )
    if "bar_close_ts" not in panel.columns or panel["bar_close_ts"].isna().all():
        panel["bar_close_ts"] = panel["bar_open_ts"] + 59_999
    else:
        panel["bar_close_ts"] = panel["bar_close_ts"].fillna(panel["bar_open_ts"] + 59_999)

    required = [
        "btc_close",
        "eth_close",
        "btc_mark_close",
        "eth_mark_close",
        "btc_open",
        "eth_open",
        "btc_mark_open",
        "eth_mark_open",
    ]
    incomplete = panel[required].isna().any(axis=1)
    panel["pair_incomplete"] = incomplete.astype("int8")

    # Consecutive hole length (minutes). No close interpolation.
    hole = incomplete.to_numpy()
    gap_len = np.zeros(len(panel), dtype=np.int32)
    run = 0
    for i, h in enumerate(hole):
        if h:
            run += 1
        else:
            run = 0
        gap_len[i] = run
    panel["gap_run"] = gap_len
    panel["data_gap"] = (gap_len > 2).astype("int8")

    months = sorted({(d.year, d.month) for d in _daterange(fetch_start, end)})
    funding_stats = {}
    for symbol, prefix in (("BTCUSDT", "btc"), ("ETHUSDT", "eth")):
        official = load_official_funding(symbol, months)
        prem_col = f"{prefix}_premium"
        prem = panel.loc[~panel[prem_col].isna(), ["bar_open_ts", prem_col]].copy()
        prem_s = prem.set_index(
            pd.to_datetime(prem["bar_open_ts"], unit="ms", utc=True)
        )[prem_col]
        reconstructed = reconstruct_funding_from_premium(prem_s)
        merged, stats = _merge_funding(official, reconstructed)
        funding_stats[symbol] = stats
        panel = _attach_funding(panel, merged, prefix)

    panel["exchange_ts"] = panel["bar_open_ts"]
    panel["bar_open"] = pd.to_datetime(panel["bar_open_ts"], unit="ms", utc=True)

    start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    # Keep warmup rows for rolling windows; caller slices PnL later.
    panel.attrs = {}
    manifest = {
        "venue": VENUE,
        "venue_name": "Binance USDⓈ-M futures",
        "source": "data.binance.vision",
        "symbols": list(SYMBOLS),
        "interval": "1m",
        "fetch_start": fetch_start.isoformat(),
        "pnl_start": start.isoformat(),
        "end": end.isoformat(),
        "rows": int(len(panel)),
        "complete_rows": int((panel["pair_incomplete"] == 0).sum()),
        "incomplete_rows": int((panel["pair_incomplete"] == 1).sum()),
        "funding_stats": funding_stats,
        "fields": {
            "last_ohlcv": "daily/klines",
            "mark_ohlc": "daily/markPriceKlines",
            "index_close": "daily/indexPriceKlines",
            "premium": "daily/premiumIndexKlines",
            "funding_official": "monthly/fundingRate (when published)",
            "funding_fallback": "reconstructed from 1m premium via Binance clamp formula",
        },
        "no_ffill_close": True,
        "join": "full UTC minute calendar + left join both legs; pair_incomplete=1 if either missing",
    }
    panel.attrs["pnl_start_ts"] = start_ts
    return panel, manifest


def save_panel(
    panel: pd.DataFrame,
    manifest: dict,
    cache_dir: Path | None = None,
    name: str = "aligned_1m.parquet",
) -> Path:
    cache_dir = cache_dir or CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / name
    panel.to_parquet(path, index=False)
    man_name = "manifest.json" if name == "aligned_1m.parquet" else Path(name).stem + ".manifest.json"
    man_path = cache_dir / man_name
    payload = dict(manifest)
    payload["parquet"] = path.name
    payload["parquet_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    man_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_panel(
    cache_dir: Path | None = None, name: str = "aligned_1m.parquet"
) -> tuple[pd.DataFrame, dict]:
    cache_dir = cache_dir or CACHE_DIR
    path = cache_dir / name
    candidates = [
        cache_dir / (Path(name).stem + ".manifest.json"),
        cache_dir / "manifest.json",
    ]
    if not path.exists():
        raise FileNotFoundError(path)
    panel = pd.read_parquet(path)
    manifest = {}
    for man_path in candidates:
        if man_path.exists():
            manifest = json.loads(man_path.read_text(encoding="utf-8"))
            break
    return panel, manifest


def resample_panel(panel: pd.DataFrame, minutes: int = 5) -> pd.DataFrame:
    """UTC-aligned OHLC resample of the 1m panel. No close ffill.

    A bucket is incomplete if any 1m child is incomplete or the bucket has
    fewer than ``minutes`` child bars. Funding flags OR onto the bucket that
    contains the settlement minute; the rate is the settlement print (not
    averaged). ``gap_run`` is the max 1m hole length in the bucket so the
    existing 2-minute freeze still means two minutes, not two bars.
    """
    minutes = int(minutes)
    if minutes <= 1:
        return panel.copy().reset_index(drop=True)
    bar_ms = minutes * 60_000
    df = panel.copy()
    ts = df["bar_open_ts"].astype("int64")
    df["_bucket"] = (ts // bar_ms) * bar_ms
    g = df.groupby("_bucket", sort=True)

    out = pd.DataFrame({"bar_open_ts": g.size().index.astype("int64")})
    out["bar_close_ts"] = out["bar_open_ts"] + bar_ms - 1
    out["n_1m"] = g.size().to_numpy()

    for prefix in ("btc", "eth"):
        out[f"{prefix}_open"] = g[f"{prefix}_open"].first().to_numpy()
        out[f"{prefix}_high"] = g[f"{prefix}_high"].max().to_numpy()
        out[f"{prefix}_low"] = g[f"{prefix}_low"].min().to_numpy()
        out[f"{prefix}_close"] = g[f"{prefix}_close"].last().to_numpy()
        out[f"{prefix}_volume"] = g[f"{prefix}_volume"].sum().to_numpy()
        out[f"{prefix}_quote_volume"] = g[f"{prefix}_quote_volume"].sum().to_numpy()
        out[f"{prefix}_n_trades"] = g[f"{prefix}_n_trades"].sum().to_numpy()
        out[f"{prefix}_mark_open"] = g[f"{prefix}_mark_open"].first().to_numpy()
        out[f"{prefix}_mark_high"] = g[f"{prefix}_mark_high"].max().to_numpy()
        out[f"{prefix}_mark_low"] = g[f"{prefix}_mark_low"].min().to_numpy()
        out[f"{prefix}_mark_close"] = g[f"{prefix}_mark_close"].last().to_numpy()
        out[f"{prefix}_index_close"] = g[f"{prefix}_index_close"].last().to_numpy()
        if f"{prefix}_premium" in df.columns:
            out[f"{prefix}_premium"] = g[f"{prefix}_premium"].last().to_numpy()
        out[f"{prefix}_is_funding"] = g[f"{prefix}_is_funding"].any().to_numpy()
        out[f"{prefix}_funding_rate"] = g[f"{prefix}_funding_rate"].max().to_numpy()
        out[f"{prefix}_next_funding_ts"] = g[f"{prefix}_next_funding_ts"].last().to_numpy()

    incomplete = (g["pair_incomplete"].max() > 0).to_numpy() | (out["n_1m"].to_numpy() < minutes)
    out["pair_incomplete"] = incomplete.astype("int8")
    out["gap_run"] = g["gap_run"].max().to_numpy(dtype="int32")
    out["data_gap"] = (out["gap_run"] > 2).astype("int8")
    out["exchange_ts"] = out["bar_open_ts"]
    out["bar_open"] = pd.to_datetime(out["bar_open_ts"], unit="ms", utc=True)
    return out.reset_index(drop=True)
