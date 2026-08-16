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


def fetch_chart(yahoo_symbol: str, range_: str = "1y", interval: str = "1d") -> dict[str, Any]:
    """Multi-source chart: A-share prefers Sina/Eastmoney; others Yahoo first."""
    errors: list[str] = []
    is_cn = bool(_cn_prefix_code(yahoo_symbol))
    sources: list[tuple[str, Any]] = []
    if is_cn:
        sources = [
            ("sina", lambda: fetch_chart_sina_cn(yahoo_symbol, range_=range_)),
            ("eastmoney", lambda: fetch_chart_eastmoney_cn(yahoo_symbol, range_=range_)),
            ("yahoo", lambda: fetch_chart_yahoo(yahoo_symbol, range_=range_, interval=interval)),
        ]
    else:
        sources = [
            ("yahoo", lambda: fetch_chart_yahoo(yahoo_symbol, range_=range_, interval=interval)),
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
    """Best-effort A-share snapshot via Eastmoney push2."""
    if not (code6.isdigit() and len(code6) == 6):
        return {}
    market = "1" if code6.startswith(("5", "6", "9")) else "0"
    fields = "f57,f58,f43,f169,f170,f46,f44,f45,f47,f48,f116,f117,f162,f167,f9,f23,f20,f18"
    qs = urllib.parse.urlencode(
        {
            "secid": f"{market}.{code6}",
            "fields": fields,
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
    )
    url = f"https://push2.eastmoney.com/api/qt/stock/get?{qs}"
    try:
        data = _get_json(url)
    except RuntimeError:
        return {}
    d = (data.get("data") or {}) if isinstance(data, dict) else {}
    if not d:
        return {}

    def _scaled(v: Any, div: float = 100.0) -> float | None:
        if v in (None, "-", ""):
            return None
        try:
            return float(v) / div
        except (TypeError, ValueError):
            return None

    price = _scaled(d.get("f43"))
    prev = _scaled(d.get("f18") or d.get("f60"))
    return {
        "shortName": d.get("f58"),
        "regularMarketPrice": price,
        "regularMarketPreviousClose": prev,
        "regularMarketChange": _scaled(d.get("f169")),
        "regularMarketChangePercent": _scaled(d.get("f170")),
        "marketCap": d.get("f116"),
        "trailingPE": _scaled(d.get("f9"), 100.0) if d.get("f9") not in (None, "-") else None,
        "priceToBook": _scaled(d.get("f23"), 100.0) if d.get("f23") not in (None, "-") else None,
        "epsTrailingTwelveMonths": None,
        "source": "eastmoney",
        "raw": {
            "f9": d.get("f9"),
            "f23": d.get("f23"),
            "f116": d.get("f116"),
            "f20": d.get("f20"),
        },
    }


def _normalize_cn_multiples(q: dict[str, Any]) -> dict[str, Any]:
    """Eastmoney PE/PB fields are sometimes *100, sometimes plain."""
    out = dict(q)
    for key in ("trailingPE", "priceToBook"):
        v = out.get(key)
        if v is None:
            # try raw
            raw_key = "f9" if key == "trailingPE" else "f23"
            raw = (out.get("raw") or {}).get(raw_key)
            if raw in (None, "-"):
                continue
            try:
                raw_f = float(raw)
            except (TypeError, ValueError):
                continue
            # Heuristic: values > 1000 are almost surely *100 scaled
            out[key] = raw_f / 100.0 if raw_f > 400 else raw_f
        else:
            try:
                vf = float(v)
                if vf > 400:
                    out[key] = vf / 100.0
            except (TypeError, ValueError):
                pass
    return out


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
    fields = "f57,f58,f43,f44,f45,f46,f47,f51,f52,f60,f92,f116,f117,f162,f167,f169,f170,f173"
    qs = urllib.parse.urlencode({"secid": secid, "fields": fields})
    url = f"https://push2delay.eastmoney.com/api/qt/stock/get?{qs}"
    try:
        data = _get_json(url, headers=_headers({"Referer": "https://quote.eastmoney.com/"}))
    except RuntimeError:
        return {}
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


def _merge_quote(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    if not extra:
        return base
    out = dict(base)
    for k, v in extra.items():
        if k in {"raw", "source", "eastmoneySecid"}:
            continue
        if out.get(k) in (None, "", 0) and v not in (None, ""):
            out[k] = v
    if not out.get("shortName") and extra.get("shortName"):
        out["shortName"] = extra["shortName"]
    if not out.get("longName") and extra.get("longName"):
        out["longName"] = extra.get("longName") or extra.get("shortName")
    return out


def load_market_bundle(yahoo_symbol: str, range_: str = "1y") -> dict[str, Any]:
    chart = fetch_chart(yahoo_symbol, range_=range_, interval="1d")
    summary = fetch_quote_summary(yahoo_symbol)
    quote = fetch_quote_v7(yahoo_symbol)
    if yahoo_symbol.endswith((".SS", ".SZ")):
        code6 = yahoo_symbol.split(".", 1)[0]
        cn = fetch_cn_quote_tencent(code6) or _normalize_cn_multiples(fetch_cn_quote_eastmoney(code6))
        quote = _merge_quote(quote, cn)
    elif "." not in yahoo_symbol or yahoo_symbol.upper().endswith((".US",)):
        # US ticker fallbacks when Yahoo fundamentals are blocked
        us_sym = yahoo_symbol.split(".")[0]
        quote = _merge_quote(quote, fetch_us_quote_sina(us_sym))
        quote = _merge_quote(quote, fetch_us_quote_eastmoney(us_sym))
        quote = _merge_quote(quote, fetch_us_quote_nasdaq(us_sym))
        fins = fetch_us_financials_eastmoney(us_sym)
        quote = _merge_quote(quote, fins)
        # Prefer PE derived from price / TTM EPS over noisy vendor PE fields
        price = quote.get("regularMarketPrice")
        eps = quote.get("trailingEps")
        if price and eps not in (None, 0):
            try:
                quote["trailingPE"] = float(price) / float(eps)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        elif quote.get("trailingPE") in (None, 0) and price and eps not in (None, 0):
            try:
                quote["trailingPE"] = float(price) / float(eps)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    snap = snapshot_from_sources(yahoo_symbol, chart, summary, quote)
    return {"chart": chart, "summary": summary, "quote": quote, "snapshot": snap}
