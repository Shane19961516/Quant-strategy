"""IC / Rank-IC and quintile tests for volume-price factors."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


HORIZONS = (1, 3, 6, 12)


def _rank_ic(x: pd.Series, y: pd.Series) -> float:
    a = x.rank()
    b = y.rank()
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(a.corr(b))


def factor_forward_stats(df: pd.DataFrame, factor: str) -> dict[str, Any]:
    if factor not in df.columns:
        return {"factor": factor, "error": "missing"}
    close = df["close"]
    out: dict[str, Any] = {"factor": factor}
    for h in HORIZONS:
        fwd = close.shift(-h) / close - 1.0
        pair = pd.concat([df[factor], fwd], axis=1, keys=["f", "r"]).dropna()
        if len(pair) < 50:
            out[f"h{h}"] = {}
            continue
        ic = float(pair["f"].corr(pair["r"]))
        ric = _rank_ic(pair["f"], pair["r"])
        hit = float(((pair["f"] - pair["f"].median()) * pair["r"] > 0).mean())
        out[f"h{h}"] = {
            "ic": ic,
            "rank_ic": ric,
            "hit_rate": hit,
            "mean_future_return": float(pair["r"].mean()),
            "n": int(len(pair)),
        }
        try:
            q = pd.qcut(pair["f"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
            qmean = pair.groupby(q, observed=False)["r"].mean()
            out[f"h{h}"]["quintiles"] = {str(k): float(v) for k, v in qmean.items()}
        except ValueError:
            out[f"h{h}"]["quintiles"] = {}
    return out


def run_factor_analysis(df: pd.DataFrame) -> dict[str, Any]:
    factors = [c for c in ("volume_ratio", "volume_z", "volume_momentum", "er", "clv", "body_ratio", "score") if c in df.columns]
    return {name: factor_forward_stats(df, name) for name in factors}
