# -*- coding: utf-8 -*-
"""Universe: S&P 500 ∪ Nasdaq-100."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import List, Set

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; Alpha101Research/1.0)"}


def _get_html(url: str, timeout: int = 60) -> str:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text


def fetch_sp500_tickers() -> List[str]:
    html = _get_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    df = pd.read_html(io.StringIO(html))[0]
    syms = (
        df["Symbol"]
        .astype(str)
        .str.replace(".", "-", regex=False)  # BRK.B -> BRK-B for Yahoo
        .str.strip()
        .tolist()
    )
    return sorted(set(syms))


def fetch_nasdaq100_tickers() -> List[str]:
    html = _get_html("https://stockanalysis.com/list/nasdaq-100-stocks/")
    df = pd.read_html(io.StringIO(html))[0]
    col = "Symbol" if "Symbol" in df.columns else df.columns[1]
    syms = (
        df[col]
        .astype(str)
        .str.replace(".", "-", regex=False)
        .str.strip()
        .tolist()
    )
    return sorted(set(syms))


def fetch_universe(cache_path: Path | str | None = None, refresh: bool = False) -> dict:
    """Return dict with sp500, nasdaq100, union lists (Yahoo-compatible)."""
    cache_path = Path(cache_path) if cache_path else None
    if cache_path and cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    spx = fetch_sp500_tickers()
    ndx = fetch_nasdaq100_tickers()
    union = sorted(set(spx) | set(ndx))
    out = {
        "sp500": spx,
        "nasdaq100": ndx,
        "union": union,
        "n_sp500": len(spx),
        "n_nasdaq100": len(ndx),
        "n_union": len(union),
    }
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
