"""Bar-structure factors from OHLC only."""

from __future__ import annotations

import pandas as pd


def close_location_value(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0.0, pd.NA)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return clv.fillna(0.0).clip(-1.0, 1.0).rename("clv")


def body_ratio(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0.0, pd.NA)
    body = (df["close"] - df["open"]).abs() / rng
    return body.fillna(0.0).clip(0.0, 1.0).rename("body_ratio")
