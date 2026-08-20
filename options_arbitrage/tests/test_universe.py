"""Tests for full option universe registry."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_fetcher.exchange_fetcher import _group_czce, _group_gfex, _group_shfe
from data_fetcher.option_universe import load_universe


def test_universe_has_full_coverage():
    items = load_universe()
    assert len(items) >= 50
    products = {i.product.upper() for i in items}
    # core exchanges represented
    assert "SR" in products
    assert "m" in {p.lower() for p in products}
    assert "cu" in {p.lower() for p in products}
    assert "si" in {p.lower() for p in products}
    assert "AP" in products


def test_czce_chain_grouping():
    import pandas as pd

    df = pd.DataFrame(
        {
            "合约代码": ["SR611C5000", "SR611P5000", "SR611C5100"],
            "今结算": [100.0, 95.0, 80.0],
            "隐含波动率": [18.0, 19.0, 17.0],
            "持仓量": [10, 20, 5],
        }
    )
    grouped = _group_czce(df)
    assert "SR611" in grouped
    assert 5000.0 in grouped["SR611"]
    assert grouped["SR611"][5000.0]["call_iv"] == 0.18


def test_gfex_chain_grouping():
    import pandas as pd

    df = pd.DataFrame(
        {
            "合约名称": ["lc2610-C-100000", "lc2610-P-100000"],
            "结算价": [5000.0, 4800.0],
            "隐含波动率": [25.0, 26.0],
            "持仓量": [100, 200],
        }
    )
    grouped = _group_gfex(df)
    assert "lc2610" in grouped
    assert grouped["lc2610"][100000.0]["call_iv"] == 0.25


def test_shfe_chain_grouping():
    import pandas as pd

    df = pd.DataFrame(
        {
            "合约代码": ["cu2609C76000", "cu2609P76000"],
            "结算价": [1200.0, 1100.0],
            "持仓量": [50, 60],
        }
    )
    grouped = _group_shfe(df)
    assert "cu2609" in grouped
    assert 76000.0 in grouped["cu2609"]


def test_czce_underlying_symbol_expansion():
    from data_fetcher.snapshot_models import norm_underlying_symbol

    assert norm_underlying_symbol("SR611", "CZCE") == "SR2611"
    assert norm_underlying_symbol("AP610", "CZCE") == "AP2610"


def test_dce_normalize_and_map():
    from data_fetcher.dce_client import DCE_OPTION_CODE_MAP, _normalize_df

    assert "聚丙烯期权" in DCE_OPTION_CODE_MAP
    assert "焦炭期权" not in DCE_OPTION_CODE_MAP
    rows = [
        {
            "variety": "聚丙烯",
            "contractId": "pp2610-C-10000",
            "open": "11",
            "high": "13.5",
            "low": "6",
            "close": "6",
            "lastClear": "8",
            "clearPrice": "9",
            "diff": "-2",
            "diff1": "1",
            "delta": "0.03",
            "volumn": 120,
            "openInterest": 24,
            "diffI": 1,
            "turnover": "1.2",
            "matchQtySum": 0,
            "impliedVolatility": "22.5",
        },
        {
            "variety": "聚丙烯",
            "contractId": "pp2610-P-10000",
            "open": "10",
            "high": "12",
            "low": "5",
            "close": "7",
            "lastClear": "9",
            "clearPrice": "8",
            "diff": "-2",
            "diff1": "-1",
            "delta": "-0.97",
            "volumn": 10,
            "openInterest": 5,
            "diffI": 0,
            "turnover": "0.5",
            "matchQtySum": 0,
            "impliedVolatility": "23.0",
        },
    ]
    df = _normalize_df(rows)
    assert len(df) == 2
    assert "合约" in df.columns
    assert float(df.iloc[0]["隐含波动率(%)"]) == 22.5


def test_zc_uses_czce_source():
    items = {i.product.upper(): i for i in load_universe()}
    assert items["ZC"].source == "czce"
    products = {i.product.lower() for i in load_universe()}
    assert "j" not in products
    assert "jm" not in products

