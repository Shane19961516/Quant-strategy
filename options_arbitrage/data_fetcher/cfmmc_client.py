"""中国期货市场监控中心（CFMMC）投资者查询 — 逐日盯市结算单下载。

登录 https://investorservice.cfmmc.com ，选择「客户交易结算日报 / 逐日盯市」，
下载 .xls 供系统导入为昨仓。

凭证仅从环境变量读取，勿写入代码仓库：
  CFMMC_USER / CFMMC_PASSWORD
可选：
  CFMMC_ACCOUNT_ID  — 导入时覆盖的内部账号（默认用结算单内账号）
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://investorservice.cfmmc.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class CfmmcError(RuntimeError):
    """CFMMC download / login failure."""


@dataclass
class CfmmcDownloadResult:
    trade_date: str
    filepath: Path
    filename: str
    bytes_len: int
    by_type: str = "date"  # date = 逐日盯市
    user_id: str = ""


def _ocr_captcha(image_bytes: bytes) -> str:
    """Recognize CFMMC captcha; prefer ddddocr, fallback to empty."""
    try:
        import ddddocr  # type: ignore

        ocr = ddddocr.DdddOcr(show_ad=False)
        raw = ocr.classification(image_bytes)
        return "".join(ch for ch in str(raw) if ch.isalnum())
    except Exception as exc:  # noqa: BLE001
        logger.warning("captcha OCR unavailable: %s", exc)
        return ""


def previous_trading_day(asof: Optional[date] = None) -> date:
    """Nearest previous Mon–Fri calendar day (weekends only; no holiday calendar)."""
    d = asof or date.today()
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def resolve_cfmmc_credentials(
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> tuple[str, str]:
    uid = (user or os.environ.get("CFMMC_USER") or "").strip()
    pwd = (password or os.environ.get("CFMMC_PASSWORD") or "").strip()
    if not uid or not pwd:
        raise CfmmcError(
            "缺少 CFMMC 凭证：请设置环境变量 CFMMC_USER / CFMMC_PASSWORD，或在请求中传入"
        )
    return uid, pwd


class CfmmcClient:
    """Session client for CFMMC investor settlement download (逐日盯市)."""

    def __init__(
        self,
        user_id: str,
        password: str,
        *,
        captcha_retries: int = 12,
        timeout: float = 45.0,
    ) -> None:
        self.user_id = user_id
        self.password = password
        self.captcha_retries = captcha_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": DEFAULT_UA, "Connection": "keep-alive"})
        self._token = ""
        self._logged_in = False

    def _decode(self, content: bytes) -> str:
        for enc in ("utf-8", "gb18030", "gbk"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="ignore")

    def _extract_token(self, html: str) -> str:
        m = re.search(
            r'name="org\.apache\.struts\.taglib\.html\.TOKEN"\s+value="([^"]+)"',
            html,
        )
        return m.group(1) if m else ""

    def login(self) -> None:
        last_err = "unknown"
        for i in range(self.captcha_retries):
            r = self.session.get(f"{BASE_URL}/login.do", timeout=self.timeout)
            html = self._decode(r.content)
            self._token = self._extract_token(html)
            t = str(int(time.time() * 1000))
            img = self.session.get(f"{BASE_URL}/veriCode.do?t={t}", timeout=self.timeout).content
            code = _ocr_captcha(img)
            if len(code) < 4:
                last_err = f"captcha too short: {code!r}"
                time.sleep(0.4)
                continue
            body = self._decode(
                self.session.post(
                    f"{BASE_URL}/login.do",
                    data={
                        "org.apache.struts.taglib.html.TOKEN": self._token,
                        "showSaveCookies": "",
                        "userID": self.user_id,
                        "password": self.password,
                        "vericode": code,
                    },
                    timeout=self.timeout,
                ).content
            )
            if "验证码错误" in body or "验证码不正确" in body:
                last_err = "验证码错误"
                logger.info("CFMMC captcha retry %s/%s", i + 1, self.captcha_retries)
                continue
            if "用户名或密码错误" in body:
                raise CfmmcError("CFMMC 用户名或密码错误")
            if "错误尝试超过" in body:
                raise CfmmcError("CFMMC 登录失败次数过多，请稍后再试")
            # success: left the login form, or settlement UI present
            if "loginForm" in body and 'name="userID"' in body and "setParameter" not in body:
                last_err = "仍停留在登录页"
                continue
            self._logged_in = True
            # refresh token if present on landing page
            tok = self._extract_token(body)
            if tok:
                self._token = tok
            logger.info("CFMMC login ok as %s", self.user_id)
            return
        raise CfmmcError(f"CFMMC 登录失败（验证码重试耗尽）: {last_err}")

    def set_daily_mark_to_market(self, trade_date: str) -> str:
        """
        Switch 客户交易结算日报 to 逐日盯市 for trade_date.
        byType: date=逐日盯市, trade=逐笔对冲
        """
        if not self._logged_in:
            self.login()
        resp = self.session.post(
            f"{BASE_URL}/customer/setParameter.do",
            data={
                "org.apache.struts.taglib.html.TOKEN": self._token,
                "tradeDate": trade_date,
                "byType": "date",
            },
            timeout=self.timeout,
        )
        html = self._decode(resp.content)
        if "登录" in html and "loginForm" in html and 'name="userID"' in html:
            self._logged_in = False
            raise CfmmcError("会话失效，请重新登录")
        # confirm 逐日盯市 selected
        if 'value="date"' not in html:
            raise CfmmcError("未能打开结算日报页面")
        tok = self._extract_token(html)
        if tok:
            self._token = tok
        return html

    def download_excel(self, *, version: str = "3") -> tuple[bytes, str]:
        """
        Download settlement workbook.
        version=3 → .xls (xlrd 可解析); version=7 → .xlsx
        """
        url = f"{BASE_URL}/customer/setupViewCustomerDetailFromCompanyWithExcel.do"
        if version:
            url = f"{url}?version={version}"
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code != 200 or len(resp.content) < 100:
            raise CfmmcError(f"下载失败 HTTP {resp.status_code} len={len(resp.content)}")
        cd = resp.headers.get("content-disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?\"?([^\";]+)', cd, re.I)
        filename = m.group(1).strip() if m else f"{self.user_id}_settlement.xls"
        # magic sniff
        if resp.content[:2] == b"PK" and not filename.lower().endswith(".xlsx"):
            filename = Path(filename).with_suffix(".xlsx").name
        elif resp.content[:2] == b"\xd0\xcf" and not filename.lower().endswith(".xls"):
            filename = Path(filename).stem + ".xls"
        return resp.content, filename

    def download_daily_mtm(
        self,
        trade_date: Optional[str] = None,
        save_dir: Optional[Path] = None,
        *,
        version: str = "3",
    ) -> CfmmcDownloadResult:
        """Login → 逐日盯市 → download .xls to save_dir."""
        td = trade_date or previous_trading_day().isoformat()
        # validate date
        datetime.strptime(td[:10], "%Y-%m-%d")
        if not self._logged_in:
            self.login()
        self.set_daily_mark_to_market(td)
        content, filename = self.download_excel(version=version)
        out_dir = Path(save_dir or Path(__file__).resolve().parents[1] / "data" / "uploads" / "cfmmc")
        out_dir.mkdir(parents=True, exist_ok=True)
        # prefer CFMMC attachment name; ensure unique path
        dest = out_dir / filename
        if dest.exists():
            dest = out_dir / f"{Path(filename).stem}_{int(time.time())}{Path(filename).suffix}"
        dest.write_bytes(content)
        return CfmmcDownloadResult(
            trade_date=td,
            filepath=dest,
            filename=dest.name,
            bytes_len=len(content),
            by_type="date",
            user_id=self.user_id,
        )


def download_settlement_xls(
    *,
    user: Optional[str] = None,
    password: Optional[str] = None,
    trade_date: Optional[str] = None,
    save_dir: Optional[Path] = None,
) -> CfmmcDownloadResult:
    """Convenience: resolve credentials → download 逐日盯市 .xls."""
    uid, pwd = resolve_cfmmc_credentials(user, password)
    client = CfmmcClient(uid, pwd)
    return client.download_daily_mtm(trade_date=trade_date, save_dir=save_dir)
