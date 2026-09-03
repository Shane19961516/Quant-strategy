"""Dalian Commodity Exchange (DCE) option dayQuotes client.

The official JSON API is protected by a JS WAF (瑞数). Plain requests get HTTP 412.
This client launches a Chromium session via Playwright, warms cookies on the
exchange site, then calls dayQuotes through the page's fetch (WAF-signed).
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DCE_OPTION_CODE_MAP: dict[str, str] = {
    "玉米期权": "c",
    "豆粕期权": "m",
    "铁矿石期权": "i",
    "液化石油气期权": "pg",
    "聚乙烯期权": "l",
    "聚氯乙烯期权": "v",
    "聚丙烯期权": "pp",
    "棕榈油期权": "p",
    "黄大豆1号期权": "a",
    "黄大豆2号期权": "b",
    "豆油期权": "y",
    "乙二醇期权": "eg",
    "苯乙烯期权": "eb",
    "鸡蛋期权": "jd",
    "玉米淀粉期权": "cs",
    "生猪期权": "lh",
    "原木期权": "lg",
}

_COLUMN_MAP = {
    "variety": "品种名称",
    "contractId": "合约",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "close": "收盘价",
    "lastClear": "前结算价",
    "clearPrice": "结算价",
    "diff": "涨跌",
    "diff1": "涨跌1",
    "delta": "Delta",
    "volumn": "成交量",
    "openInterest": "持仓量",
    "diffI": "持仓量变化",
    "turnover": "成交额",
    "matchQtySum": "行权量",
    "impliedVolatility": "隐含波动率(%)",
}

_HOME = "http://www.dce.com.cn/"
_CHANNEL = "http://www.dce.com.cn/dce/channel/list/162.html"
_API_PATH = "/dcereport/publicweb/dailystat/dayQuotes"

_lock = threading.RLock()
_session: Optional["DCESyncSession"] = None


class DCESyncSession:
    """Synchronous Playwright Chromium session for DCE dayQuotes."""

    def __init__(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw_cm = sync_playwright()
        self._pw = self._pw_cm.__enter__()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        self._page = self._context.new_page()
        self._ready = False
        self._warm()

    def _warm(self) -> None:
        self._page.goto(_HOME, wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(2500)
        resp = self._page.goto(_CHANNEL, wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(3000)
        title = self._page.title()
        if not title:
            raise RuntimeError("DCE WAF warm-up failed (empty title)")
        self._ready = True
        logger.info(
            "DCE browser session ready (status=%s title=%s)",
            getattr(resp, "status", None),
            title,
        )

    def _fetch_raw(self, variety_id: str, trade_date: str) -> dict[str, Any]:
        return self._page.evaluate(
            """async (args) => {
              const [vid, tradeDate, path] = args;
              const payload = {
                contractId: "",
                lang: "zh",
                optionSeries: "",
                statisticsType: 0,
                tradeDate,
                tradeType: "2",
                varietyId: vid,
              };
              try {
                const r = await fetch(path, {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                  },
                  body: JSON.stringify(payload),
                  credentials: "include",
                });
                const t = await r.text();
                let j = null;
                try { j = JSON.parse(t); } catch (e) {}
                return {
                  status: r.status,
                  success: !!(j && j.success),
                  code: j && j.code,
                  msg: j && j.msg,
                  data: (j && j.data) || [],
                  head: t.slice(0, 80),
                };
              } catch (e) {
                return { status: 0, success: false, err: String(e), data: [] };
              }
            }""",
            [variety_id, trade_date, _API_PATH],
        )

    def fetch_day_quotes(self, variety_id: str, trade_date: str) -> list[dict[str, Any]]:
        if not self._ready:
            self._warm()
        out = self._fetch_raw(variety_id, trade_date)
        if out.get("status") in (412, 400) or (
            out.get("status") == 200 and not out.get("success") and not out.get("data")
        ):
            logger.warning("DCE fetch degraded (%s), re-warming", out.get("status") or out.get("msg"))
            self._warm()
            out = self._fetch_raw(variety_id, trade_date)
        if not out.get("success"):
            raise RuntimeError(
                f"DCE dayQuotes failed: status={out.get('status')} "
                f"msg={out.get('msg')} head={out.get('head')}"
            )
        return list(out.get("data") or [])

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            try:
                self._pw_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._ready = False


def get_session() -> DCESyncSession:
    global _session
    with _lock:
        if _session is None:
            _session = DCESyncSession()
        return _session


def close_session() -> None:
    global _session
    with _lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:
                logger.exception("closing DCE session")
            _session = None


def _normalize_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.rename(columns={k: v for k, v in _COLUMN_MAP.items() if k in df.columns})
    keep = [
        "品种名称",
        "合约",
        "开盘价",
        "最高价",
        "最低价",
        "收盘价",
        "前结算价",
        "结算价",
        "涨跌",
        "涨跌1",
        "Delta",
        "隐含波动率(%)",
        "成交量",
        "持仓量",
        "持仓量变化",
        "成交额",
        "行权量",
    ]
    for col in keep:
        if col not in df.columns:
            df[col] = None
    df = df[keep]
    for col in (
        "开盘价",
        "最高价",
        "最低价",
        "收盘价",
        "前结算价",
        "结算价",
        "涨跌",
        "涨跌1",
        "Delta",
        "隐含波动率(%)",
        "成交额",
        "成交量",
        "持仓量",
        "持仓量变化",
        "行权量",
    ):
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(",", "", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )
    return df


def option_hist_dce_browser(symbol: str, trade_date: str) -> pd.DataFrame:
    """
    Fetch DCE option daily quotes for one variety via Playwright WAF session.

    Parameters mirror ak.option_hist_dce(symbol, trade_date).
    """
    if symbol not in DCE_OPTION_CODE_MAP:
        raise KeyError(f"unsupported DCE option symbol: {symbol}")
    vid = DCE_OPTION_CODE_MAP[symbol]
    with _lock:
        session = get_session()
        rows = session.fetch_day_quotes(vid, trade_date)
    return _normalize_df(rows)
