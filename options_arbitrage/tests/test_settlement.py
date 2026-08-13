"""Tests for settlement parser and live PnL with real broker XLS fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pnl_engine import compute_live_pnl
from core.settlement_parser import (
    lookup_multiplier,
    parse_option_symbol,
    parse_settlement_xls,
)
from database.db import init_db, reset_engine

FIXTURE = ROOT / "fixtures" / "settlement_sample_2026-08-12.xls"


@pytest.fixture(scope="module")
def parsed():
    assert FIXTURE.exists(), f"missing fixture {FIXTURE}"
    return parse_settlement_xls(FIXTURE)


class TestSettlementParser:
    def test_fund_status(self, parsed):
        f = parsed.fund
        assert f.account_id == "166308"
        assert f.trade_date == "2026-08-12"
        assert f.client_name == "许华峰"
        assert abs(f.client_equity - 948700.35) < 0.01
        assert abs(f.margin_occupied - 172326.95) < 0.01
        assert abs(f.available - 776373.4) < 0.01
        assert abs(f.risk_degree - 18.16) < 0.01
        assert abs(f.premium_net - (-8855)) < 0.01

    def test_option_positions(self, parsed):
        assert len(parsed.option_positions) == 13
        short_lots = sum(p.short_volume for p in parsed.option_positions)
        assert short_lots == 82
        ap = next(p for p in parsed.option_positions if p.symbol == "AP610C8200")
        assert ap.underlying == "AP610"
        assert ap.option_type == "CALL"
        assert ap.strike == 8200
        assert ap.short_volume == 7
        assert ap.settle_price == 29
        assert ap.prev_settle == 27
        assert ap.multiplier == 10

    def test_multipliers(self, parsed):
        by_sym = {p.symbol: p for p in parsed.option_positions}
        assert by_sym["V2610-C-4850"].multiplier == 5
        assert by_sym["EG2610-C-5200"].multiplier == 10
        assert lookup_multiplier("AU2610") == 1000
        assert lookup_multiplier("LC2610") == 1

    def test_option_trades_sheet(self, parsed):
        assert len(parsed.option_trades) >= 30
        assert sum(t.volume for t in parsed.option_trades) == 96

    def test_symbol_parser(self):
        a = parse_option_symbol("AP610C8200")
        assert a["underlying"] == "AP610" and a["option_type"] == "CALL" and a["strike"] == 8200
        b = parse_option_symbol("EG2610-P-4300")
        assert b["underlying"] == "EG2610" and b["option_type"] == "PUT" and b["strike"] == 4300


class TestLivePnL:
    def test_carry_pnl_short_call(self, parsed):
        # AP610C8200: short 7, ref settle 29, mark 27 → profit (29-27)*7*10 = 140
        y = [p.to_dict() for p in parsed.option_positions]
        report = compute_live_pnl(
            account_id="166308",
            settlement_date="2026-08-12",
            session_date="2026-08-13",
            yesterday_positions=y,
            today_trades=[],
            marks={"AP610C8200": 27.0},
            opening_equity=parsed.fund.client_equity,
            margin_occupied_settlement=parsed.fund.margin_occupied,
            available_settlement=parsed.fund.available,
            risk_degree_settlement=parsed.fund.risk_degree,
        )
        leg = next(x for x in report.by_leg if x.symbol == "AP610C8200")
        assert abs(leg.carry_pnl - 140.0) < 0.01

    def test_today_open_sell_pnl(self, parsed):
        y = [p.to_dict() for p in parsed.option_positions]
        trades = [
            {
                "symbol": "AP610C8400",
                "underlying": "AP610",
                "option_type": "CALL",
                "strike": 8400,
                "side": "SELL",
                "offset": "OPEN",
                "price": 18.0,
                "volume": 2,
                "fee": 2.0,
                "multiplier": 10,
                "trade_id": "T1",
                "trade_time": "10:00:00",
                "trade_date": "2026-08-13",
            }
        ]
        report = compute_live_pnl(
            account_id="166308",
            settlement_date="2026-08-12",
            session_date="2026-08-13",
            yesterday_positions=y,
            today_trades=trades,
            marks={"AP610C8400": 15.0},
            opening_equity=parsed.fund.client_equity,
            margin_occupied_settlement=parsed.fund.margin_occupied,
        )
        leg = next(x for x in report.by_leg if x.symbol == "AP610C8400")
        # 卖: -2*(15-18)*10 = 60；total 含费 58
        assert abs(leg.today_trade_pnl - 60.0) < 0.01
        assert abs(leg.total_pnl - 58.0) < 0.01

    def test_close_does_not_offset_yesterday_carry(self, parsed):
        """平仓只计入今成交损益，不得冲减昨仓数量。"""
        y = [p.to_dict() for p in parsed.option_positions]
        # AP610C8200: 昨空 7，昨收/settle=29，最新=27 → 昨仓损益仍按 7 手
        # 今日买平 5 @ 28 → 今成交: +5*(27-28)*10 = -50
        trades = [
            {
                "symbol": "AP610C8200",
                "underlying": "AP610",
                "option_type": "CALL",
                "strike": 8200,
                "side": "BUY",
                "offset": "CLOSE",
                "price": 28.0,
                "volume": 5,
                "fee": 0.0,
                "multiplier": 10,
                "trade_id": "C1",
                "trade_time": "11:00:00",
                "trade_date": "2026-08-13",
            }
        ]
        report = compute_live_pnl(
            account_id="166308",
            settlement_date="2026-08-12",
            session_date="2026-08-13",
            yesterday_positions=y,
            today_trades=trades,
            marks={"AP610C8200": 27.0},
            opening_equity=parsed.fund.client_equity,
            margin_occupied_settlement=parsed.fund.margin_occupied,
        )
        leg = next(x for x in report.by_leg if x.symbol == "AP610C8200")
        assert leg.short_volume == 7  # 昨仓展示数量不因平仓冲减
        assert abs(leg.carry_pnl - 140.0) < 0.01  # -7*(27-29)*10
        assert abs(leg.today_trade_pnl - (-50.0)) < 0.01
        assert abs(leg.total_pnl - 90.0) < 0.01
        assert len(report.by_trade) == 1
        assert report.by_trade[0]["pnl"] == -50.0

    def test_missing_live_mark_zeros_pnl_and_formula(self, parsed):
        """无有效最新价时昨仓/今成交浮动=0，且 formula 不因 sign 未赋值崩溃。"""
        y = [p.to_dict() for p in parsed.option_positions]
        trades = [
            {
                "symbol": "JD2610-C-4100",
                "underlying": "JD2610",
                "option_type": "CALL",
                "strike": 4100,
                "side": "BUY",
                "offset": "CLOSE",
                "price": 40.0,
                "volume": 2,
                "fee": 0.0,
                "multiplier": 10,
                "trade_id": "JD1",
                "trade_time": "21:00:00",
                "trade_date": "2026-08-13",
            }
        ]
        report = compute_live_pnl(
            account_id="166308",
            settlement_date="2026-08-12",
            session_date="2026-08-13",
            yesterday_positions=y,
            today_trades=trades,
            marks={},  # 无夜盘/无行情
            opening_equity=parsed.fund.client_equity,
        )
        assert report.total_carry_pnl == 0.0
        assert report.total_today_trade_pnl == 0.0
        jd = next(x for x in report.by_leg if x.symbol == "JD2610-C-4100")
        assert jd.carry_pnl == 0.0
        assert jd.today_trade_pnl == 0.0
        assert len(report.by_trade) == 1
        assert report.by_trade[0]["pnl"] == 0.0
        assert "*(最新" in report.by_trade[0]["formula"]

    def test_eg_carry_matches_libra_settle_mtm(self, parsed):
        """EG 昨仓 + 结算价基准 + Libra 盘中价 → 浮动 1425。"""
        y = [p.to_dict() for p in parsed.option_positions if p.underlying.startswith("EG")]
        report = compute_live_pnl(
            account_id="166308",
            settlement_date="2026-08-12",
            session_date="2026-08-13",
            yesterday_positions=y,
            today_trades=[],
            marks={"EG2610-C-5200": 34.0, "EG2610-P-4300": 43.0},
        )
        assert report.total_carry_pnl == pytest.approx(1425.0)


@pytest.fixture()
def client(tmp_path):
    reset_engine()
    url = f"sqlite:///{tmp_path / 'settle_test.db'}"
    init_db(url)
    from api.main import app

    with TestClient(app) as c:
        yield c
    reset_engine()


class TestSettlementAPI:
    def test_upload_and_live_pnl_flow(self, client):
        with open(FIXTURE, "rb") as f:
            r = client.post(
                "/api/v1/settlement/upload",
                files={"file": ("settlement.xls", f, "application/vnd.ms-excel")},
                data={"account_id": "166308"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["position_count"] == 13
        assert body["short_lots"] == 82
        assert body["settlement_date"] == "2026-08-12"
        assert body["suggested_session_date"] == "2026-08-13"

        active = client.get("/api/v1/settlement/active", params={"account_id": "166308"})
        assert active.status_code == 200
        assert len(active.json()["positions"]) == 13

        # manual today trade
        tr = client.post(
            "/api/v1/settlement/today-trades",
            json={
                "account_id": "166308",
                "session_date": "2026-08-13",
                "symbol": "V2610-C-4900",
                "side": "卖",
                "offset": "开",
                "price": 22.0,
                "volume": 3,
                "fee": 1.53,
            },
        )
        assert tr.status_code == 200, tr.text
        assert tr.json()["side"] == "SELL"
        assert tr.json()["offset"] == "OPEN"
        assert abs(tr.json()["premium_cash"] - 22 * 3 * 5) < 0.01  # V mult=5

        # marks
        client.post(
            "/api/v1/settlement/marks",
            json={
                "account_id": "166308",
                "session_date": "2026-08-13",
                "symbol": "AP610C8200",
                "price": 27.0,
            },
        )

        pnl = client.get(
            "/api/v1/settlement/live-pnl",
            params={"account_id": "166308", "session_date": "2026-08-13"},
        )
        assert pnl.status_code == 200, pnl.text
        report = pnl.json()
        assert report["yesterday_position_count"] == 13
        assert report["today_trade_count"] == 1
        assert report["opening_equity"] == pytest.approx(948700.35)
        ap = next(x for x in report["by_leg"] if x["symbol"] == "AP610C8200")
        assert ap["carry_pnl"] == pytest.approx(140.0)

        # today trades list separate from yesterday
        tl = client.get(
            "/api/v1/settlement/today-trades",
            params={"account_id": "166308", "session_date": "2026-08-13"},
        )
        assert tl.json()["count"] == 1
        yp = client.get("/api/v1/settlement/yesterday-positions", params={"account_id": "166308"})
        assert yp.json()["count"] == 13
        # ensure today's new symbol not in yesterday positions
        ysyms = {p["symbol"] for p in yp.json()["positions"]}
        assert "V2610-C-4900" not in ysyms

        # 结算导入不得把 settle 写入 marks（否则盯市全错）
        mk = client.get(
            "/api/v1/settlement/marks",
            params={"account_id": "166308", "session_date": "2026-08-13"},
        )
        assert mk.status_code == 200
        marks = mk.json()["marks"]
        # 仅手工写入的 AP610C8200=27，不应出现其它 settle 种子
        assert marks.get("AP610C8200") == pytest.approx(27.0)
        assert "EG2610-C-5200" not in marks
        assert "V2610-C-4800" not in marks


class TestGreeksBook:
    def test_net_positions_and_greeks(self, parsed):
        from core.greeks_book import compute_net_positions_and_greeks

        y = [p.to_dict() for p in parsed.option_positions]
        marks = {p.symbol: p.settle_price for p in parsed.option_positions}
        trades = [
            {
                "symbol": "AP610C8200",
                "underlying": "AP610",
                "option_type": "CALL",
                "strike": 8200,
                "side": "SELL",
                "offset": "OPEN",
                "price": 26.5,
                "volume": 2,
                "fee": 2.02,
                "multiplier": 10,
                "trade_id": "T1",
                "trade_time": "10:00:00",
                "trade_date": "2026-08-13",
            }
        ]
        g = compute_net_positions_and_greeks(
            yesterday_positions=y,
            today_trades=trades,
            marks=marks,
            asof="2026-08-13",
        )
        assert g.total_short_volume >= 82  # yesterday 82 + today open 2 on existing
        ap = next(u for u in g.by_underlying if u.underlying == "AP610")
        assert ap.y_short >= 7
        assert ap.t_short >= 2
        assert ap.call_short >= 9
        products = {p.product for p in g.by_product}
        assert "AP" in products
        assert "EG" in products
        # greeks finite
        assert abs(g.total_net_delta) < 50
        leg = next(x for x in g.leg_greeks if x.symbol == "AP610C8200")
        assert leg.net_volume < 0  # net short
        assert leg.delta < 0  # short call → negative delta contribution? Wait short call: signed=-vol, delta_unit>0 → delta = positive*negative < 0. Yes.


def test_net_positions_api(client):
    with open(FIXTURE, "rb") as f:
        client.post(
            "/api/v1/settlement/upload",
            files={"file": ("settlement.xls", f, "application/vnd.ms-excel")},
            data={"account_id": "166308"},
        )
    r = client.get(
        "/api/v1/settlement/net-positions",
        params={"account_id": "166308", "session_date": "2026-08-13"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "by_product" in body and len(body["by_product"]) >= 1
    assert "by_underlying" in body
    assert "total_net_delta" in body

    pnl = client.get(
        "/api/v1/settlement/live-pnl",
        params={"account_id": "166308", "session_date": "2026-08-13"},
    )
    assert pnl.status_code == 200
    j = pnl.json()
    assert "greeks_summary" in j
    assert "net_positions" in j
    assert "by_product" in j["net_positions"]
