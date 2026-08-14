"""Market quote providers: akshare (primary) + optional CTP.

Provides two numbers per instrument:
1. prev_close — **日盘 15:00 收盘价** used as 昨收 for live float PnL
   (night session after 21:00 → same calendar day's afternoon close)
2. last — latest price; if outside trading hours, equals session close
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

from core.session_calendar import (
    is_night_clock,
    most_recent_day_close_date,
    price_basis_note,
    product_has_night_session,
)
from core.settlement_parser import parse_option_symbol, product_code_from_underlying

SH_TZ = ZoneInfo("Asia/Shanghai")

# Sina / akshare Chinese product names for futures_zh_realtime
PRODUCT_SINA_NAME: dict[str, str] = {
    "V": "PVC",
    "JD": "鸡蛋",
    "EG": "乙二醇",
    "AP": "鲜苹果",
    "M": "豆粕",
    "C": "玉米",
    "I": "铁矿石",
    "L": "塑料",
    "PP": "PP",
    "P": "棕榈",
    "Y": "豆油",
    "A": "豆一",
    "LH": "生猪",
    "PG": "液化石油气",
    "EB": "苯乙烯",
    "SR": "白糖",
    "CF": "棉花",
    "TA": "PTA",
    "MA": "甲醇",
    "RM": "菜粕",
    "OI": "菜油",
    "FG": "玻璃",
    "SA": "纯碱",
    "UR": "尿素",
    "AU": "黄金",
    "AG": "白银",
    "CU": "沪铜",
    "RB": "螺纹钢",
    "RU": "橡胶",
    "SC": "原油",
    "LC": "碳酸锂",
    "SI": "工业硅",
}

# Sina option product display names (subset supported by option_commodity_*_sina)
PRODUCT_OPTION_SINA: dict[str, str] = {
    "EG": "乙二醇期权",
    "M": "豆粕期权",
    "C": "玉米期权",
    "I": "铁矿石期权",
    "PG": "液化石油气期权",
    "CF": "棉花期权",
    "SR": "白糖期权",
    "TA": "PTA期权",
    "MA": "甲醇期权",
    "RM": "菜籽粕期权",
    "OI": "菜籽油期权",
    "RU": "橡胶期权",
    "CU": "沪铜期权",
    "AU": "黄金期权",
    "AP": "苹果期权",  # exchange hist; sina table may be limited
    "JD": "鸡蛋期权",
    "V": "聚氯乙烯期权",
}


@dataclass
class InstrumentQuote:
    symbol: str  # broker / book symbol
    kind: str  # underlying | option
    prev_close: Optional[float]
    last: Optional[float]
    asof: str
    in_session: bool
    source: str
    sina_symbol: str = ""
    last_bar_date: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_sina_futures_symbol(underlying: str) -> str:
    """Map broker underlying to Sina futures code (CZCE 3-digit → 4-digit)."""
    u = str(underlying).strip().upper()
    m = re.match(r"^([A-Z]+)(\d{3})$", u)
    if m:
        prod, ymm = m.group(1), m.group(2)
        y, mm = int(ymm[0]), int(ymm[1:])
        if 1 <= mm <= 12:
            yy = (2020 + y) % 100
            return f"{prod}{yy:02d}{mm:02d}"
    m4 = re.match(r"^([A-Z]+)(\d{4})$", u)
    if m4:
        return u
    return u


def to_sina_option_symbol(symbol: str) -> str:
    """EG2610-C-5100 / AP610C8200 → eg2610C5100 / ap2610C8200."""
    info = parse_option_symbol(symbol)
    und = to_sina_futures_symbol(info["underlying"]).lower()
    cp = "C" if info["option_type"] == "CALL" else "P"
    strike = info["strike"]
    if float(strike).is_integer():
        k = str(int(strike))
    else:
        k = str(strike).rstrip("0").rstrip(".")
    return f"{und}{cp}{k}"


def is_cn_futures_trading_session(now: Optional[datetime] = None) -> bool:
    """
    Simplified CN commodity session check (Asia/Shanghai):
      day  08:55–11:30, 13:25–15:15
      night 20:55–02:35 (next calendar day)
    Weekends = closed. Does not model product-specific night hours / holidays.
    """
    now = now or datetime.now(SH_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SH_TZ)
    else:
        now = now.astimezone(SH_TZ)
    if now.weekday() >= 5:
        # Saturday early morning may still be Friday night session
        if now.weekday() == 5 and now.time() <= time(2, 35):
            pass
        else:
            return False
    t = now.time()
    if time(8, 55) <= t <= time(11, 30):
        return True
    if time(13, 25) <= t <= time(15, 15):
        return True
    if t >= time(20, 55) or t <= time(2, 35):
        # Sunday night doesn't trade
        if now.weekday() == 6:
            return False
        if now.weekday() == 0 and t <= time(2, 35):
            return False
        return True
    return False


class QuoteProvider(Protocol):
    name: str

    def fetch_underlying(self, underlying: str, asof: date) -> InstrumentQuote: ...

    def fetch_option(self, symbol: str, asof: date) -> InstrumentQuote: ...


def _parse_asof(asof: Optional[str | date]) -> date:
    if asof is None:
        return datetime.now(SH_TZ).date()
    if isinstance(asof, date) and not isinstance(asof, datetime):
        return asof
    return datetime.strptime(str(asof)[:10], "%Y-%m-%d").date()


class AkshareQuoteProvider:
    """AkShare / Sina based quotes."""

    name = "akshare"

    def fetch_underlying(self, underlying: str, asof: date) -> InstrumentQuote:
        import akshare as ak

        sina = to_sina_futures_symbol(underlying)
        in_sess = is_cn_futures_trading_session()
        prev_close: Optional[float] = None
        last: Optional[float] = None
        last_bar_date = ""
        note = ""

        df = ak.futures_zh_daily_sina(symbol=sina)
        if df is None or df.empty:
            raise RuntimeError(f"akshare daily empty for {sina}")
        df = df.copy()
        df["date"] = df["date"].astype(str).str[:10]
        df = df[df["date"] <= asof.isoformat()].reset_index(drop=True)
        if df.empty:
            raise RuntimeError(f"no daily bars <= {asof} for {sina}")

        last_row = df.iloc[-1]
        last_bar_date = str(last_row["date"])
        last_close = float(last_row["close"])
        close_ref_d = most_recent_day_close_date()
        night = is_night_clock()
        prod = product_code_from_underlying(underlying).upper()

        if night and last_bar_date == close_ref_d.isoformat():
            prev_close = last_close
            last = last_close
            note = f"night_prev=day_close:{last_bar_date}"
        elif last_bar_date == asof.isoformat() and len(df) >= 2 and not night:
            prev_close = float(df.iloc[-2]["close"])
            last = last_close
        elif last_bar_date == close_ref_d.isoformat():
            prev_close = last_close
            last = last_close
            note = f"prev=day_close:{last_bar_date}"
        elif last_bar_date == asof.isoformat():
            prev_close = last_close
            last = last_close
            note = "only one bar; prev_close=last_close"
        else:
            prev_close = last_close
            last = last_close
            note = f"no bar on {asof}; using {last_bar_date} close"

        # In session: try realtime last
        today = datetime.now(SH_TZ).date()
        if in_sess and (asof == today or night):
            rt = self._realtime_last(underlying, sina)
            if rt is not None:
                last = rt
                note = (note + "; realtime").strip("; ")
        elif not in_sess:
            last = last_close
            note = (note + "; off-session last=session_close").strip("; ")

        if night and not product_has_night_session(prod):
            last = None
            note = (note + "; no_night_product skip_last").strip("; ")

        today = datetime.now(SH_TZ).date()
        if (
            in_sess
            and not night
            and last is not None
            and prev_close is not None
            and abs(float(last) - float(prev_close)) < 1e-9
            and "realtime" not in note
            and last_bar_date != today.isoformat()
        ):
            last = None
            note = (note + "; suppress_stale_last").strip("; ")

        return InstrumentQuote(
            symbol=underlying,
            kind="underlying",
            prev_close=prev_close,
            last=last,
            asof=asof.isoformat(),
            in_session=in_sess,
            source=self.name,
            sina_symbol=sina,
            last_bar_date=last_bar_date,
            note=note,
        )

    def _realtime_last(self, underlying: str, sina: str) -> Optional[float]:
        import akshare as ak

        prod = product_code_from_underlying(underlying).upper()
        name = PRODUCT_SINA_NAME.get(prod)
        if not name:
            return None
        try:
            df = ak.futures_zh_realtime(symbol=name)
        except Exception:
            return None
        if df is None or df.empty:
            return None
        # match contract code
        for col in ("symbol", "name"):
            if col not in df.columns:
                continue
        # prefer exact sina code
        for _, row in df.iterrows():
            sym = str(row.get("symbol", "")).upper()
            if sym == sina.upper():
                for k in ("trade", "close", "current_price"):
                    if k in row and row[k] not in (None, ""):
                        try:
                            return float(row[k])
                        except (TypeError, ValueError):
                            pass
        return None

    def fetch_option(self, symbol: str, asof: date) -> InstrumentQuote:
        import akshare as ak

        sina = to_sina_option_symbol(symbol)
        in_sess = is_cn_futures_trading_session()
        note = ""
        last_bar_date = ""
        prev_close: Optional[float] = None
        last: Optional[float] = None

        # 1) CZCE exchange daily (苹果等) when available
        info = parse_option_symbol(symbol)
        prod = product_code_from_underlying(info["underlying"]).upper()
        if prod == "AP":
            try:
                q = self._option_from_czce_hist(symbol, asof)
                if q is not None:
                    return q
            except Exception as exc:  # noqa: BLE001
                note = f"czce_hist_fail:{exc}"

        # 2) Sina option daily hist
        df = ak.option_commodity_hist_sina(symbol=sina)
        if df is None or df.empty:
            raise RuntimeError(f"akshare option hist empty for {sina}")
        df = df.copy()
        df["date"] = df["date"].astype(str).str[:10]
        df = df[df["date"] <= asof.isoformat()].reset_index(drop=True)
        if df.empty:
            raise RuntimeError(f"no option bars <= {asof} for {sina}")

        last_row = df.iloc[-1]
        last_bar_date = str(last_row["date"])
        last_close = float(last_row["close"])
        close_ref_d = most_recent_day_close_date()
        night = is_night_clock()

        # --- 昨收 = 最近一次日盘 15:00 收盘价 ---
        # 夜盘 21:00 后：当天下午收盘价（不是结算价，也不是再往前一天）
        if night and last_bar_date == close_ref_d.isoformat():
            prev_close = last_close
            last = last_close
            note = (note + f"; night_prev=day_close:{last_bar_date}").strip("; ")
        elif last_bar_date == asof.isoformat() and len(df) >= 2 and not night:
            # 日盘：昨收 = 上一交易日收盘
            prev_close = float(df.iloc[-2]["close"])
            last = last_close
        elif last_bar_date == close_ref_d.isoformat():
            prev_close = last_close
            last = last_close
            note = (note + f"; prev=day_close:{last_bar_date}").strip("; ")
        elif last_bar_date == asof.isoformat():
            prev_close = last_close
            last = last_close
        else:
            # asof 尚无日 K：用最新已收盘日作为昨收；最新价待 RT
            prev_close = last_close
            last = last_close
            note = (note + f"; no bar on {asof}; bar={last_bar_date}").strip("; ")

        # try sina board last while in session
        today = datetime.now(SH_TZ).date()
        if in_sess and (asof == today or night):
            rt = self._option_board_last(info, sina)
            if rt is not None:
                last = rt
                note = (note + "; board_rt").strip("; ")
        elif not in_sess:
            last = last_close
            note = (note + "; off-session last=session_close").strip("; ")

        # 无夜盘品种在夜盘时段：不提供 last（调用方不写入 marks）
        if night and not product_has_night_session(prod):
            last = None
            note = (note + "; no_night_product skip_last").strip("; ")

        # 日盘交易中若无 RT、且 last 仍是昨收日盘价 → 不把陈旧收盘当「最新价」
        today = datetime.now(SH_TZ).date()
        if (
            in_sess
            and not night
            and last is not None
            and prev_close is not None
            and abs(float(last) - float(prev_close)) < 1e-9
            and "board_rt" not in note
            and last_bar_date != today.isoformat()
        ):
            last = None
            note = (note + "; suppress_stale_last").strip("; ")

        return InstrumentQuote(
            symbol=symbol,
            kind="option",
            prev_close=prev_close,
            last=last,
            asof=asof.isoformat(),
            in_session=in_sess,
            source=self.name,
            sina_symbol=sina,
            last_bar_date=last_bar_date,
            note=note,
        )

    def _option_from_czce_hist(self, symbol: str, asof: date) -> Optional[InstrumentQuote]:
        import akshare as ak

        day = asof.strftime("%Y%m%d")
        df = ak.option_hist_czce(symbol="苹果期权", trade_date=day)
        if df is None or df.empty:
            # try previous calendar days up to 5
            for i in range(1, 6):
                d2 = asof - timedelta(days=i)
                df = ak.option_hist_czce(symbol="苹果期权", trade_date=d2.strftime("%Y%m%d"))
                if df is not None and not df.empty:
                    asof = d2
                    day = d2.strftime("%Y%m%d")
                    break
        if df is None or df.empty:
            return None
        code_col = "合约代码" if "合约代码" in df.columns else df.columns[0]
        row = df[df[code_col].astype(str).str.upper() == symbol.strip().upper()]
        if row.empty:
            # try expanded sina-less broker code
            return None
        r = row.iloc[0]
        prev = float(r["昨结算"]) if "昨结算" in r and r["昨结算"] == r["昨结算"] else None
        # Prefer 今收盘 as last; if missing use 今结算
        last = None
        for k in ("今收盘", "今结算"):
            if k in r and r[k] == r[k]:
                last = float(r[k])
                break
        in_sess = is_cn_futures_trading_session()
        # For CZCE sheet on asof: 昨结算 ≈ prev settle; user asked 收盘价 —
        # also try previous day's 今收盘 via day-1 file when possible.
        prev_close = prev
        try:
            prev_day = asof - timedelta(days=1)
            for i in range(0, 5):
                d2 = prev_day - timedelta(days=i)
                dfp = ak.option_hist_czce(symbol="苹果期权", trade_date=d2.strftime("%Y%m%d"))
                if dfp is None or dfp.empty:
                    continue
                rp = dfp[dfp[code_col].astype(str).str.upper() == symbol.strip().upper()]
                if rp.empty:
                    continue
                if "今收盘" in rp.columns and rp.iloc[0]["今收盘"] == rp.iloc[0]["今收盘"]:
                    prev_close = float(rp.iloc[0]["今收盘"])
                break
        except Exception:
            pass

        if not in_sess and last is not None:
            # keep exchange 今收盘 as last when sheet is for asof
            note = "czce_option_hist; off-session"
        elif not in_sess and prev_close is not None:
            last = prev_close
            note = "czce_option_hist; off-session fallback prev_close"

        return InstrumentQuote(
            symbol=symbol,
            kind="option",
            prev_close=prev_close,
            last=last,
            asof=asof.isoformat(),
            in_session=in_sess,
            source=self.name,
            sina_symbol=symbol,
            last_bar_date=day,
            note="czce_option_hist",
        )

    def _option_board_last(self, info: dict[str, Any], sina: str) -> Optional[float]:
        import akshare as ak

        prod = product_code_from_underlying(info["underlying"]).upper()
        opt_name = PRODUCT_OPTION_SINA.get(prod)
        if not opt_name:
            return None
        contract = to_sina_futures_symbol(info["underlying"]).lower()
        try:
            df = ak.option_commodity_contract_table_sina(symbol=opt_name, contract=contract)
        except Exception:
            return None
        if df is None or df.empty:
            return None
        # find strike row
        strike = float(info["strike"])
        if "行权价" not in df.columns:
            return None
        row = df[df["行权价"].astype(float) == strike]
        if row.empty:
            return None
        r = row.iloc[0]
        if info["option_type"] == "CALL":
            col = "看涨合约-最新价"
        else:
            col = "看跌合约-最新价"
        if col in r and r[col] == r[col]:
            try:
                v = float(r[col])
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None
        return None


class CtpQuoteProvider:
    """
    Optional CTP quote provider.

    Requires env CTP_MD_FRONT / CTP_USER / CTP_PASSWORD / CTP_BROKER and a
    installed CTP market-data bridge. If unavailable, raises and caller falls
    back to akshare.
    """

    name = "ctp"

    def __init__(self) -> None:
        self._ready = bool(os.getenv("CTP_MD_FRONT") and os.getenv("CTP_USER"))

    @property
    def available(self) -> bool:
        return self._ready

    def fetch_underlying(self, underlying: str, asof: date) -> InstrumentQuote:
        raise RuntimeError(
            "CTP quote bridge not configured. Set CTP_MD_FRONT/CTP_USER/CTP_PASSWORD "
            "or use provider=akshare."
        )

    def fetch_option(self, symbol: str, asof: date) -> InstrumentQuote:
        raise RuntimeError("CTP quote bridge not configured.")


def get_quote_provider(prefer: str = "akshare") -> QuoteProvider:
    prefer = (prefer or "akshare").lower()
    if prefer == "ctp":
        ctp = CtpQuoteProvider()
        if ctp.available:
            return ctp  # type: ignore[return-value]
        # fall through
    return AkshareQuoteProvider()


def fetch_book_quotes(
    *,
    underlyings: list[str],
    option_symbols: list[str],
    asof: Optional[str | date] = None,
    provider: str = "akshare",
) -> dict[str, Any]:
    """Fetch prev_close (日盘收盘) + last for underlyings and options."""
    asof_d = _parse_asof(asof)
    prefer = (provider or os.getenv("QUOTE_PROVIDER") or "akshare").lower()
    prov = get_quote_provider(prefer)
    in_sess = is_cn_futures_trading_session()
    night = is_night_clock()
    results: list[InstrumentQuote] = []
    errors: list[dict[str, str]] = []

    for u in underlyings:
        try:
            results.append(prov.fetch_underlying(u, asof_d))
        except Exception as exc:  # noqa: BLE001
            errors.append({"symbol": u, "kind": "underlying", "error": str(exc)})

    for sym in option_symbols:
        try:
            results.append(prov.fetch_option(sym, asof_d))
        except Exception as exc:  # noqa: BLE001
            errors.append({"symbol": sym, "kind": "option", "error": str(exc)})

    # Build marks payload for settlement sync
    marks: dict[str, float] = {}
    skip_live: list[str] = []
    for q in results:
        if q.kind == "underlying":
            if q.last is not None and float(q.last) > 0:
                marks[f"__F__:{q.symbol}"] = float(q.last)
            if q.prev_close is not None:
                marks[f"__F_CLOSE__:{q.symbol}"] = float(q.prev_close)
        else:
            if q.prev_close is not None:
                marks[f"__PREV_CLOSE__:{q.symbol}"] = float(q.prev_close)
                marks[f"__CLOSE__:{q.symbol}"] = float(q.prev_close)
            if q.last is not None and float(q.last) > 0:
                marks[q.symbol] = float(q.last)
            else:
                skip_live.append(q.symbol)

    return {
        "provider": getattr(prov, "name", prefer),
        "asof": asof_d.isoformat(),
        "in_session": in_sess,
        "night_session": night,
        "day_close_ref": most_recent_day_close_date().isoformat(),
        "price_basis": price_basis_note(),
        "quotes": [q.to_dict() for q in results],
        "marks": marks,
        "clear_live_symbols": skip_live,
        "errors": errors,
        "rules": {
            "prev_close": "日盘15:00收盘价；夜盘21:00后=当天下午收盘价（非结算价）",
            "last": "交易时段实时最新价；无夜盘品种夜盘不写最新价（浮动=0）",
        },
    }
