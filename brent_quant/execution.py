"""Next-bar open fills with tick slippage."""

from __future__ import annotations


def apply_slippage(price: float, is_buy: bool, tick_size: float, ticks: float) -> float:
    adj = tick_size * ticks
    return price + adj if is_buy else price - adj


def open_fill(open_price: float, target_side: float, tick_size: float, ticks: float) -> float:
    """Enter long (target>0) as a buy; enter short as a sell."""
    if target_side == 0:
        return open_price
    return apply_slippage(open_price, is_buy=target_side > 0, tick_size=tick_size, ticks=ticks)


def close_fill(price: float, current_side: float, tick_size: float, ticks: float) -> float:
    """Exit long as a sell; exit short as a buy."""
    if current_side == 0:
        return price
    return apply_slippage(price, is_buy=current_side < 0, tick_size=tick_size, ticks=ticks)
