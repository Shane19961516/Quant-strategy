"""Fetch quotes, OHLCV and key fundamentals (Yahoo + CN fallbacks)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if extra:
        h.update(extra)
    return h


def _get_bytes(url: str, timeout: int = 25, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or _headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"行情接口失败 HTTP {exc.code}: {url}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"行情接口不可用: {exc}") from exc


def _get_json(url: str, timeout: int = 25, headers: dict[str, str] | None = None) -> dict[str, Any]:
    raw = _get_bytes(url, timeout=timeout, headers=headers)
    return json.loads(raw.decode("utf-8"))


def _range_to_datalen(range_: str) -> int:
    return {
        "1mo": 30,
        "3mo": 70,
        "6mo": 140,
        "1y": 260,
        "2y": 520,
        "5y": 1300,
        "max": 2000,
    }.get(range_, 260)


def _cn_prefix_code(yahoo_symbol: str) -> tuple[str, str] | None:
    """Return (sina_prefix, code6) for A-shares."""
    m = re.match(r"^(\d{6})\.(SS|SZ)$", yahoo_symbol.upper())
    if not m:
        return None
    code, exch = m.group(1), m.group(2)
    return ("sh" if exch == "SS" else "sz"), code


def fetch_chart_sina_cn(yahoo_symbol: str, range_: str = "1y") -> dict[str, Any]:
    parsed = _cn_prefix_code(yahoo_symbol)
    if not parsed:
        raise RuntimeError(f"非 A 股代码，无法用新浪 K 线: {yahoo_symbol}")
    prefix, code = parsed
    datalen = _range_to_datalen(range_)
    qs = urllib.parse.urlencode(
        {
            "symbol": f"{prefix}{code}",
            "scale": "240",
            "ma": "no",
            "datalen": str(datalen),
        }
    )
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?{qs}"
    )
    raw = _get_bytes(
        url,
        headers=_headers({"Referer": "https://finance.sina.com.cn/"}),
    )
    text = raw.decode("utf-8", errors="replace").strip()
    if not text or text == "null":
        raise RuntimeError(f"新浪无 K 线数据: {yahoo_symbol}")
    rows = json.loads(text)
    candles = []
    for row in rows or []:
        try:
            candles.append(
                {
                    "time": str(row["day"])[:10],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(float(row.get("volume") or 0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not candles:
        raise RuntimeError(f"新浪 K 线解析为空: {yahoo_symbol}")
    last = candles[-1]
    meta = {
        "symbol": yahoo_symbol,
        "currency": "CNY",
        "regularMarketPrice": last["close"],
        "exchangeName": "SSE" if prefix == "sh" else "SZSE",
        "dataSource": "sina",
    }
    return {"meta": meta, "candles": candles}


def fetch_chart_eastmoney_cn(yahoo_symbol: str, range_: str = "1y") -> dict[str, Any]:
    parsed = _cn_prefix_code(yahoo_symbol)
    if not parsed:
        raise RuntimeError(f"非 A 股代码: {yahoo_symbol}")
    prefix, code = parsed
    market = "1" if prefix == "sh" else "0"
    lmt = _range_to_datalen(range_)
    qs = urllib.parse.urlencode(
        {
            "secid": f"{market}.{code}",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "end": "20500101",
            "lmt": str(lmt),
        }
    )
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{qs}"
    data = _get_json(
        url,
        headers=_headers({"Referer": "https://finance.eastmoney.com/"}),
    )
    klines = ((data.get("data") or {}).get("klines")) or []
    name = (data.get("data") or {}).get("name")
    candles = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        candles.append(
            {
                "time": parts[0][:10],
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
                "volume": int(float(parts[5] or 0)),
            }
        )
    if not candles:
        raise RuntimeError(f"东财无 K 线数据: {yahoo_symbol}")
    last = candles[-1]
    meta = {
        "symbol": yahoo_symbol,
        "shortName": name,
        "currency": "CNY",
        "regularMarketPrice": last["close"],
        "dataSource": "eastmoney",
    }
    return {"meta": meta, "candles": candles}


def fetch_chart_yahoo(yahoo_symbol: str, range_: str = "1y", interval: str = "1d") -> dict[str, Any]:
    qs = urllib.parse.urlencode({"range": range_, "interval": interval})
    errors: list[str] = []
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol)}?{qs}"
        try:
            data = _get_json(
                url,
                headers=_headers({"Referer": "https://finance.yahoo.com/"}),
            )
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        result = (data.get("chart") or {}).get("result")
        if not result:
            err = (data.get("chart") or {}).get("error") or {}
            errors.append(err.get("description") or f"Yahoo 空结果 {yahoo_symbol}")
            continue
        r0 = result[0]
        meta = r0.get("meta") or {}
        meta["dataSource"] = "yahoo"
        ts = r0.get("timestamp") or []
        quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs, lows, closes, volumes = (
            quote.get("open") or [],
            quote.get("high") or [],
            quote.get("low") or [],
            quote.get("close") or [],
            quote.get("volume") or [],
        )
        candles = []
        for i, t in enumerate(ts):
            o, h, l, c = (
                opens[i] if i < len(opens) else None,
                highs[i] if i < len(highs) else None,
                lows[i] if i < len(lows) else None,
                closes[i] if i < len(closes) else None,
            )
            if None in (o, h, l, c):
                continue
            candles.append(
                {
                    "time": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": int(volumes[i] or 0) if i < len(volumes) else 0,
                }
            )
        if candles:
            return {"meta": meta, "candles": candles}
        errors.append("Yahoo K 线为空")
    raise RuntimeError("；".join(errors) or f"Yahoo 无数据: {yahoo_symbol}")


def _trim_candles(candles: list[dict[str, Any]], range_: str) -> list[dict[str, Any]]:
    n = _range_to_datalen(range_)
    if len(candles) <= n:
        return candles
    return candles[-n:]


def fetch_chart_sina_us(yahoo_symbol: str, range_: str = "1y") -> dict[str, Any]:
    sym = yahoo_symbol.split(".")[0].lower()
    url = f"https://stock.finance.sina.com.cn/usstock/api/json.php/US_MinKService.getDailyK?symbol={sym}"
    raw = _get_bytes(url, headers=_headers({"Referer": "https://stock.finance.sina.com.cn/"}))
    rows = json.loads(raw.decode("utf-8", errors="replace"))
    candles = []
    for row in rows or []:
        try:
            candles.append(
                {
                    "time": str(row["d"])[:10],
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": int(float(row.get("v") or 0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    candles = _trim_candles(candles, range_)
    if not candles:
        raise RuntimeError(f"新浪美股无 K 线: {yahoo_symbol}")
    last = candles[-1]
    return {
        "meta": {
            "symbol": yahoo_symbol,
            "currency": "USD",
            "regularMarketPrice": last["close"],
            "dataSource": "sina_us",
        },
        "candles": candles,
    }


def fetch_chart_tencent_hk(yahoo_symbol: str, range_: str = "1y") -> dict[str, Any]:
    code = yahoo_symbol.upper().replace(".HK", "")
    if not code.isdigit():
        raise RuntimeError(f"非港股数字代码: {yahoo_symbol}")
    code5 = code.zfill(5)
    n = _range_to_datalen(range_)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk{code5},day,,,{n},qfq"
    data = _get_json(url, headers=_headers({"Referer": "https://finance.qq.com/"}))
    node = ((data.get("data") or {}).get(f"hk{code5}")) or {}
    rows = node.get("qfqday") or node.get("day") or []
    candles = []
    for row in rows:
        try:
            candles.append(
                {
                    "time": str(row[0])[:10],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": int(float(row[5] or 0)),
                }
            )
        except (IndexError, TypeError, ValueError):
            continue
    if not candles:
        raise RuntimeError(f"腾讯港股无 K 线: {yahoo_symbol}")
    last = candles[-1]
    return {
        "meta": {
            "symbol": yahoo_symbol,
            "currency": "HKD",
            "regularMarketPrice": last["close"],
            "dataSource": "tencent_hk",
        },
        "candles": candles,
    }


def fetch_chart(yahoo_symbol: str, range_: str = "1y", interval: str = "1d") -> dict[str, Any]:
    """Multi-source chart with market-aware fallbacks."""
    errors: list[str] = []
    is_cn = bool(_cn_prefix_code(yahoo_symbol))
    is_hk = yahoo_symbol.upper().endswith(".HK")
    is_us = (not is_cn) and (not is_hk)
    sources: list[tuple[str, Any]] = []
    if is_cn:
        sources = [
            ("sina", lambda: fetch_chart_sina_cn(yahoo_symbol, range_=range_)),
            ("eastmoney", lambda: fetch_chart_eastmoney_cn(yahoo_symbol, range_=range_)),
            ("yahoo", lambda: fetch_chart_yahoo(yahoo_symbol, range_=range_, interval=interval)),
        ]
    elif is_hk:
        sources = [
            ("tencent_hk", lambda: fetch_chart_tencent_hk(yahoo_symbol, range_=range_)),
            ("yahoo", lambda: fetch_chart_yahoo(yahoo_symbol, range_=range_, interval=interval)),
        ]
    else:
        sources = [
            ("yahoo", lambda: fetch_chart_yahoo(yahoo_symbol, range_=range_, interval=interval)),
            ("sina_us", lambda: fetch_chart_sina_us(yahoo_symbol, range_=range_)),
        ]

    for name, fn in sources:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            continue
    raise RuntimeError("全部行情源失败 -> " + " | ".join(errors))


def fetch_quote_summary(yahoo_symbol: str) -> dict[str, Any]:
    modules = ",".join(
        [
            "price",
            "summaryDetail",
            "defaultKeyStatistics",
            "financialData",
            "earningsTrend",
            "earningsHistory",
        ]
    )
    qs = urllib.parse.urlencode({"modules": modules})
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/"
            f"{urllib.parse.quote(yahoo_symbol)}?{qs}"
        )
        try:
            data = _get_json(url)
            result = (data.get("quoteSummary") or {}).get("result")
            if result:
                return result[0]
        except RuntimeError:
            continue
    return {}


def fetch_quote_v7(yahoo_symbol: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"symbols": yahoo_symbol})
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v7/finance/quote?{qs}"
        try:
            data = _get_json(url)
            result = (data.get("quoteResponse") or {}).get("result") or []
            if result:
                return result[0]
        except RuntimeError:
            continue
    return {}


def fetch_cn_quote_tencent(code6: str) -> dict[str, Any]:
    """A-share quote via Tencent qt.gtimg.cn."""
    if not (code6.isdigit() and len(code6) == 6):
        return {}
    prefix = "sh" if code6.startswith(("5", "6", "9")) else "sz"
    url = f"https://qt.gtimg.cn/q={prefix}{code6}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("gbk", errors="replace")
    except Exception:  # noqa: BLE001
        return {}
    if "~" not in text:
        return {}
    try:
        payload = text.split("=", 1)[1].strip().strip(";").strip('"')
        p = payload.split("~")
    except Exception:  # noqa: BLE001
        return {}
    if len(p) < 50:
        return {}

    def _f(idx: int) -> float | None:
        if idx >= len(p) or p[idx] in ("", None):
            return None
        try:
            return float(p[idx])
        except ValueError:
            return None

    # p[45] total mkt cap in 亿 CNY; p[39] PE; p[46] PB
    mcap_yi = _f(45)
    return {
        "shortName": p[1] or None,
        "longName": p[1] or None,
        "regularMarketPrice": _f(3),
        "regularMarketPreviousClose": _f(4),
        "regularMarketChange": _f(31),
        "regularMarketChangePercent": _f(32),
        "trailingPE": _f(39),
        "priceToBook": _f(46),
        "marketCap": (mcap_yi * 1e8) if mcap_yi is not None else None,
        "source": "tencent",
    }


def fetch_cn_quote_eastmoney(code6: str) -> dict[str, Any]:
    """A-share snapshot via Eastmoney push2delay (more stable than push2)."""
    if not (code6.isdigit() and len(code6) == 6):
        return {}
    market = "1" if code6.startswith(("5", "6", "9")) else "0"
    fields = "f57,f58,f43,f9,f23,f116,f117,f162,f167,f168,f169,f170,f46,f44,f45,f20,f18,f173,f186,f187,f188"
    qs = urllib.parse.urlencode({"secid": f"{market}.{code6}", "fields": fields})
    data = {}
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        url = f"https://{host}/api/qt/stock/get?{qs}"
        try:
            data = _get_json(url, headers=_headers({"Referer": "https://quote.eastmoney.com/"}))
            if data.get("data"):
                break
        except RuntimeError:
            continue
    d = (data.get("data") or {}) if isinstance(data, dict) else {}
    if not d:
        return {}

    def _div(v: Any, div: float) -> float | None:
        if v in (None, "-", ""):
            return None
        try:
            return float(v) / div
        except (TypeError, ValueError):
            return None

    price = _div(d.get("f43"), 100.0)
    pe = _div(d.get("f162"), 100.0) or _div(d.get("f9"), 100.0)
    pb = _div(d.get("f167"), 100.0) or _div(d.get("f23"), 100.0)
    roe_raw = d.get("f173")
    gm_raw = d.get("f186")
    pm_raw = d.get("f187")

    def _pct_to_ratio(v: Any) -> float | None:
        if v in (None, "", "-"):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f / 100.0 if abs(f) > 1 else f

    return {
        "shortName": d.get("f58"),
        "longName": d.get("f58"),
        "regularMarketPrice": price,
        "regularMarketPreviousClose": _div(d.get("f18") or d.get("f60"), 100.0),
        "regularMarketChange": _div(d.get("f169"), 100.0),
        "regularMarketChangePercent": _div(d.get("f170"), 100.0),
        "marketCap": d.get("f116"),
        "trailingPE": pe,
        "priceToBook": pb,
        "returnOnEquity": _pct_to_ratio(roe_raw),
        "grossMargins": _pct_to_ratio(gm_raw),
        "profitMargins": _pct_to_ratio(pm_raw),
        "currency": "CNY",
        "source": "eastmoney_cn",
    }


def fetch_hk_quote_tencent(code: str) -> dict[str, Any]:
    """Hong Kong quote via Tencent."""
    code = code.upper().replace(".HK", "")
    code = code.zfill(5) if code.isdigit() else code
    # Tencent often wants 5 digits without leading zero issues: 00700
    if code.isdigit():
        code = code.zfill(5)
    url = f"https://qt.gtimg.cn/q=hk{code}"
    try:
        text = _get_bytes(url, headers=_headers({"Referer": "https://finance.qq.com/"})).decode("gbk", errors="replace")
    except RuntimeError:
        return {}
    if "~" not in text:
        return {}
    try:
        payload = text.split("=", 1)[1].strip().strip(";").strip('"')
        p = payload.split("~")
    except Exception:  # noqa: BLE001
        return {}
    if len(p) < 50:
        return {}

    def _f(idx: int) -> float | None:
        if idx >= len(p) or p[idx] in ("", None):
            return None
        try:
            return float(p[idx])
        except ValueError:
            return None

    mcap_yi = _f(45) or _f(44)
    return {
        "shortName": p[1] or None,
        "longName": p[1] or None,
        "regularMarketPrice": _f(3),
        "regularMarketPreviousClose": _f(4),
        "regularMarketChange": _f(31),
        "regularMarketChangePercent": _f(32),
        "regularMarketDayHigh": _f(33),
        "regularMarketDayLow": _f(34),
        "trailingPE": _f(39),
        # Tencent PB index is noisy for HK; leave empty and prefer Eastmoney.
        "priceToBook": None,
        "marketCap": (mcap_yi * 1e8) if mcap_yi is not None else None,
        "fiftyTwoWeekHigh": _f(48),
        "fiftyTwoWeekLow": _f(49),
        "currency": "HKD",
        "source": "tencent_hk",
    }


def _pct_field_to_ratio(v: Any) -> float | None:
    """Convert Eastmoney percentage-unit fields (e.g. 16.75 → 0.1675)."""
    if v in (None, "", "-"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f / 100.0


def _ratio_or_pct_to_ratio(v: Any) -> float | None:
    """Accept either a ratio (0.25) or percent (25)."""
    if v in (None, "", "-"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f / 100.0 if abs(f) > 1.5 else f


def fetch_hk_quote_eastmoney(code: str) -> dict[str, Any]:
    """HK snapshot via Eastmoney secid 116.xxxxx."""
    code = code.upper().replace(".HK", "")
    if not code.isdigit():
        return {}
    code5 = code.zfill(5)
    fields = "f57,f58,f43,f9,f23,f116,f117,f162,f167,f169,f170,f46,f44,f45,f18,f173,f186,f187,f51,f52"
    qs = urllib.parse.urlencode({"secid": f"116.{code5}", "fields": fields})
    data: dict[str, Any] = {}
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        url = f"https://{host}/api/qt/stock/get?{qs}"
        try:
            data = _get_json(url, headers=_headers({"Referer": "https://quote.eastmoney.com/"}))
            if data.get("data"):
                break
        except RuntimeError:
            continue
    d = (data.get("data") or {}) if isinstance(data, dict) else {}
    if not d:
        return {}

    def _div(v: Any, div: float) -> float | None:
        if v in (None, "-", ""):
            return None
        try:
            return float(v) / div
        except (TypeError, ValueError):
            return None

    pe = _div(d.get("f162"), 100.0) or _div(d.get("f9"), 100.0)
    if pe is not None and pe <= 0:
        pe = None
    pb = _div(d.get("f167"), 100.0) or _div(d.get("f23"), 100.0)
    return {
        "shortName": d.get("f58"),
        "longName": d.get("f58"),
        "regularMarketPrice": _div(d.get("f43"), 1000.0),
        "regularMarketPreviousClose": _div(d.get("f18") or d.get("f60"), 1000.0),
        "regularMarketChange": _div(d.get("f169"), 1000.0),
        "regularMarketChangePercent": _div(d.get("f170"), 100.0),
        "marketCap": d.get("f116"),
        "trailingPE": pe,
        "priceToBook": pb,
        "returnOnEquity": _pct_field_to_ratio(d.get("f173")),
        "grossMargins": _pct_field_to_ratio(d.get("f186")) if d.get("f186") not in (0, 0.0, "0") else None,
        "profitMargins": _pct_field_to_ratio(d.get("f187")) if d.get("f187") not in (0, 0.0, "0") else None,
        "fiftyTwoWeekHigh": _div(d.get("f51"), 1000.0),
        "fiftyTwoWeekLow": _div(d.get("f52"), 1000.0),
        "currency": "HKD",
        "source": "eastmoney_hk",
    }


def fetch_cn_f10_main(code6: str) -> dict[str, Any]:
    """A-share main financial indicators from Eastmoney F10."""
    if not (code6.isdigit() and len(code6) == 6):
        return {}
    qs = urllib.parse.urlencode(
        {
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code6}")',
            "pageNumber": "1",
            "pageSize": "4",
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
        }
    )
    url = f"https://datacenter.eastmoney.com/securities/api/data/v1/get?{qs}"
    try:
        data = _get_json(url, headers=_headers({"Referer": "https://emweb.securities.eastmoney.com/"}))
    except RuntimeError:
        return {}
    rows = ((data.get("result") or {}).get("data")) or []
    if not rows:
        return {}
    r = rows[0]
    out: dict[str, Any] = {
        "source": "eastmoney_cn_f10",
        "reportDate": str(r.get("REPORT_DATE") or "")[:10],
        "reportType": r.get("REPORT_TYPE"),
        "trailingEps": r.get("EPSJB") or r.get("EPSXS"),
        "bookValue": r.get("BPS"),
        "returnOnEquity": _pct_field_to_ratio(r.get("ROEJQ")),
        "grossMargins": _pct_field_to_ratio(r.get("XSMLL")),
        "profitMargins": _pct_field_to_ratio(r.get("XSJLL")),
        "revenueGrowth": _pct_field_to_ratio(r.get("TOTALOPERATEREVETZ") or r.get("DJD_TOI_YOY")),
        "earningsGrowth": _pct_field_to_ratio(r.get("PARENTNETPROFITTZ") or r.get("DJD_DPNP_YOY")),
        "operatingCashflow": r.get("NETCASH_OPERATE_PK") or r.get("NETCASH_OPERATE"),
        "shortName": r.get("SECURITY_NAME_ABBR"),
        "longName": r.get("SECURITY_NAME_ABBR"),
    }
    # Prefer 扣非 growth when headline NI is noisy / declining while ops intact.
    deduct = _pct_field_to_ratio(r.get("KCFJCXSYJLRTZ") or r.get("DJD_DEDUCTDPNP_YOY"))
    if deduct is not None and out.get("earningsGrowth") is None:
        out["earningsGrowth"] = deduct
    return {k: v for k, v in out.items() if v not in (None, "")}


def fetch_hk_f10_main(code: str) -> dict[str, Any]:
    """HK main indicators from Eastmoney HKF10."""
    code = code.upper().replace(".HK", "")
    if not code.isdigit():
        return {}
    code5 = code.zfill(5)
    qs = urllib.parse.urlencode(
        {
            "reportName": "RPT_HKF10_FN_MAININDICATOR",
            "columns": "ALL",
            "filter": f'(SECUCODE="{code5}.HK")',
            "pageNumber": "1",
            "pageSize": "4",
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
        }
    )
    url = f"https://datacenter.eastmoney.com/securities/api/data/v1/get?{qs}"
    try:
        data = _get_json(url, headers=_headers({"Referer": "https://emweb.securities.eastmoney.com/"}))
    except RuntimeError:
        return {}
    rows = ((data.get("result") or {}).get("data")) or []
    if not rows:
        return {}
    r = rows[0]
    out: dict[str, Any] = {
        "source": "eastmoney_hk_f10",
        "reportDate": str(r.get("REPORT_DATE") or "")[:10],
        "reportType": r.get("REPORT_TYPE"),
        "trailingEps": r.get("EPS_TTM") or r.get("BASIC_EPS") or r.get("DILUTED_EPS"),
        "bookValue": r.get("BPS"),
        "returnOnEquity": _pct_field_to_ratio(r.get("ROE_YEARLY") or r.get("ROE_AVG")),
        "grossMargins": _pct_field_to_ratio(r.get("GROSS_PROFIT_RATIO")),
        "profitMargins": _pct_field_to_ratio(r.get("NET_PROFIT_RATIO")),
        "revenueGrowth": _pct_field_to_ratio(r.get("OPERATE_INCOME_YOY")),
        "earningsGrowth": _pct_field_to_ratio(r.get("HOLDER_PROFIT_YOY")),
        "operatingCashflow": r.get("NETCASH_OPERATE"),
        "totalDebt": r.get("TOTAL_LIABILITIES"),
        "shortName": r.get("SECURITY_NAME_ABBR"),
        "longName": r.get("SECURITY_NAME_ABBR"),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def _raw(node: Any, default: Any = None) -> Any:
    if node is None:
        return default
    if isinstance(node, dict):
        if "raw" in node:
            return node.get("raw", default)
        if "fmt" in node:
            return node.get("fmt", default)
    return node


def snapshot_from_sources(
    yahoo_symbol: str,
    chart: dict[str, Any],
    summary: dict[str, Any],
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = chart.get("meta") or {}
    candles = chart.get("candles") or []
    quote = quote or {}
    price_mod = summary.get("price") or {}
    detail = summary.get("summaryDetail") or {}
    stats = summary.get("defaultKeyStatistics") or {}
    fin = summary.get("financialData") or {}
    trend = summary.get("earningsTrend") or {}

    last = candles[-1]["close"] if candles else meta.get("regularMarketPrice") or quote.get("regularMarketPrice")
    prev = candles[-2]["close"] if len(candles) >= 2 else meta.get("chartPreviousClose") or quote.get("regularMarketPreviousClose")
    chg = None
    chg_pct = None
    if last is not None and prev not in (None, 0):
        chg = float(last) - float(prev)
        chg_pct = chg / float(prev) * 100.0

    highs = [c["high"] for c in candles] or [None]
    lows = [c["low"] for c in candles] or [None]

    shares = _raw(stats.get("sharesOutstanding")) or quote.get("sharesOutstanding")
    market_cap = (
        _raw(price_mod.get("marketCap"))
        or _raw(detail.get("marketCap"))
        or quote.get("marketCap")
    )
    if market_cap is None and last is not None and shares:
        try:
            market_cap = float(last) * float(shares)
        except (TypeError, ValueError):
            market_cap = None

    trailing_eps = (
        _raw(stats.get("trailingEps"))
        or quote.get("epsTrailingTwelveMonths")
        or quote.get("trailingEps")
    )
    forward_eps = _raw(stats.get("forwardEps")) or quote.get("epsForward") or quote.get("forwardEps")
    trailing_pe = _raw(detail.get("trailingPE")) or _raw(stats.get("trailingPE")) or quote.get("trailingPE")
    forward_pe = _raw(detail.get("forwardPE")) or _raw(stats.get("forwardPE")) or quote.get("forwardPE")
    if trailing_pe is None and last and trailing_eps not in (None, 0):
        try:
            trailing_pe = float(last) / float(trailing_eps)
        except (TypeError, ValueError, ZeroDivisionError):
            trailing_pe = None
    if forward_pe is None and last and forward_eps not in (None, 0):
        try:
            forward_pe = float(last) / float(forward_eps)
        except (TypeError, ValueError, ZeroDivisionError):
            forward_pe = None

    # Earnings trend: extract 0=+0y current year, 1=+1y
    year_estimates = []
    for t in trend.get("trend") or []:
        period = t.get("period")
        if period in ("0y", "+1y"):
            year_estimates.append(
                {
                    "period": period,
                    "growth": _raw(t.get("growth")),
                    "earningsEstimateAvg": _raw((t.get("earningsEstimate") or {}).get("avg")),
                    "revenueEstimateAvg": _raw((t.get("revenueEstimate") or {}).get("avg")),
                    "earningsEstimateNumAnalysts": _raw((t.get("earningsEstimate") or {}).get("numOfAnalysts")),
                }
            )

    return {
        "yahoo": yahoo_symbol,
        "name": price_mod.get("longName")
        or price_mod.get("shortName")
        or quote.get("longName")
        or quote.get("shortName")
        or meta.get("shortName")
        or yahoo_symbol,
        "exchange": price_mod.get("exchangeName")
        or quote.get("fullExchangeName")
        or meta.get("fullExchangeName")
        or meta.get("exchangeName"),
        "currency": meta.get("currency") or price_mod.get("currency") or quote.get("currency") or "USD",
        "price": float(last) if last is not None else None,
        "change": chg,
        "changePct": chg_pct,
        "marketCap": market_cap,
        "trailingPE": trailing_pe,
        "forwardPE": forward_pe,
        "priceToBook": _raw(detail.get("priceToBook")) or _raw(stats.get("priceToBook")) or quote.get("priceToBook"),
        "enterpriseToRevenue": _raw(stats.get("enterpriseToRevenue")),
        "enterpriseToEbitda": _raw(stats.get("enterpriseToEbitda")),
        "pegRatio": _raw(stats.get("pegRatio")),
        "beta": _raw(detail.get("beta")) or _raw(stats.get("beta")) or quote.get("beta"),
        "dividendYield": _raw(detail.get("dividendYield")) or _raw(stats.get("dividendYield")) or quote.get("trailingAnnualDividendYield"),
        "fiftyTwoWeekHigh": _raw(detail.get("fiftyTwoWeekHigh"))
        or meta.get("fiftyTwoWeekHigh")
        or quote.get("fiftyTwoWeekHigh")
        or (max(highs) if highs[0] is not None else None),
        "fiftyTwoWeekLow": _raw(detail.get("fiftyTwoWeekLow"))
        or meta.get("fiftyTwoWeekLow")
        or quote.get("fiftyTwoWeekLow")
        or (min(lows) if lows[0] is not None else None),
        "targetMeanPrice": _raw(fin.get("targetMeanPrice")) or quote.get("targetMeanPrice"),
        "recommendationKey": fin.get("recommendationKey") or quote.get("averageAnalystRating"),
        "revenueGrowth": _raw(fin.get("revenueGrowth")) or quote.get("revenueGrowth"),
        "earningsGrowth": _raw(fin.get("earningsGrowth")) or quote.get("earningsGrowth"),
        "grossMargins": _raw(fin.get("grossMargins")) or quote.get("grossMargins"),
        "operatingMargins": _raw(fin.get("operatingMargins")) or quote.get("operatingMargins"),
        "profitMargins": _raw(fin.get("profitMargins")) or quote.get("profitMargins"),
        "returnOnEquity": _raw(fin.get("returnOnEquity")) or quote.get("returnOnEquity"),
        "returnOnAssets": _raw(fin.get("returnOnAssets")) or quote.get("returnOnAssets"),
        "totalCash": _raw(fin.get("totalCash")) or quote.get("totalCash"),
        "totalDebt": _raw(fin.get("totalDebt")) or quote.get("totalDebt"),
        "freeCashflow": _raw(fin.get("freeCashflow")) or quote.get("freeCashflow"),
        "currentRatio": _raw(fin.get("currentRatio")) or quote.get("currentRatio"),
        "trailingEps": trailing_eps,
        "forwardEps": forward_eps,
        "bookValue": _raw(stats.get("bookValue")) or quote.get("bookValue"),
        "sharesOutstanding": shares or quote.get("sharesOutstanding"),
        "floatShares": _raw(stats.get("floatShares")),
        "heldPercentInsiders": _raw(stats.get("heldPercentInsiders")),
        "heldPercentInstitutions": _raw(stats.get("heldPercentInstitutions")),
        "yearEstimates": year_estimates,
        "asOf": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "candleCount": len(candles),
    }


def _parse_money_str(s: Any) -> float | None:
    if s is None:
        return None
    text = str(s).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text.upper() in {"N/A", "NA", "-", "NULL"}:
        return None
    mult = 1.0
    upper = text.upper()
    if upper.endswith("T"):
        mult = 1e12
        text = text[:-1]
    elif upper.endswith("B"):
        mult = 1e9
        text = text[:-1]
    elif upper.endswith("M"):
        mult = 1e6
        text = text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return None


def fetch_us_quote_sina(symbol: str) -> dict[str, Any]:
    """US quote via Sina gb_xxx list."""
    sym = symbol.split(".")[0].lower()
    url = f"https://hq.sinajs.cn/list=gb_{sym}"
    try:
        raw = _get_bytes(url, headers=_headers({"Referer": "https://finance.sina.com.cn/"}))
        text = raw.decode("gbk", errors="replace")
    except RuntimeError:
        return {}
    m = re.search(r'="([^"]*)"', text)
    if not m or not m.group(1):
        return {}
    p = m.group(1).split(",")
    if len(p) < 13:
        return {}

    def _f(idx: int) -> float | None:
        if idx >= len(p) or p[idx] in ("", None):
            return None
        try:
            return float(p[idx])
        except ValueError:
            return None

    price = _f(1)
    chg_pct = _f(2)
    chg = _f(4)
    return {
        "shortName": p[0] or symbol.upper(),
        "longName": p[0] or symbol.upper(),
        "regularMarketPrice": price,
        "regularMarketChange": chg,
        "regularMarketChangePercent": chg_pct,
        "regularMarketDayHigh": _f(6),
        "regularMarketDayLow": _f(7),
        "fiftyTwoWeekHigh": _f(8),
        "fiftyTwoWeekLow": _f(9),
        "regularMarketVolume": int(_f(10) or 0),
        "marketCap": _f(12),
        "sharesOutstanding": _f(19),
        "currency": "USD",
        "source": "sina_us",
    }


def fetch_us_eastmoney_secid(symbol: str) -> str | None:
    sym = symbol.split(".")[0].upper()
    qs = urllib.parse.urlencode(
        {
            "input": sym,
            "type": "14",
            "token": "D43BF722C8E33BDC906FB84D85E326E8",
        }
    )
    url = f"https://searchapi.eastmoney.com/api/suggest/get?{qs}"
    try:
        data = _get_json(url, headers=_headers({"Referer": "https://quote.eastmoney.com/"}))
    except RuntimeError:
        return None
    rows = ((data.get("QuotationCodeTable") or {}).get("Data")) or []
    for row in rows:
        if str(row.get("Code", "")).upper() == sym and row.get("QuoteID"):
            return str(row["QuoteID"])
        if str(row.get("Code", "")).upper() == sym and row.get("MktNum"):
            return f"{row['MktNum']}.{sym}"
    return f"105.{sym}"


def fetch_us_quote_eastmoney(symbol: str) -> dict[str, Any]:
    secid = fetch_us_eastmoney_secid(symbol)
    if not secid:
        return {}
    fields = "f57,f58,f43,f44,f45,f46,f47,f51,f52,f60,f92,f116,f117,f162,f167,f169,f170,f173,f186,f187"
    qs = urllib.parse.urlencode({"secid": secid, "fields": fields})
    data: dict[str, Any] = {}
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):
        url = f"https://{host}/api/qt/stock/get?{qs}"
        try:
            data = _get_json(url, headers=_headers({"Referer": "https://quote.eastmoney.com/"}))
            if data.get("data"):
                break
        except RuntimeError:
            continue
    d = data.get("data") or {}
    if not d:
        return {}

    def _px(v: Any, div: float = 1000.0) -> float | None:
        if v in (None, "", "-"):
            return None
        try:
            return float(v) / div
        except (TypeError, ValueError):
            return None

    price = _px(d.get("f43"))
    pb = _px(d.get("f167"), 100.0)
    # f92 observed near PE-like values for some names; keep only if sensible
    pe_candidate = d.get("f92")
    trailing_pe = None
    try:
        if pe_candidate is not None:
            pe_f = float(pe_candidate)
            if 0 < pe_f < 2000:
                trailing_pe = pe_f
    except (TypeError, ValueError):
        trailing_pe = None

    return {
        "shortName": d.get("f58") or symbol.upper(),
        "longName": d.get("f58") or symbol.upper(),
        "regularMarketPrice": price,
        "regularMarketPreviousClose": _px(d.get("f60")),
        "regularMarketDayHigh": _px(d.get("f44")),
        "regularMarketDayLow": _px(d.get("f45")),
        "fiftyTwoWeekHigh": _px(d.get("f51")),
        "fiftyTwoWeekLow": _px(d.get("f52")),
        "regularMarketChange": _px(d.get("f169")),
        "regularMarketChangePercent": _px(d.get("f170"), 100.0),
        "marketCap": d.get("f116"),
        "priceToBook": pb,
        "trailingPE": trailing_pe,
        "returnOnEquity": _pct_field_to_ratio(d.get("f173")),
        "currency": "USD",
        "source": "eastmoney_us",
        "eastmoneySecid": secid,
    }


def fetch_us_quote_nasdaq(symbol: str) -> dict[str, Any]:
    sym = symbol.split(".")[0].upper()
    url = f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(sym)}/summary?assetclass=stocks"
    try:
        data = _get_json(
            url,
            headers=_headers(
                {
                    "Referer": "https://www.nasdaq.com/",
                    "Accept": "application/json",
                }
            ),
        )
    except RuntimeError:
        return {}
    summary = ((data.get("data") or {}).get("summaryData")) or {}
    if not summary:
        return {}

    def _val(key: str) -> Any:
        node = summary.get(key) or {}
        return node.get("value") if isinstance(node, dict) else None

    high_low = str(_val("FiftTwoWeekHighLow") or "")
    hi = lo = None
    if "/" in high_low:
        left, right = high_low.split("/", 1)
        hi = _parse_money_str(left)
        lo = _parse_money_str(right)
    target = _parse_money_str(_val("OneYrTarget"))
    return {
        "marketCap": _parse_money_str(_val("MarketCap")),
        "targetMeanPrice": target,
        "fiftyTwoWeekHigh": hi,
        "fiftyTwoWeekLow": lo,
        "dividendYield": _parse_money_str(_val("Yield")),
        "averageAnalystRating": _val("Sector"),
        "source": "nasdaq",
    }


def fetch_us_financials_eastmoney(symbol: str) -> dict[str, Any]:
    """TTM EPS / margins from Eastmoney US income statement line items."""
    sym = symbol.split(".")[0].upper()
    # NASDAQ often *.O ; try both
    codes = [f"{sym}.O", f"{sym}.N", sym]
    rows: list[dict[str, Any]] = []
    for code in codes:
        filt = f'(SECUCODE="{code}")'
        qs = urllib.parse.urlencode(
            {
                "reportName": "RPT_USF10_FN_INCOME",
                "columns": "ALL",
                "filter": filt,
                "pageNumber": "1",
                "pageSize": "400",
                "sortTypes": "-1",
                "sortColumns": "REPORT_DATE",
            }
        )
        url = f"https://datacenter.eastmoney.com/securities/api/data/v1/get?{qs}"
        try:
            data = _get_json(
                url,
                headers=_headers({"Referer": "https://emweb.securities.eastmoney.com/"}),
            )
        except RuntimeError:
            continue
        rows = ((data.get("result") or {}).get("data")) or []
        if rows:
            break
    if not rows:
        return {}

    q_eps: list[tuple[str, float]] = []
    q_rev: list[tuple[str, float]] = []
    q_gp: list[tuple[str, float]] = []
    q_ni: list[tuple[str, float]] = []
    for r in rows:
        if r.get("REPORT_TYPE") != "单季报":
            continue
        dt = str(r.get("REPORT_DATE") or "")[:10]
        name = r.get("ITEM_NAME") or ""
        try:
            amt = float(r.get("AMOUNT"))
        except (TypeError, ValueError):
            continue
        if name == "摊薄每股收益-普通股":
            q_eps.append((dt, amt))
        elif name in {"营业收入", "主营收入"}:
            q_rev.append((dt, amt))
        elif name == "毛利":
            q_gp.append((dt, amt))
        elif name in {"归属于母公司股东净利润", "归属于普通股股东净利润"}:
            q_ni.append((dt, amt))

    def _uniq_latest(items: list[tuple[str, float]], n: int = 4) -> list[float]:
        seen = set()
        out = []
        for dt, amt in items:
            if dt in seen:
                continue
            seen.add(dt)
            out.append(amt)
            if len(out) >= n:
                break
        return out

    eps4 = _uniq_latest(q_eps, 4)
    rev4 = _uniq_latest(q_rev, 4)
    gp4 = _uniq_latest(q_gp, 4)
    ni4 = _uniq_latest(q_ni, 4)
    out: dict[str, Any] = {"source": "eastmoney_us_financials"}
    if len(eps4) >= 4:
        out["trailingEps"] = sum(eps4)
    elif len(eps4) >= 1:
        out["trailingEps"] = sum(eps4) * (4 / len(eps4))
    if len(rev4) >= 1 and len(gp4) >= 1:
        rev = sum(rev4)
        gp = sum(gp4[: len(rev4)])
        if rev:
            out["grossMargins"] = gp / rev
    if len(rev4) >= 1 and len(ni4) >= 1:
        rev = sum(rev4)
        ni = sum(ni4[: len(rev4)])
        if rev:
            out["profitMargins"] = ni / rev
    if len(rev4) >= 4:
        # rough YoY: latest quarter vs year-ago quarter if present in list
        latest_rev = q_rev[0][1] if q_rev else None
        yoy = None
        if len(q_rev) >= 5:
            # same index 4 is ~1y ago quarter after unique? use raw list dates
            latest_dt = q_rev[0][0]
            for dt, amt in q_rev[1:]:
                if dt[:4] == str(int(latest_dt[:4]) - 1) and dt[5:] == latest_dt[5:]:
                    if amt:
                        yoy = latest_rev / amt - 1.0
                        break
        if yoy is not None:
            out["revenueGrowth"] = yoy
    return out


def _merge_quote(base: dict[str, Any], extra: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
    if not extra:
        return base
    out = dict(base)
    skip = {"raw", "source", "eastmoneySecid", "reportDate", "reportType"}
    for k, v in extra.items():
        if k in skip:
            continue
        if v in (None, ""):
            continue
        if overwrite or out.get(k) in (None, "", 0):
            out[k] = v
    if not out.get("shortName") and extra.get("shortName"):
        out["shortName"] = extra["shortName"]
    if not out.get("longName") and extra.get("longName"):
        out["longName"] = extra.get("longName") or extra.get("shortName")
    return out


def _derive_eps_pe(quote: dict[str, Any]) -> None:
    """Fill missing EPS from PE (or PE from EPS). Never overwrite a sane PE with period EPS."""
    price = quote.get("regularMarketPrice")
    pe = quote.get("trailingPE")
    eps = quote.get("trailingEps")
    if eps in (None, 0) and price and pe not in (None, 0):
        try:
            quote["trailingEps"] = float(price) / float(pe)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        return
    if pe in (None, 0) and price and eps not in (None, 0):
        try:
            quote["trailingPE"] = float(price) / float(eps)
        except (TypeError, ValueError, ZeroDivisionError):
            pass


def load_market_bundle(yahoo_symbol: str, range_: str = "1y") -> dict[str, Any]:
    sources: list[str] = []
    chart = fetch_chart(yahoo_symbol, range_=range_, interval="1d")
    if (chart.get("meta") or {}).get("dataSource"):
        sources.append(f"K线:{(chart['meta']['dataSource'])}")

    summary = fetch_quote_summary(yahoo_symbol)
    if summary:
        sources.append("Yahoo quoteSummary")
    quote = fetch_quote_v7(yahoo_symbol)
    if quote:
        sources.append("Yahoo quote")

    sym_u = yahoo_symbol.upper()
    if sym_u.endswith((".SS", ".SZ")):
        code6 = yahoo_symbol.split(".", 1)[0]
        tencent = fetch_cn_quote_tencent(code6)
        em = fetch_cn_quote_eastmoney(code6)
        f10 = fetch_cn_f10_main(code6)
        if tencent:
            quote = _merge_quote(quote, tencent)
            sources.append("腾讯行情")
        if em:
            quote = _merge_quote(quote, em)
            sources.append("东财估值")
        if f10:
            # Quality / growth from statements overwrite; never use period EPS as TTM.
            quality = {
                k: f10[k]
                for k in (
                    "returnOnEquity",
                    "grossMargins",
                    "profitMargins",
                    "revenueGrowth",
                    "earningsGrowth",
                    "operatingCashflow",
                    "bookValue",
                    "shortName",
                    "longName",
                    "reportDate",
                    "reportType",
                )
                if k in f10
            }
            quote = _merge_quote(quote, quality, overwrite=True)
            sources.append("东财F10财报")
        _derive_eps_pe(quote)
    elif sym_u.endswith(".HK"):
        code = yahoo_symbol.split(".", 1)[0]
        hk = fetch_hk_quote_tencent(code)
        em = fetch_hk_quote_eastmoney(code)
        f10 = fetch_hk_f10_main(code)
        if hk:
            quote = _merge_quote(quote, hk)
            sources.append("腾讯港股")
        if em:
            quote = _merge_quote(quote, em)
            sources.append("东财港股")
        if f10:
            quote = _merge_quote(quote, f10, overwrite=True)
            sources.append("东财港股F10")
        _derive_eps_pe(quote)
    elif "." not in yahoo_symbol or sym_u.endswith(".US"):
        us_sym = yahoo_symbol.split(".")[0]
        sina = fetch_us_quote_sina(us_sym)
        em = fetch_us_quote_eastmoney(us_sym)
        ndq = fetch_us_quote_nasdaq(us_sym)
        fins = fetch_us_financials_eastmoney(us_sym)
        if sina:
            quote = _merge_quote(quote, sina)
            sources.append("新浪美股")
        if em:
            quote = _merge_quote(quote, em)
            sources.append("东财美股")
        if ndq:
            quote = _merge_quote(quote, ndq)
            sources.append("Nasdaq")
        if fins:
            quote = _merge_quote(quote, fins, overwrite=True)
            sources.append("东财美股财报")
        # Prefer price/TTM EPS for trailing PE on US names.
        price = quote.get("regularMarketPrice")
        eps = quote.get("trailingEps")
        if price and eps not in (None, 0):
            try:
                quote["trailingPE"] = float(price) / float(eps)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    # de-dupe sources preserving order
    seen = set()
    sources = [s for s in sources if not (s in seen or seen.add(s))]

    snap = snapshot_from_sources(yahoo_symbol, chart, summary, quote)
    # Keep operating cash when FCF absent.
    if snap.get("freeCashflow") in (None, 0) and quote.get("operatingCashflow") not in (None, ""):
        snap["freeCashflow"] = quote.get("operatingCashflow")
        snap["cashflowIsOperating"] = True
    if quote.get("reportDate"):
        snap["fundamentalsAsOf"] = quote.get("reportDate")
    if quote.get("reportType"):
        snap["fundamentalsReportType"] = quote.get("reportType")
    snap["dataSources"] = sources or ["公开行情"]
    return {"chart": chart, "summary": summary, "quote": quote, "snapshot": snap}
