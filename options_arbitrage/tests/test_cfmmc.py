"""CFMMC client unit tests (mocked HTTP; no live credentials)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from data_fetcher.cfmmc_client import (
    CfmmcClient,
    CfmmcError,
    previous_trading_day,
    resolve_cfmmc_credentials,
)


def test_previous_trading_day_skips_weekend():
    # 2026-08-10 is Monday → previous trading day Friday 2026-08-07
    assert previous_trading_day(date(2026, 8, 10)) == date(2026, 8, 7)
    assert previous_trading_day(date(2026, 8, 13)) == date(2026, 8, 12)


def test_resolve_credentials_from_env(monkeypatch):
    monkeypatch.setenv("CFMMC_USER", "u1")
    monkeypatch.setenv("CFMMC_PASSWORD", "p1")
    assert resolve_cfmmc_credentials() == ("u1", "p1")


def test_resolve_credentials_missing(monkeypatch):
    monkeypatch.delenv("CFMMC_USER", raising=False)
    monkeypatch.delenv("CFMMC_PASSWORD", raising=False)
    with pytest.raises(CfmmcError):
        resolve_cfmmc_credentials()


def test_download_daily_mtm_flow(tmp_path: Path):
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "settlement_sample_2026-08-12.xls"
    xls_bytes = fixture.read_bytes()

    client = CfmmcClient("0128166308", "secret", captcha_retries=2)
    client._logged_in = True
    client._token = "tok"

    login_html = (
        '<input name="org.apache.struts.taglib.html.TOKEN" value="tok">'
        '<form name="loginForm"><input name="userID"></form>'
    )
    # after login / setParameter page with 逐日盯市
    settle_html = (
        '<select name="byType"><option value="date" selected>逐日盯市</option>'
        '<option value="trade">逐笔对冲</option></select>'
        '<a id="myDownload" href="/customer/setupViewCustomerDetailFromCompanyWithExcel.do">下载</a>'
    )

    def fake_get(url, timeout=None):
        m = MagicMock()
        if "veriCode" in url:
            m.content = b"fakeimg"
            m.status_code = 200
            return m
        if "Excel" in url:
            m.content = xls_bytes
            m.status_code = 200
            m.headers = {
                "content-disposition": 'attachment; filename=0128166308_2026-08-12.xls',
                "content-type": "application/vnd.ms-excel",
            }
            return m
        m.content = login_html.encode()
        m.status_code = 200
        m.headers = {}
        return m

    def fake_post(url, data=None, timeout=None):
        m = MagicMock()
        m.status_code = 200
        m.headers = {}
        if "setParameter" in url:
            assert data["byType"] == "date"
            assert data["tradeDate"] == "2026-08-12"
            m.content = settle_html.encode()
        else:
            # login success page without loginForm userID
            m.content = settle_html.encode()
        return m

    with patch.object(client.session, "get", side_effect=fake_get), patch.object(
        client.session, "post", side_effect=fake_post
    ), patch("data_fetcher.cfmmc_client._ocr_captcha", return_value="Ab12Cd"):
        # already marked logged in — download path
        result = client.download_daily_mtm(trade_date="2026-08-12", save_dir=tmp_path)

    assert result.filepath.exists()
    assert result.bytes_len == len(xls_bytes)
    assert result.by_type == "date"
    assert result.trade_date == "2026-08-12"
