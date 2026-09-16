"""Contract roll helpers for Phase-2 formal backtests.

Phase 1 uses Yahoo ``BZ=F`` as a research continuous series. Volume is never
back-adjusted. Price can be difference- or ratio-adjusted.
"""

from __future__ import annotations

from typing import Mapping

import pandas as pd


def difference_adjust(old_price: float, new_price: float) -> float:
    return float(new_price - old_price)


def ratio_adjust(old_price: float, new_price: float) -> float:
    if old_price == 0:
        return 1.0
    return float(new_price / old_price)


def apply_difference_to_history(ohlc: pd.DataFrame, adjustment: float) -> pd.DataFrame:
    out = ohlc.copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] + adjustment
    return out


def apply_ratio_to_history(ohlc: pd.DataFrame, ratio: float) -> pd.DataFrame:
    out = ohlc.copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] * ratio
    return out


def volume_roll_index(
    front: pd.Series,
    next_contract: pd.Series,
    confirm_bars: int = 3,
) -> pd.DatetimeIndex:
    """Roll when next volume exceeds front volume for ``confirm_bars`` consecutive bars."""
    aligned = pd.concat({"front": front, "next": next_contract}, axis=1).dropna()
    beat = aligned["next"] > aligned["front"]
    streak = beat.groupby((~beat).cumsum()).cumsum()
    roll = streak >= confirm_bars
    return aligned.index[roll]


def build_volume_continuous(
    contracts: Mapping[str, pd.DataFrame],
    order: list[str],
    confirm_bars: int = 3,
    method: str = "difference",
) -> pd.DataFrame:
    """Stitch monthly contracts with a volume roll. Volume stays raw."""
    if not order:
        raise ValueError("contract order is empty")
    current_name = order[0]
    current = contracts[current_name].copy()
    pieces: list[pd.DataFrame] = []
    adjustment = 0.0
    ratio = 1.0

    for nxt_name in order[1:]:
        nxt = contracts[nxt_name]
        if "volume" not in current.columns or "volume" not in nxt.columns:
            raise ValueError("volume column required for volume roll")
        rolls = volume_roll_index(current["volume"], nxt["volume"], confirm_bars=confirm_bars)
        if len(rolls) == 0:
            continue
        roll_ts = rolls[0]
        left = current.loc[current.index < roll_ts].copy()
        overlap = current.loc[current.index == roll_ts]
        right_px = float(nxt.loc[nxt.index >= roll_ts, "close"].iloc[0]) if (nxt.index >= roll_ts).any() else float("nan")
        left_px = float(overlap["close"].iloc[0]) if len(overlap) else float(left["close"].iloc[-1])
        if method == "ratio":
            ratio *= ratio_adjust(left_px, right_px)
            left = apply_ratio_to_history(left, ratio_adjust(left_px, right_px))
        else:
            adj = difference_adjust(left_px, right_px)
            adjustment += adj
            left = apply_difference_to_history(left, adj)
        pieces.append(left)
        current = nxt.loc[nxt.index >= roll_ts].copy()
        current_name = nxt_name

    pieces.append(current)
    out = pd.concat(pieces).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.attrs["roll_method"] = method
    out.attrs["price_adjustment_last"] = adjustment if method != "ratio" else ratio
    return out
