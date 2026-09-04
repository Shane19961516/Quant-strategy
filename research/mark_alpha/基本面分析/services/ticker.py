"""Resolve user stock codes into Yahoo / display symbols."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass
class ResolvedTicker:
    input: str
    yahoo: str
    display: str
    market: str
    currency: str
    name_hint: str

    def to_dict(self) -> dict:
        return asdict(self)


_US = re.compile(r"^[A-Za-z]{1,5}(\.[A-Za-z]{1,2})?$")
_CN_A = re.compile(r"^\d{6}$")
_HK = re.compile(r"^\d{1,5}$")


def resolve_ticker(raw: str) -> ResolvedTicker:
    text = (raw or "").strip().upper().replace(" ", "")
    if not text:
        raise ValueError("请输入股票代码，例如 TSLA 或 688008")

    # Bloomberg-like: 688008 CH Equity / TSLA US Equity
    m = re.match(r"^([A-Z0-9]+)[\s/_-]*(CH|US|HK)?[\s/_-]*(EQUITY)?$", text)
    if m and (m.group(2) or m.group(3)):
        code, region = m.group(1), m.group(2)
        if region == "CH" and code.isdigit() and len(code) == 6:
            return resolve_ticker(code)
        if region == "HK" and code.isdigit():
            code4 = code.zfill(4)
            return ResolvedTicker(text, f"{code4}.HK", f"{code4}.HK", "HK", "HKD", "港交所")
        if region == "US":
            return ResolvedTicker(text, code, code, "US", "USD", "美股")
        text = code

    # Already Yahoo-style with exchange suffix
    if "." in text:
        suffix = text.rsplit(".", 1)[-1]
        if suffix == "SS":
            return ResolvedTicker(text, text, text, "CN", "CNY", "上交所 A股")
        if suffix == "SZ":
            return ResolvedTicker(text, text, text, "CN", "CNY", "深交所 A股")
        if suffix == "HK":
            return ResolvedTicker(text, text, text, "HK", "HKD", "港交所")
        return ResolvedTicker(text, text, text, "US", "USD", "国际")

    # China A-share 6 digits
    if _CN_A.match(text):
        if text.startswith(("5", "6", "9")):
            yahoo = f"{text}.SS"
            hint = "上交所 A股"
        else:
            yahoo = f"{text}.SZ"
            hint = "深交所 A股"
        return ResolvedTicker(text, yahoo, text, "CN", "CNY", hint)

    # Hong Kong numeric codes (1-5 digits)
    if _HK.match(text):
        code4 = text.zfill(4)
        return ResolvedTicker(text, f"{code4}.HK", f"{code4}.HK", "HK", "HKD", "港交所")

    # US / global alphabetic tickers
    if _US.match(text):
        return ResolvedTicker(text, text, text, "US", "USD", "美股")

    return ResolvedTicker(text, text, text, "US", "USD", "自动识别")
